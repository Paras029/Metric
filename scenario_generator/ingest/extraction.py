"""Reading a pack of submitted documents into a verified evidence record.

The documents are read **whole**. Every submitted file is turned into text with its locators
intact, joined into one corpus, and put to the model in a handful of calls that each answer a
group of related questions across all of it at once.

Reading whole is what makes the reading good. The alternative -- surveying a passage at a time and
synthesising per question -- is a workaround for a context window too small to hold a document,
and it costs roughly thirty calls for a sixty-page pack: thirty chances to fail, thirty waits, and
thirty readings each blind to the rest of the document. The models this runs against hold a
million tokens and a sixty-page document is about forty thousand, so there is no window to work
around. A threshold in an appendix and the process it governs in section three are in front of the
model together rather than in two calls that never meet.

Three things guard that reading.

Grounding. Every answer cites verbatim quotes, and each is checked against the corpus. A quote
that cannot be found is dropped, and an answer that loses all of its evidence is marked for
confirmation rather than trusted.

The resolution sweep. Questions the first reading left open are put back to the documents,
directly, more than once. A question asked directly is often answered by material a general
reading had no reason to connect, and every question that survives to the model owner costs
days. Whatever the documents still do not settle is carried to the intake stage, which decides
what is actually blocking from the declaration itself rather than by asking a model to guess.

Splitting, but only as a fallback. A corpus past ``config.max_corpus_chars()`` is divided and the
parts merged, with a warning. That is the exception rather than the rule.
"""
from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from ..core.evidence import (FACETS, KIND_IMAGE, Claim, DocumentRef, EvidenceRecord,
                             FacetAnswer, SourceRef)
from ..core.grounding import Source
from ..llm import cancellation, config, council, prompt_loader
from ..llm.gateway import ask_llm, ask_llm_with_images
from ..utils import parse_json_object
from ..utils.replies import prose
from .context_document import FACET_HEADINGS, FACET_QUESTIONS
from . import diagram_structure
from .readers import (UnreadableDocument, is_image, load_image,
                      read_document)
from .redaction import redact_segments

logger = logging.getLogger(__name__)

CompletionFn = Callable[..., str]
ProgressFn = Callable[..., None]

_SYSTEM_PROMPT = "ingest.system"
_READ_PROMPT = "ingest.read"
_RESOLVE_PROMPT = "ingest.resolve"
_DIAGRAM_READ_PROMPT = "ingest.diagram_read"
_DIAGRAM_SYNTHESIZE_PROMPT = "ingest.diagram_synthesize"
_DIAGRAM_REPAIR_PROMPT = "ingest.diagram_repair"

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

# How many files are parsed at once. Parsing (PDF/Word/Excel extraction) is mostly waiting on
# disk and on C extensions that release the GIL, so threads help here the same way they help the
# reading calls below, even though nothing here talks to a model.
MAX_PARALLEL_READS = 4


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


def _read_one(path: Path, progress: ProgressFn) -> Tuple[Path, object, object]:
    """One file, parsed off the main thread. Returns (path, result, error) -- never raises."""
    progress("Opening the submitted files")
    try:
        return path, read_document(path), None
    except UnreadableDocument as exc:
        return path, None, exc


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

    Parsing each file is independent of every other and is where a large pack actually spends its
    time -- a sixty-page PDF and a dense workbook both take real wall-clock to turn into text, and
    nothing about that work touches another file. It runs in a thread pool for that reason.
    Redaction does not: a substitution-mode engine has to mask the same name the same way
    everywhere it appears, which means the mapping it builds while reading one document has to
    carry into the next, so that part stays a single sequential pass over the parsed results, in
    submission order, after every file has been parsed.
    """
    progress = progress or (lambda *a, **k: None)
    redact = redact or redact_segments
    should_redact = should_redact or (lambda path: False)

    ordered = [Path(p) for p in paths]
    diagrams = [p for p in ordered if is_image(p)]
    text_paths = [p for p in ordered if not is_image(p)]

    parts: List[str] = []
    documents: List[DocumentRef] = []
    mapping: Optional[dict] = None

    if text_paths:
        with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL_READS, len(text_paths))) as pool:
            # map() yields results in the order the inputs were given, whichever thread finishes
            # first, so what follows can stay a plain sequential fold over that order.
            read = list(pool.map(lambda p: _read_one(p, progress), text_paths))

        for path, result, error in read:
            if error is not None:
                logger.warning("%s could not be read: %s", path.name, error)
                documents.append(DocumentRef(name=path.name, kind="unreadable", note=str(error)))
                continue

            reference, segments = result
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
        # Every counter below is written from the pools that read the facet groups and the
        # diagrams, so all of them go through this. ``x += 1`` on an attribute is a load, an add
        # and a store, and two threads interleaving those lose one of the increments -- which for
        # _calls and _failures means the failure rate that decides whether to abandon a run is
        # computed from numbers that are quietly wrong.
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ entry point
    def run(self, paths: Sequence[Path]) -> EvidenceRecord:
        paths = [Path(p) for p in paths]

        # Everything that will happen, counted before any of it does, so the bar moves through
        # parsing as well as through the model calls. Parsing a sixty-page PDF is real time, and
        # a bar that stands at nothing until the first model call comes back has spent the part
        # of the run a person is most likely to be watching claiming that nothing is happening.
        self._total = self._estimate_units(paths)
        self._parsed = 0
        corpus = build_corpus(paths, self._parsed_one, self._redact, self._should_redact)
        if not corpus:
            raise IngestionFailed("None of the submitted files could be read.")
        cancellation.check(self._cancel)

        record = EvidenceRecord(documents=corpus.documents)
        # An oversized pack is read in several parts, which is only known once it is parsed. Any
        # extra calls that implies are added to the total rather than allowed to push the bar
        # past its own end.
        self._total += (len(self._split(corpus.text)) - 1) * len(FACET_GROUPS)

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
            part = f" (part {number} of {len(chunks)})" if len(chunks) > 1 else ""
            self._say(f"Reading the documents{part}")
            reply = self._ask(_READ_PROMPT, "INGEST_READ", questions=questions, corpus=chunk,
                              documents=inventory)
            self._step(f"Read the documents{part}")
            if reply is None:
                continue
            self._note_documents_used(reply.get("documents_used"))
            for facet in facets:
                answer = _to_answer(facet, reply.get(facet))
                if answer:
                    merged[facet] = _merge(merged.get(facet), answer)
        return merged

    def _called(self, failed: bool = False) -> None:
        """Record one model call, and whether it failed. Called from several threads."""
        with self._lock:
            self._calls += 1
            self._failures += int(failed)

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
        """Read submitted diagrams into the workflow they describe, in three passes.

        A workflow diagram *is* the intake's decision and state sheets: a box with branching
        arrows is a decision, an arrow's label is an outcome, the box it lands in is a state. So
        the reading keeps that structure all the way through rather than flattening it to prose
        and asking something later to rebuild a graph out of the prose.

        Each image is read on its own first, into an explicit list of boxes and arrows -- one
        picture at a time, the way a person would, and enumerated rather than described so that a
        box which was missed shows up as an arrow pointing at nothing instead of vanishing
        silently. A second pass is given every image's reading together and joins them into one
        graph, following an arrow that ran off the edge of one image into whatever picks it up in
        another. A third looks again, with the images still attached, at whatever the joined graph
        cannot account for -- see :meth:`_repair_structure`.

        An image that fails on its own is dropped and the rest still go through.
        """
        loaded: List[Tuple[Path, Tuple[str, bytes]]] = []
        for path in paths:
            try:
                loaded.append((path, load_image(path)))
            except UnreadableDocument as exc:
                record.documents.append(
                    DocumentRef(name=path.name, kind="unreadable", note=str(exc), is_image=True))
        if not loaded:
            return

        names = ", ".join(p.name for p in paths)
        cancellation.check(self._cancel)
        # Each image is read alone -- see the docstring above -- so one image's reading has
        # nothing to do with another's, the same reasoning that already runs the three facet
        # groups in parallel below.
        with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL_CALLS, len(loaded))) as pool:
            results = list(pool.map(
                lambda item: self._read_diagram_step(item[0], len(loaded), item[1][0], item[1][1]),
                enumerate(loaded, start=1)))
        readings = [r for r in results if r]

        if not readings:
            self._unreadable(record, loaded,
                             "the diagram could not be read. Supply a written description of the "
                             "flow it shows, or add it as a note.")
            return

        cancellation.check(self._cancel)
        self._say("Building the workflow")
        reply = self._synthesize_diagrams(readings, names)
        self._step("Built the workflow")
        if reply is None:
            self._unreadable(record, loaded,
                             "each diagram was read on its own but could not be put together "
                             "into a workflow. Supply a written description of the flow, or add "
                             "it as a note.")
            return

        structure = diagram_structure.clean(reply)
        structure = self._repair_structure(structure, [image for _, image in loaded], names)
        record.structure = structure

        for path, _ in loaded:
            record.documents.append(
                DocumentRef(name=path.name, kind="diagram", units=1, is_image=True))

        # A diagram that yielded a workflow has informed the reading, whether or not it also
        # produced a quotable observation. Without this an image carrying the entire structure of
        # the agent gets reported as "nothing rests on this document", which is both wrong and
        # exactly the sort of line that sends someone looking for a problem that is not there.
        if not diagram_structure.is_empty(structure):
            self._note_documents_used([path.name for path, _ in loaded])

        self._claim_observations(record, reply.get("observations"), names)
        if not diagram_structure.is_empty(structure):
            logger.info("Read a workflow from %d diagram(s): %s.", len(readings),
                        ", ".join(f"{count} {part}"
                                  for part, count in diagram_structure.counts(structure).items()))

    def _unreadable(self, record: EvidenceRecord, loaded: List[Tuple[Path, object]],
                    note: str) -> None:
        """Record every submitted image as needing a written description instead."""
        for path, _ in loaded:
            record.documents.append(
                DocumentRef(name=path.name, kind="unreadable", note=note, is_image=True))

    def _claim_observations(self, record: EvidenceRecord, observations, names: str) -> None:
        """What the diagrams establish in prose, alongside the graph they establish in structure.

        Nothing read from a picture can be checked against a span of text, so these are always
        recorded unverifiable and always surface for confirmation.
        """
        for entry in observations or []:
            if not isinstance(entry, dict):
                continue
            facet = str(entry.get("facet", "")).strip()
            statement = prose(entry, "statement")
            if facet in FACETS and statement:
                record.claims.append(Claim(
                    facet=facet, statement=statement, quote=str(entry.get("quote", "")).strip(),
                    status="unverifiable",
                    note="read from a diagram; confirm by hand",
                    source=SourceRef(document=names,
                                     locator=str(entry.get("locator", "")).strip(),
                                     kind=KIND_IMAGE)))

    def _read_diagram_step(self, index: int, total: int, path: Path,
                           image: Tuple[str, bytes]) -> Optional[Tuple[str, dict]]:
        """One pool worker's share: report progress, read the image, name it if it read."""
        self._say(f"Reading images ({index} of {total})")
        reading = self._read_one_diagram(path, image, index, total)
        self._step(f"Read images ({index} of {total})")
        return (path.name, reading) if reading else None

    def _read_one_diagram(self, path: Path, image: Tuple[str, bytes], index: int,
                          total: int) -> Optional[dict]:
        """One image, read on its own into boxes and arrows. None if the call failed."""
        user = prompt_loader.render(_DIAGRAM_READ_PROMPT, filename=path.name,
                                    position=f"{index} of {total}")
        try:
            reply = parse_json_object(council.deliberate(
                self._describe_images, prompt_loader.load(_SYSTEM_PROMPT), user,
                stage="INGEST_DIAGRAM_READ",
                tier=config.stage_tier("INGEST_DIAGRAM_READ", config.JUDGEMENT), images=[image]))
        except Exception as exc:
            self._called(failed=True)
            logger.warning("Could not read diagram %s: %s", path.name, exc)
            return None
        self._called()
        return reply if (reply.get("nodes") or reply.get("edges")) else None

    def _synthesize_diagrams(self, readings: List[Tuple[str, dict]],
                             names: str) -> Optional[dict]:
        """Join every image's own reading into one graph, in the intake's own vocabulary.

        Text only: everything visual was already pulled out into the node and edge lists this is
        given, so there is nothing left for this call to look at a picture for. Node references
        are namespaced by image first, since each image numbers its own boxes from one and this
        pass sees them all at once.
        """
        namespaced = diagram_structure.namespaced([reading for _, reading in readings])
        blocks = "\n\n".join(
            f"=== IMAGE {index} of {len(readings)}: {name} ===\n"
            f"{json.dumps(reading, indent=2)}"
            for index, ((name, _), reading) in enumerate(zip(readings, namespaced), start=1))

        user = prompt_loader.render(_DIAGRAM_SYNTHESIZE_PROMPT, facets=_facet_guide(),
                                    document=names, readings=blocks)
        try:
            reply = parse_json_object(council.deliberate(
                self._complete, prompt_loader.load(_SYSTEM_PROMPT), user,
                stage="INGEST_DIAGRAM_SYNTHESIZE",
                tier=config.stage_tier("INGEST_DIAGRAM_SYNTHESIZE", config.JUDGEMENT)))
        except Exception as exc:
            self._called(failed=True)
            logger.warning("Could not construct the workflow from the diagrams: %s", exc)
            return None
        self._called()
        return reply

    def _repair_structure(self, structure: dict, images: List[Tuple[str, bytes]],
                          names: str) -> dict:
        """Put whatever the joined graph cannot account for back to the images, once.

        The graph has properties it must have to be walkable at all -- every outcome leads
        somewhere, every state is reached by an outcome that exists, a branch has more than one
        branch. Where it does not, the reading is demonstrably incomplete, and the useful thing is
        that the audit says *which* points. That turns "read it again, more carefully" -- which a
        model cannot act on -- into a short list of specific arrows to go and follow, which it can.

        Once, not until clean. A second look settles the holes a first reading left; a third
        mostly re-litigates what the second decided, and each pass is a call against the largest
        tier with every image attached.
        """
        problems = diagram_structure.audit(structure)
        if not problems or not config.LLM_VISION:
            return structure

        cancellation.check(self._cancel)
        self._say("Checking the workflow against the images")
        logger.info("The workflow read from the diagrams left %d point(s) unresolved; "
                    "looking again.", len(problems))

        user = prompt_loader.render(
            _DIAGRAM_REPAIR_PROMPT, document=names,
            structure=diagram_structure.render(structure),
            problems="\n".join(f"- {problem}" for problem in problems))

        try:
            reply = parse_json_object(council.deliberate(
                self._describe_images, prompt_loader.load(_SYSTEM_PROMPT), user,
                stage="INGEST_DIAGRAM_REPAIR",
                tier=config.stage_tier("INGEST_DIAGRAM_REPAIR", config.JUDGEMENT), images=images))
        except Exception as exc:
            self._called(failed=True)
            self._step("Checked the workflow")
            logger.warning("Could not check the diagrams again: %s", exc)
            return structure
        self._called()
        self._step("Checked the workflow")

        repaired = diagram_structure.merge(structure, reply)
        remaining = diagram_structure.audit(repaired)
        logger.info("After looking again: %d of %d point(s) settled.",
                    len(problems) - len(remaining), len(problems))
        return repaired

    # ------------------------------------------------------------------ grounding
    def _attach_evidence(self, record: EvidenceRecord, corpus: str) -> None:
        """Turn each answer's citations into checked claims, and drop the ones that are not there.

        An answer whose every citation fails is not necessarily false, but nothing supports it, so
        it is marked for confirmation rather than presented as established.
        """
        checked = 0
        # Prepared once for the whole pass rather than per citation: see core.grounding.Source.
        prepared = Source(corpus)
        for answer in record.answers:
            kept: List[str] = []
            for citation in getattr(answer, "citations", []) or []:
                quote = str(citation.get("quote", "")).strip()
                checked += 1
                found, _ = prepared.locate(quote)
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
        """Put what is still open back to the documents, pass after pass.

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
        pass_of = f" ({number} of {self._passes})" if self._passes > 1 else ""
        self._say(f"Looking again at what is unanswered{pass_of}")
        established = "\n\n".join(
            f"## {FACET_HEADINGS.get(a.facet, a.facet)}\n{a.answer}"
            for a in record.answers if a.is_answered and a.answer)

        reply = self._ask(_RESOLVE_PROMPT, "INGEST_RESOLVE",
                          established=established or "Nothing yet.",
                          questions="\n".join(f"- {q}" for _, q in outstanding),
                          corpus=corpus)
        self._step(f"Looked again at what is unanswered{pass_of}")
        if reply is None:
            return

        by_question = {str(r.get("question", "")).strip().lower(): r
                       for r in (reply.get("resolved") or []) if isinstance(r, dict)}
        settled, to_ask = 0, 0

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
                # Nothing in the documents settled it, so it goes to a person. What it costs
                # them to see is one line; what it costs to drop something that mattered is a
                # part of the agent nobody tests. The intake stage decides what is actually
                # blocking, structurally, from the declaration itself -- see core.gaps.
                target.must_ask = list(target.must_ask) + [question]
                to_ask += 1

        if last:
            for answer in record.answers:
                answer.triaged = True

        logger.info("Pass %d of %d settled %d of %d outstanding point(s) from the documents.",
                    number, self._passes, settled, len(outstanding))
        if last and to_ask:
            logger.info("%d point(s) the documents did not settle are carried to the intake "
                        "stage.", to_ask)

    # ------------------------------------------------------------------ shared
    def _estimate_units(self, paths: Sequence[Path]) -> int:
        """How many things will happen, in the units the bar counts.

        A unit is one file parsed or one model call made. They are not the same size -- a model
        call takes longer than parsing a short document -- but they are the same *kind* of thing
        to a person watching: a discrete step that either has or has not happened. Weighting them
        against each other would need a model of how long each takes, which varies by document
        and by gateway, and would be wrong more often than this is uneven.
        """
        images = [p for p in paths if is_image(p)]
        sweeps = self._passes if self._resolve else 0
        # One call per diagram read on its own, one to join them into a workflow, and one more
        # where that workflow does not account for itself and is put back to the images.
        diagram_calls = len(images) + 2 if images else 0
        return len(paths) + len(FACET_GROUPS) + diagram_calls + sweeps

    def _say(self, message: str) -> None:
        """What is happening right now, without claiming it has happened.

        The bar and the line above it answer different questions -- how much is done, and what is
        being waited on -- and only the second can be known before a call returns. Advancing the
        first on starting is what makes a bar leap and then stall: three facet groups go out
        together, and counting them as sent puts it a quarter along in the first second.
        """
        with self._lock:
            done = self._parsed + self._done
        self._progress(message, done, self._total)

    def _parsed_one(self, message: str, *args, **kwargs) -> None:
        """One submitted file turned into text. Advances the bar through the parsing phase."""
        with self._lock:
            self._parsed += 1
            done = self._parsed
        self._progress(message, done, self._total)

    def _step(self, message: str) -> None:
        """One unit finished. Reported on completion, never on starting.

        The distinction is the whole difference between a bar that means something and one that
        does not: three facet groups go out at once, so counting them as they are sent puts the
        bar a quarter of the way along in the first second and then leaves it there for the
        length of the longest call.
        """
        with self._lock:
            self._done += 1
            done = self._parsed + self._done
        self._progress(message, done, self._total)

    def _ask(self, prompt: str, stage: str, **values) -> Optional[dict]:
        """One judgement-budget call, returning None rather than raising when it fails.

        ``stage`` names the call site for :func:`config.stage_tier` -- the facet-group reading and
        the resolution sweep are both JUDGEMENT-tier work but different calls a scenario space can want
        tuned independently, which a shared tier alone cannot express.
        """
        user = prompt_loader.render(prompt, **values)
        system = prompt_loader.load(_SYSTEM_PROMPT)
        try:
            reply = parse_json_object(council.deliberate(
                self._complete, system, user, stage=stage,
                tier=config.stage_tier(stage, config.JUDGEMENT)))
        except Exception as exc:
            self._called(failed=True)
            logger.warning("A reading call failed: %s", exc)
            return None
        self._called()
        return reply

    def _stop_if_mostly_failing(self, phase: str) -> None:
        with self._lock:
            calls, failures = self._calls, self._failures
        if calls and failures / calls > MAX_FAILURE_RATE:
            raise IngestionFailed(
                f"{failures} of {calls} model calls failed during {phase}. The output "
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
        answer=prose(entry, "answer"),
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
