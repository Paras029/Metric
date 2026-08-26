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
        "id": 1, "name": 2, "type": 3, "entry_states": 4, "exit_states": 5,
        "out_of_scope": 6}, prefix="CAP-"),
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

# Fields that are not columns anywhere. A capability's decisions are recorded on the *decisions*,
# one row each, which is the right place for them -- a decision belongs to exactly one capability
# and the workbook says so where the decision is.
#
# It is the wrong place to *edit* them from, though, and that was the defect. Which decisions make
# up a capability is the first judgement somebody makes about it, and everything else about the
# capability is derived from that answer: which states it folds in, which of those can be its
# entry, where a route through it leaves. Editing it one decision at a time from the other end of
# the declaration meant the capability's own controls were computed from something its own row
# could not change -- so a wrong grouping could be seen and not corrected.
#
# So the capability row carries the membership, and saving it writes the decisions. Expanded here
# rather than in the interface, so the path with scripting and the path without it cannot come to
# different conclusions about what a tick means.
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


def set_use_case(path: str, fields: Dict[str, str]) -> Report:
    """Write named fields of the use case. Fields not named are left exactly as they are.

    Its own function because its sheet is its own shape: two columns, field names down one and
    values down the other, so there is no column number to write to -- the field is found by its
    name. A field the sheet does not already carry is appended rather than refused, because the
    reader takes whatever is there and a declaration that wants to record something extra should
    be able to.
    """
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
    """Give something a new id, and repoint everything that named it by the old one.

    The one edit that *must* cascade, and the reason is worth being precise about. A deletion is
    reported rather than cascaded because the references it leaves behind become genuinely
    undefined -- somebody has to decide what they should say instead. A rename leaves nothing
    undefined: it is the same thing under a new name, every reference still means what it meant,
    and repointing them is not a judgement but bookkeeping. Left undone, a rename silently breaks
    every route through the renamed row, which is the worst outcome available here.

    What follows what:

    * a **decision** is named by states, in ``Reached Via`` (``DEC-xx=Outcome``) and in
      ``Valid Next Decisions``;
    * a **state** is named by capabilities, in their entry and exit spans;
    * a **capability** is named by decisions and by tools, in their capability column.

    Refused where the new id is already taken. Writing it anyway would fold two rows into one
    without saying so, and the graph would come back missing a branch nobody removed.
    """
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
    """One Reached Via cell with a decision id swapped, and everything else left alone.

    Only the id is touched. The outcome after the equals sign, the separators, and any note
    somebody wrote around them are what a person typed, and a rename is not licence to reformat
    them -- see the retry bounds the walk's own parser is careful to read past.
    """
    import re

    return re.sub(r"(?<![A-Za-z0-9-])" + re.escape(old_key) + r"(?![A-Za-z0-9-])",
                  new_key, cell or "", flags=re.I)


def expand(path: str, edits: Sequence[dict]) -> List[dict]:
    """Turn virtual fields into the row edits that actually record them.

    Reads the workbook, because the expansion is a *difference*: ticking three decisions for a
    capability says as much about the ones no longer ticked as about the ones now are, and the
    only way to know which those were is to look at what is recorded. A tick that only ever added
    would leave a decision belonging to two capabilities, which the graph cannot represent and the
    walk silently resolves by taking whichever it read first.
    """
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
    """Apply a batch of row edits to the workbook and say what happened.

    A batch rather than one at a time, because the edits arrive together from one Save and because
    a half-applied batch is the state nobody can reason about: a decision written and the state
    that reaches it not, with no record of which half landed. Everything is applied to one open
    workbook and saved once, so a failure part way through leaves the file untouched.

    Each edit is ``{"kind": ..., "key": ..., "action": "upsert"|"delete", "fields": {...}}``.
    """
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
