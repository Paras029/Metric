"""The review pass: a single sweep over the finished scenario space.

Every other pass sees one narrow slice — a batch of scenarios, or one owner scenario at a time.
This pass is given the whole picture: what the validation is for, what the agent is, its full
declared structure, what each metadata field means, the deterministic redundancy evidence, a
digest of every scenario generated, and, where available, the scenarios the model owner
submitted. It runs with raised reasoning effort and smaller batches because it is asked to weigh
rather than classify.

Three powers, and the first two are separate sweeps rather than one prompt asked to do both --
they are different readings, and a single call carrying both answers the first well and the
second as an afterthought:

    assess    settle each scenario's materiality with the whole set visible, and flag scenarios
              that are redundant, too vague to run, or describing something other than what they
              test.
    category  read back how each route actually ends, against the ending the intake declared.
              Category is deterministic everywhere else -- it comes from the Outcome Type on the
              state a route finishes in -- which makes it the column a wrong or blank declaration
              corrupts without anything noticing. A disagreement here usually means the workbook
              needs correcting rather than the scenario.
    propose   add scenarios that are materially missing. Capped, validated against the intake's
              vocabulary, and marked with origin "llm-proposed".

Every verdict is written to its own column beside the value it disagrees with, never over it, so
both readings stay visible and a person rules. It cannot remove anything: flagging a scenario as
redundant is a recommendation for a human.
"""
from __future__ import annotations

import json
import logging
from typing import Callable, List, Optional, Tuple

from ..core.generation import peer_signals
from ..core.models import (CATEGORIES, MATERIALITY, REVIEW_FLAGS, IntakeData,
                           OwnerScenario, Scenario)
from ..core.proposals import instantiate_proposals
from ..utils import chunks, one_of, parse_json_object
from ..utils.replies import prose
from . import cancellation, config, council, prompt_loader
from .calling import call_batch, parsed_reply
from .context import (describe_blocks, describe_graph, describe_use_case, digest,
                      hardest_routes, supplementary_context)
from .gateway import ask_llm

logger = logging.getLogger(__name__)

CompletionFn = Callable[..., str]
ProgressFn = Callable[..., None]

DEFAULT_PROPOSAL_LIMIT = 15
DEFAULT_BATCH_SIZE = 6

# Checking a category is a narrower judgement than weighing materiality -- it compares an ending
# against a five-value vocabulary rather than reasoning about business consequence -- so it takes
# a much coarser batch, and the second reviewed column costs a fraction of the first's calls.
CATEGORY_BATCH_SIZE = 20

_SYSTEM_PROMPT = "reviewer.system"
_ASSESS_PROMPT = "reviewer.assess"
_CATEGORY_PROMPT = "reviewer.category"
_PROPOSE_PROMPT = "reviewer.propose"
_OWNER_PROMPT = "reviewer.owner_block"
_ADJUDICATE_PROMPT = "reviewer.adjudicate"

# How far apart the two materiality readings have to land before a third is worth paying for.
# One tier apart is two readings agreeing to within the precision the scale has; two is one of
# them having missed something.
ADJUDICATE_GAP = 2


def _owner_block(owner_scenarios: Optional[List[OwnerScenario]]) -> str:
    """The owner's scenarios as context, or nothing at all. The trailing blank lines keep
    the block separated from the section that follows it in the task prompts."""
    if not owner_scenarios:
        return ""
    lines = "\n".join(f"- {s.id}: {s.description[:200]}" for s in owner_scenarios)
    return prompt_loader.render(_OWNER_PROMPT, owner_scenarios=lines) + "\n\n"


def _tiers_apart(scenario: Scenario) -> int:
    """How far the two materiality readings are from each other, in tiers.

    Zero where the review did not set one, which is the ordinary case for a scenario the review
    agreed with: nothing was written to the review's column, so there is no second reading to
    disagree with the first.
    """
    if not scenario.review_materiality or not scenario.materiality:
        return 0
    try:
        return abs(MATERIALITY.index(scenario.review_materiality)
                   - MATERIALITY.index(scenario.materiality))
    except ValueError:                                     # a tier outside the scale settles nothing
        return 0


def _expected_turns(scenario: Scenario) -> List[dict]:
    """What the walk says happens at each turn, beside the plan that is supposed to induce it.

    This is the fact the review was missing. Without it the only question it could ask about a
    turn plan was whether the prose reads clearly, and "under-specified" collapsed into a matter
    of taste. With it there is something to check the plan *against*: the route says turn three
    lands on DEC-04=Declined, so turn three of the plan has to be the tester doing the thing that
    gets it declined, and a plan with four turns against a six-step route is missing two.

    Only turns the walk actually determined. A proposal with no route carries placeholder turn
    meta, and listing that as ground truth would invite the review to check a plan against a
    restatement of itself.
    """
    return [{"turn": t.index, "decision": t.decision_name or t.decision_id,
             "must_land_on": t.expected_variant, "driven_by": t.input_source}
            for t in scenario.turn_meta if t.decision_id and t.decision_id != "-"]


def _batch_payload(scenarios: List[Scenario], signals: dict) -> str:
    return json.dumps([{
        "id": s.id,
        "origin": s.origin,
        "category": s.category,
        "decision_path": s.path_str,
        "description": s.description,
        "turn_plan": s.turn_plan,
        "what_each_turn_must_induce": _expected_turns(s),
        "expected_outcome": s.termination,
        "existing_materiality": s.effective_materiality,
        "capabilities": s.capabilities,
        # The block it walks, and what the tester must have arranged first. Without these the
        # review reads every capability-scoped scenario as starting from nothing, and calls the
        # ones testing the same block redundant when what they differ in is the entry position.
        "tests_capability": s.capability_id,
        "already_established": s.precondition,
        "touches_state_changing_action": s.touches_state_change,
        "num_steps": len(s.turn_meta),
        "num_turns": s.turn_count,
        **signals.get(s.id, {}),
    } for s in scenarios], indent=2)


def _repair(scenario: Scenario, entry: dict) -> str:
    """Apply the review's rewrite of one scenario. Returns what it changed, for the record.

    Each field is replaced only where a rewrite came back and differs from what is there. A model
    that echoes the text unchanged is agreeing with it, not revising it, and recording that as a
    revision would put a marker on every flagged scenario whether or not anything moved.
    """
    changed = []
    for field, revised in (("description", prose(entry, "revised_description")),
                           ("turn_plan", prose(entry, "revised_turn_plan"))):
        if revised and revised != getattr(scenario, field):
            setattr(scenario, field, revised)
            changed.append(field.replace("_", " "))
    return ", ".join(changed)


class NullReviewer:
    """Leaves the scenario space untouched."""

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
               owner_scenarios: Optional[List[OwnerScenario]] = None
               ) -> Tuple[List[Scenario], List[Scenario]]:
        """Settle each judged column in place and return (scenarios, proposals).

        One sweep per column rather than one sweep asked to settle everything at once. The two
        are genuinely different readings -- materiality asks what failure would cost, category
        asks how the interaction ends -- and a single prompt carrying both tends to answer the
        first well and the second as an afterthought.

        The sweeps run one after another rather than together. Each already sends all of its own
        chunks concurrently, and running two at once would put twice ``LLM_MAX_CONCURRENCY``
        connections in flight, which is the number that cap exists to hold down. The cost of the
        second column is modest because it needs a much coarser batch: a seventy-scenario
        scenario space is twelve materiality calls and four category ones.
        """
        cancellation.check(self._cancel)
        preamble = self._preamble(intake)
        signals = peer_signals(scenarios)
        shared = {"total": len(scenarios), "digest": digest(scenarios),
                  "owner": _owner_block(owner_scenarios)}

        # Probes are not routes through the graph and have no ending to categorise -- their
        # "category" is the probe family they came from, which is not one of CATEGORIES at all.
        routed = [s for s in scenarios if not s.is_probe]

        category_batch = config.stage_batch_size("REVIEWER_CATEGORY", CATEGORY_BATCH_SIZE)
        assess_chunks = list(chunks(scenarios, self._batch))
        category_chunks = list(chunks(routed, category_batch))
        total = len(assess_chunks) + len(category_chunks) + 1        # + the proposal call
        done = 0

        done = self._sweep(
            assess_chunks, "Reviewed", "REVIEWER_ASSESS",
            config.stage_tier("REVIEWER_ASSESS", config.JUDGEMENT),
            config.stage_concurrency("REVIEWER_ASSESS"), done, total, len(scenarios),
            lambda chunk: self._render_assess(chunk, preamble, shared, signals),
            self._apply_assessment)

        done = self._sweep(
            category_chunks, "Checked the category of", "REVIEWER_CATEGORY",
            config.stage_tier("REVIEWER_CATEGORY", config.MATERIALITY),
            config.stage_concurrency("REVIEWER_CATEGORY"), done, total,
            len(routed),
            lambda chunk: self._render_category(chunk, preamble, shared),
            self._apply_category)

        cancellation.check(self._cancel)
        self._adjudicate(scenarios, preamble, shared)

        cancellation.check(self._cancel)
        self._progress("Looking for what enumeration could not reach", done, total)
        # The hard routes go to the proposal call and nowhere else. Asked for a difficult
        # journey and given only the digest, it writes the happy path joined end to end -- that is
        # the journey a list of one-line summaries makes visible.
        proposals = self._propose(intake, preamble, shared,
                                  hardest_routes(scenarios, intake))
        self._progress("Review complete", total, total)
        return scenarios, proposals

    def _adjudicate(self, scenarios: List[Scenario], preamble: str, shared: dict) -> int:
        """Settle the scenarios the two materiality readings disagree about. Returns how many.

        A third opinion, but only where one is worth paying for. Every scenario in this scenario space
        is weighed twice already -- once by the materiality pass, against its immediate peers with
        the redundancy signals in hand, and once by the review, against the whole scenario space -- and
        those two readings are given genuinely different things to look at. Where they land in the
        same place or one tier apart, that is two readings agreeing to within the precision the
        scale has. Where they land two or more apart, one of them is missing something, and the
        tier decides how many runs the model owner is asked for.

        Asking a third time about *everything* would be the expensive version of this and would
        mostly re-litigate agreement. Asking only about the conflicts costs one call on a typical
        scenario space and nothing at all on a scenario space that has none, and the call is a better call
        for it: both rationales are in front of it, so it is adjudicating an argument rather than
        forming a fresh opinion in isolation.

        The verdict lands in the review's own column, beside the first assessment rather than over
        it -- the same rule every other verdict in this pass follows.
        """
        conflicts = [s for s in scenarios if _tiers_apart(s) >= ADJUDICATE_GAP]
        if not conflicts:
            return 0

        logger.info("%d scenario(s) were weighed %d or more tiers apart; asking once more with "
                    "both readings in view.", len(conflicts), ADJUDICATE_GAP)
        self._progress(f"Settling {len(conflicts)} disagreements", 0, len(conflicts))

        pending = list(chunks(conflicts, self._batch))
        replies = call_batch(
            self._complete, prompt_loader.load(_SYSTEM_PROMPT),
            [self._render_adjudication(chunk, preamble, shared) for chunk in pending],
            tier=config.stage_tier("REVIEWER_ASSESS", config.JUDGEMENT),
            max_concurrency=config.stage_concurrency("REVIEWER_ASSESS"), cancel=self._cancel,
            on_progress=lambda done, total: self._progress(
                f"Settled {done} of {total} disagreements", done, total))

        settled = 0
        for chunk, reply in zip(pending, replies):
            cancellation.check(self._cancel)
            parsed = parsed_reply(reply, "Adjudication call", ", ".join(s.id for s in chunk))
            for scenario in chunk:
                entry = parsed.get(scenario.id)
                if not isinstance(entry, dict):
                    continue
                tier = one_of(entry.get("materiality"), MATERIALITY)
                if not tier:
                    continue
                scenario.review_materiality = tier
                scenario.review_rationale = prose(entry, "rationale") or scenario.review_rationale
                settled += 1

        logger.info("Settled %d of %d.", settled, len(conflicts))
        return settled

    def _render_adjudication(self, chunk: List[Scenario], preamble: str, shared: dict) -> str:
        """The prompt for one chunk of disagreements, with both readings side by side.

        Only the slots this prompt has. The owner's scenarios are context for proposing
        something new; this is settling an argument about a scenario that already exists.
        """
        return f"{preamble}\n\n" + prompt_loader.render(
            _ADJUDICATE_PROMPT, total=shared["total"], digest=shared["digest"],
            materiality=prompt_loader.load("shared.materiality_scale"),
            scale_values=", ".join(MATERIALITY),
            batch=json.dumps([{
                "id": s.id,
                "name": s.name,
                "description": s.description,
                "first_reading": {"materiality": s.materiality,
                                  "rationale": s.materiality_rationale},
                "second_reading": {"materiality": s.review_materiality,
                                   "rationale": s.review_rationale},
            } for s in chunk], indent=2))

    def _sweep(self, pending: List[List[Scenario]], label: str, stage: str, tier,
               concurrency: int, done: int, total: int, subject_count: int,
               render: Callable[[List[Scenario]], str],
               apply_reply: Callable[[List[Scenario], object], None]) -> int:
        """One column judged across the whole scenario space. Returns the running progress count.

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
        replies = council.deliberate_batch(
                             self._complete, prompt_loader.load(_SYSTEM_PROMPT),
                             [render(chunk) for chunk in pending], stage=stage, tier=tier,
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
        return council.deliberate(
            self._complete, prompt_loader.load(_SYSTEM_PROMPT), user, stage="REVIEWER_PROPOSE",
            tier=config.stage_tier("REVIEWER_PROPOSE", config.JUDGEMENT), cancel=self._cancel)

    def _render_assess(self, chunk: List[Scenario], preamble: str, shared: dict,
                       signals: dict) -> str:
        """The user prompt for one chunk's assessment, built but not yet sent."""
        return f"{preamble}\n\n" + prompt_loader.render(
            _ASSESS_PROMPT, **shared, materiality=", ".join(MATERIALITY),
            batch=_batch_payload(chunk, signals))

    def _render_category(self, chunk: List[Scenario], preamble: str, shared: dict) -> str:
        """The user prompt for one chunk's category check, built but not yet sent."""
        return f"{preamble}\n\n" + prompt_loader.render(
            _CATEGORY_PROMPT, **shared, categories=", ".join(CATEGORIES),
            batch=_batch_payload(chunk, {}))

    def _apply_assessment(self, chunk: List[Scenario], reply) -> None:
        """Write one chunk's verdict, flag and repair onto its scenarios.

        A flag on its own was advice nobody could act on. Text the review calls under-specified is
        issued to the model owner exactly as it stands -- nothing downstream reads it again -- so
        a pass that can see what is missing and cannot write it down leaves the defect in the
        pack it just described. Where a repair comes back it is applied, the flag stays on so the
        judgement is still visible, and what was rewritten is recorded beside it.

        A repair is only taken where the review also flagged the scenario. Rewriting text it had
        no complaint about is the pass quietly restyling the pack, which is a different and much
        larger thing than fixing what it found.
        """
        parsed = parsed_reply(reply, "Review call", ", ".join(s.id for s in chunk))
        for scenario in chunk:
            entry = parsed.get(scenario.id)
            if not entry:
                continue
            scenario.review_materiality = one_of(entry.get("materiality"), MATERIALITY)
            scenario.review_rationale = prose(entry, "rationale")
            scenario.review_flag = one_of(entry.get("flag"), REVIEW_FLAGS)
            if scenario.review_flag:
                scenario.review_revised = _repair(scenario, entry)

    def _apply_category(self, chunk: List[Scenario], reply) -> None:
        """Record where the review reads a scenario's ending differently from the intake.

        Only a disagreement is stored. Agreement is the expected result for most scenarios, and
        writing it down anyway would fill the scenario space metadata's review columns with restatements of the
        declared value and bury the handful of rows that actually want a second look.
        """
        parsed = parsed_reply(reply, "Review call", ", ".join(s.id for s in chunk))
        for scenario in chunk:
            entry = parsed.get(scenario.id)
            if not entry:
                continue
            category = one_of(entry.get("category"), CATEGORIES)
            if not category or category == scenario.category:
                continue
            scenario.review_category = category
            scenario.review_category_rationale = prose(entry, "rationale")

    def _propose(self, intake: IntakeData, preamble: str, shared: dict,
                 hard: str = "") -> List[Scenario]:
        user = f"{preamble}\n\n" + prompt_loader.render(
            _PROPOSE_PROMPT, **shared, limit=self._limit, hard=hard,
            cds=prompt_loader.load("shared.cds"),
            # How the agent is divided, which is the one thing the digest cannot say. Every
            # scenario in it covers a single block, so the routes that cross two are exactly the
            # ones this pass has to propose -- and it cannot name one without the chain.
            blocks=describe_blocks(intake),
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
