"""Materiality assessment."""
from __future__ import annotations

import json
import logging
from typing import Callable, Dict, List

from metric.domain.models import CONFIDENCE, MATERIALITY, IntakeData, Scenario
from metric.shared import chunks, one_of
from metric.shared.replies import prose
from metric.llm import cancellation, config, prompts
from metric.llm.calling import call, call_batch, parsed_reply
from metric.llm.describe import describe_use_case, supplementary_context
from metric.llm.gateway import ask_llm
from metric.phases.scenario_generator.workflow.generation import peer_signals

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

    def __init__(self, complete: CompletionFn = None, batch_size: int = None,
                 context: str = "", progress: ProgressFn = None, cancel=None) -> None:
        self._complete = complete or ask_llm
        self._batch = (batch_size if batch_size is not None
                       else config.stage_batch_size("MATERIALITY_ASSESS", 10))
        self._context = context
        self._progress = progress or (lambda *args, **kwargs: None)
        self._cancel = cancel

    def assess(self, scenarios: List[Scenario], intake: IntakeData) -> List[Scenario]:
        cancellation.check(self._cancel)
        peers = peer_signals(scenarios)
        pending = list(chunks(scenarios, self._batch))
        # Reported as replies land, not as they are applied: every chunk is in flight at once,
        # so the applying loop below runs in a fraction of a second after a wait of minutes.
        sizes = [len(chunk) for chunk in pending]
        replies = call_batch(self._complete, prompts.load(_SYSTEM_PROMPT),
                             [self._render(c, intake, peers) for c in pending],
                             tier=config.stage_tier("MATERIALITY_ASSESS", config.MATERIALITY),
                             max_concurrency=config.stage_concurrency("MATERIALITY_ASSESS"),
                             cancel=self._cancel,
                             on_progress=lambda done, _total: self._progress(
                                 f"Weighed {sum(sizes[:done])} of {len(scenarios)} scenarios",
                                 sum(sizes[:done]), len(scenarios)))

        done_count = 0
        for chunk, reply in zip(pending, replies):
            cancellation.check(self._cancel)
            filled = self._apply(chunk, reply)
            for scenario in chunk:                         # refill anything the batch dropped
                if scenario.id not in filled:
                    cancellation.check(self._cancel)
                    self._assess_one(scenario, intake, peers)
            done_count += len(chunk)
            self._progress(f"Assessed {done_count} of {len(scenarios)} scenarios",
                           done_count, len(scenarios))
        return scenarios

    def _call(self, user: str) -> str:
        """Its own tier: this reasons about a scenario against its peers, but every chunk of the
        scenario space is in flight against it at once, which is what makes it worth separating from
        the single-shot judgement passes -- both in what it can be pointed at and in how hard a
        transient failure retries."""
        return call(self._complete, prompts.load(_SYSTEM_PROMPT), user,
                    tier=config.stage_tier("MATERIALITY_ASSESS", config.MATERIALITY))

    def _render(self, chunk: List[Scenario], intake: IntakeData, peers: Dict[str, dict]) -> str:
        """The user prompt for one chunk, built but not yet sent."""
        payload = [{
            "id": s.id,
            "category": s.category,
            "description": s.description,
            "capabilities_involved": s.capabilities,
            # Which block of the agent this tests, and what is already true when it starts. A
            # scenario covering verification only is a different proposition from one covering a
            # whole journey that happens to pass through it -- fewer steps, a narrower blast
            # radius -- and judging its weight without knowing that reads it as a thin scenario
            # rather than a scoped one.
            "tests_capability": s.capability_id,
            "already_established": s.precondition,
            "num_steps": len(s.turn_meta),
            "touches_state_changing_action": s.touches_state_change,
            "is_probe": s.is_probe,
            **({"expectation_if_probe": s.termination} if s.is_probe else {}),
            **peers.get(s.id, {}),
        } for s in chunk]
        return prompts.render(
            _TASK_PROMPT,
            mission=prompts.load("shared.mission"),
            scale=prompts.load("shared.materiality_scale"),
            use_case=describe_use_case(intake),
            context=supplementary_context(self._context),
            scenarios=json.dumps(payload, indent=2))

    def _apply(self, chunk: List[Scenario], reply) -> set:
        """Write a chunk's reply onto its scenarios; return the IDs it actually filled."""
        parsed = parsed_reply(reply, "Materiality call", ", ".join(s.id for s in chunk))

        filled = set()
        for scenario in chunk:
            entry = parsed.get(scenario.id)
            if not entry:
                continue
            scenario.materiality = one_of(entry.get("materiality"), MATERIALITY,
                                          scenario.materiality)
            scenario.materiality_confidence = one_of(entry.get("confidence"), CONFIDENCE, "Low")
            scenario.materiality_rationale = prose(entry, "rationale")
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
