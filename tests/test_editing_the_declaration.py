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
from scenario_generator.webapp.declaration import editable, graph_index
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


class TestAddingARow(unittest.TestCase):
    """Add stages a key and nothing else, because there is nothing to fill in until the row is
    there. That made it look like an edit changing nothing, it was dropped as one, and the button
    posted, came back reporting success, and created nothing."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.client = create_app(self.root).test_client()
        self.client.post("/workspaces", data={"name": "Adding"})
        with open(EXAMPLE, "rb") as handle:
            self.client.post("/stage/intake/upload",
                             data={"files": (handle, EXAMPLE.name), "group": "intake_workbook"},
                             content_type="multipart/form-data")
        self.client.post("/stage/intake/run")
        for _ in range(600):
            if self.client.get("/stage/intake/progress").get_json()["status"] != "running":
                break
            time.sleep(0.05)
        self.path = str(self.root / "adding" / EXAMPLE.name)

    def _states(self):
        return [s.id for s in read_intake(self.path).states]

    def test_a_key_and_nothing_else_creates_the_row(self):
        before = self._states()
        answer = self.client.post("/stage/intake/declaration/save", json={
            "edits": [{"kind": "state", "key": "S-90", "action": "upsert", "fields": {}}]})
        self.assertEqual(answer.status_code, 200)
        self.assertIsNone(answer.get_json().get("error"))
        self.assertEqual(set(self._states()) - set(before), {"S-90"})

    def test_it_says_it_wrote_something(self):
        """A report of nothing written under a row that did appear is the same confusion the
        other way round."""
        answer = self.client.post("/stage/intake/declaration/save", json={
            "edits": [{"kind": "decision", "key": "DEC-90", "action": "upsert", "fields": {}}]})
        self.assertTrue(answer.get_json()["report"]["written"])

    def test_the_new_row_is_on_the_page_ready_to_fill_in(self):
        self.client.post("/stage/intake/declaration/save", json={
            "edits": [{"kind": "state", "key": "S-91", "action": "upsert", "fields": {}}]})
        page = self.client.get("/stage/intake").get_data(as_text=True)
        self.assertIn('data-row-key="S-91"', page)

    def test_a_declaration_of_any_size_takes_another(self):
        """Reported as a size limit -- eighty states in and the button stopped working. It was
        never the size; it was that Add had never worked through this route at all."""
        for n in range(20, 90):
            self.client.post("/stage/intake/declaration/save", json={
                "edits": [{"kind": "state", "key": f"S-{n}", "action": "upsert",
                           "fields": {"description": f"Filler {n}"}}]})
        before = self._states()
        self.assertGreater(len(before), 79)
        self.client.post("/stage/intake/declaration/save", json={
            "edits": [{"kind": "state", "key": "S-95", "action": "upsert", "fields": {}}]})
        self.assertIn("S-95", self._states())


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
                         ["use_case", "decision", "state", "capability", "tool", "persona"])
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
            if entry["singleton"]:
                # One, and only ever one, so it reports no count -- "Use case 1" beside every
                # other tab's real tally reads as a number that means something.
                self.assertIsNone(entry["count"])
                self.assertEqual(len(self.editable["rows"][entry["kind"]]), 1)
            else:
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


class TestBuildingACapabilityFromItsDecisions(unittest.TestCase):
    """The order that makes a capability editable at all.

    Membership is recorded on the *decisions* -- a decision belongs to one capability and the
    workbook says so where the decision is, which is the right place to record it. It was the
    wrong place to edit it from, and that was the whole defect: the capability's boundary controls
    were computed from its membership, while its own row could not change that membership. A
    grouping that was wrong could be seen and not corrected, and every shortlist derived from it
    was wrong in the same way.

    So the capability row carries the membership, and the rest follows: tick the decisions, see
    the states they fold in, pick the boundary from those.
    """

    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "intake.xlsx"
        shutil.copy(EXAMPLE, self.path)

    def _owns(self):
        found = {}
        for decision in read_intake(str(self.path)).decisions:
            found.setdefault(decision.trigger_capability, []).append(decision.id)
        return found

    def test_ticking_a_decision_moves_it_into_the_capability(self):
        editing.apply_edits(str(self.path), [
            {"kind": "capability", "key": "CAP-02", "action": "upsert",
             "fields": {"decisions": ["DEC-03", "DEC-04", "DEC-05"]}}])
        self.assertIn("DEC-05", self._owns()["CAP-02"])

    def test_it_moves_rather_than_copies(self):
        """A decision belonging to two capabilities is something the graph cannot represent, and
        the walk resolves silently by taking whichever it read first."""
        before = self._owns()["CAP-03"]
        self.assertIn("DEC-05", before)
        editing.apply_edits(str(self.path), [
            {"kind": "capability", "key": "CAP-02", "action": "upsert",
             "fields": {"decisions": ["DEC-03", "DEC-05"]}}])
        self.assertNotIn("DEC-05", self._owns().get("CAP-03", []))

    def test_unticking_a_decision_takes_it_out(self):
        editing.apply_edits(str(self.path), [
            {"kind": "capability", "key": "CAP-02", "action": "upsert",
             "fields": {"decisions": ["DEC-03"]}}])
        self.assertNotIn("DEC-04", self._owns().get("CAP-02", []))
        self.assertIn("DEC-04", self._owns().get("", []),
                      "the decision was left belonging to nothing rather than to no capability")

    def test_it_leaves_other_capabilities_alone(self):
        before = self._owns()["CAP-01"]
        editing.apply_edits(str(self.path), [
            {"kind": "capability", "key": "CAP-02", "action": "upsert",
             "fields": {"decisions": ["DEC-03", "DEC-04"]}}])
        self.assertEqual(self._owns()["CAP-01"], before)

    def test_membership_and_boundary_save_together(self):
        """Both are edits to one capability, made in one pass over its row."""
        editing.apply_edits(str(self.path), [
            {"kind": "capability", "key": "CAP-02", "action": "upsert",
             "fields": {"decisions": ["DEC-03", "DEC-04", "DEC-05"],
                        "entry_states": ["S-02"], "exit_states": ["S-11"]}}])
        intake = read_intake(str(self.path))
        found = next(c for c in intake.capabilities if c.id == "CAP-02")
        self.assertEqual((found.entry_states, found.exit_states), (("S-02",), ("S-11",)))
        self.assertIn("DEC-05", [d.id for d in intake.decisions
                                 if d.trigger_capability == "CAP-02"])

    def test_the_expansion_is_a_difference_not_an_addition(self):
        """Reading the workbook is what makes it one. A tick that only ever added would say
        nothing about the decisions no longer ticked."""
        grown = editing.expand(str(self.path), [
            {"kind": "capability", "key": "CAP-02", "action": "upsert",
             "fields": {"decisions": ["DEC-03", "DEC-05"]}}])
        written = {(e["key"], e["fields"].get("capability_id"))
                   for e in grown if e["kind"] == "decision"}
        self.assertIn(("DEC-05", "CAP-02"), written, "the decision moving in was not written")
        self.assertIn(("DEC-04", ""), written, "the decision moving out was not written")
        self.assertNotIn(("DEC-03", "CAP-02"), written,
                         "a decision already there was rewritten for no reason")

    def test_nothing_is_written_where_the_membership_is_unchanged(self):
        grown = editing.expand(str(self.path), [
            {"kind": "capability", "key": "CAP-02", "action": "upsert",
             "fields": {"decisions": ["DEC-03", "DEC-04"], "name": "Verification"}}])
        self.assertEqual([e for e in grown if e["kind"] == "decision"], [])

    def test_the_virtual_field_never_reaches_a_column(self):
        """There is no Decisions column on the capabilities sheet, and writing one would put a
        second answer beside the one the decisions already give."""
        grown = editing.expand(str(self.path), [
            {"kind": "capability", "key": "CAP-02", "action": "upsert",
             "fields": {"decisions": ["DEC-03"]}}])
        capability = next(e for e in grown if e["kind"] == "capability")
        self.assertNotIn("decisions", capability["fields"])


class TestWhatTheCapabilityControlIsGiven(unittest.TestCase):
    def setUp(self):
        self.intake = read_intake(str(EXAMPLE))

    def test_every_decision_is_offered_with_where_it_belongs(self):
        """The edit somebody needs is usually "that one belongs here, not there", and a list of
        only what is already here cannot express it."""
        from scenario_generator.webapp.declaration import editable, graph_index

        offered = editable(self.intake)["vocabulary"]["decisions"]
        self.assertEqual({d["id"] for d in offered}, {d.id for d in self.intake.decisions})
        self.assertTrue(any(d["capability"] for d in offered))

    def test_the_page_carries_the_adjacency_it_needs(self):
        """Which states a decision is offered by and routes to -- so ticking one answers as fast
        as the ticking, rather than a round trip per checkbox."""
        index = graph_index(self.intake)
        self.assertEqual(set(index["decisions"]), {d.id for d in self.intake.decisions})
        entry = index["decisions"]["DEC-03"]
        self.assertTrue(entry["offered_by"])
        self.assertTrue(entry["lands_on"])
        self.assertEqual(set(index["states"]), {s.id for s in self.intake.states})

    def test_the_adjacency_only_names_states_that_exist(self):
        """An outcome leading nowhere gets a synthetic id in the walk. Offering one as a boundary
        would put a state in the span that the workbook does not have."""
        index = graph_index(self.intake)
        declared = {s.id for s in self.intake.states}
        for entry in index["decisions"].values():
            for state_id in entry["offered_by"] + entry["lands_on"]:
                self.assertIn(state_id, declared)

    def test_it_carries_no_policy_only_adjacency(self):
        """What counts as an exit is a judgement -- see core.graph.exits_for -- and it stays in
        one language. A second implementation on the page would disagree with the first.

        The start states are the one addition, and they are adjacency too: the states nothing
        routes to. The entry picker falls back to them when a capability's decisions are offered
        by nothing, and that fallback has to be identical on the page and on the server or the
        shortlist would rearrange itself under a tick that did not ask it to.
        """
        index = graph_index(self.intake)
        self.assertEqual(set(index), {"decisions", "states", "start_states"})
        self.assertEqual(set(index["decisions"]["DEC-03"]),
                         {"name", "capability", "offered_by", "lands_on"})

    def test_the_start_states_match_the_graph(self):
        """Same answer as the walk's, not a second guess at it."""
        from scenario_generator.core.graph import DecisionGraph
        graph = DecisionGraph(self.intake.decisions, self.intake.states)
        self.assertEqual(graph_index(self.intake)["start_states"], list(graph.start_states))


class TestNoStateCanBecomeUnpickable(unittest.TestCase):
    """A span may begin or end at any state, so every state has to stay reachable in the control.

    The way it stopped being true is worth stating, because it is a whole class of bug. The
    shortlist is rendered by the server and then *redrawn on the page* from the decisions as they
    are ticked -- two lists over the same set, maintained in two places. The "every state" list was
    rendered excluding whatever the shortlist held at the moment the page was built, which was
    correct then and wrong the instant a decision was unticked: the states that left the shortlist
    were in neither list, and could not be picked at all.

    The fix is the invariant rather than a patch: the long list holds every state, always, and
    entries are hidden as they appear above. The union is every state by construction.
    """

    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp())
        cls.client = create_app(cls.root).test_client()
        cls.client.post("/workspaces", data={"name": "Pickable"})
        with open(EXAMPLE, "rb") as handle:
            cls.client.post("/stage/intake/upload",
                            data={"files": (handle, EXAMPLE.name), "group": "intake_workbook"},
                            content_type="multipart/form-data")
        cls.client.post("/stage/intake/run")
        for _ in range(600):
            if cls.client.get("/stage/intake/progress").get_json()["status"] != "running":
                break
            time.sleep(0.05)
        cls.page = cls.client.get("/stage/intake").get_data(as_text=True)
        cls.intake = read_intake(str(cls.root / "pickable" / EXAMPLE.name))

    def _boundary(self, capability_id, field):
        """The two lists for one capability's one boundary field, as rendered."""
        import re

        row = re.search(
            r'data-row-key="' + capability_id + r'".*?(?=data-row-key="|</ul>\s*<p class="card__empty"|$)',
            self.page, re.S).group(0)
        picker = re.search(r'data-boundary="' + field + r'".*?</details>', row, re.S).group(0)
        long = re.search(r'span__list--long.*', picker, re.S).group(0)
        short = picker[:picker.index("span__list--long")]
        return (re.findall(r'value="(S-\d+)"', short), re.findall(r'value="(S-\d+)"', long))

    def test_the_long_list_holds_every_state_without_exception(self):
        declared = {s.id for s in self.intake.states}
        for capability in self.intake.capabilities:
            for field in ("entry_states", "exit_states"):
                _, long = self._boundary(capability.id, field)
                self.assertEqual(set(long), declared,
                                 f"{capability.id} {field} cannot reach every state")

    def test_a_state_the_shortlist_already_offers_is_hidden_rather_than_left_out(self):
        """Hidden is recoverable and left out is not: the page shows it again the moment the
        shortlist stops offering it."""
        import re

        row = re.search(r'data-row-key="CAP-01".*?</details>', self.page, re.S).group(0)
        picker = re.search(r'data-boundary="entry_states".*?</details>', row, re.S).group(0)
        long = re.search(r'span__list--long.*', picker, re.S).group(0)
        short, _ = self._boundary("CAP-01", "entry_states")
        for state_id in short:
            item = re.search(r'<li([^>]*)>(?:(?!</li>).)*?value="' + state_id + '"', long, re.S)
            self.assertIsNotNone(item, f"{state_id} is missing from the long list entirely")
            self.assertIn("hidden", item.group(1))


class TestTheEntryPickerIsNeverEmpty(unittest.TestCase):
    """An empty picker reads as a disabled one, and that is what "it will not let me set the
    starting state" turned out to mean.

    The two boundary lists are drawn from different halves of the membership: *entered at* from the
    states that offer this capability's decisions, *hands on or ends at* from the states they route
    to. Those halves fail differently. A decision no state offers -- an orphan, which a first draft
    produces routinely -- contributes nothing to the first list and its full share to the second,
    so the entry control came back empty beside an exit control full of choices. The asymmetry read
    as the entry being broken rather than as the graph being incomplete, which is the honest
    reading and the one nobody reached.

    Two fixes, and the second is the one that generalises: whatever is already drawn stays in the
    list (which the exits always did and the entries did not), and where there is still nothing to
    propose it falls back to where the conversation starts. A span has to be entered somewhere, and
    the opening is always a legitimate answer.
    """

    def setUp(self):
        from scenario_generator.core.intake import IntakeData
        self.intake = read_intake(str(EXAMPLE))
        self.assertIsInstance(self.intake, IntakeData)

    def _options(self, intake, capability_id, field):
        from scenario_generator.webapp.graphview import declaration
        _, capabilities, _, _ = declaration(intake)
        row = next(c for c in capabilities if c["id"] == capability_id)
        return [option["id"] for option in row[field]]

    def _orphan(self):
        """The same intake with nothing offering CAP-02's decisions, and no span drawn on it yet.

        Both halves matter: an orphaned decision is what empties the shortlist, and an undrawn
        span is when somebody is looking at that shortlist. Together they are the state a first
        draft arrives in, and the state the entry control could not be used from.
        """
        import dataclasses
        owned = {d.id for d in self.intake.decisions if d.trigger_capability == "CAP-02"}
        states = [dataclasses.replace(
            s, next_decisions=tuple(d for d in s.next_decisions if d not in owned))
            for s in self.intake.states]
        capabilities = [dataclasses.replace(c, entry_states=(), exit_states=())
                        if c.id == "CAP-02" else c for c in self.intake.capabilities]
        return dataclasses.replace(self.intake, states=tuple(states),
                                   capabilities=tuple(capabilities))

    def test_an_orphaned_capability_still_gets_entries_to_pick_from(self):
        orphan = self._orphan()
        from scenario_generator.core.graph import DecisionGraph, entry_candidates
        graph = DecisionGraph(orphan.decisions, orphan.states)
        self.assertEqual(entry_candidates(graph, "CAP-02", orphan.decisions), [],
                         "the case this is about did not arise")
        self.assertTrue(self._options(orphan, "CAP-02", "entry_options"),
                        "the entry picker came back empty, which reads as refusing to be set")

    def test_the_fallback_is_where_the_conversation_starts(self):
        from scenario_generator.core.graph import DecisionGraph
        orphan = self._orphan()
        graph = DecisionGraph(orphan.decisions, orphan.states)
        self.assertEqual(self._options(orphan, "CAP-02", "entry_options"),
                         list(graph.start_states))

    def test_the_page_and_the_server_fall_back_to_the_same_states(self):
        """The shortlist is drawn once by the server and redrawn on the page as decisions are
        ticked. Two fallbacks would rearrange the list under a tick that did not ask it to."""
        from scenario_generator.core.graph import DecisionGraph
        orphan = self._orphan()
        graph = DecisionGraph(orphan.decisions, orphan.states)
        self.assertEqual(graph_index(orphan)["start_states"], list(graph.start_states))

    def test_a_boundary_already_drawn_is_always_in_its_own_shortlist(self):
        """True of the exits from the start and not of the entries, which is half of why the two
        controls felt different to use."""
        import dataclasses
        odd = dataclasses.replace(self.intake, capabilities=tuple(
            dataclasses.replace(c, entry_states=("S-14",), exit_states=("S-14",))
            if c.id == "CAP-01" else c for c in self.intake.capabilities))
        self.assertIn("S-14", self._options(odd, "CAP-01", "entry_options"))
        self.assertIn("S-14", self._options(odd, "CAP-01", "exit_options"))

    def test_the_fallback_stays_out_of_the_graph_itself(self):
        """It is an affordance of a control, not a fact about the agent. Deriving the exits from a
        start state the validator never chose would put a whole graph's endings on a capability
        that holds none of them."""
        from scenario_generator.core.graph import DecisionGraph, entry_candidates
        orphan = self._orphan()
        graph = DecisionGraph(orphan.decisions, orphan.states)
        self.assertEqual(entry_candidates(graph, "CAP-02", orphan.decisions), [])
        self.assertEqual(self._options(orphan, "CAP-02", "derived_exits"), [])


class TestTheRowSaysWhatItsSpanIsNow(unittest.TestCase):
    """Ticking a state has to change something the person is looking at.

    It did not, and that is the defect underneath every report of the boundary "not letting" a
    state be assigned. The tick registered, staged and would have saved -- but the line above the
    picker went on saying "No span", because it was rendered once by the server from the workbook.
    Nothing on screen acknowledged the tick until a save came back, so the reasonable conclusion
    was that the control had refused it.

    Half a span now says which half is missing. "No exit yet" is something to act on; "no span"
    after ticking an entry reads as the entry not having registered.
    """

    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp())
        cls.client = create_app(cls.root).test_client()
        cls.client.post("/workspaces", data={"name": "Reads"})
        with open(EXAMPLE, "rb") as handle:
            cls.client.post("/stage/intake/upload",
                            data={"files": (handle, EXAMPLE.name), "group": "intake_workbook"},
                            content_type="multipart/form-data")
        cls.client.post("/stage/intake/run")
        for _ in range(600):
            if cls.client.get("/stage/intake/progress").get_json()["status"] != "running":
                break
            time.sleep(0.05)
        cls.path = str(cls.root / "reads" / EXAMPLE.name)

    def _reads(self, capability_id):
        import re
        page = self.client.get("/stage/intake").get_data(as_text=True)
        row = re.search(r'data-row-key="' + capability_id + r'".*?</p>', page, re.S).group(0)
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", row)).strip()

    def test_the_summary_is_marked_for_redrawing(self):
        """The page rewrites it as the boundary is ticked, so it has to be findable."""
        page = self.client.get("/stage/intake").get_data(as_text=True)
        self.assertIn("data-span-reads", page)

    def test_an_entry_without_an_exit_says_the_exit_is_missing(self):
        editing.apply_edits(self.path, [
            {"kind": "capability", "key": "CAP-01", "action": "upsert",
             "fields": {"entry_states": ["S-00"], "exit_states": []}}])
        self.assertIn("No exit yet", self._reads("CAP-01"))

    def test_an_exit_without_an_entry_says_the_entry_is_missing(self):
        editing.apply_edits(self.path, [
            {"kind": "capability", "key": "CAP-01", "action": "upsert",
             "fields": {"entry_states": [], "exit_states": ["S-04"]}}])
        self.assertIn("No entry yet", self._reads("CAP-01"))

    def test_neither_half_still_says_no_span(self):
        editing.apply_edits(self.path, [
            {"kind": "capability", "key": "CAP-01", "action": "upsert",
             "fields": {"entry_states": [], "exit_states": []}}])
        self.assertIn("No span", self._reads("CAP-01"))

    def test_both_halves_read_as_a_span(self):
        editing.apply_edits(self.path, [
            {"kind": "capability", "key": "CAP-01", "action": "upsert",
             "fields": {"entry_states": ["S-00"], "exit_states": ["S-04"]}}])
        reads = self._reads("CAP-01")
        self.assertIn("S-00", reads)
        self.assertIn("S-04", reads)
        self.assertIn("Walked as a block", reads)


class TestAGroupOfCheckboxesIsNotOneControl(unittest.TestCase):
    """A <label> wrapped around a list of <label>s is invalid, and browsers are left to decide what
    a click on the outer one means. The outer label pointed at an id nothing owned, so clicking
    anywhere in the field that was not exactly on an inner label went nowhere predictable."""

    @classmethod
    def setUpClass(cls):
        root = Path(tempfile.mkdtemp())
        client = create_app(root).test_client()
        client.post("/workspaces", data={"name": "Labels"})
        with open(EXAMPLE, "rb") as handle:
            client.post("/stage/intake/upload",
                        data={"files": (handle, EXAMPLE.name), "group": "intake_workbook"},
                        content_type="multipart/form-data")
        client.post("/stage/intake/run")
        for _ in range(600):
            if client.get("/stage/intake/progress").get_json()["status"] != "running":
                break
            time.sleep(0.05)
        cls.page = client.get("/stage/intake").get_data(as_text=True)

    def test_no_label_contains_another_label(self):
        import re

        nested = re.findall(r'<label class="efield"[^>]*>(?:(?!</label>).)*?<label',
                            self.page, re.S)
        self.assertEqual(nested, [], "a checkbox group is still wrapped in a label")

    def test_every_label_that_points_at_a_control_points_at_one_that_exists(self):
        import re

        owned = set(re.findall(r'\bid="(f-[^"]+)"', self.page))
        for target in re.findall(r'<label class="efield" for="([^"]+)"', self.page):
            self.assertIn(target, owned, f"a label points at {target}, which nothing owns")


class TestRenamingAnId(unittest.TestCase):
    """The one edit that must cascade, and the reason is worth being precise about.

    A deletion is reported rather than cascaded because the references it leaves behind become
    genuinely undefined -- somebody has to decide what they should say instead. A rename leaves
    nothing undefined: it is the same thing under a new name, every reference still means what it
    meant, and repointing them is bookkeeping rather than judgement. Left undone, a rename
    silently breaks every route through the renamed row.
    """

    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "intake.xlsx"
        shutil.copy(EXAMPLE, self.path)

    def _intake(self):
        return read_intake(str(self.path))

    def test_a_decision_takes_its_references_with_it(self):
        before = self._intake()
        named_by = {s.id for s in before.states
                    if "DEC-03" in s.next_decisions or "DEC-03" in s.reached_via}
        self.assertTrue(named_by, "the fixture no longer exercises this")

        editing.rename(str(self.path), "decision", "DEC-03", "DEC-30")
        after = self._intake()
        self.assertIn("DEC-30", [d.id for d in after.decisions])
        self.assertNotIn("DEC-03", [d.id for d in after.decisions])
        for state in after.states:
            self.assertNotIn("DEC-03", state.next_decisions)
            self.assertNotIn("DEC-03", state.reached_via)
        still = {s.id for s in after.states
                 if "DEC-30" in s.next_decisions or "DEC-30" in s.reached_via}
        self.assertEqual(still, named_by, "a reference was lost rather than repointed")

    def test_a_state_is_followed_through_the_capability_spans(self):
        editing.rename(str(self.path), "state", "S-07", "S-70")
        after = self._intake()
        self.assertIn("S-70", [s.id for s in after.states])
        for capability in after.capabilities:
            self.assertNotIn("S-07", capability.entry_states)
            self.assertNotIn("S-07", capability.exit_states)
        self.assertTrue(any("S-70" in c.entry_states or "S-70" in c.exit_states
                            for c in after.capabilities))

    def test_a_capability_is_followed_through_its_decisions_and_tools(self):
        editing.rename(str(self.path), "capability", "CAP-02", "CAP-20")
        after = self._intake()
        self.assertIn("CAP-20", [c.id for c in after.capabilities])
        self.assertEqual([d.id for d in after.decisions if d.trigger_capability == "CAP-02"], [])
        self.assertTrue([d.id for d in after.decisions if d.trigger_capability == "CAP-20"])
        self.assertTrue([t.name for t in after.tools if t.capability_id == "CAP-20"])

    def test_the_graph_still_walks_afterwards(self):
        """The point of the whole cascade. A rename that breaks the routes has done the opposite
        of what somebody renaming a row wanted."""
        from scenario_generator.pipeline import build_scenario_space

        before = len(build_scenario_space(self._intake(), with_probes=False))
        editing.rename(str(self.path), "decision", "DEC-03", "DEC-30")
        editing.rename(str(self.path), "state", "S-07", "S-70")
        editing.rename(str(self.path), "capability", "CAP-02", "CAP-20")
        self.assertEqual(len(build_scenario_space(self._intake(), with_probes=False)), before)

    def test_renaming_onto_a_taken_id_is_refused(self):
        """Writing it anyway would fold two rows into one without saying so, and the graph would
        come back missing a branch nobody removed."""
        report = editing.rename(str(self.path), "decision", "DEC-01", "DEC-02")
        self.assertTrue(report.refused)
        self.assertEqual(len([d for d in self._intake().decisions if d.id == "DEC-02"]), 1)
        self.assertIn("DEC-01", [d.id for d in self._intake().decisions])

    def test_renaming_to_the_same_id_does_nothing(self):
        report = editing.rename(str(self.path), "decision", "DEC-01", "DEC-01")
        self.assertFalse(report.changed)
        self.assertFalse(report.refused)

    def test_renaming_something_that_is_not_there_is_refused(self):
        report = editing.rename(str(self.path), "decision", "DEC-99", "DEC-98")
        self.assertTrue(report.refused)

    def test_the_outcome_after_the_equals_sign_is_left_alone(self):
        """A rename is not licence to reformat a cell. The outcome, the separators and any retry
        bound somebody wrote around them are what a person typed."""
        editing.apply_edits(str(self.path), [
            {"kind": "state", "key": "S-05", "action": "upsert",
             "fields": {"reached_via": "DEC-03=Verified (attempt<3)"}}])
        editing.rename(str(self.path), "decision", "DEC-03", "DEC-30")
        found = next(s for s in self._intake().states if s.id == "S-05")
        self.assertEqual(found.reached_via, "DEC-30=Verified (attempt<3)")

    def test_it_can_be_previewed_without_being_written(self):
        """A rename is exactly the edit somebody wants to see the consequences of first."""
        client = create_app(Path(tempfile.mkdtemp())).test_client()
        client.post("/workspaces", data={"name": "Renaming"})
        with open(EXAMPLE, "rb") as handle:
            client.post("/stage/intake/upload",
                        data={"files": (handle, EXAMPLE.name), "group": "intake_workbook"},
                        content_type="multipart/form-data")
        client.post("/stage/intake/run")
        for _ in range(600):
            if client.get("/stage/intake/progress").get_json()["status"] != "running":
                break
            time.sleep(0.05)

        body = client.post("/stage/intake/declaration/preview", json={"edits": [
            {"kind": "capability", "key": "CAP-01", "rename": "CAP-99"}]}).get_json()
        self.assertIn("CAP-99", [r["key"] for r in body["declaration"]["rows"]["capability"]])

        after = client.get("/stage/intake/declaration").get_json()
        self.assertIn("CAP-01", [r["key"] for r in after["declaration"]["rows"]["capability"]])
        self.assertNotIn("CAP-99", [r["key"] for r in after["declaration"]["rows"]["capability"]])


class TestEditingTheUseCase(unittest.TestCase):
    """Its sheet is two columns rather than a table of rows, so the field is found by its name."""

    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "intake.xlsx"
        shutil.copy(EXAMPLE, self.path)

    def test_a_field_is_written_and_read_back(self):
        editing.set_use_case(str(self.path), {"Use case name": "Renamed assistant"})
        self.assertEqual(read_intake(str(self.path)).name, "Renamed assistant")

    def test_fields_not_named_are_left_alone(self):
        before = read_intake(str(self.path)).use_case
        editing.set_use_case(str(self.path), {"Use case name": "Renamed"})
        after = read_intake(str(self.path)).use_case
        for field, value in before.items():
            if field != "Use case name":
                self.assertEqual(after.get(field), value)

    def test_a_field_the_sheet_does_not_carry_is_added(self):
        """The reader takes whatever is there, so a declaration that wants to record something
        extra should be able to."""
        editing.set_use_case(str(self.path), {"Regulatory owner": "MRMG"})
        self.assertEqual(read_intake(str(self.path)).use_case.get("Regulatory owner"), "MRMG")

    def test_it_is_one_row_with_no_add_and_no_remove(self):
        rows = editable(read_intake(str(self.path)))["rows"]["use_case"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["key"], "use_case")

    def test_the_panel_offers_it_first(self):
        """It is what every scenario is written against, so it leads rather than trailing the
        five lists of rows."""
        kinds = [k["kind"] for k in editable(read_intake(str(self.path)))["kinds"]]
        self.assertEqual(kinds[0], "use_case")


class TestACapabilityIsDrawnAsAContainer(unittest.TestCase):
    """The picture already says what a box is by its shape -- a stadium ends the interaction, a
    square is a branch point. A block drawn identically to a decision read as one, which is the
    wrong thing to say about a box standing for eleven."""

    def setUp(self):
        from scenario_generator.webapp.graphview import render_blocks_svg
        self.svg = render_blocks_svg(read_intake(str(EXAMPLE)))

    def test_it_has_the_doubled_outline_of_a_composite_state(self):
        self.assertIn("graph__inner", self.svg)

    def test_it_says_how_much_it_is_standing_in_for(self):
        """Without it a capability holding eleven decisions and one holding two are the same
        picture, and the whole reason to collapse is that they are not the same thing."""
        self.assertIn("graph__inside", self.svg)
        self.assertIn("decisions", self.svg)

    def test_the_detailed_drawing_has_neither(self):
        """There are no blocks in it -- every box is a decision or an ending."""
        from scenario_generator.webapp.graphview import render_svg

        detail = render_svg(read_intake(str(EXAMPLE)))
        self.assertNotIn("graph__inner", detail)
        self.assertNotIn("graph__inside", detail)
