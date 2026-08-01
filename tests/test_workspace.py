"""The workspace's job is to stop early changes leaving stale work downstream looking finished.

These tests are about that rule and the states around it, since a benchmark issued from a
registry that no longer matches its intake is exactly the failure the interface exists to make
impossible to reach by accident.
"""
import tempfile
import unittest
from pathlib import Path

from scenario_generator.webapp.stages import (COMPLETE, LOCKED, READY, STAGES, STALE,
                                              downstream_of, index_of)
from scenario_generator.webapp.workspace import Workspace, slugify


def _workspace():
    return Workspace.create(Path(tempfile.mkdtemp()), "Cardmember Disputes Assistant")


class TestStageOrder(unittest.TestCase):
    def test_the_pipeline_is_in_the_order_it_is_declared_in(self):
        self.assertEqual(index_of(STAGES[0].key), 0)
        for position, stage in enumerate(STAGES):
            self.assertEqual(index_of(stage.key), position)

    def test_downstream_is_everything_after_and_nothing_before(self):
        keys = [s.key for s in downstream_of("intake")]
        self.assertNotIn("intake", keys)
        self.assertIn("benchmark", keys)
        self.assertEqual(keys[0], "benchmark")

    def test_an_unknown_stage_is_an_error_rather_than_a_guess(self):
        with self.assertRaises(KeyError):
            index_of("not-a-stage")


class TestReachability(unittest.TestCase):
    """Optional stages do not gate what follows them, which is what allows a cold start."""

    def test_a_team_that_already_has_an_intake_can_start_at_it(self):
        workspace = _workspace()
        self.assertEqual(workspace.state("intake").status, READY)
        self.assertFalse(workspace.is_blocked("intake"))

    def test_the_document_stage_is_open_but_not_required(self):
        workspace = _workspace()
        self.assertEqual(workspace.state("documents").status, READY)

    def test_a_stage_behind_a_required_one_is_locked(self):
        workspace = _workspace()
        self.assertTrue(workspace.is_blocked("benchmark"))
        self.assertFalse(workspace.can_run("benchmark"))

    def test_the_intake_alone_unlocks_the_benchmark(self):
        """Reading documents is a way to produce an intake, not a precondition for having one."""
        workspace = _workspace()
        workspace.complete("intake")
        self.assertEqual(workspace.state("benchmark").status, READY)

    def test_skipping_an_optional_stage_does_not_strand_the_run(self):
        workspace = _workspace()
        for key in ("intake", "benchmark", "text", "materiality", "review"):
            workspace.complete(key)
        self.assertEqual(workspace.state("issue").status, READY)
        self.assertEqual(workspace.state("coverage").status, READY)


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
        self.assertEqual(workspace.state("documents").status, COMPLETE)

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

    def test_a_new_input_marks_the_stage_that_reads_it_out_of_date_too(self):
        """A stage that reports figures read off a replaced input is the most misleading kind of
        stale: the numbers read as a fact about the file in front of you rather than as something
        left over, so uploading a corrected intake has to mark the intake stage itself."""
        workspace = _workspace()
        workspace.complete("intake", summary={"Decision points": 5})
        workspace.complete("benchmark")

        invalidated = workspace.invalidate_from("intake")

        self.assertEqual(workspace.state("intake").status, STALE)
        self.assertEqual(workspace.state("benchmark").status, STALE)
        self.assertIn("intake", [s.key for s in invalidated])

    def test_a_change_upstream_still_leaves_that_stage_alone(self):
        """invalidate_after is the other case, and must not start clearing its own stage."""
        workspace = _workspace()
        workspace.complete("intake")
        workspace.complete("benchmark")

        workspace.invalidate_after("intake")

        self.assertEqual(workspace.state("intake").status, COMPLETE)
        self.assertEqual(workspace.state("benchmark").status, STALE)

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
        workspace.complete("documents", summary={"Files": 3})
        reopened = Workspace.load(workspace.root)
        self.assertEqual(reopened.name, "Cardmember Disputes Assistant")
        self.assertEqual(reopened.state("documents").summary["Files"], 3)
        self.assertEqual(reopened.state("documents").status, COMPLETE)

    def test_the_current_stage_is_the_first_needing_attention(self):
        workspace = _workspace()
        self.assertEqual(workspace.current_stage().key, STAGES[0].key)
        workspace.complete(STAGES[0].key)
        self.assertEqual(workspace.current_stage().key, STAGES[1].key)

    def test_coverage_is_read_before_the_pack_is_issued(self):
        """What the owner already covers changes what is worth issuing, so it comes first."""
        keys = [s.key for s in STAGES]
        self.assertLess(keys.index("coverage"), keys.index("issue"))

    def test_artifacts_outside_the_workspace_do_not_resolve(self):
        workspace = _workspace()
        workspace.state("intake").artifacts["escape"] = "../../etc/passwd"
        self.assertIsNone(workspace.artifact_path("intake", "escape"))

    def test_a_per_file_redaction_mark_survives_being_reopened(self):
        workspace = _workspace()
        workspace.set_redact("model_doc", "spec.pdf", True)
        workspace.save()

        reopened = Workspace.load(workspace.root)
        self.assertTrue(reopened.is_marked_for_redaction("model_doc", "spec.pdf"))
        self.assertFalse(reopened.is_marked_for_redaction("model_doc", "other.pdf"))

    def test_the_mark_is_scoped_to_its_own_group(self):
        """A filename is not unique across upload groups, so the mark must not bleed across them."""
        workspace = _workspace()
        workspace.set_redact("model_doc", "notes.md", True)
        self.assertFalse(workspace.is_marked_for_redaction("supporting", "notes.md"))

    def test_unmarking_clears_it(self):
        workspace = _workspace()
        workspace.set_redact("model_doc", "spec.pdf", True)
        workspace.set_redact("model_doc", "spec.pdf", False)
        self.assertFalse(workspace.is_marked_for_redaction("model_doc", "spec.pdf"))

    def test_names_become_stable_directories(self):
        self.assertEqual(slugify("Cardmember Disputes Assistant"),
                         "cardmember-disputes-assistant")
        self.assertEqual(slugify("  "), "use-case")


if __name__ == "__main__":
    unittest.main()
