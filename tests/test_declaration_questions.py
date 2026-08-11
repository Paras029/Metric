"""The questions the intake stage puts to a person, and the ones it deliberately does not.

Everything the reading left open used to be asked here — what the pack never said about policy,
about how the model owner tested, about domain vocabulary — and it buried the handful of questions
that actually stop a branch being walked under a much longer list nobody finished. So the list is
narrowed to the three kinds of row the declared graph is made of, and what these tests hold is the
narrowing itself: a gap in a persona or a tool is real, and is still not somebody's task.

The other half is that an answer has to survive. It is recorded against its question, so a re-run
reads it as an answer rather than as a remark that happens to mention DEC-02 — and an answer that
arrives as a loose note is an answer the next draft has no reason to act on.
"""
import re
import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook

from scenario_generator.core.intake import write_template
from scenario_generator.webapp.app import create_app
from scenario_generator.webapp.workspace import Workspace


def _workbook(directory: Path) -> Path:
    """A declaration with one gap of every kind: decision, capability, persona and tool."""
    path = directory / "intake.xlsx"
    write_template(str(path))
    book = load_workbook(path)
    book["L1 Use Case"]["B2"] = "Card servicing"
    book["Personas"].append(["P1", "Cardmember", "", "Yes"])          # no behaviours declared
    book["L2 Capabilities"].append(["CAP-01", "Identity", ""])        # no type declared
    book["L2 Capabilities"].append(["CAP-02", "Disputes", "Transactional"])
    book["L3 Decisions"].append(
        ["DEC-01", "Identity check", "CAP-01", "", "Pass / Fail", "User", 1, "", "No"])
    book["L3 Decisions"].append(                                      # no outcomes declared
        ["DEC-02", "What is asked", "CAP-02", "", "", "User", 1, "", "No"])
    book["L4 States"].append(["S-00", "Start", "Session begins", "DEC-01", "No", ""])
    book["L4 States"].append(["S-01", "DEC-01=Pass", "Verified", "DEC-02", "No", ""])
    book["L4 States"].append(["S-02", "DEC-01=Fail", "Locked out", "", "Yes", "Termination"])
    book["Tools"].append(["post_dispute", "CAP-02", "", ""])          # nothing said about it
    book.save(path)
    return path


class TestWhatIsAsked(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.client = create_app(self.root).test_client()
        self.client.post("/workspaces", data={"name": "Questions"})
        book = _workbook(Path(tempfile.mkdtemp()))
        with open(book, "rb") as handle:
            self.client.post("/stage/intake/upload",
                             data={"files": (handle, book.name), "group": "intake_workbook"},
                             content_type="multipart/form-data")

    def _page(self) -> str:
        return self.client.get("/stage/intake").data.decode()

    def _asked(self):
        return re.findall(r'name="question-\d+" value="([^"]+)"', self._page())

    def _workspace(self) -> Workspace:
        return Workspace.load(next(self.root.iterdir()))

    def test_a_decision_naming_no_outcomes_is_asked_about(self):
        """The most blocking gap there is: nothing branches, so nothing enumerates, and the
        scenario space is quietly smaller with no sign that it should not be."""
        self.assertTrue(any("DEC-02" in question for question in self._asked()))

    def test_an_untyped_capability_is_asked_about(self):
        """The type decides which adversarial probes apply, so a blank one is tested less than its
        neighbours without saying so anywhere."""
        self.assertTrue(any("CAP-01" in q or "Identity" in q for q in self._asked()))

    def test_a_persona_gap_is_not_somebodys_task(self):
        """Real, and not what stops a branch being walked. It stays in the workbook."""
        page = self._page()
        self.assertNotIn("Personas</h3>", page)
        self.assertFalse(any("Cardmember" in question for question in self._asked()))

    def test_a_tool_gap_is_not_somebodys_task_either(self):
        page = self._page()
        self.assertNotIn("Tools</h3>", page)
        self.assertFalse(any("post_dispute" in question for question in self._asked()))

    def test_the_three_kinds_of_row_the_graph_is_made_of_are_the_headings(self):
        page = self._page()
        headings = re.findall(r'questions__group-title">([^<]+)<', page)
        self.assertTrue(set(headings) <= {"Decisions", "States", "Capabilities"}, headings)


class TestAnsweringOne(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.client = create_app(self.root).test_client()
        self.client.post("/workspaces", data={"name": "Answers"})
        book = _workbook(Path(tempfile.mkdtemp()))
        with open(book, "rb") as handle:
            self.client.post("/stage/intake/upload",
                             data={"files": (handle, book.name), "group": "intake_workbook"},
                             content_type="multipart/form-data")
        page = self.client.get("/stage/intake").data.decode()
        self.questions = re.findall(r'name="question-\d+" value="([^"]+)"', page)

    def _workspace(self) -> Workspace:
        return Workspace.load(next(self.root.iterdir()))

    def test_an_answer_is_kept_against_its_question(self):
        self.client.post("/stage/intake/answers",
                         data={"question-1": self.questions[0], "answer-1": "Approved, Referred"})
        answered = self._workspace().answered_questions()
        self.assertEqual(answered.get(self.questions[0]), "Approved, Referred")

    def test_a_later_stage_reads_it_as_an_answer_rather_than_as_a_remark(self):
        """The question travels with the answer. Without it the next draft is handed "Approved,
        Referred" with nothing saying what it answers, which is not information it can act on."""
        self.client.post("/stage/intake/answers",
                         data={"question-1": self.questions[0], "answer-1": "Approved, Referred"})
        line = self._workspace().note_lines()[0]
        self.assertIn("Q: ", line)
        self.assertIn("A: Approved, Referred", line)

    def test_a_blank_box_leaves_the_question_open(self):
        """Answering the ones you know and coming back for the rest has to be the normal case, or
        the list is a thing to be finished in one sitting and is therefore never started."""
        self.client.post("/stage/intake/answers",
                         data={"question-1": self.questions[0], "answer-1": "",
                               "question-2": self.questions[1], "answer-2": "Gating"})
        answered = self._workspace().answered_questions()
        self.assertNotIn(self.questions[0], answered)
        self.assertIn(self.questions[1], answered)

    def test_the_page_says_how_far_through_the_list_somebody_is(self):
        self.client.post("/stage/intake/answers",
                         data={"question-1": self.questions[0], "answer-1": "Approved, Referred"})
        page = self.client.get("/stage/intake").data.decode()
        self.assertIn(f"1 of {len(self.questions)} answered", page)

    def test_an_answered_question_stays_editable_with_what_was_said_in_the_box(self):
        """A second thought about an answer is worth more than the first one."""
        self.client.post("/stage/intake/answers",
                         data={"question-1": self.questions[0], "answer-1": "Approved, Referred"})
        self.assertIn('value="Approved, Referred"',
                      self.client.get("/stage/intake").data.decode())

    def test_a_plain_note_still_carries_no_question(self):
        """The note box beside these is not an answer to anything, and labelling it as one would
        put a question in the context that nobody was asked."""
        self.client.post("/stage/intake/note", data={"note": "Ask the vendor about retries."})
        line = self._workspace().note_lines()[0]
        self.assertNotIn("Q: ", line)
        self.assertIn("Ask the vendor about retries.", line)


if __name__ == "__main__":
    unittest.main()
