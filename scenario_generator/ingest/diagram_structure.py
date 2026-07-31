"""The workflow a set of diagrams describes, as a graph rather than as prose about one.

A workflow diagram *is* the intake's L3 and L4 sheets. A box with branching arrows is a decision;
the label on an arrow leaving it is an outcome; the box that arrow lands in is a state. Reading
one into prose and asking a later pass to rebuild a graph out of that prose loses the structure
twice over, and loses it silently -- a dropped box in a paragraph is invisible, where a dropped
box in a node list shows up immediately as an edge pointing at nothing.

So the reading passes return structure, and this module is what holds it, checks it and repairs
against it:

    :func:`clean`   take whatever the model returned and keep the part that is well-formed,
                    discarding entries that name nothing and normalising the rest.
    :func:`audit`   list what is structurally wrong with the result. Every check here is a
                    property the graph must have to be walkable at all, which is what makes them
                    worth putting back to the model: they are not opinions about the drawing.
    :func:`merge`   fold a repair pass's answer into the structure it was asked to fix.

Nothing here calls a model or reads a file. It is the shape and the rules; :mod:`.extraction`
does the calling.
"""
from __future__ import annotations

import re
from typing import Dict, List, Sequence

from ..core.models import CATEGORIES, INPUT_SOURCES
from ..utils.replies import at_least_one, objects as _objects, string_list as _list, text as _text
from ..utils.text import one_of

# The parts a structure carries, in the order they are useful to read. Kept as one tuple so
# cleaning, merging and rendering cannot disagree about what a structure contains.
PARTS = ("capabilities", "decisions", "states")

_DECISION_ID = re.compile(r"^DEC-\d+$", re.I)
_STATE_ID = re.compile(r"^S-\d+$", re.I)


def empty() -> dict:
    """A structure with nothing in it. The shape callers can always rely on being present."""
    return {part: [] for part in PARTS}


def is_empty(structure: dict) -> bool:
    return not any(structure.get(part) for part in PARTS)


def clean(data: dict) -> dict:
    """Keep the well-formed part of what a reading returned, and normalise it.

    Ids are required and are what everything else is keyed on, so an entry without one is dropped
    rather than given a generated id -- an invented ``DEC-07`` would be indistinguishable from a
    real one in the workbook a person then reviews. Everything else is filled in with a defensible
    default, because a decision with an unreadable input source is still a decision.
    """
    cleaned = empty()

    for entry in _objects(data, "capabilities"):
        identifier = _text(entry, "id").upper()
        if identifier:
            cleaned["capabilities"].append({
                "id": identifier, "name": _text(entry, "name"), "type": _text(entry, "type")})

    for entry in _objects(data, "decisions"):
        identifier = _text(entry, "id").upper()
        if not _DECISION_ID.match(identifier):
            continue
        cleaned["decisions"].append({
            "id": identifier,
            "name": _text(entry, "name"),
            "capability_id": _text(entry, "capability_id").upper(),
            "inputs": _text(entry, "inputs"),
            "outcomes": _list(entry, "outcomes"),
            "input_source": one_of(entry.get("input_source"), INPUT_SOURCES, "User"),
            "max_attempts": at_least_one(entry.get("max_attempts")),
            "outcome_condition": _text(entry, "outcome_condition"),
        })

    for entry in _objects(data, "states"):
        identifier = _text(entry, "id").upper()
        if not _STATE_ID.match(identifier):
            continue
        terminal = bool(entry.get("is_terminal"))
        cleaned["states"].append({
            "id": identifier,
            "reached_via": _text(entry, "reached_via"),
            "description": _text(entry, "description"),
            "next_decisions": [d.upper() for d in _list(entry, "next_decisions")],
            "is_terminal": terminal,
            # An outcome type is meaningful only where the interaction ends, and the intake's
            # reader ignores it elsewhere -- carrying it anyway would put a category on a state
            # that has no ending to categorise.
            "outcome_type": one_of(entry.get("outcome_type"), CATEGORIES) if terminal else "",
        })

    return cleaned


def _edges(structure: dict) -> Dict[str, str]:
    """Every ``DEC-xx=Outcome`` a state declares itself reached by, mapped to that state."""
    edges = {}
    for state in structure["states"]:
        route = state["reached_via"].strip().lower().replace(" ", "")
        if route and route != "start":
            edges[route] = state["id"]
    return edges


def audit(structure: dict) -> List[str]:
    """What is structurally wrong with this graph, as things a second look could settle.

    Every check is a property the graph needs in order to be walked at all, which is what makes
    these worth putting back to the model with the images still in hand: none of them is an
    opinion about how the workflow should have been drawn. They are the places the reading is
    demonstrably incomplete -- an arrow whose destination went unrecorded, a box nothing leads to
    -- and naming them individually turns "read it again, better" into a list of specific holes,
    which is a far easier thing to act on.
    """
    problems: List[str] = []
    if is_empty(structure):
        return ["Nothing was read from the diagrams at all: no decisions and no states."]

    decisions = {d["id"]: d for d in structure["decisions"]}
    states = {s["id"]: s for s in structure["states"]}
    edges = _edges(structure)

    if not any(s["reached_via"].strip().lower() == "start" for s in states.values()):
        problems.append(
            "No state is marked as the start. Exactly one state should have reached_via "
            "\"Start\" -- the position the interaction opens in.")

    for decision in structure["decisions"]:
        if len(decision["outcomes"]) < 2:
            problems.append(
                f"{decision['id']} ({decision['name'] or 'unnamed'}) has "
                f"{'no' if not decision['outcomes'] else 'only one'} outcome. A branch point "
                f"with fewer than two named outcomes is not a branch -- look again at the arrows "
                f"leaving that box and name each one.")
        for outcome in decision["outcomes"]:
            route = f"{decision['id']}={outcome}".lower().replace(" ", "")
            if route not in edges:
                problems.append(
                    f"{decision['id']} outcome \"{outcome}\" leads nowhere: no state declares "
                    f"itself reached_via {decision['id']}={outcome}. Follow that arrow and say "
                    f"where it lands, or say it leaves the page.")

    for state in structure["states"]:
        route = state["reached_via"].strip()
        if route and route.lower() != "start":
            decision_id = route.split("=")[0].strip().upper()
            outcome = route.split("=", 1)[1].strip() if "=" in route else ""
            if decision_id not in decisions:
                problems.append(
                    f"{state['id']} says it is reached via {route}, and there is no "
                    f"{decision_id}. Either that decision was missed, or the state is reached "
                    f"some other way.")
            elif outcome and outcome.lower() not in {
                    o.lower() for o in decisions[decision_id]["outcomes"]}:
                problems.append(
                    f"{state['id']} says it is reached via {route}, but {decision_id} does not "
                    f"declare an outcome called \"{outcome}\". One of the two names is wrong.")

        for decision_id in state["next_decisions"]:
            if decision_id not in decisions:
                problems.append(
                    f"{state['id']} leads to {decision_id}, which was not read from any image.")

        if not state["is_terminal"] and not state["next_decisions"]:
            problems.append(
                f"{state['id']} ({state['description'] or 'no description'}) is not marked as "
                f"ending the interaction and nothing follows it. Look at what leaves that box in "
                f"the image before ruling on it -- this is usually an arrow or a downstream "
                f"decision that was missed, not a real ending. Only mark it terminal where the "
                f"image genuinely shows the flow stopping there.")

    for capability in {d["capability_id"] for d in structure["decisions"] if d["capability_id"]}:
        if capability not in {c["id"] for c in structure["capabilities"]}:
            problems.append(f"A decision names capability {capability}, which was never described.")

    return problems


def merge(structure: dict, repair: dict) -> dict:
    """Fold a repair pass's answer into the structure it was asked to fix.

    Keyed on id: an entry the repair returns for an id already present replaces it, and one for a
    new id is added. Replacing rather than patching field by field is deliberate -- the repair is
    given the original and asked to return the corrected whole, so a field it left out is a field
    it decided against, not one it forgot to mention.

    A repair that returns nothing at all leaves the structure exactly as it was, which is the
    behaviour that matters most: a failed or unusable second look must never be able to make the
    reading worse than the first one.
    """
    repaired = clean(repair)
    if is_empty(repaired):
        return structure

    merged = empty()
    for part in PARTS:
        by_id = {entry["id"]: entry for entry in structure.get(part, [])}
        for entry in repaired[part]:
            by_id[entry["id"]] = entry
        merged[part] = list(by_id.values())
    return merged


def render(structure: dict) -> str:
    """The graph as text, for a prompt or for the context document.

    Written out as the intake's own vocabulary rather than as JSON, because both readers of this
    are being asked to produce or check an intake and the closer the two look, the less there is
    to translate.
    """
    if is_empty(structure):
        return ""

    lines: List[str] = []
    if structure["capabilities"]:
        lines.append("CAPABILITIES")
        for capability in structure["capabilities"]:
            kind = f" [{capability['type']}]" if capability["type"] else ""
            lines.append(f"- {capability['id']}: {capability['name']}{kind}")
        lines.append("")

    if structure["decisions"]:
        lines.append("DECISIONS")
        for decision in structure["decisions"]:
            outcomes = " / ".join(decision["outcomes"]) or "none read"
            detail = [f"outcomes: {outcomes}"]
            if decision["capability_id"]:
                detail.append(f"capability {decision['capability_id']}")
            if decision["input_source"] != "User":
                detail.append(f"input from {decision['input_source']}")
            if decision["max_attempts"] > 1:
                detail.append(f"up to {decision['max_attempts']} attempts")
            if decision["outcome_condition"]:
                detail.append(f"when {decision['outcome_condition']}")
            lines.append(f"- {decision['id']}: {decision['name']} — {'; '.join(detail)}")
        lines.append("")

    if structure["states"]:
        lines.append("STATES")
        for state in structure["states"]:
            detail = [f"reached via {state['reached_via'] or 'unknown'}"]
            if state["next_decisions"]:
                detail.append(f"leads to {', '.join(state['next_decisions'])}")
            if state["is_terminal"]:
                detail.append("ends the interaction"
                              + (f" ({state['outcome_type']})" if state["outcome_type"] else ""))
            lines.append(f"- {state['id']}: {state['description']} — {'; '.join(detail)}")

    return "\n".join(lines).strip()


def counts(structure: dict) -> Dict[str, int]:
    """How much was read, for the run log and the stage summary."""
    return {part: len(structure.get(part, [])) for part in PARTS}


def namespaced(readings: Sequence[dict]) -> List[dict]:
    """Per-image node references made unique across images.

    Each image is read on its own and numbers its own boxes from one, so ``n1`` in the second
    image is a different box from ``n1`` in the first. The synthesis pass sees them all at once
    and would otherwise have no way to tell -- prefixing with the image number is what lets it
    say "this arrow from image 1 lands on that box in image 2" at all.
    """
    out = []
    for index, reading in enumerate(readings, start=1):
        prefix = f"i{index}."
        nodes = [{**node, "ref": f"{prefix}{node.get('ref', '')}"}
                 for node in _objects(reading, "nodes")]
        edges = [{**edge,
                  "from": f"{prefix}{edge.get('from', '')}",
                  "to": f"{prefix}{edge.get('to', '')}" if edge.get("to") else ""}
                 for edge in _objects(reading, "edges")]
        leaving = [{**edge, "from": f"{prefix}{edge.get('from', '')}"}
                   for edge in _objects(reading, "continues_offpage")]
        out.append({**reading, "nodes": nodes, "edges": edges,
                    "continues_offpage": leaving})
    return out
