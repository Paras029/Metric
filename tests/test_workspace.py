"""The workspace's job is to stop early changes leaving stale work downstream looking finished.

These tests are about that rule and the states around it, since a benchmark issued from a
registry that no longer matches its intake is exactly the failure the interface exists to make
impossible to reach by accident.
"""
import tempfile
import unittest
from pathlib import Path

from scenario_generator.webapp.stages import (COMPLETE, LOCKED, READY, STAGES, STALE,
                                              downstream_of, predecessor)
from scenario_generator.webapp.workspace import Workspace, slugify


def _workspace():
    return Workspace.create(Path(tempfile.mkdtemp()), "Cardmember Disputes Assistant")


class TestStageOrder(unittest.TestCase):
    def test_the_first_stage_has_no_predecessor(self):
        self.assertIsNone(predecessor(STAGES[0].key))

    def test_every_later_stage_depends_on_the_one_before_it(self):
        for earlier, later in zip(STAGES, STAGES[1:]):
            self.assertEqual(predecessor(later.key).key, earlier.key)

    def test_downstream_is_everything_after_and_nothing_before(self):
        keys = [s.key for s in downstream_of("intake")]
        self.assertNotIn("intake", keys)
        self.assertIn("benchmark", keys)
        self.assertEqual(keys[0], "benchmark")

    def test_an_unknown_stage_is_an_error_rather_than_a_guess(self):
        with self.assertRaises(KeyError):
            predecessor("not-a-stage")


class TestReachability(unittest.TestCase):
    def test_only_the_first_stage_starts_available(self):
        workspace = _workspace()
        self.assertEqual(workspace.state(STAGES[0].key).status, READY)
        self.assertEqual(workspace.state(STAGES[1].key).status, LOCKED)

    def test_completing_a_stage_unlocks_the_next(self):
        workspace = _workspace()
        workspace.complete(STAGES[0].key)
        self.assertEqual(workspace.state(STAGES[1].key).status, READY)

    def test_a_locked_stage_cannot_be_run(self):
        workspace = _workspace()
        self.assertTrue(workspace.is_blocked(STAGES[2].key))
        self.assertFalse(workspace.can_run(STAGES[2].key))


class TestInvalidation(unittest.TestCase):
    def _through(self, workspace, upto):
        for stage in STAGES:
            workspace.complete(stage.key)
            if stage.key == upto:
                return

    def test_redoing_a_stage_marks_everything_after_it_out_of_date(self):
        workspace = _workspace()
        self._through(workspace, "issue")
        invalidated = workspace.complete("intake")

        self.assertEqual(workspace.state("intake").status, COMPLETE)
        self.assertEqual(workspace.state("benchmark").status, STALE)
        self.assertEqual(workspace.state("issue").status, STALE)
        self.assertIn("Benchmark", [s.title for s in invalidated])

    def test_stages_before_the_change_are_untouched(self):
        workspace = _workspace()
        self._through(workspace, "issue")
        workspace.complete("benchmark")
        self.assertEqual(workspace.state("intake").status, COMPLETE)
        self.assertEqual(workspace.state("sources").status, COMPLETE)

    def test_out_of_date_output_is_kept_rather_than_deleted(self):
        workspace = _workspace()
        workspace.complete("intake", artifacts={"workbook": "intake.xlsx"})
        workspace.complete("benchmark", artifacts={"registry": "registry.xlsx"})
        workspace.complete("intake")
        self.assertEqual(workspace.state("benchmark").status, STALE)
        self.assertEqual(workspace.state("benchmark").artifacts["registry"], "registry.xlsx")

    def test_an_out_of_date_stage_can_still_be_run_again(self):
        workspace = _workspace()
        workspace.complete("intake")
        workspace.complete("benchmark")
        workspace.complete("intake")
        self.assertTrue(workspace.can_run("benchmark"))

    def test_out_of_date_stages_are_not_counted_as_done(self):
        workspace = _workspace()
        workspace.complete("intake")
        workspace.complete("benchmark")
        workspace.complete("intake")
        progress = workspace.progress()
        self.assertEqual(progress["stale"], 1)

    def test_clearing_a_stage_clears_everything_after_it(self):
        workspace = _workspace()
        self._through(workspace, "issue")
        workspace.reset_from("benchmark")
        self.assertEqual(workspace.state("benchmark").status, READY)
        self.assertEqual(workspace.state("issue").status, LOCKED)
        self.assertEqual(workspace.state("intake").status, COMPLETE)


class TestPersistence(unittest.TestCase):
    def test_a_workspace_survives_being_reopened(self):
        workspace = _workspace()
        workspace.complete("sources", summary={"Files": 3})
        reopened = Workspace.load(workspace.root)
        self.assertEqual(reopened.name, "Cardmember Disputes Assistant")
        self.assertEqual(reopened.state("sources").summary["Files"], 3)
        self.assertEqual(reopened.state("sources").status, COMPLETE)

    def test_the_current_stage_is_the_first_needing_attention(self):
        workspace = _workspace()
        self.assertEqual(workspace.current_stage().key, STAGES[0].key)
        workspace.complete(STAGES[0].key)
        self.assertEqual(workspace.current_stage().key, STAGES[1].key)

    def test_artifacts_outside_the_workspace_do_not_resolve(self):
        workspace = _workspace()
        workspace.state("intake").artifacts["escape"] = "../../etc/passwd"
        self.assertIsNone(workspace.artifact_path("intake", "escape"))

    def test_names_become_stable_directories(self):
        self.assertEqual(slugify("Cardmember Disputes Assistant"),
                         "cardmember-disputes-assistant")
        self.assertEqual(slugify("  "), "use-case")


if __name__ == "__main__":
    unittest.main()
