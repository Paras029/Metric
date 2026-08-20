"""The review pass: a single sweep over the finished scenario space."""
from __future__ import annotations

import json
import logging
from typing import Callable, List, Optional, Tuple

from metric.phases.scenario_generator.workflow.generation import peer_signals
from metric.domain.models import CATEGORIES, MATERIALITY, IntakeData, OwnerScenario, Scenario
from metric.phases.scenario_generator.review.proposals import instantiate_proposals
from metric.shared import chunks, one_of, parse_json_object
from metric.shared.replies import prose
from metric.llm import cancellation, config, council, prompts
from metric.llm.calling import call_batch, parsed_reply
from metric.llm.describe import describe_blocks, describe_graph, describe_use_case, digest, supplementary_context
from metric.llm.gateway import ask_llm

logger = logging.getLogger(__name__)

CompletionFn = Callable[..., str]
ProgressFn = Callable[..., None]

DEFAULT_PROPOSAL_LIMIT = 15
DEFAULT_BATCH_SIZE = 6

# Checking a category is a narrower judgement than weighing materiality -- it compares an ending
# against a five-value vocabulary rather than reasoning about business consequence -- so it takes
# a much coarser batch, and the second reviewed column costs a fraction of the first's calls.
CATEGORY_BATCH_SIZE = 20

REVIEW_FLAGS = ("Redundant", "Under-specified", "Mis-scoped")

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
    return prompts.render(_OWNER_PROMPT, owner_scenarios=lines) + "\n\n"


def _tiers_apart(scenario: Scenario) -> int:
    """How far the two materiality readings are from each other, in tiers."""
    if not scenario.review_materiality or not scenario.materiality:
        return 0
    try:
        return abs(MATERIALITY.index(scenario.review_materiality)
                   - MATERIALITY.index(scenario.materiality))
    except ValueError:                                     # a tier outside the scale settles nothing
        return 0


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
        """Settle each judged column in place and return (scenarios, proposals)."""
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
        proposals = self._propose(intake, preamble, shared)
        self._progress("Review complete", total, total)
        return scenarios, proposals

    def _adjudicate(self, scenarios: List[Scenario], preamble: str, shared: dict) -> int:
        """Settle the scenarios the two materiality readings disagree about. Returns how many."""
        conflicts = [s for s in scenarios if _tiers_apart(s) >= ADJUDICATE_GAP]
        if not conflicts:
            return 0

        logger.info("%d scenario(s) were weighed %d or more tiers apart; asking once more with "
                    "both readings in view.", len(conflicts), ADJUDICATE_GAP)
        self._progress(f"Settling {len(conflicts)} disagreements", 0, len(conflicts))

        pending = list(chunks(conflicts, self._batch))
        replies = call_batch(
            self._complete, prompts.load(_SYSTEM_PROMPT),
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
        """The prompt for one chunk of disagreements, with both readings side by side."""
        return f"{preamble}\n\n" + prompts.render(
            _ADJUDICATE_PROMPT, total=shared["total"], digest=shared["digest"],
            materiality=prompts.load("shared.materiality_scale"),
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
        """One column judged across the whole scenario space. Returns the running progress count."""
        if not pending:
            return done
        # Reported as replies land, not as they are applied. Every chunk is in flight at once, so
        # the applying loop below takes a fraction of a second at the end of a wait that can run
        # to minutes, and a bar driven off it stands still and then finishes all at once.
        sizes = [len(chunk) for chunk in pending]
        replies = council.deliberate_batch(
                             self._complete, prompts.load(_SYSTEM_PROMPT),
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
            prompts.load("shared.mission"),
            "THE AGENT UNDER TEST\n\n"
            "An agentic AI system: software that holds a conversation, decides what to do at each "
            "step, and calls tools to act on those decisions. This one is described below exactly "
            f"as the model owner declared it.\n\n{describe_use_case(intake)}",
            "ITS DECLARED STRUCTURE\n\n"
            "A state is a position the interaction can be in; a decision is a branch point with "
            "named outcomes; an outcome leads to another state or ends the interaction. This is "
            "the complete declared vocabulary — nothing exists outside it.\n\n"
            f"{describe_graph(intake)}{supplementary_context(self._context)}",
            prompts.load("reviewer.approach"),
            prompts.load("reviewer.field_guide"),
            prompts.load("shared.materiality_scale"),
        ])

    def _call(self, user: str) -> str:
        """Judgement budgets: this pass reasons at length and must justify every verdict."""
        return council.deliberate(
            self._complete, prompts.load(_SYSTEM_PROMPT), user, stage="REVIEWER_PROPOSE",
            tier=config.stage_tier("REVIEWER_PROPOSE", config.JUDGEMENT), cancel=self._cancel)

    def _render_assess(self, chunk: List[Scenario], preamble: str, shared: dict,
                       signals: dict) -> str:
        """The user prompt for one chunk's assessment, built but not yet sent."""
        return f"{preamble}\n\n" + prompts.render(
            _ASSESS_PROMPT, **shared, materiality=", ".join(MATERIALITY),
            batch=_batch_payload(chunk, signals))

    def _render_category(self, chunk: List[Scenario], preamble: str, shared: dict) -> str:
        """The user prompt for one chunk's category check, built but not yet sent."""
        return f"{preamble}\n\n" + prompts.render(
            _CATEGORY_PROMPT, **shared, categories=", ".join(CATEGORIES),
            batch=_batch_payload(chunk, {}))

    def _apply_assessment(self, chunk: List[Scenario], reply) -> None:
        """Write one chunk's materiality verdict and flag onto its scenarios."""
        parsed = parsed_reply(reply, "Review call", ", ".join(s.id for s in chunk))
        for scenario in chunk:
            entry = parsed.get(scenario.id)
            if not entry:
                continue
            scenario.review_materiality = one_of(entry.get("materiality"), MATERIALITY)
            scenario.review_rationale = prose(entry, "rationale")
            scenario.review_flag = one_of(entry.get("flag"), REVIEW_FLAGS)

    def _apply_category(self, chunk: List[Scenario], reply) -> None:
        """Record where the review reads a scenario's ending differently from the intake."""
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

    def _propose(self, intake: IntakeData, preamble: str, shared: dict) -> List[Scenario]:
        user = f"{preamble}\n\n" + prompts.render(
            _PROPOSE_PROMPT, **shared, limit=self._limit,
            cds=prompts.load("shared.cds"),
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
