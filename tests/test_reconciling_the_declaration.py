"""The last look: the declaration read back whole, after its named gaps are filled.

Two passes work question by question -- draft this from the documents, fill in these named gaps
-- and both can succeed on every row while leaving something that does not hang together as one
agent. The cases here are the ones nothing before this pass can see, because every row involved
is individually complete and the audit has nothing to say.
"""
import json
import tempfile
import unittest
from pathlib import Path

from metric.phases.intake.intake.gaps import find_gaps
from metric.domain.intake import read_intake
from metric.domain.models import Decision, IntakeData, State
from metric.pipeline import draft_intake_workbook, structural_problems

_USE_CASE = {"name": "Disputes", "objective": "Handle a disputed charge", "agent_type": "Chat",
             "channel": "Web", "handoff_triggers": "The cardmember asks for a person",
             "safety_requirements": "No account detail before identification",
             "success_criteria": "The dispute is filed or the cardmember is told why not"}

_PERSONAS = [{"id": "P-01", "name": "Cardmember with a disputed charge",
              "applies_to": "Wants a charge reversed", "is_default": True},
             {"id": "P-ADV", "name": "Caller trying to reach another account",
              "applies_to": "Tries to be identified as somebody else", "is_default": False}]

# Structurally complete: every decision is routed into, every outcome lands on a state, every
# state is reached, nothing is missing a description or an outcome type. find_gaps has nothing to
# say about it -- and S-04 is an escalation the use case's own hand-off trigger does not account
# for, with a Happy path outcome type on a route that failed. That is what the last look is for.
_SOUND_BUT_INCOHERENT = {
    "use_case": _USE_CASE, "personas": _PERSONAS,
    "capabilities": [{"id": "CAP-01", "name": "Identification", "type": "Gating"}],
    "decisions": [
        {"id": "DEC-01", "name": "Identify the cardmember", "capability_id": "CAP-01",
         "inputs": "Card details", "outcomes": ["Identified", "Not identified"],
         "input_source": "User", "max_attempts": 1, "outcome_condition": ""},
        {"id": "DEC-02", "name": "Verify the address", "capability_id": "CAP-01",
         "inputs": "Postcode", "outcomes": ["Matches", "Does not match"],
         "input_source": "Tool", "max_attempts": 3, "outcome_condition": ""}],
    "states": [
        {"id": "S-00", "reached_via": "Start", "description": "The chat opens",
         "next_decisions": ["DEC-01"], "is_terminal": False, "outcome_type": ""},
        {"id": "S-01", "reached_via": "DEC-01=Identified",
         "description": "Identified; the agent is checking the address on file",
         "next_decisions": ["DEC-02"], "is_terminal": False, "outcome_type": ""},
        {"id": "S-02", "reached_via": "DEC-01=Not identified",
         "description": "Not identified; the agent has ended the chat",
         "next_decisions": [], "is_terminal": True, "outcome_type": "Termination"},
        {"id": "S-03", "reached_via": "DEC-02=Matches",
         "description": "Address matches; the dispute is filed",
         "next_decisions": [], "is_terminal": True, "outcome_type": "Happy path"},
        {"id": "S-04", "reached_via": "DEC-02=Does not match",
         "description": "Address does not match; passed to a fraud adviser",
         "next_decisions": [], "is_terminal": True, "outcome_type": "Happy path"}],
    "tools": []}


def _reconciled() -> dict:
    """The same declaration with the two incoherences corrected.

    S-04 is an escalation, not a happy path; and DEC-02's retry limit of three is unreachable,
    because no state routes back into it. Neither is a gap -- both rows are complete.
    """
    fixed = json.loads(json.dumps(_SOUND_BUT_INCOHERENT))
    for state in fixed["states"]:
        if state["id"] == "S-04":
            state["outcome_type"] = "Escalation"
    for decision in fixed["decisions"]:
        if decision["id"] == "DEC-02":
            decision["max_attempts"] = 1
    return fixed


class TestADecisionNothingRoutesInto(unittest.TestCase):
    """The defect that puts a decision in red below the drawing instead of in the graph."""

    def _intake(self, offers_dec_02: bool) -> IntakeData:
        return IntakeData(
            use_case=dict(_USE_CASE), personas=[], capabilities=[],
            decisions=[Decision(id="DEC-01", name="Identify", trigger_capability="CAP-01",
                                inputs="", variants=["Identified", "Not identified"]),
                       Decision(id="DEC-02", name="Verify", trigger_capability="CAP-01",
                                inputs="", variants=["Matches", "Does not match"])],
            states=[State(id="S-00", reached_via="Start", description="Opens",
                          next_decisions=["DEC-01"], is_terminal=False),
                    State(id="S-01", reached_via="DEC-01=Identified", description="Identified",
                          next_decisions=["DEC-02"] if offers_dec_02 else [],
                          is_terminal=False),
                    State(id="S-02", reached_via="DEC-01=Not identified", description="No",
                          next_decisions=[], is_terminal=True, outcome_type="Termination"),
                    State(id="S-03", reached_via="DEC-02=Matches", description="Yes",
                          next_decisions=[], is_terminal=True, outcome_type="Happy path"),
                    State(id="S-04", reached_via="DEC-02=Does not match", description="No",
                          next_decisions=[], is_terminal=True, outcome_type="Escalation")],
            tools=[])

    def test_it_is_asked_about(self):
        gaps = [g for g in find_gaps(self._intake(offers_dec_02=False))
                if g.target_id == "DEC-02" and g.field == "reached_from"]
        self.assertEqual(len(gaps), 1, "nothing asks how DEC-02 is arrived at")
        self.assertIn("Valid Next Decisions", gaps[0].question)

    def test_a_decision_something_routes_into_is_not_asked_about(self):
        gaps = [g for g in find_gaps(self._intake(offers_dec_02=True))
                if g.field == "reached_from"]
        self.assertEqual(gaps, [])


class TestTheDeclarationIsReadBackWhole(unittest.TestCase):
    def setUp(self):
        self.work = Path(tempfile.mkdtemp())
        self.context = self.work / "run_context.md"
        self.context.write_text("Identification is followed by an address check. A mismatch goes "
                                "to a fraud adviser.\n", encoding="utf-8")
        self.output = self.work / "intake.xlsx"
        self.prompts = []

    def _complete(self, reconciled=None):
        def complete(system, user, **kwargs):
            self.prompts.append(user)
            if "WHAT TO LOOK FOR" in user:
                return json.dumps(reconciled if reconciled is not None else _reconciled())
            return json.dumps(_SOUND_BUT_INCOHERENT)
        return complete

    def _run(self, reconciled=None):
        draft_intake_workbook(str(self.context), str(self.output),
                              complete=self._complete(reconciled))
        return read_intake(str(self.output))

    def _last_look(self):
        return [p for p in self.prompts if "WHAT TO LOOK FOR" in p]

    def test_the_draft_it_works_on_has_nothing_the_audit_can_see(self):
        """Guards the premise of every test below: the repair has no reason to fire here."""
        self._run()
        self.assertEqual([p for p in self.prompts if "WHAT IS MISSING" in p], [])

    def test_it_runs_anyway(self):
        """The whole point -- a declaration with no gaps can still be incoherent."""
        self._run()
        self.assertEqual(len(self._last_look()), 1)

    def test_it_carries_the_routes_it_would_be_correcting(self):
        self._run()
        last = self._last_look()[0]
        self.assertIn("WHAT THIS DECLARATION CURRENTLY ENUMERATES TO", last)
        self.assertIn("Distinct routes from start to finish", last)
        self.assertIn("A mismatch goes to a fraud adviser", last)
        self.assertIn("HOW THE GRAPH IS WIRED", last)

    def test_the_corrections_are_what_land_on_disk(self):
        intake = self._run()
        ending = [s for s in intake.states if s.id == "S-04"][0]
        self.assertEqual(ending.outcome_type, "Escalation")
        self.assertEqual([d for d in intake.decisions if d.id == "DEC-02"][0].max_attempts, 1)

    def test_a_reconciliation_that_drops_a_row_has_it_carried_forward(self):
        lossy = _reconciled()
        lossy["decisions"] = [d for d in lossy["decisions"] if d["id"] != "DEC-02"]
        intake = self._run(lossy)
        self.assertIn("DEC-02", [d.id for d in intake.decisions],
                      "a decision the last look dropped was not put back")

    def test_a_reconciliation_that_leaves_more_gaps_is_thrown_away(self):
        worse = _reconciled()
        worse["decisions"] = worse["decisions"] + [
            {"id": "DEC-09", "name": "Invented", "capability_id": "", "inputs": "",
             "outcomes": [], "input_source": "User", "max_attempts": 1, "outcome_condition": ""}]
        intake = self._run(worse)
        self.assertNotIn("DEC-09", [d.id for d in intake.decisions])
        self.assertEqual(structural_problems(intake), [])

    def test_a_reconciliation_that_fails_outright_leaves_the_declaration_alone(self):
        def complete(system, user, **kwargs):
            if "WHAT TO LOOK FOR" in user:
                raise RuntimeError("gateway down")
            return json.dumps(_SOUND_BUT_INCOHERENT)

        draft_intake_workbook(str(self.context), str(self.output), complete=complete)
        intake = read_intake(str(self.output))
        self.assertEqual(len(intake.decisions), 2)
        self.assertEqual(len(intake.states), 5)

    def test_an_empty_reconciliation_leaves_the_declaration_alone(self):
        empty = {"use_case": _USE_CASE, "personas": [], "capabilities": [], "decisions": [],
                 "states": [], "tools": []}
        intake = self._run(empty)
        self.assertEqual(len(intake.decisions), 2)

    def test_it_can_be_switched_off(self):
        draft_intake_workbook(str(self.context), str(self.output),
                              complete=self._complete(), reconcile=False)
        self.assertEqual(self._last_look(), [])

    def test_no_scratch_workbook_is_left_behind(self):
        self._run()
        self.assertEqual(list(self.work.glob("*.candidate.xlsx")), [])


if __name__ == "__main__":
    unittest.main()
