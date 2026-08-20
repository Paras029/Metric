"""Turn enumerated paths into Scenario objects carrying their own metadata."""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from metric.domain.graph import DecisionGraph, Path, Span
from metric.domain.models import ORIGIN_GRAPH, ORIGIN_VARIANT_GAP, VARIATIONS_BY_MATERIALITY, Persona, Scenario, Step, Tool, TurnMeta


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
    """A handle built from the route itself, for a scenario nothing has written yet."""
    steps = [f"{t.decision_name}: {t.expected_variant}" for t in turn_meta if t.decision_name]
    if not steps:
        return category or "Scenario"
    return steps[-1] if len(steps) == 1 else f"{steps[0]} … {steps[-1]}"


def _placeholder_description(category: str, turn_meta: List[TurnMeta]) -> str:
    """What a scenario says before the writing step replaces it."""
    names = list(dict.fromkeys(t.decision_name for t in turn_meta))
    return f"{category} scenario exercising {', '.join(names) or 'the decision graph'}."


def _placeholder_turn_plan(turn_meta: List[TurnMeta]) -> str:
    driven = [t for t in turn_meta if t.input_source == "User"]
    if not driven:
        return "1. Supply the triggering input and let the agent run to completion."
    return "\n".join(f"{i}. Drive the conversation so that '{t.decision_name}' is exercised."
                     for i, t in enumerate(driven, start=1))


def _started_at(path: Path, graph: DecisionGraph) -> str:
    """Which start state this path actually opened from."""
    if not path:
        return graph.start_states[0]
    offering = set(graph.states_offering(path[0].decision_id))
    return next((s for s in graph.start_states if s in offering), graph.start_states[0])


def instantiate_path(path: Path, graph: DecisionGraph, personas: List[Persona],
                     tools: List[Tool], origin: str, span: Optional[Span] = None) -> Scenario:
    category = categorise(path, graph)
    turn_meta = build_turn_meta(path, graph, tools)
    tool_names, state_changing = _scenario_tools(path, graph, tools)

    # Where the conversation opens. For a capability-scoped scenario that is the span's entry
    # state, not the graph's start: a verification scenario begins with the cardmember already
    # identified, and seeding it at the top of the journey would ask for a different conversation.
    if span is not None and not span.is_whole_graph:
        start_state = graph.state(span.entry_state)
    else:
        start_state = graph.state(_started_at(path, graph)) if graph.start_states else None
    terminal_state = graph.state(path[-1].next_state) if path else None
    ending = _how_it_ends(terminal_state, graph)

    capabilities = list(dict.fromkeys(
        graph.decision(s.decision_id).trigger_capability for s in path
        if graph.decision(s.decision_id) and graph.decision(s.decision_id).trigger_capability))

    scenario = Scenario(
        id="",
        path=path,
        category=category,
        persona=bind_persona(category, personas),
        seeded_state=start_state.description if start_state else "Session start",
        termination=ending,
        capabilities=capabilities,
        tools=tool_names,
        touches_state_change=state_changing,
        turn_meta=turn_meta,
        origin=origin,
        capability_id=span.capability_id if span else "",
        precondition=_precondition(span, start_state),
    )
    scenario.name = fallback_name(category, turn_meta)
    scenario.description = _placeholder_description(category, turn_meta)
    scenario.turn_plan = _placeholder_turn_plan(turn_meta)
    scenario.materiality_rationale = "Not assessed (LLM writer not run)."
    return scenario


def _how_it_ends(state, graph: DecisionGraph) -> str:
    """What the tester should expect at the last turn."""
    if state is None:
        return "Terminal state reached"
    if not state.is_terminal and state.next_decisions:
        onward = [graph.decision(d) for d in state.next_decisions]
        if any(d is not None for d in onward) and all(
                d is None or d.out_of_scope for d in onward):
            named = ", ".join(d.id for d in onward if d is not None)
            return (f"{state.description or state.id} — the interaction hands on to {named}, "
                    f"which is out of scope for this exercise")
    return state.description or state.id


def _precondition(span: Optional[Span], start_state) -> str:
    """What the tester has to arrange before the first turn, in their own terms."""
    if span is None or span.is_whole_graph or start_state is None:
        return ""
    return (f"Start with the interaction already at: {start_state.description}. "
            f"This scenario tests {span.name} from that point on.")


def instantiate_span(span: Span, walked: List[Path], augmented: List[Path], graph: DecisionGraph,
                     personas: List[Persona], tools: List[Tool]) -> List[Scenario]:
    """The scenarios one span produces, unnumbered."""
    return ([instantiate_path(p, graph, personas, tools, ORIGIN_GRAPH, span) for p in walked]
            + [instantiate_path(p, graph, personas, tools, ORIGIN_VARIANT_GAP, span)
               for p in augmented])


def number_scenarios(scenarios: List[Scenario]) -> List[Scenario]:
    """Order the set and assign stable SC-xxx ids."""
    order = {ORIGIN_GRAPH: 0, ORIGIN_VARIANT_GAP: 1}
    scenarios.sort(key=lambda s: (s.capability_id, order.get(s.origin, 9), s.category,
                                  len(s.path)))
    for index, scenario in enumerate(scenarios, start=1):
        scenario.id = f"SC-{index:03d}"
    return scenarios


def instantiate_all(walked: List[Path], augmented: List[Path], graph: DecisionGraph,
                    personas: List[Persona], tools: List[Tool]) -> List[Scenario]:
    """All scenarios of one whole-graph walk, ordered and assigned stable SC-xxx IDs."""
    scenarios = [instantiate_path(p, graph, personas, tools, ORIGIN_GRAPH) for p in walked]
    scenarios += [instantiate_path(p, graph, personas, tools, ORIGIN_VARIANT_GAP)
                  for p in augmented]
    return number_scenarios(scenarios)


def peer_signals(scenarios: List[Scenario]) -> Dict[str, dict]:
    """Redundancy and relative depth for each scenario, computed across the whole set."""
    groups: Dict[tuple, List[Scenario]] = {}
    for scenario in scenarios:
        if scenario.is_probe:
            continue
        block = ((scenario.capability_id, scenario.precondition) if scenario.capability_id
                 else tuple(sorted(scenario.capabilities)))
        key = (block, scenario.category)
        groups.setdefault(key, []).append(scenario)

    signals = {}
    for group in groups.values():
        ranked = sorted(group, key=lambda s: len(s.turn_meta), reverse=True)
        for rank, scenario in enumerate(ranked, start=1):
            signals[scenario.id] = {"similar_scenarios_in_space": len(group),
                                    "steps_rank_within_similar_group": rank}
    return signals
