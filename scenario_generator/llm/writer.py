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
from dataclasses import replace
from typing import Callable, List

from ..core.models import IntakeData, Scenario
from ..utils import chunks, parse_json_object
from ..utils.replies import prose
from . import cancellation, config, prompt_loader, quality
from .calling import call, call_batch, parsed_reply
from .context import describe_use_case, supplementary_context
from .gateway import ask_llm

logger = logging.getLogger(__name__)

CompletionFn = Callable[[str, str], str]
ProgressFn = Callable[..., None]

_SYSTEM_PROMPT = "writer.system"
_GRAPH_PROMPT = "writer.graph_scenario"
_PROBE_PROMPT = "writer.probe"
_REPAIR_PROMPT = "writer.repair"


# A name is a handle, and a handle that runs to a line and a half is not one. The cap is generous
# against the six words the prompt asks for, because cutting a name that came back slightly long
# is better than showing something that breaks the row it sits in.
MAX_NAME_CHARS = 70

_NAME_PREFIXES = ("test that ", "test ", "verify that ", "verify ", "check that ", "check ",
                  "scenario where ", "scenario: ", "validate that ", "ensure that ")


def _clean_name(raw: str) -> str:
    """One line, no trailing stop, no throat-clearing, and short enough to sit in a column.

    Every one of these is something the prompt already asks for and a model still occasionally
    returns anyway. Fixing it here rather than re-asking is the right trade for a field this
    small: the cost of a second call is real and the cost of trimming a prefix is nothing.
    """
    name = " ".join(raw.split()).strip().strip('"')
    lowered = name.lower()
    for prefix in _NAME_PREFIXES:
        if lowered.startswith(prefix):
            name = name[len(prefix):]
            break
    name = name.rstrip(".").strip()
    if len(name) > MAX_NAME_CHARS:
        name = name[:MAX_NAME_CHARS - 1].rsplit(" ", 1)[0].rstrip(" ,;:—-") + "…"
    return name[:1].upper() + name[1:] if name else ""


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

    def write(self, scenarios: List[Scenario], intake: IntakeData) -> List[Scenario]:
        """Graph scenarios and probes are written by separate prompts, so batch them separately.

        Every chunk's call goes out together within its group; nothing in one chunk's text
        depends on another's. Replies are applied in the order the chunks were made regardless
        of which came back first, so a benchmark written this way reads exactly as it would have
        one chunk at a time -- only the waiting overlaps.
        """
        cancellation.check(self._cancel)
        written = 0
        for group in ([s for s in scenarios if not s.is_probe],
                      [s for s in scenarios if s.is_probe]):
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
                                         f"{len(scenarios)} scenarios",
                                         base + sum(sizes[:done]), len(scenarios))))

            for chunk, reply in zip(pending, replies):
                cancellation.check(self._cancel)
                filled = self._apply(chunk, reply)
                for scenario in chunk:                     # refill anything the batch dropped
                    if scenario.id not in filled:
                        cancellation.check(self._cancel)
                        self._write_one(scenario, intake)
                written += len(chunk)
                self._progress(f"Written {written} of {len(scenarios)} scenarios",
                               written, len(scenarios))

        self._repair(scenarios, intake)
        return scenarios

    def _repair(self, scenarios: List[Scenario], intake: IntakeData) -> int:
        """Audit what was written and put only the specific failures back. Returns how many.

        The pattern the diagram reading and the intake draft already use, applied to the one pass
        that did not have it and needs it most: produce, check with code, and re-ask about exactly
        what failed. What makes it worth a call is that the fault is *named* -- "your description
        states how the interaction ends" is something a model can act on, where "write it better"
        returns something different rather than something better.

        Once, not until clean. A second attempt fixes what a first got wrong; a third mostly
        rewrites what the second decided, and every pass costs a call per scenario. Anything still
        failing after this is left as it is and reported, because a scenario nobody looked at is
        worse than a scenario somebody has been told about.
        """
        faults = [(s, quality.problems(s)) for s in scenarios]
        broken = [(s, problems) for s, problems in faults if problems]
        if not broken:
            return 0

        logger.info("%d of %d scenarios need rewriting: %s", len(broken), len(scenarios),
                    ", ".join(s.id for s, _ in broken[:10]))
        leaks = sum(1 for _, problems in broken
                    if any("how the interaction ends" in p for p in problems))
        if leaks:
            logger.warning("%d scenario(s) gave the ending away in text that is issued to the "
                           "model owner. Rewriting them.", leaks)

        system = prompt_loader.load(_SYSTEM_PROMPT)
        prompts = [self._render_repair(s, problems, intake) for s, problems in broken]
        self._progress(f"Rewriting {len(broken)} scenarios", 0, len(broken))
        replies = call_batch(self._complete, system, prompts,
                             tier=config.stage_tier("WRITER", config.STANDARD),
                             max_concurrency=config.stage_concurrency("WRITER"),
                             cancel=self._cancel,
                             on_progress=lambda done, total: self._progress(
                                 f"Rewritten {done} of {total} scenarios", done, total))

        fixed = 0
        for (scenario, _), reply in zip(broken, replies):
            cancellation.check(self._cancel)
            if isinstance(reply, BaseException):
                logger.warning("Rewrite of %s did not come back: %s", scenario.id, reply)
                continue
            try:
                entry = parse_json_object(reply)
            except Exception as exc:
                logger.warning("Rewrite of %s would not parse: %s", scenario.id, exc)
                continue
            # Applied to a copy first: a rewrite that fixes one fault and introduces another is
            # not an improvement, and the text already there at least came from a call that saw
            # the whole chunk. Only a strictly cleaner result replaces it.
            candidate = replace(scenario)
            candidate.name = _clean_name(prose(entry, "name")) or candidate.name
            candidate.description = prose(entry, "description") or candidate.description
            candidate.turn_plan = prose(entry, "turn_plan") or candidate.turn_plan
            if len(quality.problems(candidate)) < len(quality.problems(scenario)):
                scenario.name, scenario.description, scenario.turn_plan = (
                    candidate.name, candidate.description, candidate.turn_plan)
                fixed += 1

        remaining = quality.leaking(scenarios)
        if remaining:
            logger.warning(
                "%d scenario(s) still describe how the interaction ends after being rewritten: "
                "%s. They are in the pack as written -- read them before it goes out.",
                len(remaining), ", ".join(s.id for s in remaining[:10]))
        logger.info("Rewrote %d of %d.", fixed, len(broken))
        return fixed

    def _render_repair(self, scenario: Scenario, problems: List[str], intake: IntakeData) -> str:
        """The prompt for one scenario being written again, with its faults named."""
        current = json.dumps({"name": scenario.name, "description": scenario.description,
                              "turn_plan": scenario.turn_plan}, indent=2)
        return prompt_loader.render(
            _REPAIR_PROMPT,
            use_case=describe_use_case(intake),
            context=supplementary_context(self._context),
            house_style=prompt_loader.load("shared.house_style"),
            current=current,
            problems="\n".join(f"- {problem}" for problem in problems),
            scenario=json.dumps(self._payload(scenario), indent=2))

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
            scenario.name = _clean_name(prose(entry, "name")) or scenario.name
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
