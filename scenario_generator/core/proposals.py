"""Turn the review pass's proposed scenarios into Scenario objects.

Everything here is validation, not generation: a proposal may only reference decisions,
outcomes, capabilities and personas the intake declares. Anything invented is discarded, and
what was discarded is recorded on the scenario so a reviewer can see it.

A proposal with no usable decision path is still kept — a perturbation or probe-like scenario
legitimately has none. What it never gets is a fabricated one.
"""
from __future__ import annotations

import logging
from typing import List, Tuple

from ..utils.replies import prose
from ..utils.text import one_of
from .models import (CATEGORIES, MATERIALITY, ORIGIN_PROPOSED, IntakeData, Scenario,
                     Step, TurnMeta)

logger = logging.getLogger(__name__)


def _validate_path(entry: dict, intake: IntakeData) -> Tuple[List[Step], int]:
    """Keep only (decision, outcome) pairs the intake declares. Returns (steps, dropped count)."""
    valid = {d.id: set(d.variants) for d in intake.decisions}
    steps, dropped = [], 0
    for item in entry.get("decision_path") or []:
        decision_id = str(item.get("decision_id", "")).strip()
        variant = str(item.get("variant", "")).strip()
        if variant in valid.get(decision_id, set()):
            steps.append(Step(decision_id, variant, "-"))
        else:
            dropped += 1
    return steps, dropped


def _scripted_turns(entry: dict) -> int:
    """How many turns the plan actually has.

    Counted off the plan rather than read from the ``turns`` field, which is the model restating
    something it has already written and is the field it most often gets wrong. The two
    disagreeing is not harmless: the turn count drives how many rows the issued Turn_Plan sheet
    has, so a six-line plan declared as three turns is issued with half of it missing.
    """
    lines = [line for line in str(entry.get("turn_plan", "")).splitlines() if line.strip()]
    if lines:
        return min(len(lines), 20)
    declared = entry.get("turns", 0)
    try:
        return max(1, min(int(declared or 3), 10))
    except (TypeError, ValueError):
        return 3


def _turn_meta(steps: List[Step], entry: dict, intake: IntakeData) -> List[TurnMeta]:
    """Per-turn ground truth. Where a path survived validation it drives the turns; otherwise
    the plan's own length does, with its expected outcome repeated per turn."""
    by_id = {d.id: d for d in intake.decisions}
    if steps:
        return [TurnMeta(index=i, decision_id=s.decision_id,
                         decision_name=by_id[s.decision_id].name if s.decision_id in by_id
                         else s.decision_id,
                         expected_variant=s.variant, expected_tool="", next_state="-",
                         input_source=by_id[s.decision_id].input_source
                         if s.decision_id in by_id else "User")
                for i, s in enumerate(steps, start=1)]

    expectation = str(entry.get("expected_outcome", "")).strip() or "See scenario description."
    return [TurnMeta(index=i, decision_id="-", decision_name=str(entry.get("title", "Proposed")),
                     expected_variant=expectation, expected_tool="", next_state="-",
                     input_source="User")
            for i in range(1, _scripted_turns(entry) + 1)]


def _category_for(entry: dict, steps: List[Step], intake: IntakeData) -> str:
    """The category, taken from where the route ends before it is taken from the claim.

    A route that ends in a declared terminal state has already said how it ends -- that is what
    Outcome Type on the L4 sheet is -- and reading it off the graph agrees with every walked
    scenario by construction. The claim is only asked for where there is no route to read.

    It used to fall back to the literal string "Proposed", which is not one of :data:`CATEGORIES`.
    That put a value in the category column that no grid cell could ever match, so a proposal with
    a category the model left blank was a scenario a reviewer could not find by narrowing.
    """
    claimed = one_of(entry.get("category"), CATEGORIES, "")
    if steps:
        last = steps[-1]
        landed = next((s for s in intake.states
                       if f"{last.decision_id}={last.variant}" in (s.reached_via or "")), None)
        if landed is not None and landed.outcome_type in CATEGORIES:
            return landed.outcome_type
    return claimed or "Fallback"


def instantiate_proposal(entry: dict, index: int, intake: IntakeData) -> Scenario:
    """Build one proposed scenario, discarding anything outside the intake's vocabulary."""
    steps, dropped = _validate_path(entry, intake)
    capability_ids = {c.id for c in intake.capabilities}

    persona_id = str(entry.get("persona_id", "")).strip()
    persona = (intake.persona_by_id(persona_id)
               or next((p for p in intake.personas if p.is_default), intake.personas[0]))

    rationale = prose(entry, "rationale")
    if dropped:
        rationale = f"{rationale} ({dropped} proposed step(s) discarded as outside the " \
                    f"intake vocabulary)".strip()

    # Which block the proposal belongs to, read off the decisions it actually walks rather than
    # taken from what it claimed. A proposal is enumerated over the same blocks as everything else
    # -- it goes into the same pack, in the same order, and a reviewer reading the identification
    # section should find the proposals about identification there. Left empty where the steps
    # straddle two blocks, because a proposal that crosses a boundary is not scoped to either and
    # pretending otherwise would file it under the wrong one.
    owner = {d.id: d.trigger_capability for d in intake.decisions}
    walked = list(dict.fromkeys(owner.get(step.decision_id, "") for step in steps))
    walked = [block_id for block_id in walked if block_id]
    block = walked[0] if len(walked) == 1 else ""

    # And which blocks it touches, derived before it is read off the reply.
    #
    # It used to be only what the proposal claimed, in a field the prompt marks as one of a dozen.
    # A proposal that left it out came back scoped to nothing at all -- no block, and no "end to
    # end" either, since that label needs more than one block to name. The steps are ground truth
    # about which blocks a route crosses where the list is a claim about it, so the steps answer
    # first, and this is the one that matters most for the scenarios the review is now asked for:
    # a cross-capability journey is *defined* by crossing, and it was the case most likely to
    # arrive with the field blank.
    declared = [c for c in (entry.get("capabilities") or []) if c in capability_ids]
    touches = walked or declared
    if not block and len(touches) == 1:
        block = touches[0]
    entry_state = next((c for c in intake.capabilities if c.id == block), None)
    seeded = "Session start"
    precondition = ""
    if entry_state is not None and entry_state.entry_states:
        opens_at = next((s for s in intake.states if s.id == entry_state.entry_states[0]), None)
        if opens_at is not None:
            seeded = opens_at.description or seeded
            precondition = (f"Start with the interaction already at: {seeded}. "
                            f"This scenario tests {entry_state.name or block} from that point on.")

    scenario = Scenario(
        id=f"LP-{index:03d}",
        path=steps,
        category=_category_for(entry, steps, intake),
        persona=persona,
        seeded_state=seeded,
        termination=str(entry.get("expected_outcome", "")).strip() or "See scenario description.",
        capabilities=touches,
        tools=[],
        touches_state_change=bool(entry.get("touches_state_change")),
        turn_meta=_turn_meta(steps, entry, intake),
        origin=ORIGIN_PROPOSED,
        capability_id=block,
        precondition=precondition,
    )
    scenario.name = prose(entry, "title") or prose(entry, "name")
    scenario.description = prose(entry, "description")
    scenario.turn_plan = prose(entry, "turn_plan")
    scenario.materiality = one_of(entry.get("materiality"), MATERIALITY, "Medium")
    scenario.materiality_confidence = "Low"
    scenario.materiality_rationale = "Proposed by the review layer; not independently assessed."
    scenario.proposed_rationale = rationale
    scenario.proposed_anchor = str(entry.get("anchor_scenario_id", "")).strip()
    return scenario


def instantiate_proposals(entries: List[dict], intake: IntakeData,
                          limit: int = None) -> List[Scenario]:
    """Validated proposals, truncated to `limit`, with stable LP-xxx IDs.

    A proposal missing a description or a turn plan is dropped. Both are issued to the model owner
    and neither can be reconstructed: a scenario with no description cannot be reviewed, and one
    with no plan is a title the other team is asked to run. Filling in either from the rest would
    put text in front of a tester that nobody wrote for them.
    """
    usable = [e for e in entries
              if str(e.get("description", "")).strip() and str(e.get("turn_plan", "")).strip()]
    if len(usable) < len(entries):
        logger.warning("Dropped %d proposal(s) with no description or no turn plan.",
                       len(entries) - len(usable))
    if limit is not None:
        usable = usable[:limit]
    return [instantiate_proposal(entry, index, intake)
            for index, entry in enumerate(usable, start=1)]
