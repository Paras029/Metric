"""The declaration as an editable list, for the panel that sits beside the drawing."""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from metric.domain.editing import KINDS, next_id
from metric.phases.intake.intake.gaps import CAPABILITY, DECISION, PERSONA, STATE, TOOL, USE_CASE, find_gaps
from metric.domain.models import IntakeData
from metric.phases.intake.intake.drafting import CAPABILITY_TYPES, INPUT_SOURCES, OUTCOME_TYPES

# What each kind is called on screen, in the order the toggles run. Decisions first because that
# is what a validator opens this to correct: a branch with a missing outcome is the defect that
# stops a route being walked, and everything else is a supporting fact about it.
ORDER = (
    ("use_case", "Use case", USE_CASE),
    ("decision", "Decisions", DECISION),
    ("state", "States", STATE),
    ("capability", "Capabilities", CAPABILITY),
    ("tool", "Tools", TOOL),
    ("persona", "Personas", PERSONA),
)

# Written out rather than derived by removing the last letter, which produced "Add capabilitie".
ONE_OF = {"use_case": "use case", "decision": "decision", "state": "state",
          "capability": "capability", "tool": "tool", "persona": "persona"}

# The use case is one row and there is only ever one of it, so it has no "add" and no id.
SINGLETON = {"use_case"}

# How each field is edited. "text" is a line, "long" a paragraph, "choice" a fixed vocabulary,
# "ids" a comma-separated list of ids, "flag" a checkbox, "number" a small integer.
FIELDS: Dict[str, List[dict]] = {
    "use_case": [
        {"name": "Use case name", "label": "Name", "kind": "text"},
        {"name": "Business objective", "label": "Business objective", "kind": "long",
         "hint": "Every scenario is written against this."},
        {"name": "Agent type", "label": "Agent type", "kind": "text"},
        {"name": "Channel / modality", "label": "Channel", "kind": "text"},
        {"name": "Human handoff triggers", "label": "Hand-off triggers", "kind": "long"},
        {"name": "Safety requirements", "label": "Safety requirements", "kind": "long"},
        {"name": "Success criteria", "label": "Success criteria", "kind": "long"},
        {"name": "Known limitations", "label": "Known limitations", "kind": "long"},
        {"name": "Use case rating (informational)", "label": "Rating", "kind": "text"},
    ],
    "decision": [
        {"name": "name", "label": "What it decides", "kind": "text"},
        {"name": "outcomes", "label": "Possible outputs", "kind": "list", "separator": " / ",
         "hint": "Every named way this can resolve, separated by /. Two at the minimum."},
        {"name": "capability_id", "label": "Capability", "kind": "choice", "of": "capabilities"},
        {"name": "input_source", "label": "Input source", "kind": "choice", "of": "input_sources",
         "hint": "Only User becomes a turn a tester can script."},
        {"name": "inputs", "label": "Inputs", "kind": "text"},
        {"name": "max_attempts", "label": "Max attempts", "kind": "number"},
        {"name": "outcome_condition", "label": "Outcome condition", "kind": "text"},
        {"name": "out_of_scope", "label": "Out of scope", "kind": "flag"},
    ],
    "state": [
        {"name": "description", "label": "What position this is", "kind": "long"},
        {"name": "reached_via", "label": "Reached via", "kind": "text",
         "hint": "Start, or DEC-xx=Outcome. This is what connects the graph."},
        {"name": "next_decisions", "label": "Valid next decisions", "kind": "list",
         "separator": ", ", "of": "decisions",
         "hint": "Which decisions can be taken from here. Empty only where it ends."},
        {"name": "is_terminal", "label": "Ends the interaction", "kind": "flag"},
        {"name": "outcome_type", "label": "Outcome type", "kind": "choice", "of": "outcome_types",
         "when": "is_terminal"},
    ],
    "capability": [
        {"name": "name", "label": "Name", "kind": "text"},
        {"name": "type", "label": "Type", "kind": "choice", "of": "capability_types",
         "hint": "Decides which adversarial probes apply."},
        # First, because everything below it follows from the answer. Which decisions make up a
        # capability is the judgement somebody actually makes about it; which states it folds in
        # and which of those can be its boundary are consequences, not further judgements.
        {"name": "decisions", "label": "Decisions in this capability", "kind": "decisions",
         "hint": "A decision belongs to one capability. Ticking one here moves it."},
        # Rendered by the span control rather than as two text boxes -- a shortlist of the states
        # that could be a boundary of *this* capability, with every state one click behind it, and
        # the endings takeable from the graph. Declared here all the same so it stages, previews
        # and saves through the one path everything else does. It used to be a form of its own
        # that posted and redirected, which navigated out of the expanded view every time somebody
        # drew a span -- and left the span unable to name a state the shortlist had pruned.
        {"name": "entry_states", "label": "Entered at", "kind": "states", "of": "entry_options",
         "hint": "Where a route arrives. Usually an exit of the capability before it."},
        {"name": "exit_states", "label": "Hands on or ends at", "kind": "states",
         "of": "exit_options",
         "hint": "Where a route leaves — the next capability's entry, or an ending."},
    ],
    "tool": [
        {"name": "capability_id", "label": "Capability", "kind": "choice", "of": "capabilities"},
        {"name": "changes_state", "label": "Changes stored data", "kind": "flag",
         "hint": "Weighs on materiality."},
    ],
    "persona": [
        {"name": "name", "label": "Name", "kind": "text"},
        {"name": "applies_to", "label": "What they are trying to achieve", "kind": "long"},
        {"name": "is_default", "label": "Default persona", "kind": "flag"},
    ],
}


def _problems(intake: IntakeData) -> Dict[str, Dict[str, List[str]]]:
    """What the audit says about each row, addressed to the row rather than to a list."""
    by_kind: Dict[str, Dict[str, List[str]]] = {kind: {} for kind, _, _ in ORDER}
    kinds = {gap_kind: kind for kind, _, gap_kind in ORDER}
    for gap in find_gaps(intake):
        kind = kinds.get(gap.kind)
        if kind and gap.target_id:
            by_kind[kind].setdefault(gap.target_id, []).append(gap.question)
    return by_kind


def _rows(intake: IntakeData, spans: Optional[Dict[str, dict]] = None) -> Dict[str, List[dict]]:
    audit = _problems(intake)
    spans = spans or {}
    rows: Dict[str, List[dict]] = {}

    # One row, always present even where the sheet is empty -- a use case with nothing filled in
    # is the case somebody most needs to open, and a list with no rows in it has nothing to open.
    rows["use_case"] = [{
        "key": "use_case",
        "title": intake.name,
        "fields": {spec["name"]: intake.use_case.get(spec["name"], "")
                   for spec in FIELDS["use_case"]},
        "note": intake.objective,
        "problems": [gap for gaps in audit["use_case"].values() for gap in gaps],
    }]
    rows["decision"] = [{
        "key": d.id,
        "title": d.name or d.id,
        "fields": {"name": d.name, "outcomes": list(d.variants),
                   "capability_id": d.trigger_capability, "input_source": d.input_source,
                   "inputs": d.inputs, "max_attempts": d.max_attempts,
                   "outcome_condition": d.outcome_condition, "out_of_scope": d.out_of_scope},
        "note": " / ".join(d.variants) or "no outcomes declared",
        "problems": audit["decision"].get(d.id, []),
    } for d in intake.decisions]

    rows["state"] = [{
        "key": s.id,
        "title": s.description or s.id,
        "fields": {"description": s.description, "reached_via": s.reached_via,
                   "next_decisions": list(s.next_decisions), "is_terminal": s.is_terminal,
                   "outcome_type": s.outcome_type},
        "note": (f"{s.outcome_type or 'ending'} · reached via {s.reached_via or 'nothing stated'}"
                 if s.is_terminal else
                 f"reached via {s.reached_via or 'nothing stated'}"),
        "problems": audit["state"].get(s.id, []),
    } for s in intake.states]

    rows["capability"] = [{
        "key": c.id,
        "title": c.name or c.id,
        "fields": {"name": c.name, "type": c.type,
                   "decisions": [d.id for d in intake.decisions
                                 if d.trigger_capability == c.id],
                   "entry_states": list(c.entry_states), "exit_states": list(c.exit_states)},
        "span": spans.get(c.id, {}),
        "note": (" → ".join(filter(None, [", ".join(c.entry_states), ", ".join(c.exit_states)]))
                 or "no span drawn"),
        "problems": audit["capability"].get(c.id, []),
    } for c in intake.capabilities]

    rows["tool"] = [{
        "key": t.name,
        "title": t.name,
        "fields": {"capability_id": t.capability_id, "changes_state": t.state_changing},
        "note": ((t.capability_id or "not linked to a capability")
                 + (" · changes stored data" if t.state_changing else "")),
        "problems": audit["tool"].get(t.name, []),
    } for t in intake.tools]

    rows["persona"] = [{
        "key": p.id,
        "title": p.name or p.id,
        "fields": {"name": p.name, "applies_to": list(p.applies_to), "is_default": p.is_default},
        "note": (", ".join(p.applies_to) or "no objective stated")
                + (" · default" if p.is_default else ""),
        "problems": audit["persona"].get(p.id, []),
    } for p in intake.personas]

    return rows


def _spans(intake: IntakeData) -> List[dict]:
    """The span picker's own data, from the same function the page has always used for it."""
    from metric.web.graph.view import declaration as _declared

    return _declared(intake)[1]


def editable(intake: IntakeData) -> dict:
    """Everything the editing panel needs: the rows, what may go in each field, and the next id."""
    spans = {row["id"]: row for row in _spans(intake)}
    rows = _rows(intake, spans)
    return {
        # The use case reports no count: there is one and only ever one, and "Use case 1" beside
        # every other tab's real tally reads as a number that means something.
        "kinds": [{"kind": kind, "label": label, "one": ONE_OF[kind],
                   "count": None if kind in SINGLETON else len(rows[kind]),
                   "singleton": kind in SINGLETON}
                  for kind, label, _ in ORDER],
        "fields": FIELDS,
        "rows": rows,
        "vocabulary": {
            "capabilities": [{"id": c.id, "label": c.name or c.id} for c in intake.capabilities],
            # With where each one currently belongs, so the capability control can say what a
            # tick is about to move rather than moving it silently.
            "decisions": [{"id": d.id, "label": d.name or d.id,
                           "capability": d.trigger_capability} for d in intake.decisions],
            "states": [{"id": s.id, "label": s.description or s.id} for s in intake.states],
            "capability_types": list(CAPABILITY_TYPES),
            "input_sources": list(INPUT_SOURCES),
            "outcome_types": list(OUTCOME_TYPES),
        },
        "next": {kind: next_id([row["key"] for row in rows[kind]], KINDS[kind].prefix)
                 for kind, _, _ in ORDER if kind in KINDS},
    }


def graph_index(intake: IntakeData) -> dict:
    """The adjacency the capability control needs to answer its own questions on the page."""
    from metric.domain.graph import DecisionGraph

    graph = DecisionGraph(intake.decisions, intake.states)
    return {
        "decisions": {
            d.id: {
                "name": d.name or d.id,
                "capability": d.trigger_capability,
                "offered_by": graph.states_offering(d.id),
                "lands_on": list(dict.fromkeys(
                    landing for landing in
                    (graph.successor(d.id, variant) for variant in d.variants)
                    if landing in graph.states)),
            } for d in intake.decisions},
        "states": {s.id: {"label": s.description or s.id, "terminal": s.is_terminal}
                   for s in intake.states},
        # Where the conversation opens. The entry picker falls back to it when nothing offers a
        # capability's decisions, and the fallback has to be the same on the page as it is on the
        # server or the shortlist would change under a tick that did not ask it to.
        "start_states": list(graph.start_states),
    }


def counts(intake: IntakeData) -> Dict[str, int]:
    """How many of each kind, for the toggles, without building every row."""
    return {"decision": len(intake.decisions), "state": len(intake.states),
            "capability": len(intake.capabilities), "tool": len(intake.tools),
            "persona": len(intake.personas)}
