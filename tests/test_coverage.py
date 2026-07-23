import unittest

from scenario_generator.core.coverage import match_scenarios
from scenario_generator.core.models import BenchmarkScenario, ExtractedMeta, OwnerScenario

SIG_A = (("DEC-01", "Pass"), ("DEC-02", "Found"))


def _benchmark():
    return [
        BenchmarkScenario(id="SC-001", path_str="DEC-01=Pass -> DEC-02=Found", category="Happy path",
                          materiality="High", capabilities=["CAP-01", "CAP-02"],
                          persona_id="P1", signature=SIG_A),
    ]


class TestMatchScenarios(unittest.TestCase):
    def test_exact_path_and_persona_is_a_match(self):
        owner = [OwnerScenario(id="OW-01", description="d")]
        extracted = [ExtractedMeta(decision_path=list(SIG_A), category="Happy path",
                                   capabilities=["CAP-01", "CAP-02"], persona_id="P1",
                                   confidence="Confident")]
        matches = match_scenarios(owner, extracted, _benchmark(), default_persona_id="P1")
        self.assertEqual(matches[0].verdict, "Match")
        self.assertEqual(matches[0].scenario_id, "SC-001")

    def test_persona_mismatch_is_no_match_not_partial(self):
        owner = [OwnerScenario(id="OW-01", description="d")]
        extracted = [ExtractedMeta(decision_path=list(SIG_A), category="Happy path",
                                   capabilities=["CAP-01", "CAP-02"], persona_id="P2",
                                   confidence="Confident")]
        matches = match_scenarios(owner, extracted, _benchmark(), default_persona_id="P1")
        self.assertEqual(matches[0].verdict, "No match")
        self.assertEqual(matches[0].scenario_id, "")   # not counted as covering the benchmark
        self.assertIn("persona differs", matches[0].notes)

    def test_category_mismatch_demotes_but_does_not_block(self):
        owner = [OwnerScenario(id="OW-01", description="d")]
        extracted = [ExtractedMeta(decision_path=list(SIG_A), category="Retry",
                                   capabilities=["CAP-01", "CAP-02"], persona_id="P1",
                                   confidence="Confident")]
        matches = match_scenarios(owner, extracted, _benchmark(), default_persona_id="P1")
        self.assertEqual(matches[0].verdict, "Partial match")
        self.assertEqual(matches[0].scenario_id, "SC-001")   # still counts as covered
        self.assertIn("category differs", matches[0].notes)

    def test_no_path_overlap_is_no_match(self):
        owner = [OwnerScenario(id="OW-01", description="d")]
        extracted = [ExtractedMeta(decision_path=[("DEC-99", "Nope")], persona_id="P1",
                                   confidence="Watch-out")]
        matches = match_scenarios(owner, extracted, _benchmark(), default_persona_id="P1")
        self.assertEqual(matches[0].verdict, "No match")
        self.assertEqual(matches[0].scenario_id, "")


if __name__ == "__main__":
    unittest.main()
