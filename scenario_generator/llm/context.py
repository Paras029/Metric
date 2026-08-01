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
    """The agent's declared structure: capabilities, decisions, states, personas and tools."""
    capabilities = "\n".join(
        f"- {c.id} ({c.name}){f' — type: {c.type}' if c.type else ''}"
        for c in intake.capabilities) or "- none declared"

    decisions = "\n".join(
        f"- {d.id} ({d.name}): {' / '.join(d.variants)}"
        f"{f' | input from: {d.input_source}' if d.input_source else ''}"
        f"{f' | up to {d.max_attempts} attempts' if d.max_attempts > 1 else ''}"
        f"{f' | condition: {d.outcome_condition}' if d.outcome_condition else ''}"
        for d in intake.decisions) or "- none declared"

    states = "\n".join(
        f"- {s.id}: {s.description}"
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


def supplementary_context(text: str) -> str:
    """A supplied context file, framed so the model treats it as additional to the intake."""
    if not text:
        return ""
    return ("\nSUPPLEMENTARY CONTEXT (extracts from the model documentation, additional to the "
            f"intake above)\n\n{text}\n")


def digest(scenarios: List[Scenario], description_chars: int = 160) -> str:
    """One line per scenario — the whole benchmark in a form a single prompt can carry."""
    lines = []
    for s in scenarios:
        parts = [s.id, f"[{s.origin}]", f"({s.category})",
                 f"materiality={s.effective_materiality}"]
        if s.path:
            parts.append(f"path: {s.path_str}")
        parts.append(f"| {s.description[:description_chars]}")
        lines.append(" ".join(parts))
    return "\n".join(lines)
