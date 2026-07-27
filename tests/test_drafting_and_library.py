"""Drafting an intake, and reading a scenario library out of whatever shape it arrived in.

Both exist to remove a wall of manual work, and both fail in the same direction if they are timid:
a draft that fills nothing is a blank form with extra steps, and a reader that accepts one exact
layout reports that a team tested nothing when in fact their spreadsheet had different headings.
"""
import json
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook, load_workbook

from scenario_generator.core.intake import read_intake
from scenario_generator.ingest import (UnreadableLibrary, draft_intake, read_owner_library,
                                       write_drafted_intake)
from scenario_generator.pipeline import build_scenarios

_DRAFT = {
    "use_case": {"name": "Charge Verification Assistant", "objective": "Resolve disputes",
                 "agent_type": "Conversational", "channel": "Chat",
                 "handoff_triggers": "Locked session", "safety_requirements": "Verify first",
                 "success_criteria": "Dispute filed"},
    "personas": [{"id": "P1", "name": "Cardmember", "applies_to": "Happy path",
                  "is_default": True}],
    "capabilities": [{"id": "CAP-01", "name": "Identity verification", "type": "Gating"},
                     {"id": "CAP-02", "name": "Lookup", "type": "Lookup"}],
    "decisions": [
        {"id": "DEC-01", "name": "Identity check", "capability_id": "CAP-01",
         "inputs": "credentials", "outcomes": ["Pass", "Fail"], "input_source": "User",
         "max_attempts": 3, "outcome_condition": "three attempts"},
        {"id": "DEC-02", "name": "Lookup", "capability_id": "CAP-02", "inputs": "reference",
         "outcomes": ["Found", "Not found"], "input_source": "Tool", "max_attempts": 1,
         "outcome_condition": ""}],
    "states": [
        {"id": "S-00", "reached_via": "Start", "description": "Session begins",
         "next_decisions": ["DEC-01"], "is_terminal": False, "outcome_type": ""},
        {"id": "S-01", "reached_via": "DEC-01=Pass", "description": "Verified",
         "next_decisions": ["DEC-02"], "is_terminal": False, "outcome_type": ""},
        {"id": "S-02", "reached_via": "DEC-01=Fail", "description": "Locked out",
         "next_decisions": [], "is_terminal": True, "outcome_type": "Termination"},
        {"id": "S-03", "reached_via": "DEC-02=Found", "description": "Located",
         "next_decisions": [], "is_terminal": True, "outcome_type": "Happy path"}],
    "tools": [{"name": "Identity service", "capability_id": "CAP-01", "changes_state": False}],
    "confidence": {"use_case": "High", "decisions": "Medium", "states": "Medium",
                   "capabilities": "High", "personas": "Low", "tools": "Low"},
    "review_notes": [{"field": "personas", "note": "Only one persona was described."}],
}


def _drafted(payload=None) -> Path:
    draft = draft_intake("context", complete=lambda s, u, **k: json.dumps(payload or _DRAFT))
    path = Path(tempfile.mkdtemp()) / "drafted.xlsx"
    write_drafted_intake(path, draft)
    return path


class TestDraftedIntake(unittest.TestCase):
    def test_the_draft_is_a_workbook_the_pipeline_reads_unchanged(self):
        intake = read_intake(str(_drafted()))
        self.assertEqual(intake.name, "Charge Verification Assistant")
        self.assertEqual(len(intake.decisions), 2)
        self.assertEqual(len(intake.states), 4)

    def test_the_draft_produces_a_benchmark_without_being_edited(self):
        """The point of drafting: a person corrects it, but it works before they do."""
        scenarios = build_scenarios(read_intake(str(_drafted())), with_probes=True)
        self.assertTrue(scenarios)

    def test_retry_bounds_and_input_sources_survive(self):
        intake = read_intake(str(_drafted()))
        identity = next(d for d in intake.decisions if d.id == "DEC-01")
        lookup = next(d for d in intake.decisions if d.id == "DEC-02")
        self.assertEqual(identity.max_attempts, 3)
        self.assertEqual(identity.input_source, "User")
        self.assertEqual(lookup.input_source, "Tool")

    def test_where_the_draft_is_weak_is_written_into_the_workbook(self):
        book = load_workbook(_drafted())
        self.assertIn("Review This", book.sheetnames)
        text = " ".join(str(c.value) for row in book["Review This"].iter_rows()
                        for c in row if c.value)
        self.assertIn("Only one persona", text)
        self.assertIn("Low", text)

    def test_a_type_outside_the_vocabulary_is_dropped_rather_than_kept(self):
        """An invented capability type silently changes which probes apply."""
        payload = json.loads(json.dumps(_DRAFT))
        payload["capabilities"][0]["type"] = "Conversational"
        intake = read_intake(str(_drafted(payload)))
        self.assertEqual(next(c for c in intake.capabilities if c.id == "CAP-01").type, "")

    def test_a_draft_with_no_default_persona_still_reads(self):
        """read_intake indexes the default persona directly, so one must always exist."""
        payload = json.loads(json.dumps(_DRAFT))
        payload["personas"][0]["is_default"] = False
        intake = read_intake(str(_drafted(payload)))
        self.assertTrue(any(p.is_default for p in intake.personas))


class TestOwnerLibrary(unittest.TestCase):
    """Submissions arrive in whatever shape the owner already had."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def test_a_workbook_with_a_title_row_and_a_cover_sheet(self):
        book = Workbook()
        book.active.title = "Glossary"
        book.active.append(["Term", "Meaning"])
        sheet = book.create_sheet("UAT Cases")
        sheet.append(["Charge Verification — UAT pack v3"])
        sheet.append([])
        sheet.append(["Test Case ID", "Test Scenario", "Expected Route"])
        sheet.append(["TC-001", "Cardmember passes identity verification first time.", ""])
        sheet.append(["TC-002", "Cardmember fails verification three times.", ""])
        path = self.dir / "uat.xlsx"
        book.save(path)

        found, how = read_owner_library(path)
        self.assertEqual(len(found), 2)
        self.assertEqual(found[0].id, "TC-001")
        self.assertIn("UAT Cases", how)

    def test_a_semicolon_csv_with_unfamiliar_headings(self):
        path = self.dir / "cases.csv"
        path.write_text("S.No;Narrative;Notes\n"
                        "1;Member disputes a charge they do not recognise.;x\n"
                        "2;Member asks for legal advice and is handed over.;y\n",
                        encoding="utf-8")
        found, _ = read_owner_library(path)
        self.assertEqual(len(found), 2)
        self.assertIn("disputes a charge", found[0].description)

    def test_a_numbered_list_with_no_table_at_all(self):
        path = self.dir / "list.md"
        path.write_text("# Our testing\n\n"
                        "1. Happy path where the member is verified and the dispute is filed.\n"
                        "2. Member abandons the conversation midway through filing.\n",
                        encoding="utf-8")
        found, how = read_owner_library(path)
        self.assertEqual(len(found), 2)
        self.assertIn("numbered", how)

    def test_the_widest_column_is_taken_where_no_heading_is_recognised(self):
        book = Workbook()
        sheet = book.active
        sheet.append(["Col A", "Col B"])
        sheet.append(["x", "The member is verified and then files a dispute successfully."])
        path = self.dir / "odd.xlsx"
        book.save(path)
        found, how = read_owner_library(path)
        self.assertEqual(len(found), 1)
        self.assertIn("widest column", how)

    def test_a_file_that_is_not_a_scenario_list_is_refused_with_a_reason(self):
        book = Workbook()
        book.active.append(["Setting", "Value"])
        book.active.append(["Region", "US"])
        path = self.dir / "config.xlsx"
        book.save(path)
        with self.assertRaises(UnreadableLibrary):
            read_owner_library(path)

    def test_an_unsupported_format_names_what_is_supported(self):
        path = self.dir / "thing.zip"
        path.write_text("x", encoding="utf-8")
        with self.assertRaises(UnreadableLibrary) as caught:
            read_owner_library(path)
        self.assertIn(".xlsx", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
