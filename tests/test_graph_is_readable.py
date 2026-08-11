"""Three things that made a real declared graph hard to read, and what the picture does instead.

**A box was sometimes a decision and sometimes an ending, drawn identically.** The caption above
the picture had been claiming "rounded boxes the ways an interaction can end" for some time, and
every box carried the same corner radius -- so the page named a distinction the drawing never
made, and a reader could not tell a branch point from a terminus without hovering over it. The
intake was never ambiguous about which was which; the SVG was.

**The same ending was drawn once per route into it.** A drafted intake writes one terminal state
per decision outcome, so "escalated to a case handler" arrives as four states with four ids. Four
boxes is four endings, which is not what the agent does, and it is what fills the right-hand
columns with boxes that are all the same box.

**And every line crossed every other.** The question a reader has in front of a graph like that is
never "what is the whole shape" -- it is "where does *this* come from and where does it go", which
until now they had to answer by tracing with a finger.

What is pinned here is the data behind all three. The hover itself is browser behaviour and is
checked by driving one; what these tests hold is that the SVG carries what a hover needs -- every
arrow saying which two boxes it joins -- because that is the part that silently stops being true.
"""
import re
import unittest

from scenario_generator.core.models import Capability, Decision, IntakeData, Persona, State, Tool
from scenario_generator.webapp.graphview import (BOX_HEIGHT, DECISION, TERMINAL, build_layout,
                                                 graph_summary, render_svg)

_PERSONA = Persona("P1", "Cardmember", [], True)


def _intake(states, decisions=None):
    return IntakeData(
        use_case={"Use case name": "Servicing", "Business objective": "Serve"},
        personas=[_PERSONA],
        capabilities=[Capability("CAP-01", "Identity", "Gating")],
        decisions=decisions or [
            Decision("DEC-01", "Identity check", "CAP-01", "", ["Pass", "Fail"]),
            Decision("DEC-02", "What is asked", "CAP-01", "", ["Dispute", "Unclear"]),
        ],
        states=states,
        tools=[Tool("Identity service", "CAP-01", True)],
    )


_SPLIT_ENDINGS = _intake([
    State("S-00", "Start", "The chat opens", ["DEC-01"], False),
    State("S-01", "DEC-01=Pass", "Verified", ["DEC-02"], False),
    # The same ending, declared once per route into it -- which is what a drafted intake produces.
    State("S-02", "DEC-01=Fail", "Escalated to a case handler", [], True, "Escalation"),
    State("S-03", "DEC-02=Dispute", "Dispute filed", [], True, "Happy path"),
    State("S-04", "DEC-02=Unclear", "Escalated to a case handler", [], True, "Escalation"),
])


class TestABoxSaysWhatKindOfThingItIs(unittest.TestCase):
    """Shape rather than colour: it survives printing, and it does not spend the one colour idea
    the interface has."""

    def test_a_decision_is_square_and_an_ending_is_a_stadium(self):
        svg = render_svg(_SPLIT_ENDINGS)
        corners = re.findall(r'<rect x="[\d.]+" y="[\d.]+" width="190" height="62" rx="([\d.]+)"',
                             svg)
        self.assertIn("3", corners, "no square-cornered box: a decision is drawn like an ending")
        self.assertIn(f"{BOX_HEIGHT / 2}", corners, "no stadium: an ending is drawn like a branch")

    def test_the_two_shapes_are_actually_different(self):
        """The caption promised this distinction long before the drawing made it."""
        from scenario_generator.webapp.graphview import _corner

        self.assertNotEqual(_corner(DECISION), _corner(TERMINAL))

    def test_the_page_and_the_picture_agree_about_the_shapes(self):
        """A legend describing a distinction the drawing does not make is worse than no legend."""
        from pathlib import Path

        from scenario_generator.webapp import app as webapp

        template = (Path(webapp.__file__).parent / "templates" / "stage.html").read_text()
        self.assertIn("rounded boxes the ways an interaction can end", template)


class TestOneEndingIsOneBox(unittest.TestCase):
    def test_states_describing_the_same_ending_are_drawn_once(self):
        layout = build_layout(_SPLIT_ENDINGS)
        terminals = [n for n in layout.nodes.values() if n.kind == TERMINAL]
        self.assertEqual(len(terminals), 2, "three declared endings, two distinct ones")

    def test_every_route_into_it_still_arrives(self):
        """Merging the boxes must not lose an arrow -- that would hide a route rather than tidy
        the picture."""
        layout = build_layout(_SPLIT_ENDINGS)
        escalation = next(n for n in layout.nodes.values()
                          if n.caption == "Escalated to a case handler")
        arriving = [e for e in layout.edges if e.target == escalation.id]
        self.assertEqual(sorted(e.source for e in arriving), ["DEC-01", "DEC-02"])

    def test_the_box_names_every_id_it_stands_for(self):
        layout = build_layout(_SPLIT_ENDINGS)
        escalation = next(n for n in layout.nodes.values()
                          if n.caption == "Escalated to a case handler")
        self.assertEqual(escalation.merged_ids, ("S-02", "S-04"))
        self.assertIn("S-02, S-04", escalation.detail)
        self.assertIn("describe the same ending", escalation.detail)

    def test_it_is_reported_as_a_finding_rather_than_quietly_tidied(self):
        """An intake that says the same thing three times is a declaration to tidy, and the
        picture hiding that would be the picture covering for it."""
        self.assertEqual(graph_summary(_SPLIT_ENDINGS)["duplicate_endings"],
                         [("S-02", "S-04")])

    def test_endings_that_differ_by_a_word_are_left_alone(self):
        """Two endings that differ may well be two endings, and merging them would lie about the
        agent in the direction that hides a route."""
        nearly = _intake([
            State("S-00", "Start", "The chat opens", ["DEC-01"], False),
            State("S-01", "DEC-01=Pass", "Escalated to a case handler", [], True, "Escalation"),
            State("S-02", "DEC-01=Fail", "Escalated to a fraud handler", [], True, "Escalation"),
        ])
        self.assertEqual(graph_summary(nearly)["duplicate_endings"], [])

    def test_the_same_words_under_a_different_outcome_type_are_two_endings(self):
        differing = _intake([
            State("S-00", "Start", "The chat opens", ["DEC-01"], False),
            State("S-01", "DEC-01=Pass", "Handed to a person", [], True, "Escalation"),
            State("S-02", "DEC-01=Fail", "Handed to a person", [], True, "Termination"),
        ])
        self.assertEqual(graph_summary(differing)["duplicate_endings"], [])

    def test_endings_with_no_description_are_never_merged_together(self):
        """Grouping every blank one would invent a convergence out of an omission."""
        blank = _intake([
            State("S-00", "Start", "The chat opens", ["DEC-01"], False),
            State("S-01", "DEC-01=Pass", "", [], True, "Happy path"),
            State("S-02", "DEC-01=Fail", "", [], True, "Termination"),
        ])
        layout = build_layout(blank)
        self.assertEqual(len([n for n in layout.nodes.values() if n.kind == TERMINAL]), 2)
        self.assertEqual(graph_summary(blank)["duplicate_endings"], [])

    def test_a_merged_ending_is_not_reported_as_unreachable(self):
        """Its id is no longer a node of its own, which is exactly how a reachability check that
        did not know about merging would decide nothing leads to it."""
        self.assertEqual(graph_summary(_SPLIT_ENDINGS)["unreachable"], [])


class TestTheSvgCarriesWhatAHoverNeeds(unittest.TestCase):
    """The highlighting is browser behaviour; these hold the data it reads."""

    def test_every_arrow_says_which_two_boxes_it_joins(self):
        svg = render_svg(_SPLIT_ENDINGS)
        arrows = re.findall(r'<g class="graph__edge[^"]*" data-source="([^"]+)" '
                            r'data-target="([^"]+)"', svg)
        self.assertEqual(len(arrows), len(build_layout(_SPLIT_ENDINGS).edges))

    def test_an_arrows_label_carries_the_same_pair_so_the_two_light_together(self):
        """They are two elements standing for one thing, and lighting one without the other reads
        as a rendering fault."""
        svg = render_svg(_SPLIT_ENDINGS)
        labels = re.findall(r'<g class="graph__label[^"]*"[^>]*data-source="([^"]+)" '
                            r'data-target="([^"]+)"', svg)
        arrows = re.findall(r'<g class="graph__edge[^"]*" data-source="([^"]+)" '
                            r'data-target="([^"]+)"', svg)
        self.assertEqual(sorted(labels), sorted(arrows))

    def test_every_box_is_addressable_by_id(self):
        svg = render_svg(_SPLIT_ENDINGS)
        drawn = set(re.findall(r'data-node="([^"]+)"', svg))
        self.assertEqual(drawn, set(build_layout(_SPLIT_ENDINGS).nodes))

    def test_every_endpoint_an_arrow_names_is_a_box_that_exists(self):
        """A hover looks a box up by the id on the arrow; one that resolves to nothing lights
        half a thread and says nothing about it."""
        layout = build_layout(_SPLIT_ENDINGS)
        for edge in layout.edges:
            self.assertIn(edge.source, layout.nodes)
            self.assertIn(edge.target, layout.nodes)

    def test_the_graph_still_draws_with_no_scripting_at_all(self):
        """Nothing here is required for the picture to work: the classes the hover adds are added
        by a browser, and the SVG carries its own tooltips."""
        svg = render_svg(_SPLIT_ENDINGS)
        self.assertNotIn("graph--focused", svg)
        self.assertNotIn("is-lit", svg)
        self.assertIn("<title>", svg)


if __name__ == "__main__":
    unittest.main()
