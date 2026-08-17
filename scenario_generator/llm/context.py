"""Descriptions of the use case under test, derived from the intake.

Every LLM pass needs to know what the agent is before it can write about it, judge it, or review
it. All of that description is built here from the intake alone, so a pass is never dependent on
a supplementary context file being supplied. Where a context file *is* supplied it supplements
this, never replaces it.

This is deliberately separate from the prompt library. What lives here is logic -- text assembled
from intake data, which changes only when the data model changes. The wording that surrounds it
lives in ``scenario_generator/prompts`` where it can be edited without touching Python.
"""
from __future__ import annotations

from collections import Counter

from typing import List

from ..core.models import IntakeData, Scenario

_L1_ORDER = ["Agent type", "Channel / modality", "Human handoff triggers",
             "Safety requirements", "Success criteria", "Known limitations",
             "Use case rating (informational)"]


def _declared_fields(intake: IntakeData) -> List[str]:
    """L1 fields the intake declares, beyond the name and objective, in a stable order."""
    skip = {"Use case name", "Business objective"}
    ordered = [k for k in _L1_ORDER if k in intake.use_case]
    ordered += [k for k in intake.use_case if k not in _L1_ORDER and k not in skip]
    return [f"{key}: {intake.use_case[key]}" for key in ordered
            if key not in skip and str(intake.use_case.get(key, "")).strip()]


def _shape(intake: IntakeData) -> str:
    """One sentence on the size and shape of the agent, so scale is obvious at a glance."""
    terminal = sum(1 for s in intake.states if s.is_terminal)
    state_changing = sum(1 for t in intake.tools if t.state_changing)
    parts = [f"{len(intake.capabilities)} capabilities",
             f"{len(intake.decisions)} decision points",
             f"{len(intake.states)} states ({terminal} terminal)",
             f"{len(intake.personas)} personas",
             f"{len(intake.tools)} tools"]
    if state_changing:
        verb = "changes" if state_changing == 1 else "change"
        parts.append(f"{state_changing} of which {verb} account or financial state")
    return ", ".join(parts) + "."


def describe_use_case(intake: IntakeData) -> str:
    """Orientation: what the agent is and what it exists to do. Always available."""
    lines = [f"Name: {intake.name}", f"Business objective: {intake.objective}"]
    lines += _declared_fields(intake)
    lines.append(f"Declared shape: {_shape(intake)}")
    return "\n".join(lines)


def describe_graph(intake: IntakeData) -> str:
    """The agent's declared structure: capabilities, decisions, states, personas and tools.

    **The edges are part of the structure.** This used to render states as descriptions and a
    terminal flag and nothing else, which meant the two calls whose entire job is the wiring --
    the intake repair and the structure review -- were shown a bag of states with no wiring in it.
    Asked "where does DEC-02's 'Too old' outcome lead?", the model had to reconstruct every edge
    in the agent from the state descriptions, and a reconstruction that came back one branch short
    was indistinguishable from the declaration it replaced. That is the intermittent failure this
    line fixes: not a model that is bad at graphs, a prompt that withheld the graph.
    """
    # The span is part of what a capability *is* now: it is the block of the graph the scenario
    # space is enumerated over, so a call asked to reason about the structure -- to reconnect a
    # stray decision, or to judge whether two decisions are the same branch -- has to be able to
    # see where one block ends and the next begins. Without it, a merge that reads perfectly well
    # locally can weld two blocks together and change how the whole space is enumerated.
    def _span(c) -> str:
        if not c.entry_states and not c.exit_states:
            return " — no span drawn, so nothing is walked through it"
        return (f" — entered at {', '.join(c.entry_states) or 'nothing'}"
                f"; hands on or ends at {', '.join(c.exit_states) or 'nothing'}")

    capabilities = "\n".join(
        f"- {c.id} ({c.name}){f' — type: {c.type}' if c.type else ''}{_span(c)}"
        for c in intake.capabilities) or "- none declared"

    decisions = "\n".join(
        f"- {d.id} ({d.name}): {' / '.join(d.variants)}"
        f"{f' | input from: {d.input_source}' if d.input_source else ''}"
        f"{f' | up to {d.max_attempts} attempts' if d.max_attempts > 1 else ''}"
        f"{f' | condition: {d.outcome_condition}' if d.outcome_condition else ''}"
        for d in intake.decisions) or "- none declared"

    states = "\n".join(
        f"- {s.id}: {s.description}"
        f" | reached via: {s.reached_via or 'NOTHING DECLARED'}"
        f"{' | next: ' + ', '.join(s.next_decisions) if s.next_decisions else ''}"
        f"{' [terminal]' if s.is_terminal else ''}"
        f"{f' [outcome type: {s.outcome_type}]' if s.outcome_type else ''}"
        for s in intake.states) or "- none declared"

    personas = "\n".join(
        f"- {p.id} ({p.name}){' [default]' if p.is_default else ''}"
        for p in intake.personas) or "- none declared"

    tools = "\n".join(
        f"- {t.name} (capability {t.capability_id})"
        f"{' [state-changing]' if t.state_changing else ''}"
        for t in intake.tools) or "- none declared"

    return (f"Capabilities:\n{capabilities}\n\n"
            f"Decision points and their possible outcomes:\n{decisions}\n\n"
            f"States (a terminal state ends the interaction; Outcome Type sets a scenario's "
            f"category):\n{states}\n\n"
            f"Personas:\n{personas}\n\n"
            f"Tools:\n{tools}")


def describe_enumeration(intake: IntakeData) -> str:
    """What the declared graph actually walks to, as arithmetic rather than as a verdict.

    The repair call is asked to fix the wiring, and until now the only thing it could see about
    the wiring was the wiring itself. This is the consequence: how many distinct routes the
    declaration produces, where they end, and which declared outcomes stop the walk because
    nothing says where they lead.

    Deliberately not a judgement. An agent that genuinely does one thing enumerates to one route,
    and a check that called that a fault would be wrong about a real agent and would teach anyone
    reading it to ignore the next one. What is offered is the count and the endings; whether that
    is the right shape for this agent is something only the documentation can settle, and the
    model has the documentation.
    """
    from ..core.graph import DecisionGraph, enumerate_paths

    graph = DecisionGraph(intake.decisions, intake.states)
    walked, augmented = enumerate_paths(graph)
    routes = list(walked) + list(augmented)

    endings = []
    for route in routes:
        target = graph.successor(route[-1].decision_id, route[-1].variant) if route else ""
        state = graph.state(target)
        endings.append(f"{target} ({state.description})" if state else target or "nowhere")

    dangling = [f"{d.id}={v}" for d in intake.decisions for v in d.variants
                if graph.successor(d.id, v).startswith("OUT:")]

    lines = [f"Distinct routes from start to finish: {len(routes)}",
             f"Endings those routes reach: {len(set(endings))} of "
             f"{sum(1 for s in intake.states if s.is_terminal)} declared terminal state(s)"]
    if dangling:
        lines.append(f"Declared outcomes that stop the walk because nothing says where they lead: "
                     f"{', '.join(dangling)}")
    counted = Counter(endings)
    lines.append("")
    lines.append("Routes by where they end:")
    lines += [f"- {ending}: {count} route(s)" for ending, count in counted.most_common()]
    return "\n".join(lines)


def supplementary_context(text: str) -> str:
    """A supplied context file, framed so the model treats it as additional to the intake."""
    if not text:
        return ""
    return ("\nSUPPLEMENTARY CONTEXT (extracts from the model documentation, additional to the "
            f"intake above)\n\n{text}\n")


def digest(scenarios: List[Scenario], description_chars: int = 160) -> str:
    """One line per scenario — the whole scenario space in a form a single prompt can carry."""
    lines = []
    for s in scenarios:
        parts = [s.id, f"[{s.origin}]", f"({s.category})",
                 f"materiality={s.effective_materiality}"]
        if s.path:
            parts.append(f"path: {s.path_str}")
        parts.append(f"| {s.description[:description_chars]}")
        lines.append(" ".join(parts))
    return "\n".join(lines)
