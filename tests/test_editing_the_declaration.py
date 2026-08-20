"""Editing the declaration where it is read, instead of in a spreadsheet.

The workbook is the record and stays the record. What this removes is the round trip to change one
cell of it -- download, find the row, edit, save, upload -- because the judgement being made in
that loop ("this outcome leads to the wrong state") is made by looking at the graph, which is on
screen the whole time.

Two properties hold the whole thing up and both are tested here rather than assumed: an edit
touches only what it names, and a deletion is reported rather than cascaded.
"""
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from scenario_generator.core import editing
from scenario_generator.core.intake import read_intake
from scenario_generator.webapp.app import create_app
from scenario_generator.webapp.declaration import editable
from scenario_generator.webapp.graphview import highlights

EXAMPLE = (Path(__file__).resolve().parent.parent
           / "examples" / "intakes" / "1_disputes_three_blocks.xlsx")


class TestApplyingAnEdit(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "intake.xlsx"
        shutil.copy(EXAMPLE, self.path)

    def _intake(self):
        return read_intake(str(self.path))

    def test_a_field_is_changed_and_read_back(self):
        editing.apply_edits(str(self.path), [
            {"kind": "decision", "key": "DEC-03", "action": "upsert",
             "fields": {"name": "Verify the recent activity", "max_attempts": 3}}])
        found = next(d for d in self._intake().decisions if d.id == "DEC-03")
        self.assertEqual(found.name, "Verify the recent activity")
        self.assertEqual(found.max_attempts, 3)

    def test_nothing_the_edit_did_not_name_is_touched(self):
        """The property the whole module rests on. An editor that rewrites a row wholesale loses
        whatever a person typed into a column it does not know about."""
        before = self._intake()
        editing.apply_edits(str(self.path), [
            {"kind": "decision", "key": "DEC-03", "action": "upsert", "fields": {"inputs": "x"}}])
        after = self._intake()

        changed = next(d for d in after.decisions if d.id == "DEC-03")
        was = next(d for d in before.decisions if d.id == "DEC-03")
        self.assertEqual(changed.inputs, "x")
        self.assertEqual(changed.variants, was.variants)
        self.assertEqual(changed.name, was.name)
        self.assertEqual(changed.trigger_capability, was.trigger_capability)
        self.assertEqual([s.id for s in after.states], [s.id for s in before.states])
        self.assertEqual([c.exit_states for c in after.capabilities],
                         [c.exit_states for c in before.capabilities])

    def test_a_list_field_survives_the_round_trip_unchanged(self):
        """What goes in has to come out. A round trip that reformats is one that eventually loses
        something -- an outcome name with a comma in it, a span with a trailing space."""
        editing.apply_edits(str(self.path), [
            {"kind": "decision", "key": "DEC-03", "action": "upsert",
             "fields": {"outcomes": ["Verified", "Not verified", "Timed out"]}}])
        found = next(d for d in self._intake().decisions if d.id == "DEC-03")
        self.assertEqual(found.variants, ["Verified", "Not verified", "Timed out"])

    def test_a_new_row_is_appended_with_the_key_it_was_given(self):
        editing.apply_edits(str(self.path), [
            {"kind": "state", "key": "S-40", "action": "upsert",
             "fields": {"description": "A new position", "reached_via": "DEC-03=Verified",
                        "next_decisions": ["DEC-04"], "is_terminal": False}}])
        found = next(s for s in self._intake().states if s.id == "S-40")
        self.assertEqual(found.description, "A new position")
        self.assertEqual(found.next_decisions, ["DEC-04"])
        self.assertFalse(found.is_terminal)

    def test_a_flag_can_be_turned_off_as_well_as_on(self):
        """A checkbox that only ever sets is a checkbox that cannot be corrected."""
        for wanted in (True, False):
            editing.apply_edits(str(self.path), [
                {"kind": "decision", "key": "DEC-03", "action": "upsert",
                 "fields": {"out_of_scope": wanted}}])
            found = next(d for d in self._intake().decisions if d.id == "DEC-03")
            self.assertEqual(found.out_of_scope, wanted)

    def test_a_key_that_differs_only_in_case_finds_the_same_row(self):
        """An id typed by hand is as likely to be s-04 as S-04, and a second row for one state is
        the worst outcome available here -- both are read, and the graph gains a phantom."""
        before = len(self._intake().states)
        editing.apply_edits(str(self.path), [
            {"kind": "state", "key": "s-04", "action": "upsert",
             "fields": {"description": "Lowercase key"}}])
        after = self._intake()
        self.assertEqual(len(after.states), before)
        self.assertEqual(next(s for s in after.states if s.id == "S-04").description,
                         "Lowercase key")

    def test_a_batch_is_applied_together(self):
        """A half-applied batch is the state nobody can reason about: a decision written and the
        state that reaches it not, with no record of which half landed."""
        report = editing.apply_edits(str(self.path), [
            {"kind": "decision", "key": "DEC-20", "action": "upsert",
             "fields": {"name": "New branch", "outcomes": ["Yes", "No"],
                        "capability_id": "CAP-03"}},
            {"kind": "state", "key": "S-41", "action": "upsert",
             "fields": {"description": "After the new branch", "reached_via": "DEC-20=Yes",
                        "is_terminal": True, "outcome_type": "Happy path"}},
        ])
        self.assertEqual(len(report.written), 2)
        intake = self._intake()
        self.assertIn("DEC-20", [d.id for d in intake.decisions])
        self.assertIn("S-41", [s.id for s in intake.states])

    def test_an_unknown_kind_is_refused_rather_than_guessed_at(self):
        report = editing.apply_edits(str(self.path), [{"kind": "sprocket", "key": "X"}])
        self.assertTrue(report.refused)
        self.assertFalse(report.changed)


class TestRemoving(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "intake.xlsx"
        shutil.copy(EXAMPLE, self.path)

    def test_the_row_goes(self):
        editing.apply_edits(str(self.path), [
            {"kind": "decision", "key": "DEC-06", "action": "delete"}])
        self.assertNotIn("DEC-06", [d.id for d in read_intake(str(self.path)).decisions])

    def test_what_still_points_at_it_is_reported_not_tidied_away(self):
        """The rule that keeps a deletion honest. Cleaning up the references too turns one
        deliberate removal into several nobody asked for, and the states that named it are where
        the person deciding needs to look."""
        report = editing.apply_edits(str(self.path), [
            {"kind": "decision", "key": "DEC-03", "action": "delete"}])
        self.assertTrue(report.dangling, "nothing was said about what still names DEC-03")
        self.assertTrue(any("DEC-03" in line for line in report.dangling))

        after = read_intake(str(self.path))
        still = [s.id for s in after.states if "DEC-03" in s.next_decisions]
        self.assertTrue(still, "the references were cleaned up instead of reported")

    def test_removing_something_that_is_not_there_is_refused_not_ignored(self):
        report = editing.apply_edits(str(self.path), [
            {"kind": "decision", "key": "DEC-99", "action": "delete"}])
        self.assertTrue(report.refused)
        self.assertFalse(report.removed)


class TestPreviewing(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "intake.xlsx"
        shutil.copy(EXAMPLE, self.path)

    def test_it_says_what_the_declaration_would_become(self):
        peek, _ = editing.preview(str(self.path), [
            {"kind": "decision", "key": "DEC-30", "action": "upsert",
             "fields": {"name": "Proposed", "outcomes": ["A", "B"]}}])
        self.assertIn("DEC-30", [d.id for d in peek.decisions])

    def test_it_writes_nothing(self):
        before = len(read_intake(str(self.path)).decisions)
        editing.preview(str(self.path), [
            {"kind": "decision", "key": "DEC-30", "action": "upsert",
             "fields": {"name": "Proposed", "outcomes": ["A", "B"]}}])
        self.assertEqual(len(read_intake(str(self.path)).decisions), before)

    def test_it_leaves_no_scratch_file_behind(self):
        editing.preview(str(self.path), [
            {"kind": "state", "key": "S-50", "action": "upsert", "fields": {"description": "x"}}])
        self.assertEqual(sorted(p.name for p in self.path.parent.iterdir()), ["intake.xlsx"])


class TestWhatEachRowPointsAtInTheDrawing(unittest.TestCase):
    """Selecting a row should answer "which part of the agent is this" without anybody tracing it,
    and the answer is a different shape for each kind. Getting those differences right is most of
    what makes the panel worth sitting beside the picture."""

    @classmethod
    def setUpClass(cls):
        cls.intake = read_intake(str(EXAMPLE))
        cls.found = highlights(cls.intake)

    def test_a_decision_is_a_box(self):
        self.assertIn("DEC-03", self.found["decision"]["DEC-03"]["nodes"])

    def test_an_intermediate_state_is_an_arrow_rather_than_a_box(self):
        """The one that is easy to get wrong. Only a terminal state has a box; every other state
        *is* the arrow between the decision that produced it and the decision it offers, so a
        highlight that only looked for boxes would light nothing for most of the list."""
        middle = next(s for s in self.intake.states
                      if not s.is_terminal and s.next_decisions and s.reached_via.strip()
                      and s.reached_via.strip().lower() != "start")
        found = self.found["state"][middle.id]
        self.assertEqual(found["nodes"], [])
        self.assertTrue(found["edges"], f"{middle.id} lights nothing at all")

    def test_a_terminal_state_is_a_box(self):
        ending = next(s for s in self.intake.states if s.is_terminal)
        self.assertTrue(self.found["state"][ending.id]["nodes"])

    def test_a_capability_is_its_decisions_and_its_block(self):
        found = self.found["capability"]["CAP-02"]
        inside = [d.id for d in self.intake.decisions if d.trigger_capability == "CAP-02"]
        for decision_id in inside:
            self.assertIn(decision_id, found["nodes"])
        self.assertIn("CAP-02", found["nodes"],
                      "the block itself is not lit, so selecting it does nothing in the "
                      "collapsed drawing")

    def test_a_tool_is_where_it_is_used(self):
        """A tool has nothing of its own in the graph. What it has is the decisions of the
        capability it belongs to, which is the honest answer to "where is this used"."""
        tool = next(t for t in self.intake.tools if t.capability_id)
        used_by = {d.id for d in self.intake.decisions
                   if d.trigger_capability == tool.capability_id}
        self.assertEqual(set(self.found["tool"][tool.name]["nodes"]), used_by)

    def test_a_persona_lights_nothing_and_that_is_correct(self):
        """Every route is walked by every persona, so lighting anything would light the graph."""
        for persona in self.intake.personas:
            found = self.found["persona"][persona.id]
            self.assertEqual((found["nodes"], found["edges"]), ([], []))

    def test_every_edge_named_is_one_the_drawing_actually_has(self):
        """A highlight naming an arrow the picture does not contain lights nothing and reads as
        the feature being broken."""
        from scenario_generator.webapp.graphview import build_layout

        drawn = {(e.source, e.target, e.outcome) for e in build_layout(self.intake).edges}
        for by_key in self.found.values():
            for entry in by_key.values():
                for edge in entry["edges"]:
                    self.assertIn(tuple(edge), drawn)


class TestTheEditingRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp())
        cls.client = create_app(cls.root).test_client()
        cls.client.post("/workspaces", data={"name": "Editing"})
        with open(EXAMPLE, "rb") as handle:
            cls.client.post("/stage/intake/upload",
                            data={"files": (handle, EXAMPLE.name), "group": "intake_workbook"},
                            content_type="multipart/form-data")
        for key in ("intake", "workflow"):
            cls.client.post(f"/stage/{key}/run")
            for _ in range(600):
                if cls.client.get(f"/stage/{key}/progress").get_json()["status"] != "running":
                    break
                time.sleep(0.05)
        cls.workbook = cls.root / "editing" / EXAMPLE.name

    def _decisions(self):
        return [d.id for d in read_intake(str(self.workbook)).decisions]

    def test_the_declaration_reads_back_with_its_drawings(self):
        """Both drawings go with the rows on purpose. An editor that answers "saved" and leaves
        the picture as it was makes the reader reload to check, which is the round trip this
        removes."""
        body = self.client.get("/stage/intake/declaration").get_json()
        self.assertEqual([k["kind"] for k in body["declaration"]["kinds"]],
                         ["decision", "state", "capability", "tool", "persona"])
        self.assertIn("<svg", body["graph_svg"])
        self.assertIn("<svg", body["blocks_svg"])
        self.assertIn("decision", body["highlights"])

    def test_a_preview_changes_the_drawing_and_not_the_file(self):
        before = self._decisions()
        body = self.client.post("/stage/intake/declaration/preview", json={"edits": [
            {"kind": "decision", "key": "DEC-60", "action": "upsert",
             "fields": {"name": "Only previewed", "outcomes": ["A", "B"],
                        "capability_id": "CAP-03"}}]}).get_json()
        self.assertIn("DEC-60", [r["key"] for r in body["declaration"]["rows"]["decision"]])
        self.assertEqual(self._decisions(), before, "a preview wrote to the workbook")

    def test_saving_writes_and_marks_the_later_stages_out_of_date(self):
        """The part that is easy to leave out and expensive to get wrong. A corrected branch
        changes which routes exist, so a scenario space built before it is a space for a different
        agent -- and a page showing it as finished would be the worst kind of wrong.

        The workflow stage is re-run first, deliberately: another test in this class may already
        have made it stale, and invalidating something already stale is correctly a no-op. What is
        being tested is that a save invalidates, not that this particular save was the first to.
        """
        self.client.post("/stage/workflow/run")
        for _ in range(600):
            if self.client.get("/stage/workflow/progress").get_json()["status"] != "running":
                break
            time.sleep(0.05)

        body = self.client.post("/stage/intake/declaration/save", json={"edits": [
            {"kind": "decision", "key": "DEC-61", "action": "upsert",
             "fields": {"name": "Saved from the page", "outcomes": ["A", "B"],
                        "capability_id": "CAP-03"}}]}).get_json()
        self.assertIn("DEC-61", self._decisions())
        self.assertIn("Workflow", body["invalidated"])

    def test_a_deletion_comes_back_with_what_still_points_at_it(self):
        self.client.post("/stage/intake/declaration/save", json={"edits": [
            {"kind": "decision", "key": "DEC-61", "action": "delete"}]})
        body = self.client.post("/stage/intake/declaration/preview", json={"edits": [
            {"kind": "decision", "key": "DEC-04", "action": "delete"}]}).get_json()
        self.assertTrue(body["report"]["dangling"])

    def test_one_row_can_be_posted_as_an_ordinary_form(self):
        """The path that works with scripting off. Everything else in this interface does, and an
        editor that needs a script to save anything at all would be the one exception."""
        answer = self.client.post("/stage/intake/declaration/row", data={
            "kind": "state", "key": "S-70", "action": "upsert",
            "description": "Added without scripting", "reached_via": "DEC-01=Dispute"})
        self.assertEqual(answer.status_code, 302)
        found = next(s for s in read_intake(str(self.workbook)).states if s.id == "S-70")
        self.assertEqual(found.description, "Added without scripting")

    def test_the_page_carries_the_rows_and_what_they_point_at(self):
        page = self.client.get("/stage/intake").get_data(as_text=True)
        self.assertIn("data-editor", page)
        self.assertIn('data-editor-tab="decision"', page)
        self.assertIn("METRIC_HIGHLIGHTS", page)
        self.assertIn('data-row-kind="state"', page)

    def test_the_editor_is_only_on_the_intake_stage(self):
        """Every later stage is built *from* the declaration. An editor on one of those would let
        somebody change the graph a scenario space was already walked from, on a page with no way
        to say what that cost."""
        for key in ("workflow", "scenarios", "summary"):
            page = self.client.get(f"/stage/{key}").get_data(as_text=True)
            self.assertNotIn("data-editor-tab", page, f"the editor is on the {key} stage")


class TestWhatThePanelOffers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.editable = editable(read_intake(str(EXAMPLE)))

    def test_every_kind_has_fields_and_rows(self):
        for entry in self.editable["kinds"]:
            self.assertTrue(self.editable["fields"][entry["kind"]])
            self.assertEqual(len(self.editable["rows"][entry["kind"]]), entry["count"])

    def test_a_dropdown_only_offers_ids_that_exist(self):
        """Assembled in one call rather than per kind, because the vocabularies cross. Built
        separately is how a dropdown ends up offering an id removed two edits ago."""
        declared = {entry["id"] for entry in self.editable["vocabulary"]["capabilities"]}
        self.assertEqual(declared, {row["key"] for row in self.editable["rows"]["capability"]})

    def test_the_next_id_carries_on_from_the_highest_rather_than_counting_rows(self):
        """A declaration with DEC-01, DEC-02 and DEC-07 has had rows removed, and reusing DEC-03
        would silently attach a new decision to whatever still references the old one."""
        self.assertEqual(editing.next_id(["DEC-01", "DEC-02", "DEC-07"], "DEC-"), "DEC-08")
        self.assertEqual(editing.next_id([], "S-"), "S-01")

    def test_a_capability_carries_its_span_control_rather_than_two_text_boxes(self):
        """Where a block starts is a judgement made by reading the drawing and where it ends is
        arithmetic. Neither is served by a comma-separated list of ids typed from memory."""
        row = self.editable["rows"]["capability"][0]
        self.assertIn("span", row)
        self.assertIn("entry_options", row["span"])
        # Declared as an ordinary field so it stages, previews and saves through the one path
        # everything else does, but rendered by the span control rather than as a text box.
        self.assertIn("entry_states", row["fields"])
        spec = next(f for f in self.editable["fields"]["capability"]
                    if f["name"] == "entry_states")
        self.assertEqual(spec["kind"], "states")

    def test_every_row_carries_the_questions_the_audit_asks_about_it(self):
        """A question asked in one place and answerable in another is a question nobody closes."""
        broken = read_intake(str(EXAMPLE.parent / "4_spans_drawn_wrongly.xlsx"))
        rows = editable(broken)["rows"]
        self.assertTrue(any(row["problems"] for kind in rows for row in rows[kind]),
                        "nothing on any row says what the audit wants from it")


if __name__ == "__main__":
    unittest.main()


class TestWhatAnEditDoesToTheStages(unittest.TestCase):
    """Editing a row does not make the intake out of date.

    The declaration *is* the workbook, the workbook has just been written, and the page is
    rendered from it -- so a stage marked stale was telling somebody to re-run the one thing that
    is already current. What the edit does make stale is everything built from the declaration,
    which is a real and expensive difference and the one worth showing.
    """

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.client = create_app(self.root).test_client()
        self.client.post("/workspaces", data={"name": "Staleness"})
        with open(EXAMPLE, "rb") as handle:
            self.client.post("/stage/intake/upload",
                             data={"files": (handle, EXAMPLE.name), "group": "intake_workbook"},
                             content_type="multipart/form-data")
        for key in ("intake", "workflow"):
            self.client.post(f"/stage/{key}/run")
            for _ in range(600):
                if self.client.get(f"/stage/{key}/progress").get_json()["status"] != "running":
                    break
                time.sleep(0.05)

    def _workspace(self):
        from scenario_generator.webapp.workspace import Workspace
        return Workspace.load(self.root / "staleness")

    def _add(self):
        return self.client.post("/stage/intake/declaration/save", json={"edits": [
            {"kind": "decision", "key": "DEC-50", "action": "upsert",
             "fields": {"name": "Added", "outcomes": ["A", "B"],
                        "capability_id": "CAP-03"}}]}).get_json()

    def test_the_intake_stays_complete(self):
        self._add()
        self.assertEqual(self._workspace().state("intake").status, "complete")

    def test_what_was_built_from_it_goes_stale(self):
        body = self._add()
        self.assertEqual(self._workspace().state("workflow").status, "stale")
        self.assertIn("Workflow", body["invalidated"])

    def test_the_counts_the_stage_reports_are_brought_up_to_date(self):
        """The one thing that genuinely does go stale on the intake. A count is a fact about the
        file and cheap to recompute; asking for a model run to correct it would be absurd."""
        before = self._workspace().state("intake").summary["Decision points"]
        self._add()
        self.assertEqual(self._workspace().state("intake").summary["Decision points"], before + 1)

    def test_the_same_holds_for_a_row_posted_without_scripting(self):
        self.client.post("/stage/intake/declaration/row", data={
            "kind": "state", "key": "S-60", "action": "upsert", "description": "Added"})
        workspace = self._workspace()
        self.assertEqual(workspace.state("intake").status, "complete")
        self.assertEqual(workspace.state("workflow").status, "stale")


class TestEditingASpan(unittest.TestCase):
    """A span is edited through the same path as every other field.

    It used to be a form of its own that posted and redirected, which navigated out of the
    expanded view every time somebody drew one -- and offered only a shortlist, so a span could
    not name a state the shortlist had pruned.
    """

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.client = create_app(self.root).test_client()
        self.client.post("/workspaces", data={"name": "Spans"})
        with open(EXAMPLE, "rb") as handle:
            self.client.post("/stage/intake/upload",
                             data={"files": (handle, EXAMPLE.name), "group": "intake_workbook"},
                             content_type="multipart/form-data")
        self.client.post("/stage/intake/run")
        for _ in range(600):
            if self.client.get("/stage/intake/progress").get_json()["status"] != "running":
                break
            time.sleep(0.05)
        self.workbook = self.root / "spans" / EXAMPLE.name

    def _span(self, capability_id="CAP-02"):
        found = next(c for c in read_intake(str(self.workbook)).capabilities
                     if c.id == capability_id)
        return found.entry_states, found.exit_states

    def test_it_stages_and_saves_like_any_other_field(self):
        self.client.post("/stage/intake/declaration/save", json={"edits": [
            {"kind": "capability", "key": "CAP-02", "action": "upsert",
             "fields": {"entry_states": ["S-02"], "exit_states": ["S-07", "S-08"]}}]})
        self.assertEqual(self._span(), (("S-02",), ("S-07", "S-08")))

    def test_a_state_the_shortlist_would_have_pruned_can_still_be_named(self):
        """A span may legitimately begin or end anywhere. A control that only offers what it
        guessed at is a span that cannot be corrected."""
        from scenario_generator.core.graph import DecisionGraph, entry_candidates

        intake = read_intake(str(self.workbook))
        graph = DecisionGraph(intake.decisions, intake.states)
        shortlisted = set(entry_candidates(graph, "CAP-02", intake.decisions))
        odd = next(s.id for s in intake.states if s.id not in shortlisted and not s.is_terminal)

        self.client.post("/stage/intake/declaration/save", json={"edits": [
            {"kind": "capability", "key": "CAP-02", "action": "upsert",
             "fields": {"entry_states": [odd], "exit_states": ["S-07"]}}]})
        self.assertEqual(self._span()[0], (odd,))

    def test_a_span_can_be_cleared_by_ticking_nothing(self):
        """Undividing a capability has to be as available as dividing one, and an empty list is
        how a set of checkboxes says nothing is ticked."""
        self.client.post("/stage/intake/declaration/save", json={"edits": [
            {"kind": "capability", "key": "CAP-02", "action": "upsert",
             "fields": {"entry_states": [], "exit_states": []}}]})
        self.assertEqual(self._span(), ((), ()))

    def test_the_page_offers_every_state_behind_the_shortlist(self):
        page = self.client.get("/stage/intake").get_data(as_text=True)
        self.assertIn("Every state", page)
        self.assertIn('type="checkbox" name="entry_states"', page)

    def test_the_collapsed_view_has_somewhere_to_appear(self):
        """A declaration that starts with no spans had no blocks wrapper and no toggle, so drawing
        the first span could never produce a collapsed view however correctly it was drawn."""
        spanless = create_app(Path(tempfile.mkdtemp())).test_client()
        spanless.post("/workspaces", data={"name": "No spans"})
        source = EXAMPLE.parent / "2_travel_no_spans.xlsx"
        with open(source, "rb") as handle:
            spanless.post("/stage/intake/upload",
                          data={"files": (handle, source.name), "group": "intake_workbook"},
                          content_type="multipart/form-data")
        spanless.post("/stage/intake/run")
        for _ in range(600):
            if spanless.get("/stage/intake/progress").get_json()["status"] != "running":
                break
            time.sleep(0.05)

        page = spanless.get("/stage/intake").get_data(as_text=True)
        self.assertIn('data-graph-view="blocks"', page, "there is nowhere to put a collapsed view")
        self.assertIn("data-graph-detail", page, "there is no control to switch to it")

        drawn = spanless.post("/stage/intake/declaration/preview", json={"edits": [
            {"kind": "capability", "key": "CAP-01", "action": "upsert",
             "fields": {"entry_states": ["S-00"], "exit_states": ["S-02", "S-03"]}}]}).get_json()
        self.assertIn("<svg", drawn["blocks_svg"],
                      "drawing the first span produced no collapsed view")
