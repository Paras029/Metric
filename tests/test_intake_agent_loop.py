"""The loop that finishes a declaration, driven by stubs rather than by a gateway.

The decisions are the part worth testing, and a loop tested only against a live model is tested
on a good day. Every case here drives it with a scripted conversation, so what is being checked is
the loop's own behaviour: when it stops, what it refuses, what it does with a tool that fails.

The property the whole thing rests on: **the model never decides it is finished.** That is settled
by the audit, against the declaration on disk. A loop that judges its own completion runs until it
feels like stopping, which on a bad day is never and on a worse day is immediately.
"""
import shutil
import tempfile
import unittest
from pathlib import Path

from scenario_generator.ingest import agent

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "intakes"


def _scripted(*turns):
    """A conversation that returns each scripted turn once, then stops calling tools."""
    remaining = list(turns)

    def converse(messages):
        if remaining:
            return remaining.pop(0)
        return {"content": "Nothing further.", "tool_calls": [], "raw_tool_calls": []}
    return converse


def _call(_tool, **args):
    """One scripted tool call. The tool's own name is positional-only so it cannot collide with
    an argument called `name`, which two of these tools take."""
    return {"content": "", "raw_tool_calls": [],
            "tool_calls": [{"tool": _tool, "args": args, "id": f"c{_tool}"}]}


class TestWhenItStops(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.intake = self.root / "intake.xlsx"
        shutil.copy(EXAMPLES / "4_spans_drawn_wrongly.xlsx", self.intake)

    def test_a_complete_declaration_is_not_worked_on_at_all(self):
        """The cheapest correct outcome, and one a loop that trusts a model never reaches."""
        clean = self.root / "clean.xlsx"
        shutil.copy(EXAMPLES / "1_disputes_three_blocks.xlsx", clean)
        state = agent.run(self.root, str(clean), converse=_scripted())
        self.assertEqual(state.calls, 0)
        self.assertIn("already structurally complete", state.stopped_because)

    def test_the_model_saying_it_is_done_does_not_end_it(self):
        """The property everything else rests on."""
        done = {"content": "The declaration is complete.", "tool_calls": [], "raw_tool_calls": []}
        state = agent.run(self.root, str(self.intake), converse=_scripted(done, done, done))
        self.assertGreater(state.gaps_now, 0)
        self.assertNotIn("clean", state.stopped_because)

    def test_it_stops_at_its_budget_rather_than_looping(self):
        def forever(messages):
            return _call("audit_declaration")
        state = agent.run(self.root, str(self.intake), converse=forever, max_turns=5)
        self.assertEqual(state.turns, 5)
        self.assertIn("budget of 5 turns", state.stopped_because)

    def test_a_failing_model_call_stops_it_without_losing_what_was_done(self):
        def breaks(messages):
            raise RuntimeError("gateway down")
        state = agent.run(self.root, str(self.intake), converse=breaks)
        self.assertIn("gateway down", state.stopped_because)
        self.assertEqual(state.gaps_now, state.gaps_at_start)

    def test_a_failing_tool_is_information_rather_than_a_stop(self):
        state = agent.run(self.root, str(self.intake),
                          converse=_scripted(_call("read_document", name="nothing.pdf")))
        self.assertGreaterEqual(state.calls, 1)
        self.assertIn("read_document", state.tools_run)


class TestTheToolSurface(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.intake = self.root / "intake.xlsx"
        shutil.copy(EXAMPLES / "4_spans_drawn_wrongly.xlsx", self.intake)
        self.tools = {t.name: t for t in agent.build_tools(self.root, str(self.intake))}

    def test_it_lists_what_was_submitted(self):
        sources = (self.root / "sources" / "model_doc")
        sources.mkdir(parents=True)
        (sources / "notes.md").write_text("# Agent\n\nIt verifies then files.\n")
        tools = {t.name: t for t in agent.build_tools(self.root, str(self.intake))}
        self.assertIn("notes.md", tools["list_sources"].run())

    def test_it_says_so_plainly_when_nothing_was_submitted(self):
        """Any combination of documents and diagrams is a pack, including none of either."""
        self.assertIn("Nothing was submitted", self.tools["list_sources"].run())

    def test_asking_for_a_file_that_is_not_there_is_answered_not_raised(self):
        self.assertIn("no submitted file", self.tools["read_document"].run(name="absent.pdf"))

    def test_the_audit_is_the_declaration_rather_than_the_documents(self):
        reported = self.tools["audit_declaration"].run()
        self.assertIn("CAP-02", reported)

    def test_what_is_declared_renders_the_graph_with_its_edges(self):
        rendered = self.tools["what_is_declared"].run()
        self.assertIn("DEC-01", rendered)
        self.assertIn("Capabilities", rendered)


class TestQuestionsHaveToBeAnswerable(unittest.TestCase):
    """The failure is not asking too many questions. It is asking unanswerable ones -- a list of
    forty "is this clear?" is a list nobody opens."""

    def setUp(self):
        self.problems = ["DEC-07: outcome \"Timeout\" leads to no declared state",
                         "CAP-02: which states does it hand on at?"]
        self.recorded = []
        self.tool = agent.build_question_tool(self.problems, self.recorded)

    def test_a_question_naming_a_row_the_audit_raised_is_recorded(self):
        self.tool.run(question="DEC-07 times out and leads nowhere — where does it go?")
        self.assertEqual(len(self.recorded), 1)

    def test_a_vague_question_is_refused_with_the_reason(self):
        answer = self.tool.run(question="Is decision seven clear?")
        self.assertEqual(self.recorded, [])
        self.assertIn("does not name a row", answer)

    def test_a_question_about_something_nobody_flagged_is_refused(self):
        self.tool.run(question="What is the retry limit on DEC-99, which nobody mentioned?")
        self.assertEqual(self.recorded, [])

    def test_the_same_question_twice_is_recorded_once(self):
        for _ in range(2):
            self.tool.run(question="CAP-02 hands on at which states?")
        self.assertEqual(len(self.recorded), 1)

    def test_questions_reach_the_result(self):
        root = Path(tempfile.mkdtemp())
        intake = root / "intake.xlsx"
        shutil.copy(EXAMPLES / "4_spans_drawn_wrongly.xlsx", intake)
        state = agent.run(root, str(intake), converse=_scripted(
            _call("ask_the_model_owner", question="CAP-02 hands on at which states?")))
        self.assertEqual(state.questions, ["CAP-02 hands on at which states?"])


class TestTheOracle(unittest.TestCase):
    def test_it_names_an_outcome_that_leads_nowhere(self):
        from openpyxl import load_workbook
        root = Path(tempfile.mkdtemp())
        intake = root / "intake.xlsx"
        shutil.copy(EXAMPLES / "1_disputes_three_blocks.xlsx", intake)
        book = load_workbook(intake)
        # Give DEC-01 an outcome nothing declares a destination for.
        for row in book["L3 Decisions"].iter_rows(min_row=2):
            if row[0].value == "DEC-01":
                row[4].value = "Dispute / Other intent / Unclear"
        book.save(intake)

        problems = agent.outstanding(str(intake))
        self.assertTrue(any("Unclear" in p and "DEC-01" in p for p in problems), problems)

    def test_an_unreadable_declaration_is_reported_rather_than_raised(self):
        broken = Path(tempfile.mkdtemp()) / "not-a-workbook.xlsx"
        broken.write_text("this is not a workbook")
        self.assertTrue(agent.outstanding(str(broken))[0].startswith("The declaration could not"))


if __name__ == "__main__":
    unittest.main()


class TestTheSwitch(unittest.TestCase):
    """Off by default, and never able to fail the stage when on.

    The fixed sequence works, is what every other test exercises, and needs nothing from the
    gateway beyond an ordinary completion. The loop needs tool-calling, which not every gateway
    offers -- so it is a switch, and a gateway that cannot run it leaves a drafted declaration
    rather than a failed stage.
    """

    def test_it_is_off_unless_switched_on(self):
        import os
        from scenario_generator.llm import config
        os.environ.pop("LLM_INTAKE_LOOP", None)
        self.assertFalse(config.intake_loop())

    def test_a_gateway_that_cannot_run_it_leaves_the_draft_alone(self):
        import os
        from unittest import mock
        from openpyxl import load_workbook

        from scenario_generator.webapp import runners

        root = Path(tempfile.mkdtemp())
        target = root / "drafted_intake.xlsx"
        shutil.copy(EXAMPLES / "4_spans_drawn_wrongly.xlsx", target)
        before = load_workbook(target)["L3 Decisions"].max_row

        class _Workspace:
            def __init__(self, root):
                self.root = root

        with mock.patch.object(runners.agent if hasattr(runners, "agent") else agent, "run",
                               side_effect=RuntimeError("no tool calling here")):
            with mock.patch("scenario_generator.ingest.agent.run",
                            side_effect=RuntimeError("no tool calling here")):
                result = runners._finish_with_loop(_Workspace(root), target,
                                                   lambda *a, **k: None)

        self.assertIn("could not run", str(result["The loop"]))
        self.assertEqual(load_workbook(target)["L3 Decisions"].max_row, before)

    def test_the_summary_says_what_it_did(self):
        root = Path(tempfile.mkdtemp())
        intake = root / "intake.xlsx"
        shutil.copy(EXAMPLES / "4_spans_drawn_wrongly.xlsx", intake)
        state = agent.run(root, str(intake), converse=_scripted(_call("audit_declaration")))
        summary = state.summary()
        self.assertIn("Gaps at the start", summary)
        self.assertIn("Stopped because", summary)
        self.assertGreaterEqual(summary["Model calls"], 1)
