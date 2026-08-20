"""The shipped example intakes are real inputs, so they are checked like inputs.

An example workbook that stops parsing, or that quietly stops demonstrating the thing it was
built to demonstrate, is worse than none: it is the first thing somebody runs, and the numbers
beside it in CHECKS.md are what they compare against.
"""
import unittest
from pathlib import Path

from metric.domain.graph import DecisionGraph, enumerate_by_span, spans_for
from metric.domain.intake import read_intake
from metric.pipeline import build_scenario_space

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "intakes"


def _counts(name: str):
    intake = read_intake(str(EXAMPLES / name))
    graph = DecisionGraph(intake.decisions, intake.states)
    whole = sum(len(w) + len(a) for _, w, a in enumerate_by_span(graph, []))
    return intake, graph, whole, build_scenario_space(intake, with_probes=False)


class TestEveryExampleIsAValidIntake(unittest.TestCase):
    def test_they_all_parse_and_enumerate(self):
        for path in sorted(EXAMPLES.glob("*.xlsx")):
            with self.subTest(example=path.name):
                _, _, _, scenarios = _counts(path.name)
                self.assertTrue(scenarios, f"{path.name} produced no scenarios at all")


class TestTheNumbersInTheNotesAreTheNumbers(unittest.TestCase):
    """CHECKS.md quotes these. A drift between the two is a document that lies."""

    def test_the_canonical_example(self):
        _, _, _, scenarios = _counts("1_disputes_three_blocks.xlsx")
        self.assertEqual(len(scenarios), 14)
        per = {}
        for scenario in scenarios:
            per.setdefault(scenario.capability_id, []).append(scenario)
        self.assertEqual({k: len(v) for k, v in sorted(per.items())},
                         {"CAP-01": 4, "CAP-02": 6, "CAP-03": 4})

    def test_the_block_entered_twice_produces_two_sets(self):
        _, _, _, scenarios = _counts("1_disputes_three_blocks.xlsx")
        verification = {s.precondition for s in scenarios if s.capability_id == "CAP-02"}
        self.assertEqual(len(verification), 2)

    def test_an_undivided_intake_walks_whole(self):
        _, _, whole, scenarios = _counts("2_travel_no_spans.xlsx")
        self.assertEqual(len(scenarios), whole)
        self.assertEqual({s.capability_id for s in scenarios}, {""})

    def test_the_deep_example_keeps_its_loop_inside_one_block(self):
        _, _, _, scenarios = _counts("3_onboarding_deep_with_retries.xlsx")
        self.assertEqual(len(scenarios), 16)
        for scenario in scenarios:
            blocks = {step.decision_id[:5] for step in scenario.path}
            self.assertLessEqual(len(blocks), 2, f"{scenario.id} left its block")

    def test_a_capability_with_no_exit_contributes_nothing(self):
        intake, graph, _, scenarios = _counts("4_spans_drawn_wrongly.xlsx")
        self.assertNotIn("CAP-02", {s.capability_id for s in scenarios})
        self.assertNotIn("CAP-02", {s.capability_id for s in spans_for(graph, intake.capabilities)})

    def test_a_span_naming_a_missing_state_keeps_the_rest_of_itself(self):
        intake, graph, _, _ = _counts("4_spans_drawn_wrongly.xlsx")
        first = next(s for s in spans_for(graph, intake.capabilities)
                     if s.capability_id == "CAP-01")
        self.assertEqual(first.exit_states, frozenset({"S-01"}))

    def test_the_scale_example_is_the_whole_argument(self):
        _, _, whole, scenarios = _counts("5_wide_chain_scale.xlsx")
        self.assertEqual((whole, len(scenarios)), (200, 29))


class TestTheCaveatIsTrueToo(unittest.TestCase):
    """CHECKS.md says a narrow agent gets slightly worse. If that stops being true, the note is
    wrong and somebody will read a number as a regression."""

    def test_a_narrow_graph_costs_slightly_more_divided(self):
        _, _, whole, scenarios = _counts("4_spans_drawn_wrongly.xlsx")
        self.assertGreater(len(scenarios), whole)


if __name__ == "__main__":
    unittest.main()
