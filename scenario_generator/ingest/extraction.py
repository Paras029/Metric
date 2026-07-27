"""Reading a pack of submitted documents into a verified, synthesised evidence record.

Two passes, and the split is the whole design.

**Survey** reads one passage at a time and brings back observations, each with the words it was
read from. It is deliberately generous: a passage is not asked to answer anything, only to
surrender whatever might bear on the questions later. Every observation is then checked against
its document, so what leaves this pass is what the documentation demonstrably says.

**Synthesis** takes one question at a time and sees *every* observation bearing on it, from every
document at once. This is the pass that assembles a process described in one section, its
exception three pages later, and the threshold that governs it in a table, into a single answer.

The earlier version of this module had only the first pass, and that was a structural limit
rather than a tuning problem: a fact stated nowhere in a single passage could not be expressed at
all, however the prompt was worded. Most of what a benchmark needs to know about an agent is of
exactly that kind.

Grounding did not go away, it moved. Observations are checked strictly. Answers are explicitly
derived and cite the observation ids they rest on, so a reader traces answer to observation to
page, and restructuring stays visible as restructuring.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from ..core.evidence import (FACETS, KIND_IMAGE, Claim, DocumentRef, EvidenceRecord, FacetAnswer,
                             SourceRef)
from ..core.grounding import rejection_report, verify
from ..llm import config, prompt_loader
from ..llm.gateway import ask_llm, ask_llm_with_images
from ..utils import parse_json_object
from .context_document import FACET_HEADINGS, FACET_QUESTIONS
from .readers import (Segment, UnreadableDocument, chunk, is_image, load_image, read_document)

logger = logging.getLogger(__name__)

CompletionFn = Callable[..., str]
ProgressFn = Callable[..., None]

_SYSTEM_PROMPT = "ingest.system"
_SURVEY_PROMPT = "ingest.survey"
_SYNTHESISE_PROMPT = "ingest.synthesise"
_DIAGRAM_PROMPT = "ingest.diagram"

# How many observations one synthesis call is shown. Well past what a single question usually
# gathers; a question that exceeds it is trimmed to the ones from the most documents, so breadth
# of source survives truncation ahead of repetition from one place.
MAX_OBSERVATIONS_PER_FACET = 160

# A run where most calls failed produces an artifact that looks like evidence and is not. Past
# this share of failures the run is abandoned rather than written, because a thin context file is
# indistinguishable from a document that genuinely said little, and the mistake surfaces much
# later as a thin benchmark.
MAX_FAILURE_RATE = 0.5


class IngestionFailed(RuntimeError):
    """Too much of the run failed for its output to be worth keeping."""


def _facet_guide() -> str:
    return "\n".join(f"- {key}: {description}" for key, description in FACETS.items())


class DocumentExtractor:
    """Reads a pack of documents into verified observations, then answers each question from them."""

    def __init__(self, complete: Optional[CompletionFn] = None,
                 progress: Optional[ProgressFn] = None, synthesise: bool = True,
                 describe_images: Optional[Callable[..., str]] = None) -> None:
        self._complete = complete or ask_llm
        self._describe_images = describe_images or ask_llm_with_images
        self._progress = progress or (lambda *args, **kwargs: None)
        self._done = 0
        self._total = 0
        self._synthesise = synthesise
        self._calls = 0
        self._failures = 0

    # ------------------------------------------------------------------ entry point
    def run(self, paths: Sequence[Path]) -> EvidenceRecord:
        """Survey every document, then answer every question from what the survey found."""
        record = EvidenceRecord()
        self._total = self._estimate_calls(paths)

        for path in paths:
            path = Path(path)
            self._progress(f"Opening {path.name}", self._done, self._total)

            if is_image(path):
                record.documents.append(self._read_diagram(path, record))
                continue

            try:
                reference, segments = read_document(path)
            except UnreadableDocument as exc:
                logger.warning("%s could not be read: %s", path.name, exc)
                record.documents.append(
                    DocumentRef(name=path.name, kind="unreadable", units=0, note=str(exc)))
                continue

            record.documents.append(reference)
            record.claims.extend(self._survey_document(reference, segments))

        _assign_ids(record.claims)
        logger.info("Survey found %d observation(s) across %d document(s); %d were kept.",
                    len(record.claims), len(record.documents), len(record.usable()))
        self._stop_if_mostly_failing("survey")

        if self._synthesise:
            record.answers = self._answer_all(record)
            self._stop_if_mostly_failing("synthesis")
        return record

    def _estimate_calls(self, paths: Sequence[Path]) -> int:
        """How many model calls this run will make, for the progress bar.

        Reading a document twice -- once to count its passages, once to send them -- is far
        cheaper than the calls themselves, and an honest total is what lets someone watching
        decide whether to wait. A document that cannot be read contributes nothing and is skipped
        here exactly as it will be skipped later.
        """
        passages = 0
        for path in paths:
            if is_image(path):
                passages += 1
                continue
            try:
                _, segments = read_document(Path(path))
            except UnreadableDocument:
                continue
            passages += len(chunk(segments))
        return passages + (len(FACETS) if self._synthesise else 0)

    def _step(self, message: str) -> None:
        self._done += 1
        self._progress(message, self._done, self._total)

    def _stop_if_mostly_failing(self, phase: str) -> None:
        """Abandon a run that mostly did not happen, rather than writing its remains."""
        if self._calls and self._failures / self._calls > MAX_FAILURE_RATE:
            raise IngestionFailed(
                f"{self._failures} of {self._calls} model calls failed during {phase}. "
                f"The output would not be a usable reading of these documents, so nothing was "
                f"written. Check the gateway credentials and try again; the log above names the "
                f"individual failures.")

    # ------------------------------------------------------------------ pass one
    def _survey_document(self, reference: DocumentRef, segments: List[Segment]) -> List[Claim]:
        """Read one document passage by passage.

        Verification runs against the whole document rather than the passage the observation came
        from. Passages overlap and a quote can legitimately straddle a boundary; checking against
        the passage alone rejected real quotes for an accident of where the text was cut.
        """
        whole = "\n\n".join(segment.text for segment in segments)
        passages = chunk(segments)
        claims: List[Claim] = []

        for number, (text, span) in enumerate(passages, start=1):
            self._step(f"Reading {reference.name} — passage {number} of {len(passages)}")
            candidates = self._survey_passage(reference, text, span)
            checked = verify(candidates, whole)
            for line in rejection_report(checked):
                logger.info("Discarded — %s", line)
            claims.extend(checked)

        return claims

    def _survey_passage(self, reference: DocumentRef, text: str, span: str) -> List[Claim]:
        user = prompt_loader.render(_SURVEY_PROMPT, facets=_facet_guide(),
                                    document=reference.name, locator=span, chunk=text)
        try:
            reply = parse_json_object(self._call(user))
        except Exception as exc:
            logger.warning("%s (%s): survey call failed, passage skipped: %s",
                           reference.name, span, exc)
            return []

        entries = reply.get("observations")
        if not isinstance(entries, list):
            return []

        found = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            facet = str(entry.get("facet", "")).strip()
            statement = str(entry.get("statement", "")).strip()
            if facet not in FACETS or not statement:
                continue                                   # a facet outside the vocabulary is noise
            found.append(Claim(
                facet=facet, statement=statement,
                quote=str(entry.get("quote", "")).strip(),
                source=SourceRef(document=reference.name,
                                 locator=str(entry.get("locator", "")).strip() or span,
                                 kind=reference.kind)))
        return found

    def _read_diagram(self, path: Path, record: EvidenceRecord) -> DocumentRef:
        """Describe a submitted diagram with a vision-capable model.

        Nothing read from an image can be checked against a span of text, so every observation is
        marked unverifiable and surfaces for human confirmation. That is honest rather than
        cautious: a diagram is often the clearest statement of the agent's branching, and refusing
        to read it at all would lose the most useful artefact in the pack.

        Where vision is unavailable the file is recorded as unreadable with a reason the submitter
        can act on, and the run continues.
        """
        self._step(f"Reading the diagram {path.name}")
        try:
            media_type, raw = load_image(path)
        except UnreadableDocument as exc:
            return DocumentRef(name=path.name, kind="unreadable", note=str(exc))

        user = prompt_loader.render(_DIAGRAM_PROMPT, facets=_facet_guide(), document=path.name)
        self._calls += 1
        try:
            reply = parse_json_object(self._describe_images(
                prompt_loader.load(_SYSTEM_PROMPT), user, [(media_type, raw)],
                max_tokens=config.JUDGEMENT_MAX_TOKENS,
                reasoning_effort=config.JUDGEMENT_REASONING_EFFORT))
        except Exception as exc:
            self._failures += 1
            logger.warning("Could not read the diagram %s: %s", path.name, exc)
            return DocumentRef(
                name=path.name, kind="unreadable",
                note=f"the diagram could not be read ({exc}). Supply a written description of "
                     f"the flow it shows, or add it as a note.")

        found = []
        for entry in reply.get("observations") or []:
            if not isinstance(entry, dict):
                continue
            facet = str(entry.get("facet", "")).strip()
            statement = str(entry.get("statement", "")).strip()
            if facet not in FACETS or not statement:
                continue
            found.append(Claim(
                facet=facet, statement=statement, quote=str(entry.get("quote", "")).strip(),
                source=SourceRef(document=path.name,
                                 locator=str(entry.get("locator", "")).strip(),
                                 kind=KIND_IMAGE)))

        # Run these through the same check as everything else. It has no text to match against
        # and so marks them unverifiable rather than verified, which is the point: a statement
        # read off a picture must reach a person before anything is built on it.
        record.claims.extend(verify(found, ""))
        return DocumentRef(name=path.name, kind="diagram", units=1)

    # ------------------------------------------------------------------ pass two
    def _answer_all(self, record: EvidenceRecord) -> List[FacetAnswer]:
        answers = []
        for number, facet in enumerate(FACETS, start=1):
            self._step(f"Answering {FACET_HEADINGS.get(facet, facet)} "
                       f"({number} of {len(FACETS)})")
            answers.append(self._answer_one(facet, record.by_facet(facet)))

        answered = sum(1 for a in answers if a.is_answered)
        failed = sum(1 for a in answers if a.failed)
        if failed:
            logger.warning("Answered %d of %d questions; %d could not be answered because the "
                           "model call failed.", answered, len(FACETS), failed)
        else:
            logger.info("Answered %d of %d questions from the observations.",
                        answered, len(FACETS))
        return answers

    def _answer_one(self, facet: str, claims: List[Claim]) -> FacetAnswer:
        """Assemble one question's answer from every observation bearing on it."""
        if not claims:
            return FacetAnswer(
                facet=facet, confidence="Low",
                unknowns=[FACET_QUESTIONS.get(facet, f"What does the agent do about {facet}?")])

        selected = _spread_across_documents(claims, MAX_OBSERVATIONS_PER_FACET)
        user = prompt_loader.render(
            _SYNTHESISE_PROMPT,
            heading=FACET_HEADINGS.get(facet, facet),
            question=FACETS[facet],
            observations="\n".join(
                f"- {c.id} [{c.source}] {c.statement}" for c in selected))

        try:
            reply = parse_json_object(self._call(user))
        except Exception as exc:
            logger.warning("Could not answer '%s': %s", facet, exc)
            # The observations are kept so the material is not lost, but the answer is marked
            # failed so that nothing downstream, and no count shown to the user, treats an
            # unassembled pile of observations as an answer to the question.
            return FacetAnswer(
                facet=facet, confidence="Low", failed=True,
                sources=[c.id for c in selected],
                points=[c.statement for c in selected],
                unknowns=["Synthesis did not complete; the points above are the raw "
                          "observations, unassembled. Run this stage again."])

        valid_ids = {c.id for c in selected}
        confidence = str(reply.get("confidence", "")).strip().title()
        return FacetAnswer(
            facet=facet,
            answer=str(reply.get("answer", "")).strip(),
            points=[str(p).strip() for p in (reply.get("points") or []) if str(p).strip()],
            unknowns=[str(u).strip() for u in (reply.get("unknowns") or []) if str(u).strip()],
            # An id the survey never produced is a citation to nothing, so it is dropped rather
            # than shown to a reader who would try to follow it.
            sources=[str(s).strip() for s in (reply.get("sources") or [])
                     if str(s).strip() in valid_ids],
            confidence=confidence if confidence in ("Low", "Medium", "High") else "Low",
        )

    # ------------------------------------------------------------------ shared
    def _call(self, user: str) -> str:
        """Both passes run on the judgement budget. Deciding what is relevant, copying a quote
        exactly, and assembling scattered material into one account all degrade badly when the
        model is hurried."""
        system = prompt_loader.load(_SYSTEM_PROMPT)
        self._calls += 1
        try:
            try:
                return self._complete(system, user,
                                      max_tokens=config.JUDGEMENT_MAX_TOKENS,
                                      reasoning_effort=config.JUDGEMENT_REASONING_EFFORT)
            except TypeError:                              # a stub completion without the keywords
                return self._complete(system, user)
        except Exception:
            self._failures += 1
            raise


def _assign_ids(claims: List[Claim]) -> None:
    """Give every observation a stable id, so an answer can cite it and a reader can find it."""
    for number, claim in enumerate(claims, start=1):
        claim.id = f"O-{number:03d}"


def _spread_across_documents(claims: List[Claim], limit: int) -> List[Claim]:
    """Trim to ``limit`` observations while keeping every document represented.

    Taking the first N would let one long document crowd out a short one that happened to hold
    the decisive sentence. Round-robin across documents until the limit is reached instead, so
    what is lost to truncation is repetition rather than breadth.
    """
    if len(claims) <= limit:
        return claims

    by_document: Dict[str, List[Claim]] = {}
    for claim in claims:
        by_document.setdefault(claim.source.document, []).append(claim)

    selected: List[Claim] = []
    while len(selected) < limit and any(by_document.values()):
        for queue in by_document.values():
            if queue and len(selected) < limit:
                selected.append(queue.pop(0))
    return selected


def extract_documents(paths: Sequence[Path], complete: Optional[CompletionFn] = None,
                      progress: Optional[ProgressFn] = None, synthesise: bool = True,
                      describe_images: Optional[Callable[..., str]] = None) -> EvidenceRecord:
    """Read a pack of documents into a verified, synthesised evidence record."""
    return DocumentExtractor(complete=complete, progress=progress, synthesise=synthesise,
                             describe_images=describe_images).run(paths)


def record_to_json(record: EvidenceRecord, path: Path) -> None:
    Path(path).write_text(json.dumps(record.to_dict(), indent=2), encoding="utf-8")


def record_from_json(path: Path) -> EvidenceRecord:
    return EvidenceRecord.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
