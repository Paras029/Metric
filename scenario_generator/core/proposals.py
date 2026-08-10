"""Turn the review pass's proposed scenarios into Scenario objects.

Everything here is validation, not generation: a proposal may only reference decisions,
outcomes, capabilities and personas the intake declares. Anything invented is discarded, and
what was discarded is recorded on the scenario so a reviewer can see it.

A proposal with no usable decision path is still kept — a perturbation or probe-like scenario
legitimately has none. What it never gets is a fabricated one.
"""
from __future__ import annotations

from typing import List, Tuple

from ..utils.replies import prose
from ..utils.text import one_of
from .models import (CATEGORIES, MATERIALITY, ORIGIN_PROPOSED, IntakeData, Scenario,
                     Step, TurnMeta)


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


def _turn_meta(steps: List[Step], entry: dict, intake: IntakeData) -> List[TurnMeta]:
    """Per-turn ground truth. Where a path survived validation it drives the turns; otherwise
    the proposal's own turn count does, with its expected outcome repeated per turn."""
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
    turns = max(1, min(int(entry.get("turns", 3) or 3), 10))
    return [TurnMeta(index=i, decision_id="-", decision_name=str(entry.get("title", "Proposed")),
                     expected_variant=expectation, expected_tool="", next_state="-",
                     input_source="User")
            for i in range(1, turns + 1)]


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

    scenario = Scenario(
        id=f"LP-{index:03d}",
        path=steps,
        category=one_of(entry.get("category"), CATEGORIES, "Proposed"),
        persona=persona,
        seeded_state="Session start",
        termination=str(entry.get("expected_outcome", "")).strip() or "See scenario description.",
        capabilities=[c for c in (entry.get("capabilities") or []) if c in capability_ids],
        tools=[],
        touches_state_change=bool(entry.get("touches_state_change")),
        turn_meta=_turn_meta(steps, entry, intake),
        origin=ORIGIN_PROPOSED,
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

    A proposal missing a description is dropped — it cannot be issued or reviewed without one.
    """
    usable = [e for e in entries if str(e.get("description", "")).strip()]
    if limit is not None:
        usable = usable[:limit]
    return [instantiate_proposal(entry, index, intake)
            for index, entry in enumerate(usable, start=1)]
