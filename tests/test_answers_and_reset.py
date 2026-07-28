"""Answering questions in one pass, and clearing work that is genuinely cleared.

Both of these were doing something close to, but not quite, what they claimed. Answering happened
one question at a time with a page reload between each, which turns a list of six into six round
trips. Clearing a stage cleared its status and nothing else — and since several stages read what
they need straight off disk rather than through the record, the old work came straight back on
the next run.
"""
import json
import tempfile
import unittest
from pathlib import Path

from scenario_generator.webapp.app import create_app
from scenario_generator.webapp.workspace import Workspace


class TestAnsweringSeveralAtOnce(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.client = create_app(self.root).test_client()
        self.client.post("/workspaces", data={"name": "Test agent"})
        self.workspace = self.root / "test-agent"

    def _answer(self, pairs):
        data = {}
        for index, (question, answer) in enumerate(pairs, start=1):
            data[f"question-{index}"] = question
            data[f"answer-{index}"] = answer
        return self.client.post("/stage/questions/answers", data=data)

    def _answered(self):
        return Workspace.load(self.workspace).answered_questions()

    def test_several_answers_land_in_one_request(self):
        self._answer([("What are the outcomes of DEC-03?", "Approved or referred"),
                      ("Which systems does it call?", "The claims API only")])
        self.assertEqual(self._answered(), {
            "What are the outcomes of DEC-03?": "Approved or referred",
            "Which systems does it call?": "The claims API only"})

    def test_a_partial_pass_registers_what_it_carried(self):
        """Nobody has to finish the list in one sitting."""
        self._answer([("Question one", "An answer"), ("Question two", "")])
        self.assertEqual(list(self._answered()), ["Question one"])

    def test_the_rest_can_be_answered_later(self):
        self._answer([("Question one", "An answer"), ("Question two", "")])
        self._answer([("Question two", "A later answer")])
        self.assertEqual(self._answered(),
                         {"Question one": "An answer", "Question two": "A later answer"})

    def test_an_answer_already_given_can_be_changed(self):
        self._answer([("Question one", "First thought")])
        self._answer([("Question one", "Second thought")])
        self.assertEqual(self._answered()["Question one"], "Second thought")

    def test_a_blank_pass_records_nothing(self):
        self._answer([("Question one", "  "), ("Question two", "")])
        self.assertEqual(self._answered(), {})

    def test_answers_reach_the_context_every_later_stage_reads(self):
        self._answer([("What are the outcomes of DEC-03?", "Approved or referred")])
        context = Workspace.load(self.workspace).context_text()
        self.assertIn("What are the outcomes of DEC-03?", context)
        self.assertIn("Approved or referred", context)


class TestClearingWork(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.client = create_app(self.root).test_client()
        self.client.post("/workspaces", data={"name": "Test agent"})
        self.workspace = self.root / "test-agent"

        # Stand in for what the stages would have produced.
        for name in ("registry.xlsx", "challenge_pack.xlsx", "coverage.xlsx"):
            (self.workspace / name).write_bytes(b"stand-in")
        source = self.workspace / "sources" / "model_doc"
        source.mkdir(parents=True, exist_ok=True)
        (source / "notes.md").write_text("The agent branches on identity.", encoding="utf-8")
        (self.workspace / "intake.xlsx").write_bytes(b"stand-in")

        space = Workspace.load(self.workspace)
        space.state("intake").artifacts["workbook"] = "intake.xlsx"
        for key in ("intake", "benchmark", "text", "materiality", "review", "issue"):
            space.stages[key].status = "complete"
        space.save()

    def _status(self, key):
        return json.loads((self.workspace / "workspace.json").read_text())["stages"][key]["status"]

    def test_clearing_the_status_leaves_the_files_readable(self):
        self.client.post("/stage/benchmark/reset")
        self.assertTrue((self.workspace / "registry.xlsx").exists())
        self.assertNotEqual(self._status("benchmark"), "complete")

    def test_starting_again_deletes_what_those_stages_produced(self):
        self.client.post("/stage/benchmark/reset", data={"purge": "1"})
        self.assertFalse((self.workspace / "registry.xlsx").exists())
        self.assertFalse((self.workspace / "challenge_pack.xlsx").exists())

    def test_starting_again_keeps_the_submitted_documents(self):
        """They are input, not output. Losing the pack because a stage was rerun would be a rout."""
        self.client.post("/stage/benchmark/reset", data={"purge": "1"})
        self.assertTrue((self.workspace / "sources" / "model_doc" / "notes.md").exists())

    def test_starting_again_keeps_the_intake_workbook_you_provided(self):
        self.client.post("/stage/benchmark/reset", data={"purge": "1"})
        self.assertTrue((self.workspace / "intake.xlsx").exists())
        self.assertEqual(self._status("intake"), "complete")

    def test_clearing_a_later_stage_leaves_an_earlier_one_alone(self):
        """The registry belongs to the benchmark stage, not to the stages that rewrite it."""
        self.client.post("/stage/text/reset", data={"purge": "1"})
        self.assertTrue((self.workspace / "registry.xlsx").exists())
        self.assertEqual(self._status("benchmark"), "complete")

    def test_deletion_cannot_reach_outside_the_workspace(self):
        outside = self.root / "elsewhere.xlsx"
        outside.write_bytes(b"not ours")
        Workspace.load(self.workspace).reset_from("benchmark", delete=["../elsewhere.xlsx"])
        self.assertTrue(outside.exists())


if __name__ == "__main__":
    unittest.main()
