"""Editing the declaration a row at a time, from the interface rather than in a spreadsheet.

The workbook is the record and stays the record -- everything downstream reads it, a validator can
open it, and a change made here is a change made there. What this removes is the round trip.
Correcting one outcome used to mean downloading the workbook, finding the row, editing it, saving,
and uploading it again, and the judgement being made in that loop -- "this branch leads to the
wrong state" -- is made by looking at the drawing, which is on the screen the whole time. Six steps
of friction around a two-second edit is how a declaration ends up with known-wrong rows in it.

Two rules hold the whole module up.

**Nothing is written that was not asked for.** An upsert touches the named columns of the named
row and leaves every other cell exactly as it was, including columns this file does not know about
and anything a person typed into the sheet by hand. A row that is not named is not read, let alone
written.

**A delete is reported, never cascaded.** Removing a decision leaves states naming it in their
Valid Next Decisions, and the tempting fix -- clean those up too -- turns one deliberate deletion
into several nobody asked for. So the dangling references come back as a report, the audit raises
them on the next render, and the person decides.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from ..utils.text import split_list
from .intake import _open_for_editing, read_intake

# Each editable kind: which sheet it lives on, which column holds its identity, and what each
# field is called in the workbook. The column numbers are 1-based because that is what openpyxl
# takes, and they match :func:`intake.write_template` -- if that gains a column, this gains a name
# for it or the column is simply left alone, which is the safe failure.
@dataclass(frozen=True)
class Kind:
    sheet: str
    key: str
    columns: Dict[str, int]
    prefix: str = ""
    """What a new id looks like: CAP-01, DEC-07, S-12. Empty where the key is a name."""


KINDS: Dict[str, Kind] = {
    "capability": Kind("L2 Capabilities", "id", {
        "id": 1, "name": 2, "type": 3, "entry_states": 4, "exit_states": 5}, prefix="CAP-"),
    "decision": Kind("L3 Decisions", "id", {
        "id": 1, "name": 2, "capability_id": 3, "inputs": 4, "outcomes": 5, "input_source": 6,
        "max_attempts": 7, "outcome_condition": 8, "out_of_scope": 9}, prefix="DEC-"),
    "state": Kind("L4 States", "id", {
        "id": 1, "reached_via": 2, "description": 3, "next_decisions": 4, "is_terminal": 5,
        "outcome_type": 6}, prefix="S-"),
    "tool": Kind("Tools", "name", {"name": 1, "capability_id": 2, "changes_state": 3}),
    "persona": Kind("Personas", "id", {
        "id": 1, "name": 2, "applies_to": 3, "is_default": 4}, prefix="P-"),
}

# Fields the reader parses out of a cell as a list. Written back joined by the separator that
# reader accepts, so what goes in comes out unchanged -- a round trip that reformats is a round
# trip that eventually loses something.
_JOINED = {"entry_states": ", ", "exit_states": ", ", "outcomes": " / ", "next_decisions": ", ",
           "applies_to": ", "}
_BOOLEAN = {"out_of_scope", "is_terminal", "changes_state", "is_default"}


@dataclass
class Report:
    """What an edit did, in the terms the page has to show."""

    written: List[Tuple[str, str]] = field(default_factory=list)
    removed: List[Tuple[str, str]] = field(default_factory=list)
    refused: List[str] = field(default_factory=list)
    dangling: List[str] = field(default_factory=list)
    """References left pointing at something deleted. Reported rather than tidied away."""

    @property
    def changed(self) -> bool:
        return bool(self.written or self.removed)


class EditRefused(ValueError):
    """An edit that would leave the workbook unreadable, refused before it is applied."""


def next_id(existing: Sequence[str], prefix: str) -> str:
    """The next free id in a series, as a person would number it.

    Reading the highest rather than counting the rows: a declaration with DEC-01, DEC-02 and DEC-07
    has had rows removed, and reusing DEC-03 would silently attach a new decision to whatever still
    references the old one.
    """
    if not prefix:
        return ""
    highest = 0
    for value in existing:
        text = str(value or "").strip().upper()
        if text.startswith(prefix.upper()):
            tail = text[len(prefix):]
            if tail.isdigit():
                highest = max(highest, int(tail))
    return f"{prefix}{highest + 1:02d}"


def _as_cell(field_name: str, value) -> str:
    """One field as the text the reader will parse back out of it."""
    if field_name in _BOOLEAN:
        truthy = value if isinstance(value, bool) else str(value or "").strip().lower() in (
            "1", "y", "yes", "true", "on")
        return "Yes" if truthy else "No"
    if field_name in _JOINED:
        if isinstance(value, (list, tuple)):
            parts = [str(v).strip() for v in value if str(v).strip()]
        else:
            parts = [p for p in split_list(str(value or ""),
                                           separators=r"[/,;]" if field_name == "outcomes"
                                           else r"[,;]") if p]
        return _JOINED[field_name].join(parts)
    return "" if value is None else str(value).strip()


def _rows_of(sheet, key_column: int) -> Dict[str, int]:
    """Where each row lives, by its key. Case-insensitive, because an id typed by hand is as
    likely to be s-04 as S-04 and a second row for the same state is the worst outcome here."""
    found: Dict[str, int] = {}
    for row in sheet.iter_rows(min_row=2):
        if not row:
            continue
        key = str(row[0].value or "").strip()
        if key:
            found.setdefault(key.upper(), row[0].row)
    return found


def _first_empty(sheet) -> int:
    """The row a new entry goes on. ``max_row`` alone lands on the last row that ever held a
    value, and a sheet somebody cleared by deleting the text rather than the rows would grow a
    hundred blank lines before the next entry appeared."""
    for row in sheet.iter_rows(min_row=2):
        if not any(str(cell.value or "").strip() for cell in row):
            return row[0].row
    return sheet.max_row + 1


def apply_edits(path: str, edits: Sequence[dict]) -> Report:
    """Apply a batch of row edits to the workbook and say what happened.

    A batch rather than one at a time, because the edits arrive together from one Save and because
    a half-applied batch is the state nobody can reason about: a decision written and the state
    that reaches it not, with no record of which half landed. Everything is applied to one open
    workbook and saved once, so a failure part way through leaves the file untouched.

    Each edit is ``{"kind": ..., "key": ..., "action": "upsert"|"delete", "fields": {...}}``.
    """
    if not edits:
        return Report()

    workbook = _open_for_editing(path)
    report = Report()

    for edit in edits:
        kind = KINDS.get(str(edit.get("kind", "")))
        if kind is None:
            report.refused.append(f"{edit.get('kind')!r} is not something this can edit.")
            continue
        if kind.sheet not in workbook.sheetnames:
            report.refused.append(f"This workbook has no {kind.sheet} sheet.")
            continue

        sheet = workbook[kind.sheet]
        fields = dict(edit.get("fields") or {})
        key = str(edit.get("key") or fields.get(kind.key) or "").strip()
        action = str(edit.get("action", "upsert"))

        if not key:
            report.refused.append(f"A {edit.get('kind')} was sent with no {kind.key}.")
            continue

        at = _rows_of(sheet, 1).get(key.upper())

        if action == "delete":
            if at is None:
                report.refused.append(f"{key} is not in {kind.sheet}, so it was not removed.")
                continue
            sheet.delete_rows(at, 1)
            report.removed.append((edit["kind"], key))
            continue

        if at is None:
            at = _first_empty(sheet)
            fields.setdefault(kind.key, key)
        for name, value in fields.items():
            column = kind.columns.get(name)
            if column is None:                    # a field this file has no column for
                continue
            sheet.cell(row=at, column=column, value=_as_cell(name, value))
        report.written.append((edit["kind"], key))

    if not report.changed:
        return report

    workbook.save(path)
    report.dangling = _dangling(path)
    return report


def _dangling(path: str) -> List[str]:
    """References the declaration still makes to things that are not in it.

    Read back off the saved workbook rather than tracked through the edits, because the question
    is about the file as it now stands: a decision deleted while a state still offers it, and a
    decision that never existed but was typed into a state by hand, are the same problem and the
    person fixing them does not care which edit produced it.
    """
    try:
        intake = read_intake(path)
    except Exception:                     # an unreadable workbook is a louder problem than this
        return []

    said: List[str] = []
    decisions = {d.id.upper() for d in intake.decisions}
    states = {s.id.upper() for s in intake.states}
    capabilities = {c.id.upper() for c in intake.capabilities}

    for state in intake.states:
        for decision_id in state.next_decisions:
            if decision_id.upper() not in decisions:
                said.append(f"{state.id} offers {decision_id}, which is not declared.")
    for capability in intake.capabilities:
        for state_id in tuple(capability.entry_states) + tuple(capability.exit_states):
            if state_id.upper() not in states:
                said.append(f"{capability.id}'s span names {state_id}, which is not declared.")
    for decision in intake.decisions:
        if decision.trigger_capability and decision.trigger_capability.upper() not in capabilities:
            said.append(f"{decision.id} belongs to {decision.trigger_capability}, "
                        f"which is not declared.")
    for tool in intake.tools:
        if tool.capability_id and tool.capability_id.upper() not in capabilities:
            said.append(f"{tool.name} is linked to {tool.capability_id}, which is not declared.")
    return said


def preview(path: str, edits: Sequence[dict], scratch: Optional[str] = None):
    """What the declaration would be with these edits, without writing them.

    A copy of the workbook, edited and read back. The alternative -- applying the edits to an
    ``IntakeData`` in memory -- would be faster and would answer a different question: it would
    say what the *model* becomes, where what is wanted is what the *file* becomes, which is what
    every stage after this one reads. The two differ exactly where an edit is malformed, which is
    the case a preview exists for.

    Returns the :class:`IntakeData` the workbook would hold, and the report.
    """
    import shutil
    import tempfile

    source = Path(path)
    target = Path(scratch) if scratch else Path(tempfile.mkdtemp()) / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(source, target)
    try:
        report = apply_edits(str(target), edits)
        return read_intake(str(target)), report
    finally:
        target.unlink(missing_ok=True)
