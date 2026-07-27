"""The review layer: materiality precedence, proposal validation, and the limits on what a
review is allowed to do.
"""
import unittest
import json
from scenario_generator.core.models import (Capability, Decision, IntakeData, OwnerScenario,
                                            Persona, State, Tool)
from scenario_generator.core.probes import build_probes
from scenario_generator.core.proposals import instantiate_proposals
from scenario_generator.llm import config
from scenario_generator.llm.reviewer import ScenarioReviewer

_INTAKE = IntakeData(
    use_case={"Use case name": "Test", "Business objective": "Objective"},
    personas=[Persona("P1", "Default", [], True)],
    capabilities=[Capability("CAP-01", "Auth", "Gating")],
    decisions=[Decision("DEC-01", "Auth", "CAP-01", "", ["Pass", "Fail"])],
    states=[State("S-00", "Start", "Start", ["DEC-01"], False)],
    tools=[Tool("Verifier", "CAP-01", False)],
)


def _valid_proposal(**overrides):
    entry = {
        "title": "A proposal",
        "description": "Something worth testing.",
        "turn_plan": "1. Do the thing.",
        "turns": 2,
        "expected_outcome": "Handled correctly.",
        "decision_path": [{"decision_id": "DEC-01", "variant": "Pass"}],
        "anchor_scenario_id": "SC-001",
        "category": "Fallback",
        "capabilities": ["CAP-01"],
        "persona_id": "P1",
        "materiality": "High",
        "rationale": "Fills a gap.",
    }
    entry.update(overrides)
    return entry


class TestProposalValidation(unittest.TestCase):
    def test_a_valid_proposal_survives_intact(self):
        scenario = instantiate_proposals([_valid_proposal()], _INTAKE)[0]
        self.assertEqual(scenario.path_str, "DEC-01=Pass")
        self.assertEqual(scenario.category, "Fallback")
        self.assertEqual(scenario.materiality, "High")
        self.assertEqual(scenario.capabilities, ["CAP-01"])
        self.assertEqual(scenario.proposed_anchor, "SC-001")

    def test_undeclared_decisions_are_discarded_and_recorded(self):
        scenario = instantiate_proposals(
            [_valid_proposal(decision_path=[{"decision_id": "DEC-99", "variant": "Nope"}])],
            _INTAKE)[0]
        self.assertEqual(scenario.path, [])
        self.assertIn("discarded", scenario.proposed_rationale)

    def test_undeclared_outcome_on_a_real_decision_is_discarded(self):
        scenario = instantiate_proposals(
            [_valid_proposal(decision_path=[{"decision_id": "DEC-01", "variant": "Invented"}])],
            _INTAKE)[0]
        self.assertEqual(scenario.path, [])

    def test_unknown_capability_persona_category_and_tier_fall_back(self):
        scenario = instantiate_proposals(
            [_valid_proposal(capabilities=["CAP-99"], persona_id="P9",
                             category="NotACategory", materiality="Enormous")], _INTAKE)[0]
        self.assertEqual(scenario.capabilities, [])
        self.assertEqual(scenario.persona.id, "P1")
        self.assertEqual(scenario.category, "Proposed")
        self.assertEqual(scenario.materiality, "Medium")

    def test_a_proposal_without_a_description_is_dropped(self):
        self.assertEqual(instantiate_proposals([_valid_proposal(description="  ")], _INTAKE), [])

    def test_limit_is_enforced(self):
        proposals = instantiate_proposals([_valid_proposal() for _ in range(30)], _INTAKE, limit=5)
        self.assertEqual(len(proposals), 5)

    def test_proposals_are_marked_and_never_look_graph_derived(self):
        scenario = instantiate_proposals([_valid_proposal()], _INTAKE)[0]
        self.assertEqual(scenario.origin, "llm-proposed")
        self.assertTrue(scenario.is_proposed)
        self.assertTrue(scenario.id.startswith("LP-"))

    def test_pathless_proposal_gets_turns_from_its_own_count(self):
        scenario = instantiate_proposals(
            [_valid_proposal(decision_path=[], turns=4)], _INTAKE)[0]
        self.assertEqual(len(scenario.turn_meta), 4)
        self.assertTrue(all(t.decision_id == "-" for t in scenario.turn_meta))


class TestReviewSweep(unittest.TestCase):
    def setUp(self):
        self.scenarios = build_probes(_INTAKE)[:8]
        self.seen = []

    def _fake(self, revision=None, proposals=None):
        def complete(system, user, max_tokens=None, reasoning_effort=None, tier=None,
                     model=None):
            self.seen.append({"user": user, "tier": tier, "effort": reasoning_effort,
                              "max_tokens": max_tokens})
            if '{"proposals"' in user:
                return json.dumps({"proposals": proposals or []})
            ids = [line.split('"id": "')[1].split('"')[0]
                   for line in user.splitlines() if '"id": "' in line]
            return json.dumps({i: (revision or {"materiality": "High", "rationale": "r",
                                                "flag": ""}) for i in ids})
        return complete

    def test_review_runs_on_the_judgement_tier(self):
        """This pass reads the whole benchmark and justifies every verdict, so it takes the
        largest output budget and the highest reasoning effort. Reasoning is drawn from the reply
        budget, so the two only make sense together -- which is why a tier carries both."""
        ScenarioReviewer(complete=self._fake(), batch_size=4).review(self.scenarios, _INTAKE)
        self.assertTrue(self.seen)
        for call in self.seen:
            self.assertIs(call["tier"], config.JUDGEMENT)
        self.assertEqual(config.JUDGEMENT.reasoning_effort, "high")
        self.assertGreater(config.JUDGEMENT.max_tokens, config.STANDARD.max_tokens)
        self.assertGreaterEqual(config.STANDARD.max_tokens, config.FAST.max_tokens)

    def test_every_call_carries_the_whole_registry_digest(self):
        """Each batch is judged against the full set, not just its own rows."""
        ScenarioReviewer(complete=self._fake(), batch_size=4).review(self.scenarios, _INTAKE)
        for call in self.seen:
            for scenario in self.scenarios:
                self.assertIn(scenario.id, call["user"])

    def test_revision_does_not_overwrite_the_original_assessment(self):
        for scenario in self.scenarios:
            scenario.materiality = "Low"
        ScenarioReviewer(complete=self._fake(), batch_size=4).review(self.scenarios, _INTAKE)
        for scenario in self.scenarios:
            self.assertEqual(scenario.materiality, "Low")
            self.assertEqual(scenario.review_materiality, "High")
            self.assertEqual(scenario.effective_materiality, "High")

    def test_human_override_still_wins_over_a_revision(self):
        self.scenarios[0].materiality_override = "Critical"
        ScenarioReviewer(complete=self._fake(), batch_size=4).review(self.scenarios, _INTAKE)
        self.assertEqual(self.scenarios[0].review_materiality, "High")
        self.assertEqual(self.scenarios[0].effective_materiality, "Critical")

    def test_invalid_tier_or_flag_is_ignored_rather_than_stored(self):
        reviewer = ScenarioReviewer(
            complete=self._fake(revision={"materiality": "Enormous", "rationale": "r",
                                          "flag": "Nonsense"}), batch_size=4)
        reviewer.review(self.scenarios, _INTAKE)
        self.assertTrue(all(s.review_materiality == "" for s in self.scenarios))
        self.assertTrue(all(s.review_flag == "" for s in self.scenarios))

    def test_proposal_limit_is_enforced_on_the_sweep(self):
        many = [_valid_proposal(description=f"Proposal {i}") for i in range(40)]
        _, proposals = ScenarioReviewer(complete=self._fake(proposals=many), batch_size=4,
                                        proposal_limit=3).review(self.scenarios, _INTAKE)
        self.assertEqual(len(proposals), 3)

    def test_review_never_removes_scenarios(self):
        """A flag is a recommendation; deletion is a human act."""
        reviewer = ScenarioReviewer(
            complete=self._fake(revision={"materiality": "Low", "rationale": "duplicate",
                                          "flag": "Redundant"}), batch_size=4)
        reviewed, _ = reviewer.review(self.scenarios, _INTAKE)
        self.assertEqual(len(reviewed), len(self.scenarios))
        self.assertTrue(all(s.review_flag == "Redundant" for s in reviewed))

    def test_peer_signals_are_supplied_as_evidence(self):
        """Redundancy is measured deterministically and handed over, not left to be guessed."""
        ScenarioReviewer(complete=self._fake(), batch_size=4).review(self.scenarios, _INTAKE)
        self.assertIn("similar_scenarios_in_benchmark", self.seen[0]["user"])

    def test_materiality_scale_and_mission_reach_every_call(self):
        ScenarioReviewer(complete=self._fake(), batch_size=4).review(self.scenarios, _INTAKE)
        for call in self.seen:
            self.assertIn("MATERIALITY SCALE", call["user"])
            self.assertIn("WHAT THIS EXERCISE IS", call["user"])

    def test_owner_scenarios_are_shown_only_when_supplied(self):
        reviewer = ScenarioReviewer(complete=self._fake(), batch_size=4)
        reviewer.review(self.scenarios, _INTAKE)
        self.assertNotIn("WHAT THE AGENT'S OWN TEAM SUBMITTED", self.seen[0]["user"])

        self.seen.clear()
        reviewer.review(self.scenarios, _INTAKE,
                        [OwnerScenario("OWN-1", "Their own happy path test.")])
        self.assertIn("WHAT THE AGENT'S OWN TEAM SUBMITTED", self.seen[0]["user"])
        self.assertIn("Their own happy path test.", self.seen[0]["user"])

    def test_a_failed_call_leaves_the_registry_untouched(self):
        def broken(system, user, max_tokens=None, reasoning_effort=None):
            raise RuntimeError("gateway down")
        reviewed, proposals = ScenarioReviewer(complete=broken).review(self.scenarios, _INTAKE)
        self.assertEqual(proposals, [])
        self.assertTrue(all(s.review_materiality == "" for s in reviewed))


if __name__ == "__main__":
    unittest.main()
