import unittest

from scenario_generator.core.generation import peer_signals
from scenario_generator.core.models import Persona, Scenario, TurnMeta

_PERSONA = Persona("P1", "Default user", [], True)


def _scenario(id_, category, capabilities, num_steps):
    turn_meta = [TurnMeta(i, f"DEC-0{i}", f"Decision {i}", "Pass", "", "S")
                for i in range(1, num_steps + 1)]
    return Scenario(id=id_, path=[], category=category, persona=_PERSONA, seeded_state="s",
                    termination="t", capabilities=capabilities, tools=[],
                    touches_state_change=False, turn_meta=turn_meta)


class TestPeerSignals(unittest.TestCase):
    def test_scenarios_sharing_category_and_capabilities_form_a_group(self):
        scenarios = [
            _scenario("SC-A", "Fallback", ["CAP-01"], 3),
            _scenario("SC-B", "Fallback", ["CAP-01"], 1),
            _scenario("SC-C", "Happy path", ["CAP-02"], 5),   # different group
        ]
        signals = peer_signals(scenarios)
        self.assertEqual(signals["SC-A"]["similar_scenarios_in_space"], 2)
        self.assertEqual(signals["SC-B"]["similar_scenarios_in_space"], 2)
        self.assertEqual(signals["SC-C"]["similar_scenarios_in_space"], 1)

    def test_deepest_scenario_in_group_ranks_first(self):
        scenarios = [
            _scenario("SC-A", "Fallback", ["CAP-01"], 3),
            _scenario("SC-B", "Fallback", ["CAP-01"], 1),
        ]
        signals = peer_signals(scenarios)
        self.assertEqual(signals["SC-A"]["steps_rank_within_similar_group"], 1)
        self.assertEqual(signals["SC-B"]["steps_rank_within_similar_group"], 2)

    def test_capability_order_does_not_affect_grouping(self):
        scenarios = [
            _scenario("SC-A", "Fallback", ["CAP-01", "CAP-02"], 2),
            _scenario("SC-B", "Fallback", ["CAP-02", "CAP-01"], 2),   # same set, different order
        ]
        signals = peer_signals(scenarios)
        self.assertEqual(signals["SC-A"]["similar_scenarios_in_space"], 2)


if __name__ == "__main__":
    unittest.main()
