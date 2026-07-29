"""Read a filled intake workbook, read an owner's scenario library, and write the blank
template. Assumes a well-formed workbook, with row 1 of each sheet as the header.
"""
from __future__ import annotations

import re
from pathlib import Path as _Path
from typing import List

from openpyxl import Workbook, load_workbook

from ..io import sheets
from ..utils.text import is_yes, normalise_variant, one_of, split_list
from .models import (CATEGORIES, INPUT_SOURCES, Capability, Decision, IntakeData, OwnerScenario,
                     Persona, State, Tool)

_DECISION_TOKEN = re.compile(r"DEC-\d+")


def _open_workbook(path: str, **kwargs):
    """Open a workbook, turning a corrupt or wrong-format file into something actionable.

    A .xlsx file is a zip archive; anything else under that extension -- an older .xls saved
    with the wrong extension, a download that did not finish, a password-protected file -- fails
    here with a message that names the container format ("File is not a zip file") rather than
    the fix. This is the one place every intake read goes through, so it is the one place worth
    catching that.
    """
    try:
        return load_workbook(path, **kwargs)
    except Exception as exc:
        raise ValueError(
            f"'{_Path(path).name}' could not be opened as an Excel workbook ({exc}). This "
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
def read_intake(path: str) -> IntakeData:
    workbook = _open_workbook(path, data_only=True)

    use_case = {}
    for row in sheets.read_rows(workbook["L1 Use Case"]):
        if _cell(row, 0):
            use_case[_cell(row, 0)] = _cell(row, 1)

    personas = [Persona(_cell(r, 0), _cell(r, 1),
                        split_list(_cell(r, 2), separators=r"[,;]"), is_yes(_cell(r, 3)))
                for r in sheets.read_rows(workbook["Personas"])]
    if not any(p.is_default for p in personas):
        personas[0] = Persona(personas[0].id, personas[0].name, personas[0].applies_to, True)

    capabilities = [Capability(_cell(r, 0), _cell(r, 1), _cell(r, 2))
                    for r in sheets.read_rows(workbook["L2 Capabilities"])]

    decisions = [Decision(_cell(r, 0), _cell(r, 1), _cell(r, 2), _cell(r, 3),
                          [normalise_variant(v) for v in split_list(_cell(r, 4), separators=r"[/]")],
                          _input_source(_cell(r, 5)), _max_attempts(_cell(r, 6)), _cell(r, 7))
                 for r in sheets.read_rows(workbook["L3 Decisions"])]

    states = [State(_cell(r, 0), _cell(r, 1), _cell(r, 2),
                    _DECISION_TOKEN.findall(_cell(r, 3)), is_yes(_cell(r, 4)),
                    _outcome_type(_cell(r, 5)))
              for r in sheets.read_rows(workbook["L4 States"])]

    tools = []
    if "Tools" in workbook.sheetnames:
        tools = [Tool(_cell(r, 0), _cell(r, 1), is_yes(_cell(r, 2)))
                 for r in sheets.read_rows(workbook["Tools"]) if _cell(r, 0)]

    return IntakeData(use_case, personas, capabilities, decisions, states, tools)


def read_owner_scenarios(path: str, sheet_name: str = "Scenarios") -> List[OwnerScenario]:
    """Read a modeling team's own scenario library: ID, Description, optional Decision Path."""
    workbook = _open_workbook(path, data_only=True)
    sheet = workbook[sheet_name]
    return [OwnerScenario(_cell(r, 0), _cell(r, 1), _cell(r, 2))
            for r in sheets.read_rows(sheet) if _cell(r, 0) and _cell(r, 1)]


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


def append_rows(path: str, decisions: List[list] = None, states: List[list] = None,
                links: dict = None) -> int:
    """Apply sketched additions to an existing intake workbook. Returns how many changes landed.

    Rows are appended and existing ones amended in place rather than the workbook rewritten, so
    every other sheet, and anything a person has put in the file by hand, survives untouched --
    the workbook is theirs, and a tool that rewrites it wholesale is a tool people stop trusting
    with their own edits.

    ``links`` maps a declared state to the new decisions that should follow it. Adding a decision
    is never only a new row: something has to lead to it, and that something is a row that already
    exists. Without this the decision is written and nothing reaches it, which draws as an
    unreachable box and generates no scenarios.

    An id already in the sheet is skipped rather than duplicated: two rows with one id would give
    the graph two nodes for one thing, and the reader would silently take whichever came last.
    """
    workbook = _open_workbook(path)
    changed = 0

    for sheet_name, rows in (("L3 Decisions", decisions or []), ("L4 States", states or [])):
        if not rows or sheet_name not in workbook.sheetnames:
            continue
        sheet = workbook[sheet_name]
        existing = {str(r[0]).strip() for r in sheets.read_rows(sheet) if r and r[0]}
        for row in rows:
            if str(row[0]).strip() in existing:
                continue
            sheet.append(row)
            existing.add(str(row[0]).strip())
            changed += 1

    if links and "L4 States" in workbook.sheetnames:
        sheet = workbook["L4 States"]
        for row in sheet.iter_rows(min_row=2):
            wanted = links.get(str(row[0].value or "").strip())
            if not wanted:
                continue
            cell = row[_NEXT_DECISIONS_COLUMN - 1]
            already = _DECISION_TOKEN.findall(str(cell.value or ""))
            addition = [d for d in wanted if d not in already]
            if not addition:
                continue
            cell.value = ", ".join(already + addition)
            changed += 1

    if changed:
        workbook.save(path)
    return changed


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
                      "Possible Outputs", "Input Source", "Max Attempts", "Outcome Condition"],
                     [12, 28, 22, 30, 30, 20, 14, 34])
    sheets.add_sheet(workbook, "L4 States",
                     ["State ID", "Reached Via", "Description", "Valid Next Decisions",
                      "Terminal?", "Outcome Type"], [10, 26, 34, 26, 11, 16])
    sheets.add_sheet(workbook, "Tools",
                     ["Tool Name", "Capability ID", "State-changing?"], [30, 16, 16])

    workbook.save(path)
