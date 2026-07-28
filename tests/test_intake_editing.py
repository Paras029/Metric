"""Sketching an intake change, seeing it, then committing it.

Correcting an intake means editing a workbook, and a workbook edit is a round trip: change it,
save it, upload it, look at the graph, find it was wrong, start again. The sketch buffer is there
so the question "does this branch belong here?" can be answered before anything is committed.

Two properties matter more than the convenience. The workbook stays the only thing a benchmark is
ever built from, so a sketch must not reach it until someone says so. And committing must leave
the addition *reachable* — a new decision nothing leads to is a box that draws fine and generates
nothing, which is the failure mode that would make the whole feature quietly useless.
"""
import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook

from scenario_generator.core.intake import append_rows, read_intake, write_template
from scenario_generator.webapp.draft import (DECISION, STATE, PendingEdits, PendingItem, next_id)
from scenario_generator.webapp.graphview import build_layout


def _intake_workbook() -> Path:
    """A small intake with one branch, one state that continues and one that ends."""
    path = Path(tempfile.mkdtemp()) / "intake.xlsx"
    write_template(str(path))
    book = load_workbook(path)
    for row in book["L1 Use Case"].iter_rows(min_row=2):        # row 1 is the header
        if str(row[0].value or "").strip() == "Use case name":
            row[1].value = "Disputes"
        elif str(row[0].value or "").strip() == "Business objective":
            row[1].value = "Resolve disputes"
    book["Personas"].append(["P1", "Cardholder", "All", "Y"])
    book["L2 Capabilities"].append(["CAP-01", "Authenticate", "Gating"])
    book["L3 Decisions"].append(
        ["DEC-01", "Identity", "CAP-01", "credentials", "Pass / Fail", "User", 1, ""])
    book["L4 States"].append(["S-00", "Start", "The conversation opens", "DEC-01", "N", ""])
    book["L4 States"].append(["S-01", "DEC-01=Pass", "Authenticated", "", "N", ""])
    book["L4 States"].append(["S-02", "DEC-01=Fail", "Locked out", "", "Y", "Termination"])
    book.save(path)
    return path


class TestNaming(unittest.TestCase):
    def test_a_new_id_continues_the_intake_own_numbering(self):
        self.assertEqual(next_id("DEC", ["DEC-01", "DEC-02"]), "DEC-03")
        self.assertEqual(next_id("S", ["S-00", "S-01"]), "S-02")

    def test_a_gap_in_the_numbering_is_filled_rather_than_skipped(self):
        self.assertEqual(next_id("DEC", ["DEC-01", "DEC-03"]), "DEC-02")

    def test_the_first_id_does_not_need_anything_to_exist(self):
        self.assertEqual(next_id("DEC", []), "DEC-01")


class TestASketchIsOnlyASketch(unittest.TestCase):
    def test_the_workbook_is_untouched_until_it_is_committed(self):
        path = _intake_workbook()
        before = read_intake(str(path))

        pending = PendingEdits([PendingItem(kind=DECISION, id="DEC-02", name="Eligibility",
                                            outcomes=["Eligible", "Not eligible"],
                                            reached_from="S-01")])
        pending.merged(before)

        after = read_intake(str(path))
        self.assertEqual(len(after.decisions), len(before.decisions))

    def test_the_merged_view_leaves_the_real_intake_alone(self):
        """It is drawn from a copy, so nothing downstream can build a benchmark from a sketch."""
        intake = read_intake(str(_intake_workbook()))
        pending = PendingEdits([PendingItem(kind=DECISION, id="DEC-02", name="Eligibility",
                                            outcomes=["Eligible"], reached_from="S-01")])
        merged = pending.merged(intake)

        self.assertEqual(len(merged.decisions), len(intake.decisions) + 1)
        self.assertEqual([s.next_decisions for s in intake.states if s.id == "S-01"], [[]])


class TestSeeingItBeforeCommitting(unittest.TestCase):
    def test_an_attached_decision_appears_in_the_flow(self):
        intake = read_intake(str(_intake_workbook()))
        pending = PendingEdits([PendingItem(kind=DECISION, id="DEC-02", name="Eligibility",
                                            outcomes=["Eligible", "Not eligible"],
                                            reached_from="S-01")])
        layout = build_layout(pending.merged(intake), pending_decisions=["DEC-02"])

        self.assertIn("DEC-02", {edge.target for edge in layout.edges})
        self.assertTrue(layout.nodes["DEC-02"].pending)

    def test_an_unattached_decision_is_reported_as_having_nowhere_to_go(self):
        intake = read_intake(str(_intake_workbook()))
        pending = PendingEdits([PendingItem(kind=DECISION, id="DEC-02", name="Eligibility",
                                            outcomes=["Eligible"])])

        self.assertEqual(pending.unattached(intake), ["DEC-02"])
        row = pending.rows(intake)[0]
        self.assertFalse(row["attached"])
        self.assertIn("nothing leads to it", row["where"])

    def test_attaching_it_settles_that(self):
        intake = read_intake(str(_intake_workbook()))
        pending = PendingEdits([PendingItem(kind=DECISION, id="DEC-02", name="Eligibility",
                                            outcomes=["Eligible"], reached_from="S-01")])

        self.assertEqual(pending.unattached(intake), [])
        self.assertIn("reached from S-01", pending.rows(intake)[0]["where"])

    def test_a_state_is_attached_by_the_outcome_that_produces_it(self):
        intake = read_intake(str(_intake_workbook()))
        loose = PendingItem(kind=STATE, id="S-03", name="Referred to an adviser")
        pending = PendingEdits([loose])
        self.assertEqual(pending.unattached(intake), ["S-03"])

        loose.reached_via = "DEC-01=Fail"
        self.assertEqual(pending.unattached(intake), [])


class TestCommitting(unittest.TestCase):
    def test_a_committed_decision_ends_up_reachable(self):
        """The failure this guards: a new decision written in with nothing leading to it."""
        path = _intake_workbook()
        pending = PendingEdits([PendingItem(kind=DECISION, id="DEC-02", name="Eligibility",
                                            outcomes=["Eligible", "Not eligible"],
                                            reached_from="S-01")])
        decisions, states = pending.as_workbook_rows()
        append_rows(str(path), decisions=decisions, states=states, links=pending.links())

        after = read_intake(str(path))
        self.assertIn("DEC-02", {d.id for d in after.decisions})
        self.assertEqual([s.next_decisions for s in after.states if s.id == "S-01"], [["DEC-02"]])
        self.assertIn("DEC-02", {edge.target for edge in build_layout(after).edges})

    def test_a_committed_state_carries_the_outcome_that_reaches_it(self):
        path = _intake_workbook()
        pending = PendingEdits([PendingItem(kind=STATE, id="S-03", name="Referred to an adviser",
                                            reached_via="DEC-01=Fail", is_terminal=True,
                                            outcome_type="Escalation")])
        decisions, states = pending.as_workbook_rows()
        append_rows(str(path), decisions=decisions, states=states, links=pending.links())

        after = read_intake(str(path))
        added = next(s for s in after.states if s.id == "S-03")
        self.assertEqual(added.reached_via, "DEC-01=Fail")
        self.assertTrue(added.is_terminal)

    def test_everything_already_in_the_workbook_survives(self):
        path = _intake_workbook()
        pending = PendingEdits([PendingItem(kind=DECISION, id="DEC-02", name="Eligibility",
                                            outcomes=["Eligible"], reached_from="S-01")])
        decisions, states = pending.as_workbook_rows()
        append_rows(str(path), decisions=decisions, states=states, links=pending.links())

        after = read_intake(str(path))
        self.assertEqual(after.use_case["Use case name"], "Disputes")
        self.assertEqual({s.id for s in after.states}, {"S-00", "S-01", "S-02"})
        self.assertEqual([p.id for p in after.personas], ["P1"])

    def test_committing_the_same_id_twice_does_not_duplicate_it(self):
        path = _intake_workbook()
        pending = PendingEdits([PendingItem(kind=DECISION, id="DEC-02", name="Eligibility",
                                            outcomes=["Eligible"], reached_from="S-01")])
        decisions, states = pending.as_workbook_rows()
        append_rows(str(path), decisions=decisions, states=states, links=pending.links())
        append_rows(str(path), decisions=decisions, states=states, links=pending.links())

        after = read_intake(str(path))
        self.assertEqual(len([d for d in after.decisions if d.id == "DEC-02"]), 1)
        self.assertEqual([s.next_decisions for s in after.states if s.id == "S-01"], [["DEC-02"]])

    def test_the_buffer_empties_so_the_same_change_is_not_written_twice(self):
        pending = PendingEdits([PendingItem(kind=DECISION, id="DEC-02", name="Eligibility")])
        pending.clear()
        self.assertFalse(pending)
        self.assertEqual(pending.to_list(), [])


class TestTheBufferSurvivesARestart(unittest.TestCase):
    def test_a_sketch_round_trips_through_the_workspace_record(self):
        original = PendingEdits([
            PendingItem(kind=DECISION, id="DEC-02", name="Eligibility",
                        outcomes=["Eligible", "Not eligible"], reached_from="S-01"),
            PendingItem(kind=STATE, id="S-03", name="Referred", reached_via="DEC-01=Fail",
                        is_terminal=True)])

        restored = PendingEdits.from_list(original.to_list())
        self.assertEqual([i.id for i in restored.items], ["DEC-02", "S-03"])
        self.assertEqual(restored.items[0].outcomes, ["Eligible", "Not eligible"])
        self.assertEqual(restored.items[0].reached_from, "S-01")
        self.assertTrue(restored.items[1].is_terminal)

    def test_an_unrecognised_entry_does_not_bring_the_page_down(self):
        restored = PendingEdits.from_list([{"kind": "decision", "id": "DEC-09"}, "nonsense", None])
        self.assertEqual([i.id for i in restored.items], ["DEC-09"])


if __name__ == "__main__":
    unittest.main()
