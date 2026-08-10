"""A file with the right extension and the wrong contents underneath.

A .xlsx file is a zip archive. Anything else saved under that extension -- an older .xls renamed
rather than converted, a download that stopped partway through, a password-protected export --
fails to open with `zipfile.BadZipFile: File is not a zip file`. That message names the container
format, not the fix, and is easy to misread as "this tool only accepts one kind of file" when the
real answer is "this particular upload is not what it claims to be" -- CSV works exactly as well.

Every place a workbook a person might have sent is opened has to turn that failure into something
they can act on, because it is the one openpyxl error a real submission will eventually produce.
"""
import tempfile
import unittest
from pathlib import Path

from scenario_generator.core.intake import read_intake, read_owner_scenarios
from scenario_generator.ingest.conversations import UnreadableConversations, read_conversations
from scenario_generator.io.workbooks import read_registry, read_scenarios


def _not_really_a_workbook(name: str) -> Path:
    """A file with a workbook's extension and none of its substance."""
    path = Path(tempfile.mkdtemp()) / name
    path.write_bytes(b"this is plain text, not a zip archive")
    return path


class TestTheirConversations(unittest.TestCase):
    """Where the user hit this: submitting their testing under Stage 1 or the coverage stage."""

    def test_a_bad_file_is_reported_not_traced(self):
        with self.assertRaises(UnreadableConversations) as raised:
            read_conversations(_not_really_a_workbook("their_conversations.xlsx"))
        self.assertNotIn("BadZipFile", str(raised.exception))
        self.assertNotIn("Traceback", str(raised.exception))

    def test_the_message_says_what_probably_happened(self):
        with self.assertRaises(UnreadableConversations) as raised:
            read_conversations(_not_really_a_workbook("their_conversations.xlsx"))
        message = str(raised.exception)
        self.assertIn(".xls", message)
        self.assertIn("password-protected", message)

    def test_the_message_says_csv_already_works(self):
        """The user's actual question: does it only take one format? No -- say so."""
        with self.assertRaises(UnreadableConversations) as raised:
            read_conversations(_not_really_a_workbook("their_conversations.xlsx"))
        self.assertIn(".csv", str(raised.exception))

    def test_a_csv_with_the_same_content_is_read_fine(self):
        """The format was never the obstacle; this proves it by succeeding."""
        path = Path(tempfile.mkdtemp()) / "their_conversations.csv"
        path.write_text("ID,Transcript\n"
                        "C1,\"User: I want to dispute a charge\nAgent: you are verified\"\n",
                        encoding="utf-8")
        found, _ = read_conversations(path)
        self.assertEqual(len(found), 1)

    def test_xlsm_gets_the_same_treatment_as_xlsx(self):
        with self.assertRaises(UnreadableConversations) as raised:
            read_conversations(_not_really_a_workbook("macro_conversations.xlsm"))
        self.assertIn("Excel workbook", str(raised.exception))


class TestTheIntakeWorkbook(unittest.TestCase):
    """The same bug, one call away from the intake upload."""

    def test_a_bad_intake_file_is_reported_not_traced(self):
        with self.assertRaises(ValueError) as raised:
            read_intake(str(_not_really_a_workbook("intake.xlsx")))
        message = str(raised.exception)
        self.assertNotIn("BadZipFile", message)
        self.assertIn("intake.xlsx", message)
        self.assertIn(".xls", message)

    def test_read_owner_scenarios_gets_the_same_treatment(self):
        with self.assertRaises(ValueError) as raised:
            read_owner_scenarios(str(_not_really_a_workbook("owner.xlsx")))
        message = str(raised.exception)
        self.assertNotIn("BadZipFile", message)
        self.assertIn("owner.xlsx", message)
        self.assertIn("Save As", message)


class TestAnIncompleteIntake(unittest.TestCase):
    """A workbook that opens fine and is not an intake, or is one with nothing in it.

    Both are ordinary things to upload by mistake, and both reach the reader as a bare IndexError
    or KeyError unless caught -- an error naming neither the file nor what was wrong with it.
    """

    def test_a_blank_template_says_what_it_is_missing(self):
        from scenario_generator.core.intake import write_template

        path = Path(tempfile.mkdtemp()) / "blank.xlsx"
        write_template(str(path))

        with self.assertRaises(ValueError) as raised:
            read_intake(str(path))
        message = str(raised.exception)
        self.assertIn("personas", message.lower())
        self.assertIn("blank.xlsx", message)

    def test_a_workbook_without_the_intake_sheets_names_them(self):
        from openpyxl import Workbook

        path = Path(tempfile.mkdtemp()) / "something_else.xlsx"
        book = Workbook()
        book.active.title = "Sheet1"
        book.save(path)

        with self.assertRaises(ValueError) as raised:
            read_intake(str(path))
        message = str(raised.exception)
        self.assertIn("something_else.xlsx", message)
        self.assertIn("L3 Decisions", message)


class TestInternallyWrittenWorkbooks(unittest.TestCase):
    """Registries and graphs this tool wrote itself, read back after something disturbed them."""

    def test_a_disturbed_registry_is_reported_not_traced(self):
        with self.assertRaises(ValueError) as raised:
            read_registry(str(_not_really_a_workbook("registry.xlsx")))
        message = str(raised.exception)
        self.assertNotIn("BadZipFile", message)
        self.assertIn("registry.xlsx", message)

    def test_a_disturbed_graph_file_is_reported_not_traced(self):
        with self.assertRaises(ValueError) as raised:
            read_scenarios(str(_not_really_a_workbook("graph.xlsx")), intake=None)
        self.assertNotIn("BadZipFile", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
