"""The workbooks the generator writes.

    Scenario Graph   Internal, pre-LLM. Deterministic metadata and per-turn expected outcomes
                     straight off the decision graph. Output of `build-graph`, input to `refine`.
    Challenge Pack   Issued to the agent's owner and returned filled in. Every scenario, and no
                     expected outcomes of any kind — no decision path, expected variant,
                     expected tool call, category or materiality. Two reference sheets state
                     what to run; two response sheets are pre-populated with one row per
                     scenario, run and turn.
    Registry         Internal. Full metadata, per-turn expected outcomes, and the text as
                     issued. The canonical index coverage matches against.
    Overlap Report   Internal. What the owner's own scenarios covered, what they missed, and
                     what they tested beyond the declared model.
"""
from __future__ import annotations

from typing import List

from openpyxl import Workbook

from . import sheets
from ..core.generation import (fallback_description, fallback_turn_plan, recommended_turns,
                                required_runs, turn_plan_lines)
from ..utils.text import parse_path_str
from ..core.models import (FUNCTIONAL_ORIGINS, BenchmarkScenario, IntakeData, Scenario,
                           Step, TurnMeta)
from ..core.probes import ADVERSARIAL_PERSONA


_METADATA_COLUMNS = ["SC ID", "Decision Path", "Category", "Materiality",
                     "Materiality Confidence", "Materiality Rationale", "Capabilities", "Tools",
                     "Persona ID", "State-changing?", "Seeded State", "Termination", "Origin",
                     "Materiality Override", "Probe ID", "Probe Family",
                     "Reviewed Materiality", "Review Rationale", "Review Flag",
                     "Proposal Rationale", "Proposal Anchor", "Effective Materiality",
                     "Their Coverage", "Their Coverage Note",
                     "Reviewed Category", "Review Category Rationale"]
_METADATA_WIDTHS = [10, 40, 18, 12, 12, 46, 22, 24, 11, 13, 26, 40, 13, 18, 26, 22,
                    18, 52, 16, 52, 14, 18, 18, 26, 18, 52]

_PACK_INSTRUCTIONS = [
    ("What this workbook is", "A set of test scenarios for your agent, issued by MRMG. Run each "
                              "one the requested number of times and record what happened in the "
                              "Run_Log and Run_Summary sheets. Return this same file."),
    ("Sheets you read", "Scenarios and Turn_Plan describe what to run. Do not edit them."),
    ("Sheets you fill", "Run_Log (one row per scenario, run and turn) and Run_Summary (one row "
                        "per scenario and run). Both are already pre-populated with the SC ID, "
                        "Run and Turn numbers — fill the blank columns beside them."),
    ("Runs", "Required Runs on the Scenarios sheet is how many independent executions are "
             "required of that scenario. Start each run from a fresh session."),
    ("Turns", "Recommended Turns is a guide, not a limit. If a run takes more turns, insert extra "
              "rows in Run_Log for that SC ID and Run, numbered on from the last turn. If it takes "
              "fewer, leave the surplus rows blank."),
    ("Tool Metadata", "Whatever your framework records for tool activity on that turn — a JSON "
                      "blob, a trace fragment, or plain text. Paste it as-is; no fixed schema."),
    ("Agent Reasoning / Trace", "Any reasoning, chain-of-thought or planning output your framework "
                                "exposes for that turn. Leave blank if none is available."),
    ("Additional Metadata", "Optional. Anything else you already capture per turn — latency, token "
                            "counts, model version, guardrail flags."),
    ("Do not", "Add, remove or renumber SC IDs, or change the Scenarios and Turn_Plan sheets. "
               "Everything is keyed on SC ID plus Run plus Turn."),
]

_TURN_COLUMNS = ["SC ID", "Turn", "Decision ID", "Decision Name",
                 "Expected Variant", "Expected Tool Call", "Next State", "Input Source"]
_TURN_WIDTHS = [10, 7, 12, 26, 22, 28, 16, 18]


def _at(row: List[str], index: int) -> str:
    """One cell of a row, or empty where the row is shorter than the header promised."""
    return row[index] if index < len(row) else ""


def _write_metadata_sheet(workbook, scenarios: List[Scenario]) -> None:
    sheet = sheets.add_sheet(workbook, "Scenario_Metadata", _METADATA_COLUMNS, _METADATA_WIDTHS)
    sheets.write_rows(sheet, [[
        s.id, s.path_str, s.category, s.materiality, s.materiality_confidence,
        s.materiality_rationale, ", ".join(s.capabilities), ", ".join(s.tools), s.persona.id,
        "Yes" if s.touches_state_change else "No", s.seeded_state, s.termination, s.origin,
        s.materiality_override, s.probe_id, s.probe_family,
        s.review_materiality, s.review_rationale, s.review_flag,
        s.proposed_rationale, s.proposed_anchor, s.effective_materiality,
        s.owner_coverage, s.owner_coverage_note,
        s.review_category, s.review_category_rationale]
        for s in scenarios])


def _write_turn_sheet(workbook, scenarios: List[Scenario]) -> None:
    sheet = sheets.add_sheet(workbook, "Turn_Metadata", _TURN_COLUMNS, _TURN_WIDTHS)
    sheets.write_rows(sheet, [[s.id, t.index, t.decision_id, t.decision_name,
                               t.expected_variant, t.expected_tool, t.next_state, t.input_source]
                              for s in scenarios for t in s.turn_meta])


def write_scenario_graph(path: str, intake: IntakeData, scenarios: List[Scenario]) -> None:
    """Pre-LLM internal workbook: deterministic metadata, per-turn expected outcomes, and any
    text already written.

    Probes arrive from the library with a real description and script, so the text sheet is
    written here too — without it that text would be lost on the next read and replaced by
    placeholder wording.
    """
    workbook = Workbook()
    workbook.remove(workbook.active)
    _write_metadata_sheet(workbook, scenarios)
    _write_turn_sheet(workbook, scenarios)
    _write_text_sheet(workbook, scenarios)
    workbook.save(path)


def read_scenarios(path: str, intake: IntakeData) -> List[Scenario]:
    """Reconstruct full Scenario objects from a graph or registry workbook (same Scenario_Metadata
    / Turn_Metadata layout). If a Scenario_Text sheet is present (registry files), its LLM-authored
    description/turn_plan are used; otherwise the deterministic fallback text is recomputed."""
    with sheets.open_for_reading(path, "a benchmark workbook") as workbook:
        turns_by_id: dict = {}
        for row in sheets.read_rows(workbook["Turn_Metadata"]):
            # Positional rather than numbered: these rows are written in order, and a registry
            # that has been through a spreadsheet by hand can arrive with the turn number
            # reformatted or blanked. Refusing to open the whole benchmark over one such cell
            # would be a far worse outcome than renumbering from the order it is already in.
            turns = turns_by_id.setdefault(row[0], [])
            turns.append(TurnMeta(
                index=len(turns) + 1, decision_id=_at(row, 2), decision_name=_at(row, 3),
                expected_variant=_at(row, 4), expected_tool=_at(row, 5), next_state=_at(row, 6),
                input_source=_at(row, 7) or "User"))

        text_by_id: dict = {}
        if "Scenario_Text" in workbook.sheetnames:
            for row in sheets.read_rows(workbook["Scenario_Text"]):
                text_by_id[row[0]] = (row[1] if len(row) > 1 else "",
                                      row[2] if len(row) > 2 else "")

        header, metadata = sheets.read_table(workbook["Scenario_Metadata"])

    # Columns are addressed by the heading printed in the file rather than by position. Twenty-six
    # hand-counted indices had to be kept in step with the writer by eye, and a registry written
    # before a column existed simply has no cell there -- looking the name up handles both.
    at = {name: index for index, name in enumerate(header)}

    scenarios = []
    for row in metadata:
        def cell(name, _row=row):
            index = at.get(name)
            return _row[index] if index is not None and index < len(_row) else ""

        turn_meta = turns_by_id.get(cell("SC ID"), [])
        origin = cell("Origin")
        is_probe = origin == "probe"

        # Synthetic turn rows (probes, and proposals with no declared route) carry "-" as their
        # decision id. Reconstructing a path from those would invent one that never existed.
        path_steps = [Step(t.decision_id, t.expected_variant, t.next_state)
                      for t in turn_meta if t.decision_id and t.decision_id != "-"]
        persona = (ADVERSARIAL_PERSONA if is_probe else
                   intake.persona_by_id(cell("Persona ID")) or
                   next((p for p in intake.personas if p.is_default), intake.personas[0]))

        scenarios.append(Scenario(
            id=cell("SC ID"), path=path_steps, category=cell("Category"),
            persona=persona, seeded_state=cell("Seeded State"), termination=cell("Termination"),
            capabilities=[c.strip() for c in cell("Capabilities").split(",") if c.strip()],
            tools=[t.strip() for t in cell("Tools").split(",") if t.strip()],
            touches_state_change=cell("State-changing?").strip().lower() == "yes",
            turn_meta=turn_meta, origin=origin,
            materiality=cell("Materiality") or "Medium",
            materiality_confidence=cell("Materiality Confidence") or "Low",
            materiality_rationale=cell("Materiality Rationale") or "Not assessed.",
            materiality_override=cell("Materiality Override"),
            probe_id=cell("Probe ID"), probe_family=cell("Probe Family"),
            review_materiality=cell("Reviewed Materiality"),
            review_rationale=cell("Review Rationale"), review_flag=cell("Review Flag"),
            proposed_rationale=cell("Proposal Rationale"),
            proposed_anchor=cell("Proposal Anchor"),
            owner_coverage=cell("Their Coverage"),
            owner_coverage_note=cell("Their Coverage Note"),
            review_category=cell("Reviewed Category"),
            review_category_rationale=cell("Review Category Rationale"),
        ))
        description, turn_plan = text_by_id.get(cell("SC ID"), ("", ""))
        scenarios[-1].description = description or fallback_description(scenarios[-1].category, turn_meta)
        scenarios[-1].turn_plan = turn_plan or fallback_turn_plan(turn_meta)
    return scenarios


def write_challenge_pack(path: str, intake: IntakeData, scenarios: List[Scenario],
                         runs_mapping: dict = None) -> int:
    """Modeling-team-facing workbook, issued blank and returned filled. Returns scenario count.

    Two reference sheets (what to run) and two response sheets (what to fill in). Run_Log and
    Run_Summary are pre-populated down to one row per scenario/run/turn so the requested number
    of runs is a fixed contract rather than something the team has to construct.
    """
    workbook = Workbook()
    workbook.remove(workbook.active)

    guide = sheets.add_sheet(workbook, "Instructions", ["Topic", "Notes"], [26, 110])
    sheets.write_rows(guide, _PACK_INSTRUCTIONS)

    index = sheets.add_sheet(workbook, "Scenarios",
                             ["SC ID", "Description", "Persona", "Starting Situation",
                              "Recommended Turns", "Required Runs"], [10, 66, 30, 32, 18, 14])
    sheets.write_rows(index, [[s.id, s.description, s.persona.name, s.seeded_state,
                               recommended_turns(s), required_runs(s.effective_materiality, runs_mapping)]
                              for s in scenarios])

    plan = sheets.add_sheet(workbook, "Turn_Plan",
                            ["SC ID", "Turn", "What To Induce"], [10, 7, 110])
    sheets.write_rows(plan, [[s.id, turn, line]
                             for s in scenarios
                             for turn, line in enumerate(turn_plan_lines(s), start=1)])

    log = sheets.add_sheet(workbook, "Run_Log",
                           ["SC ID", "Run", "Turn", "User Input (Actual)", "Agent Response",
                            "Tool Metadata", "Agent Reasoning / Trace", "Additional Metadata"],
                           [10, 7, 7, 46, 52, 40, 46, 30])
    sheets.write_rows(log, [[s.id, run, turn, "", "", "", "", ""]
                            for s in scenarios
                            for run in range(1, required_runs(s.effective_materiality, runs_mapping) + 1)
                            for turn in range(1, recommended_turns(s) + 1)])

    summary = sheets.add_sheet(workbook, "Run_Summary",
                               ["SC ID", "Run", "Actual Turns", "Outcome Reached", "How It Ended",
                                "Anomalies / Notes"], [10, 7, 14, 40, 34, 46])
    sheets.write_rows(summary, [[s.id, run, "", "", "", ""]
                                for s in scenarios
                                for run in range(1, required_runs(s.effective_materiality, runs_mapping) + 1)])

    workbook.save(path)
    return len(scenarios)


def _write_text_sheet(workbook: Workbook, scenarios: List[Scenario]) -> None:
    text = sheets.add_sheet(workbook, "Scenario_Text",
                            ["SC ID", "Description", "Turn Plan"], [10, 60, 66])
    sheets.write_rows(text, [[s.id, s.description, s.turn_plan] for s in scenarios])


def write_registry(path: str, intake: IntakeData, scenarios: List[Scenario]) -> None:
    """Internal metadata registry: graph metadata plus the authored owner-facing text."""
    workbook = Workbook()
    workbook.remove(workbook.active)

    _write_metadata_sheet(workbook, scenarios)
    _write_turn_sheet(workbook, scenarios)
    _write_text_sheet(workbook, scenarios)

    workbook.save(path)


def read_registry(path: str, functional_only: bool = True) -> List[BenchmarkScenario]:
    """Load a generated registry back as the benchmark, keeping its category and materiality.

    Coverage is a statement about the functional benchmark, so probes are excluded by default —
    filtered on `Origin`, not on having an empty decision path, since other scenario kinds may
    legitimately share a path with a graph scenario.
    """
    with sheets.open_for_reading(path, "a benchmark workbook") as workbook:
        header, rows = sheets.read_table(workbook["Scenario_Metadata"])

    at = {name: index for index, name in enumerate(header)}
    benchmark = []
    for row in rows:
        def cell(name, _row=row):
            index = at.get(name)
            return _row[index] if index is not None and index < len(_row) else ""

        origin = cell("Origin") or "graph"
        if functional_only and origin not in FUNCTIONAL_ORIGINS:
            continue
        benchmark.append(BenchmarkScenario(
            id=cell("SC ID"), path_str=cell("Decision Path"), category=cell("Category"),
            # The same precedence effective_materiality applies, read off the sheet.
            materiality=(cell("Materiality Override") or cell("Reviewed Materiality")
                         or cell("Materiality")),
            capabilities=[c.strip() for c in cell("Capabilities").split(",") if c.strip()],
            persona_id=cell("Persona ID"),
            signature=tuple(parse_path_str(cell("Decision Path"))),
            origin=origin))
    return benchmark


def write_coverage_report(path: str, report, mappings, texts: dict = None) -> None:
    """What the modelling team's conversations cover, as a workbook.

    Three sheets, in the order the questions get asked. *Scenarios* is the answer -- every
    benchmark scenario with how many of their conversations landed on it, least covered first,
    because the thin end of that list is what goes back to them. *Conversations* is the working:
    one row per transcript with the scenario it was matched to and how sure the match was, so a
    figure on the first sheet can be traced to the exchanges behind it. *Their grouping* appears
    only where they supplied one, and says whether it agrees with ours.
    """
    texts = texts or {}
    workbook = Workbook()
    workbook.remove(workbook.active)

    scenarios = sheets.add_sheet(
        workbook, "Scenarios",
        ["SC ID", "Category", "Materiality", "Conversations", "Represented?",
         "Confidence Split", "Conversation IDs", "Description"],
        [10, 14, 12, 14, 14, 22, 38, 60])
    sheets.write_rows(scenarios, [
        [entry.scenario.id, entry.scenario.category, entry.scenario.materiality, entry.count,
         "Yes" if entry.represented(report.threshold) else "No",
         entry.confidence_summary, ", ".join(entry.conversation_ids),
         texts.get(entry.scenario.id, "")]
        for entry in report.scenarios])

    conversations = sheets.add_sheet(
        workbook, "Conversations",
        ["Conversation ID", "Mapped To", "Confidence", "What They Wanted", "How It Ended",
         "Why", "Filed By The Team As"],
        [18, 12, 12, 44, 44, 60, 22])
    sheets.write_rows(conversations, [
        [m.conversation_id, m.scenario_id or "— none —", m.confidence, m.intent, m.ending,
         m.reason, m.declared_group]
        for m in mappings])

    if report.groups:
        grouping = sheets.add_sheet(
            workbook, "Their Grouping",
            ["Their Label", "Conversations", "Agrees?", "Maps To", "Verdict"],
            [26, 14, 10, 30, 70])
        sheets.write_rows(grouping, [
            [g.group, g.total, "Yes" if g.agrees else "No",
             ", ".join(f"{k} ({v})" for k, v in sorted(g.scenario_counts.items())),
             g.verdict]
            for g in report.groups])

    workbook.save(path)
