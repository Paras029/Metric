"""Reading a pack of submitted documents into a verified evidence record.

The shape of the pass is map-then-check. Each document is read into locatable passages, each
passage is put to a model that returns claims with the words they came from, and every claim is
then checked against that same passage before it is kept. The check is deterministic and lives in
:mod:`~scenario_generator.core.grounding`; nothing reaches the record on the model's word alone.

Rejections are counted and reported. A pass that discards much of what it extracted is saying
something about either the documents or the prompt, and that signal is lost if the number never
surfaces.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from ..core.evidence import FACETS, Claim, DocumentRef, EvidenceRecord, SourceRef
from ..core.grounding import verify
from ..llm import config, prompt_loader
from ..llm.gateway import ask_llm
from ..utils import parse_json_object
from .readers import UnreadableDocument, chunk, read_document

logger = logging.getLogger(__name__)

CompletionFn = Callable[..., str]
ProgressFn = Callable[[str], None]

_SYSTEM_PROMPT = "ingest.system"
_EXTRACT_PROMPT = "ingest.extract"


def _facet_guide() -> str:
    return "\n".join(f"- {key}: {description}" for key, description in FACETS.items())


class DocumentExtractor:
    """Reads a set of documents into claims, keeping only those a source supports."""

    def __init__(self, complete: Optional[CompletionFn] = None,
                 progress: Optional[ProgressFn] = None) -> None:
        self._complete = complete or ask_llm
        self._progress = progress or (lambda message: None)

    def run(self, paths: Sequence[Path]) -> EvidenceRecord:
        """Read every document and return the verified record.

        A document that cannot be read does not stop the run. It is recorded with the reason,
        because the person who submitted the pack is the one who can do something about it.
        """
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
            record.claims.extend(self._read_one(reference, segments))
        return record

    def _read_one(self, reference: DocumentRef, segments) -> List[Claim]:
        claims: List[Claim] = []
        passages = chunk(segments)
        for number, (text, span) in enumerate(passages, start=1):
            self._progress(f"{reference.name}: passage {number} of {len(passages)}")
            claims.extend(self._extract(reference, text, span))
        return claims

    def _call(self, user: str) -> str:
        """Extraction runs on the judgement budget: deciding what is relevant, and copying a
        quote exactly, both degrade badly when the model is hurried."""
        system = prompt_loader.load(_SYSTEM_PROMPT)
        try:
            return self._complete(system, user,
                                  max_tokens=config.JUDGEMENT_MAX_TOKENS,
                                  reasoning_effort=config.JUDGEMENT_REASONING_EFFORT)
        except TypeError:                                  # a stub completion without the keywords
            return self._complete(system, user)

    def _extract(self, reference: DocumentRef, text: str, span: str) -> List[Claim]:
        user = prompt_loader.render(_EXTRACT_PROMPT, facets=_facet_guide(),
                                    document=reference.name, locator=span, chunk=text)
        try:
            reply = parse_json_object(self._call(user))
        except Exception as exc:
            logger.warning("%s (%s): extraction call failed, passage skipped: %s",
                           reference.name, span, exc)
            return []

        entries = reply.get("claims")
        if not isinstance(entries, list):
            return []

        candidates = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            facet = str(entry.get("facet", "")).strip()
            statement = str(entry.get("statement", "")).strip()
            quote = str(entry.get("quote", "")).strip()
            if facet not in FACETS or not statement:
                continue                                   # a facet outside the vocabulary is noise
            candidates.append(Claim(
                facet=facet, statement=statement, quote=quote,
                source=SourceRef(document=reference.name,
                                 locator=str(entry.get("locator", "")).strip() or span,
                                 kind=reference.kind)))

        checked = verify(candidates, text)
        kept = [c for c in checked if c.is_usable]
        if len(kept) < len(checked):
            logger.info("%s (%s): kept %d of %d claims; the rest cited text that is not there.",
                        reference.name, span, len(kept), len(checked))
        return checked


def extract_documents(paths: Sequence[Path], complete: Optional[CompletionFn] = None,
                      progress: Optional[ProgressFn] = None) -> EvidenceRecord:
    """Read a pack of documents into a verified evidence record."""
    return DocumentExtractor(complete=complete, progress=progress).run(paths)


def record_to_json(record: EvidenceRecord, path: Path) -> None:
    Path(path).write_text(json.dumps(record.to_dict(), indent=2), encoding="utf-8")


def record_from_json(path: Path) -> EvidenceRecord:
    return EvidenceRecord.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
