"""Read a filled intake workbook, read an owner's declared scenario list, and write the blank
template. Assumes a well-formed workbook, with row 1 of each sheet as the header.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List

from openpyxl import Workbook, load_workbook

from ..io import sheets
from ..utils.text import is_yes, normalise_variant, one_of, parse_reached_via, split_list
from .models import (CATEGORIES, INPUT_SOURCES, Capability, Decision, IntakeData, OwnerScenario,
                     Persona, State, Tool)

_DECISION_TOKEN = re.compile(r"DEC-\d+")

# The sheets an intake must have. "Tools" is optional -- an agent that calls nothing
# is unusual but not malformed.
_REQUIRED_SHEETS = ("L1 Use Case", "Personas", "L2 Capabilities", "L3 Decisions",
                    "L4 States")


def _open_for_editing(path: str):
    """Open a workbook that is about to be written back to.

    Reading goes through :func:`sheets.open_for_reading`, which is faster and closes the file
    behind it; this is the one path that cannot use it, because a streaming read-only workbook
    cannot be saved. The error handling is the same either way: a .xlsx is a zip archive, and
    anything else carrying that extension fails with a message naming the container format rather
    than the fix.
    """
    try:
        return load_workbook(path)
    except Exception as exc:
        raise ValueError(
            f"'{Path(path).name}' could not be opened as an Excel workbook ({exc}). This "
            f"usually means the file is not really .xlsx underneath -- an older .xls saved with "
            f"the wrong extension, a download that did not finish, or a password-protected file. "
            f"Re-save an unprotected copy from Excel (File > Save As > Excel Workbook), then "
            f"upload that.") from exc


def _cell(row: List[str], index: int) -> str:
    return row[index] if index < len(row) else ""


def _input_source(raw: str) -> str:
    """Match the declared source against the closed vocabulary; anything unknown reads as User."""
    return one_of(raw, INPUT_SOURCES, "User")


def _max_attempts(raw: str) -> int:
    """How many times one decision may be exercised within a single path. Blank or invalid -> 1."""
    try:
        return max(1, int(float(str(raw).strip())))
    except (TypeError, ValueError):
        return 1


def _outcome_type(raw: str) -> str:
    """Declared category of a terminal state; blank falls back to the keyword heuristic."""
    return one_of(raw, CATEGORIES)


# --------------------------------------------------------------------------- readers
def _sheet(workbook, name: str, path: str) -> list:
    """One sheet's data rows, or a message naming the sheet that is not there.

    A workbook that has been through Google Sheets, or been rebuilt by hand from a copy, loses or
    renames tabs surprisingly often. openpyxl reports that as a bare KeyError on the sheet name,
    which does not say what the file was expected to contain or where to get one that does.
    """
    if name not in workbook.sheetnames:
        raise ValueError(
            f"'{Path(path).name}' has no '{name}' sheet, so it is not an intake workbook this "
            f"can read. It needs the sheets the template ships with: "
            f"{', '.join(_REQUIRED_SHEETS)}. Download a blank template and fill that in, or "
            f"check you have uploaded the right file.")
    return sheets.read_rows(workbook[name])


def read_intake(path: str) -> IntakeData:
    with sheets.open_for_reading(path, "an intake workbook") as workbook:
        rows = {name: _sheet(workbook, name, path) for name in _REQUIRED_SHEETS}
        rows["Tools"] = (sheets.read_rows(workbook["Tools"])
                         if "Tools" in workbook.sheetnames else [])

    use_case = {_cell(r, 0): _cell(r, 1) for r in rows["L1 Use Case"] if _cell(r, 0)}

    personas = [Persona(_cell(r, 0), _cell(r, 1),
                        split_list(_cell(r, 2), separators=r"[,;]"), is_yes(_cell(r, 3)))
                for r in rows["Personas"]]
    if not personas:
        raise ValueError(
            f"'{Path(path).name}' declares no personas, and every scenario is walked by one. Add "
            f"at least one row to the Personas sheet -- a cooperative user trying to use the "
            f"service as intended is the one every agent has.")
    if not any(p.is_default for p in personas):
        personas[0] = Persona(personas[0].id, personas[0].name, personas[0].applies_to, True)

    capabilities = [Capability(_cell(r, 0), _cell(r, 1), _cell(r, 2))
                    for r in rows["L2 Capabilities"]]

    decisions = [Decision(_cell(r, 0), _cell(r, 1), _cell(r, 2), _cell(r, 3),
                          [normalise_variant(v) for v in split_list(_cell(r, 4), separators=r"[/]")],
                          _input_source(_cell(r, 5)), _max_attempts(_cell(r, 6)), _cell(r, 7),
                          is_yes(_cell(r, 8)))
                 for r in rows["L3 Decisions"]]

    states = [State(_cell(r, 0), _cell(r, 1), _cell(r, 2),
                    _DECISION_TOKEN.findall(_cell(r, 3)), is_yes(_cell(r, 4)),
                    _outcome_type(_cell(r, 5)))
              for r in rows["L4 States"]]

    tools = [Tool(_cell(r, 0), _cell(r, 1), is_yes(_cell(r, 2)))
             for r in rows["Tools"] if _cell(r, 0)]

    return IntakeData(use_case, personas, capabilities, decisions, states, tools)


def read_review_notes(path: str) -> List[dict]:
    """The drafter's own review notes, read back from the "Review This" sheet it wrote.

    Nothing else persists a draft's self-assessment -- the workbook is the one place it lives,
    the same as everything else this tool declares about the agent -- so recovering it later (to
    fold into the intake stage's gap questions) means reading this sheet back rather than keeping
    a second copy of it anywhere. Absent, unreadable, or from a hand-built workbook with no such
    sheet, this returns no notes rather than raising: a missing self-assessment is not a reason to
    keep the rest of the intake from being read.
    """
    try:
        with sheets.open_for_reading(path, "an intake workbook") as workbook:
            if "Review This" not in workbook.sheetnames:
                return []
            rows = sheets.read_rows(workbook["Review This"])
    except Exception:
        return []

    notes: List[dict] = []
    in_notes = False
    for row in rows:
        first = _cell(row, 0).strip()
        if first == "Note":
            in_notes = True
            continue
        if not in_notes:
            continue
        note = _cell(row, 2).strip()
        if first and note:
            notes.append({"field": first, "note": note})
    return notes


def read_owner_scenarios(path: str, sheet_name: str = "Scenarios") -> List[OwnerScenario]:
    """Read a modelling team's own declared scenario list: ID, Description, optional Decision Path.

    Not the coverage stage's input -- that reads transcripts, since a list of scenario titles is a
    claim about their testing rather than the testing itself. This is the optional context handed
    to the final review, where knowing what they say they cover can inform what it proposes.
    """
    with sheets.open_for_reading(path, "a scenario library") as workbook:
        if sheet_name not in workbook.sheetnames:
            raise ValueError(
                f"'{Path(path).name}' has no '{sheet_name}' sheet. This reads a list with one row "
                f"per scenario: an id in the first column, a description in the second, and "
                f"optionally a decision path in the third. It has: "
                f"{', '.join(workbook.sheetnames)}.")
        rows = sheets.read_rows(workbook[sheet_name])
    return [OwnerScenario(_cell(r, 0), _cell(r, 1), _cell(r, 2))
            for r in rows if _cell(r, 0) and _cell(r, 1)]


# --------------------------------------------------------------------------- template
_GUIDE = [
    ("Fill order", "L1 Use Case, Personas, L2 Capabilities, L3 Decisions, L4 States, then Tools."),
    ("The core idea", "L3 Decisions and L4 States describe the agent as a graph. A State is a "
                      "position the conversation can be in; a Decision is a branch point with named "
                      "outcomes; 'Reached Via' on a State says which Decision outcome leads there."),
    ("Reached Via", "Use 'Start' for the opening state, or 'DEC-xx=Variant' for a state reached by a "
                    "decision outcome. Two states may share the same value if an outcome recurs."),
    ("Possible Outputs", "Separate outcomes with '/'. Use plain words where they fit (Pass, Fail, "
                         "Escalate, Terminate, Retry) so scenarios are categorised correctly."),
    ("Input Source", "Where the decision's input arrives from: User, Tool, Memory-Session, "
                     "Memory-CrossSession, System-Context, Document. Only 'User' steps become "
                     "conversational turns — internal reasoning, memory lookups and tool-driven "
                     "branches are still decisions, but the tester does not script them. Blank "
                     "reads as User."),
    ("Max Attempts", "How many times this one decision may be exercised within a single path, "
                     "e.g. 3 where authentication allows three tries. Blank reads as 1. This is "
                     "what bounds retry loops — there is no global cap."),
    ("Outcome Condition", "Optional. What actually triggers each outcome, e.g. 'risk score < 0.3'. "
                          "Not used for graph traversal; recorded so boundary inputs can be "
                          "derived later."),
    ("Out of Scope?", "Y if this decision has already been reviewed elsewhere and is being reused "
                      "as-is -- a plug-and-play sub-system covered by a separate engagement is "
                      "the usual case. The decision still belongs in the sheet, because the graph "
                      "is not honest without it, but no scenario is generated through it. Blank "
                      "reads as No."),
    ("Outcome Type", "On a terminal state only: Happy path, Retry, Fallback, Escalation or "
                     "Termination. This is what sets a scenario's category, so declare it rather "
                     "than relying on outcome wording."),
    ("Capability Type", "One of: Lookup (reads and reports), Transactional (changes account or "
                        "financial state), Gating (authenticates or authorises), Advisory "
                        "(explains or recommends), PII-handling (touches sensitive personal "
                        "data). Used to decide which adversarial probes apply, so pick the "
                        "closest fit rather than leaving it blank."),
    ("Example — Persona", "P1 | Cooperative, verified user | Happy path | Y"),
    ("Example — Capability", "CAP-01 | Authentication | Gating"),
    ("Example — Decision", "DEC-01 | Authentication outcome | CAP-01 | credentials | Pass / Fail "
                           "| User | 3 | 3 failed tries locks the session"),
    ("Example — State", "S-00 | Start | Session begins, unauthenticated | DEC-01 | No | (blank)"),
    ("Example — Tool", "Identity verification service | CAP-01 | No"),
]

_L1_FIELDS = [
    "Use case name", "Business objective", "Use case rating (informational)",
    "Agent type", "Channel / modality", "Human handoff triggers",
    "Safety requirements", "Success criteria", "Known limitations",
]


_NEXT_DECISIONS_COLUMN = 4                                 # "Valid Next Decisions" on L4 States


def set_decision_scope(path: str, decision_id: str, out_of_scope: bool) -> bool:
    """Flip one decision's Out of Scope column in place. Returns whether a row was found.

    A narrow exception to "edit the workbook and upload it again": scope is a single yes/no a
    person is expected to flip while looking at the graph rather than while looking at a
    spreadsheet, so the interface writes it directly instead of making a full workbook round trip
    the only way to set it. ``sheet.cell(...)`` rather than indexing the row tuple, because a
    workbook drafted before this column existed has fewer than nine columns and indexing past the
    end of a short row raises; writing by row and column number extends the sheet instead.
    """
    workbook = _open_for_editing(path)
    if "L3 Decisions" not in workbook.sheetnames:
        return False
    sheet = workbook["L3 Decisions"]
    for row in sheet.iter_rows(min_row=2):
        if row and str(row[0].value or "").strip() == decision_id:
            sheet.cell(row=row[0].row, column=9, value="Yes" if out_of_scope else "No")
            workbook.save(path)
            return True
    return False


def set_state_reached_via(path: str, state_id: str, reached_via: str) -> bool:
    """Correct one state's Reached Via column in place. Returns whether a row was found.

    The same narrow exception as :func:`set_decision_scope`, for the other half of a structure
    review's reconnection proposals: a state the declared graph does not actually connect, fixed
    by naming the decision outcome that reaches it, without a full workbook round trip.
    """
    workbook = _open_for_editing(path)
    if "L4 States" not in workbook.sheetnames:
        return False
    sheet = workbook["L4 States"]
    for row in sheet.iter_rows(min_row=2):
        if row and str(row[0].value or "").strip() == state_id:
            sheet.cell(row=row[0].row, column=2, value=reached_via)
            workbook.save(path)
            return True
    return False


def attach_decision_to_state(path: str, state_id: str, decision_id: str) -> bool:
    """Add ``decision_id`` to a state's Valid Next Decisions. Returns whether anything changed.

    Amended in place rather than the workbook rewritten, so every other sheet -- and anything a
    person has put in the file by hand -- survives untouched. A decision the state already names
    is left alone rather than repeated.
    """
    workbook = _open_for_editing(path)
    if "L4 States" not in workbook.sheetnames:
        return False

    sheet = workbook["L4 States"]
    for row in sheet.iter_rows(min_row=2):
        if str(row[0].value or "").strip() != state_id:
            continue
        cell = row[_NEXT_DECISIONS_COLUMN - 1]
        already = _DECISION_TOKEN.findall(str(cell.value or ""))
        if decision_id in already:
            return False
        cell.value = ", ".join(already + [decision_id])
        workbook.save(path)
        return True
    return False


_REACHED_VIA_KEY = re.compile(r"^\s*([A-Za-z]+-\d+)\s*=\s*(.+?)\s*$")


def _outcome_key(decision_id: str, outcome: str) -> str:
    """A ``DEC-xx=Outcome`` pair as a lookup key, tolerant of case and spacing.

    Whatever wrote the workbook and whatever proposed a merge may not agree on either -- the
    workbook's own text is whatever a person or an earlier draft happened to type, and the model
    was never shown it directly, only the intake's rendered description of it.
    """
    return f"{decision_id.strip().upper()}={outcome.strip().lower()}"


def merge_decisions(path: str, decision_ids: List[str], new_id: str, new_name: str,
                    new_outcomes: List[str], outcome_map: Dict[str, str],
                    primary_capability: str = "") -> bool:
    """Collapse several decisions into one, remapping every state that pointed at any of them.

    ``new_id`` must be one of ``decision_ids`` -- that row is rewritten in place with the new
    name and outcomes rather than replaced, so nothing that already pointed at it (a state's
    Reached Via, another state's Valid Next Decisions) has to change to keep pointing at the right
    row. Every *other* named decision's row is deleted.

    ``outcome_map`` gives, for each ``DECID=OldOutcome`` combination across every merged decision,
    which of ``new_outcomes`` it becomes. Every L4 state whose Reached Via names one of the merged
    decisions is rewritten to ``new_id`` and the mapped outcome; every state whose Valid Next
    Decisions named one of the *other* merged decisions is rewritten to name ``new_id`` instead. A
    pair with no entry in ``outcome_map`` is left exactly as it was, on the id it already named --
    better an orphaned reference to a decision that still exists in spirit under a new id than a
    silently invented mapping.

    Capabilities are not rewritten: the merged decision keeps whichever single capability
    ``primary_capability`` names (or its own, where none is given), and a decision that drew on
    more than one keeps the others only as a note a person can see, not as a structural link --
    the workbook's Triggering Capability column holds one id, and forcing several into it would be
    inventing a shape the sheet does not have rather than merging within the one it does.
    """
    decision_ids = [str(d).strip().upper() for d in decision_ids]
    new_id = str(new_id).strip().upper()
    if new_id not in decision_ids or len(decision_ids) < 2 or len(new_outcomes) < 2:
        return False
    remove_ids = [d for d in decision_ids if d != new_id]

    normalised_map: Dict[str, str] = {}
    for key, value in (outcome_map or {}).items():
        match = _REACHED_VIA_KEY.match(str(key))
        if match and str(value).strip() in new_outcomes:
            normalised_map[_outcome_key(match.group(1), match.group(2))] = str(value).strip()

    workbook = _open_for_editing(path)
    if "L3 Decisions" not in workbook.sheetnames or "L4 States" not in workbook.sheetnames:
        return False

    decisions_sheet = workbook["L3 Decisions"]
    to_delete, found_new = [], False
    for row in decisions_sheet.iter_rows(min_row=2):
        identifier = str(row[0].value or "").strip().upper()
        if identifier == new_id:
            found_new = True
            decisions_sheet.cell(row=row[0].row, column=2, value=new_name or row[1].value)
            decisions_sheet.cell(row=row[0].row, column=5, value=" / ".join(new_outcomes))
            if primary_capability:
                decisions_sheet.cell(row=row[0].row, column=3, value=primary_capability)
            note = f"Merged with {', '.join(d for d in remove_ids)}."
            existing_inputs = str(row[3].value or "").strip()
            decisions_sheet.cell(row=row[0].row, column=4,
                                 value=f"{existing_inputs} ({note})" if existing_inputs else note)
        elif identifier in remove_ids:
            to_delete.append(row[0].row)
    if not found_new:
        return False
    for row_number in sorted(to_delete, reverse=True):
        decisions_sheet.delete_rows(row_number)

    states_sheet = workbook["L4 States"]
    for row in states_sheet.iter_rows(min_row=2):
        reached_via = str(row[1].value or "").strip()
        pairs = parse_reached_via(reached_via)
        if pairs and any(dec in decision_ids for dec, _ in pairs):
            rebuilt, changed = [], False
            for dec, outcome in pairs:
                mapped = normalised_map.get(_outcome_key(dec, outcome)) if dec in decision_ids else None
                if mapped:
                    rebuilt.append(f"{new_id}={mapped}")
                    changed = True
                else:
                    rebuilt.append(f"{dec}={outcome}")
            if changed:
                states_sheet.cell(row=row[0].row, column=2,
                                  value=", ".join(dict.fromkeys(rebuilt)))

        next_cell = row[_NEXT_DECISIONS_COLUMN - 1]
        existing = _DECISION_TOKEN.findall(str(next_cell.value or ""))
        if any(d in decision_ids for d in existing):
            rebuilt_ids = []
            for decision_id in existing:
                candidate = new_id if decision_id in decision_ids else decision_id
                if candidate not in rebuilt_ids:
                    rebuilt_ids.append(candidate)
            states_sheet.cell(row=row[0].row, column=_NEXT_DECISIONS_COLUMN,
                              value=", ".join(rebuilt_ids))

    workbook.save(path)
    return True


def write_template(path: str) -> None:
    workbook = Workbook()
    workbook.remove(workbook.active)

    guide = sheets.add_sheet(workbook, "Guide", ["Topic", "Notes"], [26, 100])
    sheets.write_rows(guide, _GUIDE)

    l1 = sheets.add_sheet(workbook, "L1 Use Case", ["Field", "Value"], [34, 90])
    sheets.write_rows(l1, [[field, ""] for field in _L1_FIELDS])

    sheets.add_sheet(workbook, "Personas",
                     ["ID", "Name", "Applies To", "Default"], [10, 34, 28, 10])
    sheets.add_sheet(workbook, "L2 Capabilities",
                     ["Capability ID", "Name", "Type"], [14, 30, 20])
    sheets.add_sheet(workbook, "L3 Decisions",
                     ["Decision ID", "Decision", "Triggering Capability", "Inputs",
                      "Possible Outputs", "Input Source", "Max Attempts", "Outcome Condition",
                      "Out of Scope?"],
                     [12, 28, 22, 30, 30, 20, 14, 34, 14])
    sheets.add_sheet(workbook, "L4 States",
                     ["State ID", "Reached Via", "Description", "Valid Next Decisions",
                      "Terminal?", "Outcome Type"], [10, 26, 34, 26, 11, 16])
    sheets.add_sheet(workbook, "Tools",
                     ["Tool Name", "Capability ID", "State-changing?"], [30, 16, 16])

    workbook.save(path)
