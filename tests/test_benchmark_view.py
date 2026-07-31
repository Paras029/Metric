"""The benchmark as it appears on screen.

Two things are being pinned. The first is that the page narrows to what a reviewer can act on: a
benchmark of three hundred scenarios is triaged, not read, so the default view is whatever is
asking for a decision, and the page always says how much it is not showing.

The second is what must never appear. The registry holds the decision path, the seeded state and
the expected outcome, and those are the answer key for a pack issued to the team that built the
agent. They are not secret from the validation team, but a page that puts them beside the
scenario text is a page somebody eventually screenshots into an email, and the exercise stops
measuring anything at that point.
"""
import unittest

from scenario_generator.core.models import (Decision, IntakeData, Persona, Scenario, State, Step,
                                            Tool, TurnMeta)
from scenario_generator.webapp.scenarios import PAGE_SIZE, build_rows, to_row

_PERSONA = Persona("P1", "Cardholder", [], True)

_INTAKE = IntakeData(
    use_case={"Use case name": "Disputes", "Business objective": "Resolve disputes"},
    personas=[_PERSONA],
    capabilities=[],
    decisions=[Decision("DEC-01", "Identity", "CAP-01", "", ["Pass", "Fail"])],
    states=[State("S-00", "Start", "Start", ["DEC-01"], False)],
    tools=[Tool("Identity service", "CAP-01", True)],
)


def _scenario(identifier, **overrides):
    scenario = Scenario(
        id=identifier,
        path=[Step("DEC-01", "Pass", "S-01")],
        category="Happy Path",
        persona=_PERSONA,
        seeded_state="S-00",
        termination="The dispute is filed and the cardholder is told the reference number.",
        capabilities=["CAP-01"],
        tools=["Identity service"],
        touches_state_change=True,
        turn_meta=[TurnMeta(1, "DEC-01", "Identity", "Pass", "Identity service", "S-01")],
        description="The cardholder disputes a charge and is asked to confirm who they are.",
        turn_plan="1. Open the conversation.",
    )
    for name, value in overrides.items():
        setattr(scenario, name, value)
    return scenario


class TestWhatTheRowShows(unittest.TestCase):
    def test_the_expected_outcome_never_reaches_the_row(self):
        row = to_row(_scenario("SC-001"))
        self.assertNotIn("reference number", " ".join(str(v) for v in row.values()))

    def test_the_decision_path_never_reaches_the_row(self):
        row = to_row(_scenario("SC-001"))
        self.assertNotIn("DEC-01", " ".join(str(v) for v in row.values()))

    def test_the_title_is_the_scenario_own_first_sentence(self):
        row = to_row(_scenario("SC-001"))
        self.assertEqual(row["title"],
                         "The cardholder disputes a charge and is asked to confirm who they are")

    def test_a_scenario_with_no_text_yet_is_still_identifiable(self):
        row = to_row(_scenario("SC-001", description=""))
        self.assertIn("SC-001", row["title"])

    def test_a_long_first_sentence_is_cut_rather_than_wrapped(self):
        row = to_row(_scenario("SC-001", description="The cardholder " + "and again " * 40))
        self.assertLessEqual(len(row["title"]), 110)


class TestWhereTheTierCameFrom(unittest.TestCase):
    """Three passes can set materiality and the precedence is not obvious from the number alone."""

    def test_the_first_assessment_is_attributed(self):
        row = to_row(_scenario("SC-001", materiality="Medium",
                               materiality_rationale="Routine."))
        self.assertEqual(row["materiality"], "Medium")
        self.assertEqual(row["materiality_source"], "the materiality pass")
        self.assertEqual(row["materiality_reason"], "Routine.")

    def test_the_review_outranks_the_first_assessment(self):
        row = to_row(_scenario("SC-001", materiality="Medium", review_materiality="High",
                               review_rationale="Touches money."))
        self.assertEqual(row["materiality"], "High")
        self.assertEqual(row["materiality_source"], "the final review")
        self.assertEqual(row["materiality_reason"], "Touches money.")

    def test_a_human_override_outranks_both(self):
        row = to_row(_scenario("SC-001", materiality="Medium", review_materiality="High",
                               materiality_override="Critical"))
        self.assertEqual(row["materiality"], "Critical")
        self.assertEqual(row["materiality_source"], "your override")
        self.assertTrue(row["overridden"])


class TestWhichScenariosAreShown(unittest.TestCase):
    def _mixed(self):
        return [
            _scenario("SC-001", materiality="Low"),
            _scenario("SC-002", materiality="Critical"),
            _scenario("SC-003", materiality="Medium", review_flag="Redundant"),
            _scenario("SC-004", materiality="Medium", origin="llm-proposed"),
            _scenario("SC-005", materiality="Medium", owner_coverage="Covered"),
        ]

    def test_the_default_view_is_what_needs_a_decision(self):
        result = build_rows(self._mixed(), "attention")
        self.assertEqual({r["id"] for r in result["rows"]}, {"SC-003", "SC-004", "SC-005"})

    def test_it_falls_back_to_the_whole_benchmark_when_nothing_is_flagged(self):
        """An empty list would read as 'no scenarios', which is the opposite of the truth."""
        result = build_rows([_scenario("SC-001"), _scenario("SC-002")], "attention")
        self.assertEqual(result["view"], "all")
        self.assertEqual(len(result["rows"]), 2)

    def test_the_high_view_takes_the_top_two_tiers(self):
        result = build_rows(self._mixed(), "high")
        self.assertEqual({r["id"] for r in result["rows"]}, {"SC-002"})

    def test_the_most_material_scenarios_come_first(self):
        result = build_rows(self._mixed(), "all")
        self.assertEqual(result["rows"][0]["id"], "SC-002")

    def test_the_page_says_what_it_is_not_showing(self):
        scenarios = [_scenario(f"SC-{n:03d}") for n in range(PAGE_SIZE + 20)]
        result = build_rows(scenarios, "all")
        self.assertEqual(result["shown"], PAGE_SIZE)
        self.assertEqual(result["total"], PAGE_SIZE + 20)
        self.assertGreater(result["selected"], result["shown"])

    def test_the_counts_are_of_the_whole_benchmark_not_the_page(self):
        result = build_rows(self._mixed(), "high")
        self.assertEqual(result["total"], 5)
        self.assertEqual(result["attention"], 3)


class TestFilteringAndPageSize(unittest.TestCase):
    def _mixed(self):
        return [
            _scenario("SC-001", materiality="Low"),
            _scenario("SC-002", materiality="Critical"),
            _scenario("SC-003", materiality="Medium", review_flag="Redundant"),
            _scenario("SC-004", materiality="Medium", origin="llm-proposed"),
            _scenario("SC-005", materiality="Medium", owner_coverage="Covered"),
        ]

    def test_a_filter_narrows_within_the_view_rather_than_replacing_it(self):
        result = build_rows(self._mixed(), "all", filters={"materiality": "Medium"})
        self.assertEqual({r["id"] for r in result["rows"]}, {"SC-003", "SC-004", "SC-005"})
        self.assertEqual(result["active_filters"], {"materiality": "Medium"})

    def test_an_unknown_filter_value_matches_nothing_rather_than_erroring(self):
        result = build_rows(self._mixed(), "all", filters={"materiality": "Nonexistent"})
        self.assertEqual(result["rows"], [])

    def test_filter_options_reflect_the_view_not_the_whole_benchmark(self):
        """Choosing between personas should offer what the current view actually has."""
        result = build_rows(self._mixed(), "high")
        self.assertEqual(result["rows"][0]["id"], "SC-002")
        # Only one materiality value survives the "high" view, so it is not offered as a choice.
        self.assertNotIn("materiality", result["filter_options"])

    def test_a_column_the_stage_has_not_produced_is_never_offered(self):
        result = build_rows(self._mixed(), "all", stage="text")
        self.assertNotIn("materiality", result["filter_options"])
        self.assertNotIn("coverage", result["filter_options"])

    def test_a_single_valued_column_is_not_offered_as_a_filter(self):
        """A dropdown that can only narrow to everything is not a filter."""
        same_origin = [_scenario(f"SC-{n:03d}") for n in range(3)]
        result = build_rows(same_origin, "all")
        self.assertNotIn("origin", result["filter_options"])

    def test_limit_caps_the_rendered_rows_without_changing_the_counts(self):
        scenarios = [_scenario(f"SC-{n:03d}") for n in range(10)]
        result = build_rows(scenarios, "all", limit=3)
        self.assertEqual(len(result["rows"]), 3)
        self.assertEqual(result["shown"], 3)
        self.assertEqual(result["selected"], 10)
        self.assertEqual(result["total"], 10)

    def test_limit_none_shows_everything_selected(self):
        scenarios = [_scenario(f"SC-{n:03d}") for n in range(PAGE_SIZE + 5)]
        result = build_rows(scenarios, "all", limit=None)
        self.assertEqual(len(result["rows"]), PAGE_SIZE + 5)
        self.assertEqual(result["shown"], PAGE_SIZE + 5)

    def test_the_default_page_size_is_fifty(self):
        self.assertEqual(PAGE_SIZE, 50)


if __name__ == "__main__":
    unittest.main()
