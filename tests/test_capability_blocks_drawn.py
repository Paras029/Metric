"""The graph has two drawings of one agent, and they have to agree.

The detailed graph answers "what happens inside verification". On a real use case that is forty
boxes, and the sequence of blocks -- which is what a validator orients by -- is the first thing
lost in it. So a capability with a span is also drawn collapsed: one box, its endings hanging off
it, and an arrow to whatever it hands on to.

What must not happen is the two disagreeing. A collapsed drawing that shows a handoff the walk
does not take, or hides an ending the pack tests, is worse than no second drawing at all.
"""
import unittest

from scenario_generator.core.graph import DecisionGraph, spans_for
from scenario_generator.core.intake import read_intake
from scenario_generator.webapp.graphview import (BLOCK, TERMINAL, build_block_layout,
                                                 render_blocks_svg, render_svg)

from pathlib import Path

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "intakes"


def _intake(name="1_disputes_three_blocks.xlsx"):
    return read_intake(str(EXAMPLES / name))


class TestTheCollapsedDrawing(unittest.TestCase):
    def test_one_box_per_bounded_capability(self):
        layout = build_block_layout(_intake())
        blocks = {n.id for n in layout.nodes.values() if n.kind == BLOCK}
        self.assertEqual(blocks, {"CAP-01", "CAP-02", "CAP-03"})

    def test_a_capability_with_no_span_is_left_out(self):
        """Nothing is walked through it, and drawing it would give the emptiest part of the
        declaration the same weight as the rest."""
        layout = build_block_layout(_intake("4_spans_drawn_wrongly.xlsx"))
        self.assertNotIn("CAP-02", {n.id for n in layout.nodes.values() if n.kind == BLOCK})

    def test_no_spans_at_all_means_no_second_drawing(self):
        self.assertEqual(render_blocks_svg(_intake("2_travel_no_spans.xlsx")), "")

    def test_every_ending_a_block_can_reach_is_drawn(self):
        """How a block can fail is exactly what gets lost when one is summarised, and it is what
        the pack tests.

        Not only the states listed as exits. An exit list says where the block *hands on*; a
        decision inside it can also refuse, escalate or lock out, and those endings are reached
        without ever appearing in the list. Drawing only the listed ones showed a block as having
        one way to fail when it had three.
        """
        layout = build_block_layout(_intake())
        endings = {n.title for n in layout.nodes.values() if n.kind == TERMINAL}
        self.assertEqual(endings,
                         {"S-04", "S-08", "S-09", "S-10", "S-11", "S-12", "S-13", "S-14"})

    def test_an_ending_reached_inside_a_block_but_not_listed_as_an_exit_is_still_drawn(self):
        """The specific hole. S-10 is "account restricted, handed to fraud" -- reachable from a
        decision inside verification, and named in nobody's exit list."""
        from scenario_generator.core.intake import read_intake

        intake = read_intake(str(EXAMPLES / "1_disputes_three_blocks.xlsx"))
        listed = {state for c in intake.capabilities for state in c.exit_states}
        self.assertNotIn("S-10", listed, "the fixture no longer exercises this case")

        drawn = {n.title for n in build_block_layout(intake).nodes.values() if n.kind == TERMINAL}
        self.assertIn("S-10", drawn)

    def test_two_blocks_joined_two_ways_are_one_arrow(self):
        """Two edges between the same pair land on top of each other with their labels colliding,
        and the fact worth reading is that they join."""
        layout = build_block_layout(_intake())
        handoffs = [e for e in layout.edges if e.outcome == "hands on"]
        pairs = [(e.source, e.target) for e in handoffs]
        self.assertEqual(len(pairs), len(set(pairs)))
        joined = next(e for e in handoffs if (e.source, e.target) == ("CAP-01", "CAP-02"))
        self.assertEqual(joined.state_label, "2 ways")


class TestTheTwoDrawingsAgree(unittest.TestCase):
    def test_every_handoff_drawn_is_a_span_boundary_the_walk_uses(self):
        intake = _intake()
        graph = DecisionGraph(intake.decisions, intake.states)
        entries = {span.entry_state: span.capability_id
                   for span in spans_for(graph, intake.capabilities)}
        by_id = {c.id: c for c in intake.capabilities}

        for edge in build_block_layout(intake).edges:
            if edge.outcome != "hands on":
                continue
            shared = [s for s in by_id[edge.source].exit_states
                      if entries.get(s) == edge.target]
            self.assertTrue(shared, f"{edge.source} -> {edge.target} is not a boundary the walk "
                                    f"crosses")

    def test_the_detailed_drawing_tags_every_box_with_its_block(self):
        """What the collapsed drawing groups and what the detailed one highlights have to be the
        same grouping, or opening a block lights the wrong boxes."""
        svg = render_svg(_intake())
        for capability in ("CAP-01", "CAP-02", "CAP-03"):
            self.assertIn(f'data-capability="{capability}"', svg)

    def test_a_block_box_carries_the_capability_it_opens(self):
        svg = render_blocks_svg(_intake())
        self.assertIn('data-capability="CAP-02"', svg)


class TestLabelsDoNotStackOnOneLine(unittest.TestCase):
    """Two branches out of one box put two labels at the same vertical midpoint, and the wider of
    the two reads as nonsense running through the other."""

    def test_siblings_are_staggered(self):
        import re
        svg = render_blocks_svg(_intake())
        rows = re.findall(r'<g class="graph__label"[^>]*>.*?<rect x="[\d.]+" y="([\d.]+)"', svg)
        self.assertEqual(len(rows), len(set(rows)), "two labels share a line")


if __name__ == "__main__":
    unittest.main()


class TestTheSpanIsEditableWithoutDestroyingItself(unittest.TestCase):
    """The control has to look like a control, and must not lose a span to an ordinary click.

    It was a <select multiple>: a grey scrolling list that reads as a display rather than
    something changeable, needing a ctrl-click nobody discovers. The failure that forced the
    rebuild is worse than the discoverability one — an ordinary click on one option clears every
    other, so somebody adding a second entry state would silently delete the first.
    """

    def setUp(self):
        import tempfile, time

        from scenario_generator.webapp.app import create_app

        self.root = Path(tempfile.mkdtemp())
        self.client = create_app(self.root).test_client()
        self.client.post("/workspaces", data={"name": "Spans"})
        source = EXAMPLES / "1_disputes_three_blocks.xlsx"
        with open(source, "rb") as handle:
            self.client.post("/stage/intake/upload",
                             data={"files": (handle, source.name), "group": "intake_workbook"},
                             content_type="multipart/form-data")
        self.client.post("/stage/intake/run")
        for _ in range(400):
            if self.client.get("/stage/intake/progress").get_json()["status"] != "running":
                break
            time.sleep(0.05)
        self.workbook = Path(next(self.root.glob("*/"))) / source.name

    def _spans(self):
        from scenario_generator.core.intake import read_intake
        return {c.id: (c.entry_states, c.exit_states)
                for c in read_intake(str(self.workbook)).capabilities}

    def test_the_control_is_checkboxes_rather_than_a_multi_select(self):
        page = self.client.get("/stage/intake").data.decode()
        self.assertIn('type="checkbox" name="entry_states"', page)
        self.assertIn('type="checkbox" name="exit_states"', page)
        self.assertNotIn('name="entry_states" multiple', page)

    def test_what_is_already_set_comes_back_ticked(self):
        """Matched on the pair rather than on exact whitespace: an indentation change in the
        template is not a defect, and a test that fails on one hides the ones that are."""
        import re

        page = self.client.get("/stage/intake").data.decode()
        ticked = re.search(r'name="entry_states" value="S-00"[^>]*checked', page)
        self.assertIsNotNone(ticked, "the entry already drawn came back unticked")

    def test_the_span_reads_as_a_span_before_it_is_edited(self):
        """Two lists of every state in the graph, twice per capability, was 280 checkboxes on a
        real declaration -- and the clutter was the symptom. The control asked "which of these
        twenty-eight, twice" when what a validator knows is "identification runs from the chat
        opening until it hands on"."""
        page = self.client.get("/stage/intake").data.decode()
        self.assertIn("span__reads", page)
        self.assertIn("pill--in", page)
        self.assertIn("pill--out", page)

    def test_the_boundary_is_built_from_the_decisions_rather_than_from_a_fixed_list(self):
        """The order that makes a capability editable at all: which decisions it holds is the
        judgement, and the states it folds in and the boundary among them follow from it. Both
        halves are drawn on the page as the decisions are ticked, so what is rendered here is the
        machinery rather than the answer."""
        page = self.client.get("/stage/intake").data.decode()
        self.assertIn("data-owns-decision", page, "membership cannot be changed from the row")
        self.assertIn("data-folds-in", page, "nothing says which states the decisions fold in")
        self.assertIn("data-boundary-why", page, "the boundary list does not say how it was drawn")
        self.assertIn("METRIC_GRAPH", page, "the page cannot work out what a tick folds in")

    def test_the_endings_can_be_taken_from_the_graph(self):
        """Where a block starts is a judgement. Where it ends is arithmetic, and typing it out is
        doing by hand what the tool already knows."""
        self.client.post("/stage/intake/capability/CAP-01/span",
                         data={"entry": ["S-00"], "exit": [], "derive": "1"})
        entries, exits = self._spans()["CAP-01"]
        self.assertEqual(entries, ("S-00",))
        self.assertTrue(exits, "the endings came back empty")
        self.assertIn("S-09", exits, "an ending the declaration never listed was not found")

    def test_deriving_needs_an_entry_rather_than_guessing_one(self):
        """A proposal for a block whose boundary nobody has drawn is a guess dressed as
        arithmetic."""
        self.client.post("/stage/intake/capability/CAP-01/span",
                         data={"entry": [], "exit": [], "derive": "1"})
        self.assertEqual(self._spans()["CAP-01"], ((), ()))

    def test_saving_several_states_keeps_all_of_them(self):
        self.client.post("/stage/intake/capability/CAP-02/span",
                         data={"entry": ["S-02", "S-03"], "exit": ["S-07", "S-08", "S-10"]})
        self.assertEqual(self._spans()["CAP-02"], (("S-02", "S-03"), ("S-07", "S-08", "S-10")))

    def test_editing_one_capability_leaves_the_others_alone(self):
        before = self._spans()
        self.client.post("/stage/intake/capability/CAP-02/span",
                         data={"entry": ["S-02"], "exit": ["S-07"]})
        after = self._spans()
        self.assertEqual({k: v for k, v in after.items() if k != "CAP-02"},
                         {k: v for k, v in before.items() if k != "CAP-02"})

    def test_a_span_can_be_cleared_by_ticking_nothing(self):
        """Undividing a capability has to be as available as dividing one."""
        self.client.post("/stage/intake/capability/CAP-03/span", data={})
        self.assertEqual(self._spans()["CAP-03"], ((), ()))

    def test_the_page_says_whether_a_capability_is_walked_as_a_block(self):
        self.client.post("/stage/intake/capability/CAP-03/span", data={})
        page = self.client.get("/stage/intake").data.decode()
        self.assertIn("nothing is walked through this capability", page)


class TestARouteBackIntoAnEarlierBlock(unittest.TestCase):
    """A real agent does not run its blocks in a straight line.

    Verification decides the cardmember has to be identified again, and routes back into
    identification -- not politely to its entry state, but to whichever state asks the question,
    which is somewhere in the middle of it. A collapsed drawing that loses that arrow says the
    agent is a chain when it is a cycle, and the route going backwards is usually the most
    interesting thing on the page.
    """

    def _intake(self):
        from scenario_generator.core.models import Capability, Decision, IntakeData, State

        return IntakeData(
            use_case={"Use case name": "Disputes"}, personas=[],
            capabilities=[
                Capability(id="CAP-01", name="Identification", type="Gating",
                           entry_states=("S-00",), exit_states=("S-02", "S-03")),
                Capability(id="CAP-02", name="Verification", type="Gating",
                           entry_states=("S-02",), exit_states=("S-05", "S-06"))],
            decisions=[
                Decision(id="DEC-01", name="Identify", trigger_capability="CAP-01", inputs="",
                         variants=["Identified", "Not identified"]),
                Decision(id="DEC-02", name="Verify", trigger_capability="CAP-02", inputs="",
                         variants=["Verified", "Stale identity", "Failed"])],
            states=[
                State(id="S-00", reached_via="Start", description="The chat opens",
                      next_decisions=["DEC-01"], is_terminal=False),
                State(id="S-02", reached_via="DEC-01=Identified", description="Identified",
                      next_decisions=["DEC-02"], is_terminal=False),
                State(id="S-03", reached_via="DEC-01=Not identified", description="Chat ended",
                      next_decisions=[], is_terminal=True, outcome_type="Termination"),
                State(id="S-05", reached_via="DEC-02=Verified", description="Verified",
                      next_decisions=[], is_terminal=True, outcome_type="Happy path"),
                State(id="S-06", reached_via="DEC-02=Failed", description="To an adviser",
                      next_decisions=[], is_terminal=True, outcome_type="Escalation"),
                # The return. Verification sends the cardmember back to be identified again, and
                # S-00 is identification's entry rather than one of verification's exits.
                State(id="S-00b", reached_via="DEC-02=Stale identity",
                      description="Identity is stale; identifying again",
                      next_decisions=["DEC-01"], is_terminal=False)],
            tools=[])

    def test_the_arrow_back_is_drawn(self):
        edges = build_block_layout(self._intake()).edges
        back = [e for e in edges if (e.source, e.target) == ("CAP-02", "CAP-01")]
        self.assertTrue(back, "the route back into identification was not drawn at all")

    def test_it_is_not_called_a_handoff(self):
        """A hand-off and a return read differently, and one drawn as the other is a picture that
        says the agent runs forwards only."""
        back = [e for e in build_block_layout(self._intake()).edges
                if (e.source, e.target) == ("CAP-02", "CAP-01")][0]
        self.assertEqual(back.outcome, "returns to")
        self.assertIn("routes back into", back.detail)

    def test_it_is_drawn_as_a_back_edge(self):
        """Which is what puts it in the loop colour rather than in the ordinary edge colour."""
        back = [e for e in build_block_layout(self._intake()).edges
                if (e.source, e.target) == ("CAP-02", "CAP-01")][0]
        self.assertTrue(back.is_back)
        self.assertIn("graph__edge--loop", render_blocks_svg(self._intake()))

    def test_the_forward_handoff_is_still_a_handoff(self):
        forward = [e for e in build_block_layout(self._intake()).edges
                   if (e.source, e.target) == ("CAP-01", "CAP-02")]
        self.assertEqual([e.outcome for e in forward], ["hands on"])

    def test_a_return_does_not_make_the_earlier_block_look_later(self):
        """Depth is how far into the interaction a block is; an arrow pointing backwards must not
        push its target down the page."""
        layout = build_block_layout(self._intake())
        self.assertLess(layout.nodes["CAP-01"].depth, layout.nodes["CAP-02"].depth)
