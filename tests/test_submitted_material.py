"""What happens to a file between someone uploading it and the reading pass seeing it.

This is the path that had the most ways to lose a document quietly, and each test here pins one
of them shut. A file that is stored but never read, a format that is refused for its extension
rather than its content, a document that contributes nothing without anyone noticing -- none of
these announce themselves. The scenario space simply comes out thinner, months later, with no record
of why.
"""
import json
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from scenario_generator.core.evidence import DocumentRef, EvidenceRecord, FacetAnswer
from scenario_generator.ingest import build_corpus, read_document
from scenario_generator.ingest.groups import (DEFAULT_GROUP, EVIDENCE_GROUPS, GROUPS,
                                              evidence_files, files_in, remove_file)
from scenario_generator.webapp.app import create_app


def _scratch() -> Path:
    return Path(tempfile.mkdtemp())


def _rules_workbook(directory: Path) -> Path:
    """The shape a routing table actually arrives in."""
    path = directory / "thresholds.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "Routing"
    sheet.append(["Condition", "Threshold", "Outcome"])
    sheet.append(["Dispute value", "over 500", "Hand to a person"])
    sheet.append(["Identity attempts", "3", "Lock the session"])
    book.save(path)
    return path


class TestSpreadsheetsAreEvidence(unittest.TestCase):
    """A team that keeps its decision rules in a spreadsheet will send the spreadsheet."""

    def test_a_workbook_is_read_sheet_by_sheet_with_locators(self):
        reference, segments = read_document(_rules_workbook(_scratch()))
        self.assertEqual(reference.kind, "spreadsheet")
        self.assertIn("Routing", segments[0].locator)
        self.assertIn("rows", segments[0].locator)

    def test_the_header_travels_with_every_passage(self):
        """A row means nothing without the columns it sits under."""
        _, segments = read_document(_rules_workbook(_scratch()))
        self.assertIn("Condition | Threshold | Outcome", segments[0].text)
        self.assertIn("Hand to a person", segments[0].text)

    def test_a_csv_is_read_the_same_way(self):
        path = _scratch() / "cases.csv"
        path.write_text("id,description\n1,The customer disputes a charge\n", encoding="utf-8")
        reference, segments = read_document(path)
        self.assertEqual(reference.kind, "spreadsheet")
        self.assertIn("disputes a charge", segments[0].text)

    def test_a_spreadsheet_reaches_the_corpus(self):
        corpus = build_corpus([_rules_workbook(_scratch())])
        self.assertIn("thresholds.xlsx", corpus.text)
        self.assertIn("Hand to a person", corpus.text)

    def test_every_group_that_takes_evidence_accepts_a_spreadsheet(self):
        for group in GROUPS:
            if group.key in EVIDENCE_GROUPS and group.key != "diagrams":
                self.assertIn(".xlsx", group.accepts, f"{group.key} refuses spreadsheets")


class TestNothingUploadedIsLost(unittest.TestCase):
    """Every route into the workspace has to land somewhere the reading pass looks."""

    def setUp(self):
        self.root = _scratch()
        self.app = create_app(self.root)
        self.client = self.app.test_client()
        self.client.post("/workspaces", data={"name": "Test agent"})
        self.workspace = self.root / "test-agent"

    def _upload(self, stage, path, group=None):
        data = {"files": (open(path, "rb"), path.name)}
        if group is not None:
            data["group"] = group
        return self.client.post(f"/stage/{stage}/upload", data=data,
                                content_type="multipart/form-data")

    def test_a_document_added_without_a_stated_kind_is_still_read(self):
        """The 'add a document' form on every stage sends no group, so an ungrouped upload
        still has to land somewhere it will be read."""
        path = _scratch() / "vendor.md"
        path.write_text("# Vendor limits\n\nThe API returns at most fifty records.\n",
                        encoding="utf-8")
        self._upload("materiality", path)

        readable = [p.name for paths in evidence_files(self.workspace).values() for p in paths]
        self.assertIn("vendor.md", readable)
        self.assertIn("vendor.md", [p.name for p in files_in(self.workspace, DEFAULT_GROUP)])

    def test_their_scenarios_can_be_submitted_on_the_coverage_stage(self):
        path = _rules_workbook(_scratch())
        self._upload("coverage", path)
        self.assertIn("thresholds.xlsx",
                      [p.name for p in files_in(self.workspace, "owner_scenarios")])

    def test_their_scenarios_are_never_read_as_evidence_about_the_agent(self):
        """Their blind spots must not reach the scenario space through the back door."""
        self.assertNotIn("owner_scenarios", EVIDENCE_GROUPS)

    def _statuses(self):
        return {key: stage["status"] for key, stage
                in json.loads((self.workspace / "workspace.json").read_text())["stages"].items()}

    def _finish(self, *keys):
        """Mark stages complete, so that anything invalidating them is visible."""
        state = json.loads((self.workspace / "workspace.json").read_text())
        for key in keys:
            state["stages"].setdefault(key, {"artifacts": {}})["status"] = "complete"
        (self.workspace / "workspace.json").write_text(json.dumps(state))

    def test_their_scenarios_arriving_does_not_make_the_declaration_out_of_date(self):
        """Nothing before coverage reads them -- they are kept out of the evidence corpus on
        purpose -- so nothing before coverage can be wrong because one arrived. Invalidating from
        the intake threw away six finished stages to re-derive an identical result, and re-running
        them costs a few hundred model calls."""
        self._finish("intake", "workflow", "scenarios", "materiality", "review")
        self._upload("coverage", _rules_workbook(_scratch()))

        statuses = self._statuses()
        for key in ("intake", "workflow", "scenarios", "materiality", "review"):
            self.assertEqual(statuses[key], "complete", f"{key} was marked stale")

    def test_a_finished_coverage_stage_is_out_of_date_though(self):
        """It is the one stage that reads them, and its verdict was measured against the
        conversations it had at the time."""
        self._upload("coverage", _rules_workbook(_scratch()))
        self._finish("coverage")
        self._upload("coverage", _rules_workbook(_scratch()))
        self.assertEqual(self._statuses()["coverage"], "stale")

    def test_a_document_about_the_agent_still_makes_the_declaration_out_of_date(self):
        """The other half of the same rule: this one the intake does read, so its report of what
        the pack says is stale the moment the pack changes."""
        self._finish("intake", "workflow")
        path = _scratch() / "vendor.md"
        path.write_text("# Vendor limits\n", encoding="utf-8")
        self._upload("intake", path, "supporting")

        statuses = self._statuses()
        self.assertEqual(statuses["intake"], "stale")
        self.assertEqual(statuses["workflow"], "stale")

    def test_taking_their_scenarios_back_out_leaves_the_declaration_alone(self):
        self._upload("coverage", _rules_workbook(_scratch()))
        self._finish("intake", "workflow", "materiality")
        self.client.post("/stage/coverage/remove",
                         data={"group": "owner_scenarios", "name": "thresholds.xlsx"})

        statuses = self._statuses()
        for key in ("intake", "workflow", "materiality"):
            self.assertEqual(statuses[key], "complete", f"{key} was marked stale")

    def test_marking_their_scenarios_for_redaction_leaves_the_declaration_alone(self):
        self._upload("coverage", _rules_workbook(_scratch()))
        self._finish("intake", "workflow", "materiality")
        self.client.post("/stage/coverage/redact",
                         data={"group": "owner_scenarios", "name": "thresholds.xlsx", "on": "1"})

        statuses = self._statuses()
        for key in ("intake", "workflow", "materiality"):
            self.assertEqual(statuses[key], "complete", f"{key} was marked stale")

    def _image(self, name):
        import base64
        path = _scratch() / name
        path.write_bytes(base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAE"
            "hQGAhKmMIQAAAABJRU5ErkJggg=="))
        return path

    def test_how_the_pictures_relate_is_asked_only_once_there_are_several(self):
        """With one picture there is nothing to relate, and a control asking how it relates to
        itself is one that makes a reader wonder what they have missed."""
        self._upload("intake", self._image("one.png"), "diagrams")
        self.assertNotIn("How these pictures relate",
                         self.client.get("/stage/intake").data.decode())

        self._upload("intake", self._image("two.png"), "diagrams")
        self.assertIn("How these pictures relate",
                      self.client.get("/stage/intake").data.decode())

    def test_the_steer_is_kept_and_makes_a_finished_reading_out_of_date(self):
        """It is an input to the reading, so a completed reading has not seen it."""
        self._finish("intake")
        self.client.post("/stage/intake/images", data={"relationship": "separate"})

        from scenario_generator.webapp.workspace import Workspace
        self.assertEqual(Workspace.load(self.workspace).image_relationship, "separate")
        self.assertEqual(self._statuses()["intake"], "stale")

    def test_choosing_what_was_already_chosen_changes_nothing(self):
        """Re-running a reading that would be given the same instruction is a few hundred model
        calls to reproduce what is already on disk."""
        self.client.post("/stage/intake/images", data={"relationship": "one_flow"})
        self._finish("intake")
        self.client.post("/stage/intake/images", data={"relationship": "one_flow"})
        self.assertEqual(self._statuses()["intake"], "complete")

    def test_a_value_nobody_offered_is_not_stored(self):
        self.client.post("/stage/intake/images", data={"relationship": "../../etc"})
        from scenario_generator.webapp.workspace import Workspace
        self.assertEqual(Workspace.load(self.workspace).image_relationship, "")

    def test_an_unsupported_file_says_what_is_supported(self):
        path = _scratch() / "old.doc"
        path.write_bytes(b"not really a word document")
        self._upload("intake", path, "model_doc")

        state = json.loads((self.workspace / "workspace.json").read_text())["stages"]["intake"]
        self.assertEqual(state["status"], "failed")
        self.assertIn("old.doc", state["note"])
        self.assertIn(".docx", state["note"])

    def test_a_file_can_be_taken_back_out(self):
        path = _rules_workbook(_scratch())
        self._upload("intake", path, "supporting")
        self.assertTrue(remove_file(self.workspace, "supporting", "thresholds.xlsx"))
        self.assertEqual(files_in(self.workspace, "supporting"), [])

    def test_removal_cannot_reach_outside_the_group(self):
        outside = self.workspace / "workspace.json"
        self.assertFalse(remove_file(self.workspace, "supporting", "../../workspace.json"))
        self.assertTrue(outside.exists())

    def test_uploading_does_not_claim_a_result_nobody_produced(self):
        """Attaching the intake is not the same as having read it."""
        path = _rules_workbook(_scratch())
        self._upload("intake", path, "intake_workbook")
        state = json.loads((self.workspace / "workspace.json").read_text())["stages"]["intake"]
        self.assertNotEqual(state["status"], "complete")
        self.assertEqual(state["artifacts"]["workbook"], "thresholds.xlsx")


class TestDocumentsAreAccountedFor(unittest.TestCase):
    """A pack read as though it were its largest file is the failure this reports."""

    def _record(self, drawn_on):
        record = EvidenceRecord(
            documents=[DocumentRef(name="model.pdf", kind="PDF", units=8, drawn_on=drawn_on),
                       DocumentRef(name="vendor.pdf", kind="PDF", units=3)],
            answers=[FacetAnswer(facet="decisions", answer="It branches on identity.")])
        return record

    def test_a_document_nothing_rests_on_is_named_in_the_context(self):
        from scenario_generator.ingest import build_context_document

        text = build_context_document(self._record(drawn_on=True), "Test agent")
        self.assertIn("vendor.pdf", text)
        self.assertIn("nothing below rests on this document", text)

    def test_a_document_that_was_used_is_not_flagged(self):
        from scenario_generator.ingest import build_context_document

        text = build_context_document(self._record(drawn_on=True), "Test agent")
        line = next(l for l in text.splitlines() if "model.pdf" in l)
        self.assertNotIn("nothing below rests", line)

    def test_an_unreadable_file_is_not_reported_as_ignored(self):
        """It failed to open; that is a different problem with a different fix."""
        from scenario_generator.ingest import build_context_document

        record = EvidenceRecord(documents=[
            DocumentRef(name="scan.pdf", kind="unreadable", note="no text layer")])
        text = build_context_document(record, "Test agent")
        self.assertNotIn("nothing below rests on this document", text)


if __name__ == "__main__":
    unittest.main()
