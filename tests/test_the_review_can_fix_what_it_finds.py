"""A flag on its own is advice nobody acts on.

Text the review calls under-specified is issued to the model owner exactly as it stands -- nothing
after the review reads it again -- so a pass that can see what is missing and cannot write it down
leaves the defect in the pack it just described.

The other half is what the pass is given to judge against. Without the walk's per-turn
expectations the only question it could ask about a turn plan was whether the prose reads clearly,
and "under-specified" collapsed into a matter of taste. With them there is a fact to check against:
the route says turn three lands on DEC-04=Declined, so turn three of the plan has to be the tester
doing the thing that gets it declined.
"""
import json
import unittest
from pathlib import Path

from scenario_generator.core.intake import read_intake
from scenario_generator.core.models import Scenario
from scenario_generator.llm import prompt_loader
from scenario_generator.llm.context import hardest_routes
from scenario_generator.llm.reviewer import ScenarioReviewer
from scenario_generator.pipeline import build_scenario_space

EXAMPLE = (Path(__file__).resolve().parent.parent
           / "examples" / "intakes" / "3_onboarding_deep_with_retries.xlsx")


def _reviewer(reply: dict, seen: list):
    """A reviewer whose assess sweep returns `reply` and records the prompt it was sent."""
    def complete(system, user, **kwargs):
        seen.append(user)
        if '"proposals"' in user or "WHICH FULL JOURNEYS" in user:
            return json.dumps({"proposals": []})
        if "category" in user.lower() and "revised_turn_plan" not in user:
            return json.dumps({})
        return json.dumps(reply)
    return ScenarioReviewer(complete=complete, batch_size=50)


class TestWhatTheAssessPassIsShown(unittest.TestCase):
    def setUp(self):
        self.intake = read_intake(str(EXAMPLE))
        self.scenarios = build_scenario_space(self.intake, with_probes=False)[:3]

    def test_the_walk_is_in_the_payload(self):
        seen = []
        _reviewer({}, seen).review(self.scenarios, self.intake)
        assess = next(u for u in seen if "what_each_turn_must_induce" in u)
        walked = self.scenarios[0].turn_meta[0]
        self.assertIn(walked.expected_variant, assess)
        self.assertIn("must_land_on", assess)

    def test_a_turn_the_walk_did_not_determine_is_not_offered_as_ground_truth(self):
        """A proposal's placeholder turn meta is a restatement of its own text. Listing it as
        ground truth would invite the pass to check a plan against itself."""
        from scenario_generator.llm.reviewer import _expected_turns
        loose = Scenario(id="LP-001", path=[], category="Fallback",
                         persona=self.intake.personas[0], seeded_state="", termination="",
                         capabilities=[], tools=[], touches_state_change=False)
        self.assertEqual(_expected_turns(loose), [])


class TestTheRepairIsApplied(unittest.TestCase):
    def setUp(self):
        self.intake = read_intake(str(EXAMPLE))
        self.scenarios = build_scenario_space(self.intake, with_probes=False)[:2]
        for scenario in self.scenarios:
            scenario.description = "Old description."
            scenario.turn_plan = "1. Do the thing."

    def _review(self, entry: dict):
        first = self.scenarios[0]
        _reviewer({first.id: entry}, []).review(self.scenarios, self.intake)
        return first

    def test_a_flagged_scenario_is_rewritten(self):
        ruled = self._review({"materiality": "High", "rationale": "Turn 2 names no value.",
                              "flag": "Under-specified",
                              "revised_description": "A cardmember whose date of birth does not "
                                                     "match the record on file.",
                              "revised_turn_plan": "1. Give the account number.\n2. Give a date "
                                                   "of birth that does not match."})
        self.assertIn("does not match", ruled.description)
        self.assertIn("2. Give a date of birth", ruled.turn_plan)
        self.assertEqual(ruled.review_revised, "description, turn plan")

    def test_the_flag_stays_on_so_the_judgement_is_still_visible(self):
        """A rewrite with the flag cleared hides the finding behind the fix."""
        ruled = self._review({"materiality": "High", "rationale": "r", "flag": "Under-specified",
                              "revised_description": "Rewritten.", "revised_turn_plan": ""})
        self.assertEqual(ruled.review_flag, "Under-specified")
        self.assertEqual(ruled.review_revised, "description")

    def test_text_it_had_no_complaint_about_is_left_alone(self):
        """Rewriting what it did not flag is the pass quietly restyling the pack, which is a
        different and much larger thing than fixing what it found."""
        ruled = self._review({"materiality": "High", "rationale": "r", "flag": "",
                              "revised_description": "Rewritten.",
                              "revised_turn_plan": "1. Rewritten."})
        self.assertEqual(ruled.description, "Old description.")
        self.assertEqual(ruled.review_revised, "")

    def test_an_echo_of_the_existing_text_is_not_recorded_as_a_revision(self):
        ruled = self._review({"materiality": "High", "rationale": "r", "flag": "Redundant",
                              "revised_description": "Old description.",
                              "revised_turn_plan": "1. Do the thing."})
        self.assertEqual(ruled.review_revised, "")

    def test_a_flag_with_no_repair_still_works_as_it_did(self):
        ruled = self._review({"materiality": "Low", "rationale": "Duplicated by SC-002.",
                              "flag": "Redundant"})
        self.assertEqual(ruled.review_flag, "Redundant")
        self.assertEqual(ruled.review_revised, "")
        self.assertEqual(ruled.description, "Old description.")


class TestTheHardRoutesGivenToTheProposalPass(unittest.TestCase):
    """Asked for a difficult journey and given only the digest, the pass writes the happy path
    joined end to end -- that is the journey a list of one-line summaries makes visible."""

    def setUp(self):
        self.intake = read_intake(str(EXAMPLE))
        self.scenarios = build_scenario_space(self.intake, with_probes=False)
        self.said = hardest_routes(self.scenarios, self.intake)

    def test_it_names_routes_by_id_so_a_journey_can_say_what_it_chained(self):
        listed = [s.id for s in self.scenarios if s.id in self.said]
        self.assertTrue(listed)

    def test_a_route_that_retries_is_marked_as_one(self):
        retrying = [s for s in self.scenarios
                    if len({step.decision_id for step in s.path}) < len(s.path)]
        self.assertTrue(retrying, "the example no longer has a retry loop")
        self.assertIn("retries", self.said)

    def test_the_longest_route_in_a_block_is_offered(self):
        blocked = [s for s in self.scenarios if s.capability_id and s.path]
        longest = max(blocked, key=lambda s: len(s.path))
        self.assertIn(longest.id, self.said)

    def test_probes_are_not_offered_as_pieces_of_a_journey(self):
        """A probe is path-independent; it is not a route anything can be chained onto."""
        with_probes = build_scenario_space(self.intake, with_probes=True)
        said = hardest_routes(with_probes, self.intake)
        for probe in (s for s in with_probes if s.is_probe):
            self.assertNotIn(probe.id, said)

    def test_it_reaches_the_prompt(self):
        rendered = prompt_loader.render(
            "reviewer.propose", owner="", total=1, digest="d", limit=3, blocks="b",
            hard=self.said, categories="c", materiality="m", cds="")
        self.assertIn("THE HARDEST ROUTES ALREADY WALKED", rendered)
        self.assertIn("retries", rendered)
        self.assertIn("happy path end to end is not worth proposing", rendered)


if __name__ == "__main__":
    unittest.main()
