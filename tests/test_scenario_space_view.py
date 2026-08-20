"""The scenario space as it appears on screen.

Two things are being pinned. The first is that the page narrows to what a reviewer can act on: a
scenario space of three hundred scenarios is triaged, not read, so the default view is whatever is
asking for a decision, and the page always says how much it is not showing.

The second is what must never appear. The scenario space metadata holds the decision path, the seeded state and
the expected outcome, and those are the answer key for a pack issued to the model owner. They
are not secret from the validator, but a page that puts them beside the
scenario text is a page somebody eventually screenshots into an email, and the exercise stops
measuring anything at that point.
"""
import unittest

from metric.domain.models import (Decision, IntakeData, Persona, Scenario, State, Step,
                                            Tool, TurnMeta)
from metric.web.scenariolist import PAGE_SIZE, build_rows, to_row

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


class TestWhichTierIsInForce(unittest.TestCase):
    """Three passes can set materiality, and the card shows one tier with one reason behind it."""

    def test_the_first_assessment_stands_until_something_replaces_it(self):
        row = to_row(_scenario("SC-001", materiality="Medium",
                               materiality_rationale="Routine."))
        self.assertEqual(row["materiality"], "Medium")
        self.assertEqual(row["materiality_reason"], "Routine.")

    def test_the_review_outranks_the_first_assessment(self):
        row = to_row(_scenario("SC-001", materiality="Medium", review_materiality="High",
                               review_rationale="Touches money."))
        self.assertEqual(row["materiality"], "High")
        self.assertEqual(row["materiality_reason"], "Touches money.")

    def test_a_human_override_outranks_both(self):
        row = to_row(_scenario("SC-001", materiality="Medium", review_materiality="High",
                               materiality_override="High"))
        self.assertEqual(row["materiality"], "High")
        self.assertTrue(row["overridden"])

    def test_a_flag_does_not_repeat_the_reason_already_given_for_the_tier(self):
        """The review settles the tier and raises the flag in one call and returns one rationale.

        Rendered as two notes that is the same sentence printed twice, which reads as a rendering
        fault rather than as the two verdicts agreeing.
        """
        row = to_row(_scenario("SC-001", materiality="Medium", review_materiality="Low",
                               review_flag="Under-specified",
                               review_rationale="The intake never says how many attempts."))
        self.assertEqual(row["materiality_reason"], "The intake never says how many attempts.")
        self.assertEqual(row["flag_reason"], "")
        self.assertTrue(row["flag_shares_reason"])

    def test_a_flag_that_says_something_new_keeps_its_own_note(self):
        row = to_row(_scenario("SC-001", materiality="Medium",
                               materiality_rationale="Routine account question.",
                               review_flag="Redundant",
                               review_rationale="Materially the same walk as SC-004."))
        self.assertEqual(row["materiality_reason"], "Routine account question.")
        self.assertEqual(row["flag_reason"], "Materially the same walk as SC-004.")
        self.assertFalse(row["flag_shares_reason"])

    def test_which_pass_set_the_tier_is_not_carried_onto_the_card(self):
        """A reviewer asks why this is Critical, not which pass decided it was.

        The provenance used to be printed in front of every rationale, which put a sentence
        answering nobody's question between the tier and the reason for it. Every pass keeps its
        own column in the scenario space metadata, so nothing is lost for anyone auditing how a tier was reached.
        """
        row = to_row(_scenario("SC-001", materiality="Medium", review_materiality="High",
                               review_rationale="Touches money."))
        self.assertNotIn("materiality_source", row)
        self.assertNotIn("materiality pass", " ".join(str(v) for v in row.values()))


class TestWhichScenariosAreShown(unittest.TestCase):
    """The grid is the only selector. It replaced three fixed view tabs and five dropdowns, and
    the reason is that neither could express "these two cells", which is the thing somebody
    triaging a space actually wants."""

    def _mixed(self):
        return [
            _scenario("SC-001", materiality="Low"),
            _scenario("SC-002", materiality="High"),
            _scenario("SC-003", materiality="Medium", review_flag="Redundant"),
            _scenario("SC-004", materiality="Medium", origin="llm-proposed"),
            _scenario("SC-005", materiality="Medium", owner_coverage="Covered"),
        ]

    def test_picking_nothing_shows_the_whole_space(self):
        """What the "all" tab was for, without a tab."""
        result = build_rows(self._mixed())
        self.assertEqual(len(result["rows"]), 5)

    def test_one_cell_narrows_to_it(self):
        result = build_rows(self._mixed(), cells=[("Happy Path", "High")])
        self.assertEqual({r["id"] for r in result["rows"]}, {"SC-002"})

    def test_two_cells_are_a_union_rather_than_an_intersection(self):
        """The only reading of "I clicked two things" that anybody means."""
        result = build_rows(self._mixed(),
                            cells=[("Happy Path", "High"), ("Happy Path", "Low")])
        self.assertEqual({r["id"] for r in result["rows"]}, {"SC-001", "SC-002"})

    def test_a_column_header_takes_every_category_at_that_tier(self):
        result = build_rows(self._mixed(), cells=[("*", "Medium")])
        self.assertEqual({r["id"] for r in result["rows"]}, {"SC-003", "SC-004", "SC-005"})

    def test_a_row_header_takes_every_tier_of_that_category(self):
        result = build_rows(self._mixed(), cells=[("Happy Path", "*")])
        self.assertEqual(len(result["rows"]), 5)

    def test_a_cell_nothing_falls_in_shows_nothing_rather_than_erroring(self):
        result = build_rows(self._mixed(), cells=[("Termination", "High")])
        self.assertEqual(result["rows"], [])

    def test_the_most_material_scenarios_come_first(self):
        self.assertEqual(build_rows(self._mixed())["rows"][0]["id"], "SC-002")

    def test_the_page_says_what_it_is_not_showing(self):
        scenarios = [_scenario(f"SC-{n:03d}") for n in range(PAGE_SIZE + 20)]
        result = build_rows(scenarios)
        self.assertEqual(result["shown"], PAGE_SIZE)
        self.assertEqual(result["total"], PAGE_SIZE + 20)
        self.assertGreater(result["selected"], result["shown"])

    def test_the_counts_are_of_the_whole_space_not_the_selection(self):
        result = build_rows(self._mixed(), cells=[("Happy Path", "High")])
        self.assertEqual(result["total"], 5)
        self.assertEqual(result["selected"], 1)

    def test_a_stage_with_no_tiers_yet_ignores_a_selection_rather_than_emptying_the_page(self):
        """Every scenario carries Medium before the materiality pass, so a cell picked at a later
        stage and carried back here would narrow on a default nobody chose."""
        result = build_rows(self._mixed(), stage="scenarios", cells=[("Happy Path", "High")])
        self.assertEqual(len(result["rows"]), 5)
        self.assertEqual(result["matrix"], {})


class TestHowManyRowsAreRendered(unittest.TestCase):
    def test_limit_caps_the_rendered_rows_without_changing_the_counts(self):
        scenarios = [_scenario(f"SC-{n:03d}") for n in range(10)]
        result = build_rows(scenarios, limit=3)
        self.assertEqual(len(result["rows"]), 3)
        self.assertEqual(result["shown"], 3)
        self.assertEqual(result["selected"], 10)
        self.assertEqual(result["total"], 10)

    def test_limit_none_shows_everything_selected(self):
        scenarios = [_scenario(f"SC-{n:03d}") for n in range(PAGE_SIZE + 5)]
        result = build_rows(scenarios, limit=None)
        self.assertEqual(len(result["rows"]), PAGE_SIZE + 5)

    def test_the_default_page_size_is_fifty(self):
        self.assertEqual(PAGE_SIZE, 50)


if __name__ == "__main__":
    unittest.main()
