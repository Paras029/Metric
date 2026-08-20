"""The graph as a second reading of the scenario space.

The materiality table answers "how material is this scenario". It does not answer the question a
validator arrives with, which is *where the material work is*: which branch of the agent produces
the scenarios that matter, and which produces thirty that do not. That is a question about the
shape of the graph, and no sorting of three hundred rows answers it.

Counted over :func:`webapp.graphview.routes`, deliberately, rather than over the paths a second
time -- so an edge a scenario lights when its card is opened is an edge that scenario is counted
on. Two implementations of "which parts of the drawing does this route touch" disagree the first
time either one changes.
"""
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path

from scenario_generator.core.intake import read_intake
from scenario_generator.core.models import ORIGIN_PROPOSED
from scenario_generator.pipeline import build_scenario_space
from scenario_generator.webapp.app import create_app
from scenario_generator.webapp.graphview import load, routes

EXAMPLE = (Path(__file__).resolve().parent.parent
           / "examples" / "intakes" / "1_disputes_three_blocks.xlsx")


class TestCountingTheSpaceOntoTheDrawing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.intake = read_intake(str(EXAMPLE))
        cls.scenarios = build_scenario_space(cls.intake, with_probes=False)

    def _tiered(self, tiers, flags=()):
        """The same space with tiers and flags put on it, so the split has something to split."""
        out = []
        for index, scenario in enumerate(self.scenarios):
            copy = replace(scenario)
            copy.materiality = tiers[index % len(tiers)]
            copy.review_flag = flags[index % len(flags)] if flags else ""
            out.append(copy)
        return out

    def test_every_route_lands_somewhere(self):
        counted = load(self.intake, self.scenarios)
        self.assertTrue(counted["at"], "nothing was counted at all")
        self.assertGreater(counted["peak"]["all"], 0)

    def test_it_counts_the_same_places_the_lighting_lights(self):
        """The paint is the background reading and an opened card is the foreground answer. They
        have to be about the same picture."""
        counted = load(self.intake, self.scenarios)
        for where in routes(self.intake, self.scenarios).values():
            for node_id in where["nodes"]:
                self.assertIn("node:" + node_id, counted["at"])
            for source, target, outcome in where["edges"]:
                self.assertIn("|".join(("edge", source, target, outcome)), counted["at"])

    def test_a_busy_edge_carries_more_than_a_quiet_one(self):
        counted = load(self.intake, self.scenarios)["at"]
        totals = sorted(entry["all"] for entry in counted.values())
        self.assertGreater(totals[-1], totals[0],
                           "every part of the graph carries the same load, so there is nothing to "
                           "see and the whole control is one colour")

    def test_each_band_is_counted_on_its_own(self):
        """One at a time, never blended. An arrow coloured by whichever tier happens to dominate
        it is a summary of three numbers that hides all three."""
        counted = load(self.intake, self._tiered(["High", "Low"]))
        self.assertGreater(counted["peak"]["High"], 0)
        self.assertGreater(counted["peak"]["Low"], 0)
        self.assertEqual(counted["peak"]["Medium"], 0)

    def test_the_bands_add_up_to_the_whole(self):
        counted = load(self.intake, self._tiered(["High", "Medium", "Low"]))["at"]
        for entry in counted.values():
            self.assertEqual(entry["High"] + entry["Medium"] + entry["Low"], entry["all"])

    def test_the_flagged_ones_come_out_of_their_own_band(self):
        """Taking the flagged scenarios out of the High band is a different subtraction from
        taking them out of the space, so each band is counted twice rather than once overall."""
        counted = load(self.intake, self._tiered(["High", "Low"], flags=["Redundant", ""]))["at"]
        for entry in counted.values():
            for band in ("all", "High", "Medium", "Low"):
                self.assertLessEqual(entry[band + "_kept"], entry[band])
        self.assertTrue(any(e["High_kept"] < e["High"] for e in counted.values()),
                        "no band lost anything, so the toggle has nothing to show")

    def test_nothing_flagged_means_nothing_to_take_away(self):
        counted = load(self.intake, self.scenarios)
        self.assertEqual(counted["peak"]["flagged"], 0)
        for band in ("all", "High", "Medium", "Low"):
            self.assertEqual(counted["peak"][band + "_kept"], counted["peak"][band])

    def test_a_proposal_is_counted_as_an_addition(self):
        proposed = replace(self.scenarios[0])
        proposed.id = "LP-001"
        proposed.origin = ORIGIN_PROPOSED
        counted = load(self.intake, list(self.scenarios) + [proposed])
        self.assertEqual(counted["peak"]["proposed"], 1)

    def test_the_later_tier_wins_where_the_review_moved_it(self):
        """The review settles materiality. A picture painted from the tier it replaced is a
        picture of the previous run."""
        moved = replace(self.scenarios[0])
        moved.materiality = "Low"
        moved.review_materiality = "High"
        counted = load(self.intake, [moved])
        self.assertEqual(counted["peak"]["High"], 1)
        self.assertEqual(counted["peak"]["Low"], 0)


class TestWhereTheControlIsOffered(unittest.TestCase):
    """Before materiality nothing has been weighed, so every route paints the same and a control
    offering three ways to draw one colour is worse than none."""

    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp())
        cls.client = create_app(cls.root).test_client()
        cls.client.post("/workspaces", data={"name": "Paint"})
        with open(EXAMPLE, "rb") as handle:
            cls.client.post("/stage/intake/upload",
                            data={"files": (handle, EXAMPLE.name), "group": "intake_workbook"},
                            content_type="multipart/form-data")
        cls.client.post("/stage/intake/run")
        for _ in range(600):
            if cls.client.get("/stage/intake/progress").get_json()["status"] != "running":
                break
            time.sleep(0.05)
        cls.client.post("/stage/workflow/run")
        for _ in range(600):
            if cls.client.get("/stage/workflow/progress").get_json()["status"] != "running":
                break
            time.sleep(0.05)

    def _page(self, key):
        return self.client.get(f"/stage/{key}").get_data(as_text=True)

    def test_not_on_the_intake_stage(self):
        self.assertNotIn("data-paint-mode", self._page("intake"))

    def test_not_on_the_workflow_stage(self):
        self.assertNotIn("data-paint-mode", self._page("workflow"))

    def test_on_the_materiality_stage(self):
        page = self._page("materiality")
        for band in ("all", "High", "Medium", "Low"):
            self.assertIn(f'data-paint-mode="{band}"', page)

    def test_it_can_be_switched_off(self):
        """A reading laid over the drawing has to be removable, and the control has to say so
        rather than leaving somebody to guess which option means none."""
        page = self._page("materiality")
        self.assertIn('data-paint-mode="off"', page)
        self.assertIn(">\n                Off\n              <", page)

    def test_the_review_mode_is_only_on_the_review(self):
        self.assertNotIn('data-paint-mode="review"', self._page("materiality"))
        self.assertIn('data-paint-mode="review"', self._page("review"))

    def test_the_counts_reach_the_page(self):
        self.assertIn("metric-load", self._page("materiality"))


class TestOnlyTheArrowsArePainted(unittest.TestCase):
    """A box's colour already means something -- which kind of ending it is, whether it is out of
    scope, whether it is a block -- and that reading is true of the *declaration*, where a paint is
    true of one run over it. Overwriting the first with the second loses a fact to show a number,
    and the number has somewhere of its own to go: a channel behind the arrow, widening with what
    flows along it."""

    def setUp(self):
        base = Path(__file__).resolve().parent.parent / "scenario_generator" / "webapp" / "static"
        self.script = (base / "graph.js").read_text(encoding="utf-8")
        self.css = (base / "app.css").read_text(encoding="utf-8")

    def test_no_rule_paints_a_box(self):
        painting = self.css[self.css.index("painting the drawing"):]
        self.assertNotIn(".graph__node.is-painted", painting)
        self.assertNotIn("is-painted rect", painting)

    def test_the_script_only_looks_at_edges(self):
        painting = self.script[self.script.index("painting the drawing"):]
        self.assertNotIn("data-node", painting)

    def test_every_arrow_carries_a_channel_to_paint(self):
        from scenario_generator.webapp.graphview import render_svg
        drawn = render_svg(read_intake(str(EXAMPLE)))
        self.assertEqual(drawn.count("graph__flow"), drawn.count('marker-end="url(#arrow)"'))

    def test_the_channel_is_invisible_until_it_is_painted(self):
        """Rendered always, so the standalone page and the interface come off one function."""
        self.assertIn("stroke: none", self.css[self.css.index(".graph__flow {"):][:200])

    def test_width_carries_the_reading(self):
        """A width difference is legible where twelve shades of one colour are not."""
        painted = self.css[self.css.index(".graph__edge.is-painted .graph__flow"):][:400]
        self.assertIn("stroke-width: calc", painted)


class TestTheCollapsedDrawingKeepsTheReading(unittest.TestCase):
    """Folding the capabilities to see the shape of the agent is exactly what somebody asking
    where the material work sits would do."""

    @classmethod
    def setUpClass(cls):
        cls.intake = read_intake(str(EXAMPLE))
        cls.counted = load(cls.intake, build_scenario_space(cls.intake, with_probes=False))["at"]

    def _blocks(self):
        return {key: entry["all"] for key, entry in self.counted.items()
                if key.startswith("edge|CAP") or key.startswith("edge|__start__|CAP")}

    def test_the_hand_offs_between_blocks_carry_a_load(self):
        """Every scenario is scoped to one block, so no route walks a hand-off from the inside.
        Left at that the collapsed drawing paints the endings and leaves the agent's main arteries
        blank, which reads as a fault rather than as a fact about scoping. A route that *ends* by
        handing on travels that arrow to do it."""
        self.assertTrue(any("hands on" in key for key in self._blocks()),
                        f"no hand-off was counted at all: {sorted(self._blocks())}")

    def test_an_ending_hanging_off_a_block_carries_one_too(self):
        self.assertTrue(any(key.endswith("|ends") for key in self._blocks()))

    def test_nothing_is_counted_on_an_arrow_the_collapsed_view_never_drew(self):
        from scenario_generator.webapp.graphview import build_block_layout
        drawn = {(edge.source, edge.target) for edge in build_block_layout(self.intake).edges}
        for key in self._blocks():
            _, source, target, _ = key.split("|")
            self.assertIn((source, target), drawn)


class TestThePaintDoesNotFightTheLighting(unittest.TestCase):
    """Paint is the background reading -- the shape of the space. An opened card is a foreground
    answer about one route, and it is the reason the list and the drawing were put side by side."""

    def setUp(self):
        base = Path(__file__).resolve().parent.parent / "scenario_generator" / "webapp" / "static"
        self.script = (base / "graph.js").read_text(encoding="utf-8")
        self.css = (base / "app.css").read_text(encoding="utf-8")

    def test_the_lit_rules_come_after_the_painted_ones(self):
        self.assertGreater(self.css.rindex(".graph__edge.is-lit .graph__flow"),
                           self.css.rindex(".graph__edge.is-painted .graph__flow"))

    def test_switching_modes_needs_no_round_trip(self):
        self.assertIn("METRIC_LOAD", self.script)
        painting = self.script[self.script.index("painting the drawing"):]
        self.assertNotIn("fetch(", painting)

    def test_the_drop_toggle_holds_the_denominator(self):
        """Measured against its own new peak, a branch that lost half its scenarios renormalises
        straight back to the shade it had, and the picture shows nothing."""
        painting = self.script[self.script.index("painting the drawing"):]
        peak = painting[painting.index("function peak()"):]
        self.assertNotIn("dropFlagged", peak[:200])

    def test_one_band_is_painted_at_a_time(self):
        """Never a blend. An arrow coloured by whichever tier dominates it is a summary of three
        numbers that hides all three, and leaves the reader doing arithmetic against a legend."""
        painting = self.script[self.script.index("painting the drawing"):]
        self.assertNotIn("dominant", painting)


if __name__ == "__main__":
    unittest.main()
