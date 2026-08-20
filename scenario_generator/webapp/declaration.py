"""The declaration as an editable list, for the panel that sits beside the drawing.

Everything downstream reads the intake workbook, and it stays the record. What this removes is the
round trip to change one cell of it: download, find the row, edit, save, upload. The judgement
being made in that loop -- "this outcome leads to the wrong state", "this capability is really two"
-- is made by looking at the graph, which is on screen the whole time, and putting a spreadsheet
between the judgement and the picture it comes from is how a declaration ends up carrying rows
somebody knew were wrong.

Five kinds, one shape. Each row is a key, the fields that can be edited, and what is wrong with it
if anything. The page renders them the same way and the save path applies them the same way, so
adding a sixth kind is a row in :data:`KINDS` and a column map in :mod:`core.editing`.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from ..core.editing import KINDS, next_id
from ..core.gaps import CAPABILITY, DECISION, PERSONA, STATE, TOOL, find_gaps
from ..core.models import IntakeData
from ..ingest.drafting import CAPABILITY_TYPES, INPUT_SOURCES, OUTCOME_TYPES

# What each kind is called on screen, in the order the toggles run. Decisions first because that
# is what a validator opens this to correct: a branch with a missing outcome is the defect that
# stops a route being walked, and everything else is a supporting fact about it.
ORDER = (
    ("decision", "Decisions", DECISION),
    ("state", "States", STATE),
    ("capability", "Capabilities", CAPABILITY),
    ("tool", "Tools", TOOL),
    ("persona", "Personas", PERSONA),
)

# Written out rather than derived by removing the last letter, which produced "Add capabilitie".
ONE_OF = {"decision": "decision", "state": "state", "capability": "capability",
          "tool": "tool", "persona": "persona"}

# How each field is edited. "text" is a line, "long" a paragraph, "choice" a fixed vocabulary,
# "ids" a comma-separated list of ids, "flag" a checkbox, "number" a small integer.
FIELDS: Dict[str, List[dict]] = {
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
    """What the audit says about each row, addressed to the row rather than to a list.

    The same questions the page already shows under "What the declaration still needs", put beside
    the field that answers them. A question asked in one place and answerable in another is a
    question nobody closes.
    """
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
    tools_of = {}
    for tool in intake.tools:
        tools_of.setdefault(tool.capability_id, []).append(tool.name)

    rows: Dict[str, List[dict]] = {}

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
    from .graphview import declaration as _declared

    return _declared(intake)[1]


def editable(intake: IntakeData) -> dict:
    """Everything the editing panel needs: the rows, what may go in each field, and the next id.

    Built in one call rather than per kind, because the vocabularies cross: a decision's capability
    has to be one of the declared capabilities, and a state's next decisions have to be declared
    decisions. Assembling them separately is how a dropdown ends up offering an id that was
    removed two edits ago.
    """
    spans = {row["id"]: row for row in _spans(intake)}
    rows = _rows(intake, spans)
    return {
        "kinds": [{"kind": kind, "label": label, "one": ONE_OF[kind], "count": len(rows[kind])}
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
                 for kind, _, _ in ORDER},
    }


def graph_index(intake: IntakeData) -> dict:
    """The adjacency the capability control needs to answer its own questions on the page.

    Which states a capability folds in is set arithmetic over the decisions ticked -- the states
    that offer them, and the states their outcomes land on -- and it has to answer as fast as the
    ticking. A round trip per tick would make building a capability feel like filing a form, which
    is the thing the whole panel exists not to be.

    Deliberately adjacency and nothing else. What counts as an *exit* is policy -- see
    :func:`core.graph.exits_for` -- and policy stays in one language: the interface asks for it
    with a preview rather than working it out a second time in another.
    """
    from ..core.graph import DecisionGraph

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
    }


def counts(intake: IntakeData) -> Dict[str, int]:
    """How many of each kind, for the toggles, without building every row."""
    return {"decision": len(intake.decisions), "state": len(intake.states),
            "capability": len(intake.capabilities), "tool": len(intake.tools),
            "persona": len(intake.personas)}
