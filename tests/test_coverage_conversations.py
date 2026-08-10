"""Reading conversations, and turning them into what the team's testing actually covers.

The coverage stage's input is transcripts, not a scenario list. Two things are pinned here.

Reading: the three layouts a real submission arrives in, and the rule that a team's own grouping
is carried but never trusted -- it is the thing being checked.

Counting: one conversation lands in exactly one scenario's bucket, every scenario is reported
including the ones nothing reached, and *represented* is a line drawn at a threshold the caller
sets rather than a property baked in here.
"""
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from scenario_generator.core.models import ScenarioRow
from scenario_generator.core.representation import build_report
from scenario_generator.ingest.conversations import (UnreadableConversations, read_conversations,
                                                     _split_prefixed)
from scenario_generator.llm.conversation_mapping import Mapping, _transcript
from scenario_generator.ingest.conversations import Conversation, Turn


def _sheet(rows) -> Path:
    path = Path(tempfile.mkdtemp()) / "conversations.xlsx"
    book = Workbook()
    for row in rows:
        book.active.append(row)
    book.save(path)
    return path


def _bench(*ids) -> list:
    return [ScenarioRow(id=i, path_str="DEC-01=Pass", category="Happy path",
                              materiality="High", capabilities=[], persona_id="P1",
                              signature=()) for i in ids]


class TestReadingTurnPerRow(unittest.TestCase):
    def setUp(self):
        self.path = _sheet([
            ["Conversation ID", "Turn", "Speaker", "Message", "Scenario"],
            ["C1", 1, "Customer", "I want to dispute a charge on my card", "Dispute"],
            ["C1", 2, "Agent", "Can you confirm the last four of your SSN?", "Dispute"],
            ["C1", 3, "Customer", "It is 1234", "Dispute"],
            ["C2", 1, "Customer", "Why was I charged twice for this order", ""],
            ["C2", 2, "Agent", "I cannot verify you, transferring now.", ""],
        ])

    def test_rows_sharing_an_id_become_one_conversation(self):
        conversations, _ = read_conversations(self.path)
        self.assertEqual([c.id for c in conversations], ["C1", "C2"])
        self.assertEqual(len(conversations[0].turns), 3)

    def test_speakers_are_normalised_to_the_side_they_are_on(self):
        conversations, _ = read_conversations(self.path)
        self.assertEqual([t.speaker for t in conversations[0].turns],
                         ["user", "agent", "user"])

    def test_turns_keep_the_order_they_were_submitted_in(self):
        conversations, _ = read_conversations(self.path)
        self.assertTrue(conversations[0].transcript.startswith("User: I want to dispute"))
        self.assertTrue(conversations[0].transcript.endswith("User: It is 1234"))

    def test_the_teams_own_label_is_carried(self):
        conversations, _ = read_conversations(self.path)
        self.assertEqual(conversations[0].group, "Dispute")
        self.assertEqual(conversations[1].group, "")

    def test_how_it_was_read_is_reported(self):
        _, how = read_conversations(self.path)
        self.assertIn("one row per turn", how)


class TestReadingOtherLayouts(unittest.TestCase):
    def test_a_whole_transcript_in_one_cell_is_split_on_its_prefixes(self):
        path = _sheet([["id", "transcript", "scenario"],
                       ["X-1", "User: I lost my card\nAgent: I can block it\nUser: yes please",
                        "Lost card"]])
        conversations, how = read_conversations(path)
        self.assertEqual(len(conversations), 1)
        self.assertEqual(len(conversations[0].turns), 3)
        self.assertEqual(conversations[0].group, "Lost card")
        self.assertIn("one row per conversation", how)

    def test_a_document_with_no_table_reads_by_heading(self):
        path = Path(tempfile.mkdtemp()) / "notes.md"
        path.write_text("# First\nUser: check a charge\nAgent: which one?\n\n"
                        "# Second\nCustomer: my card fails\nBot: it is under review\n")
        conversations, _ = read_conversations(path)
        self.assertEqual([c.id for c in conversations], ["First", "Second"])

    def test_a_wrapped_line_continues_the_turn_above_it(self):
        turns = _split_prefixed("User: I would like to ask\nabout a charge\nAgent: certainly")
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[0].text, "I would like to ask about a charge")

    def test_an_unreadable_file_says_so_rather_than_reading_as_empty(self):
        path = Path(tempfile.mkdtemp()) / "empty.md"
        path.write_text("nothing that looks like an exchange")
        with self.assertRaises(UnreadableConversations):
            read_conversations(path)

    def test_an_unsupported_format_is_refused_with_a_reason(self):
        path = Path(tempfile.mkdtemp()) / "thing.pptx"
        path.write_text("x")
        with self.assertRaises(UnreadableConversations):
            read_conversations(path)


class TestTruncationKeepsTheEnding(unittest.TestCase):
    """The match turns on where a conversation ends, so that is the part that must survive."""

    def test_a_long_transcript_is_cut_from_the_middle(self):
        turns = [Turn("user", "x" * 3000), Turn("agent", "y" * 3000),
                 Turn("agent", "THE DISPUTE IS FILED")]
        text = _transcript(Conversation(id="C1", turns=turns))
        self.assertIn("THE DISPUTE IS FILED", text)
        self.assertIn("omitted", text)

    def test_a_short_transcript_is_left_alone(self):
        text = _transcript(Conversation(id="C1", turns=[Turn("user", "hello there friend")]))
        self.assertEqual(text, "User: hello there friend")


class TestCountingWhatIsCovered(unittest.TestCase):
    def _report(self, threshold=1):
        return build_report([
            Mapping("C1", "SC-001", "high", declared_group="Dispute"),
            Mapping("C2", "SC-001", "high", declared_group="Dispute"),
            Mapping("C3", "SC-002", "low", declared_group="Lost card"),
            Mapping("C4", "SC-003", "high", declared_group="Lost card"),
            Mapping("C5", "", "high", declared_group="Lost card"),
        ], _bench("SC-001", "SC-002", "SC-003", "SC-004"), threshold=threshold)

    def test_every_scenario_appears_including_the_untouched_ones(self):
        report = self._report()
        self.assertEqual(len(report.scenarios), 4)
        self.assertEqual([s.scenario.id for s in report.untouched()], ["SC-004"])

    def test_conversations_are_counted_per_scenario(self):
        counts = {s.scenario.id: s.count for s in self._report().scenarios}
        self.assertEqual(counts, {"SC-001": 2, "SC-002": 1, "SC-003": 1, "SC-004": 0})

    def test_a_conversation_matching_nothing_is_recorded_rather_than_dropped(self):
        report = self._report()
        self.assertEqual(report.unmatched, ["C5"])
        self.assertEqual(report.conversations, 5)

    def test_the_threshold_decides_what_counts_as_represented(self):
        self.assertEqual(len(self._report(threshold=1).represented()), 3)
        self.assertEqual(len(self._report(threshold=2).represented()), 1)
        self.assertEqual([s.scenario.id for s in self._report(threshold=2).under_represented()],
                         ["SC-004", "SC-002", "SC-003"])

    def test_confidence_is_reported_beside_the_count_not_folded_into_it(self):
        by_id = {s.scenario.id: s for s in self._report().scenarios}
        self.assertEqual(by_id["SC-001"].confidence_summary, "2 high")
        self.assertEqual(by_id["SC-002"].confidence_summary, "1 low")
        # A low-confidence mapping still counts toward representation; a person judges it.
        self.assertTrue(by_id["SC-002"].represented(1))

    def test_the_least_covered_scenarios_are_listed_first(self):
        self.assertEqual([s.scenario.id for s in self._report().scenarios][0], "SC-004")


class TestAssessingTheTeamsOwnGrouping(unittest.TestCase):
    def _groups(self, mappings):
        report = build_report(mappings, _bench("SC-001", "SC-002", "SC-003"))
        return {g.group: g for g in report.groups}

    def test_a_group_whose_conversations_agree_is_reported_as_agreeing(self):
        groups = self._groups([Mapping("C1", "SC-001", "high", declared_group="Dispute"),
                               Mapping("C2", "SC-001", "high", declared_group="Dispute")])
        self.assertTrue(groups["Dispute"].agrees)
        self.assertIn("All 2 map to SC-001", groups["Dispute"].verdict)

    def test_a_group_that_splits_is_reported_as_disagreeing(self):
        groups = self._groups([Mapping("C1", "SC-001", "high", declared_group="Mixed"),
                               Mapping("C2", "SC-002", "high", declared_group="Mixed")])
        self.assertFalse(groups["Mixed"].agrees)
        self.assertIn("disagree", groups["Mixed"].verdict)

    def test_the_dominant_scenario_is_named_where_one_conversation_strays(self):
        groups = self._groups([Mapping("C1", "SC-001", "high", declared_group="Mostly"),
                               Mapping("C2", "SC-001", "high", declared_group="Mostly"),
                               Mapping("C3", "SC-002", "high", declared_group="Mostly")])
        self.assertEqual(groups["Mostly"].dominant, "SC-001")
        self.assertEqual(groups["Mostly"].total, 3)

    def test_ungrouped_conversations_produce_no_group_at_all(self):
        report = build_report([Mapping("C1", "SC-001", "high")], _bench("SC-001"))
        self.assertEqual(report.groups, [])


if __name__ == "__main__":
    unittest.main()
