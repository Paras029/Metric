"""The enumeration is the one part of this tool nothing downstream can audit.

Three hundred routes over a forty-decision graph is not something anybody reads for correctness,
and both ways it can be wrong are silent: an impossible route looks like a scenario, and a missing
route looks like nothing at all. So the check has to be shown to *catch* things, not merely to
pass on a declaration that was already sound. Most of what is below injects a fault and asserts it
is found, and names which of the four checks found it.

The checks share no code with the walk. Re-using walk_paths to check walk_paths would only prove
it agrees with itself.
"""
import dataclasses
import unittest
from pathlib import Path

from scenario_generator.core.intake import read_intake
from scenario_generator.core.models import Step
from scenario_generator.core.verify import check_walk
from scenario_generator.pipeline import build_scenario_space

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "intakes"
SOUND = ("1_disputes_three_blocks", "2_travel_no_spans",
         "3_onboarding_deep_with_retries", "5_wide_chain_scale")


def _space(name: str):
    intake = read_intake(str(EXAMPLES / f"{name}.xlsx"))
    return intake, build_scenario_space(intake, with_probes=False)


class TestASoundDeclarationChecksOut(unittest.TestCase):
    """The first thing a check has to be is quiet where there is nothing to report."""

    def test_every_route_replays(self):
        for name in SOUND:
            intake, scenarios = _space(name)
            report = check_walk(intake, scenarios)
            with self.subTest(intake=name):
                self.assertTrue(report.sound,
                                [str(f) for f in report.unreplayable + report.misplaced
                                 + report.duplicates])
                self.assertEqual(report.replayed, report.routes)

    def test_every_declared_outcome_is_exercised(self):
        for name in SOUND:
            intake, scenarios = _space(name)
            report = check_walk(intake, scenarios)
            with self.subTest(intake=name):
                self.assertTrue(report.complete, report.outcomes_missing)

    def test_every_declared_state_is_reached(self):
        for name in SOUND:
            intake, scenarios = _space(name)
            report = check_walk(intake, scenarios)
            with self.subTest(intake=name):
                self.assertEqual(report.states_missing, [])

    def test_the_independent_count_agrees_with_the_walk(self):
        """Counted by a plain recursion over the declaration, sharing no code with the walk."""
        for name in SOUND:
            intake, scenarios = _space(name)
            report = check_walk(intake, scenarios)
            with self.subTest(intake=name):
                self.assertEqual(report.count_disagreements, [])

    def test_a_capability_entered_two_ways_is_not_a_duplicate(self):
        """The same decisions walked from two entry positions are two scenarios, which is the
        case the span machinery exists to separate."""
        intake, scenarios = _space("1_disputes_three_blocks")
        report = check_walk(intake, scenarios)
        self.assertEqual(report.duplicates, [])


class TestItCatchesAnImpossibleRoute(unittest.TestCase):
    """Each fault, and the check that finds it."""

    def setUp(self):
        self.intake, self.base = _space("1_disputes_three_blocks")

    def _with(self, change):
        scenarios = [dataclasses.replace(s) for s in self.base]
        for scenario in scenarios:
            scenario.path = list(scenario.path)
        change(scenarios)
        return check_walk(self.intake, scenarios)

    def test_an_outcome_the_declaration_never_states(self):
        report = self._with(lambda s: s[2].path.__setitem__(
            0, Step(s[2].path[0].decision_id, "Invented", s[2].path[0].next_state)))
        self.assertTrue(any("no declared outcome" in f.problem for f in report.unreplayable),
                        [str(f) for f in report.unreplayable])

    def test_a_step_the_previous_state_does_not_offer(self):
        report = self._with(lambda s: s[4].path.append(Step("DEC-06", "Filed", "S-11")))
        self.assertTrue(any("could not continue" in f.problem for f in report.unreplayable),
                        [str(f) for f in report.unreplayable])

    def test_a_route_that_stops_in_the_middle(self):
        def cut(scenarios):
            last = scenarios[1].path[-1]
            scenarios[1].path[-1] = Step(last.decision_id, last.variant, "S-05")
        report = self._with(cut)
        self.assertTrue(any("hands on anywhere declared" in f.problem
                            for f in report.unreplayable),
                        [str(f) for f in report.unreplayable])

    def test_a_decision_taken_more_often_than_declared(self):
        """Walked around a legal retry loop once more than the declaration allows, so every step
        is still offered and only the attempt count is wrong. That is the case the other rules
        cannot see."""
        intake, base = _space("3_onboarding_deep_with_retries")
        looping = next(s for s in base
                       if sum(1 for step in s.path if step.decision_id == "DEC-04") > 1)
        over = dataclasses.replace(looping)
        cycle = [step for step in looping.path
                 if step.decision_id in ("DEC-03", "DEC-04")][:2]
        over.path = list(looping.path[:-1]) + cycle * 2 + [looping.path[-1]]
        report = check_walk(intake, [over])
        self.assertTrue(any("attempt" in f.problem for f in report.unreplayable),
                        [str(f) for f in report.unreplayable])

    def test_a_landing_state_the_declaration_does_not_have(self):
        def stray(scenarios):
            last = scenarios[3].path[-1]
            scenarios[3].path[-1] = Step(last.decision_id, last.variant, "S-404")
        report = self._with(stray)
        self.assertTrue(any("not a declared state" in f.problem for f in report.unreplayable),
                        [str(f) for f in report.unreplayable])


class TestItCatchesAMissingRoute(unittest.TestCase):
    """The harder half. An impossible route is visible in what is there; a missing one is only
    visible against what should have been."""

    def setUp(self):
        self.intake, self.base = _space("1_disputes_three_blocks")

    def test_a_dropped_route_shows_up_as_a_count_disagreement(self):
        thinner = [s for s in self.base if s.id != self.base[3].id]
        report = check_walk(self.intake, thinner)
        self.assertTrue(report.count_disagreements, "a dropped route went unnoticed")
        self.assertIn("the walk produced", report.count_disagreements[0])

    def test_a_dropped_route_shows_up_as_an_unexercised_outcome(self):
        """The second, independent way the same fault surfaces."""
        thinner = [s for s in self.base if s.id != self.base[3].id]
        report = check_walk(self.intake, thinner)
        self.assertFalse(report.complete)
        self.assertTrue(report.outcomes_missing)

    def test_a_whole_capability_left_unwalked_is_named(self):
        kept = [s for s in self.base if s.capability_id != "CAP-02"]
        report = check_walk(self.intake, kept)
        self.assertIn("CAP-02", report.capabilities_empty)


class TestItCatchesAMisfiledRoute(unittest.TestCase):
    def setUp(self):
        self.intake, self.base = _space("1_disputes_three_blocks")

    def test_a_route_filed_under_a_capability_it_never_walks(self):
        scenarios = [dataclasses.replace(s) for s in self.base]
        moved = next(s for s in scenarios if s.capability_id == "CAP-03")
        moved.capability_id = "CAP-01"
        report = check_walk(self.intake, scenarios)
        self.assertTrue(any("walks no decision tagged with it" in f.problem
                            for f in report.misplaced), [str(f) for f in report.misplaced])


class TestTheDeliberatelyBrokenExample(unittest.TestCase):
    """4_spans_drawn_wrongly exists to be wrong. The check has to say how, and say nothing else."""

    def test_it_reports_the_span_that_covers_another_capability(self):
        intake, scenarios = _space("4_spans_drawn_wrongly")
        report = check_walk(intake, scenarios)
        self.assertFalse(report.sound)
        self.assertTrue(any("CAP-04" in f.problem or f.scenario_id for f in report.misplaced))

    def test_it_does_not_report_a_route_the_walk_produced_for_an_unplaceable_span(self):
        """A capability whose span cannot be placed is walked as a gap instead, and the routes
        that come out of that are legitimate. Reporting them would blame the walk for a hole in
        the declaration."""
        intake, scenarios = _space("4_spans_drawn_wrongly")
        report = check_walk(intake, scenarios)
        self.assertEqual(report.unreplayable, [],
                         [str(f) for f in report.unreplayable])


if __name__ == "__main__":
    unittest.main()
