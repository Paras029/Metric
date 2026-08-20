"""Editing the declaration a row at a time, from the interface rather than in a spreadsheet."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from metric.shared.text import split_list
from metric.domain.intake import _open_for_editing, read_intake

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

# Fields with no column of their own. A capability's decisions are recorded one per decision row,
# which is where they belong, but everything else about the capability derives from them -- so the
# capability's own row has to be able to set them. expand() turns the tick list into row writes.
VIRTUAL = {("capability", "decisions")}

# The one sheet that is not a table of rows. L1 Use Case is a column of field names beside a column
# of values, so a "row" of it is the whole use case and its fields are found by name rather than by
# column number.
USE_CASE_SHEET = "L1 Use Case"


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
    """The next free id in a series, as a person would number it."""
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


def set_use_case(path: str, fields: Dict[str, str]) -> Report:
    """Write named fields of the use case. Fields not named are left exactly as they are."""
    report = Report()
    if not fields:
        return report

    workbook = _open_for_editing(path)
    if USE_CASE_SHEET not in workbook.sheetnames:
        report.refused.append(f"This workbook has no {USE_CASE_SHEET} sheet.")
        return report

    sheet = workbook[USE_CASE_SHEET]
    at = {}
    for row in sheet.iter_rows(min_row=2):
        name = str(row[0].value or "").strip()
        if name:
            at.setdefault(name.casefold(), row[0].row)

    for name, value in fields.items():
        where = at.get(name.strip().casefold())
        if where is None:
            where = _first_empty(sheet)
            sheet.cell(row=where, column=1, value=name)
            at[name.strip().casefold()] = where
        sheet.cell(row=where, column=2, value="" if value is None else str(value).strip())
        report.written.append(("use_case", name))

    workbook.save(path)
    return report


def rename(path: str, kind: str, old_key: str, new_key: str) -> Report:
    """Give something a new id, and repoint everything that named it by the old one."""
    report = Report()
    old_key, new_key = str(old_key or "").strip(), str(new_key or "").strip()
    spec = KINDS.get(kind)

    if spec is None:
        report.refused.append(f"{kind!r} is not something this can rename.")
        return report
    if not old_key or not new_key:
        report.refused.append("A rename needs both the old id and the new one.")
        return report
    if old_key.casefold() == new_key.casefold():
        return report

    try:
        intake = read_intake(path)
    except Exception as exc:
        report.refused.append(f"The declaration could not be read: {exc}")
        return report

    taken = {
        "decision": {d.id for d in intake.decisions},
        "state": {s.id for s in intake.states},
        "capability": {c.id for c in intake.capabilities},
        "tool": {t.name for t in intake.tools},
        "persona": {p.id for p in intake.personas},
    }[kind]
    if old_key not in {k for k in taken}:
        report.refused.append(f"{old_key} is not in the declaration.")
        return report
    if any(new_key.casefold() == k.casefold() for k in taken):
        report.refused.append(
            f"{new_key} is already taken. Renaming onto it would fold two rows into one and the "
            f"graph would come back missing a branch nobody removed.")
        return report

    edits: List[dict] = [{"kind": kind, "key": old_key, "action": "rekey", "to": new_key}]
    edits.extend(_repoint(intake, kind, old_key, new_key))

    written = apply_edits(path, edits)
    written.written.append((kind, f"{old_key} → {new_key}"))
    return written


def _repoint(intake, kind: str, old_key: str, new_key: str) -> List[dict]:
    """Every other row that names the thing being renamed, rewritten to name it by its new id."""
    same = old_key.casefold()
    edits: List[dict] = []

    if kind == "decision":
        for state in intake.states:
            fields = {}
            if any(d.casefold() == same for d in state.next_decisions):
                fields["next_decisions"] = [new_key if d.casefold() == same else d
                                            for d in state.next_decisions]
            reached = _rewrite_reached_via(state.reached_via, old_key, new_key)
            if reached != state.reached_via:
                fields["reached_via"] = reached
            if fields:
                edits.append({"kind": "state", "key": state.id, "action": "upsert",
                              "fields": fields})

    elif kind == "state":
        for capability in intake.capabilities:
            fields = {}
            for name, current in (("entry_states", capability.entry_states),
                                  ("exit_states", capability.exit_states)):
                if any(s.casefold() == same for s in current):
                    fields[name] = [new_key if s.casefold() == same else s for s in current]
            if fields:
                edits.append({"kind": "capability", "key": capability.id, "action": "upsert",
                              "fields": fields})

    elif kind == "capability":
        for decision in intake.decisions:
            if decision.trigger_capability.casefold() == same:
                edits.append({"kind": "decision", "key": decision.id, "action": "upsert",
                              "fields": {"capability_id": new_key}})
        for tool in intake.tools:
            if tool.capability_id.casefold() == same:
                edits.append({"kind": "tool", "key": tool.name, "action": "upsert",
                              "fields": {"capability_id": new_key}})

    return edits


def _rewrite_reached_via(cell: str, old_key: str, new_key: str) -> str:
    """One Reached Via cell with a decision id swapped, and everything else left alone."""
    import re

    return re.sub(r"(?<![A-Za-z0-9-])" + re.escape(old_key) + r"(?![A-Za-z0-9-])",
                  new_key, cell or "", flags=re.I)


def expand(path: str, edits: Sequence[dict]) -> List[dict]:
    """Turn virtual fields into the row edits that actually record them."""
    if not any((str(e.get("kind", "")), name) in VIRTUAL
               for e in edits for name in (e.get("fields") or {})):
        return list(edits)

    try:
        intake = read_intake(path)
    except Exception:
        # An unreadable workbook is a louder problem than this, and apply_edits will raise its own
        # message about it. Passing the edits through unexpanded drops the virtual fields, which
        # have no column and are ignored -- so nothing is written wrongly, only not at all.
        return list(edits)

    owned_by = {d.id.upper(): d.trigger_capability for d in intake.decisions}
    grown: List[dict] = []

    for edit in edits:
        kind = str(edit.get("kind", ""))
        fields = dict(edit.get("fields") or {})
        key = str(edit.get("key") or "").strip()

        if kind == "capability" and "decisions" in fields:
            wanted = {str(d).strip().upper()
                      for d in _as_list(fields.pop("decisions")) if str(d).strip()}
            for decision_id, capability_id in owned_by.items():
                belongs = decision_id in wanted
                if belongs == (capability_id.upper() == key.upper()):
                    continue                       # already says what the tick says
                grown.append({"kind": "decision", "key": decision_id, "action": "upsert",
                              "fields": {"capability_id": key if belongs else ""}})
        grown.append({**edit, "fields": fields})

    return grown


def _as_list(value) -> List[str]:
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return [p for p in split_list(str(value or ""), separators=r"[,;]") if p]


def apply_edits(path: str, edits: Sequence[dict]) -> Report:
    """Apply a batch of row edits to the workbook and say what happened."""
    if not edits:
        return Report()

    edits = expand(path, edits)
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

        if action == "rekey":
            if at is None:
                report.refused.append(f"{key} is not in {kind.sheet}, so it was not renamed.")
                continue
            sheet.cell(row=at, column=kind.columns[kind.key],
                       value=str(edit.get("to") or "").strip())
            report.written.append((edit["kind"], key))
            continue

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
    """References the declaration still makes to things that are not in it."""
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
    """What the declaration would be with these edits, without writing them."""
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
