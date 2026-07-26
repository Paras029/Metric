"""Writes owner-facing scenario text, batched with a solo mop-up for any ID a batch drops.

Description and turn plan are the only fields the model sets. Category comes from the intake's
declared Outcome Type and materiality from its own later sweep, so neither depends on this call.

Both fields are issued to the team that owns the agent, so neither may reveal the expected
outcome. That is enforced here by what the model is given rather than only by what it is told:
:meth:`ScenarioWriter._payload` withholds the scenario's terminal state entirely. Per-step
outcomes are supplied, because the tester has to know which condition to induce, but the route's
destination is never in the prompt and so cannot reach the challenge pack through this call.
"""
from __future__ import annotations

import json
import logging
from typing import Callable, List

from ..core.models import IntakeData, Scenario
from ..utils import chunks, parse_json_object
from . import prompt_loader
from .context import describe_use_case, supplementary_context
from .gateway import ask_llm

logger = logging.getLogger(__name__)

CompletionFn = Callable[[str, str], str]

_SYSTEM_PROMPT = "writer.system"
_GRAPH_PROMPT = "writer.graph_scenario"
_PROBE_PROMPT = "writer.probe"


class NullWriter:
    """Leaves the deterministic fallback text and default materiality in place."""

    def write(self, scenarios: List[Scenario], intake: IntakeData) -> List[Scenario]:
        return scenarios


class ScenarioWriter:
    def __init__(self, complete: CompletionFn = None, batch_size: int = 8,
                 context: str = "") -> None:
        self._complete = complete or ask_llm
        self._batch = batch_size
        self._context = context

    def write(self, scenarios: List[Scenario], intake: IntakeData) -> List[Scenario]:
        """Graph scenarios and probes are written by separate prompts, so batch them separately."""
        for group in ([s for s in scenarios if not s.is_probe],
                      [s for s in scenarios if s.is_probe]):
            for chunk in chunks(group, self._batch):
                done = self._write(chunk, intake)
                for scenario in chunk:                     # refill anything the batch dropped
                    if scenario.id not in done:
                        self._write([scenario], intake)
        return scenarios

    def _payload(self, scenario: Scenario) -> dict:
        """What the model is shown. The terminal state is deliberately absent: it is the answer
        key, and everything this call produces is issued to the team that owns the agent."""
        if scenario.is_probe:
            return {"id": scenario.id,
                    "probe": scenario.probe_family,
                    "intent": scenario.description,
                    "turns_to_write": len(scenario.turn_meta)}
        return {
            "id": scenario.id,
            "persona": scenario.persona.name,
            "starting_state": scenario.seeded_state,
            "steps": [{"decision": t.decision_name, "outcome": t.expected_variant,
                       "resulting_situation": t.next_state,
                       "driven_by_tester": t.input_source == "User"}
                      for t in scenario.turn_meta],
            "num_steps": len(scenario.turn_meta),
            "turns_to_write": scenario.turn_count,
            "capabilities_involved": scenario.capabilities,
            "touches_state_changing_action": scenario.touches_state_change,
        }

    def _write(self, chunk: List[Scenario], intake: IntakeData) -> set:
        """Call the model for these scenarios and apply the reply; return the IDs it filled."""
        name = _PROBE_PROMPT if chunk[0].is_probe else _GRAPH_PROMPT
        user = prompt_loader.render(
            name,
            use_case=describe_use_case(intake),
            context=supplementary_context(self._context),
            house_style=prompt_loader.load("shared.house_style"),
            scenarios=json.dumps([self._payload(s) for s in chunk], indent=2))
        try:
            reply = parse_json_object(
                self._complete(prompt_loader.load(_SYSTEM_PROMPT), user))
        except Exception as exc:
            logger.warning("Writer call left as fallback (%s): %s",
                           ", ".join(s.id for s in chunk), exc)
            return set()

        filled = set()
        for scenario in chunk:
            entry = reply.get(scenario.id)
            if not entry:
                continue
            scenario.description = str(entry.get("description", "")).strip() or scenario.description
            scenario.turn_plan = str(entry.get("turn_plan", "")).strip() or scenario.turn_plan
            filled.add(scenario.id)
        if len(filled) < len(chunk):
            logger.info("Batch filled %d/%d; refilling %s individually.",
                        len(filled), len(chunk), ", ".join(s.id for s in chunk if s.id not in filled))
        return filled


# --------------------------------------------------------------------------- materiality
