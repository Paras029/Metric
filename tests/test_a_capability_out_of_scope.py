"""A block the agent performs but this review is not testing.

The usual case is a capability owned by another team and covered by a separate engagement. It
cannot simply be deleted from the declaration -- a route through the block after it genuinely does
pass through it, and a graph that omits it is a graph that lies about how the agent works. So it
stays declared, contributes no scenarios, and everything downstream treats it as something that
has already happened.

The failure this guards against is the quiet one: excluding a block and finding its decisions
walked anyway under "not grouped into a capability", which would produce exactly the scenarios the
exclusion asked not to exist.
"""
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from scenario_generator.core.graph import DecisionGraph, spans_for
from scenario_generator.core.intake import read_intake, set_capability_scope
from scenario_generator.core.verify import check_walk
from scenario_generator.llm.context import describe_blocks, describe_graph
from scenario_generator.pipeline import build_scenario_space

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "intakes" / \
    "1_disputes_three_blocks.xlsx"


def _excluding(capability_id: str):
    """The example declaration with one capability marked out of scope, read back from disk."""
    copied = Path(tempfile.mkdtemp()) / EXAMPLE.name
    shutil.copyfile(EXAMPLE, copied)
    assert set_capability_scope(str(copied), capability_id, True)
    return read_intake(str(copied))


class TestItRoundTripsThroughTheWorkbook(unittest.TestCase):
    def test_the_column_is_written_and_read_back(self):
        intake = _excluding("CAP-02")
        excluded = next(c for c in intake.capabilities if c.id == "CAP-02")
        self.assertTrue(excluded.out_of_scope)
        self.assertFalse(any(c.out_of_scope for c in intake.capabilities if c.id != "CAP-02"))

    def test_a_workbook_written_before_the_column_existed_still_reads(self):
        """Every existing intake has five columns on the L2 sheet, and none of them is out of
        scope."""
        intake = read_intake(str(EXAMPLE))
        self.assertFalse(any(c.out_of_scope for c in intake.capabilities))


class TestNoScenarioIsWrittenForIt(unittest.TestCase):
    def setUp(self):
        self.intake = _excluding("CAP-02")
        self.scenarios = build_scenario_space(self.intake, with_probes=False)

    def test_it_gets_no_span(self):
        graph = DecisionGraph(self.intake.decisions, self.intake.states)
        self.assertNotIn("CAP-02", {s.capability_id for s
                                    in spans_for(graph, self.intake.capabilities)})

    def test_it_gets_no_scenarios(self):
        self.assertEqual([s.id for s in self.scenarios if s.capability_id == "CAP-02"], [])

    def test_its_decisions_are_not_adopted_by_a_gap_span(self):
        """The quiet failure: excluded from its own block and walked anyway as an orphan."""
        excluded = {d.id for d in self.intake.decisions if d.trigger_capability == "CAP-02"}
        self.assertTrue(excluded, "the example no longer has decisions in CAP-02")
        walked = {step.decision_id for s in self.scenarios for step in s.path}
        self.assertEqual(walked & excluded, set())

    def test_the_blocks_still_in_scope_are_walked_as_before(self):
        whole = build_scenario_space(read_intake(str(EXAMPLE)), with_probes=False)
        for capability in ("CAP-01", "CAP-03"):
            self.assertEqual(
                len([s for s in self.scenarios if s.capability_id == capability]),
                len([s for s in whole if s.capability_id == capability]),
                f"excluding CAP-02 changed how {capability} is walked")


class TestTheCheckDoesNotCallItAHole(unittest.TestCase):
    """An excluded block is not a block the walk failed to reach, and reporting it as one would
    make every declaration with anything excluded read as incomplete."""

    def setUp(self):
        self.intake = _excluding("CAP-02")
        self.report = check_walk(self.intake, build_scenario_space(self.intake, with_probes=False))

    def test_the_walk_is_still_sound(self):
        self.assertTrue(self.report.sound, [str(f) for f in self.report.unreplayable
                                            + self.report.misplaced + self.report.duplicates])

    def test_it_is_not_reported_as_an_unwalked_capability(self):
        self.assertNotIn("CAP-02", self.report.capabilities_empty)

    def test_its_outcomes_are_not_reported_as_missing(self):
        excluded = {d.id for d in self.intake.decisions if d.trigger_capability == "CAP-02"}
        self.assertEqual([m for m in self.report.outcomes_missing
                          if m.split("=")[0] in excluded], [])

    def test_states_only_it_reaches_are_not_reported_as_missing(self):
        self.assertEqual(self.report.states_missing, [])


class TestWhatTheModelIsTold(unittest.TestCase):
    """A later block's turn plan has to say the excluded one already happened, which it can only
    do if it is told so."""

    def setUp(self):
        self.intake = _excluding("CAP-02")

    def test_the_declaration_says_which_block_is_out_of_scope(self):
        described = describe_graph(self.intake)
        self.assertIn("OUT OF SCOPE", described)
        line = next(l for l in described.splitlines() if l.startswith("- CAP-02"))
        self.assertIn("already completed", line)

    def test_a_block_in_scope_is_described_as_before(self):
        line = next(l for l in describe_graph(self.intake).splitlines()
                    if l.startswith("- CAP-01"))
        self.assertNotIn("OUT OF SCOPE", line)

    def test_the_chain_still_names_it_so_a_journey_is_not_missing_a_step(self):
        """Dropped from the chain, an excluded block looks like a gap in the graph -- and the next
        thing a proposal does with an apparent gap is invent a step to fill it."""
        chain = describe_blocks(self.intake)
        self.assertIn("CAP-02", chain)
        self.assertIn("[OUT OF SCOPE]", chain)


class TestEverythingExcluded(unittest.TestCase):
    """A declaration where every block is drawn and then excluded has said something, and it is not
    "walk me end to end", which is what the undivided-graph fallback would have done."""

    def test_nothing_is_walked(self):
        intake = read_intake(str(EXAMPLE))
        intake = replace(intake, capabilities=[replace(c, out_of_scope=True)
                                               for c in intake.capabilities])
        graph = DecisionGraph(intake.decisions, intake.states)
        self.assertEqual(spans_for(graph, intake.capabilities), [])


if __name__ == "__main__":
    unittest.main()
