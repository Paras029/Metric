"""Notes a validator adds by hand, and clearing work that is genuinely cleared.

A note is the one thing on any page that came from the person rather than from a model, so losing
one is worse than losing a whole stage's output -- the stage can be run again. And clearing a
stage's status is not clearing the stage: several stages read what they need straight off disk
rather than through the record, so work that is only forgotten comes straight back on the next
run.
"""
import json
import tempfile
import unittest
from pathlib import Path

from metric.web.server import create_app
from metric.web.workspace import Workspace


class TestAddingANote(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.client = create_app(self.root).test_client()
        self.client.post("/workspaces", data={"name": "Test agent"})
        self.workspace = self.root / "test-agent"

    def _note(self, text, stage="intake"):
        return self.client.post(f"/stage/{stage}/note", data={"note": text})

    def _notes(self):
        return [note["text"] for note in Workspace.load(self.workspace).notes]

    def test_a_note_lands_against_the_stage_it_was_written_at(self):
        self._note("DEC-03 is approved or referred, never declined.")
        note = Workspace.load(self.workspace).notes[0]
        self.assertEqual(note["stage"], "intake")
        self.assertEqual(note["text"], "DEC-03 is approved or referred, never declined.")

    def test_notes_accumulate_rather_than_replace(self):
        self._note("First thing.")
        self._note("Second thing.")
        self.assertEqual(self._notes(), ["First thing.", "Second thing."])

    def test_a_blank_note_records_nothing(self):
        self._note("   ")
        self.assertEqual(self._notes(), [])

    def test_a_note_reaches_the_context_every_later_stage_reads(self):
        """The whole reason to type one: a note written while reading documents has to inform the
        review, and asking for it again there is a good way to lose it."""
        self._note("Disputes over 500 always go to a person.")
        context = Workspace.load(self.workspace).context_text()
        self.assertIn("Disputes over 500 always go to a person.", context)


class TestClearingWork(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.client = create_app(self.root).test_client()
        self.client.post("/workspaces", data={"name": "Test agent"})
        self.workspace = self.root / "test-agent"

        # Stand in for what the stages would have produced.
        for name in ("scenario_space_metadata.xlsx", "data_template.xlsx", "coverage.xlsx"):
            (self.workspace / name).write_bytes(b"stand-in")
        source = self.workspace / "sources" / "model_doc"
        source.mkdir(parents=True, exist_ok=True)
        (source / "notes.md").write_text("The agent branches on identity.", encoding="utf-8")
        (self.workspace / "intake.xlsx").write_bytes(b"stand-in")

        space = Workspace.load(self.workspace)
        space.state("intake").artifacts["workbook"] = "intake.xlsx"
        for key in ("intake", "workflow", "scenarios", "materiality", "review", "summary"):
            space.stages[key].status = "complete"
        space.save()

    def _status(self, key):
        return json.loads((self.workspace / "workspace.json").read_text())["stages"][key]["status"]

    def test_clearing_the_status_leaves_the_files_readable(self):
        self.client.post("/stage/workflow/reset")
        self.assertTrue((self.workspace / "scenario_space_metadata.xlsx").exists())
        self.assertNotEqual(self._status("workflow"), "complete")

    def test_starting_again_deletes_what_those_stages_produced(self):
        self.client.post("/stage/workflow/reset", data={"purge": "1"})
        self.assertFalse((self.workspace / "scenario_space_metadata.xlsx").exists())
        self.assertFalse((self.workspace / "data_template.xlsx").exists())

    def test_starting_again_keeps_the_submitted_documents(self):
        """They are input, not output. Losing the pack because a stage was rerun would be a rout."""
        self.client.post("/stage/workflow/reset", data={"purge": "1"})
        self.assertTrue((self.workspace / "sources" / "model_doc" / "notes.md").exists())

    def test_starting_again_keeps_the_intake_workbook_you_provided(self):
        self.client.post("/stage/workflow/reset", data={"purge": "1"})
        self.assertTrue((self.workspace / "intake.xlsx").exists())
        self.assertEqual(self._status("intake"), "complete")

    def test_clearing_a_later_stage_leaves_an_earlier_one_alone(self):
        """The scenario space metadata belongs to the workflow stage, not to the stages that rewrite it."""
        self.client.post("/stage/scenarios/reset", data={"purge": "1"})
        self.assertTrue((self.workspace / "scenario_space_metadata.xlsx").exists())
        self.assertEqual(self._status("workflow"), "complete")

    def test_deletion_cannot_reach_outside_the_workspace(self):
        outside = self.root / "elsewhere.xlsx"
        outside.write_bytes(b"not ours")
        Workspace.load(self.workspace).reset_from("workflow", delete=["../elsewhere.xlsx"])
        self.assertTrue(outside.exists())


if __name__ == "__main__":
    unittest.main()
