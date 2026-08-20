"""Behaviour driven by the intake schema: declared outcome type, per-decision retry bounds,
and the separation of internal steps from conversational turns.
"""
import unittest

from metric.phases.scenario_generator.workflow.generation import categorise, instantiate_path
from metric.domain.graph import DecisionGraph, enumerate_paths
from metric.domain.models import Decision, Persona, State, Step

_PERSONAS = [Persona("P1", "Default", [], True)]


class TestOutcomeTypeDrivesCategory(unittest.TestCase):
    def test_declared_outcome_type_wins_over_the_keyword_heuristic(self):
        """'Fail' would read as Fallback by keyword; the declared type must override it."""
        states = [State("S-00", "Start", "Start", ["DEC-01"], False),
                  State("S-01", "DEC-01=Fail", "Handed off", [], True, outcome_type="Escalation")]
        graph = DecisionGraph([Decision("DEC-01", "Auth", "CAP-01", "", ["Fail"])], states)
        path = [Step("DEC-01", "Fail", "S-01")]
        self.assertEqual(categorise(path, graph), "Escalation")

    def test_heuristic_still_applies_when_nothing_is_declared(self):
        states = [State("S-00", "Start", "Start", ["DEC-01"], False),
                  State("S-01", "DEC-01=Escalate", "Escalated", [], True)]
        graph = DecisionGraph([Decision("DEC-01", "Route", "CAP-01", "", ["Escalate"])], states)
        self.assertEqual(categorise([Step("DEC-01", "Escalate", "S-01")], graph), "Escalation")


class TestMaxAttemptsBoundsLoops(unittest.TestCase):
    def _graph(self, max_attempts):
        decisions = [Decision("DEC-01", "Auth", "CAP-01", "", ["Pass", "Fail"],
                              max_attempts=max_attempts)]
        states = [State("S-00", "Start", "Start", ["DEC-01"], False),
                  State("S-01", "DEC-01=Fail", "Retry", ["DEC-01"], False),
                  State("S-02", "DEC-01=Pass", "Done", [], True)]
        return DecisionGraph(decisions, states)

    def test_retry_depth_follows_the_declared_limit(self):
        """A decision allowing three tries must produce a deeper path than one allowing one."""
        one, _ = enumerate_paths(self._graph(1))
        three, _ = enumerate_paths(self._graph(3))
        self.assertGreater(max(len(p) for p in three), max(len(p) for p in one))

    def _graph_that_says_what_the_last_failure_does(self):
        """The same agent, declared properly: a second state also reached by DEC-01=Fail, which is
        where the interaction lands once the attempts are gone."""
        decisions = [Decision("DEC-01", "Auth", "CAP-01", "", ["Pass", "Fail"], max_attempts=2)]
        states = [State("S-00", "Start", "Start", ["DEC-01"], False),
                  State("S-01", "DEC-01=Fail", "Retry", ["DEC-01"], False),
                  State("S-03", "DEC-01=Fail", "Locked out", [], True, "Termination"),
                  State("S-02", "DEC-01=Pass", "Done", [], True, "Happy path")]
        return DecisionGraph(decisions, states)

    def test_exhausting_the_retry_limit_is_a_scenario_where_the_intake_says_where_it_lands(self):
        """Running out of attempts is a real outcome, and testable exactly when somebody has
        written down what it is."""
        walked, _ = enumerate_paths(self._graph_that_says_what_the_last_failure_does())
        exhausted = [p for p in walked if all(step.variant == "Fail" for step in p)]
        self.assertTrue(exhausted, "the all-failures route was discarded")
        self.assertEqual(max(len(p) for p in exhausted), 2)
        self.assertEqual(exhausted[0][-1].next_state, "S-03")

    def test_where_it_does_not_say_the_route_is_not_a_scenario(self):
        """The same walk, against a declaration that stops at "Retry" and never says what the
        second failure does. There is no expected outcome to hold the model owner to, so issuing it
        would ask them to run a conversation nobody can mark."""
        walked, augmented = enumerate_paths(self._graph(2))
        self.assertEqual(
            [p for p in walked + augmented if all(s.variant == "Fail" for s in p)], [])

    def test_every_issued_route_ends_where_the_intake_says_the_interaction_ends(self):
        graph = self._graph(2)
        walked, augmented = enumerate_paths(graph)
        for path in walked + augmented:
            landing = graph.state(path[-1].next_state)
            self.assertIsNotNone(landing, path)
            self.assertTrue(landing.is_terminal, path)

    def test_no_path_exceeds_the_declared_limit(self):
        walked, _ = enumerate_paths(self._graph(2))
        for path in walked:
            fired = sum(1 for step in path if step.decision_id == "DEC-01")
            self.assertLessEqual(fired, 2)


class TestInputSourceSeparatesStepsFromTurns(unittest.TestCase):
    def _scenario(self, sources):
        decisions = [Decision(f"DEC-0{i}", f"Step {i}", "CAP-01", "", ["Ok"], input_source=source)
                     for i, source in enumerate(sources, start=1)]
        states = [State("S-00", "Start", "Start", [d.id for d in decisions], False)]
        graph = DecisionGraph(decisions, states)
        path = [Step(d.id, "Ok", "S-00") for d in decisions]
        return instantiate_path(path, graph, _PERSONAS, [], "graph")

    def test_only_user_steps_become_turns(self):
        scenario = self._scenario(["User", "Tool", "User"])
        self.assertEqual(len(scenario.turn_meta), 3)
        self.assertEqual(scenario.turn_count, 2)

    def test_a_fully_internal_path_is_a_single_trigger(self):
        """A planner or batch agent has no user turns, but is still one thing to run."""
        scenario = self._scenario(["Tool", "Memory-Session", "System-Context"])
        self.assertEqual(scenario.user_turns, [])
        self.assertEqual(scenario.turn_count, 1)


if __name__ == "__main__":
    unittest.main()
