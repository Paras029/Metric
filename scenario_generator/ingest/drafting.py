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
from ..llm.gateway import ask_llm
from ..utils import parse_json_object

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = "ingest.system"
_DRAFT_PROMPT = "intake.draft"

CAPABILITY_TYPES = ("Lookup", "Transactional", "Gating", "Advisory", "PII-handling")
INPUT_SOURCES = ("User", "Tool", "Memory-Session", "Memory-CrossSession", "System-Context",
                 "Document")
OUTCOME_TYPES = ("Happy path", "Retry", "Fallback", "Escalation", "Termination")
CONFIDENCE = ("High", "Medium", "Low")

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


def draft_intake(context: str, complete: Optional[Callable[..., str]] = None) -> DraftedIntake:
    """Ask for a filled intake, given everything the documents established."""
    complete = complete or ask_llm
    user = prompt_loader.render(_DRAFT_PROMPT, context=context)
    system = prompt_loader.load(_SYSTEM_PROMPT)

    try:
        reply = complete(system, user, tier=config.JUDGEMENT)
    except TypeError:                                      # a stub completion without the keywords
        reply = complete(system, user)

    return DraftedIntake(_validate(parse_json_object(reply)))


def _objects(data: dict, key: str) -> List[dict]:
    """The list at ``key``, keeping only the entries that are objects at all."""
    return [entry for entry in (data.get(key) or []) if isinstance(entry, dict)]


def _text(entry: dict, key: str) -> str:
    return str(entry.get(key, "") or "").strip()


def _positive_int(value, default: int = 1) -> int:
    """A retry bound. Anything unreadable falls back to one attempt rather than to none."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number >= 1 else default


def _validate(data: dict) -> dict:
    """Keep the parts that fit the intake's vocabulary and drop what does not.

    A capability type or outcome type outside the declared list is not a harmless variation --
    the type decides which probes apply and the outcome type decides a scenario's category, so an
    invented value silently changes what gets tested. Dropping it leaves a blank cell a reviewer
    can see, which is the failure worth having.
    """
    use_case = data.get("use_case") if isinstance(data.get("use_case"), dict) else {}

    personas = []
    for entry in _objects(data, "personas"):
        identifier = _text(entry, "id") or f"P{len(personas) + 1}"
        personas.append({"id": identifier, "name": _text(entry, "name") or identifier,
                         "applies_to": _text(entry, "applies_to"),
                         "is_default": bool(entry.get("is_default"))})
    if personas and not any(p["is_default"] for p in personas):
        personas[0]["is_default"] = True                   # read_intake needs exactly one default

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
            "max_attempts": _positive_int(entry.get("max_attempts")),
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

    use_case = data.get("use_case") or {}
    sheet = book["L1 Use Case"]
    for row, (label, key) in enumerate(_L1_KEYS, start=2):
        if sheet.cell(row=row, column=1).value == label:
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
            decision["max_attempts"], decision["outcome_condition"]])

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
