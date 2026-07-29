"""Reading a pack of submitted documents into a verified evidence record.

The documents are read **whole**. Every submitted file is turned into text with its locators
intact, joined into one corpus, and put to the model in a handful of calls that each answer a
group of related questions across all of it at once.

This replaced a passage-at-a-time survey followed by a per-question synthesis. That shape existed
to work around a context window too small to hold a document, and cost roughly thirty calls for a
sixty-page pack -- thirty chances to fail, thirty waits, and thirty readings each blind to the
rest of the document. The models this runs against hold a million tokens; a sixty-page document is
about forty thousand. The workaround was solving a problem that had gone away, and the reading is
better for being whole: a threshold in an appendix and the process it governs in section three
are in front of the model together rather than in two calls that never meet.

Three things survive from the old design because they earn their place.

Grounding. Every answer cites verbatim quotes, and each is checked against the corpus. A quote
that cannot be found is dropped, and an answer that loses all of its evidence is marked for
confirmation rather than trusted.

The resolution sweep. Questions the first reading left open are put back to the documents,
directly, more than once. A question asked directly is often answered by material a general
reading had no reason to connect, and every question that survives to the modelling team costs
days. The last sweep also divides what is still open in two: the things only a person can settle,
which are worth asking, and the things that would not change which scenarios exist, which are
not. Only the first kind reaches the interface; both are recorded.

Splitting, but only as a fallback. A corpus past ``MAX_CORPUS_CHARS`` is divided and the parts
merged, with a warning. That is the exception now rather than the rule.
"""
from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from ..core.evidence import (FACETS, INTAKE_PARTS, KIND_IMAGE, Claim, DocumentRef, EvidenceRecord,
                             FacetAnswer, SourceRef)
from ..core.grounding import locate
from ..llm import cancellation, config, prompt_loader
from ..llm.calling import call
from ..llm.gateway import ask_llm, ask_llm_with_images
from ..utils import parse_json_object
from .context_document import FACET_HEADINGS, FACET_QUESTIONS
from .readers import UnreadableDocument, is_image, load_image, read_document
from .redaction import redact_segments

logger = logging.getLogger(__name__)

CompletionFn = Callable[..., str]
ProgressFn = Callable[..., None]

_SYSTEM_PROMPT = "ingest.system"
_READ_PROMPT = "ingest.read"
_RESOLVE_PROMPT = "ingest.resolve"
_DIAGRAM_READ_PROMPT = "ingest.diagram_read"
_DIAGRAM_SYNTHESIZE_PROMPT = "ingest.diagram_synthesize"

# How much text goes into one reading call, from LLM_MAX_CORPUS_CHARS. A pack past this is split,
# which reads worse than reading it whole, so the number is set to make that rare.
MAX_CORPUS_CHARS = config.MAX_CORPUS_CHARS

# What the resolution sweep may conclude about a question it could not answer from the documents.
ASK_THE_TEAM = "ask_the_team"
NOT_MATERIAL = "not_material"

# The eleven questions asked in three calls rather than eleven. Grouped by what they have in
# common, so each call answers questions that draw on the same parts of the document, and each
# has room to answer three or four questions in detail without the reply running out of space.
FACET_GROUPS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("what the agent is for and who it serves", ("use_case", "personas", "terminology")),
    ("how the agent is built", ("capabilities", "decisions", "states", "tools")),
    ("what bounds it and what is known to go wrong",
     ("policy_constraints", "scope_boundaries", "risk_areas", "owner_testing")),
)

MAX_PARALLEL_CALLS = 3
MAX_FAILURE_RATE = 0.5


class IngestionFailed(RuntimeError):
    """Too much of the run failed for its output to be worth keeping."""


@dataclass
class Corpus:
    """Every submitted document as one body of locatable text."""

    text: str
    documents: List[DocumentRef]
    diagrams: List[Path]

    def __bool__(self) -> bool:
        return bool(self.text.strip()) or bool(self.diagrams)


def build_corpus(paths: Sequence[Path], progress: ProgressFn = None,
                 redact: Callable[..., Tuple[List, Optional[dict]]] = None,
                 should_redact: Callable[[Path], bool] = None) -> Corpus:
    """Read every submitted file into one corpus. Deterministic; no model involved.

    Each passage keeps the marker that says where it came from, so a quote can be traced to a
    page after the fact. Images are set aside for the vision pass, which cannot read text.

    Every document's segments are redacted immediately after they are read and before anything
    is joined into the corpus this returns -- see :mod:`.redaction`. That is what makes it true
    that nothing downstream, including every model call ingestion makes, ever sees a passage this
    step has not already been through, regardless of which facet reads it next.

    ``should_redact``, given a path, says whether *this* document must be redacted regardless of
    the global ``PII_REDACTION`` setting -- the interface's per-file toggle is what sets this.
    Nothing here decides the global default; that stays entirely in :func:`.redaction.redact_segments`.
    """
    progress = progress or (lambda *a, **k: None)
    redact = redact or redact_segments
    should_redact = should_redact or (lambda path: False)
    parts: List[str] = []
    documents: List[DocumentRef] = []
    diagrams: List[Path] = []
    mapping: Optional[dict] = None

    for path in paths:
        path = Path(path)
        progress(f"Reading {path.name}")

        if is_image(path):
            diagrams.append(path)
            continue

        try:
            reference, segments = read_document(path)
        except UnreadableDocument as exc:
            logger.warning("%s could not be read: %s", path.name, exc)
            documents.append(DocumentRef(name=path.name, kind="unreadable", note=str(exc)))
            continue

        segments, mapping = redact(segments, mapping, force=should_redact(path))
        documents.append(reference)
        body = "\n\n".join(f"[{segment.locator}]\n{segment.text}" for segment in segments)
        parts.append(f"=== DOCUMENT: {path.name} ===\n\n{body}")

    return Corpus(text="\n\n\n".join(parts), documents=documents, diagrams=diagrams)


class DocumentExtractor:
    """Reads a submitted pack into answers, with every answer traceable to the documents."""

    def __init__(self, complete: Optional[CompletionFn] = None,
                 progress: Optional[ProgressFn] = None, resolve: bool = True,
                 describe_images: Optional[Callable[..., str]] = None,
                 resolve_passes: Optional[int] = None, cancel=None,
                 redact: Optional[Callable[..., Tuple[List, Optional[dict]]]] = None,
                 should_redact: Optional[Callable[[Path], bool]] = None) -> None:
        self._complete = complete or ask_llm
        self._describe_images = describe_images or ask_llm_with_images
        self._progress = progress or (lambda *args, **kwargs: None)
        self._resolve = resolve
        self._passes = (config.INGEST_RESOLVE_PASSES if resolve_passes is None
                        else max(0, int(resolve_passes)))
        self._cancel = cancel
        self._redact = redact or redact_segments
        self._should_redact = should_redact
        self._calls = 0
        self._failures = 0
        self._done = 0
        self._total = 0
        self._drawn_on: Set[str] = set()
        self._lock = threading.Lock()                      # the reading calls run in parallel

    # ------------------------------------------------------------------ entry point
    def run(self, paths: Sequence[Path]) -> EvidenceRecord:
        paths = [Path(p) for p in paths]
        corpus = build_corpus(paths, self._progress, self._redact, self._should_redact)
        if not corpus:
            raise IngestionFailed("None of the submitted files could be read.")
        cancellation.check(self._cancel)

        record = EvidenceRecord(documents=corpus.documents)
        self._total = self._estimate_calls(corpus)

        # Reading the text and reading the diagrams are independent, so they go together.
        chunks = self._split(corpus.text)
        inventory = _inventory(corpus)
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL_CALLS) as pool:
            futures = [pool.submit(self._read_group, label, facets, chunks, inventory)
                       for label, facets in FACET_GROUPS]
            diagram_future = (pool.submit(self._read_diagrams, corpus.diagrams, record)
                              if corpus.diagrams else None)
            answers: Dict[str, FacetAnswer] = {}
            for future in futures:
                answers.update(future.result())
            if diagram_future:
                diagram_future.result()

        record.answers = [answers.get(facet) or _unanswered(facet) for facet in FACETS]
        self._attach_evidence(record, corpus.text)
        _fold_diagram_observations(record)
        self._mark_documents_drawn_on(record)
        self._stop_if_mostly_failing("reading")
        cancellation.check(self._cancel)

        if self._resolve and self._passes:
            self._resolve_unknowns(record, corpus.text)

        answered = sum(1 for a in record.answers if a.is_answered)
        readable = [d for d in record.documents if d.kind != "unreadable"]
        logger.info("Read %d document(s) in %d model call(s). Answered %d of %d questions.",
                    len(readable), self._calls, answered, len(FACETS))

        ignored = [d.name for d in readable if not d.drawn_on]
        if ignored:
            logger.warning(
                "%d of %d submitted document(s) did not inform any answer: %s. Either they do not "
                "bear on the agent's behaviour, or they were read and passed over -- worth "
                "checking before the intake is drafted from this.",
                len(ignored), len(readable), ", ".join(ignored))
        return record

    # ------------------------------------------------------------------ reading
    def _split(self, text: str) -> List[str]:
        """One part unless the corpus is too large to send, which should be uncommon."""
        limit = config.max_corpus_chars()
        if len(text) <= limit:
            return [text]

        parts = [text[i:i + limit] for i in range(0, len(text), limit)]
        logger.warning(
            "The submitted pack is %d characters and was split into %d parts to be read. Reading "
            "it whole gives a better result; consider trimming what does not bear on the agent's "
            "behaviour.", len(text), len(parts))
        return parts

    def _read_group(self, label: str, facets: Sequence[str], chunks: List[str],
                    inventory: str) -> Dict[str, FacetAnswer]:
        """Answer one group of questions from the whole corpus."""
        questions = "\n".join(
            f"- {facet}: {FACET_HEADINGS.get(facet, facet)} — {FACETS[facet]}"
            for facet in facets)

        merged: Dict[str, FacetAnswer] = {}
        for number, chunk in enumerate(chunks, start=1):
            cancellation.check(self._cancel)
            self._step(f"Reading the documents for {label}"
                       + (f" (part {number} of {len(chunks)})" if len(chunks) > 1 else ""))
            reply = self._ask(_READ_PROMPT, questions=questions, corpus=chunk,
                              documents=inventory)
            if reply is None:
                continue
            self._note_documents_used(reply.get("documents_used"))
            for facet in facets:
                answer = _to_answer(facet, reply.get(facet))
                if answer:
                    merged[facet] = _merge(merged.get(facet), answer)
        return merged

    def _note_documents_used(self, names) -> None:
        """Record which documents a reading call said it drew on. Called from three threads."""
        if not isinstance(names, list):
            return
        with self._lock:
            self._drawn_on.update(str(n).strip().lower() for n in names if str(n).strip())

    def _mark_documents_drawn_on(self, record: EvidenceRecord) -> None:
        """Flag each document as used or not.

        A citation that survived verification is the stronger signal, because it was checked; what
        the reading *said* it used is taken as well, since a document can inform an answer without
        being the source of the sentence quoted from it.
        """
        cited = {claim.source.document.strip().lower() for claim in record.claims
                 if claim.source.document}
        for document in record.documents:
            name = document.name.strip().lower()
            document.drawn_on = name in cited or name in self._drawn_on or any(
                name in mentioned for mentioned in cited | self._drawn_on)

    def _read_diagrams(self, paths: List[Path], record: EvidenceRecord) -> None:
        """Describe submitted diagrams in two passes.

        A workflow is often split across several images because it did not fit in one picture,
        and asking a single call to both parse every image and stitch them into one flow at once
        asks it to do two different things together. This reads each image on its own first --
        the same way a person would, one picture at a time -- and only afterwards, with grounded
        descriptions in hand rather than raw pixels, works out how they connect and what they
        establish. An image that fails on its own is dropped and the rest still go through; the
        second pass only runs at all if at least one image was actually read.
        """
        loaded: List[Tuple[Path, Tuple[str, bytes]]] = []
        for path in paths:
            try:
                loaded.append((path, load_image(path)))
            except UnreadableDocument as exc:
                record.documents.append(
                    DocumentRef(name=path.name, kind="unreadable", note=str(exc)))
        if not loaded:
            return

        names = ", ".join(p.name for p in paths)
        descriptions: List[Tuple[str, str]] = []
        for index, (path, image) in enumerate(loaded, start=1):
            cancellation.check(self._cancel)
            self._step(f"Reading diagram {index} of {len(loaded)}: {path.name}")
            description = self._read_one_diagram(path, image, index, len(loaded))
            if description:
                descriptions.append((path.name, description))

        if not descriptions:
            for path, _ in loaded:
                record.documents.append(DocumentRef(
                    name=path.name, kind="unreadable",
                    note="the diagram could not be read. Supply a written description of the "
                         "flow it shows, or add it as a note."))
            return

        cancellation.check(self._cancel)
        self._step(f"Constructing the workflow from {len(descriptions)} diagram(s)")
        reply = self._synthesize_diagrams(descriptions, names)
        if reply is None:
            for path, _ in loaded:
                record.documents.append(DocumentRef(
                    name=path.name, kind="unreadable",
                    note="each diagram was read on its own but could not be put together into "
                         "a workflow. Supply a written description of the flow, or add it as a "
                         "note."))
            return

        for path, _ in loaded:
            record.documents.append(DocumentRef(name=path.name, kind="diagram", units=1))

        # Nothing read from a picture can be checked against text, so it is always unconfirmed.
        for entry in reply.get("observations") or []:
            if not isinstance(entry, dict):
                continue
            facet = str(entry.get("facet", "")).strip()
            statement = str(entry.get("statement", "")).strip()
            if facet in FACETS and statement:
                record.claims.append(Claim(
                    facet=facet, statement=statement, quote=str(entry.get("quote", "")).strip(),
                    status="unverifiable",
                    note="read from a diagram; confirm by hand",
                    source=SourceRef(document=names,
                                     locator=str(entry.get("locator", "")).strip(),
                                     kind=KIND_IMAGE)))

    def _read_one_diagram(self, path: Path, image: Tuple[str, bytes], index: int,
                          total: int) -> Optional[str]:
        """One image, read on its own. Returns the description, or None if the call failed."""
        user = prompt_loader.render(_DIAGRAM_READ_PROMPT, filename=path.name,
                                    position=f"{index} of {total}")
        self._calls += 1
        try:
            reply = parse_json_object(call(
                self._describe_images, prompt_loader.load(_SYSTEM_PROMPT), user,
                tier=config.JUDGEMENT, images=[image]))
        except Exception as exc:
            self._failures += 1
            logger.warning("Could not read diagram %s: %s", path.name, exc)
            return None
        return str(reply.get("description", "")).strip() or None

    def _synthesize_diagrams(self, descriptions: List[Tuple[str, str]],
                             names: str) -> Optional[dict]:
        """Reconcile every image's own reading into one workflow, and extract what it settles.

        Text only -- everything visual that matters was already pulled out into the descriptions
        this is given, so there is nothing left for this call to look at a picture for.
        """
        readings = "\n\n".join(
            f"=== IMAGE {index} of {len(descriptions)}: {name} ===\n{text}"
            for index, (name, text) in enumerate(descriptions, start=1))
        user = prompt_loader.render(_DIAGRAM_SYNTHESIZE_PROMPT, facets=_facet_guide(),
                                    document=names, readings=readings)
        self._calls += 1
        try:
            return parse_json_object(call(
                self._complete, prompt_loader.load(_SYSTEM_PROMPT), user, tier=config.JUDGEMENT))
        except Exception as exc:
            self._failures += 1
            logger.warning("Could not construct the workflow from the diagrams: %s", exc)
            return None

    # ------------------------------------------------------------------ grounding
    def _attach_evidence(self, record: EvidenceRecord, corpus: str) -> None:
        """Turn each answer's citations into checked claims, and drop the ones that are not there.

        An answer whose every citation fails is not necessarily false, but nothing supports it, so
        it is marked for confirmation rather than presented as established.
        """
        checked = 0
        for answer in record.answers:
            kept: List[str] = []
            for citation in getattr(answer, "citations", []) or []:
                quote = str(citation.get("quote", "")).strip()
                checked += 1
                found, _ = locate(quote, corpus)
                if not found:
                    continue
                claim = Claim(
                    id=f"E-{len(record.claims) + 1:03d}", facet=answer.facet,
                    statement=quote, quote=quote,
                    source=SourceRef(document=str(citation.get("document", "")).strip(),
                                     locator=str(citation.get("locator", "")).strip()))
                record.claims.append(claim)
                kept.append(claim.id)

            answer.sources = kept
            if answer.is_answered and not kept:
                answer.unknowns = list(answer.unknowns) + [
                    "None of the quotes supporting this answer could be found in the documents. "
                    "Check it before relying on it."]

        dropped = checked - len(record.claims)
        if dropped > 0:
            logger.info("%d of %d citations could not be found in the documents and were dropped.",
                        dropped, checked)

    # ------------------------------------------------------------------ resolution sweep
    def _resolve_unknowns(self, record: EvidenceRecord, corpus: str) -> None:
        """Put what is still open back to the documents, then triage what survives.

        Each pass takes only what the pass before it left open, so the questions narrow and the
        prompt shortens. The final pass is told to rule on anything it still cannot answer:
        whether a person has to settle it, or whether it would not change which scenarios exist.
        That ruling is what keeps the list at the end of this short enough to be worked through.
        """
        for number in range(1, self._passes + 1):
            cancellation.check(self._cancel)
            outstanding = self._outstanding(record)
            if not outstanding:
                break
            last = number == self._passes
            self._settle_pass(record, corpus, outstanding, number, last)

        if not any(a.triaged for a in record.answers):     # every sweep failed; ask everything
            return
        for answer in record.answers:
            if not answer.triaged:
                answer.must_ask = list(answer.unknowns)
                answer.triaged = True

    def _outstanding(self, record: EvidenceRecord) -> List[Tuple[str, str]]:
        """What is still open: every unsettled point, plus every question with no answer at all."""
        outstanding = list(record.open_unknowns())
        for facet in record.empty_facets():
            outstanding.append((facet, FACET_QUESTIONS.get(facet, "")))
        seen, unique = set(), []
        for facet, question in outstanding:
            key = question.strip().lower()
            if key and key not in seen:
                seen.add(key)
                unique.append((facet, question))
        return unique

    def _settle_pass(self, record: EvidenceRecord, corpus: str,
                     outstanding: List[Tuple[str, str]], number: int, last: bool) -> None:
        """One sweep: answer what the documents settle, and on the last pass rule on the rest."""
        self._step(f"Looking again for {len(outstanding)} unanswered point(s)"
                   + (f" (pass {number} of {self._passes})" if self._passes > 1 else ""))
        established = "\n\n".join(
            f"## {FACET_HEADINGS.get(a.facet, a.facet)}\n{a.answer}"
            for a in record.answers if a.is_answered and a.answer)

        reply = self._ask(_RESOLVE_PROMPT, established=established or "Nothing yet.",
                          questions="\n".join(f"- {q}" for _, q in outstanding),
                          corpus=corpus,
                          triage=prompt_loader.load("ingest.triage") if last else "")
        if reply is None:
            return

        by_question = {str(r.get("question", "")).strip().lower(): r
                       for r in (reply.get("resolved") or []) if isinstance(r, dict)}
        settled, to_ask, set_aside = 0, 0, 0

        for facet, question in outstanding:
            target = record.answer_for(facet)
            if target is None:
                continue
            found = by_question.get(question.strip().lower())
            status = str(found.get("status", "")).strip() if found else ""

            if status == "answered" and str(found.get("answer", "")).strip():
                answer_text = str(found["answer"]).strip()
                target.points = list(target.points) + [answer_text]
                target.unknowns = [u for u in target.unknowns
                                   if u.strip().lower() != question.strip().lower()]
                if not target.answer:
                    target.answer = answer_text
                settled += 1
            elif last:
                # Nothing settled it, so it is one of two kinds of leftover, and the sweep has to
                # justify asking by naming which part of the intake the question blocks. A ruling
                # of ask_the_team with no part named does not earn the ask -- documentation is
                # always incomplete, and "worth being sure about" is not a blocked intake.
                blocked = str(found.get("blocks", "")).strip().lower() if found else ""
                if status == NOT_MATERIAL or (status == ASK_THE_TEAM
                                              and blocked not in INTAKE_PARTS):
                    set_aside += 1
                elif status == ASK_THE_TEAM:
                    target.must_ask = list(target.must_ask) + [question]
                    target.blocks = dict(target.blocks, **{question: blocked})
                    to_ask += 1
                else:
                    # No ruling at all. Asked rather than dropped: a question nobody judged is
                    # the one failure this arrangement exists to avoid.
                    target.must_ask = list(target.must_ask) + [question]
                    to_ask += 1

        if last:
            for answer in record.answers:
                answer.triaged = True

        logger.info("Pass %d of %d settled %d of %d outstanding point(s) from the documents.",
                    number, self._passes, settled, len(outstanding))
        if last and (to_ask or set_aside):
            logger.info("%d point(s) need a person; %d were judged not to change what gets "
                        "tested and are recorded but not asked.", to_ask, set_aside)

    # ------------------------------------------------------------------ shared
    def _estimate_calls(self, corpus: Corpus) -> int:
        parts = max(1, -(-len(corpus.text) // config.max_corpus_chars()))
        sweeps = self._passes if self._resolve else 0
        # One call per diagram, read on its own, plus one to construct the workflow from them.
        diagram_calls = len(corpus.diagrams) + 1 if corpus.diagrams else 0
        return len(FACET_GROUPS) * parts + diagram_calls + sweeps

    def _step(self, message: str) -> None:
        self._done += 1
        self._progress(message, self._done, self._total)

    def _ask(self, prompt: str, **values) -> Optional[dict]:
        """One judgement-budget call, returning None rather than raising when it fails."""
        self._calls += 1
        user = prompt_loader.render(prompt, **values)
        system = prompt_loader.load(_SYSTEM_PROMPT)
        try:
            return parse_json_object(
                call(self._complete, system, user, tier=config.JUDGEMENT))
        except Exception as exc:
            self._failures += 1
            logger.warning("A reading call failed: %s", exc)
            return None

    def _stop_if_mostly_failing(self, phase: str) -> None:
        if self._calls and self._failures / self._calls > MAX_FAILURE_RATE:
            raise IngestionFailed(
                f"{self._failures} of {self._calls} model calls failed during {phase}. The output "
                f"would not be a usable reading of these documents, so nothing was written. "
                f"Check that SafeChain can reach the model named in your config.yml, and run it "
                f"again.")


def _fold_diagram_observations(record: EvidenceRecord) -> None:
    """Attach what the diagrams established to the answers they inform.

    Without this a diagram is read, its observations are stored, and then nothing downstream ever
    looks at them: the context document renders only the claims an *answer* names as its sources,
    and the intake is drafted from the context document alone. A workflow diagram is frequently
    the only place a branch is written down at all, so the effect was a context file reporting
    "where it branches -- not covered by the submitted documents" while the evidence record sat
    there holding the branches.

    Run after :meth:`DocumentExtractor._attach_evidence`, which assigns ``sources`` wholesale from
    the verified text citations and would otherwise overwrite what this adds.
    """
    for index, claim in enumerate(record.claims, start=1):
        if claim.source.kind != KIND_IMAGE or claim.id:
            continue
        claim.id = f"D-{index:03d}"

        answer = record.answer_for(claim.facet)
        if answer is None:
            continue
        if claim.statement not in answer.points:
            answer.points.append(claim.statement)
        if claim.id not in answer.sources:
            answer.sources.append(claim.id)

        # The placeholder unknown only means "nothing addressed this at all", which has just
        # stopped being true. Anything the diagram genuinely left open is recorded by the
        # resolution sweep on its own terms rather than by this stand-in.
        generic = FACET_QUESTIONS.get(claim.facet, "").strip()
        if generic:
            answer.unknowns = [u for u in answer.unknowns if u.strip() != generic]


def _facet_guide() -> str:
    return "\n".join(f"- {key}: {description}" for key, description in FACETS.items())


def _inventory(corpus: Corpus) -> str:
    """The submitted pack listed by name, for the reading prompt.

    Naming them makes the reading accountable for each one. A pack is otherwise read as a single
    wall of text in which the longest document answers everything, and a vendor appendix that
    contradicts the main document contributes nothing because nothing drew attention to it.
    """
    lines = [f"- {d.name} ({d.kind}, {d.units} sections)" if d.units else f"- {d.name} ({d.kind})"
             for d in corpus.documents if d.kind != "unreadable"]
    lines += [f"- {p.name} (diagram, read separately)" for p in corpus.diagrams]
    return "\n".join(lines) or "- none"


def _unanswered(facet: str) -> FacetAnswer:
    return FacetAnswer(facet=facet, confidence="Low",
                       unknowns=[FACET_QUESTIONS.get(facet, "")])


def _to_answer(facet: str, entry) -> Optional[FacetAnswer]:
    if not isinstance(entry, dict):
        return None
    confidence = str(entry.get("confidence", "")).strip().title()
    answer = FacetAnswer(
        facet=facet,
        answer=str(entry.get("answer", "")).strip(),
        points=[str(p).strip() for p in (entry.get("points") or []) if str(p).strip()],
        unknowns=[str(u).strip() for u in (entry.get("unknowns") or []) if str(u).strip()],
        confidence=confidence if confidence in ("Low", "Medium", "High") else "Low",
    )
    # Carried until the citations are checked, then replaced by the ids of the claims that stood.
    answer.citations = [c for c in (entry.get("evidence") or []) if isinstance(c, dict)]
    return answer


def _merge(existing: Optional[FacetAnswer], addition: FacetAnswer) -> FacetAnswer:
    """Combine answers to one question from separate parts of a split corpus."""
    if existing is None:
        return addition
    existing.answer = "  ".join(p for p in (existing.answer, addition.answer) if p)
    existing.points = existing.points + [p for p in addition.points if p not in existing.points]
    existing.unknowns = existing.unknowns + [u for u in addition.unknowns
                                             if u not in existing.unknowns]
    existing.citations = list(existing.citations) + list(addition.citations)
    order = {"Low": 0, "Medium": 1, "High": 2}
    if order.get(addition.confidence, 0) > order.get(existing.confidence, 0):
        existing.confidence = addition.confidence
    return existing


def extract_documents(paths: Sequence[Path], complete: Optional[CompletionFn] = None,
                      progress: Optional[ProgressFn] = None, resolve: bool = True,
                      describe_images: Optional[Callable[..., str]] = None,
                      resolve_passes: Optional[int] = None, cancel=None) -> EvidenceRecord:
    """Read a submitted pack into a verified evidence record."""
    return DocumentExtractor(complete=complete, progress=progress, resolve=resolve,
                             describe_images=describe_images,
                             resolve_passes=resolve_passes, cancel=cancel).run(paths)


def record_to_json(record: EvidenceRecord, path: Path) -> None:
    Path(path).write_text(json.dumps(record.to_dict(), indent=2), encoding="utf-8")


def record_from_json(path: Path) -> EvidenceRecord:
    return EvidenceRecord.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
