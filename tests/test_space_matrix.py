"""The scenario space as a grid, and the one thing a ranked list can never show.

A list sorted by materiality answers "what is most material" and nothing else. The two questions
being asked at this point in a validation are *where is the mass* -- is this a space of eighty
happy paths and four terminations? -- and, more sharply, *which combinations have nothing in
them*. An empty critical/termination cell is a hole in the exercise, and no ranked list will ever
show it, because a hole has no row.

Two properties are pinned here. Every cell's number is exactly what clicking that cell shows,
which is why the grid is counted over the view before the filters run rather than after. And the
grid never appears before the stage that fills it: every scenario carries "Medium" from the moment
it is built, so a grid drawn on the scenario-text page would be a picture of a default presented
as a judgement.
"""
import unittest

from scenario_generator.core.models import Decision, IntakeData, Persona, Scenario, State, Step, Tool
from scenario_generator.webapp.scenarios import build_rows, matrix, to_row

_PERSONA = Persona("P1", "Cardholder", [], True)

_INTAKE = IntakeData(
    use_case={"Use case name": "Disputes", "Business objective": "Resolve disputes"},
    personas=[_PERSONA],
    capabilities=[],
    decisions=[Decision("DEC-01", "Identity", "CAP-01", "", ["Pass", "Fail"])],
    states=[State("S-00", "Start", "Start", ["DEC-01"], False)],
    tools=[Tool("Identity service", "CAP-01", True)],
)


def _scenario(identifier, category="Happy Path", materiality="Medium", **overrides):
    scenario = Scenario(
        id=identifier,
        path=[Step("DEC-01", "Pass", "S-01")],
        category=category,
        persona=_PERSONA,
        seeded_state="S-00",
        termination="The dispute is filed.",
        capabilities=["CAP-01"],
        tools=["Identity service"],
        touches_state_change=True,
        turn_meta=[],
        description="The cardholder disputes a charge.",
        materiality=materiality,
    )
    for name, value in overrides.items():
        setattr(scenario, name, value)
    return scenario


def _grid(scenarios, active=None):
    return matrix([to_row(s) for s in scenarios], active)


class TestWhatTheGridCounts(unittest.TestCase):
    def setUp(self):
        self.scenarios = [
            _scenario("SC-001", "Happy Path", "Critical"),
            _scenario("SC-002", "Happy Path", "Critical"),
            _scenario("SC-003", "Happy Path", "Low"),
            _scenario("SC-004", "Termination", "Low"),
        ]

    def test_a_cell_holds_the_scenarios_at_that_category_and_tier(self):
        grid = _grid(self.scenarios)
        by_category = {row["category"]: row for row in grid["rows"]}
        cells = {cell["tier"]: cell["count"] for cell in by_category["Happy Path"]["cells"]}
        self.assertEqual(cells, {"Critical": 2, "Low": 1})

    def test_a_combination_with_nothing_in_it_is_a_cell_rather_than_a_missing_row(self):
        """The whole reason the grid exists: no termination scenario is critical, and that fact
        has no row in any list sorted by tier."""
        grid = _grid(self.scenarios)
        termination = next(r for r in grid["rows"] if r["category"] == "Termination")
        critical = next(c for c in termination["cells"] if c["tier"] == "Critical")
        self.assertEqual(critical["count"], 0)
        # Four cells, three of them filled.
        self.assertEqual(grid["empty"], 1)

    def test_the_column_totals_are_the_whole_column(self):
        grid = _grid(self.scenarios)
        self.assertEqual({t["tier"]: t["count"] for t in grid["tiers"]}, {"Critical": 2, "Low": 2})

    def test_the_row_totals_are_the_whole_row(self):
        grid = _grid(self.scenarios)
        self.assertEqual({r["category"]: r["total"] for r in grid["rows"]},
                         {"Happy Path": 3, "Termination": 1})

    def test_a_tier_nothing_reaches_is_not_given_a_column(self):
        """Four columns of dashes is not a finding, it is an unused scale."""
        self.assertEqual([t["tier"] for t in _grid(self.scenarios)["tiers"]], ["Critical", "Low"])

    def test_the_columns_run_most_material_first(self):
        scenarios = self.scenarios + [_scenario("SC-005", "Happy Path", "High"),
                                      _scenario("SC-006", "Happy Path", "Medium")]
        self.assertEqual([t["tier"] for t in _grid(scenarios)["tiers"]],
                         ["Critical", "High", "Medium", "Low"])


class TestHowDarkACellGets(unittest.TestCase):
    def test_a_cell_holding_one_scenario_is_never_shaded_as_empty(self):
        """The one distinction the shading exists to make. A hundred in the busiest cell and one
        here still has to read as "something is here"."""
        scenarios = ([_scenario(f"SC-{n:03d}", "Happy Path", "Low") for n in range(100)]
                     + [_scenario("SC-999", "Termination", "Critical")])
        grid = _grid(scenarios)
        lone = next(c for row in grid["rows"] if row["category"] == "Termination"
                    for c in row["cells"] if c["tier"] == "Critical")
        self.assertEqual(lone["count"], 1)
        self.assertGreaterEqual(lone["density"], 1)

    def test_the_busiest_cell_is_the_darkest_step(self):
        scenarios = ([_scenario(f"SC-{n:03d}", "Happy Path", "Low") for n in range(10)]
                     + [_scenario("SC-900", "Termination", "Critical")])
        grid = _grid(scenarios)
        busiest = next(c for row in grid["rows"] if row["category"] == "Happy Path"
                       for c in row["cells"] if c["tier"] == "Low")
        self.assertEqual(busiest["density"], 4)

    def test_a_grid_where_every_filled_cell_is_equal_is_not_drawn_at_full_strength(self):
        """Scaling a flat grid against its own maximum paints every occupied cell solid, which
        reads as "everything is dense" when it means "nothing here varies"."""
        flat = [_scenario("SC-001", "Happy Path", "Critical"),
                _scenario("SC-002", "Termination", "Low")]
        densities = {c["density"] for row in _grid(flat)["rows"] for c in row["cells"] if c["count"]}
        self.assertEqual(densities, {2})

    def test_an_empty_cell_carries_no_shade(self):
        grid = _grid([_scenario("SC-001", "Happy Path", "Critical"),
                      _scenario("SC-002", "Termination", "Low")])
        empty = next(c for row in grid["rows"] if row["category"] == "Termination"
                     for c in row["cells"] if c["tier"] == "Critical")
        self.assertEqual(empty["density"], 0)


class TestWhichCellIsInForce(unittest.TestCase):
    def setUp(self):
        self.scenarios = [_scenario("SC-001", "Happy Path", "Critical"),
                          _scenario("SC-002", "Termination", "Low")]

    def test_the_cell_matching_both_active_filters_is_marked(self):
        grid = _grid(self.scenarios, {"category": "Happy Path", "materiality": "Critical"})
        marked = [c for row in grid["rows"] for c in row["cells"] if c["active"]]
        self.assertEqual([(c["category"], c["tier"]) for c in marked],
                         [("Happy Path", "Critical")])

    def test_a_whole_column_is_marked_only_when_nothing_narrows_it_further(self):
        """Filtering to Critical marks the Critical header; filtering to Critical *and* a category
        marks the cell instead, because that is what the view is actually showing."""
        self.assertTrue(next(t for t in _grid(self.scenarios, {"materiality": "Critical"})["tiers"]
                             if t["tier"] == "Critical")["active"])
        both = _grid(self.scenarios, {"materiality": "Critical", "category": "Happy Path"})
        self.assertFalse(next(t for t in both["tiers"] if t["tier"] == "Critical")["active"])

    def test_a_whole_row_is_marked_the_same_way(self):
        grid = _grid(self.scenarios, {"category": "Happy Path"})
        self.assertEqual([r["category"] for r in grid["rows"] if r["active"]], ["Happy Path"])


class TestWhereTheGridAppears(unittest.TestCase):
    def _scenarios(self):
        return [_scenario("SC-001", "Happy Path", "Critical"),
                _scenario("SC-002", "Termination", "Low")]

    def test_it_is_not_drawn_before_a_tier_has_been_assigned(self):
        """Every scenario carries Medium from the moment it is built, so a grid on the
        scenario-text page would be a picture of a default presented as a judgement."""
        self.assertEqual(build_rows(self._scenarios(), "all", stage="scenarios")["matrix"], {})

    def test_it_is_drawn_from_the_materiality_stage_onwards(self):
        for stage in ("materiality", "review", "coverage", "summary"):
            self.assertTrue(build_rows(self._scenarios(), "all", stage=stage)["matrix"], stage)

    def test_every_cell_counts_what_clicking_it_would_show(self):
        """Counted over the view before the filters run. Rebuilt afterwards, the grid would show
        one cell holding everything -- a picture of the filter rather than of the space."""
        scenarios = self._scenarios()
        grid = build_rows(scenarios, "all", stage="materiality")["matrix"]
        for row in grid["rows"]:
            for cell in row["cells"]:
                result = build_rows(scenarios, "all", stage="materiality",
                                    filters={"category": cell["category"],
                                             "materiality": cell["tier"]})
                self.assertEqual(result["selected"], cell["count"],
                                 f"{cell['category']} / {cell['tier']}")

    def test_the_grid_does_not_shrink_as_the_filters_narrow_the_list(self):
        scenarios = self._scenarios()
        whole = build_rows(scenarios, "all", stage="materiality")["matrix"]
        narrowed = build_rows(scenarios, "all", stage="materiality",
                              filters={"materiality": "Critical"})["matrix"]
        self.assertEqual(whole["total"], narrowed["total"])
        self.assertEqual([r["total"] for r in whole["rows"]],
                         [r["total"] for r in narrowed["rows"]])

    def test_a_space_with_one_category_and_one_tier_is_not_worth_a_grid(self):
        """A one-by-one grid is a number with a border around it."""
        same = [_scenario(f"SC-{n:03d}", "Happy Path", "Low") for n in range(4)]
        grid = build_rows(same, "all", stage="materiality")["matrix"]
        self.assertEqual(len(grid["rows"]), 1)
        self.assertEqual(len(grid["tiers"]), 1)
        self.assertEqual(grid["empty"], 0)


if __name__ == "__main__":
    unittest.main()
