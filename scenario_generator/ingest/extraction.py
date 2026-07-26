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

from ..core.evidence import (FACETS, Claim, DocumentRef, EvidenceRecord, FacetAnswer, SourceRef)
from ..core.grounding import rejection_report, verify
from ..llm import config, prompt_loader
from ..llm.gateway import ask_llm
from ..utils import parse_json_object
from .context_document import FACET_HEADINGS, FACET_QUESTIONS
from .readers import Segment, UnreadableDocument, chunk, read_document

logger = logging.getLogger(__name__)

CompletionFn = Callable[..., str]
ProgressFn = Callable[[str], None]

_SYSTEM_PROMPT = "ingest.system"
_SURVEY_PROMPT = "ingest.survey"
_SYNTHESISE_PROMPT = "ingest.synthesise"

# How many observations one synthesis call is shown. Well past what a single question usually
# gathers; a question that exceeds it is trimmed to the ones from the most documents, so breadth
# of source survives truncation ahead of repetition from one place.
MAX_OBSERVATIONS_PER_FACET = 160


def _facet_guide() -> str:
    return "\n".join(f"- {key}: {description}" for key, description in FACETS.items())


class DocumentExtractor:
    """Reads a pack of documents into verified observations, then answers each question from them."""

    def __init__(self, complete: Optional[CompletionFn] = None,
                 progress: Optional[ProgressFn] = None, synthesise: bool = True) -> None:
        self._complete = complete or ask_llm
        self._progress = progress or (lambda message: None)
        self._synthesise = synthesise

    # ------------------------------------------------------------------ entry point
    def run(self, paths: Sequence[Path]) -> EvidenceRecord:
        """Survey every document, then answer every question from what the survey found."""
        record = EvidenceRecord()

        for path in paths:
            path = Path(path)
            self._progress(f"Reading {path.name}")
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

        if self._synthesise:
            record.answers = self._answer_all(record)
        return record

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
            self._progress(f"{reference.name}: passage {number} of {len(passages)}")
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

    # ------------------------------------------------------------------ pass two
    def _answer_all(self, record: EvidenceRecord) -> List[FacetAnswer]:
        answers = []
        for number, facet in enumerate(FACETS, start=1):
            self._progress(f"Answering question {number} of {len(FACETS)}: "
                           f"{FACET_HEADINGS.get(facet, facet)}")
            answers.append(self._answer_one(facet, record.by_facet(facet)))
        answered = sum(1 for a in answers if a.is_answered)
        logger.info("Answered %d of %d questions from the observations.", answered, len(FACETS))
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
            return FacetAnswer(
                facet=facet, confidence="Low", sources=[c.id for c in selected],
                points=[c.statement for c in selected],
                unknowns=["Synthesis did not complete; the points below are the raw "
                          "observations, unassembled."])

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
        try:
            return self._complete(system, user,
                                  max_tokens=config.JUDGEMENT_MAX_TOKENS,
                                  reasoning_effort=config.JUDGEMENT_REASONING_EFFORT)
        except TypeError:                                  # a stub completion without the keywords
            return self._complete(system, user)


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
                      progress: Optional[ProgressFn] = None,
                      synthesise: bool = True) -> EvidenceRecord:
    """Read a pack of documents into a verified, synthesised evidence record."""
    return DocumentExtractor(complete=complete, progress=progress,
                             synthesise=synthesise).run(paths)


def record_to_json(record: EvidenceRecord, path: Path) -> None:
    Path(path).write_text(json.dumps(record.to_dict(), indent=2), encoding="utf-8")


def record_from_json(path: Path) -> EvidenceRecord:
    return EvidenceRecord.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
