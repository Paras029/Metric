"""Materiality assessment.

Run as its own sweep rather than alongside the description, because the tier depends on
comparison: whether a scenario matters is partly a question of what else the benchmark already
contains. Redundancy and relative depth are computed deterministically across the whole set
first, then attached to every batched call as evidence.
"""
from __future__ import annotations

import json
import logging
from typing import Callable, Dict, List

from ..core.models import IntakeData, Scenario
from ..utils import chunks, parse_json_object
from . import config, prompt_loader
from .context import describe_use_case, supplementary_context
from .gateway import ask_llm
from ..core.generation import peer_signals

logger = logging.getLogger(__name__)

CompletionFn = Callable[[str, str], str]

_SYSTEM_PROMPT = "materiality.system"
_TASK_PROMPT = "materiality.task"


class NullMaterialityAssessor:
    """Leaves whatever materiality is already on the scenarios (deterministic default or unset)."""

    def assess(self, scenarios: List[Scenario], intake: IntakeData) -> List[Scenario]:
        return scenarios


class MaterialityAssessor:
    """A separate sweep, run after scenario text/category are finalised. Computes deterministic
    peer-group signals across the WHOLE scenario set first, then batches materiality calls with
    those signals attached, so each call can reason about redundancy beyond its own batch."""

    def __init__(self, complete: CompletionFn = None, batch_size: int = 10,
                 context: str = "") -> None:
        self._complete = complete or ask_llm
        self._batch = batch_size
        self._context = context

    def assess(self, scenarios: List[Scenario], intake: IntakeData) -> List[Scenario]:
        peers = peer_signals(scenarios)
        for chunk in chunks(scenarios, self._batch):
            done = self._assess(chunk, intake, peers)
            for scenario in chunk:
                if scenario.id not in done:                # refill anything the batch dropped
                    self._assess([scenario], intake, peers)
        return scenarios

    def _call(self, user: str) -> str:
        """Judgement budgets: assigning a tier requires weighing a scenario against its peers."""
        system = prompt_loader.load(_SYSTEM_PROMPT)
        try:
            return self._complete(system, user,
                                  max_tokens=config.JUDGEMENT_MAX_TOKENS,
                                  reasoning_effort=config.JUDGEMENT_REASONING_EFFORT)
        except TypeError:                          # a stub completion without the keywords
            return self._complete(system, user)

    def _assess(self, chunk: List[Scenario], intake: IntakeData, peers: Dict[str, dict]) -> set:
        payload = [{
            "id": s.id,
            "category": s.category,
            "description": s.description,
            "capabilities_involved": s.capabilities,
            "num_steps": len(s.turn_meta),
            "touches_state_changing_action": s.touches_state_change,
            "is_probe": s.is_probe,
            **({"expectation_if_probe": s.termination} if s.is_probe else {}),
            **peers.get(s.id, {}),
        } for s in chunk]
        user = prompt_loader.render(
            _TASK_PROMPT,
            mission=prompt_loader.load("shared.mission"),
            scale=prompt_loader.load("shared.materiality_scale"),
            use_case=describe_use_case(intake),
            context=supplementary_context(self._context),
            scenarios=json.dumps(payload, indent=2))
        try:
            reply = parse_json_object(self._call(user))
        except Exception as exc:
            logger.warning("Materiality call left as fallback (%s): %s",
                           ", ".join(s.id for s in chunk), exc)
            return set()

        filled = set()
        for scenario in chunk:
            entry = reply.get(scenario.id)
            if not entry:
                continue
            materiality = str(entry.get("materiality", "")).strip().title()
            if materiality in ("Low", "Medium", "High", "Critical"):
                scenario.materiality = materiality
            confidence = str(entry.get("confidence", "")).strip().title()
            scenario.materiality_confidence = confidence if confidence in ("Low", "Medium", "High") else "Low"
            scenario.materiality_rationale = str(entry.get("rationale", "")).strip()
            filled.add(scenario.id)
        if len(filled) < len(chunk):
            logger.info("Materiality batch filled %d/%d; refilling %s individually.",
                        len(filled), len(chunk), ", ".join(s.id for s in chunk if s.id not in filled))
        return filled


# --------------------------------------------------------------------------- reverse
