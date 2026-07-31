"""Editing a declared intake in place, from the interface.

Three narrow writes exist against a workbook that is otherwise only ever replaced by uploading a
corrected copy: marking a decision out of scope, correcting a state's route, and attaching a
decision to the state that should lead to it. Each is a single cell a person is expected to change
while looking at the graph rather than while looking at a spreadsheet.

What matters about all three is the same thing: they amend one cell and leave the rest of the
workbook -- every other sheet, and anything put there by hand -- exactly as it was. A tool that
rewrites someone's workbook wholesale is a tool they stop trusting with their own edits.
"""
import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook

from scenario_generator.core.intake import (attach_decision_to_state, read_intake,
                                            set_decision_scope, set_state_reached_via,
                                            write_template)


def _workbook() -> Path:
    path = Path(tempfile.mkdtemp()) / "intake.xlsx"
    write_template(str(path))
    book = load_workbook(path)
    book["L1 Use Case"]["B2"] = "Disputes"
    book["Personas"].append(["P1", "Cardholder", "Happy path", "Y"])
    book["L2 Capabilities"].append(["CAP-01", "Identify", "Gating"])
    book["L3 Decisions"].append(
        ["DEC-01", "Identity check", "CAP-01", "credentials", "Pass / Fail", "User", 1, "", "No"])
    book["L3 Decisions"].append(
        ["DEC-02", "Dispute check", "CAP-01", "claim", "Valid / Invalid", "User", 1, "", "No"])
    book["L4 States"].append(["S-00", "Start", "Session begins", "DEC-01", "No", ""])
    book["L4 States"].append(["S-01", "DEC-01=Pass", "Identified", "", "No", ""])
    book.save(path)
    return path


class TestAttachingADecisionToAState(unittest.TestCase):
    def test_the_decision_joins_the_state_next_steps(self):
        path = _workbook()
        self.assertTrue(attach_decision_to_state(str(path), "S-01", "DEC-02"))
        state = next(s for s in read_intake(str(path)).states if s.id == "S-01")
        self.assertIn("DEC-02", state.next_decisions)

    def test_a_decision_already_named_is_not_repeated(self):
        path = _workbook()
        state = next(s for s in read_intake(str(path)).states if s.id == "S-00")
        self.assertEqual(state.next_decisions, ["DEC-01"])
        self.assertFalse(attach_decision_to_state(str(path), "S-00", "DEC-01"))
        state = next(s for s in read_intake(str(path)).states if s.id == "S-00")
        self.assertEqual(state.next_decisions, ["DEC-01"])

    def test_an_unknown_state_reports_failure_rather_than_inventing_a_row(self):
        path = _workbook()
        self.assertFalse(attach_decision_to_state(str(path), "S-99", "DEC-02"))
        self.assertEqual(len(read_intake(str(path)).states), 2)

    def test_everything_else_in_the_workbook_survives(self):
        """The workbook is theirs. One cell changes; nothing else may."""
        path = _workbook()
        attach_decision_to_state(str(path), "S-01", "DEC-02")

        intake = read_intake(str(path))
        self.assertEqual(intake.use_case["Use case name"], "Disputes")
        self.assertEqual([d.id for d in intake.decisions], ["DEC-01", "DEC-02"])
        self.assertEqual([p.id for p in intake.personas], ["P1"])
        self.assertEqual([c.id for c in intake.capabilities], ["CAP-01"])
        first = next(s for s in intake.states if s.id == "S-00")
        self.assertEqual(first.next_decisions, ["DEC-01"])


class TestTheOtherTwoNarrowWrites(unittest.TestCase):
    def test_scope_is_flipped_without_disturbing_the_row(self):
        path = _workbook()
        self.assertTrue(set_decision_scope(str(path), "DEC-02", True))
        decision = next(d for d in read_intake(str(path)).decisions if d.id == "DEC-02")
        self.assertTrue(decision.out_of_scope)
        self.assertEqual(decision.name, "Dispute check")
        self.assertEqual(decision.variants, ["Valid", "Invalid"])

    def test_scope_can_be_taken_back_off(self):
        path = _workbook()
        set_decision_scope(str(path), "DEC-02", True)
        set_decision_scope(str(path), "DEC-02", False)
        decision = next(d for d in read_intake(str(path)).decisions if d.id == "DEC-02")
        self.assertFalse(decision.out_of_scope)

    def test_a_state_route_is_corrected_in_place(self):
        path = _workbook()
        self.assertTrue(set_state_reached_via(str(path), "S-01", "DEC-01=Fail"))
        state = next(s for s in read_intake(str(path)).states if s.id == "S-01")
        self.assertEqual(state.reached_via, "DEC-01=Fail")
        self.assertEqual(state.description, "Identified")

    def test_an_unknown_row_reports_failure(self):
        path = _workbook()
        self.assertFalse(set_decision_scope(str(path), "DEC-99", True))
        self.assertFalse(set_state_reached_via(str(path), "S-99", "DEC-01=Pass"))


if __name__ == "__main__":
    unittest.main()
