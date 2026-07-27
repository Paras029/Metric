"""Every stage runner, exercised end to end against stubbed model calls.

This file exists because a runner that is never called is a runner that is never checked. The
coverage stage shipped iterating a function that returns nothing, and no test noticed, because
the tests until now only covered the three stages a walkthrough happened to touch. Running all of
them here is cheap and catches exactly that class of mistake.

It also pins the property the two front ends depend on: the runners hold no pipeline logic, so
what the interface does and what the command line does cannot drift apart.
"""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from openpyxl import Workbook

from scenario_generator.core.intake import write_template
from scenario_generator.webapp.app import RUNNERS, create_app
from scenario_generator.webapp.stages import STAGES

_EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "build_claims_intake.py"

def _ingest_reply(system, user, **kwargs):
    """Ingestion runs two passes; the prompt says which one is being asked for."""
    if "WHAT TO RETURN FOR EACH OBSERVATION" in user:
        return json.dumps({"observations": [{
            "facet": "scope_boundaries",
            "statement": "Legal advice is out of scope.",
            "quote": "The assistant does not offer legal advice",
            "locator": "under “Out of scope”"}]})
    return json.dumps({"answer": "Legal advice is out of scope and goes to a person.",
                       "points": ["Legal advice is out of scope."],
                       "unknowns": [], "sources": ["O-001"], "confidence": "Medium"})

_SOURCE = """# Out of scope

The assistant does not offer legal advice and must hand any such request to a person.
"""


def _intake_workbook(directory: Path) -> Path:
    """A minimal but valid intake, built through the template writer the tool ships."""
    path = directory / "intake.xlsx"
    write_template(str(path))

    from openpyxl import load_workbook
    book = load_workbook(path)
    book["L1 Use Case"]["B1"] = "Test agent"
    book["L1 Use Case"]["B2"] = "Answer questions"
    book["Personas"].append(["P1", "Cooperative user", "Happy path", "Y"])
    book["L2 Capabilities"].append(["CAP-01", "Authentication", "Gating"])
    book["L3 Decisions"].append(
        ["DEC-01", "Identity check", "CAP-01", "credentials", "Pass / Fail", "User", 2, ""])
    book["L4 States"].append(["S-00", "Start", "Session begins", "DEC-01", "No", ""])
    book["L4 States"].append(["S-01", "DEC-01=Pass", "Authenticated", "", "Yes", "Happy path"])
    book["L4 States"].append(["S-02", "DEC-01=Fail", "Locked out", "", "Yes", "Termination"])
    book["Tools"].append(["Identity service", "CAP-01", "No"])
    book.save(path)
    return path


def _owner_workbook(directory: Path) -> Path:
    path = directory / "owner.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "Scenarios"
    sheet.append(["ID", "Description", "Decision Path"])
    sheet.append(["OS-1", "The user authenticates successfully.", ""])
    book.save(path)
    return path


class TestEveryStageRuns(unittest.TestCase):
    """Drive all ten stages through the interface and assert none of them fail."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.app = create_app(self.root)
        self.client = self.app.test_client()
        self.client.post("/workspaces", data={"name": "Test agent"})

        scratch = Path(tempfile.mkdtemp())
        (scratch / "notes.md").write_text(_SOURCE, encoding="utf-8")

        for key, path in (("documents", scratch / "notes.md"),
                          ("intake", _intake_workbook(scratch)),
                          ("coverage", _owner_workbook(scratch))):
            with open(path, "rb") as handle:
                self.client.post(f"/stage/{key}/upload",
                                 data={"files": (handle, path.name)},
                                 content_type="multipart/form-data")

    def _settle(self, key, timeout=30.0):
        """Stages run on a background thread; wait for this one to stop running."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            status = self.client.get(f"/stage/{key}/progress").get_json()["status"]
            if status != "running":
                return
            time.sleep(0.05)
        self.fail(f"{key} was still running after {timeout}s")

    def _failed(self, key):
        page = self.client.get(f"/stage/{key}").data.decode()
        return "This stage did not finish" in page

    def test_all_runners_complete(self):
        patches = {
            "scenario_generator.ingest.extraction.ask_llm": _ingest_reply,
            "scenario_generator.llm.writer.ask_llm": lambda s, u, **k: "{}",
            "scenario_generator.llm.materiality.ask_llm": lambda s, u, **k: "{}",
            "scenario_generator.llm.reviewer.ask_llm": lambda s, u, **k: '{"proposals": []}',
            "scenario_generator.llm.extractor.ask_llm": lambda s, u, **k: "{}",
        }
        stack = [mock.patch(target, stub) for target, stub in patches.items()]
        for patch in stack:
            patch.start()
        self.addCleanup(lambda: [patch.stop() for patch in stack])

        for stage in STAGES:
            self.client.post(f"/stage/{stage.key}/run")
            self._settle(stage.key)
            self.assertFalse(self._failed(stage.key), f"{stage.key} failed")

    def test_every_stage_has_a_runner(self):
        """Adding a document and reading it are one job, so no stage is upload-only any more."""
        self.assertEqual({s.key for s in STAGES} - set(RUNNERS), set())


class TestCoverageReturnsItsResult(unittest.TestCase):
    """map_coverage used to return None while a caller counted what it returned."""

    def test_map_coverage_reports_what_it_matched(self):
        from scenario_generator.pipeline import build_scenarios, map_coverage
        from scenario_generator.core.intake import read_intake
        from scenario_generator.io import write_registry

        scratch = Path(tempfile.mkdtemp())
        intake_path = _intake_workbook(scratch)
        intake = read_intake(str(intake_path))
        write_registry(str(scratch / "registry.xlsx"), intake, build_scenarios(intake))

        with mock.patch("scenario_generator.llm.extractor.ask_llm", lambda s, u, **k: "{}"):
            result = map_coverage(str(intake_path), str(scratch / "registry.xlsx"),
                                  str(_owner_workbook(scratch)), str(scratch / "overlap.xlsx"))

        self.assertIsNotNone(result)
        self.assertEqual(result.owner_scenarios, 1)
        self.assertEqual(result.covered + result.gaps, result.benchmark)


if __name__ == "__main__":
    unittest.main()
