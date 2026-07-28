import unittest

from scenario_generator.core.coverage import match_scenarios
from scenario_generator.core.models import (BenchmarkScenario, ExtractedMeta, Match,
                                            OwnerScenario)

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


class TestTheRegistryIsAnnotated(unittest.TestCase):
    """What their testing covers is written against each scenario, and named.

    An annotation, never a filter: a covered scenario stays in the pack, because whether running
    it again is duplicated effort or independent confirmation depends on how far their testing is
    trusted, and that is the reader's judgement rather than this tool's.
    """

    def _workspace(self):
        import tempfile
        from pathlib import Path
        from scenario_generator.core.intake import read_intake, write_template
        from scenario_generator.io import write_registry
        from scenario_generator.pipeline import build_scenarios
        from openpyxl import load_workbook

        directory = Path(tempfile.mkdtemp())
        intake_path = directory / "intake.xlsx"
        write_template(str(intake_path))
        book = load_workbook(intake_path)
        book["L1 Use Case"]["B1"] = "Disputes"
        book["L1 Use Case"]["B2"] = "Resolve disputes"
        book["L2 Capabilities"].append(["CAP-01", "Authenticate", "Gating"])
        book["L3 Decisions"].append(
            ["DEC-01", "Identity", "CAP-01", "credentials", "Pass / Fail", "User", 2, ""])
        book["L4 States"].append(["S-00", "Start", "The conversation opens", "DEC-01", "No", ""])
        book["L4 States"].append(["S-01", "DEC-01=Pass", "Authenticated", "", "Yes", "Happy path"])
        book["L4 States"].append(["S-02", "DEC-01=Fail", "Locked", "", "Yes", "Termination"])
        book["Personas"].append(["P1", "Cardholder", "All", "Y"])
        book["Tools"].append(["Identity service", "CAP-01", "No"])
        book.save(intake_path)

        intake = read_intake(str(intake_path))
        registry = directory / "registry.xlsx"
        write_registry(str(registry), intake, build_scenarios(intake))
        return intake, registry

    def test_a_matched_scenario_names_their_scenario(self):
        from scenario_generator.io import read_scenarios
        from scenario_generator.pipeline import annotate_coverage

        intake, registry = self._workspace()
        target = read_scenarios(str(registry), intake)[0]
        match = Match(owner=OwnerScenario("OS-7", "They authenticate successfully."),
                      extracted=ExtractedMeta(), scenario_id=target.id, verdict="Match")

        self.assertEqual(annotate_coverage(str(registry), intake, [match]), 1)
        annotated = {s.id: s for s in read_scenarios(str(registry), intake)}[target.id]
        self.assertEqual(annotated.owner_coverage, "Covered")
        self.assertEqual(annotated.owner_coverage_note, "Their OS-7")

    def test_a_partial_match_is_recorded_as_partial(self):
        from scenario_generator.io import read_scenarios
        from scenario_generator.pipeline import annotate_coverage

        intake, registry = self._workspace()
        target = read_scenarios(str(registry), intake)[0]
        match = Match(owner=OwnerScenario("OS-2", "They authenticate."),
                      extracted=ExtractedMeta(), scenario_id=target.id, verdict="Partial match")

        annotate_coverage(str(registry), intake, [match])
        annotated = {s.id: s for s in read_scenarios(str(registry), intake)}[target.id]
        self.assertEqual(annotated.owner_coverage, "Partially covered")

    def test_nothing_is_removed_from_the_registry(self):
        from scenario_generator.io import read_scenarios
        from scenario_generator.pipeline import annotate_coverage

        intake, registry = self._workspace()
        before = read_scenarios(str(registry), intake)
        match = Match(owner=OwnerScenario("OS-1", "They authenticate."),
                      extracted=ExtractedMeta(), scenario_id=before[0].id, verdict="Match")

        annotate_coverage(str(registry), intake, [match])
        self.assertEqual(len(read_scenarios(str(registry), intake)), len(before))
