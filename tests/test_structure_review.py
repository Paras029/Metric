"""The structure-review pass: the core writers it drives, and the routes that expose it.

Two layers, because the layers fail differently. core.intake's writers can merge or reconnect a
workbook wrongly while the LLM call that proposed it was never involved; the routes can wire the
proposal to the wrong writer, or forget to invalidate what the change makes stale. Both are worth
catching on their own.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from openpyxl import load_workbook

from scenario_generator.core.graph import DecisionGraph, convergence_candidates
from scenario_generator.core.intake import (attach_decision_to_state, merge_decisions,
                                            read_intake, set_state_reached_via, write_template)
from scenario_generator.core.models import Decision, IntakeData, Persona, State
from scenario_generator.llm.structure_review import review_structure
from scenario_generator.webapp.app import create_app


def _workbook(directory: Path) -> Path:
    """Two decisions that establish the same fact by different means, plus one orphan decision."""
    path = directory / "intake.xlsx"
    write_template(str(path))
    book = load_workbook(path)
    book["L1 Use Case"]["B1"] = "Test agent"
    book["Personas"].append(["P1", "Cooperative user", "Happy path", "Y"])
    book["L2 Capabilities"].append(["CAP-01", "Identify via SSN4", "Lookup"])
    book["L2 Capabilities"].append(["CAP-02", "Identify via full SSN", "Lookup"])
    book["L3 Decisions"].append(
        ["DEC-03", "Identify via last 4 SSN", "CAP-01", "", "Matched / Not matched", "User", 1,
         "", "No"])
    book["L3 Decisions"].append(
        ["DEC-05", "Identify via full SSN", "CAP-02", "", "Matched / Not matched", "User", 1,
         "", "No"])
    book["L3 Decisions"].append(
        ["DEC-09", "Orphaned decision nothing leads to", "CAP-01", "", "A / B", "User", 1, "",
         "No"])
    book["L4 States"].append(["S-00", "Start", "Session begins", "DEC-03, DEC-05", "No", ""])
    book["L4 States"].append(["S-01", "DEC-03=Matched, DEC-05=Matched", "Identified", "", "Yes",
                              "Happy path"])
    book["L4 States"].append(["S-02", "DEC-03=Not matched", "Rejected", "", "Yes", "Termination"])
    book["L4 States"].append(["S-03", "DEC-05=Not matched", "Rejected", "", "Yes", "Termination"])
    book.save(path)
    return path


class TestConvergenceCandidates(unittest.TestCase):
    def test_decisions_landing_on_the_same_places_are_grouped(self):
        intake = IntakeData(
            use_case={}, personas=[Persona("P1", "P", [], True)], capabilities=[],
            decisions=[Decision("DEC-03", "SSN4", "CAP-01", "", ["Matched", "Not matched"]),
                      Decision("DEC-05", "Full SSN", "CAP-02", "", ["Matched", "Not matched"])],
            states=[State("S-00", "Start", "Start", ["DEC-03", "DEC-05"], False),
                   State("S-01", "DEC-03=Matched, DEC-05=Matched", "Identified", [], True,
                         "Happy path"),
                   State("S-02", "DEC-03=Not matched", "Rejected", [], True, "Termination"),
                   State("S-03", "DEC-05=Not matched", "Rejected", [], True, "Termination")],
            tools=[])
        graph = DecisionGraph(intake.decisions, intake.states)
        groups = convergence_candidates(graph)
        self.assertEqual(groups, [["DEC-03", "DEC-05"]])

    def test_decisions_landing_differently_are_not_grouped(self):
        intake = IntakeData(
            use_case={}, personas=[Persona("P1", "P", [], True)], capabilities=[],
            decisions=[Decision("DEC-01", "A", "CAP-01", "", ["Pass", "Fail"]),
                      Decision("DEC-02", "B", "CAP-01", "", ["Pass", "Fail"])],
            states=[State("S-00", "Start", "Start", ["DEC-01", "DEC-02"], False),
                   State("S-01", "DEC-01=Pass", "One ending", [], True, "Happy path"),
                   State("S-02", "DEC-01=Fail", "Two ending", [], True, "Termination"),
                   State("S-03", "DEC-02=Pass", "Three ending", [], True, "Escalation"),
                   State("S-04", "DEC-02=Fail", "Four ending", [], True, "Fallback")],
            tools=[])
        graph = DecisionGraph(intake.decisions, intake.states)
        self.assertEqual(convergence_candidates(graph), [])

    def test_an_out_of_scope_decision_is_never_a_candidate(self):
        intake = IntakeData(
            use_case={}, personas=[Persona("P1", "P", [], True)], capabilities=[],
            decisions=[Decision("DEC-03", "SSN4", "CAP-01", "", ["Matched", "Not matched"],
                                out_of_scope=True),
                      Decision("DEC-05", "Full SSN", "CAP-02", "", ["Matched", "Not matched"])],
            states=[State("S-00", "Start", "Start", ["DEC-03", "DEC-05"], False),
                   State("S-01", "DEC-03=Matched, DEC-05=Matched", "Identified", [], True,
                         "Happy path"),
                   State("S-02", "DEC-03=Not matched", "Rejected", [], True, "Termination"),
                   State("S-03", "DEC-05=Not matched", "Rejected", [], True, "Termination")],
            tools=[])
        graph = DecisionGraph(intake.decisions, intake.states)
        self.assertEqual(convergence_candidates(graph), [])


class TestReviewStructureValidation(unittest.TestCase):
    """The LLM pass drops anything that does not name something the intake actually has."""

    def setUp(self):
        self.intake = IntakeData(
            use_case={"Use case name": "Test"}, personas=[Persona("P1", "P", [], True)],
            capabilities=[],
            decisions=[Decision("DEC-03", "SSN4", "CAP-01", "", ["Matched", "Not matched"]),
                      Decision("DEC-05", "Full SSN", "CAP-02", "", ["Matched", "Not matched"]),
                      Decision("DEC-09", "Orphan", "CAP-01", "", ["A", "B"])],
            states=[State("S-00", "Start", "Start", ["DEC-03", "DEC-05"], False),
                   State("S-01", "DEC-03=Matched, DEC-05=Matched", "Identified", [], True,
                         "Happy path"),
                   State("S-02", "DEC-03=Not matched", "Rejected", [], True, "Termination"),
                   State("S-03", "DEC-05=Not matched", "Rejected", [], True, "Termination")],
            tools=[])

    def _reply(self, payload):
        return lambda system, user, **kwargs: json.dumps(payload)

    def test_a_reconnection_naming_an_unknown_id_is_dropped(self):
        review = review_structure(self.intake, complete=self._reply(
            {"reconnections": [{"kind": "decision", "id": "DEC-99", "attach_to_state": "S-01"}]}))
        self.assertEqual(review.reconnections, [])

    def test_a_reconnection_naming_a_state_that_does_not_exist_is_dropped(self):
        review = review_structure(self.intake, complete=self._reply(
            {"reconnections": [{"kind": "decision", "id": "DEC-09", "attach_to_state": "S-99"}]}))
        self.assertEqual(review.reconnections, [])

    def test_a_valid_reconnection_survives(self):
        review = review_structure(self.intake, complete=self._reply(
            {"reconnections": [{"kind": "decision", "id": "DEC-09", "attach_to_state": "S-01",
                                "rationale": "r"}]}))
        self.assertEqual(len(review.reconnections), 1)
        self.assertEqual(review.reconnections[0].attach_to_state, "S-01")

    def test_a_consolidation_whose_kept_id_is_not_a_member_is_dropped(self):
        review = review_structure(self.intake, complete=self._reply(
            {"consolidations": [{"decisions": ["DEC-03", "DEC-05"], "id": "DEC-99",
                                 "outcomes": ["A", "B"]}]}))
        self.assertEqual(review.consolidations, [])

    def test_a_consolidation_with_fewer_than_two_outcomes_is_dropped(self):
        review = review_structure(self.intake, complete=self._reply(
            {"consolidations": [{"decisions": ["DEC-03", "DEC-05"], "id": "DEC-03",
                                 "outcomes": ["OnlyOne"]}]}))
        self.assertEqual(review.consolidations, [])

    def test_an_outcome_map_entry_outside_the_new_outcomes_is_dropped(self):
        review = review_structure(self.intake, complete=self._reply(
            {"consolidations": [{"decisions": ["DEC-03", "DEC-05"], "id": "DEC-03",
                                 "outcomes": ["Identified", "Not identified"],
                                 "outcome_map": {"DEC-03=Matched": "Identified",
                                                "DEC-05=Matched": "Something else"}}]}))
        self.assertEqual(review.consolidations[0].outcome_map,
                         {"DEC-03=Matched": "Identified"})

    def test_a_failed_call_returns_an_empty_review_rather_than_raising(self):
        def boom(system, user, **kwargs):
            raise RuntimeError("gateway down")
        review = review_structure(self.intake, complete=boom)
        self.assertFalse(review)


class TestMergeAndReconnectWriters(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = _workbook(self.dir)

    def test_attach_decision_to_state_adds_it_to_next_decisions(self):
        self.assertTrue(attach_decision_to_state(str(self.path), "S-01", "DEC-09"))
        intake = read_intake(str(self.path))
        s01 = next(s for s in intake.states if s.id == "S-01")
        self.assertIn("DEC-09", s01.next_decisions)

    def test_attach_decision_to_an_unknown_state_reports_failure(self):
        self.assertFalse(attach_decision_to_state(str(self.path), "S-99", "DEC-09"))

    def test_set_state_reached_via_rewrites_the_column(self):
        self.assertTrue(set_state_reached_via(str(self.path), "S-01", "DEC-03=Matched"))
        intake = read_intake(str(self.path))
        s01 = next(s for s in intake.states if s.id == "S-01")
        self.assertEqual(s01.reached_via, "DEC-03=Matched")

    def test_set_state_reached_via_on_an_unknown_state_reports_failure(self):
        self.assertFalse(set_state_reached_via(str(self.path), "S-99", "DEC-03=Matched"))

    def test_merging_two_decisions_collapses_the_space(self):
        before = read_intake(str(self.path))
        from scenario_generator.pipeline import build_scenario_space
        # 2x2 combinations through DEC-03/DEC-05, plus DEC-09's own two outcomes as an orphan
        # decision nothing leads to -- unmerged, unconnected, but still enumerated on its own.
        self.assertEqual(len(build_scenario_space(before)), 6)

        ok = merge_decisions(
            str(self.path), ["DEC-03", "DEC-05"], "DEC-03", "Identify caller",
            ["Identified", "Not identified"],
            {"DEC-03=Matched": "Identified", "DEC-05=Matched": "Identified",
             "DEC-03=Not matched": "Not identified", "DEC-05=Not matched": "Not identified"},
            primary_capability="CAP-01")
        self.assertTrue(ok)

        after = read_intake(str(self.path))
        ids = {d.id for d in after.decisions}
        self.assertIn("DEC-03", ids)
        self.assertNotIn("DEC-05", ids)
        kept = next(d for d in after.decisions if d.id == "DEC-03")
        self.assertEqual(kept.name, "Identify caller")
        self.assertEqual(set(kept.variants), {"Identified", "Not identified"})

        self.assertEqual(len(build_scenario_space(after)), 4)      # the combinatorial blow-up is gone

    def test_merging_requires_the_kept_id_to_be_a_member(self):
        self.assertFalse(merge_decisions(
            str(self.path), ["DEC-03", "DEC-05"], "DEC-09", "x", ["A", "B"], {}))

    def test_merging_requires_at_least_two_decisions(self):
        self.assertFalse(merge_decisions(str(self.path), ["DEC-03"], "DEC-03", "x", ["A", "B"], {}))

    def test_a_missing_outcome_map_entry_leaves_that_route_on_its_old_name(self):
        merge_decisions(
            str(self.path), ["DEC-03", "DEC-05"], "DEC-03", "Identify caller",
            ["Identified", "Not identified"],
            {"DEC-03=Matched": "Identified", "DEC-05=Matched": "Identified"})   # Not matched unmapped
        after = read_intake(str(self.path))
        rejected = [s for s in after.states if "Not matched" in s.reached_via]
        self.assertTrue(rejected, "an unmapped pair should survive under its old wording")


class TestStructureReviewRoutes(unittest.TestCase):
    """The web routes: run, apply, dismiss -- through the actual Flask app."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.app = create_app(self.root)
        self.client = self.app.test_client()
        self.client.post("/workspaces", data={"name": "Structure review test"})

        scratch = Path(tempfile.mkdtemp())
        workbook = _workbook(scratch)
        with open(workbook, "rb") as handle:
            self.client.post("/stage/intake/upload",
                             data={"files": (handle, workbook.name), "group": "intake_workbook"},
                             content_type="multipart/form-data")

    def _reply(self, payload):
        return lambda system, user, **kwargs: json.dumps(payload)

    def test_running_the_pass_stores_proposals_and_shows_them(self):
        reply = {
            "reconnections": [{"kind": "decision", "id": "DEC-09", "attach_to_state": "S-01",
                               "rationale": "Nothing else leads to it."}],
            "consolidations": [{"decisions": ["DEC-03", "DEC-05"], "id": "DEC-03",
                                "name": "Identify caller",
                                "outcomes": ["Identified", "Not identified"],
                                "outcome_map": {"DEC-03=Matched": "Identified",
                                               "DEC-05=Matched": "Identified",
                                               "DEC-03=Not matched": "Not identified",
                                               "DEC-05=Not matched": "Not identified"},
                                "capabilities": ["CAP-01", "CAP-02"], "importance": "Low",
                                "rationale": "Both establish the same fact."}],
        }
        with mock.patch("scenario_generator.llm.structure_review.ask_llm", self._reply(reply)):
            response = self.client.post("/stage/intake/structure-review", follow_redirects=True)
        page = response.data.decode()
        self.assertIn("DEC-09", page)
        self.assertIn("Identify caller", page)

    def test_applying_a_reconnection_writes_it_and_removes_the_proposal(self):
        with mock.patch("scenario_generator.llm.structure_review.ask_llm",
                        self._reply({"reconnections": [
                            {"kind": "decision", "id": "DEC-09", "attach_to_state": "S-01",
                             "rationale": "r"}]})):
            self.client.post("/stage/intake/structure-review")

        workspace = self.app.config["WORKSPACE_ROOT"]
        from scenario_generator.webapp.workspace import Workspace
        ws = Workspace.load(next(workspace.iterdir()))
        self.assertEqual(len(ws.structure_proposals), 1)
        proposal_id = ws.structure_proposals[0]["id"]

        self.client.post(f"/stage/intake/structure-review/{proposal_id}/apply")

        ws = Workspace.load(ws.root)
        self.assertEqual(ws.structure_proposals, [])
        intake = read_intake(str(ws.artifact_path("intake", "workbook")))
        s01 = next(s for s in intake.states if s.id == "S-01")
        self.assertIn("DEC-09", s01.next_decisions)

    def test_dismissing_a_proposal_removes_it_without_touching_the_workbook(self):
        with mock.patch("scenario_generator.llm.structure_review.ask_llm",
                        self._reply({"reconnections": [
                            {"kind": "decision", "id": "DEC-09", "attach_to_state": "S-01",
                             "rationale": "r"}]})):
            self.client.post("/stage/intake/structure-review")

        workspace = self.app.config["WORKSPACE_ROOT"]
        from scenario_generator.webapp.workspace import Workspace
        ws = Workspace.load(next(workspace.iterdir()))
        proposal_id = ws.structure_proposals[0]["id"]

        self.client.post(f"/stage/intake/structure-review/{proposal_id}/dismiss")

        ws = Workspace.load(ws.root)
        self.assertEqual(ws.structure_proposals, [])
        intake = read_intake(str(ws.artifact_path("intake", "workbook")))
        s01 = next(s for s in intake.states if s.id == "S-01")
        self.assertNotIn("DEC-09", s01.next_decisions)


if __name__ == "__main__":
    unittest.main()
