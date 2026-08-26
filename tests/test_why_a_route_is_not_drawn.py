"""When a scenario's decision path does not light up in the drawing, and why.

The reported symptom was that some scenarios -- proposals from the review especially -- showed no
route in the tree, with the suspicion that the drawing could not light a scenario spanning two
capabilities. It can: a route crossing three blocks lights every hop of it, and that is asserted
below so the question stays answered.

What could not be lit was a proposed path that is not a route. Each pair in it is declared, so
nothing rejects it, but the sequence skips a step: DEC-01 then DEC-03, where the state DEC-01
lands on offers DEC-02. The drawing only has arrows the declaration has, so a hop the declaration
does not have is drawn as nothing at all -- and a reader sees a scenario with no route rather than
a route with a hole in it. The gap is now named on the scenario instead of being left to show as
an absence.
"""
import unittest
from pathlib import Path

from scenario_generator.core.graph import DecisionGraph
from scenario_generator.core.intake import read_intake
from scenario_generator.core.proposals import instantiate_proposals
from scenario_generator.pipeline import build_scenario_space
from scenario_generator.webapp.graphview import routes

EXAMPLE = (Path(__file__).resolve().parent.parent
           / "examples" / "intakes" / "1_disputes_three_blocks.xlsx")


def _proposal(intake, path, **overrides):
    entry = {"title": "A journey", "description": "Something worth testing.",
             "turn_plan": "\n".join(f"{i}. Say something." for i in range(1, len(path) + 2)),
             "turns": len(path), "expected_outcome": "Internal.", "decision_path": path,
             "capabilities": ["CAP-01", "CAP-02", "CAP-03"], "materiality": "High",
             "rationale": "Chains two hard routes."}
    entry.update(overrides)
    return instantiate_proposals([entry], intake)[0]


class TestAJourneyAcrossBlocksIsDrawn(unittest.TestCase):
    """The hypothesis worth ruling out: that the drawing cannot light a route crossing blocks."""

    def setUp(self):
        self.intake = read_intake(str(EXAMPLE))
        graph = DecisionGraph(self.intake.decisions, self.intake.states)
        states = {s.id: s for s in self.intake.states}
        at, self.path = graph.start_states[0], []
        while states.get(at) and states[at].next_decisions:
            decision = graph.decisions[states[at].next_decisions[0]]
            self.path.append({"decision_id": decision.id, "variant": decision.variants[0]})
            at = graph.successor(decision.id, decision.variants[0])

    def test_the_route_used_here_really_does_cross_blocks(self):
        owner = {d.id: d.trigger_capability for d in self.intake.decisions}
        crossed = {owner[step["decision_id"]] for step in self.path}
        self.assertGreater(len(crossed), 1, "this test no longer tests what it says it does")

    def test_every_hop_of_it_is_lit(self):
        journey = _proposal(self.intake, self.path)
        journey.id = "LP-900"
        lit = routes(self.intake, build_scenario_space(self.intake, with_probes=False) + [journey])
        self.assertIn(journey.id, lit)
        self.assertEqual(len(lit[journey.id]["edges"]), len(self.path))

    def test_it_is_not_flagged_for_a_gap_it_does_not_have(self):
        self.assertEqual(_proposal(self.intake, self.path).review_flag, "")


class TestAPathThatSkipsAStep(unittest.TestCase):
    def setUp(self):
        self.intake = read_intake(str(EXAMPLE))
        # Each pair is declared. The sequence is not a route: DEC-01=Dispute lands on a state
        # that offers DEC-02, not DEC-03.
        self.skipping = _proposal(self.intake, [
            {"decision_id": "DEC-01", "variant": "Dispute"},
            {"decision_id": "DEC-03", "variant": "Verified"}])

    def test_the_break_is_named_rather_than_left_to_show_as_an_absence(self):
        self.assertIn("DEC-03", self.skipping.review_rationale)
        self.assertIn("not a route", self.skipping.review_rationale)

    def test_it_is_flagged_so_it_lands_where_everything_else_needing_a_look_lands(self):
        self.assertEqual(self.skipping.review_flag, "Under-specified")

    def test_the_note_a_reader_sees_says_it_too(self):
        self.assertIn("skips a step", self.skipping.proposed_rationale)
        self.assertIn("Chains two hard routes", self.skipping.proposed_rationale)

    def test_the_steps_are_kept_rather_than_bridged_or_truncated(self):
        """Bridging invents steps nobody proposed; truncating turns a journey into a fragment
        while its description still talks about the whole thing."""
        self.assertEqual([s.decision_id for s in self.skipping.path], ["DEC-01", "DEC-03"])

    def test_the_drawing_lights_what_the_declaration_has_and_no_more(self):
        self.skipping.id = "LP-901"
        lit = routes(self.intake,
                     build_scenario_space(self.intake, with_probes=False) + [self.skipping])
        self.assertLess(len(lit[self.skipping.id]["edges"]), len(self.skipping.path) + 1)


class TestEveryWalkedRouteIsFullyDrawn(unittest.TestCase):
    """The walk's own scenarios are routes by construction, so any of them lighting short is a
    fault in the drawing rather than in the scenario."""

    def test_no_walked_scenario_lights_fewer_arrows_than_it_has_steps(self):
        for name in ("1_disputes_three_blocks", "3_onboarding_deep_with_retries",
                     "5_wide_chain_scale"):
            intake = read_intake(str(EXAMPLE.parent / f"{name}.xlsx"))
            space = build_scenario_space(intake, with_probes=False)
            lit = routes(intake, space)
            for scenario in space:
                with self.subTest(intake=name, scenario=scenario.id):
                    self.assertIn(scenario.id, lit)
                    self.assertGreaterEqual(len(lit[scenario.id]["edges"]), len(scenario.path))


if __name__ == "__main__":
    unittest.main()
