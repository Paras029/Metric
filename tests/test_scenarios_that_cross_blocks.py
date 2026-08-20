"""The journeys the walk cannot produce, and the boundary it now stops at.

Two consequences of scoping the scenario space by capability, both of which need something other
than the walk to handle them.

**No scenario runs end to end any more.** A verification scenario starts with the cardmember
already identified. That is the whole point -- walking every combination of every block multiplies
out into hundreds of scenarios testing the same downstream behaviour -- but it means the routes
that cross a boundary are exactly the ones nobody can see by reading the space, and the review is
the only pass in a position to propose them.

**An out-of-scope decision is a boundary, not a dead end.** A route arriving at one used to be
dropped entirely, which meant marking a sub-system out of scope also stopped everything leading up
to it being tested.
"""
import shutil
import tempfile
import unittest
from pathlib import Path

from scenario_generator.core import editing
from scenario_generator.core.intake import read_intake
from scenario_generator.core.proposals import instantiate_proposals
from scenario_generator.llm.context import describe_blocks, digest
from scenario_generator.pipeline import build_scenario_space
from scenario_generator.webapp.scenarios import to_row

EXAMPLE = (Path(__file__).resolve().parent.parent
           / "examples" / "intakes" / "1_disputes_three_blocks.xlsx")


class TestTheWalkStopsAtAnOutOfScopeBoundary(unittest.TestCase):
    """Marking a decision out of scope says "do not test past here". It used to mean "do not test
    anything that leads here", which is a different and much more expensive statement."""

    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "intake.xlsx"
        shutil.copy(EXAMPLE, self.path)

    def _space(self):
        return build_scenario_space(read_intake(str(self.path)), with_probes=False)

    def _mark(self, decision_id):
        editing.apply_edits(str(self.path), [
            {"kind": "decision", "key": decision_id, "action": "upsert",
             "fields": {"out_of_scope": True}}])

    def test_routes_up_to_the_boundary_are_still_scenarios(self):
        self._mark("DEC-05")
        ending = [s for s in self._space() if "out of scope" in s.termination]
        self.assertTrue(ending, "everything leading to the out-of-scope decision went untested")

    def test_no_route_walks_past_it(self):
        self._mark("DEC-05")
        for scenario in self._space():
            self.assertNotIn("DEC-05", [step.decision_id for step in scenario.path])

    def test_the_ending_says_what_the_tester_should_expect(self):
        """The state it stops at is not an ending, and its description reads as though the
        conversation carries on -- which for a tester marking a transcript is the difference
        between a pass and a bug report."""
        self._mark("DEC-05")
        ending = next(s for s in self._space() if "out of scope" in s.termination)
        self.assertIn("hands on to DEC-05", ending.termination)

    def test_a_retry_running_out_is_still_not_a_scenario(self):
        """The case that must keep being dropped. There the intake genuinely has not said what the
        agent does next, so there is no expected outcome to issue."""
        for scenario in self._space():
            self.assertTrue(scenario.termination.strip())


class TestWhatTheProposalPassIsTold(unittest.TestCase):
    def setUp(self):
        self.intake = read_intake(str(EXAMPLE))

    def test_the_digest_says_which_block_each_scenario_covers(self):
        """The single most important fact about a scenario once the space is scoped: every entry
        covers one block, so what is *not* in the list is any route crossing two."""
        space = build_scenario_space(self.intake, with_probes=False)
        lines = digest(space).splitlines()
        self.assertTrue(any("block=CAP-02" in line for line in lines))

    def test_the_chain_of_blocks_is_described(self):
        """A journey cannot be proposed without knowing the order the blocks run in and where
        they join."""
        said = describe_blocks(self.intake)
        for capability in ("CAP-01", "CAP-02", "CAP-03"):
            self.assertIn(capability, said)
        self.assertIn("-> CAP-02", said)

    def test_it_says_where_each_block_ends_the_interaction(self):
        said = describe_blocks(self.intake)
        self.assertIn("ends the interaction", said)

    def test_an_undivided_declaration_says_so_rather_than_listing_nothing(self):
        """Where no span is drawn the space is already walked whole, and asking for end-to-end
        journeys would be asking for what is already there."""
        undivided = read_intake(str(EXAMPLE.parent / "2_travel_no_spans.xlsx"))
        self.assertIn("already runs end to end", describe_blocks(undivided))

    def test_the_prompt_asks_for_the_journeys_and_caps_them(self):
        from scenario_generator.llm import prompt_loader

        rendered = prompt_loader.render(
            "reviewer.propose", owner="", total=1, digest="d", limit=5,
            blocks=describe_blocks(self.intake), categories="c", materiality="m", cds="")
        self.assertIn("one block at a time", rendered)
        self.assertIn("no scenario in the digest runs the whole way through", rendered)
        self.assertIn("CAP-02", rendered, "the block chain was not carried into the prompt")
        self.assertIn("three", rendered, "nothing caps how many journeys are proposed")


class TestAProposalThatCrossesBlocks(unittest.TestCase):
    def setUp(self):
        self.intake = read_intake(str(EXAMPLE))
        self.crossing = instantiate_proposals([{
            "title": "Identity corrected after the dispute is described",
            "description": "The cardmember gives one card, describes the charge, then says the "
                           "charge was on a different card.",
            "turn_plan": "1. Identify with the first card.\n2. Describe the charge.\n"
                         "3. Say it was a different card.",
            "turns": 3,
            "expected_outcome": "Internal.",
            "decision_path": [{"decision_id": "DEC-01", "variant": "Dispute"},
                              {"decision_id": "DEC-02", "variant": "By card details"},
                              {"decision_id": "DEC-03", "variant": "Verified"},
                              {"decision_id": "DEC-05", "variant": "Within window"}],
            "capabilities": ["CAP-01", "CAP-02", "CAP-03"],
            "materiality": "High",
            "rationale": "Nothing in the space carries a correction across a boundary.",
        }], self.intake)[0]

    def test_it_is_scoped_to_no_single_block(self):
        """A route straddling two blocks belongs to neither, and filing it under one would put it
        in the wrong section of the pack."""
        self.assertEqual(self.crossing.capability_id, "")

    def test_it_starts_at_the_beginning_rather_than_being_seeded_part_way_through(self):
        self.assertEqual(self.crossing.precondition, "")
        self.assertEqual(self.crossing.seeded_state, "Session start")

    def test_it_keeps_every_block_it_crosses(self):
        self.assertEqual(self.crossing.capabilities, ["CAP-01", "CAP-02", "CAP-03"])

    def test_the_page_reads_it_as_a_journey_rather_than_as_a_blank(self):
        row = to_row(self.crossing, {c.id: c.name for c in self.intake.capabilities})
        self.assertTrue(row["crosses_blocks"])
        self.assertEqual(row["capability"], "End to end")

    def test_a_proposal_inside_one_block_is_not_called_a_journey(self):
        inside = instantiate_proposals([{
            "title": "Boundary case on the dispute window",
            "description": "A charge dated exactly at the window boundary.",
            "turn_plan": "1. Describe a charge dated at the limit.",
            "turns": 1,
            "expected_outcome": "Internal.",
            "decision_path": [{"decision_id": "DEC-05", "variant": "Within window"}],
            "capabilities": ["CAP-03"],
            "materiality": "Medium", "rationale": "The threshold is stated and untested.",
        }], self.intake)[0]
        row = to_row(inside, {c.id: c.name for c in self.intake.capabilities})
        self.assertFalse(row["crosses_blocks"])
        self.assertEqual(inside.capability_id, "CAP-03")


class TestTheOrderScenariosAreListedIn(unittest.TestCase):
    """A probe is path-independent -- it tests what the agent must refuse whatever route it is on
    -- so it belongs in the pack but not at the top of it. Ids sort probes first on their own,
    which is how they came to lead."""

    def _rows(self):
        from scenario_generator.webapp.scenarios import build_rows

        intake = read_intake(str(EXAMPLE))
        space = build_scenario_space(intake, with_probes=True)
        return build_rows(space, stage="scenarios", limit=None)["rows"]

    def test_routes_come_before_probes(self):
        kinds = [bool(row["is_probe"]) for row in self._rows()]
        self.assertEqual(kinds, sorted(kinds), "a probe was listed before a route")

    def test_nothing_is_lost_by_the_reordering(self):
        rows = self._rows()
        self.assertEqual(len(rows), len({row["id"] for row in rows}))
        self.assertTrue(any(row["is_probe"] for row in rows))
        self.assertTrue(any(not row["is_probe"] for row in rows))


if __name__ == "__main__":
    unittest.main()
