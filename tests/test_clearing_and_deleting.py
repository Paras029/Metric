"""Getting rid of things: a stage started from nothing, and a workspace removed.

Both were half-there. "Start again from here" deleted what the stages produced and left the
submitted documentation in place, so the next run was built from a pack nobody remembered
uploading -- which for a tool whose whole output is meant to be traceable to its inputs is the
most confusing state available. And a workspace, once made, could not be removed at all.
"""
import tempfile
import time
import unittest
from pathlib import Path

from scenario_generator.webapp.app import create_app
from scenario_generator.webapp.workspace import Workspace

EXAMPLE = (Path(__file__).resolve().parent.parent
           / "examples" / "intakes" / "1_disputes_three_blocks.xlsx")


def _started(root: Path, name="Clearing"):
    client = create_app(root).test_client()
    client.post("/workspaces", data={"name": name})
    with open(EXAMPLE, "rb") as handle:
        client.post("/stage/intake/upload",
                    data={"files": (handle, EXAMPLE.name), "group": "intake_workbook"},
                    content_type="multipart/form-data")
    for key in ("intake", "workflow"):
        client.post(f"/stage/{key}/run")
        for _ in range(600):
            if client.get(f"/stage/{key}/progress").get_json()["status"] != "running":
                break
            time.sleep(0.05)
    return client


class TestClearingAStage(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.client = _started(self.root)
        self.workspace = self.root / "clearing"

    def _files(self):
        return sorted(p.relative_to(self.workspace).as_posix()
                      for p in self.workspace.rglob("*") if p.is_file())

    def test_clearing_the_status_keeps_everything(self):
        """What you want when comparing a rerun against what came before."""
        before = self._files()
        self.client.post("/stage/workflow/reset")
        self.assertEqual(self._files(), before)

    def test_starting_again_deletes_what_the_stages_produced_and_keeps_the_pack(self):
        self.client.post("/stage/workflow/reset", data={"purge": "1"})
        left = self._files()
        self.assertIn(EXAMPLE.name, left, "the submitted workbook was removed without being asked")
        self.assertNotIn("scenario_space_metadata.xlsx", left)

    def test_starting_from_nothing_removes_the_pack_as_well(self):
        self.client.post("/stage/intake/reset", data={"purge": "1", "submissions": "1"})
        left = self._files()
        self.assertNotIn(EXAMPLE.name, left, "the submitted workbook survived a full clear")
        self.assertEqual(left, ["workspace.json"],
                         f"something other than the record survived: {left}")

    def test_it_is_scoped_to_the_stage_and_the_ones_after_it(self):
        """Resetting coverage should not throw away the model documentation the intake was built
        from. Which stage reads a file is recorded against it, and that is what decides."""
        self.client.post("/stage/coverage/reset", data={"purge": "1", "submissions": "1"})
        self.assertIn(EXAMPLE.name, self._files())

    def test_the_stage_can_be_run_again_afterwards(self):
        """A clear that leaves the workspace unable to start again is not a clear."""
        self.client.post("/stage/intake/reset", data={"purge": "1", "submissions": "1"})
        page = self.client.get("/stage/intake")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Provide the intake workbook", page.get_data(as_text=True))

    def test_a_redaction_mark_does_not_outlive_the_file_it_was_on(self):
        """It would be applied to the next file uploaded under the same name -- a setting nobody
        chose, arriving from a previous run."""
        workspace = Workspace.load(self.workspace)
        workspace.set_redact("model_documentation", "notes.md", True)
        workspace.save()
        self.client.post("/stage/intake/reset", data={"purge": "1", "submissions": "1"})
        self.assertEqual(Workspace.load(self.workspace).redact_files, set())


class TestDeletingAWorkspace(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.client = _started(self.root, "To be removed")
        self.client.post("/workspaces", data={"name": "Kept"})

    def _names(self):
        return sorted(w.name for w in Workspace.list_all(self.root))

    def test_the_workspace_and_everything_in_it_goes(self):
        self.assertIn("To be removed", self._names())
        self.client.post("/workspaces/to-be-removed/delete")
        self.assertNotIn("To be removed", self._names())
        self.assertFalse((self.root / "to-be-removed").exists())

    def test_the_others_are_untouched(self):
        self.client.post("/workspaces/to-be-removed/delete")
        self.assertEqual(self._names(), ["Kept"])

    def test_a_slug_that_climbs_out_of_the_workspace_root_is_refused(self):
        """It is one recursive delete, which is exactly why the path is resolved and checked."""
        outside = self.root.parent / "not-a-workspace"
        outside.mkdir(exist_ok=True)
        answer = self.client.post("/workspaces/..%2Fnot-a-workspace/delete")
        self.assertIn(answer.status_code, (301, 308, 400, 404))
        self.assertTrue(outside.exists(), "a crafted slug reached outside the workspace root")

    def test_deleting_something_that_is_not_a_workspace_is_refused(self):
        stray = self.root / "just-a-directory"
        stray.mkdir()
        self.assertEqual(self.client.post("/workspaces/just-a-directory/delete").status_code, 404)
        self.assertTrue(stray.exists())

    def test_the_entry_screen_offers_it(self):
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn("/delete", page)
        self.assertIn("data-confirm", page, "the only irreversible action asks nothing first")


if __name__ == "__main__":
    unittest.main()
