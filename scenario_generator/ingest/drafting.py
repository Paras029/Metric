"""Drafting the intake workbook from the evidence.

The intake is the boundary of what can be tested, and filling it in by hand from a sixty-page
document is the slowest part of the whole exercise. This drafts it instead, so the work becomes
correction rather than transcription.

Two things make that safe to do.

The draft is written into the same workbook shape a person would have filled in themselves, so
nothing downstream can tell the difference and nothing needs a separate approval path. And it is
never authoritative: the intake stage exists precisely so a person reads it, fixes it, and says
so. What the draft owes them in return is honesty about itself, which is why every part carries a
confidence and why the review notes are written into the workbook beside the cells they concern.

It is deliberately willing to commit. A draft that declines to fill anything it is not certain of
is a blank form with extra steps, and leaves the reader exactly where they started.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Dict, List, Optional

from openpyxl import load_workbook

from ..core.intake import write_template
from ..llm import config, prompt_loader
from ..llm.calling import call
from ..llm.gateway import ask_llm
from ..utils import parse_json_object
from ..utils.replies import at_least_one, objects as _objects, text as _text

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = "ingest.system"
_DRAFT_PROMPT = "intake.draft"
_REVISE_PROMPT = "intake.revise"
_REPAIR_PROMPT = "intake.repair"

CAPABILITY_TYPES = ("Lookup", "Transactional", "Gating", "Advisory", "PII-handling")
INPUT_SOURCES = ("User", "Tool", "Memory-Session", "Memory-CrossSession", "System-Context",
                 "Document")
OUTCOME_TYPES = ("Happy path", "Retry", "Fallback", "Escalation", "Termination")
CONFIDENCE = ("High", "Medium", "Low")

def _structure_block(structure: Optional[dict]) -> str:
    """The diagram-derived graph as a prompt section, or a line saying there was not one."""
    from . import diagram_structure

    if not structure or diagram_structure.is_empty(structure):
        return ("No workflow diagram was submitted, or none could be read. Build the graph below "
                "from the prose above.")
    return (
        "A workflow diagram was submitted and read into the structure below, box by box. It is "
        "already in the shape you are being asked for, so **start from it**: carry it through, "
        "correct it where the documents above contradict it, and add what it does not cover. It "
        "was read off an image and could not be checked against any text, so it is a draft to "
        "confirm rather than a fact -- but a branch drawn on a diagram and mentioned nowhere in "
        "the prose is still a real branch, and dropping it would leave it untested.\n\n"
        + diagram_structure.render(structure))


_L1_KEYS = [
    ("Use case name", "name"),
    ("Business objective", "objective"),
    ("Agent type", "agent_type"),
    ("Channel / modality", "channel"),
    ("Human handoff triggers", "handoff_triggers"),
    ("Safety requirements", "safety_requirements"),
    ("Success criteria", "success_criteria"),
]


class DraftedIntake:
    """A drafted intake and what the drafter thought of its own work."""

    def __init__(self, data: dict) -> None:
        self.data = data

    @property
    def confidence(self) -> Dict[str, str]:
        raw = self.data.get("confidence") or {}
        return {k: v for k, v in raw.items() if v in CONFIDENCE}

    @property
    def review_notes(self) -> List[dict]:
        return [n for n in (self.data.get("review_notes") or []) if isinstance(n, dict)]

    def counts(self) -> Dict[str, int]:
        return {part: len(self.data.get(part) or [])
                for part in ("personas", "capabilities", "decisions", "states", "tools")}


def draft_intake(context: str, complete: Optional[Callable[..., str]] = None,
                 structure: Optional[dict] = None) -> DraftedIntake:
    """Ask for a filled intake, given everything the documents established.

    ``structure`` is the decision graph read out of any submitted workflow diagrams, in the
    intake's own vocabulary. It is passed separately from the prose context rather than only
    rendered into it, because it is the one part of the reading that already has the shape being
    asked for -- confirming and completing a graph is a far more reliable job than rebuilding one
    from sentences describing it, and a diagram is frequently the only place a branch is drawn.
    """
    complete = complete or ask_llm
    user = prompt_loader.render(_DRAFT_PROMPT, context=context,
                                structure=_structure_block(structure))
    system = prompt_loader.load(_SYSTEM_PROMPT)

    try:
        reply = complete(system, user, tier=config.stage_tier("INTAKE_DRAFT", config.JUDGEMENT))
    except TypeError:                                      # a stub completion without the keywords
        reply = complete(system, user)

    return DraftedIntake(_validate(parse_json_object(reply)))


def repair_intake(context: str, current: str, problems: List[str],
                  complete: Optional[Callable[..., str]] = None,
                  structure: Optional[dict] = None) -> Optional[DraftedIntake]:
    """Put the draft's own structural failures back to the model, with the documents still in hand.

    The first draft is one call over a long context, and the things it most often leaves out are
    the small structural ones: a branch with a single named outcome, an outcome that leads to no
    declared state, a state nothing reaches. None of those are matters of opinion -- the graph
    cannot be walked without them, so whatever they concern is silently never tested -- and the
    answer is usually a paragraph away in the documentation the first pass had already read.

    Which is why this exists and why it is worth a second call. ``problems`` comes from
    :func:`core.gaps.find_gaps`, which reads the *declaration* rather than the documents, so the
    model is told exactly what is wrong rather than asked to look again in general.

    Returns ``None`` where nothing usable came back. A second look may improve the declaration and
    must never damage it, so the caller keeps the first draft in that case -- the same rule the
    diagram reading follows when its own repair pass finds nothing.
    """
    if not problems:
        return None

    complete = complete or ask_llm
    user = prompt_loader.render(_REPAIR_PROMPT, context=context, current=current,
                                structure=_structure_block(structure),
                                problems="\n".join(f"- {problem}" for problem in problems))
    system = prompt_loader.load(_SYSTEM_PROMPT)

    try:
        reply = call(complete, system, user,
                     tier=config.stage_tier("INTAKE_REPAIR", config.JUDGEMENT))
        repaired = DraftedIntake(_validate(parse_json_object(reply)))
    except Exception as exc:
        logger.warning("Could not fill in what the draft left out: %s", exc)
        return None

    # A repair that empties the declaration is not a repair. The check is deliberately crude --
    # it is guarding against a reply that parsed but said nothing, not judging the content.
    if not repaired.data["decisions"] or not repaired.data["states"]:
        logger.warning("The repair pass returned an empty graph; keeping the first draft.")
        return None
    return repaired


def revise_intake(context: str, current: str, complete: Optional[Callable[..., str]] = None,
                  structure: Optional[dict] = None) -> DraftedIntake:
    """Ask for the intake revised in place, given what has been added since it was last written.

    The distinction from :func:`draft_intake` is the whole point of this function: a draft starts
    from nothing, and a revision starts from ``current`` -- whatever is declared right now,
    whether that is an earlier draft, a hand correction, or both -- and is explicitly told to
    change only what the new evidence and answers actually require. Calling ``draft_intake`` again
    on a corrected workbook would silently discard the correction; this is what exists instead,
    for the intake stage's "Revise with these answers" action.
    """
    complete = complete or ask_llm
    user = prompt_loader.render(_REVISE_PROMPT, context=context, current=current,
                                structure=_structure_block(structure))
    system = prompt_loader.load(_SYSTEM_PROMPT)

    try:
        reply = complete(system, user, tier=config.stage_tier("INTAKE_DRAFT", config.JUDGEMENT))
    except TypeError:                                      # a stub completion without the keywords
        reply = complete(system, user)

    return DraftedIntake(_validate(parse_json_object(reply)))


# A persona is a person arriving with an objective, and only two objectives are universal: to use
# the service as intended, and to make it do something it should not. Anything past those has to
# earn its place by changing what the agent does.
MAX_PERSONAS = 4

COOPERATIVE = {"id": "P1", "name": "Cooperative user",
               "applies_to": "Wants the service to work and is honestly trying to use it",
               "is_default": True}
ADVERSARIAL = {"id": "P-ADV", "name": "Adversarial user",
               "applies_to": "Trying to make the agent act outside its remit",
               "is_default": False}

# Words that describe how someone behaves rather than what they came to do. A persona named only
# by one of these is the same objective in a different tone, and enumerating tones tests the same
# route repeatedly while the routes that matter go untested.
_MANNER_ONLY = ("impatient", "confused", "frustrated", "angry", "polite", "rude", "verbose",
                "terse", "hurried", "novice", "expert", "first-time", "returning", "elderly",
                "young", "casual", "formal", "chatty", "brief")


def _is_adversarial(persona: dict) -> bool:
    text = f"{persona['name']} {persona['applies_to']}".lower()
    return any(word in text for word in
               ("adversar", "malicious", "attacker", "abus", "hostile", "bad actor",
                "non-cooperative", "noncooperative", "fraud"))


def _earns_its_place(persona: dict) -> bool:
    """Whether an extra persona describes a different objective rather than a different manner.

    An extra persona has to say what the agent does differently for it. One that says nothing, or
    that is named only for a manner of speaking, is a variation on a persona already present --
    and every one of those multiplies the scenario space without widening it.
    """
    difference = persona["applies_to"].strip()
    if len(difference) < 12:
        return False
    name = persona["name"].strip().lower()
    return not any(name.startswith(word) or name == word for word in _MANNER_ONLY)


def _personas(data: dict) -> List[dict]:
    """The people who arrive, as objectives rather than temperaments.

    Two are guaranteed because two objectives are always in play: someone using the service as
    intended, and someone trying to turn it against its owner. The rest are admitted only where
    the draft says what the agent itself does differently, and never more than a handful -- every
    persona multiplies the whole scenario space, so a loose one costs a run of the entire pack.
    """
    drafted = []
    for entry in _objects(data, "personas"):
        identifier = _text(entry, "id") or f"P{len(drafted) + 1}"
        drafted.append({"id": identifier, "name": _text(entry, "name") or identifier,
                        "applies_to": _text(entry, "applies_to"),
                        "is_default": bool(entry.get("is_default"))})

    adversarial = [p for p in drafted if _is_adversarial(p)]
    others = [p for p in drafted if not _is_adversarial(p)]

    cooperative = next((p for p in others if p["is_default"]), None) or (
        others[0] if others else dict(COOPERATIVE))
    cooperative["is_default"] = True

    extras = [p for p in others if p is not cooperative and _earns_its_place(p)]
    kept = [cooperative, adversarial[0] if adversarial else dict(ADVERSARIAL)]
    for persona in extras[:max(0, MAX_PERSONAS - len(kept))]:
        persona["is_default"] = False
        kept.append(persona)

    dropped = len(drafted) - len([p for p in kept if p in drafted])
    if dropped > 0:
        logger.info("Kept %d persona(s) of %d drafted. The rest described how someone speaks "
                    "rather than what they came to do, which is not a persona.",
                    len(kept), len(drafted))
    return kept


def _validate(data: dict) -> dict:
    """Keep the parts that fit the intake's vocabulary and drop what does not.

    A capability type or outcome type outside the declared list is not a harmless variation --
    the type decides which probes apply and the outcome type decides a scenario's category, so an
    invented value silently changes what gets tested. Dropping it leaves a blank cell a reviewer
    can see, which is the failure worth having.
    """
    use_case = data.get("use_case") if isinstance(data.get("use_case"), dict) else {}

    personas = _personas(data)

    capabilities = []
    for entry in _objects(data, "capabilities"):
        kind = _text(entry, "type")
        capabilities.append({"id": _text(entry, "id"), "name": _text(entry, "name"),
                             "type": kind if kind in CAPABILITY_TYPES else ""})

    decisions = []
    for entry in _objects(data, "decisions"):
        source = _text(entry, "input_source")
        outcomes = [str(o).strip() for o in (entry.get("outcomes") or []) if str(o).strip()]
        decisions.append({
            "id": _text(entry, "id"), "name": _text(entry, "name"),
            "capability_id": _text(entry, "capability_id"), "inputs": _text(entry, "inputs"),
            "outcomes": outcomes,
            "input_source": source if source in INPUT_SOURCES else "User",
            "max_attempts": at_least_one(entry.get("max_attempts")),
            "outcome_condition": _text(entry, "outcome_condition"),
        })

    states = []
    for entry in _objects(data, "states"):
        terminal = bool(entry.get("is_terminal"))
        outcome_type = _text(entry, "outcome_type")
        states.append({
            "id": _text(entry, "id"), "reached_via": _text(entry, "reached_via"),
            "description": _text(entry, "description"),
            "next_decisions": [str(d).strip() for d in (entry.get("next_decisions") or [])
                               if str(d).strip()],
            "is_terminal": terminal,
            "outcome_type": outcome_type if terminal and outcome_type in OUTCOME_TYPES else "",
        })

    tools = [{"name": _text(e, "name"), "capability_id": _text(e, "capability_id"),
              "changes_state": bool(e.get("changes_state"))}
             for e in _objects(data, "tools") if _text(e, "name")]

    return {"use_case": use_case, "personas": personas, "capabilities": capabilities,
            "decisions": decisions, "states": states, "tools": tools,
            "confidence": data.get("confidence") or {},
            "review_notes": data.get("review_notes") or []}


def write_drafted_intake(path: Path, draft: DraftedIntake) -> None:
    """Write the draft into a workbook of exactly the shape ``init-template`` produces.

    The provenance goes on its own sheet rather than into the declared columns. ``read_intake``
    looks sheets up by name and reads cells positionally, so an extra sheet is invisible to it and
    the file works as a ``build-graph`` input whether or not anyone edits it.
    """
    path = Path(path)
    write_template(str(path))
    book = load_workbook(path)
    data = draft.data

    # Written by looking each label up in the sheet, not by counting rows. The template carries
    # fields the drafter is not asked for -- a rating, known limitations -- so the two lists are
    # different lengths and in different orders, and walking them in step lands every field after
    # the second in the wrong row. Worse, a positional write guarded by a label check does not
    # land in the wrong row: it lands nowhere, and five of the seven fields are dropped in silence.
    use_case = data.get("use_case") or {}
    sheet = book["L1 Use Case"]
    at = {str(sheet.cell(row=row, column=1).value or "").strip(): row
          for row in range(2, sheet.max_row + 1)}
    for label, key in _L1_KEYS:
        row = at.get(label)
        if row is None:
            logger.warning("The intake template has no '%s' row; that field was not written.",
                           label)
            continue
        sheet.cell(row=row, column=2, value=str(use_case.get(key, "") or ""))

    for persona in data["personas"]:
        book["Personas"].append([persona["id"], persona["name"], persona["applies_to"],
                                 "Y" if persona["is_default"] else ""])

    for capability in data["capabilities"]:
        book["L2 Capabilities"].append(
            [capability["id"], capability["name"], capability["type"]])

    for decision in data["decisions"]:
        book["L3 Decisions"].append([
            decision["id"], decision["name"], decision["capability_id"], decision["inputs"],
            " / ".join(decision["outcomes"]), decision["input_source"],
            decision["max_attempts"], decision["outcome_condition"],
            # Never drafted. Whether a decision is already covered by a separate engagement is a
            # scoping call the validator makes, not something the documents state.
            "No"])

    for state in data["states"]:
        book["L4 States"].append([
            state["id"], state["reached_via"], state["description"],
            ", ".join(state["next_decisions"]),
            "Yes" if state["is_terminal"] else "No", state["outcome_type"]])

    for tool in data["tools"]:
        book["Tools"].append([tool["name"], tool["capability_id"],
                              "Yes" if tool["changes_state"] else "No"])

    _write_review_sheet(book, draft)
    book.save(path)


def _write_review_sheet(book, draft: DraftedIntake) -> None:
    """Where the draft says what it is unsure of, so a reviewer knows where to look first."""
    sheet = book.create_sheet("Review This")
    sheet.append(["Part", "Confidence", "What to check"])
    for column, width in zip("ABC", (22, 14, 110)):
        sheet.column_dimensions[column].width = width

    confidence = draft.confidence
    for part in ("use_case", "personas", "capabilities", "decisions", "states", "tools"):
        sheet.append([part.replace("_", " ").title(), confidence.get(part, "Low"), ""])

    sheet.append([])
    sheet.append(["Note", "", ""])
    for note in draft.review_notes:
        sheet.append([str(note.get("field", ""))[:60], "",
                      str(note.get("note", ""))[:500]])

    if not draft.review_notes:
        sheet.append(["", "", "The draft raised nothing specific. Read it against the source "
                              "documents regardless — it is a draft, not a finding."])
