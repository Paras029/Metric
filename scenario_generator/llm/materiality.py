"""Materiality assessment.

Run as its own sweep rather than alongside the description, because the tier depends on
comparison: whether a scenario matters is partly a question of what else the benchmark already
contains. Redundancy and relative depth are computed deterministically across the whole set
first, then attached to every batched call as evidence.

Every chunk's call goes out together rather than one after another -- nothing in one chunk's
verdict depends on another's chunk having been sent yet, only on the peer signals computed
upfront, so there is no reason the second call should wait for the first to come back.
"""
from __future__ import annotations

import json
import logging
from typing import Callable, Dict, List

from ..core.models import IntakeData, Scenario
from ..utils import chunks, parse_json_object
from . import config, prompt_loader
from .calling import call, call_batch
from .context import describe_use_case, supplementary_context
from .gateway import ask_llm
from ..core.generation import peer_signals

logger = logging.getLogger(__name__)

CompletionFn = Callable[[str, str], str]
ProgressFn = Callable[..., None]

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
                 context: str = "", progress: ProgressFn = None) -> None:
        self._complete = complete or ask_llm
        self._batch = batch_size
        self._context = context
        self._progress = progress or (lambda *args, **kwargs: None)

    def assess(self, scenarios: List[Scenario], intake: IntakeData) -> List[Scenario]:
        peers = peer_signals(scenarios)
        pending = list(chunks(scenarios, self._batch))
        replies = call_batch(self._complete, prompt_loader.load(_SYSTEM_PROMPT),
                             [self._render(c, intake, peers) for c in pending],
                             tier=config.JUDGEMENT)

        done_count = 0
        for chunk, reply in zip(pending, replies):
            filled = self._apply(chunk, reply)
            for scenario in chunk:                         # refill anything the batch dropped
                if scenario.id not in filled:
                    self._assess_one(scenario, intake, peers)
            done_count += len(chunk)
            self._progress(f"Assessed {done_count} of {len(scenarios)} scenarios",
                           done_count, len(scenarios))
        return scenarios

    def _call(self, user: str) -> str:
        """Judgement budgets: assigning a tier requires weighing a scenario against its peers."""
        return call(self._complete, prompt_loader.load(_SYSTEM_PROMPT), user,
                    tier=config.JUDGEMENT)

    def _render(self, chunk: List[Scenario], intake: IntakeData, peers: Dict[str, dict]) -> str:
        """The user prompt for one chunk, built but not yet sent."""
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
        return prompt_loader.render(
            _TASK_PROMPT,
            mission=prompt_loader.load("shared.mission"),
            scale=prompt_loader.load("shared.materiality_scale"),
            use_case=describe_use_case(intake),
            context=supplementary_context(self._context),
            scenarios=json.dumps(payload, indent=2))

    def _apply(self, chunk: List[Scenario], reply) -> set:
        """Write a chunk's reply onto its scenarios; return the IDs it actually filled.

        ``reply`` is either the model's text or the exception raised getting it -- call_batch
        reports a failed call this way rather than raising, so a batch failure and a reply that
        parsed but left some ids out are handled by the same path here.
        """
        if isinstance(reply, BaseException):
            logger.warning("Materiality call left as fallback (%s): %s",
                           ", ".join(s.id for s in chunk), reply)
            return set()
        try:
            parsed = parse_json_object(reply)
        except Exception as exc:
            logger.warning("Materiality call left as fallback (%s): %s",
                           ", ".join(s.id for s in chunk), exc)
            return set()

        filled = set()
        for scenario in chunk:
            entry = parsed.get(scenario.id)
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

    def _assess_one(self, scenario: Scenario, intake: IntakeData, peers: Dict[str, dict]) -> None:
        """A single scenario a batch dropped, called and applied on its own."""
        chunk = [scenario]
        try:
            reply = self._call(self._render(chunk, intake, peers))
        except Exception as exc:
            reply = exc
        self._apply(chunk, reply)


# --------------------------------------------------------------------------- reverse
