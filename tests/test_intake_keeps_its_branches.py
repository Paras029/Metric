"""The second look at a draft can improve the graph and must never shrink it.

The intake is the boundary of what can be tested: a branch that is not declared is a branch
nobody ever runs a conversation down. So the failure mode that matters here is not a draft that
is wrong, which a person corrects in ten seconds -- it is a draft that was right and then quietly
got smaller, because nothing on any later screen says a route used to exist.

Two mechanisms produced exactly that, and both were intermittent, which is what made them hard to
see: whether a re-draft happened to reconstruct every edge correctly was luck.

*The repair call could not see the wiring.* ``describe_graph`` rendered states as a description
and a terminal flag and nothing else -- no ``reached_via``, no ``next_decisions``. The one call
whose entire job is "where does this outcome lead" was shown a bag of states with no edges in it
and had to re-derive every edge from prose. A re-derivation one branch short parses exactly as
well as the declaration it replaced.

*And a repair that lost ground was kept.* The reply was written to disk before anything checked
whether it had helped, and the only trace of a bad one was a log line reporting a negative number
of gaps filled.
"""
import json
import tempfile
import unittest
from pathlib import Path

from scenario_generator.core import read_intake
from scenario_generator.core.models import Decision, IntakeData, Persona, State, Tool
from scenario_generator.ingest.drafting import carry_forward
from scenario_generator.llm.context import describe_enumeration, describe_graph
from scenario_generator.pipeline import build_scenario_space, draft_intake_workbook

_BRANCHED = {
    "use_case": {"name": "Disputes", "objective": "Resolve disputes", "agent_type": "Chatbot",
                 "channel": "App", "handoff_triggers": "", "safety_requirements": "",
                 "success_criteria": "The dispute is filed"},
    "personas": [{"id": "P1", "name": "Cardmember", "applies_to": "Wants the charge reversed",
                  "is_default": True},
                 {"id": "P-ADV", "name": "Adversarial user",
                  "applies_to": "Trying to make the agent act outside its remit",
                  "is_default": False}],
    "capabilities": [{"id": "CAP-01", "name": "Identity", "type": "Gating"},
                     {"id": "CAP-02", "name": "Dispute filing", "type": "Transactional"}],
    "decisions": [
        {"id": "DEC-01", "name": "Identity check", "capability_id": "CAP-01", "inputs": "SSN",
         "outcomes": ["Pass", "Fail"], "input_source": "User", "max_attempts": 2,
         "outcome_condition": "last four match"},
        {"id": "DEC-02", "name": "Dispute eligible", "capability_id": "CAP-02", "inputs": "age",
         "outcomes": ["Eligible", "Too old"], "input_source": "System-Context",
         "max_attempts": 1, "outcome_condition": "within 60 days"},
    ],
    "states": [
        {"id": "S-00", "reached_via": "Start", "description": "The chat opens",
         "next_decisions": ["DEC-01"], "is_terminal": False, "outcome_type": ""},
        {"id": "S-01", "reached_via": "DEC-01=Pass", "description": "Verified",
         "next_decisions": ["DEC-02"], "is_terminal": False, "outcome_type": ""},
        {"id": "S-02", "reached_via": "DEC-01=Fail", "description": "Locked out",
         "next_decisions": [], "is_terminal": True, "outcome_type": "Termination"},
        {"id": "S-03", "reached_via": "DEC-02=Eligible", "description": "Dispute filed",
         "next_decisions": [], "is_terminal": True, "outcome_type": "Happy path"},
        {"id": "S-04", "reached_via": "DEC-02=Too old", "description": "Refused, too old",
         "next_decisions": [], "is_terminal": True, "outcome_type": "Fallback"},
    ],
    "tools": [{"name": "Identity service", "capability_id": "CAP-01", "changes_state": False}],
    "confidence": {k: "High" for k in
                   ("use_case", "personas", "capabilities", "decisions", "states", "tools")},
    "review_notes": [],
}


def _copy(**changes):
    data = json.loads(json.dumps(_BRANCHED))
    data.update(changes)
    return data


def _intake_of(data):
    """The declaration as IntakeData, without going through a workbook."""
    return IntakeData(
        use_case={"Use case name": data["use_case"]["name"],
                  "Business objective": data["use_case"]["objective"]},
        personas=[Persona(p["id"], p["name"], [p["applies_to"]], p["is_default"])
                  for p in data["personas"]],
        capabilities=[],
        decisions=[Decision(d["id"], d["name"], d["capability_id"], "", list(d["outcomes"]))
                   for d in data["decisions"]],
        states=[State(s["id"], s["reached_via"], s["description"], list(s["next_decisions"]),
                      s["is_terminal"], s["outcome_type"])
                for s in data["states"]],
        tools=[Tool(t["name"], t["capability_id"], t["changes_state"]) for t in data["tools"]],
    )


class TestTheRepairCallCanSeeTheWiring(unittest.TestCase):
    """A call asked where an outcome leads has to be shown where the other outcomes lead."""

    def test_every_state_carries_what_reaches_it(self):
        rendered = describe_graph(_intake_of(_BRANCHED))
        for state in _BRANCHED["states"]:
            self.assertIn(f"reached via: {state['reached_via']}", rendered, state["id"])

    def test_every_state_carries_what_follows_it(self):
        rendered = describe_graph(_intake_of(_BRANCHED))
        self.assertIn("S-00: The chat opens | reached via: Start | next: DEC-01", rendered)

    def test_a_state_nothing_reaches_says_so_rather_than_saying_nothing(self):
        """A blank field renders as an absence somebody has to notice; this renders as a fact."""
        orphan = _copy(states=[dict(s, reached_via="") if s["id"] == "S-02" else s
                               for s in _BRANCHED["states"]])
        self.assertIn("reached via: NOTHING DECLARED", describe_graph(_intake_of(orphan)))


class TestTheConsequenceOfTheWiringIsStated(unittest.TestCase):
    """The routes are what the exercise tests, and nothing else in the repair prompt showed them."""

    def test_it_counts_the_routes_the_declaration_produces(self):
        self.assertIn("Distinct routes from start to finish: 3",
                      describe_enumeration(_intake_of(_BRANCHED)))

    def test_it_names_the_outcomes_that_lead_nowhere(self):
        hole = _copy(states=[s for s in _BRANCHED["states"] if s["id"] != "S-04"])
        self.assertIn("DEC-02=Too old", describe_enumeration(_intake_of(hole)))

    def test_a_genuinely_linear_agent_is_reported_rather_than_faulted(self):
        """One route is a correct answer about an agent that does one thing. Calling that a fault
        would be wrong about a real agent and would teach the reader to ignore the next one."""
        linear = _copy(
            decisions=[dict(_BRANCHED["decisions"][0], outcomes=["Pass"])],
            states=[s for s in _BRANCHED["states"] if s["id"] in ("S-00", "S-01")]
                   + [{"id": "S-01", "reached_via": "DEC-01=Pass", "description": "Verified",
                       "next_decisions": [], "is_terminal": True, "outcome_type": "Happy path"}])
        text = describe_enumeration(_intake_of(linear))
        self.assertIn("Distinct routes from start to finish: 1", text)
        for shouting in ("problem", "too few", "should", "wrong", "fault"):
            self.assertNotIn(shouting, text.lower())


class TestNothingDeclaredIsLostInARepair(unittest.TestCase):
    def test_a_dropped_decision_is_carried_forward(self):
        repaired = _copy(decisions=[_BRANCHED["decisions"][0]])
        merged = carry_forward(_BRANCHED, repaired)
        self.assertEqual([d["id"] for d in merged["decisions"]], ["DEC-01", "DEC-02"])

    def test_a_dropped_state_is_carried_forward(self):
        repaired = _copy(states=[s for s in _BRANCHED["states"] if s["id"] != "S-02"])
        merged = carry_forward(_BRANCHED, repaired)
        self.assertIn("S-02", [s["id"] for s in merged["states"]])

    def test_a_dropped_outcome_is_carried_forward(self):
        """The decision survives and one of its branch labels does not, so a route disappears
        while every row still looks present."""
        repaired = _copy(decisions=[dict(_BRANCHED["decisions"][0], outcomes=["Pass"]),
                                    _BRANCHED["decisions"][1]])
        merged = carry_forward(_BRANCHED, repaired)
        self.assertEqual(merged["decisions"][0]["outcomes"], ["Pass", "Fail"])

    def test_a_corrected_row_is_the_repairs_version_not_the_drafts(self):
        """Carrying rows forward must not undo the correction that was asked for."""
        repaired = _copy(states=[dict(s, description="Locked out after two attempts")
                                 if s["id"] == "S-02" else s for s in _BRANCHED["states"]])
        merged = carry_forward(_BRANCHED, repaired)
        settled = next(s for s in merged["states"] if s["id"] == "S-02")
        self.assertEqual(settled["description"], "Locked out after two attempts")

    def test_a_row_the_repair_added_survives(self):
        added = {"id": "S-09", "reached_via": "DEC-02=Withdrawn", "description": "Withdrawn",
                 "next_decisions": [], "is_terminal": True, "outcome_type": "Termination"}
        merged = carry_forward(_BRANCHED, _copy(states=_BRANCHED["states"] + [added]))
        self.assertIn("S-09", [s["id"] for s in merged["states"]])

    def test_carrying_forward_does_not_mutate_the_draft_it_reads(self):
        before = json.dumps(_BRANCHED, sort_keys=True)
        carry_forward(_BRANCHED, _copy(decisions=[_BRANCHED["decisions"][0]]))
        self.assertEqual(json.dumps(_BRANCHED, sort_keys=True), before)


class TestTheWholeDraftAndRepairRound(unittest.TestCase):
    """End to end, through the workbook, because the audit that decides this reads a file."""

    def _draft(self, repair_reply):
        # The first draft answers everything except where DEC-02's "Too old" outcome leads, which
        # is the hole that makes the repair pass fire at all.
        first = _copy(states=[s for s in _BRANCHED["states"] if s["id"] != "S-04"])
        self.calls = []

        def complete(system, user, **kwargs):
            if "WHAT IS MISSING" in user:
                self.calls.append("repair")
                return json.dumps(repair_reply)
            self.calls.append("draft")
            return json.dumps(first)

        directory = Path(tempfile.mkdtemp())
        context = directory / "context.md"
        context.write_text("An agent that verifies a cardmember and files a dispute.",
                           encoding="utf-8")
        self.path = directory / "intake.xlsx"
        draft_intake_workbook(str(context), str(self.path), complete=complete)
        return read_intake(str(self.path))

    def test_the_repair_fires_and_fills_the_hole(self):
        intake = self._draft(_BRANCHED)
        self.assertEqual(self.calls, ["draft", "repair"])
        self.assertIn("S-04", [s.id for s in intake.states])

    def test_a_repair_that_fills_the_hole_and_drops_a_branch_keeps_the_branch(self):
        """The failure this whole file exists for: the named gap is answered, and DEC-01's Fail
        branch goes with it. Both must survive."""
        lossy = _copy(decisions=[dict(_BRANCHED["decisions"][0], outcomes=["Pass"]),
                                 _BRANCHED["decisions"][1]],
                      states=[s for s in _BRANCHED["states"] if s["id"] != "S-02"])
        intake = self._draft(lossy)

        self.assertIn("S-04", [s.id for s in intake.states], "the hole was not filled")
        self.assertIn("S-02", [s.id for s in intake.states], "the branch's ending was dropped")
        self.assertIn("Fail", intake.decisions[0].variants, "the branch label was dropped")
        self.assertEqual(len(build_scenario_space(intake, with_probes=False)), 3)

    def test_a_repair_that_leaves_more_gaps_than_it_found_is_thrown_away(self):
        """Carrying rows forward cannot save a repair that adds new holes of its own."""
        worse = _copy(decisions=_BRANCHED["decisions"] + [
            {"id": "DEC-09", "name": "Invented", "capability_id": "", "inputs": "",
             "outcomes": [], "input_source": "User", "max_attempts": 1, "outcome_condition": ""}])
        intake = self._draft(worse)
        self.assertNotIn("DEC-09", [d.id for d in intake.decisions])

    def test_a_repair_that_never_comes_back_leaves_the_draft_alone(self):
        def complete(system, user, **kwargs):
            if "WHAT IS MISSING" in user:
                raise RuntimeError("gateway down")
            return json.dumps(_copy(states=[s for s in _BRANCHED["states"] if s["id"] != "S-04"]))

        directory = Path(tempfile.mkdtemp())
        context = directory / "context.md"
        context.write_text("An agent.", encoding="utf-8")
        path = directory / "intake.xlsx"
        draft_intake_workbook(str(context), str(path), complete=complete)

        intake = read_intake(str(path))
        self.assertEqual(len(intake.states), 4)
        self.assertIn("Fail", intake.decisions[0].variants)

    def test_no_scratch_workbook_is_left_behind(self):
        """The candidate is written to a scratch path so a bad repair is recoverable; leaving it
        in the workspace would put a second intake workbook beside the real one."""
        self._draft(_BRANCHED)
        self.assertEqual(sorted(p.name for p in self.path.parent.glob("*.xlsx")),
                         ["intake.xlsx"])


if __name__ == "__main__":
    unittest.main()
