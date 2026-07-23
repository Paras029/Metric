"""Behaviour driven by the intake schema: declared outcome type, per-decision retry bounds,
and the separation of internal steps from conversational turns.
"""
import unittest

from scenario_generator.core.generation import categorise, instantiate_path
from scenario_generator.core.graph import DecisionGraph, enumerate_paths
from scenario_generator.core.models import Decision, Persona, State, Step

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

    def test_exhausting_the_retry_limit_is_itself_a_path(self):
        """Running out of attempts is a real outcome and must not be silently dropped."""
        walked, _ = enumerate_paths(self._graph(2))
        exhausted = [p for p in walked if all(step.variant == "Fail" for step in p)]
        self.assertTrue(exhausted, "the all-failures path was discarded")
        self.assertEqual(max(len(p) for p in exhausted), 2)

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
