"""The review layer: materiality precedence, proposal validation, and the limits on what a
review is allowed to do.
"""
import unittest
import json
from scenario_generator.core.models import (Capability, Decision, IntakeData, OwnerScenario,
                                            Persona, State, Tool)
from scenario_generator.core.probes import build_probes
from scenario_generator.pipeline import build_scenario_space
from scenario_generator.core.proposals import instantiate_proposals
from scenario_generator.llm import config
from scenario_generator.llm.reviewer import ScenarioReviewer

_INTAKE = IntakeData(
    use_case={"Use case name": "Test", "Business objective": "Objective"},
    personas=[Persona("P1", "Default", [], True)],
    capabilities=[Capability("CAP-01", "Auth", "Gating")],
    decisions=[Decision("DEC-01", "Auth", "CAP-01", "", ["Pass", "Fail"])],
    # Both outcomes land somewhere declared. A route whose ending the intake never states is not
    # issued as a scenario at all now, so a fixture without these produces an empty space.
    states=[State("S-00", "Start", "Start", ["DEC-01"], False),
            State("S-01", "DEC-01=Pass", "Verified", [], True, "Happy path"),
            State("S-02", "DEC-01=Fail", "Locked out", [], True, "Termination")],
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
        self.assertNotIn("CAP-99", scenario.capabilities)
        self.assertEqual(scenario.persona.id, "P1")
        self.assertEqual(scenario.category, "Proposed")
        self.assertEqual(scenario.materiality, "Medium")


class TestWhichBlocksAProposalTouches(unittest.TestCase):
    """Read off the route it walks, not off the field where it says so.

    That field is one of a dozen the proposal is asked for, and a proposal arriving without it
    came back scoped to nothing at all -- no block, and no "end to end" either, since that label
    needs more than one block to name. It landed hardest on exactly the scenarios the review is
    now asked for: a cross-capability journey is *defined* by crossing, so it is the case most
    likely to arrive with the field blank and the least affordable to lose.
    """

    def setUp(self):
        """A second block, because crossing needs two."""
        import dataclasses
        from scenario_generator.core.models import Capability, Decision, State
        self.intake = dataclasses.replace(
            _INTAKE,
            capabilities=[Capability("CAP-01", "Auth", "Gating"),
                          Capability("CAP-02", "Disputes", "Transactional")],
            decisions=[Decision("DEC-01", "Auth", "CAP-01", "", ["Pass", "Fail"]),
                       Decision("DEC-03", "Raise it", "CAP-02", "", ["Recognised", "Not"])],
            states=[State("S-00", "Start", "Start", ["DEC-01"], False),
                    State("S-01", "DEC-01=Pass", "Verified", ["DEC-03"], False),
                    State("S-02", "DEC-01=Fail", "Locked out", [], True, "Termination"),
                    State("S-03", "DEC-03=Recognised", "Filed", [], True, "Happy path"),
                    State("S-04", "DEC-03=Not", "Refused", [], True, "Termination")])

    def test_the_steps_answer_where_the_field_is_missing(self):
        scenario = instantiate_proposals([_valid_proposal(capabilities=[])], self.intake)[0]
        self.assertEqual(scenario.capabilities, ["CAP-01"])
        self.assertEqual(scenario.capability_id, "CAP-01")

    def test_a_route_that_crosses_blocks_is_scoped_to_neither_and_says_so(self):
        crossing = _valid_proposal(capabilities=[], decision_path=[
            {"decision_id": "DEC-01", "variant": "Pass"},
            {"decision_id": "DEC-03", "variant": "Recognised"}])
        scenario = instantiate_proposals([crossing], self.intake)[0]
        self.assertEqual(scenario.capability_id, "", "a crossing route is not one block's")
        self.assertGreater(len(scenario.capabilities), 1,
                           "and the blocks it crosses have to be named, or it reads as scoped to "
                           "nothing rather than to several")

    def test_the_blocks_are_in_the_order_the_route_walks_them(self):
        crossing = _valid_proposal(capabilities=[], decision_path=[
            {"decision_id": "DEC-03", "variant": "Recognised"},
            {"decision_id": "DEC-01", "variant": "Pass"}])
        walked = instantiate_proposals([crossing], self.intake)[0].capabilities
        forwards = _valid_proposal(capabilities=[], decision_path=[
            {"decision_id": "DEC-01", "variant": "Pass"},
            {"decision_id": "DEC-03", "variant": "Recognised"}])
        self.assertEqual(walked, list(reversed(
            instantiate_proposals([forwards], self.intake)[0].capabilities)))

    def test_a_proposal_with_no_route_falls_back_to_what_it_declared(self):
        """A perturbation legitimately follows no declared route. What it says it exercises is
        then the only statement anybody has made about it."""
        scenario = instantiate_proposals(
            [_valid_proposal(decision_path=[], capabilities=["CAP-02"])], self.intake)[0]
        self.assertEqual(scenario.capabilities, ["CAP-02"])
        self.assertEqual(scenario.capability_id, "CAP-02")

    def test_nothing_undeclared_survives_either_way(self):
        scenario = instantiate_proposals(
            [_valid_proposal(decision_path=[], capabilities=["CAP-99"])], self.intake)[0]
        self.assertEqual(scenario.capabilities, [])

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
        """This pass reads the whole scenario space and justifies every verdict, so it takes the
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
        self.scenarios[0].materiality_override = "High"
        ScenarioReviewer(complete=self._fake(), batch_size=4).review(self.scenarios, _INTAKE)
        self.assertEqual(self.scenarios[0].review_materiality, "High")
        self.assertEqual(self.scenarios[0].effective_materiality, "High")

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
        self.assertIn("similar_scenarios_in_space", self.seen[0]["user"])

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

    def test_probes_are_left_out_of_the_category_sweep(self):
        """A probe's 'category' is the family it came from, not one of the five endings a route
        can have, so asking which of those it is would be asking a question with no right answer."""
        seen = []

        def complete(system, user, **kwargs):
            if "how the interaction ends" in user:
                seen.append(user)
            return self._fake()(system, user, **kwargs)

        ScenarioReviewer(complete=complete, batch_size=4).review(self.scenarios, _INTAKE)
        self.assertEqual(seen, [])

    def test_a_failed_call_leaves_the_registry_untouched(self):
        def broken(system, user, max_tokens=None, reasoning_effort=None):
            raise RuntimeError("gateway down")
        reviewed, proposals = ScenarioReviewer(complete=broken).review(self.scenarios, _INTAKE)
        self.assertEqual(proposals, [])
        self.assertTrue(all(s.review_materiality == "" for s in reviewed))


class TestTheCategorySweep(unittest.TestCase):
    """Category is otherwise deterministic -- taken from the Outcome Type the intake declares on
    the state a route ends in. That makes it exactly the column a wrong declaration corrupts
    silently, which is why the review reads it back."""

    def setUp(self):
        self.scenarios = build_scenario_space(_INTAKE)
        self.assertTrue(self.scenarios, "the fixture intake should produce graph scenarios")

    def _fake(self, category="Escalation", rationale="ends with a handoff"):
        def complete(system, user, **kwargs):
            if '{"proposals"' in user:
                return json.dumps({"proposals": []})
            ids = [line.split('"id": "')[1].split('"')[0]
                   for line in user.splitlines() if '"id": "' in line]
            if "how the interaction ends" in user:
                return json.dumps({i: {"category": category, "rationale": rationale} for i in ids})
            return json.dumps({i: {"materiality": "High", "rationale": "r", "flag": ""}
                               for i in ids})
        return complete

    def test_a_disagreement_is_recorded_beside_the_declared_value(self):
        declared = [s.category for s in self.scenarios]
        ScenarioReviewer(complete=self._fake(), batch_size=4).review(self.scenarios, _INTAKE)

        self.assertEqual([s.category for s in self.scenarios], declared)   # untouched
        self.assertTrue(all(s.review_category == "Escalation" for s in self.scenarios))
        self.assertTrue(all(s.effective_category == "Escalation" for s in self.scenarios))

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

    def test_a_differently_capitalised_answer_still_counts(self):
        """'happy path' is the same verdict as 'Happy path'; dropping it would silently discard
        a correction for a formatting difference."""
        ScenarioReviewer(complete=self._fake(category="eSCALATION"),
                         batch_size=4).review(self.scenarios, _INTAKE)
        self.assertTrue(all(s.review_category == "Escalation" for s in self.scenarios))

    def test_the_second_column_costs_far_fewer_calls_than_the_first(self):
        """It takes a much coarser batch, so reviewing a second column is not a second review."""
        prompts = []

        def complete(system, user, **kwargs):
            prompts.append(user)
            return self._fake()(system, user, **kwargs)

        ScenarioReviewer(complete=complete, batch_size=1).review(self.scenarios, _INTAKE)
        assess = [p for p in prompts if "how the interaction ends" not in p and '"proposals"' not in p]
        category = [p for p in prompts if "how the interaction ends" in p]

        self.assertEqual(len(assess), len(self.scenarios))    # batch_size=1, one call each
        self.assertLess(len(category), len(assess))


if __name__ == "__main__":
    unittest.main()
