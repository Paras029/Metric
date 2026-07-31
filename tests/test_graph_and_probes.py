"""Where the benchmark's shape is decided, and the ways it went quietly wrong.

Every case here is one a real intake produces and none of them raised anything: the walk started
somewhere it should not have, a scenario reported the wrong opening state, or a set of adversarial
probes stopped being generated because a capability was typed with a space. A benchmark that is
silently missing routes is the failure this whole tool exists to prevent, so each is pinned.
"""
import logging
import unittest

from scenario_generator.core.generation import instantiate_all
from scenario_generator.core.graph import DecisionGraph, enumerate_paths
from scenario_generator.core.models import Capability, Decision, IntakeData, Persona, State
from scenario_generator.core.probes import build_probes, evaluate


def _intake(capabilities=(), decisions=(), states=(), tools=()) -> IntakeData:
    return IntakeData(use_case={"Use case name": "Test"},
                      personas=[Persona("P1", "Cardmember", [], True)],
                      capabilities=list(capabilities), decisions=list(decisions),
                      states=list(states), tools=list(tools))


class TestWhereTheWalkStarts(unittest.TestCase):
    """A start state is one nothing leads to, not one whose wording happens to contain 'connect'."""

    def _graph(self, reached_via: str) -> DecisionGraph:
        return DecisionGraph(
            [Decision("DEC-01", "Session", "CAP-01", "", ["Fresh", "Reconnected"]),
             Decision("DEC-02", "Identity", "CAP-01", "", ["Pass", "Fail"])],
            [State("S-00", "Start", "The chat opens", ["DEC-01"], False),
             State("S-01", reached_via, "Session resumed", ["DEC-02"], False),
             State("S-02", "DEC-02=Pass", "Verified", [], True, "Happy path"),
             State("S-03", "DEC-02=Fail", "Locked out", [], True, "Termination")])

    def test_a_state_reached_by_a_decision_is_never_a_start(self):
        """'DEC-01=Reconnected' contains 'connect'; it is still somewhere the agent arrives at."""
        self.assertEqual(self._graph("DEC-01=Reconnected").start_states, ["S-00"])

    def test_a_state_nothing_leads_to_and_named_like_an_opening_is_a_start(self):
        graph = self._graph("Inbound voice")
        self.assertEqual(graph.start_states, ["S-00", "S-01"])

    def test_a_false_start_does_not_invent_routes_the_agent_cannot_take(self):
        """Walking from a mid-graph state produces scenarios that skip the steps before it."""
        walked, _ = enumerate_paths(self._graph("DEC-01=Reconnected"))
        self.assertTrue(all(path[0].decision_id == "DEC-01" for path in walked),
                        "every route must open on the decision the start state offers")


class TestWhichStartAScenarioOpenedFrom(unittest.TestCase):
    """An agent with two ways in has two seeded states, and the registry has to say which."""

    def setUp(self):
        self.intake = _intake(
            capabilities=[Capability("CAP-01", "Identity", "Gating")],
            decisions=[Decision("DEC-01", "Chat identity", "CAP-01", "", ["Pass"]),
                       Decision("DEC-02", "Voice identity", "CAP-01", "", ["Pass"])],
            states=[State("S-00", "Start (chat)", "The cardmember opens a chat", ["DEC-01"], False),
                    State("S-10", "Start (voice)", "The cardmember calls in", ["DEC-02"], False),
                    State("S-01", "DEC-01=Pass", "Verified in chat", [], True, "Happy path"),
                    State("S-11", "DEC-02=Pass", "Verified on the call", [], True, "Happy path")])

    def test_each_route_reports_the_state_it_actually_opened_from(self):
        graph = DecisionGraph(self.intake.decisions, self.intake.states)
        walked, augmented = enumerate_paths(graph)
        scenarios = instantiate_all(walked, augmented, graph, self.intake.personas,
                                    self.intake.tools)

        seeded = {s.path[0].decision_id: s.seeded_state for s in scenarios if s.path}
        self.assertEqual(seeded["DEC-01"], "The cardmember opens a chat")
        self.assertEqual(seeded["DEC-02"], "The cardmember calls in")


class TestWhichProbesApply(unittest.TestCase):
    """Probe applicability turns on the declared capability type, so its spelling cannot decide it."""

    def _probe_ids(self, capability_type: str) -> set:
        intake = _intake(capabilities=[Capability("CAP-01", "Handles cardmember data",
                                                  capability_type)])
        return {s.probe_id for s in build_probes(intake)}

    def test_a_capability_typed_with_a_space_generates_the_same_probes(self):
        canonical = self._probe_ids("PII-handling")
        self.assertEqual(self._probe_ids("PII handling"), canonical)
        self.assertEqual(self._probe_ids("pii_handling"), canonical)
        self.assertEqual(self._probe_ids("  PII-Handling  "), canonical)

    def test_the_pii_probes_are_the_ones_at_stake(self):
        """Not a tautology: the spellings above must add probes a blank type does not get."""
        self.assertGreater(len(self._probe_ids("PII handling")), len(self._probe_ids("")))

    def test_a_type_outside_the_vocabulary_still_matches_no_predicate(self):
        self.assertEqual(self._probe_ids("Something else"), self._probe_ids(""))

    def test_an_unknown_condition_says_so_rather_than_dropping_probes_in_silence(self):
        with self.assertLogs("scenario_generator.core.probes", level=logging.WARNING) as caught:
            applied = evaluate("has_teleportation", _intake())
        self.assertFalse(applied)
        self.assertIn("has_teleportation", "".join(caught.output))


class TestEnumerationDoesNotRepeatItself(unittest.TestCase):
    def test_one_search_covers_every_outcome_a_decision_is_missing(self):
        """The route to a decision is the same whichever of its outcomes is being reached for."""
        searches = []
        graph = DecisionGraph(
            [Decision("DEC-01", "Open", "CAP-01", "", ["Go"]),
             Decision("DEC-02", "Branch", "CAP-01", "", ["A", "B", "C", "D"])],
            [State("S-00", "Start", "Opens", ["DEC-01"], False),
             State("S-01", "DEC-01=Go", "Moving", ["DEC-02"], False)])

        from scenario_generator.core import graph as graph_module
        original = graph_module._shortest_prefix_to

        def counted(g, decision_id):
            searches.append(decision_id)
            return original(g, decision_id)

        graph_module._shortest_prefix_to = counted
        try:
            enumerate_paths(graph)
        finally:
            graph_module._shortest_prefix_to = original

        self.assertLessEqual(searches.count("DEC-02"), 1)


if __name__ == "__main__":
    unittest.main()
