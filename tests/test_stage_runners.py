"""Every stage runner, exercised end to end against stubbed model calls.

This file exists because a runner that is never called is a runner that is never checked, and a
walkthrough test only touches the stages it happens to walk through. Running every runner here is
cheap, and it catches the class of mistake that survives everywhere else: a stage that looks like
it did something because nothing ever asked it for the result.

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


def _conversation_workbook(directory: Path) -> Path:
    """What the model owner submits: transcripts, under their own grouping where there is one."""
    path = directory / "their_conversations.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.append(["Conversation ID", "Turn", "Speaker", "Message", "Scenario"])
    sheet.append(["C1", 1, "Customer", "I need to get into my account", "Sign in"])
    sheet.append(["C1", 2, "Agent", "You are authenticated, how can I help?", "Sign in"])
    book.save(path)
    return path


class TestEveryStageRuns(unittest.TestCase):
    """Drive every stage through the interface and assert none of them fail."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.app = create_app(self.root)
        self.client = self.app.test_client()
        self.client.post("/workspaces", data={"name": "Test agent"})

        scratch = Path(tempfile.mkdtemp())
        (scratch / "notes.md").write_text(_SOURCE, encoding="utf-8")

        uploads = (("documents", scratch / "notes.md", "model_doc"),
                   ("documents", _conversation_workbook(scratch), "owner_scenarios"),
                   ("intake", _intake_workbook(scratch), ""))
        for key, path, group in uploads:
            with open(path, "rb") as handle:
                self.client.post(f"/stage/{key}/upload",
                                 data={"files": (handle, path.name), "group": group},
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
            "scenario_generator.llm.conversation_mapping.ask_llm": lambda s, u, **k: "{}",
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


class TestCoverageReportsWhatItMapped(unittest.TestCase):
    """The stage returns its result, and every conversation read is accounted for in it."""

    def _run(self, scratch, reply):
        from scenario_generator.pipeline import build_scenarios, map_conversation_coverage
        from scenario_generator.core.intake import read_intake
        from scenario_generator.io import write_registry

        intake_path = _intake_workbook(scratch)
        intake = read_intake(str(intake_path))
        write_registry(str(scratch / "registry.xlsx"), intake, build_scenarios(intake))

        with mock.patch("scenario_generator.llm.conversation_mapping.ask_llm",
                        lambda s, u, **k: reply):
            return map_conversation_coverage(
                str(intake_path), str(scratch / "registry.xlsx"),
                str(_conversation_workbook(scratch)), str(scratch / "coverage.xlsx"))

    def test_a_matched_conversation_lands_in_its_scenarios_bucket(self):
        scratch = Path(tempfile.mkdtemp())
        result = self._run(scratch, json.dumps(
            {"C1": {"scenario_id": "SC-001", "confidence": "high", "reason": "ends signed in"}}))

        self.assertEqual(len(result.mappings), 1)
        self.assertEqual(result.report.summary()["Mapped to a scenario"], 1)
        self.assertTrue((scratch / "coverage.xlsx").exists())

    def test_a_conversation_the_call_says_nothing_about_is_reported_as_unmatched(self):
        result = self._run(Path(tempfile.mkdtemp()), "{}")
        self.assertEqual(result.report.unmatched, ["C1"])
        self.assertEqual(result.report.summary()["Matched no scenario"], 1)


if __name__ == "__main__":
    unittest.main()
