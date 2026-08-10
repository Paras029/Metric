"""Turn enumerated paths into Scenario objects carrying their own metadata.

Everything here is deterministic and derived from the intake. A later pass replaces only the
placeholder description and turn plan; category comes from the declared Outcome Type of the
state a path ends in, so it never depends on a model call.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from .graph import DecisionGraph, Path
from .models import (ORIGIN_GRAPH, ORIGIN_VARIANT_GAP, RUNS_BY_MATERIALITY, Persona,
                     Scenario, Step, Tool, TurnMeta)

_NUMBERED_LINE = re.compile(r"^\s*\d+\s*[\.\)]\s*(.+)$")

_OUTCOME_WORDS = ("fail", "error", "unavailable", "not-confirmed", "stale",
                  "retry", "reject", "denied", "timeout", "not found", "not eligible")


def categorise(path: List[Step], graph: Optional[DecisionGraph] = None) -> str:
    """Category of a path, from the declared Outcome Type of the state it ends in.

    Falls back to the outcome-word heuristic only where the ending state declares nothing.
    """
    if graph is not None and path:
        ending = graph.state(path[-1].next_state)
        if ending is not None and ending.outcome_type:
            return ending.outcome_type
    variants = " ".join(step.variant.lower() for step in path)
    if "escalate" in variants:
        return "Escalation"
    if "terminate" in variants:
        return "Termination"
    if "fail" in variants and "pass" in variants:
        return "Retry"
    if any(word in variants for word in _OUTCOME_WORDS):
        return "Fallback"
    return "Happy path"


def bind_persona(category: str, personas: List[Persona]) -> Persona:
    """First persona tagged for this category, else the declared default."""
    for persona in personas:
        if category in persona.applies_to:
            return persona
    return next((p for p in personas if p.is_default), personas[0])


def _scenario_tools(path: Path, graph: DecisionGraph, tools: List[Tool]) -> Tuple[List[str], bool]:
    """Tool names linked to the path's decisions, and whether any is state-changing."""
    names, state_changing = [], False
    for step in path:
        decision = graph.decision(step.decision_id)
        if not decision:
            continue
        for tool in tools:
            if tool.capability_id == decision.trigger_capability:
                if tool.name not in names:
                    names.append(tool.name)
                state_changing = state_changing or tool.state_changing
    return names, state_changing


def build_turn_meta(path: Path, graph: DecisionGraph, tools: List[Tool]) -> List[TurnMeta]:
    """Per-step expected variant and expected tool call — the internal ground truth."""
    meta = []
    for index, step in enumerate(path, start=1):
        decision = graph.decision(step.decision_id)
        linked = [t.name for t in tools if decision and t.capability_id == decision.trigger_capability]
        meta.append(TurnMeta(
            index=index,
            decision_id=step.decision_id,
            decision_name=decision.name if decision else step.decision_id,
            expected_variant=step.variant,
            expected_tool=", ".join(linked),
            next_state=step.next_state,
            input_source=decision.input_source if decision else "User",
        ))
    return meta


def fallback_name(category: str, turn_meta: List[TurnMeta]) -> str:
    """A handle built from the route itself, for a scenario nothing has written yet.

    Deterministic, like everything else in this module, and deliberately plain: it exists so that
    a list of scenarios is scannable the moment the graph is walked, before any model has run.
    The writer replaces it with something a person would have chosen.
    """
    steps = [f"{t.decision_name}: {t.expected_variant}" for t in turn_meta if t.decision_name]
    if not steps:
        return category or "Scenario"
    return steps[-1] if len(steps) == 1 else f"{steps[0]} … {steps[-1]}"


def fallback_description(category: str, turn_meta: List[TurnMeta]) -> str:
    names = list(dict.fromkeys(t.decision_name for t in turn_meta))
    return f"{category} scenario exercising {', '.join(names) or 'the decision graph'}."


def fallback_turn_plan(turn_meta: List[TurnMeta]) -> str:
    driven = [t for t in turn_meta if t.input_source == "User"]
    if not driven:
        return "1. Supply the triggering input and let the agent run to completion."
    return "\n".join(f"{i}. Drive the conversation so that '{t.decision_name}' is exercised."
                     for i, t in enumerate(driven, start=1))


def turn_plan_lines(scenario: Scenario) -> List[str]:
    """The turn plan split into one instruction per turn, numbering stripped.

    Falls back to the whole plan as a single turn when it carries no numbering.
    """
    lines = [m.group(1).strip() for m in
             (_NUMBERED_LINE.match(line) for line in scenario.turn_plan.splitlines()) if m]
    return lines or [scenario.turn_plan.strip() or "Drive the scenario to its stated outcome."]


def recommended_turns(scenario: Scenario) -> int:
    """Turns the tester is asked to script — the turn plan's own length."""
    return len(turn_plan_lines(scenario))


def required_runs(materiality: str, mapping: dict = None) -> int:
    """Runs requested per scenario for a materiality tier. Placeholder values pending sign-off."""
    return (mapping or RUNS_BY_MATERIALITY).get(materiality, 1)


def _started_at(path: Path, graph: DecisionGraph) -> str:
    """Which start state this path actually opened from.

    An agent can have more than one way in -- an inbound call and an inbound chat are two start
    states over one graph -- and a path records where it went rather than where it began. Taking
    the first start state regardless would have every scenario claim to be seeded from the same
    place, and the seeded state is issued in the registry and read by the writer.

    The path's first decision is offered by the state it opened from, so that is what identifies
    it. Where more than one start offers it, or the path is empty, the first is as good as any.
    """
    if not path:
        return graph.start_states[0]
    offering = set(graph.states_offering(path[0].decision_id))
    return next((s for s in graph.start_states if s in offering), graph.start_states[0])


def instantiate_path(path: Path, graph: DecisionGraph, personas: List[Persona],
                     tools: List[Tool], origin: str) -> Scenario:
    category = categorise(path, graph)
    turn_meta = build_turn_meta(path, graph, tools)
    tool_names, state_changing = _scenario_tools(path, graph, tools)

    start_state = graph.state(_started_at(path, graph)) if graph.start_states else None
    terminal_state = graph.state(path[-1].next_state) if path else None

    capabilities = list(dict.fromkeys(
        graph.decision(s.decision_id).trigger_capability for s in path
        if graph.decision(s.decision_id) and graph.decision(s.decision_id).trigger_capability))

    scenario = Scenario(
        id="",
        path=path,
        category=category,
        persona=bind_persona(category, personas),
        seeded_state=start_state.description if start_state else "Session start",
        termination=terminal_state.description if terminal_state else "Terminal state reached",
        capabilities=capabilities,
        tools=tool_names,
        touches_state_change=state_changing,
        turn_meta=turn_meta,
        origin=origin,
    )
    scenario.name = fallback_name(category, turn_meta)
    scenario.description = fallback_description(category, turn_meta)
    scenario.turn_plan = fallback_turn_plan(turn_meta)
    scenario.materiality_rationale = "Not assessed (LLM writer not run)."
    return scenario


def instantiate_all(walked: List[Path], augmented: List[Path], graph: DecisionGraph,
                    personas: List[Persona], tools: List[Tool]) -> List[Scenario]:
    """All scenarios, ordered and assigned stable SC-xxx IDs."""
    scenarios = [instantiate_path(p, graph, personas, tools, ORIGIN_GRAPH) for p in walked]
    scenarios += [instantiate_path(p, graph, personas, tools, ORIGIN_VARIANT_GAP)
                  for p in augmented]

    order = {ORIGIN_GRAPH: 0, ORIGIN_VARIANT_GAP: 1}
    scenarios.sort(key=lambda s: (order.get(s.origin, 9), s.category, len(s.path)))
    for index, scenario in enumerate(scenarios, start=1):
        scenario.id = f"SC-{index:03d}"
    return scenarios


def peer_signals(scenarios: List[Scenario]) -> Dict[str, dict]:
    """Redundancy and relative depth for each scenario, computed across the whole set.

    Grouped by (category, capability set); within a group, ranked by depth. Supplied to the
    materiality judgement as evidence, so redundancy is measured rather than guessed at.

    Probes are excluded: with no decision path, depth and redundancy say nothing useful about
    them, and each tests a distinct property.
    """
    groups: Dict[tuple, List[Scenario]] = {}
    for scenario in scenarios:
        if scenario.is_probe:
            continue
        key = (scenario.category, tuple(sorted(scenario.capabilities)))
        groups.setdefault(key, []).append(scenario)

    signals = {}
    for group in groups.values():
        ranked = sorted(group, key=lambda s: len(s.turn_meta), reverse=True)
        for rank, scenario in enumerate(ranked, start=1):
            signals[scenario.id] = {"similar_scenarios_in_benchmark": len(group),
                                    "steps_rank_within_similar_group": rank}
    return signals
