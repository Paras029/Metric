"""Map an owner's free-text scenarios onto the intake's own decision vocabulary.

Batched, with a single-record retry for anything a batch omits. Any decision, outcome,
capability or persona outside the intake's vocabulary is discarded during validation rather
than guessed at.
"""
from __future__ import annotations

import json
import logging
from typing import Callable, Dict, List

from ..core.models import CATEGORIES, ExtractedMeta, IntakeData, OwnerScenario
from ..utils import chunks, parse_json_object
from . import config, prompt_loader
from .context import describe_use_case
from .gateway import ask_llm

logger = logging.getLogger(__name__)

CompletionFn = Callable[[str, str], str]

_SYSTEM_PROMPT = "extractor.system"
_TASK_PROMPT = "extractor.task"


class MetadataExtractor:
    def __init__(self, complete: CompletionFn = None, batch_size: int = 8) -> None:
        self._complete = complete or ask_llm
        self._batch = batch_size

    def extract(self, owner_scenarios: List[OwnerScenario], intake: IntakeData) -> List[ExtractedMeta]:
        results: Dict[str, ExtractedMeta] = {}
        for chunk in chunks(owner_scenarios, self._batch):
            results.update(self._extract(chunk, intake))
            for owner in chunk:                            # refill anything the batch dropped
                if owner.id not in results:
                    results.update(self._extract([owner], intake))
        return [results.get(s.id, ExtractedMeta(rationale="extraction failed")) for s in owner_scenarios]

    def _extract(self, chunk: List[OwnerScenario], intake: IntakeData) -> Dict[str, ExtractedMeta]:
        payload = [{"id": s.id, "description": s.description,
                    "declared_path": s.declared_path} for s in chunk]
        user = prompt_loader.render(
            _TASK_PROMPT,
            use_case=describe_use_case(intake),
            decisions="\n".join(f"- {d.id} ({d.name}): {' / '.join(d.variants)}" for d in intake.decisions),
            capabilities="\n".join(f"- {c.id} ({c.name})" for c in intake.capabilities),
            personas="\n".join(f"- {p.id} ({p.name})" for p in intake.personas),
            categories=", ".join(CATEGORIES),
            scenarios=json.dumps(payload, indent=2),
        )
        try:
            reply = parse_json_object(self._call(prompt_loader.load(_SYSTEM_PROMPT), user))
        except Exception as exc:
            logger.warning("Extraction call failed (%s): %s", ", ".join(s.id for s in chunk), exc)
            return {}
        return {s.id: self._validate(reply[s.id], intake) for s in chunk if reply.get(s.id)}

    def _call(self, system: str, user: str) -> str:
        """Mapping free text onto a closed vocabulary is classification, and anything outside that
        vocabulary is discarded by validation regardless -- so this runs on the fast tier."""
        try:
            return self._complete(system, user, tier=config.FAST)
        except TypeError:                                  # a stub without the keyword arguments
            return self._complete(system, user)

    def _validate(self, entry: dict, intake: IntakeData) -> ExtractedMeta:
        """Keep only decisions, outcomes, capabilities and personas the intake declares."""
        valid_variants = {d.id: set(d.variants) for d in intake.decisions}
        capability_ids = {c.id for c in intake.capabilities}

        path, dropped = [], 0
        for item in entry.get("decision_path") or []:
            decision_id = str(item.get("decision_id", "")).strip()
            variant = str(item.get("variant", "")).strip()
            if variant in valid_variants.get(decision_id, set()):
                path.append((decision_id, variant))
            else:
                dropped += 1

        category = str(entry.get("category", "")).strip()
        persona_id = str(entry.get("persona_id", "")).strip()
        raw_confidence = str(entry.get("confidence", "")).strip().lower()
        confidence = "Confident" if raw_confidence in ("confident", "high") else "Watch-out"
        rationale = str(entry.get("rationale", "")).strip()
        if dropped:
            rationale = f"{rationale} ({dropped} step(s) discarded as outside the vocabulary)".strip()

        return ExtractedMeta(
            decision_path=path,
            category=category if category in CATEGORIES else "",
            capabilities=[c for c in (entry.get("capabilities") or []) if c in capability_ids],
            persona_id=persona_id if intake.persona_by_id(persona_id) else "",
            confidence=confidence,
            rationale=rationale,
        )
