"""Writes owner-facing scenario text, batched with a solo mop-up for any ID a batch drops.

Description and turn plan are the only fields the model sets. Category comes from the intake's
declared Outcome Type and materiality from its own later sweep, so neither depends on this call.

Both fields are issued to the model owner, so neither may reveal the expected
outcome. That is enforced here by what the model is given rather than only by what it is told:
:meth:`ScenarioWriter._payload` withholds the scenario's terminal state entirely. Per-step
outcomes are supplied, because the tester has to know which condition to induce, but the route's
destination is never in the prompt and so cannot reach the challenge pack through this call.

A benchmark of any size is several chunks of scenarios, and every chunk needs its own call. Those
calls go out together rather than one after another: nothing in one chunk's text depends on
another's, so there is no reason the second should wait for the first to come back. What each
chunk's reply settles is still applied one chunk at a time, in the order the chunks were made, so
the result reads the same as it would have sequentially -- only the waiting is concurrent.
"""
from __future__ import annotations

import json
import logging
from typing import Callable, Iterable, List

from ..core.models import IntakeData, Scenario
from ..utils import chunks
from ..utils.replies import prose
from . import cancellation, config, prompt_loader
from .calling import call, call_batch, parsed_reply
from .context import describe_use_case, supplementary_context
from .gateway import ask_llm
from .selection import narrow

logger = logging.getLogger(__name__)

CompletionFn = Callable[[str, str], str]
ProgressFn = Callable[..., None]

_SYSTEM_PROMPT = "writer.system"
_GRAPH_PROMPT = "writer.graph_scenario"
_PROBE_PROMPT = "writer.probe"


class NullWriter:
    """Leaves the deterministic fallback text and default materiality in place."""

    def write(self, scenarios: List[Scenario], intake: IntakeData) -> List[Scenario]:
        return scenarios


class ScenarioWriter:
    def __init__(self, complete: CompletionFn = None, batch_size: int = None,
                 context: str = "", progress: ProgressFn = None, cancel=None) -> None:
        self._complete = complete or ask_llm
        self._batch = (batch_size if batch_size is not None
                       else config.stage_batch_size("WRITER", 8))
        self._context = context
        self._progress = progress or (lambda *args, **kwargs: None)
        self._cancel = cancel

    def write(self, scenarios: List[Scenario], intake: IntakeData,
              only: Iterable[str] = None) -> List[Scenario]:
        """Graph scenarios and probes are written by separate prompts, so batch them separately.

        Every chunk's call goes out together within its group; nothing in one chunk's text
        depends on another's. Replies are applied in the order the chunks were made regardless
        of which came back first, so a benchmark written this way reads exactly as it would have
        one chunk at a time -- only the waiting overlaps.

        ``only`` narrows the run to a set of ids and leaves every other scenario's text untouched.
        Nothing else changes: each scenario is written from its own route and the shared house
        style, so writing five of them says exactly what writing all three hundred would have said
        about those five.
        """
        cancellation.check(self._cancel)
        subject = narrow(scenarios, only)
        written = 0
        for group in ([s for s in subject if not s.is_probe],
                      [s for s in subject if s.is_probe]):
            if not group:
                continue
            pending = list(chunks(group, self._batch))
            system = prompt_loader.load(_SYSTEM_PROMPT)
            # Report as replies land rather than only once they are applied. Every call in a group
            # goes out together, so applying them is a fraction of a second at the end of a wait
            # that can run to minutes -- a bar driven off the applying loop sits at nothing and
            # then finishes at once, which is accurate about the code and useless to watch.
            sizes = [len(chunk) for chunk in pending]
            base = written
            replies = call_batch(self._complete, system, [self._render(c, intake) for c in pending],
                                 tier=config.stage_tier("WRITER", config.STANDARD),
                                 max_concurrency=config.stage_concurrency("WRITER"),
                                 cancel=self._cancel,
                                 on_progress=lambda done, _total, sizes=sizes, base=base: (
                                     self._progress(
                                         f"Written {base + sum(sizes[:done])} of "
                                         f"{len(subject)} scenarios",
                                         base + sum(sizes[:done]), len(subject))))

            for chunk, reply in zip(pending, replies):
                cancellation.check(self._cancel)
                filled = self._apply(chunk, reply)
                for scenario in chunk:                     # refill anything the batch dropped
                    if scenario.id not in filled:
                        cancellation.check(self._cancel)
                        self._write_one(scenario, intake)
                written += len(chunk)
                self._progress(f"Written {written} of {len(subject)} scenarios",
                               written, len(subject))
        return scenarios

    def _payload(self, scenario: Scenario) -> dict:
        """What the model is shown. The terminal state is deliberately absent: it is the answer
        key, and everything this call produces is issued to the model owner."""
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

    def _call(self, system: str, user: str) -> str:
        """Writing scenario text is mechanical, but it is read by the model owner, so it stays
        on the standard tier rather than the cheapest one."""
        return call(self._complete, system, user, tier=config.stage_tier("WRITER", config.STANDARD))

    def _render(self, chunk: List[Scenario], intake: IntakeData) -> str:
        """The user prompt for one chunk, built but not yet sent."""
        name = _PROBE_PROMPT if chunk[0].is_probe else _GRAPH_PROMPT
        return prompt_loader.render(
            name,
            use_case=describe_use_case(intake),
            context=supplementary_context(self._context),
            house_style=prompt_loader.load("shared.house_style"),
            scenarios=json.dumps([self._payload(s) for s in chunk], indent=2))

    def _apply(self, chunk: List[Scenario], reply) -> set:
        """Write a chunk's reply onto its scenarios; return the IDs it actually filled.

        ``reply`` is either the model's text or the exception raised getting it -- call_batch
        reports a failed call this way rather than raising, so it is handled here exactly like a
        reply that parsed but left some ids out: logged, and left for the individual refill.
        """
        parsed = parsed_reply(reply, "Writer call", ", ".join(s.id for s in chunk))

        filled = set()
        for scenario in chunk:
            entry = parsed.get(scenario.id)
            if not entry:
                continue
            scenario.description = prose(entry, "description") or scenario.description
            scenario.turn_plan = prose(entry, "turn_plan") or scenario.turn_plan
            filled.add(scenario.id)
        if len(filled) < len(chunk):
            logger.info("Batch filled %d/%d; refilling %s individually.",
                        len(filled), len(chunk), ", ".join(s.id for s in chunk if s.id not in filled))
        return filled

    def _write_one(self, scenario: Scenario, intake: IntakeData) -> None:
        """A single scenario a batch dropped, called and applied on its own.

        Rare enough, and small enough, that batching the mop-up too would not be worth the
        complexity -- most runs refill nothing at all.
        """
        chunk = [scenario]
        try:
            reply = self._call(prompt_loader.load(_SYSTEM_PROMPT), self._render(chunk, intake))
        except Exception as exc:                           # handled the same way _apply handles
            reply = exc                                     # a failed call inside a batch
        self._apply(chunk, reply)


# --------------------------------------------------------------------------- materiality
