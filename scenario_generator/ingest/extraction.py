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

The resolution sweep. Questions the first reading left open are put back to the documents once,
directly. A question asked directly is often answered by material a general reading had no reason
to connect, and every question that survives to the modelling team costs days.

Splitting, but only as a fallback. A corpus past ``MAX_CORPUS_CHARS`` is divided and the parts
merged, with a warning. That is the exception now rather than the rule.
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from ..core.evidence import (FACETS, KIND_HUMAN, KIND_IMAGE, Claim, DocumentRef, EvidenceRecord,
                             FacetAnswer, SourceRef)
from ..core.grounding import locate
from ..llm import config, prompt_loader
from ..llm.gateway import ask_llm, ask_llm_with_images
from ..utils import parse_json_object
from .context_document import FACET_HEADINGS, FACET_QUESTIONS
from .readers import UnreadableDocument, is_image, load_image, read_document

logger = logging.getLogger(__name__)

CompletionFn = Callable[..., str]
ProgressFn = Callable[..., None]

_SYSTEM_PROMPT = "ingest.system"
_READ_PROMPT = "ingest.read"
_RESOLVE_PROMPT = "ingest.resolve"
_DIAGRAM_PROMPT = "ingest.diagram"

# Roughly 150k tokens of text. Well inside a million-token window, and far enough inside that the
# reply has room. A corpus past this is split, which is a worse reading, so the number is set to
# make that rare rather than routine.
MAX_CORPUS_CHARS = 600_000

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


def build_corpus(paths: Sequence[Path], progress: ProgressFn = None) -> Corpus:
    """Read every submitted file into one corpus. Deterministic; no model involved.

    Each passage keeps the marker that says where it came from, so a quote can be traced to a
    page after the fact. Images are set aside for the vision pass, which cannot read text.
    """
    progress = progress or (lambda *a, **k: None)
    parts: List[str] = []
    documents: List[DocumentRef] = []
    diagrams: List[Path] = []

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

        documents.append(reference)
        body = "\n\n".join(f"[{segment.locator}]\n{segment.text}" for segment in segments)
        parts.append(f"=== DOCUMENT: {path.name} ===\n\n{body}")

    return Corpus(text="\n\n\n".join(parts), documents=documents, diagrams=diagrams)


class DocumentExtractor:
    """Reads a submitted pack into answers, with every answer traceable to the documents."""

    def __init__(self, complete: Optional[CompletionFn] = None,
                 progress: Optional[ProgressFn] = None, resolve: bool = True,
                 describe_images: Optional[Callable[..., str]] = None) -> None:
        self._complete = complete or ask_llm
        self._describe_images = describe_images or ask_llm_with_images
        self._progress = progress or (lambda *args, **kwargs: None)
        self._resolve = resolve
        self._calls = 0
        self._failures = 0
        self._done = 0
        self._total = 0

    # ------------------------------------------------------------------ entry point
    def run(self, paths: Sequence[Path]) -> EvidenceRecord:
        paths = [Path(p) for p in paths]
        corpus = build_corpus(paths, self._progress)
        if not corpus:
            raise IngestionFailed("None of the submitted files could be read.")

        record = EvidenceRecord(documents=corpus.documents)
        self._total = self._estimate_calls(corpus)

        # Reading the text and reading the diagrams are independent, so they go together.
        chunks = self._split(corpus.text)
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL_CALLS) as pool:
            futures = [pool.submit(self._read_group, label, facets, chunks)
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
        self._stop_if_mostly_failing("reading")

        if self._resolve:
            self._resolve_unknowns(record, corpus.text)

        answered = sum(1 for a in record.answers if a.is_answered)
        logger.info("Read %d document(s) in %d model call(s). Answered %d of %d questions.",
                    len([d for d in record.documents if d.kind != "unreadable"]),
                    self._calls, answered, len(FACETS))
        return record

    # ------------------------------------------------------------------ reading
    def _split(self, text: str) -> List[str]:
        """One part unless the corpus is too large to send, which should be uncommon."""
        if len(text) <= MAX_CORPUS_CHARS:
            return [text]

        parts = [text[i:i + MAX_CORPUS_CHARS] for i in range(0, len(text), MAX_CORPUS_CHARS)]
        logger.warning(
            "The submitted pack is %d characters and was split into %d parts to be read. Reading "
            "it whole gives a better result; consider trimming what does not bear on the agent's "
            "behaviour.", len(text), len(parts))
        return parts

    def _read_group(self, label: str, facets: Sequence[str],
                    chunks: List[str]) -> Dict[str, FacetAnswer]:
        """Answer one group of questions from the whole corpus."""
        questions = "\n".join(
            f"- {facet}: {FACET_HEADINGS.get(facet, facet)} — {FACETS[facet]}"
            for facet in facets)

        merged: Dict[str, FacetAnswer] = {}
        for number, chunk in enumerate(chunks, start=1):
            self._step(f"Reading the documents for {label}"
                       + (f" (part {number} of {len(chunks)})" if len(chunks) > 1 else ""))
            reply = self._ask(_READ_PROMPT, questions=questions, corpus=chunk)
            if reply is None:
                continue
            for facet in facets:
                answer = _to_answer(facet, reply.get(facet))
                if answer:
                    merged[facet] = _merge(merged.get(facet), answer)
        return merged

    def _read_diagrams(self, paths: List[Path], record: EvidenceRecord) -> None:
        """Describe submitted diagrams. Several images of one flow are read as a sequence."""
        images = []
        for path in paths:
            try:
                images.append(load_image(path))
            except UnreadableDocument as exc:
                record.documents.append(
                    DocumentRef(name=path.name, kind="unreadable", note=str(exc)))
        if not images:
            return

        self._step(f"Reading {len(images)} diagram(s)")
        names = ", ".join(p.name for p in paths)
        user = prompt_loader.render(_DIAGRAM_PROMPT, facets=_facet_guide(), document=names)

        self._calls += 1
        try:
            reply = parse_json_object(self._describe_images(
                prompt_loader.load(_SYSTEM_PROMPT), user, images,
                max_tokens=config.JUDGEMENT_MAX_TOKENS,
                reasoning_effort=config.JUDGEMENT_REASONING_EFFORT))
        except Exception as exc:
            self._failures += 1
            logger.warning("Could not read the diagrams: %s", exc)
            for path in paths:
                record.documents.append(DocumentRef(
                    name=path.name, kind="unreadable",
                    note=f"the diagram could not be read ({exc}). Supply a written description "
                         f"of the flow it shows, or add it as a note."))
            return

        for path in paths:
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
        """Put what is still open back to the documents once, directly."""
        outstanding = [(facet, unknown) for facet, unknown in record.open_unknowns()]
        for facet in record.empty_facets():
            outstanding.append((facet, FACET_QUESTIONS.get(facet, "")))
        outstanding = [(f, q) for f, q in outstanding if q.strip()]
        if not outstanding:
            return

        self._step(f"Looking again for {len(outstanding)} unanswered point(s)")
        established = "\n\n".join(
            f"## {FACET_HEADINGS.get(a.facet, a.facet)}\n{a.answer}"
            for a in record.answers if a.is_answered and a.answer)

        reply = self._ask(_RESOLVE_PROMPT, established=established or "Nothing yet.",
                          questions="\n".join(f"- {q}" for _, q in outstanding),
                          corpus=corpus)
        if reply is None:
            return

        by_question = {str(r.get("question", "")).strip().lower(): r
                       for r in (reply.get("resolved") or []) if isinstance(r, dict)}
        settled = 0

        for facet, question in outstanding:
            found = by_question.get(question.strip().lower())
            if not found or str(found.get("status", "")).strip() != "answered":
                continue
            answer_text = str(found.get("answer", "")).strip()
            if not answer_text:
                continue

            target = record.answer_for(facet)
            if target is None:
                continue
            target.points = list(target.points) + [answer_text]
            target.unknowns = [u for u in target.unknowns if u.strip().lower() != question.strip().lower()]
            if not target.answer:
                target.answer = answer_text
            settled += 1

        if settled:
            logger.info("A second look at the documents settled %d of %d outstanding point(s).",
                        settled, len(outstanding))

    # ------------------------------------------------------------------ shared
    def _estimate_calls(self, corpus: Corpus) -> int:
        parts = max(1, -(-len(corpus.text) // MAX_CORPUS_CHARS))
        return len(FACET_GROUPS) * parts + (1 if corpus.diagrams else 0) + (1 if self._resolve
                                                                            else 0)

    def _step(self, message: str) -> None:
        self._done += 1
        self._progress(message, self._done, self._total)

    def _ask(self, prompt: str, **values) -> Optional[dict]:
        """One judgement-budget call, returning None rather than raising when it fails."""
        self._calls += 1
        user = prompt_loader.render(prompt, **values)
        system = prompt_loader.load(_SYSTEM_PROMPT)
        try:
            try:
                reply = self._complete(system, user,
                                       max_tokens=config.JUDGEMENT_MAX_TOKENS,
                                       reasoning_effort=config.JUDGEMENT_REASONING_EFFORT)
            except TypeError:                              # a stub without the keyword arguments
                reply = self._complete(system, user)
            return parse_json_object(reply)
        except Exception as exc:
            self._failures += 1
            logger.warning("A reading call failed: %s", exc)
            return None

    def _stop_if_mostly_failing(self, phase: str) -> None:
        if self._calls and self._failures / self._calls > MAX_FAILURE_RATE:
            raise IngestionFailed(
                f"{self._failures} of {self._calls} model calls failed during {phase}. The output "
                f"would not be a usable reading of these documents, so nothing was written. "
                f"Check the gateway credentials and try again.")


def _facet_guide() -> str:
    return "\n".join(f"- {key}: {description}" for key, description in FACETS.items())


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
                      describe_images: Optional[Callable[..., str]] = None) -> EvidenceRecord:
    """Read a submitted pack into a verified evidence record."""
    return DocumentExtractor(complete=complete, progress=progress, resolve=resolve,
                             describe_images=describe_images).run(paths)


def record_to_json(record: EvidenceRecord, path: Path) -> None:
    Path(path).write_text(json.dumps(record.to_dict(), indent=2), encoding="utf-8")


def record_from_json(path: Path) -> EvidenceRecord:
    return EvidenceRecord.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
