"""Row-scoped gaps in the declared intake, and revising the declaration to fill them.

A gap is addressed to a specific decision, state, capability, tool or persona rather than to
the evidence as a whole -- see metric/core/gaps.py for why that distinction matters.
These tests pin two things: that the gap detector finds what it claims to (and only what it claims
to -- a well-formed intake should raise nothing), and that revising an intake changes only what
new information touches rather than silently redrafting the rest.
"""
import json
import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook

from metric.phases.intake.intake.gaps import find_gaps
from metric.domain.intake import read_intake, read_review_notes, write_template
from metric.domain.models import (Capability, Decision, IntakeData, Persona, State,
                                            Tool)
from metric.phases.intake.intake.drafting import revise_intake, write_drafted_intake, DraftedIntake
from metric.pipeline import revise_intake_workbook

_WELL_FORMED = IntakeData(
    use_case={"Use case name": "Disputes", "Business objective": "Resolve card disputes",
             "Agent type": "Chatbot", "Success criteria": "Dispute filed correctly"},
    personas=[Persona("P1", "Cardholder", ["wants their dispute filed correctly"], True),
             Persona("P-ADV", "Attacker", ["trying to file a dispute on someone else's card"],
                     False)],
    capabilities=[Capability("CAP-01", "Identify", "Gating")],
    decisions=[Decision("DEC-01", "Identity check", "CAP-01", "credentials", ["Pass", "Fail"])],
    states=[State("S-00", "Start", "Session begins", ["DEC-01"], False),
           State("S-01", "DEC-01=Pass", "Identified", [], True, "Happy path"),
           State("S-02", "DEC-01=Fail", "Rejected", [], True, "Termination")],
    tools=[Tool("Identity service", "CAP-01", True)],
)


class TestFindGaps(unittest.TestCase):
    def test_a_well_formed_intake_raises_nothing(self):
        self.assertEqual(find_gaps(_WELL_FORMED), [])

    def test_a_decision_with_one_outcome_is_a_gap(self):
        intake = IntakeData(
            use_case=_WELL_FORMED.use_case, personas=_WELL_FORMED.personas,
            capabilities=_WELL_FORMED.capabilities,
            decisions=[Decision("DEC-01", "Identity check", "CAP-01", "", ["Pass"])],
            states=_WELL_FORMED.states, tools=_WELL_FORMED.tools)
        gaps = find_gaps(intake)
        self.assertTrue(any(g.kind == "decision" and g.target_id == "DEC-01" for g in gaps))

    def test_an_out_of_scope_decision_is_never_a_gap(self):
        intake = IntakeData(
            use_case=_WELL_FORMED.use_case, personas=_WELL_FORMED.personas,
            capabilities=_WELL_FORMED.capabilities,
            decisions=[Decision("DEC-01", "Identity check", "CAP-01", "", ["Pass"],
                                out_of_scope=True)],
            states=_WELL_FORMED.states, tools=_WELL_FORMED.tools)
        gaps = find_gaps(intake)
        self.assertFalse(any(g.target_id == "DEC-01" for g in gaps))

    def test_a_capability_with_no_type_is_a_gap(self):
        intake = IntakeData(
            use_case=_WELL_FORMED.use_case, personas=_WELL_FORMED.personas,
            capabilities=[Capability("CAP-01", "Identify", "")],
            decisions=_WELL_FORMED.decisions, states=_WELL_FORMED.states, tools=[])
        gaps = find_gaps(intake)
        self.assertTrue(any(g.kind == "capability" and g.field == "type" for g in gaps))

    def test_a_terminal_state_with_no_outcome_type_is_a_gap(self):
        intake = IntakeData(
            use_case=_WELL_FORMED.use_case, personas=_WELL_FORMED.personas,
            capabilities=_WELL_FORMED.capabilities, decisions=_WELL_FORMED.decisions,
            states=[State("S-00", "Start", "Session begins", ["DEC-01"], False),
                   State("S-01", "DEC-01=Pass", "Identified", [], True, "")],
            tools=[])
        gaps = find_gaps(intake)
        self.assertTrue(any(g.kind == "state" and g.field == "outcome_type" for g in gaps))

    def test_an_unreachable_state_is_a_gap(self):
        intake = IntakeData(
            use_case=_WELL_FORMED.use_case, personas=_WELL_FORMED.personas,
            capabilities=_WELL_FORMED.capabilities, decisions=_WELL_FORMED.decisions,
            states=list(_WELL_FORMED.states) + [
                State("S-99", "DEC-99=Nothing", "Orphaned", [], True, "Termination")],
            tools=_WELL_FORMED.tools)
        gaps = find_gaps(intake)
        self.assertTrue(any(g.target_id == "S-99" and g.field == "reached_via" for g in gaps))

    def test_a_thin_persona_is_a_gap(self):
        intake = IntakeData(
            use_case=_WELL_FORMED.use_case,
            personas=[Persona("P1", "Cardholder", [], True), Persona("P2", "Vague", [], False)],
            capabilities=_WELL_FORMED.capabilities, decisions=_WELL_FORMED.decisions,
            states=_WELL_FORMED.states, tools=_WELL_FORMED.tools)
        gaps = find_gaps(intake)
        self.assertTrue(any(g.kind == "persona" and g.target_id == "P2" for g in gaps))

    def test_no_start_state_is_cross_cutting(self):
        intake = IntakeData(
            use_case=_WELL_FORMED.use_case, personas=_WELL_FORMED.personas,
            capabilities=_WELL_FORMED.capabilities, decisions=_WELL_FORMED.decisions,
            states=[State("S-00", "", "Session begins", ["DEC-01"], False)] + list(
                _WELL_FORMED.states[1:]),
            tools=_WELL_FORMED.tools)
        gaps = find_gaps(intake)
        self.assertTrue(any(g.kind == "" for g in gaps))


class TestReviewNotesRoundTrip(unittest.TestCase):
    def test_review_notes_survive_a_write_and_a_read(self):
        path = Path(tempfile.mktemp(suffix=".xlsx"))
        draft = DraftedIntake({
            "use_case": {}, "personas": [{"id": "P1", "name": "P", "applies_to": "x",
                                         "is_default": True}],
            "capabilities": [], "decisions": [], "states": [], "tools": [],
            "confidence": {}, "review_notes": [{"field": "DEC-03 outcomes",
                                                "note": "Inferred from context, not stated."}]})
        write_drafted_intake(path, draft)
        notes = read_review_notes(str(path))
        self.assertEqual(notes, [{"field": "DEC-03 outcomes",
                                  "note": "Inferred from context, not stated."}])
        path.unlink()

    def test_a_workbook_with_no_review_sheet_returns_nothing(self):
        path = Path(tempfile.mktemp(suffix=".xlsx"))
        write_template(str(path))
        self.assertEqual(read_review_notes(str(path)), [])
        path.unlink()


class TestRevision(unittest.TestCase):
    def _reply(self, payload):
        return lambda system, user, **kwargs: json.dumps(payload)

    def test_revision_is_told_what_is_currently_declared(self):
        seen = {}

        def complete(system, user, **kwargs):
            seen["user"] = user
            return json.dumps({"use_case": {}, "personas": [], "capabilities": [],
                               "decisions": [], "states": [], "tools": [], "confidence": {},
                               "review_notes": []})

        revise_intake("new context", "DEC-01: Identity check -- Pass / Fail", complete=complete)
        self.assertIn("WHAT IS CURRENTLY DECLARED", seen["user"])
        self.assertIn("DEC-01", seen["user"])
        self.assertIn("new context", seen["user"])

    def test_revising_a_workbook_folds_in_an_answered_gap(self):
        path = Path(tempfile.mktemp(suffix=".xlsx"))
        write_template(str(path))
        book = load_workbook(path)
        book["L1 Use Case"]["B2"] = "Disputes"
        book["Personas"].append(["P1", "Cardholder", "Happy path", "Y"])
        book["L2 Capabilities"].append(["CAP-01", "Identify", "Gating"])
        book["L3 Decisions"].append(
            ["DEC-01", "Identity check", "CAP-01", "credentials", "Pass", "User", 1, "", "No"])
        book["L4 States"].append(["S-00", "Start", "Session begins", "DEC-01", "No", ""])
        book.save(path)

        before = read_intake(str(path))
        self.assertTrue(any(g.kind == "decision" and g.target_id == "DEC-01"
                            for g in find_gaps(before)))

        revise_intake_workbook(str(path), str(path), notes=[
            "The other outcome of DEC-01 is Fail, when credentials do not match."],
            complete=self._reply({
                "use_case": {"name": "Disputes"},
                "personas": [{"id": "P1", "name": "Cardholder", "applies_to": "Happy path",
                             "is_default": True}],
                "capabilities": [{"id": "CAP-01", "name": "Identify", "type": "Gating"}],
                "decisions": [{"id": "DEC-01", "name": "Identity check",
                              "capability_id": "CAP-01", "inputs": "credentials",
                              "outcomes": ["Pass", "Fail"], "input_source": "User",
                              "max_attempts": 1, "outcome_condition": ""}],
                "states": [{"id": "S-00", "reached_via": "Start",
                           "description": "Session begins", "next_decisions": ["DEC-01"],
                           "is_terminal": False, "outcome_type": ""}],
                "tools": [], "confidence": {}, "review_notes": []}))

        after = read_intake(str(path))
        self.assertEqual(set(after.decisions[0].variants), {"Pass", "Fail"})
        path.unlink()


if __name__ == "__main__":
    unittest.main()
