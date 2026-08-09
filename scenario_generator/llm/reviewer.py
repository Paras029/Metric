"""The review pass: a single sweep over the finished benchmark.

Every other pass sees one narrow slice — a batch of scenarios, or one owner scenario at a time.
This pass is given the whole picture: what the validation is for, what the agent is, its full
declared structure, what each registry field means, the deterministic redundancy evidence, a
digest of every scenario generated, and, where available, the scenarios the model owner
submitted. It runs with raised reasoning effort and smaller batches because it is asked to weigh
rather than classify.

Two calls, and the split between them follows what each is actually reading:

    assess    one call per chunk of scenarios, settling three things at once. What failure here
              would cost, judged with the whole set visible. How the route actually ends, against
              the ending the intake declared -- category is deterministic everywhere else, taken
              from the Outcome Type on the state a route finishes in, which makes it the column a
              wrong or blank declaration corrupts without anything noticing, and a disagreement
              here usually means the workbook needs correcting rather than the scenario. And
              whether anything is wrong with the scenario: redundant, too vague to run, or
              describing something other than what it tests.

              These are one call rather than three because they are answered from the same
              material -- the route, the expected outcome and the description. Reading it once to
              answer all three is cheaper and more coherent than reading it three times, and it
              stops one batch size from having to serve two incompatible kinds of judgement.

    propose   one call for the whole benchmark, adding scenarios that are materially missing.
              Capped, validated against the intake's vocabulary, and marked "llm-proposed". It is
              asked about the set rather than about any chunk of it, so it has nothing to batch
              and nothing to share with a per-scenario reading.

Every verdict is written to its own column beside the value it disagrees with, never over it, so
both readings stay visible and a person rules. It cannot remove anything: flagging a scenario as
redundant is a recommendation for a human.
"""
from __future__ import annotations

import json
import logging
from typing import Callable, Iterable, List, Optional, Tuple

from ..core.generation import peer_signals
from ..core.models import (CATEGORIES, MATERIALITY, IntakeData, OwnerScenario, Scenario)
from ..core.proposals import instantiate_proposals
from ..utils import chunks, one_of, parse_json_object
from ..utils.replies import prose
from . import cancellation, config, prompt_loader
from .calling import call, call_batch, parsed_reply
from .context import describe_graph, describe_use_case, digest, supplementary_context
from .gateway import ask_llm
from .selection import narrow

logger = logging.getLogger(__name__)

CompletionFn = Callable[..., str]
ProgressFn = Callable[..., None]

DEFAULT_PROPOSAL_LIMIT = 15

# How many scenarios one review call weighs. Small, because this is the most demanding read in the
# pipeline: it settles materiality against the whole benchmark, checks the declared ending and
# looks for what is wrong with each scenario, with the full field guide in view. A chunk large
# enough to skim is a chunk that gets skimmed.
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
    def __init__(self, complete: CompletionFn = None, batch_size: int = None,
                 context: str = "", proposal_limit: int = DEFAULT_PROPOSAL_LIMIT,
                 progress: ProgressFn = None, cancel=None) -> None:
        self._complete = complete or ask_llm
        self._batch = (batch_size if batch_size is not None
                       else config.stage_batch_size("REVIEWER_ASSESS", DEFAULT_BATCH_SIZE))
        self._context = context
        self._limit = proposal_limit
        self._progress = progress or (lambda *args, **kwargs: None)
        self._cancel = cancel

    def review(self, scenarios: List[Scenario], intake: IntakeData,
               owner_scenarios: Optional[List[OwnerScenario]] = None,
               only: Iterable[str] = None) -> Tuple[List[Scenario], List[Scenario]]:
        """Settle every judged column in place and return (scenarios, proposals).

        **One sweep, not one per column.** Materiality, the ending a scenario is filed under and
        anything wrong with it are three questions answered from the same material -- the route,
        the expected outcome and the description -- and reading that material once to answer all
        three is both cheaper and more coherent than reading it three times. Splitting them also
        made the batch size mean two different things at once, since a mechanical category check
        tolerates a far coarser chunk than a materiality judgement does, so a benchmark could not
        be tuned for one without mistuning the other.

        Proposing what enumeration could not reach stays a separate call, and genuinely is one: it
        is asked about the benchmark as a whole rather than about any chunk of it, so it has
        nothing to batch and nothing to share with a per-scenario reading.

        ``only`` narrows which scenarios are *judged*. The digest, the totals and the redundancy
        signals are built from the whole benchmark regardless, because every question this pass
        asks is comparative -- whether something is redundant, whether coverage is thin here --
        and answering it against a handful of hand-picked scenarios would answer a different
        question. A subset run also skips the proposal call: proposing is a reading of what the
        benchmark as a whole is missing, and it has nothing to do with which rows were re-judged.
        """
        cancellation.check(self._cancel)
        preamble = self._preamble(intake)
        signals = peer_signals(scenarios)
        shared = {"total": len(scenarios), "digest": digest(scenarios),
                  "owner": _owner_block(owner_scenarios)}

        subject = narrow(scenarios, only)
        propose = only is None
        assess_chunks = list(chunks(subject, self._batch))
        total = len(assess_chunks) + (1 if propose else 0)
        done = 0

        done = self._sweep(
            assess_chunks, "Reviewed",
            config.stage_tier("REVIEWER_ASSESS", config.JUDGEMENT),
            config.stage_concurrency("REVIEWER_ASSESS"), done, total, len(subject),
            lambda chunk: self._render_assess(chunk, preamble, shared, signals),
            self._apply_assessment)

        if not propose:
            self._progress("Review complete", total, total)
            return scenarios, []

        cancellation.check(self._cancel)
        self._progress("Looking for what enumeration could not reach", done, total)
        proposals = self._propose(intake, preamble, shared)
        self._progress("Review complete", total, total)
        return scenarios, proposals

    def _sweep(self, pending: List[List[Scenario]], label: str, tier, concurrency: int,
               done: int, total: int, subject_count: int,
               render: Callable[[List[Scenario]], str],
               apply_reply: Callable[[List[Scenario], object], None]) -> int:
        """One column judged across the whole benchmark. Returns the running progress count.

        Every chunk's call goes out together: each judges its own scenarios against the
        whole-set digest built once above, so none of them waits on another's reply. Replies are
        applied in the order the chunks were made regardless of which came back first.
        """
        if not pending:
            return done
        # Reported as replies land, not as they are applied. Every chunk is in flight at once, so
        # the applying loop below takes a fraction of a second at the end of a wait that can run
        # to minutes, and a bar driven off it stands still and then finishes all at once.
        sizes = [len(chunk) for chunk in pending]
        replies = call_batch(self._complete, prompt_loader.load(_SYSTEM_PROMPT),
                             [render(chunk) for chunk in pending], tier=tier,
                             max_concurrency=concurrency, cancel=self._cancel,
                             on_progress=lambda landed, _total, at=done: self._progress(
                                 f"{label} {min(sum(sizes[:landed]), subject_count)} of "
                                 f"{subject_count} scenarios", at + landed, total))

        seen = 0
        for chunk, reply in zip(pending, replies):
            cancellation.check(self._cancel)
            apply_reply(chunk, reply)
            seen += len(chunk)
            done += 1
            self._progress(f"{label} {min(seen, subject_count)} of {subject_count} scenarios",
                           done, total)
        return done

    def _preamble(self, intake: IntakeData) -> str:
        """Everything needed before a single scenario is seen. Built from the intake, so a
        supplementary context file is an addition rather than a prerequisite."""
        return "\n\n".join([
            prompt_loader.load("shared.mission"),
            "THE AGENT UNDER TEST\n\n"
            "An agentic AI system: software that holds a conversation, decides what to do at each "
            "step, and calls tools to act on those decisions. This one is described below exactly "
            f"as the model owner declared it.\n\n{describe_use_case(intake)}",
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
        return call(self._complete, prompt_loader.load(_SYSTEM_PROMPT), user,
                    tier=config.stage_tier("REVIEWER_PROPOSE", config.JUDGEMENT))

    def _render_assess(self, chunk: List[Scenario], preamble: str, shared: dict,
                       signals: dict) -> str:
        """The user prompt for one chunk, built but not yet sent."""
        return f"{preamble}\n\n" + prompt_loader.render(
            _ASSESS_PROMPT, **shared, materiality=", ".join(MATERIALITY),
            categories=", ".join(CATEGORIES), batch=_batch_payload(chunk, signals))

    def _apply_assessment(self, chunk: List[Scenario], reply) -> None:
        """Write one chunk's verdict onto its scenarios: materiality, ending, and any flag.

        The category is recorded **only where it disagrees** with what the intake declared.
        Agreement is the expected result for most scenarios, and writing it down anyway would fill
        the registry's review columns with restatements of the declared value and bury the handful
        of rows that actually want a second look. A probe has no ending to categorise, so whatever
        comes back for one is ignored.
        """
        parsed = parsed_reply(reply, "Review call", ", ".join(s.id for s in chunk))
        for scenario in chunk:
            entry = parsed.get(scenario.id)
            if not entry:
                continue
            scenario.review_materiality = one_of(entry.get("materiality"), MATERIALITY)
            scenario.review_rationale = prose(entry, "rationale")
            scenario.review_flag = one_of(entry.get("flag"), REVIEW_FLAGS)

            if scenario.is_probe:
                continue
            category = one_of(entry.get("category"), CATEGORIES)
            if not category or category == scenario.category:
                continue
            scenario.review_category = category
            scenario.review_category_rationale = (prose(entry, "category_rationale")
                                                  or prose(entry, "rationale"))

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
