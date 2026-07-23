import unittest

from scenario_generator.core.graph import DecisionGraph, enumerate_paths
from scenario_generator.core.models import Decision, State


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
