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
from .gateway import ask_llm
from .prompts import describe_use_case

logger = logging.getLogger(__name__)

CompletionFn = Callable[[str, str], str]

_EXTRACT_SYSTEM = (
    "You assist an independent model-risk validation team (MRMG). You map a modeling team's own "
    "free-text test scenarios onto a known decision model of the same agent, using ONLY the "
    "provided vocabulary of decision IDs, outcomes, capability IDs and persona IDs. Return only "
    "valid JSON."
)

_EXTRACT_USER = """THE USE CASE

{use_case}

Known decisions (ID: name — outcomes). Use ONLY these IDs and outcomes:
{decisions}

Known capabilities (ID: name). Use ONLY these IDs:
{capabilities}

Known personas (ID: name). Use ONLY these IDs:
{personas}

Categories: {categories}

For each modeling-team scenario below, return an object with:
- decision_path: list of {{"decision_id": "DEC-xx", "variant": "<one of that decision's outcomes>"}}
  in the order the scenario exercises them.
- category: one of the categories above.
- capabilities: list of capability IDs the scenario touches.
- persona_id: the persona the scenario is written for. Choose a non-default persona ONLY where
  the text gives a positive behavioural signal for it — an explicitly uncooperative, confused,
  adversarial, or otherwise distinctive user. Vocabulary, tone, or level of detail in the
  scenario's own writing is not such a signal. Where the text says nothing about the user's
  behaviour, return the default persona rather than guessing, since persona is scored as a hard
  gate and a wrong guess discards an otherwise valid match. Use "" only if the scenario
  describes no user at all.
- confidence: binary, and about the EXTRACTION, not the scenario's quality.
  "Confident" — the text names or unambiguously implies each decision and its outcome, in order,
  and any persona signal is explicit. Another reader would extract the same path.
  "Watch-out" — anything less: an inferred step, an outcome you had to assume, an ambiguous
  ordering, a guessed persona, or a scenario vague enough that a different reader could map it
  differently. When genuinely torn between the two, choose "Watch-out".
- rationale: one sentence on how you mapped it, noting anything ambiguous.
If a scenario maps to no known decision, return an empty decision_path and confidence "Watch-out".
Never invent an ID or outcome not listed above.

Scenarios (JSON):
{scenarios}

Work through the scenarios one at a time and return a separate, independent object for every
"id" in the list. Return ONLY a single JSON object mapping each "id" to its object. Do not wrap it
in markdown fences, and keep each string value on a single line."""


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
        user = _EXTRACT_USER.format(
            use_case=describe_use_case(intake),
            decisions="\n".join(f"- {d.id} ({d.name}): {' / '.join(d.variants)}" for d in intake.decisions),
            capabilities="\n".join(f"- {c.id} ({c.name})" for c in intake.capabilities),
            personas="\n".join(f"- {p.id} ({p.name})" for p in intake.personas),
            categories=", ".join(CATEGORIES),
            scenarios=json.dumps(payload, indent=2),
        )
        try:
            reply = parse_json_object(self._complete(_EXTRACT_SYSTEM, user))
        except Exception as exc:
            logger.warning("Extraction call failed (%s): %s", ", ".join(s.id for s in chunk), exc)
            return {}
        return {s.id: self._validate(reply[s.id], intake) for s in chunk if reply.get(s.id)}

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
