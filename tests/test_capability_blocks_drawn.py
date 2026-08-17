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

    def test_each_ending_of_a_block_is_drawn(self):
        """How a block can fail is exactly what gets lost when one is summarised, and it is what
        the pack tests."""
        layout = build_block_layout(_intake())
        endings = {n.title for n in layout.nodes.values() if n.kind == TERMINAL}
        self.assertEqual(endings, {"S-04", "S-08", "S-11", "S-12", "S-13"})

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
