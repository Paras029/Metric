"""The review pass: a single sweep over the finished benchmark.

Every other pass sees one narrow slice — a batch of scenarios, or one owner scenario at a time.
This pass is given the whole picture: what the validation is for, what the agent is, its full
declared structure, what each registry field means, the deterministic redundancy evidence, a
digest of every scenario generated, and, where available, the scenarios the agent's own team
submitted. It runs with raised reasoning effort and smaller batches because it is asked to weigh
rather than classify.

Two powers:

    assess   settle each scenario's materiality with the whole set visible, and flag scenarios
             that are redundant, too vague to run, or describing something other than what they
             test. Written to its own columns, so any earlier assessment survives alongside it.
    propose  add scenarios that are materially missing. Capped, validated against the intake's
             vocabulary, and marked with origin "llm-proposed".

It cannot remove anything. Flagging a scenario as redundant is a recommendation for a human.
"""
from __future__ import annotations

import json
import logging
from typing import Callable, List, Optional, Tuple

from ..core.generation import peer_signals
from ..core.models import (CATEGORIES, MATERIALITY, IntakeData, OwnerScenario, Scenario)
from ..core.proposals import instantiate_proposals
from ..utils import chunks, parse_json_object
from . import config, prompt_loader
from .context import describe_graph, describe_use_case, digest, supplementary_context
from .gateway import ask_llm

logger = logging.getLogger(__name__)

CompletionFn = Callable[..., str]

DEFAULT_PROPOSAL_LIMIT = 15
DEFAULT_BATCH_SIZE = 6

REVIEW_FLAGS = ("Redundant", "Under-specified", "Mis-scoped")

_SYSTEM_PROMPT = "reviewer.system"
_ASSESS_PROMPT = "reviewer.assess"
_PROPOSE_PROMPT = "reviewer.propose"
_OWNER_PROMPT = "reviewer.owner_block"


def _owner_block(owner_scenarios: Optional[List[OwnerScenario]]) -> str:
    """The owner's own scenarios as context, or nothing at all. The trailing blank lines keep
    the block separated from the section that follows it in the task prompts."""
    if not owner_scenarios:
        return ""
    lines = "\n".join(f"- {s.id}: {s.description[:200]}" for s in owner_scenarios)
    return prompt_loader.render(_OWNER_PROMPT, owner_scenarios=lines) + "\n\n"


def _batch_payload(scenarios: List[Scenario], signals: dict) -> str:
    return json.dumps([{
        "id": s.id,
        "origin": s.origin,
        "category": s.category,
        "decision_path": s.path_str,
        "description": s.description,
        "turn_plan": s.turn_plan,
        "expected_outcome": s.termination,
        "existing_materiality": s.effective_materiality,
        "capabilities": s.capabilities,
        "touches_state_changing_action": s.touches_state_change,
        "num_steps": len(s.turn_meta),
        "num_turns": s.turn_count,
        **signals.get(s.id, {}),
    } for s in scenarios], indent=2)


class NullReviewer:
    """Leaves the benchmark untouched."""

    def review(self, scenarios: List[Scenario], intake: IntakeData,
               owner_scenarios: Optional[List[OwnerScenario]] = None
               ) -> Tuple[List[Scenario], List[Scenario]]:
        return scenarios, []


class ScenarioReviewer:
    def __init__(self, complete: CompletionFn = None, batch_size: int = DEFAULT_BATCH_SIZE,
                 context: str = "", proposal_limit: int = DEFAULT_PROPOSAL_LIMIT) -> None:
        self._complete = complete or ask_llm
        self._batch = batch_size
        self._context = context
        self._limit = proposal_limit

    def review(self, scenarios: List[Scenario], intake: IntakeData,
               owner_scenarios: Optional[List[OwnerScenario]] = None
               ) -> Tuple[List[Scenario], List[Scenario]]:
        """Settle materiality in place and return (scenarios, proposals)."""
        preamble = self._preamble(intake)
        signals = peer_signals(scenarios)
        shared = {"total": len(scenarios), "digest": digest(scenarios),
                  "owner": _owner_block(owner_scenarios)}

        for chunk in chunks(scenarios, self._batch):
            self._assess_batch(chunk, preamble, shared, signals)
        return scenarios, self._propose(intake, preamble, shared)

    def _preamble(self, intake: IntakeData) -> str:
        """Everything needed before a single scenario is seen. Built from the intake, so a
        supplementary context file is an addition rather than a prerequisite."""
        return "\n\n".join([
            prompt_loader.load("shared.mission"),
            "THE AGENT UNDER TEST\n\n"
            "An agentic AI system: software that holds a conversation, decides what to do at each "
            "step, and calls tools to act on those decisions. This one is described below exactly "
            f"as the team that built it declared it.\n\n{describe_use_case(intake)}",
            "ITS DECLARED STRUCTURE\n\n"
            "A state is a position the interaction can be in; a decision is a branch point with "
            "named outcomes; an outcome leads to another state or ends the interaction. This is "
            "the complete declared vocabulary — nothing exists outside it.\n\n"
            f"{describe_graph(intake)}{supplementary_context(self._context)}",
            prompt_loader.load("reviewer.approach"),
            prompt_loader.load("reviewer.field_guide"),
            prompt_loader.load("shared.materiality_scale"),
        ])

    def _call(self, user: str) -> str:
        """Judgement budgets: this pass reasons at length and must justify every verdict."""
        system = prompt_loader.load(_SYSTEM_PROMPT)
        try:
            return self._complete(system, user,
                                  max_tokens=config.JUDGEMENT_MAX_TOKENS,
                                  reasoning_effort=config.JUDGEMENT_REASONING_EFFORT)
        except TypeError:                          # a stub completion without the keywords
            return self._complete(system, user)

    def _assess_batch(self, chunk: List[Scenario], preamble: str, shared: dict,
                      signals: dict) -> None:
        user = f"{preamble}\n\n" + prompt_loader.render(
            _ASSESS_PROMPT, **shared, materiality=", ".join(MATERIALITY),
            batch=_batch_payload(chunk, signals))
        try:
            reply = parse_json_object(self._call(user))
        except Exception as exc:
            logger.warning("Review call left unchanged (%s): %s",
                           ", ".join(s.id for s in chunk), exc)
            return

        for scenario in chunk:
            entry = reply.get(scenario.id)
            if not entry:
                continue
            materiality = str(entry.get("materiality", "")).strip().title()
            if materiality in MATERIALITY:
                scenario.review_materiality = materiality
            scenario.review_rationale = str(entry.get("rationale", "")).strip()
            flag = str(entry.get("flag", "")).strip()
            scenario.review_flag = flag if flag in REVIEW_FLAGS else ""

    def _propose(self, intake: IntakeData, preamble: str, shared: dict) -> List[Scenario]:
        user = f"{preamble}\n\n" + prompt_loader.render(
            _PROPOSE_PROMPT, **shared, limit=self._limit,
            categories=", ".join(CATEGORIES), materiality=", ".join(MATERIALITY))
        try:
            reply = parse_json_object(self._call(user))
        except Exception as exc:
            logger.warning("Proposal call returned nothing usable: %s", exc)
            return []

        entries = reply.get("proposals") or []
        if not isinstance(entries, list):
            logger.warning("Proposal reply was not a list; ignoring.")
            return []
        if len(entries) > self._limit:
            logger.info("Model proposed %d scenarios; keeping the first %d.",
                        len(entries), self._limit)
        return instantiate_proposals(entries, intake, self._limit)
