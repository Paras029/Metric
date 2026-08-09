"""The review layer: materiality precedence, proposal validation, and the limits on what a
review is allowed to do.
"""
import unittest
import json
from scenario_generator.core.models import (Capability, Decision, IntakeData, OwnerScenario,
                                            Persona, State, Tool)
from scenario_generator.core.probes import build_probes
from scenario_generator.pipeline import build_scenarios
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
                     model=None, **kwargs):
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
        # config.stage_tier builds a freshly named Tier per call site even where nothing
        # overrides it, so this checks the tier's substance (model, budget, effort, retries)
        # rather than object identity with the JUDGEMENT constant itself.
        for call in self.seen:
            tier = call["tier"]
            self.assertEqual(tier.model, config.JUDGEMENT.model)
            self.assertEqual(tier.max_tokens, config.JUDGEMENT.max_tokens)
            self.assertEqual(tier.reasoning_effort, config.JUDGEMENT.reasoning_effort)
            self.assertEqual(tier.max_attempts, config.JUDGEMENT.max_attempts)
        self.assertEqual(config.JUDGEMENT.reasoning_effort, "high")
        self.assertGreater(config.JUDGEMENT.max_tokens, config.STANDARD.max_tokens)
        self.assertGreaterEqual(config.STANDARD.max_tokens, config.STANDARD.max_tokens)

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
        self.assertNotIn("WHAT THE MODEL OWNER SUBMITTED", self.seen[0]["user"])

        self.seen.clear()
        reviewer.review(self.scenarios, _INTAKE,
                        [OwnerScenario("OWN-1", "Their own happy path test.")])
        self.assertIn("WHAT THE MODEL OWNER SUBMITTED", self.seen[0]["user"])
        self.assertIn("Their own happy path test.", self.seen[0]["user"])

    def test_a_probe_is_never_given_an_ending(self):
        """A probe's 'category' is the family it came from, not one of the five endings a route
        can have. A verdict on one is an answer to a question with no right answer, so whatever
        comes back for a probe is ignored rather than written down."""
        def complete(system, user, **kwargs):
            if '{"proposals"' in user:
                return json.dumps({"proposals": []})
            ids = [line.split('"id": "')[1].split('"')[0]
                   for line in user.splitlines() if '"id": "' in line]
            return json.dumps({i: {"materiality": "High", "rationale": "r", "flag": "",
                                   "category": "Escalation", "category_rationale": "x"}
                               for i in ids})

        scenarios = self.scenarios + build_probes(_INTAKE)
        ScenarioReviewer(complete=complete, batch_size=4).review(scenarios, _INTAKE)

        probes = [s for s in scenarios if s.is_probe]
        self.assertTrue(probes)
        self.assertTrue(all(s.review_category == "" for s in probes))

    def test_a_failed_call_leaves_the_registry_untouched(self):
        def broken(system, user, max_tokens=None, reasoning_effort=None):
            raise RuntimeError("gateway down")
        reviewed, proposals = ScenarioReviewer(complete=broken).review(self.scenarios, _INTAKE)
        self.assertEqual(proposals, [])
        self.assertTrue(all(s.review_materiality == "" for s in reviewed))


class TestOneCallSettlesEveryJudgedColumn(unittest.TestCase):
    """Materiality, the ending a scenario is filed under, and anything wrong with it.

    Three questions answered from one reading of the same material -- the route, the expected
    outcome and the description. Asking them in three passes reads that material three times, and
    made the batch size mean two different things at once: a mechanical category check tolerates a
    far coarser chunk than a materiality judgement, so tuning for one mistuned the other.

    Category is otherwise deterministic, taken from the Outcome Type the intake declares on the
    state a route ends in. That makes it exactly the column a wrong declaration corrupts silently,
    which is why the review reads it back at all.
    """

    def setUp(self):
        self.scenarios = build_scenarios(_INTAKE)
        self.assertTrue(self.scenarios, "the fixture intake should produce graph scenarios")

    def _fake(self, category="Escalation", rationale="ends with a handoff"):
        def complete(system, user, **kwargs):
            if '{"proposals"' in user:
                return json.dumps({"proposals": []})
            ids = [line.split('"id": "')[1].split('"')[0]
                   for line in user.splitlines() if '"id": "' in line]
            return json.dumps({i: {"materiality": "High", "rationale": "r", "flag": "",
                                   "category": category, "category_rationale": rationale}
                               for i in ids})
        return complete

    def test_one_call_per_chunk_settles_all_of_it(self):
        """The property this exists for: no second sweep over the same scenarios."""
        prompts = []

        def complete(system, user, **kwargs):
            prompts.append(user)
            return self._fake()(system, user, **kwargs)

        ScenarioReviewer(complete=complete, batch_size=1).review(self.scenarios, _INTAKE)

        per_scenario = [p for p in prompts if '{"proposals"' not in p]
        self.assertEqual(len(per_scenario), len(self.scenarios),
                         "batch_size=1 should be exactly one call per scenario, not two")
        # And that one call carried every question.
        self.assertIn("materiality", per_scenario[0])
        self.assertIn("how the interaction ends", per_scenario[0])
        self.assertIn("flag", per_scenario[0])

    def test_proposing_stays_its_own_call(self):
        """It is asked about the benchmark as a whole rather than about any chunk of it, so it has
        nothing to batch and nothing to share with a per-scenario reading."""
        prompts = []

        def complete(system, user, **kwargs):
            prompts.append(user)
            return self._fake()(system, user, **kwargs)

        ScenarioReviewer(complete=complete, batch_size=1).review(self.scenarios, _INTAKE)
        self.assertEqual(sum(1 for p in prompts if '{"proposals"' in p), 1)

    def test_materiality_and_the_ending_both_land_from_the_one_reply(self):
        ScenarioReviewer(complete=self._fake(), batch_size=4).review(self.scenarios, _INTAKE)
        self.assertTrue(all(s.review_materiality == "High" for s in self.scenarios))
        self.assertTrue(all(s.review_category == "Escalation" for s in self.scenarios))

    def test_a_disagreement_is_recorded_beside_the_declared_value(self):
        declared = [s.category for s in self.scenarios]
        ScenarioReviewer(complete=self._fake(), batch_size=4).review(self.scenarios, _INTAKE)

        self.assertEqual([s.category for s in self.scenarios], declared)   # untouched
        self.assertTrue(all(s.review_category == "Escalation" for s in self.scenarios))
        self.assertTrue(all(s.effective_category == "Escalation" for s in self.scenarios))

    def test_the_reason_for_a_changed_ending_is_kept_apart_from_the_materiality_reason(self):
        """Two verdicts in one reply, so the reply has to carry two reasons -- a page showing the
        materiality rationale under a changed category would be attributing the wrong argument."""
        ScenarioReviewer(complete=self._fake(rationale="a person picks it up"),
                         batch_size=4).review(self.scenarios, _INTAKE)
        changed = [s for s in self.scenarios if s.review_category]
        self.assertTrue(changed)
        self.assertTrue(all(s.review_category_rationale == "a person picks it up" for s in changed))
        self.assertTrue(all(s.review_rationale == "r" for s in changed))

    def test_agreement_is_not_written_down(self):
        """Most scenarios come back agreeing. Recording that would fill the review columns with
        restatements and bury the few rows that want a second look."""
        agreed = self.scenarios[0].category
        ScenarioReviewer(complete=self._fake(category=agreed),
                         batch_size=4).review(self.scenarios, _INTAKE)

        matching = [s for s in self.scenarios if s.category == agreed]
        self.assertTrue(matching)
        self.assertTrue(all(s.review_category == "" for s in matching))
        self.assertTrue(all(s.effective_category == s.category for s in matching))

    def test_a_category_outside_the_vocabulary_is_ignored(self):
        ScenarioReviewer(complete=self._fake(category="Sideways"),
                         batch_size=4).review(self.scenarios, _INTAKE)
        self.assertTrue(all(s.review_category == "" for s in self.scenarios))

    def test_an_empty_category_leaves_the_declared_one_alone(self):
        """What a probe comes back with, and what a reply that had nothing to say returns."""
        ScenarioReviewer(complete=self._fake(category=""),
                         batch_size=4).review(self.scenarios, _INTAKE)
        self.assertTrue(all(s.review_category == "" for s in self.scenarios))

    def test_a_differently_capitalised_answer_still_counts(self):
        """'happy path' is the same verdict as 'Happy path'; dropping it would silently discard
        a correction for a formatting difference."""
        ScenarioReviewer(complete=self._fake(category="eSCALATION"),
                         batch_size=4).review(self.scenarios, _INTAKE)
        self.assertTrue(all(s.review_category == "Escalation" for s in self.scenarios))


if __name__ == "__main__":
    unittest.main()
