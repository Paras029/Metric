"""Walking the declared graph into routes.

The rule the whole enumeration rests on: **a scenario is a route from a start state to a state the
intake marks as ending the interaction.** Nothing shorter. A scenario is a conversation issued to
the model owner with an expected outcome behind it, and a route the declaration stops short of has
no expected outcome to have -- it used to be issued anyway, with an ending of nothing at all, which
asked somebody to run a conversation nobody could mark.

There are three ways a walk stops short, and all three are the declaration running out rather than
the agent finishing: an outcome naming a destination no state declares, a state that leads nowhere
and is not marked as an ending, and every decision on offer having used up its attempts or being
out of scope. None of them produce a scenario.
"""
import unittest

from scenario_generator.core import graph as graph_module

from scenario_generator.core.graph import DecisionGraph, enumerate_paths
from scenario_generator.core.models import Capability, Decision, IntakeData, Persona, State, Tool


def _build_graph():
    decisions = [
        Decision("DEC-01", "Auth", "CAP-01", "creds", ["Pass", "Fail"]),
        Decision("DEC-02", "Lookup", "CAP-02", "id", ["Found", "Not found"]),
    ]
    states = [
        State("S-00", "Start", "start", ["DEC-01"], False),
        State("S-01", "DEC-01=Pass", "authed", ["DEC-02"], False),
        State("S-02", "DEC-01=Fail", "auth failed", [], True),
        State("S-03", "DEC-02=Found", "found", [], True),
        State("S-04", "DEC-02=Not found", "not found", [], True),
    ]
    return DecisionGraph(decisions, states)


class TestGraphEnumeration(unittest.TestCase):
    def test_every_declared_variant_is_covered(self):
        graph = _build_graph()
        walked, augmented = enumerate_paths(graph)
        all_pairs = {(s.decision_id, s.variant) for path in walked + augmented for s in path}
        self.assertIn(("DEC-01", "Pass"), all_pairs)
        self.assertIn(("DEC-01", "Fail"), all_pairs)
        self.assertIn(("DEC-02", "Found"), all_pairs)
        self.assertIn(("DEC-02", "Not found"), all_pairs)

    def test_paths_are_deduplicated_by_signature(self):
        graph = _build_graph()
        walked, augmented = enumerate_paths(graph)
        signatures = [tuple((s.decision_id, s.variant) for s in path) for path in walked + augmented]
        self.assertEqual(len(signatures), len(set(signatures)))


if __name__ == "__main__":
    unittest.main()


class TestOnlyWholeRoutesAreIssued(unittest.TestCase):
    """One graph carrying every way a walk can stop, so the split between the two is visible."""

    def _graph(self):
        return DecisionGraph(
            [Decision("DEC-01", "Identity check", "CAP-01", "", ["Pass", "Fail"], max_attempts=2),
             Decision("DEC-02", "What is asked", "CAP-01", "", ["Dispute", "Odd"]),
             Decision("DEC-07", "Vendor sub-flow", "CAP-01", "", ["A"], out_of_scope=True),
             Decision("DEC-08", "Hand to the vendor", "CAP-01", "", ["Done"])],
            [State("S-00", "Start", "The chat opens", ["DEC-01"], False),
             State("S-01", "DEC-01=Pass", "Verified", ["DEC-02"], False),
             # Fail loops back to DEC-01, and nothing says what the second failure does.
             State("S-02", "DEC-01=Fail", "Retry", ["DEC-01"], False),
             State("S-03", "DEC-02=Dispute", "Dispute filed", [], True, "Happy path"),
             # DEC-02=Odd is declared as an outcome, and no state claims to be reached by it.
             State("S-05", "Start", "Second entry", ["DEC-07", "DEC-08"], False),
             # Reached by an in-scope decision, leads nowhere, and not marked as ending the
             # interaction. Reachable is the point: behind the out-of-scope DEC-07 the walk would
             # never arrive here, and the case would go untested.
             State("S-06", "DEC-08=Done", "Handed to the vendor", [], False)])

    def test_every_issued_route_ends_at_a_declared_ending(self):
        graph = self._graph()
        walked, augmented = enumerate_paths(graph)
        self.assertTrue(walked)
        for path in walked + augmented:
            landing = graph.state(path[-1].next_state)
            self.assertIsNotNone(landing, f"{path} ends at an undeclared destination")
            self.assertTrue(landing.is_terminal, f"{path} ends mid-graph at {landing.id}")

    def test_an_outcome_leading_nowhere_declared_is_not_a_scenario(self):
        walked, augmented = enumerate_paths(self._graph())
        taken = {(step.decision_id, step.variant) for p in walked + augmented for step in p}
        self.assertNotIn(("DEC-02", "Odd"), taken)

    def test_running_out_of_attempts_with_nowhere_to_go_is_not_a_scenario(self):
        walked, _ = enumerate_paths(self._graph())
        self.assertEqual([p for p in walked if all(s.variant == "Fail" for s in p)], [])

    def test_a_state_that_leads_nowhere_and_is_not_an_ending_is_not_one_either(self):
        """S-06 is reached, leads nowhere, and nobody marked it as ending the interaction. It is a
        row somebody has not finished, not a place a conversation can be said to have got to."""
        walked, augmented = enumerate_paths(self._graph())
        self.assertNotIn("S-06", {p[-1].next_state for p in walked + augmented})

    def test_an_outcome_the_walk_misses_is_carried_on_to_an_ending(self):
        """Augmented routes exist to exercise an outcome the exhaustive walk could not reach.
        Stopping the moment it fires made every one of them a route with no declared ending -- the
        one thing a scenario cannot be -- so they continue to an ending like any other route."""
        graph = DecisionGraph(
            [Decision("DEC-01", "Identity check", "CAP-01", "", ["Pass", "Fail"], max_attempts=3),
             Decision("DEC-04", "Rare route", "CAP-01", "", ["Escalate", "Refuse"])],
            [State("S-00", "Start", "The chat opens", ["DEC-01"], False),
             State("S-01", "DEC-01=Pass", "Verified", [], True, "Happy path"),
             State("S-02", "DEC-01=Fail", "Retry", ["DEC-01"], False),
             State("S-08", "DEC-04=Escalate", "Handed over", [], True, "Escalation"),
             State("S-09", "DEC-04=Refuse", "Refused", [], True, "Termination")])
        walked, augmented = enumerate_paths(graph)
        self.assertTrue(augmented, "DEC-04 is unreachable by the walk and needs a focused route")
        for path in augmented:
            self.assertTrue(graph.state(path[-1].next_state).is_terminal, path)

        # And the point of them survives: every declared outcome is still exercised somewhere.
        taken = {(step.decision_id, step.variant) for p in walked + augmented for step in p}
        declared = {(d.id, v) for d in graph.decisions.values() for v in d.variants}
        self.assertEqual(declared - taken, set())


class TestAnAugmentedRouteObeysTheSameRules(unittest.TestCase):
    """The focused routes are still routes, and are bound by everything an ordinary one is.

    What binds them is two different rules, and this graph is built to separate them. DEC-02
    allows one attempt, so no route may take it twice *in a row* -- that is what Max Attempts
    says, and carrying the counts into the focused search is what keeps it true; restarting them
    there produces a route that reads perfectly well and could never happen.

    S-04 routing back to DEC-02 is not that. It is the flow going elsewhere and returning, which
    the intake never claimed to bound, and holding it to the retry count used to delete every
    route through it: the only way onward from DEC-09 is back into DEC-02, so DEC-09's outcome
    could not finish anywhere and was dropped from the space entirely. A declaration that draws a
    loop means the loop to be tested.
    """

    def _graph(self):
        return DecisionGraph(
            [Decision("DEC-01", "Identity check", "CAP-01", "", ["Pass", "Fail"], max_attempts=1),
             Decision("DEC-02", "One shot", "CAP-01", "", ["Ok", "Retry"], max_attempts=1),
             Decision("DEC-09", "Only reachable past DEC-02", "CAP-01", "", ["Z"])],
            [State("S-00", "Start", "The chat opens", ["DEC-01"], False),
             State("S-01", "DEC-01=Pass", "Verified", ["DEC-02"], False),
             State("S-99", "DEC-01=Fail", "Turned away", [], True, "Termination"),
             State("S-03", "DEC-02=Ok", "Settled", ["DEC-09"], False),
             State("S-06", "DEC-02=Retry", "Gave up", [], True, "Fallback"),
             State("S-04", "DEC-09=Z", "Back round", ["DEC-02"], False)])

    def test_no_route_retries_a_decision_more_often_than_the_intake_allows(self):
        """Consecutively, which is the thing Max Attempts is a statement about."""
        graph = self._graph()
        walked, augmented = enumerate_paths(graph)
        for path in walked + augmented:
            run, last = 0, ""
            for step in path:
                run = run + 1 if step.decision_id == last else 1
                last = step.decision_id
                self.assertLessEqual(
                    run, graph.decision(step.decision_id).max_attempts,
                    f"{[str(s) for s in path]} retries {step.decision_id} {run} times")

    def test_a_returning_route_is_walked_and_is_bounded(self):
        graph = self._graph()
        walked, augmented = enumerate_paths(graph)
        returns = [p for p in walked + augmented
                   if sum(1 for s in p if s.decision_id == "DEC-02") > 1]
        self.assertTrue(returns, "the loop the declaration draws produced no routes at all")
        for path in returns:
            self.assertLessEqual(sum(1 for s in path if s.decision_id == "DEC-02"), graph_module.LOOP_VISITS,
                                 f"{[str(s) for s in path]} goes round more than once")

    def test_an_outcome_reachable_only_through_the_loop_is_exercised(self):
        """DEC-09's only way onward is back into DEC-02, so it used to be dropped from the space
        with nothing to say so -- the shape of the report that "the traversal is not visiting the
        states creating the cyclicity"."""
        walked, augmented = enumerate_paths(self._graph())
        taken = {(step.decision_id, step.variant) for p in walked + augmented for step in p}
        self.assertIn(("DEC-09", "Z"), taken)

    def test_every_route_still_ends_somewhere_declared(self):
        """A loop that is walked must still finish. A route that goes round and stops is not a
        conversation anybody can run."""
        graph = self._graph()
        walked, augmented = enumerate_paths(graph)
        endings = {s.id for s in graph.states.values() if s.is_terminal}
        for path in walked + augmented:
            self.assertIn(path[-1].next_state, endings, [str(s) for s in path])
