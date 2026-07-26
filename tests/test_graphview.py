"""Drawing the declared graph.

The picture is only worth having if it tells the truth about the declaration, so the tests here
are mostly about the two things a reader would be misled by: an edge that does not exist, and a
state quietly left out because nothing leads to it.
"""
import unittest

from scenario_generator.core.models import Decision, IntakeData, Persona, State, Tool
from scenario_generator.webapp.graphview import build_layout, graph_summary, render_svg

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


class TestLayout(unittest.TestCase):
    def test_depth_counts_from_the_start_state(self):
        layout = build_layout(_INTAKE)
        self.assertEqual(layout.nodes["S-00"].depth, 0)
        self.assertEqual(layout.nodes["S-01"].depth, 1)
        self.assertEqual(layout.nodes["S-03"].depth, 2)

    def test_an_edge_exists_for_every_declared_outcome_that_leads_somewhere(self):
        edges = {(e.source, e.decision_id, e.variant, e.target) for e in build_layout(_INTAKE).edges}
        self.assertIn(("S-00", "DEC-01", "Pass", "S-01"), edges)
        self.assertIn(("S-00", "DEC-01", "Fail", "S-02"), edges)
        self.assertIn(("S-01", "DEC-02", "Found", "S-03"), edges)

    def test_no_edge_is_invented_for_an_outcome_nothing_declares(self):
        for edge in build_layout(_INTAKE).edges:
            self.assertIn(edge.target, {s.id for s in _INTAKE.states})

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


class TestRendering(unittest.TestCase):
    def test_every_state_appears_in_the_drawing(self):
        svg = render_svg(_INTAKE)
        for state in _INTAKE.states:
            self.assertIn(state.id, svg)

    def test_terminal_states_are_marked_by_their_outcome_type(self):
        svg = render_svg(_INTAKE)
        self.assertIn("graph__node--termination", svg)
        self.assertIn("graph__node--happy", svg)

    def test_hover_detail_names_the_decision_and_its_outcome(self):
        self.assertIn("outcome “Pass”", render_svg(_INTAKE))

    def test_unreachable_states_are_drawn_differently(self):
        self.assertIn("graph__node--unreachable", render_svg(_ORPHANED))

    def test_descriptions_are_escaped_rather_than_injected(self):
        intake = IntakeData(
            use_case={}, personas=[], capabilities=[], decisions=[],
            states=[State("S-00", "Start", "<script>alert(1)</script>", [], True)], tools=[])
        svg = render_svg(intake)
        self.assertNotIn("<script>", svg)
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
