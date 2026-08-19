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


class TestTheBlockSurvivesTheWorkbook(unittest.TestCase):
    """A scenario read back out of the metadata workbook has to still know which block it walks.

    Every stage after the workflow reads the scenario space off disk rather than out of memory, so
    a field the writer records and the reader drops is a field that exists only until the next
    stage runs -- and the precondition is the one instruction that makes a capability-scoped
    scenario runnable at all.
    """

    def _round_trip(self):
        import tempfile
        from pathlib import Path

        from scenario_generator.core.models import IntakeData
        from scenario_generator.io import read_scenarios, write_space_metadata

        intake = IntakeData(use_case={"Use case name": "Disputes"}, personas=_PERSONAS,
                            capabilities=_CAPABILITIES, decisions=_DECISIONS, states=_STATES,
                            tools=[])
        path = Path(tempfile.mkdtemp()) / "space.xlsx"
        written = _scenarios()
        write_space_metadata(str(path), intake, written)
        return written, read_scenarios(str(path), intake)

    def test_the_capability_and_precondition_come_back(self):
        written, read_back = self._round_trip()
        self.assertEqual([s.capability_id for s in read_back],
                         [s.capability_id for s in written])
        self.assertEqual([s.precondition for s in read_back],
                         [s.precondition for s in written])


class TestTheHandDrawnSpanSurvivesTheSequentialPass(unittest.TestCase):
    """A span is drawn once, by hand, and every re-run of the intake stage must leave it alone.

    Nothing proposes a span: where a capability begins and ends is the validator's call, and it is
    the one thing in the workbook a model never writes. Which makes it the one thing at risk --
    drafting and revising both rewrite the workbook wholesale, so a span survives only because it
    is read off the file before that happens and put back afterwards. A pass that quietly drops it
    costs an afternoon of work and shows nothing on screen except a scenario count that changed.
    """

    _DRAWN = {
        "use_case": {"name": "Disputes", "objective": "Handle a disputed charge",
                     "agent_type": "Chat", "channel": "Web",
                     "handoff_triggers": "The cardmember asks for a person",
                     "safety_requirements": "No account detail before identification",
                     "success_criteria": "The dispute is filed or refused with a reason"},
        "personas": [{"id": "P-01", "name": "Cardmember", "applies_to": "Wants a reversal",
                      "is_default": True},
                     {"id": "P-ADV", "name": "Impostor", "applies_to": "Wants another account",
                      "is_default": False}],
        "capabilities": [{"id": "CAP-01", "name": "Identification", "type": "Gating"},
                         {"id": "CAP-02", "name": "Charge handling", "type": "Transactional"}],
        "decisions": [
            {"id": "DEC-01", "name": "Identify", "capability_id": "CAP-01", "inputs": "Card",
             "outcomes": ["Identified", "Not identified"], "input_source": "User",
             "max_attempts": 1, "outcome_condition": ""},
            {"id": "DEC-02", "name": "Assess the charge", "capability_id": "CAP-02",
             "inputs": "Charge", "outcomes": ["Disputable", "Not disputable"],
             "input_source": "Tool", "max_attempts": 1, "outcome_condition": ""}],
        "states": [
            {"id": "S-00", "reached_via": "Start", "description": "The chat opens",
             "next_decisions": ["DEC-01"], "is_terminal": False, "outcome_type": ""},
            {"id": "S-01", "reached_via": "DEC-01=Identified",
             "description": "Identified; asking which charge", "next_decisions": ["DEC-02"],
             "is_terminal": False, "outcome_type": ""},
            {"id": "S-02", "reached_via": "DEC-01=Not identified",
             "description": "Not identified; chat ended", "next_decisions": [],
             "is_terminal": True, "outcome_type": "Termination"},
            {"id": "S-03", "reached_via": "DEC-02=Disputable",
             "description": "Dispute filed", "next_decisions": [], "is_terminal": True,
             "outcome_type": "Happy path"},
            {"id": "S-04", "reached_via": "DEC-02=Not disputable",
             "description": "Refused with a reason", "next_decisions": [], "is_terminal": True,
             "outcome_type": "Termination"}],
        "tools": []}

    def setUp(self):
        import json
        import tempfile
        from pathlib import Path

        from scenario_generator.core.intake import set_capability_span
        from scenario_generator.pipeline import draft_intake_workbook

        self.work = Path(tempfile.mkdtemp())
        self.context = self.work / "run_context.md"
        self.context.write_text("Identification, then the charge is assessed.\n",
                                encoding="utf-8")
        self.path = self.work / "intake.xlsx"

        payload = json.dumps(self._DRAWN)
        self.complete = lambda system, user, **kwargs: payload
        draft_intake_workbook(str(self.context), str(self.path), complete=self.complete)

        # Drawn by hand afterwards, which is the only way a span is ever set.
        set_capability_span(str(self.path), "CAP-01", ["S-00"], ["S-01", "S-02"])
        set_capability_span(str(self.path), "CAP-02", ["S-01"], ["S-03", "S-04"])

    def _spans(self):
        from scenario_generator.core.intake import read_intake

        return {c.id: (tuple(c.entry_states), tuple(c.exit_states))
                for c in read_intake(str(self.path)).capabilities}

    def test_the_span_is_there_to_begin_with(self):
        self.assertEqual(self._spans()["CAP-02"], (("S-01",), ("S-03", "S-04")))

    def test_redrafting_over_it_keeps_it(self):
        from scenario_generator.pipeline import draft_intake_workbook

        draft_intake_workbook(str(self.context), str(self.path), complete=self.complete)
        self.assertEqual(self._spans()["CAP-01"], (("S-00",), ("S-01", "S-02")))
        self.assertEqual(self._spans()["CAP-02"], (("S-01",), ("S-03", "S-04")))

    def test_revising_it_keeps_it(self):
        from scenario_generator.pipeline import revise_intake_workbook

        revise_intake_workbook(str(self.path), str(self.path), context_path=str(self.context),
                               complete=self.complete)
        self.assertEqual(self._spans()["CAP-02"], (("S-01",), ("S-03", "S-04")))

    def test_the_scenarios_are_walked_per_block_after_a_re_run(self):
        """What the span is for. If it were lost, this would walk the whole graph instead."""
        from scenario_generator.core.intake import read_intake
        from scenario_generator.pipeline import build_scenario_space, draft_intake_workbook

        draft_intake_workbook(str(self.context), str(self.path), complete=self.complete)
        space = build_scenario_space(read_intake(str(self.path)), with_probes=False)
        self.assertEqual({s.capability_id for s in space}, {"CAP-01", "CAP-02"})


class TestWhereABlockIsLeftIsArithmetic(unittest.TestCase):
    """Where a block *starts* is a judgement about the agent. Where it *ends* is not.

    Walk what the block holds from its entry, and every state one of its decisions lands on that
    the block is no longer inside is a way out. Asking a person to type that is asking them to
    compute something the tool already knows -- which is what made the span control two lists of
    twenty-eight checkboxes when the question was "where does identification start".

    Read off the example workbook rather than the small fixture above, because the cases that
    matter are the ones a real declaration has: a block that ends several ways it never listed,
    and a block entered from two positions.
    """

    @classmethod
    def setUpClass(cls):
        from pathlib import Path

        from scenario_generator.core.graph import DecisionGraph
        from scenario_generator.core.intake import read_intake

        example = (Path(__file__).resolve().parent.parent / "examples" / "intakes"
                   / "1_disputes_three_blocks.xlsx")
        cls.intake = read_intake(str(example))
        cls.graph = DecisionGraph(cls.intake.decisions, cls.intake.states)
        cls.bounded = [c for c in cls.intake.capabilities if c.is_bounded]

    def _derived(self, capability):
        from scenario_generator.core.graph import exits_for
        return exits_for(self.graph, capability.id, capability.entry_states,
                         self.intake.decisions)

    def test_it_keeps_every_exit_somebody_declared(self):
        """A proposal that quietly dropped a hand-drawn boundary would be worse than no proposal:
        accepting it would undo the work it was offered to save."""
        for capability in self.bounded:
            lost = set(capability.exit_states) - set(self._derived(capability))
            self.assertEqual(lost, set(), f"{capability.id} lost {lost}")

    def test_it_finds_an_ending_nobody_listed(self):
        """The reason to offer it at all. A block hands on at the states somebody wrote down and
        *ends* at several more, and those endings are what the pack tests."""
        extra = {c.id: set(self._derived(c)) - set(c.exit_states) for c in self.bounded}
        self.assertTrue(any(extra.values()),
                        "the derivation found nothing the declaration had not already listed")

    def test_nothing_inside_a_block_is_offered_as_a_way_out(self):
        from scenario_generator.core.graph import decisions_owned_by, states_inside

        for capability in self.bounded:
            owned = decisions_owned_by(self.graph, capability.id, capability.entry_states,
                                       self.intake.decisions)
            inside = states_inside(self.graph, capability.entry_states, owned)
            for state_id in self._derived(capability):
                state = self.graph.state(state_id)
                self.assertTrue(state.is_terminal or state_id not in inside,
                                f"{state_id} is inside {capability.id} and was called a way out")

    def test_a_capability_entered_two_ways_derives_from_both(self):
        """A block entered from two positions is walked from each, so its ways out are the union
        -- deriving from the first entry alone would lose whatever only the second reaches."""
        from scenario_generator.core.graph import exits_for

        several = next((c for c in self.bounded if len(c.entry_states) > 1), None)
        self.assertIsNotNone(several, "the example no longer has a block entered two ways")
        both = set(self._derived(several))
        for entry in several.entry_states:
            one = set(exits_for(self.graph, several.id, [entry], self.intake.decisions))
            self.assertTrue(one <= both, f"deriving from {entry} alone found something the pair "
                                         f"did not: {one - both}")

    def test_a_capability_with_no_entry_derives_nothing_rather_than_guessing(self):
        from scenario_generator.core.graph import exits_for

        self.assertEqual(exits_for(self.graph, "CAP-01", [], self.intake.decisions), [])

    def test_the_shortlist_of_entries_is_where_its_own_decisions_can_be_taken_from(self):
        from scenario_generator.core.graph import entry_candidates

        mine = {d.id for d in self.intake.decisions if d.trigger_capability == "CAP-02"}
        shortlist = entry_candidates(self.graph, "CAP-02", self.intake.decisions)
        self.assertTrue(shortlist, "no entry candidates at all")
        for state_id in shortlist:
            offered = set(self.graph.state(state_id).next_decisions)
            self.assertTrue(offered & mine, f"{state_id} offers nothing CAP-02 owns")

    def test_the_shortlist_contains_what_is_already_drawn(self):
        """Otherwise the form would open with a saved entry missing, and the next save would clear
        it without anybody seeing it go."""
        from scenario_generator.core.graph import entry_candidates

        for capability in self.bounded:
            shortlist = set(entry_candidates(self.graph, capability.id, self.intake.decisions))
            self.assertTrue(set(capability.entry_states) <= shortlist,
                            f"{capability.id}'s drawn entries are not all in its shortlist")
