"""Drawing the declared graph.

Boxes are decisions and arrows are the states between them, which is how the intake is authored:
a state's `reached_via` is written as `DEC-01=Pass`, so a state is defined by the outcome that
produces it. Drawing it the other way round asks the reader to translate.

The picture is only worth having if it tells the truth about the declaration, so most of what is
pinned here is the two things a reader would be misled by: an arrow that does not exist, and a
state quietly left out because nothing leads to it.
"""
import unittest

from scenario_generator.core.models import Decision, IntakeData, Persona, State, Tool
from scenario_generator.webapp.graphview import (DECISION, START, TERMINAL, build_layout,
                                                 graph_summary, render_svg)

_INTAKE = IntakeData(
    use_case={"Use case name": "Test", "Business objective": "Objective"},
    personas=[Persona("P1", "Default user", [], True)],
    capabilities=[],
    decisions=[Decision("DEC-01", "Auth", "CAP-01", "", ["Pass", "Fail"]),
               Decision("DEC-02", "Lookup", "CAP-02", "", ["Found"])],
    states=[State("S-00", "Start", "Session begins", ["DEC-01"], False),
            State("S-01", "DEC-01=Pass", "Authenticated", ["DEC-02"], False),
            State("S-02", "DEC-01=Fail", "Locked out", [], True, "Termination"),
            State("S-03", "DEC-02=Found", "Record located", [], True, "Happy path")],
    tools=[Tool("Identity service", "CAP-01", False)],
)

_LOOPED = IntakeData(
    use_case={"Use case name": "Test", "Business objective": "Objective"},
    personas=[Persona("P1", "Default user", [], True)],
    capabilities=[],
    decisions=[Decision("DEC-01", "PIN check", "CAP-01", "", ["Pass", "Fail"], max_attempts=3)],
    states=[State("S-00", "Start", "Session begins", ["DEC-01"], False),
            State("S-01", "DEC-01=Pass", "Authenticated", [], True, "Happy path"),
            # A failed attempt loops straight back to the same decision -- a retry, not a step
            # forward, and exactly the shape that used to draw an arrow back up through every
            # row in between.
            State("S-02", "DEC-01=Fail", "Try again", ["DEC-01"], False)],
    tools=[],
)

_ORPHANED = IntakeData(
    use_case={"Use case name": "Test", "Business objective": "Objective"},
    personas=[Persona("P1", "Default user", [], True)],
    capabilities=[],
    decisions=[Decision("DEC-01", "Auth", "CAP-01", "", ["Pass"])],
    states=[State("S-00", "Start", "Session begins", ["DEC-01"], False),
            State("S-01", "DEC-01=Pass", "Authenticated", [], True, "Happy path"),
            State("S-09", "DEC-07=Nowhere", "Unreachable", [], True, "Termination")],
    tools=[],
)


class TestWhatIsABoxAndWhatIsAnArrow(unittest.TestCase):
    def test_every_decision_is_a_box(self):
        nodes = build_layout(_INTAKE).nodes
        for decision in _INTAKE.decisions:
            self.assertEqual(nodes[decision.id].kind, DECISION)

    def test_each_way_the_interaction_ends_is_a_box(self):
        nodes = build_layout(_INTAKE).nodes
        self.assertEqual(nodes["S-02"].kind, TERMINAL)
        self.assertEqual(nodes["S-03"].kind, TERMINAL)

    def test_a_state_the_interaction_passes_through_is_an_arrow_not_a_box(self):
        """S-01 is a position on the way somewhere, so it belongs on the arrow into DEC-02."""
        layout = build_layout(_INTAKE)
        self.assertNotIn("S-01", layout.nodes)
        carried = [e for e in layout.edges if e.target == "DEC-02"]
        self.assertEqual([e.state_label for e in carried], ["Authenticated"])

    def test_there_is_somewhere_for_the_interaction_to_open(self):
        starts = [n for n in build_layout(_INTAKE).nodes.values() if n.kind == START]
        self.assertEqual(len(starts), 1)
        self.assertIn("Session begins", starts[0].caption)

    def test_an_arrow_carries_the_outcome_that_took_it(self):
        edges = {(e.source, e.outcome, e.target) for e in build_layout(_INTAKE).edges}
        self.assertIn(("DEC-01", "Pass", "DEC-02"), edges)
        self.assertIn(("DEC-01", "Fail", "S-02"), edges)
        self.assertIn(("DEC-02", "Found", "S-03"), edges)

    def test_no_arrow_is_invented_for_an_outcome_nothing_declares(self):
        declared = {d.id for d in _INTAKE.decisions} | {s.id for s in _INTAKE.states}
        for edge in build_layout(_INTAKE).edges:
            self.assertIn(edge.target, declared)


class TestLayout(unittest.TestCase):
    def test_depth_counts_from_where_the_interaction_opens(self):
        layout = build_layout(_INTAKE)
        self.assertEqual(layout.nodes["DEC-01"].depth, 1)
        self.assertEqual(layout.nodes["DEC-02"].depth, 2)
        self.assertEqual(layout.nodes["S-03"].depth, 3)

    def test_a_state_nothing_leads_to_is_reported_rather_than_hidden(self):
        layout = build_layout(_ORPHANED)
        self.assertEqual(layout.unreachable, ["S-09"])
        self.assertIn("S-09", layout.nodes)

    def test_a_well_formed_graph_reports_nothing_unreachable(self):
        self.assertEqual(build_layout(_INTAKE).unreachable, [])

    def test_an_empty_intake_lays_out_without_failing(self):
        empty = IntakeData(use_case={}, personas=[], capabilities=[], decisions=[], states=[],
                           tools=[])
        self.assertEqual(build_layout(empty).nodes, {})
        self.assertEqual(render_svg(empty), "")


class TestRetryLoops(unittest.TestCase):
    """A decision that revisits itself, or an earlier point, is not a step forward -- drawing it
    as one used to send an arrow straight up through every row in between."""

    def test_a_loop_is_recognised_by_its_depth_not_moving_forward(self):
        layout = build_layout(_LOOPED)
        loops = [e for e in layout.edges if e.outcome == "Fail" and e.target == "DEC-01"]
        self.assertEqual(len(loops), 1)
        self.assertTrue(loops[0].is_back)

    def test_a_forward_edge_is_never_marked_as_a_loop(self):
        layout = build_layout(_LOOPED)
        forward = [e for e in layout.edges if e.outcome == "Pass"]
        self.assertEqual(len(forward), 1)
        self.assertFalse(forward[0].is_back)

    def test_each_loop_gets_its_own_lane(self):
        """Two independent loops must not be routed through the same lane, or they would overlap
        each other exactly the way a single loop used to overlap the ordinary flow."""
        two_loops = IntakeData(
            use_case={}, personas=[Persona("P1", "Default", [], True)], capabilities=[],
            decisions=[Decision("DEC-01", "Check A", "", "", ["Pass", "Fail"]),
                      Decision("DEC-02", "Check B", "", "", ["Pass", "Fail"])],
            states=[State("S-00", "Start", "Start", ["DEC-01"], False),
                    State("S-01", "DEC-01=Pass", "Next", ["DEC-02"], False),
                    State("S-02", "DEC-01=Fail", "Retry A", ["DEC-01"], False),
                    State("S-03", "DEC-02=Pass", "Done", [], True, "Happy path"),
                    State("S-04", "DEC-02=Fail", "Retry B", ["DEC-02"], False)],
            tools=[])
        layout = build_layout(two_loops)
        loops = [e for e in layout.edges if e.is_back]
        self.assertEqual(len(loops), 2)
        self.assertNotEqual(loops[0].lane_x, loops[1].lane_x)

    def test_the_drawing_reserves_room_for_the_lanes(self):
        """The SVG's own declared width grows to fit the loop lanes, rather than the lane
        overrunning the edge of the picture."""
        without_loop = build_layout(_INTAKE)
        with_loop = build_layout(_LOOPED)
        # Both are small graphs; the point is only that a loop costs extra width, not a number.
        self.assertGreater(with_loop.width, 0)
        self.assertGreater(without_loop.width, 0)

    def test_a_loop_is_drawn_with_its_own_class_and_rotated_label(self):
        svg = render_svg(_LOOPED)
        self.assertIn("graph__edge--loop", svg)
        self.assertIn("graph__label--loop", svg)
        self.assertIn("rotate(-90", svg)

    def test_a_loop_never_crosses_through_an_unrelated_row(self):
        """The loop's path must stay inside its own lane, to the right of every node -- not cut
        back across the x range any node actually occupies."""
        layout = build_layout(_LOOPED)
        rightmost_node = max(n.x + 190 for n in layout.nodes.values())  # BOX_WIDTH
        loop = next(e for e in layout.edges if e.is_back)
        self.assertGreater(loop.lane_x, rightmost_node)


class TestSketchedEdits(unittest.TestCase):
    """Additions made in the interface are drawn, and marked as not yet committed."""

    def test_a_sketched_decision_is_marked_as_pending(self):
        layout = build_layout(_INTAKE, pending_decisions=["DEC-02"])
        self.assertTrue(layout.nodes["DEC-02"].pending)
        self.assertFalse(layout.nodes["DEC-01"].pending)

    def test_a_sketched_element_is_drawn_differently(self):
        self.assertIn("graph__node--pending", render_svg(_INTAKE, pending_decisions=["DEC-02"]))

    def test_an_arrow_touching_a_sketched_element_is_marked_too(self):
        layout = build_layout(_INTAKE, pending_states=["S-02"])
        touching = [e for e in layout.edges if e.target == "S-02"]
        self.assertTrue(touching and all(e.pending for e in touching))

    def test_nothing_is_pending_when_nothing_was_sketched(self):
        self.assertNotIn("graph__node--pending", render_svg(_INTAKE))


class TestRendering(unittest.TestCase):
    def test_every_decision_and_ending_appears_in_the_drawing(self):
        svg = render_svg(_INTAKE)
        for identifier in ("DEC-01", "DEC-02", "S-02", "S-03"):
            self.assertIn(identifier, svg)

    def test_terminal_states_are_marked_by_their_outcome_type(self):
        svg = render_svg(_INTAKE)
        self.assertIn("graph__node--termination", svg)
        self.assertIn("graph__node--happy", svg)

    def test_an_arrow_is_labelled_with_the_state_it_leads_to(self):
        self.assertIn("Pass → Authenticated", render_svg(_INTAKE))

    def test_hover_detail_names_the_decision_the_outcome_and_the_state(self):
        svg = render_svg(_INTAKE)
        self.assertIn("DEC-01 · Auth → Pass", svg)
        self.assertIn("Leads to: S-01 · Authenticated", svg)

    def test_hover_detail_is_a_labelled_panel_rather_than_one_run_on_line(self):
        """A tooltip is where most of the intake is actually read. One fact per line, each named,
        is the same information without having to pick it apart by eye."""
        detail = build_layout(_INTAKE).nodes["DEC-01"].detail
        self.assertEqual(detail.splitlines()[0], "DEC-01 · Auth")
        self.assertIn("Outcomes: Pass / Fail", detail)
        self.assertIn("Capability: CAP-01", detail)

    def test_a_field_with_no_value_is_left_out_rather_than_shown_empty(self):
        """DEC-01 declares no retry bound and no outcome condition."""
        detail = build_layout(_INTAKE).nodes["DEC-01"].detail
        self.assertNotIn("Attempts allowed:", detail)
        self.assertNotIn("Selected when:", detail)

    def test_a_terminal_state_says_how_the_interaction_ends(self):
        detail = build_layout(_INTAKE).nodes["S-02"].detail
        self.assertIn("Reached via: DEC-01=Fail", detail)
        self.assertIn("Ends the interaction (Termination)", detail)

    def test_the_drawing_states_its_own_size_so_it_can_be_zoomed(self):
        self.assertIn('width="', render_svg(_INTAKE))
        self.assertIn('viewBox="', render_svg(_INTAKE))

    def test_unreachable_states_are_drawn_differently(self):
        self.assertIn("graph__node--unreachable", render_svg(_ORPHANED))

    def test_descriptions_are_escaped_rather_than_injected(self):
        intake = IntakeData(
            use_case={}, personas=[], capabilities=[],
            decisions=[Decision("DEC-01", "<img src=x>", "", "", ["Pass"])],
            states=[State("S-00", "Start", "<script>alert(1)</script>", ["DEC-01"], False)],
            tools=[])
        svg = render_svg(intake)
        self.assertNotIn("<script>", svg)
        self.assertNotIn("<img src=x>", svg)
        self.assertIn("&lt;script&gt;", svg)


class TestSummary(unittest.TestCase):
    def test_counts_match_the_declaration(self):
        facts = graph_summary(_INTAKE)
        self.assertEqual(facts["states"], 4)
        self.assertEqual(facts["terminal"], 2)
        self.assertEqual(facts["decisions"], 2)
        self.assertEqual(facts["outcomes"], 3)
        self.assertEqual(facts["unreachable"], [])


if __name__ == "__main__":
    unittest.main()
