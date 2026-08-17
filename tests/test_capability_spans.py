"""Scenarios are enumerated one capability at a time, not once over the whole journey.

A real agent is a sequence of blocks. It identifies a cardmember, then verifies them, then
verifies a charge. Each block has a handful of routes through it; walking the journey end to end
multiplies them, so three blocks of five routes is a hundred and twenty-five scenarios that
differ only in how an earlier block was entered. At depth ten it is several hundred, and nobody
tests "identified by document, then verified by security question, then charge accepted" and
"identified by one-time code, then verified by security question, then charge accepted" as two
separate exercises — the second block behaves identically in both.

So the walk is scoped to a block: from one entry state, to that block's exits. A block entered
two ways gets one scenario set per entry, stated as a precondition, which is the distinction that
does matter — the second block genuinely might behave differently depending on how it was
entered, and that is a claim worth testing once rather than once per upstream route.

The counts in this file are the whole argument, so they are asserted exactly.
"""
import unittest

from scenario_generator.core.graph import DecisionGraph, Span, enumerate_by_span, spans_for
from scenario_generator.core.models import Capability, Decision, Persona, State, Tool
from scenario_generator.core.generation import instantiate_span, number_scenarios

# A chain of blocks, each the same shape: an entry, two binary decisions, and three ways out --
# a handoff to the next block, reachable two ways, and a failure ending, reachable two ways.
#
#   S{n}0 -> DEC-{n}1 Yes -> S{n}1 -> DEC-{n}2 Ok  -> S{n}X   handoff to the next block
#                                             Bad -> S{n}E   ending
#                     No  -> S{n}2 -> DEC-{n}3 Ok  -> S{n}X
#                                             Bad -> S{n}E
#
# Four routes through each block. Walked whole, the two that hand on multiply against everything
# downstream; walked block by block, they add. That difference is the entire point of the change,
# and it is only visible at this depth -- on a graph of one decision per block, walking blocks is
# the more expensive of the two, which is why the fixture is built rather than drawn by hand.
def _chain(blocks: int):
    """`blocks` blocks in sequence, as (decisions, states, capabilities)."""
    decisions, states, capabilities = [], [], []
    for n in range(1, blocks + 1):
        last = n == blocks
        entry = "S-%d00" % n
        handoff = "S-%d00" % (n + 1) if not last else "S-%dX" % n
        ending = "S-%dE" % n
        capability = "CAP-%02d" % n

        decisions += [
            Decision("DEC-%d1" % n, "Open block %d" % n, capability, "", ["Yes", "No"]),
            Decision("DEC-%d2" % n, "Settle block %d one way" % n, capability, "", ["Ok", "Bad"]),
            Decision("DEC-%d3" % n, "Settle block %d the other" % n, capability, "",
                     ["Ok", "Bad"]),
        ]
        states += [
            State(entry, "Start" if n == 1 else "DEC-%d2=Ok, DEC-%d3=Ok" % (n - 1, n - 1),
                  "Block %d begins" % n, ["DEC-%d1" % n], False),
            State("S-%d1" % n, "DEC-%d1=Yes" % n, "Block %d, first way" % n,
                  ["DEC-%d2" % n], False),
            State("S-%d2" % n, "DEC-%d1=No" % n, "Block %d, second way" % n,
                  ["DEC-%d3" % n], False),
            State(ending, "DEC-%d2=Bad, DEC-%d3=Bad" % (n, n), "Block %d failed" % n,
                  [], True, "Termination"),
        ]
        if last:
            states.append(State(handoff, "DEC-%d2=Ok, DEC-%d3=Ok" % (n, n),
                                "Block %d done" % n, [], True, "Happy path"))
        capabilities.append(Capability(capability, "Block %d" % n, "Gating",
                                       entry_states=(entry,), exit_states=(handoff, ending)))
    return decisions, states, capabilities


def _chain_counts(blocks: int):
    """(whole-journey routes, block-by-block routes) for a chain of that many blocks."""
    decisions, states, capabilities = _chain(blocks)
    graph = DecisionGraph(decisions, states)
    whole = sum(len(w) for _, w, _ in enumerate_by_span(graph, []))
    scoped = sum(len(w) for _, w, _ in enumerate_by_span(graph, capabilities))
    return whole, scoped


# A small, readable graph for everything that is about what a scenario *says* rather than how
# many there are.
_DECISIONS = [
    Decision("DEC-01", "Identify the cardmember", "CAP-01", "credentials",
             ["By document", "By code", "Cannot identify"]),
    Decision("DEC-02", "Verify the cardmember", "CAP-02", "security answers",
             ["Verified", "Refused"]),
    Decision("DEC-03", "Verify the charge", "CAP-03", "charge detail",
             ["Filed", "Declined"]),
]
_STATES = [
    State("S-00", "Start", "The chat opens", ["DEC-01"], False),
    State("S-01", "DEC-01=By document", "Identified from a document", ["DEC-02"], False),
    State("S-02", "DEC-01=By code", "Identified by one-time code", ["DEC-02"], False),
    State("S-03", "DEC-01=Cannot identify", "Locked out", [], True, "Termination"),
    State("S-04", "DEC-02=Verified", "Cardmember verified", ["DEC-03"], False),
    State("S-05", "DEC-02=Refused", "Verification refused", [], True, "Escalation"),
    State("S-06", "DEC-03=Filed", "Dispute filed", [], True, "Happy path"),
    State("S-07", "DEC-03=Declined", "Charge upheld", [], True, "Fallback"),
]
_CAPABILITIES = [
    Capability("CAP-01", "Identification", "Gating",
               entry_states=("S-00",), exit_states=("S-01", "S-02", "S-03")),
    Capability("CAP-02", "Verification", "Gating",
               entry_states=("S-01", "S-02"), exit_states=("S-04", "S-05")),
    Capability("CAP-03", "Charge verification", "Transactional",
               entry_states=("S-04",), exit_states=("S-06", "S-07")),
]

_PERSONAS = [Persona("P1", "Cardmember", ["Wants the charge reversed"], True)]


def _graph():
    return DecisionGraph(_DECISIONS, _STATES)


def _scenarios(capabilities=_CAPABILITIES):
    graph = _graph()
    out = []
    for span, walked, augmented in enumerate_by_span(graph, capabilities):
        out += instantiate_span(span, walked, augmented, graph, _PERSONAS, [])
    return number_scenarios(out)


class TestTheWalkIsScopedToABlock(unittest.TestCase):
    def test_a_capability_is_walked_from_its_entry_to_its_exits(self):
        graph = _graph()
        walked = [w for span, w, _ in enumerate_by_span(graph, _CAPABILITIES)
                  if span.capability_id == "CAP-01"][0]
        self.assertEqual(len(walked), 3, "three outcomes out of the identification block")
        for path in walked:
            self.assertIn(path[-1].next_state, {"S-01", "S-02", "S-03"})
            self.assertEqual(len(path), 1, "the walk did not stop at the block's edge")

    def test_a_block_entered_two_ways_is_walked_from_each(self):
        spans = [s for s in spans_for(_graph(), _CAPABILITIES) if s.capability_id == "CAP-02"]
        self.assertEqual(sorted(s.entry_state for s in spans), ["S-01", "S-02"])

    def test_the_whole_journey_is_no_longer_one_scenario(self):
        """The behaviour being replaced. Nothing walks start to finish any more."""
        for scenario in _scenarios():
            self.assertLessEqual(len(scenario.path), 1)


class TestTheCountIsWhatChanges(unittest.TestCase):
    def test_walking_the_journey_whole_multiplies_the_blocks(self):
        """Each block that hands on multiplies against everything downstream."""
        self.assertEqual([_chain_counts(n)[0] for n in (1, 2, 3, 4)], [4, 10, 22, 46])

    def test_walking_block_by_block_adds_them_instead(self):
        """Four routes per block, however many blocks there are."""
        self.assertEqual([_chain_counts(n)[1] for n in (1, 2, 3, 4)], [4, 8, 12, 16])

    def test_the_gap_widens_with_every_block(self):
        """The reason this change exists. A real use case is ten blocks deep, not four."""
        whole, scoped = _chain_counts(6)
        self.assertGreater(whole, 5 * scoped)

    def test_a_shallow_graph_is_not_helped_and_that_is_expected(self):
        """Stated so nobody reads the numbers above as a promise. Blocks of one decision each
        cost more walked separately than walked through, because every entry is walked from --
        there is simply nothing to collapse."""
        self.assertEqual(len(_scenarios(capabilities=[])), 7)
        self.assertEqual(len(_scenarios()), 9)

    def test_no_scenario_repeats_a_block_another_scenario_already_covers(self):
        by_capability = {}
        for scenario in _scenarios():
            signature = tuple((s.decision_id, s.variant) for s in scenario.path)
            key = (scenario.capability_id, scenario.precondition, signature)
            self.assertNotIn(key, by_capability, "the same route walked twice")
            by_capability[key] = scenario


class TestAScenarioSaysWhereItStarts(unittest.TestCase):
    def test_a_downstream_scenario_carries_the_precondition_it_needs(self):
        """Without this the tester runs a different conversation from the one being asked for."""
        verification = [s for s in _scenarios() if s.capability_id == "CAP-02"]
        self.assertTrue(verification)
        for scenario in verification:
            self.assertIn("Identified", scenario.precondition)
            self.assertIn("Verification", scenario.precondition)

    def test_the_two_entries_into_a_block_are_told_apart_by_it(self):
        preconditions = {s.precondition for s in _scenarios() if s.capability_id == "CAP-02"}
        self.assertEqual(len(preconditions), 2, "both entries produced the same instruction")

    def test_the_first_block_needs_no_precondition_beyond_its_own_start(self):
        first = [s for s in _scenarios() if s.capability_id == "CAP-01"]
        for scenario in first:
            self.assertIn("The chat opens", scenario.precondition)

    def test_a_whole_graph_walk_states_no_precondition_at_all(self):
        """A scenario that genuinely starts at the start does not need saying, and saying it on
        every scenario in an undivided pack is noise."""
        for scenario in _scenarios(capabilities=[]):
            self.assertEqual(scenario.precondition, "")

    def test_a_scenario_is_seeded_at_its_own_entry_not_at_the_graphs_start(self):
        charge = next(s for s in _scenarios() if s.capability_id == "CAP-03")
        self.assertEqual(charge.seeded_state, "Cardmember verified")


class TestAnUndividedIntakeStillWorks(unittest.TestCase):
    """The ordinary state of a use case on its first run. A blank scenario space here would read
    as the tool being broken rather than as a step nobody has taken yet."""

    def test_no_spans_drawn_walks_the_whole_graph(self):
        self.assertEqual(len(_scenarios(capabilities=[])), 7)

    def test_a_capability_with_no_span_drawn_is_not_walked_as_a_block(self):
        untouched = [Capability("CAP-01", "Identification", "Gating")]
        self.assertEqual([s.is_whole_graph for s in spans_for(_graph(), untouched)], [True])

    def test_a_span_naming_a_state_the_graph_does_not_have_is_refused(self):
        """A mistyped id would enumerate nothing, and a block contributing no scenarios is the
        quietest way for this to go wrong."""
        typo = [Capability("CAP-01", "Identification", "Gating",
                           entry_states=("S-99",), exit_states=("S-01",))]
        self.assertEqual([s.is_whole_graph for s in spans_for(_graph(), typo)], [True])


class TestOutcomesTheWalkCannotReachAreStillCovered(unittest.TestCase):
    def test_a_retry_bounded_outcome_inside_a_block_gets_its_own_route(self):
        decisions = [Decision("DEC-01", "Identity check", "CAP-01", "", ["Pass", "Fail"],
                              max_attempts=3),
                     Decision("DEC-09", "Rare route", "CAP-01", "", ["Escalate", "Refuse"])]
        states = [State("S-00", "Start", "The chat opens", ["DEC-01"], False),
                  State("S-01", "DEC-01=Pass", "Verified", [], True, "Happy path"),
                  State("S-02", "DEC-01=Fail", "Retry", ["DEC-01"], False),
                  State("S-08", "DEC-09=Escalate", "Handed over", [], True, "Escalation"),
                  State("S-09", "DEC-09=Refuse", "Refused", [], True, "Termination")]
        graph = DecisionGraph(decisions, states)
        capabilities = [Capability("CAP-01", "Identification", "Gating",
                                   entry_states=("S-00",), exit_states=("S-01", "S-02"))]

        taken = set()
        for _, walked, augmented in enumerate_by_span(graph, capabilities):
            for path in walked + augmented:
                taken.update((step.decision_id, step.variant) for step in path)

        # DEC-09 is disconnected: nothing in the graph leads to it, so only the capability it
        # names says which block it belongs to.
        self.assertIn(("DEC-09", "Escalate"), taken)
        self.assertIn(("DEC-09", "Refuse"), taken)

    def test_a_disconnected_decision_is_walked_once_even_with_two_entries(self):
        decisions = [Decision("DEC-01", "Check", "CAP-01", "", ["Pass", "Fail"]),
                     Decision("DEC-09", "Orphan", "CAP-01", "", ["Yes", "No"])]
        states = [State("S-00", "Start", "Opens", ["DEC-01"], False),
                  State("S-05", "Start again", "Also opens", ["DEC-01"], False),
                  State("S-01", "DEC-01=Pass", "Done", [], True, "Happy path"),
                  State("S-02", "DEC-01=Fail", "Stopped", [], True, "Termination"),
                  State("S-08", "DEC-09=Yes", "One way", [], True, "Happy path"),
                  State("S-09", "DEC-09=No", "The other", [], True, "Fallback")]
        graph = DecisionGraph(decisions, states)
        capabilities = [Capability("CAP-01", "Identification", "Gating",
                                   entry_states=("S-00", "S-05"), exit_states=("S-01", "S-02"))]

        orphan_routes = [p for _, _, augmented in enumerate_by_span(graph, capabilities)
                         for p in augmented
                         if any(step.decision_id == "DEC-09" for step in p)]
        self.assertEqual(len(orphan_routes), 2, "one route per outcome of the orphan, not four")


if __name__ == "__main__":
    unittest.main()
