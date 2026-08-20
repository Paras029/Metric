"""The graph at the size it needs, with the scenarios beside it.

A declaration of any size draws to a picture taller than the frame it sits in, and zoom cannot
rescue it -- a graph scaled to fit a paragraph-high frame is unreadable at the scale that fits.
And the premise of the route highlighting is that a scenario *is* a path through the drawing,
which only pays off with both in front of you: on the stage, opening a card lights a route
several screens up.

So both sections are moved into one overlay. Moved, not copied: a second drawing would need its
own zoom, its own hover and its own route lighting, and the two would disagree the first time
either was touched. These tests pin the markup that makes the move possible and reversible,
because the failure mode is a section that goes into the overlay and does not come back.
"""
import re
import tempfile
import time
import unittest
from pathlib import Path

from scenario_generator.webapp.app import create_app

EXAMPLE = (Path(__file__).resolve().parent.parent
           / "examples" / "intakes" / "1_disputes_three_blocks.xlsx")


class TestTheExpandedView(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp())
        client = create_app(cls.root).test_client()
        client.post("/workspaces", data={"name": "Disputes"})
        with open(EXAMPLE, "rb") as handle:
            client.post("/stage/intake/upload",
                        data={"files": (handle, EXAMPLE.name), "group": "intake_workbook"},
                        content_type="multipart/form-data")
        for key in ("intake", "workflow"):
            client.post(f"/stage/{key}/run")
            for _ in range(600):
                if client.get(f"/stage/{key}/progress").get_json()["status"] != "running":
                    break
                time.sleep(0.05)
        cls.client = client
        cls.intake = client.get("/stage/intake").get_data(as_text=True)
        cls.scenarios = client.get("/stage/scenarios").get_data(as_text=True)

    def test_the_intake_stage_expands_with_the_declaration_beside_it(self):
        """Which panel comes along depends on what the stage has. The intake stage carries the
        editor, every stage after it carries the scenario space, and no stage carries both --
        the declaration is editable only where nothing has been built from it yet."""
        self.assertIn("data-expand-open", self.intake)
        self.assertIn("Open with the declaration", self.intake)
        self.assertIn('data-movable="declaration"', self.intake)
        self.assertNotIn('data-movable="space"', self.intake)

    def test_a_stage_with_scenarios_says_the_list_comes_too(self):
        self.assertIn("Open with the scenarios", self.scenarios)

    def test_the_control_is_outside_the_fold(self):
        """The graph section is a <details> that starts folded on most stages, and folded is
        exactly when somebody wants the bigger view. A control inside the fold is unreachable
        without first doing the thing the control exists to avoid."""
        head = re.search(r"<summary class=\"graph__section-head\".*?</summary>",
                         self.scenarios, re.S)
        self.assertIsNotNone(head, "the section header is not where this expects it")
        self.assertIn("data-expand-open", head.group(0))

    def test_a_stage_with_no_side_panel_expands_the_graph_on_its_own(self):
        from scenario_generator.webapp.app import create_app

        bare = create_app(Path(tempfile.mkdtemp())).test_client()
        bare.post("/workspaces", data={"name": "Nothing yet"})
        page = bare.get("/stage/workflow").get_data(as_text=True)
        if "data-expand-open" in page:
            self.assertIn("Open full screen", page)

    def test_both_sections_are_movable_and_both_have_somewhere_to_go_back_to(self):
        """The anchors are the whole of the reversibility. Without them a section put back lands
        at the end of the page and stays there."""
        for name in ("graph", "space"):
            self.assertIn(f'data-movable="{name}"', self.scenarios)
            self.assertIn(f'data-anchor-for="{name}"', self.scenarios)
            self.assertIn(f'data-expand-slot="{name}"', self.scenarios)

    def test_the_overlay_is_empty_until_something_is_moved_into_it(self):
        """Nothing is rendered twice. A page carrying two copies of the scenario list would
        double every id on it, and the second copy would light nothing."""
        overlay = re.search(r'<div class="expand" data-expand.*?</div>\s*</div>',
                            self.scenarios, re.S).group(0)
        self.assertNotIn("data-route=", overlay)
        self.assertNotIn("<svg", overlay)

    def test_it_is_announced_as_a_dialog(self):
        self.assertIn('role="dialog"', self.scenarios)
        self.assertIn('aria-modal="true"', self.scenarios)


class TestTheScriptBehindIt(unittest.TestCase):
    """The behaviour lives in graph.js, which no test here executes. What can be checked is that
    the file still contains the parts the markup is useless without -- a template wired to a
    script that no longer moves anything is a button that does nothing."""

    @classmethod
    def setUpClass(cls):
        cls.script = (Path(__file__).resolve().parent.parent / "scenario_generator" / "webapp"
                      / "static" / "graph.js").read_text(encoding="utf-8")

    def test_it_moves_rather_than_copies(self):
        self.assertIn("appendChild", self.script)
        self.assertNotIn("cloneNode", self.script)

    def test_it_puts_each_section_back_at_its_anchor(self):
        self.assertIn("data-anchor-for", self.script)
        self.assertIn("insertBefore", self.script)

    def test_the_overlay_survives_any_reload_of_the_stage(self):
        """It used to be the editor's business, so a save came back expanded and everything else
        did not -- a filter, a sort, an added row, anything that submits -- and each one took away
        the arrangement the work was being done in. The overlay belongs to this file, so
        remembering it does too, and every reload of every stage gets the same answer without each
        control having to know about it."""
        self.assertIn("sessionStorage", self.script)
        self.assertIn("wasExpanded", self.script)
        self.assertIn("location.pathname", self.script,
                      "the memory is not scoped to the stage it was left on")

    def test_only_one_file_remembers_it(self):
        """Two memories of one thing open it twice, and the second open is a close."""
        editor = (Path(__file__).resolve().parent.parent / "scenario_generator" / "webapp"
                  / "static" / "editor.js").read_text(encoding="utf-8")
        self.assertNotIn("data-expand-open", editor)

    def test_the_editor_still_remembers_its_own_half(self):
        """Which tab was showing and which row was open are the editor's, and only the editor
        knows them."""
        editor = (Path(__file__).resolve().parent.parent / "scenario_generator" / "webapp"
                  / "static" / "editor.js").read_text(encoding="utf-8")
        self.assertIn("sessionStorage", editor)
        for remembered in ("tab", "row"):
            self.assertIn(remembered, editor)

    def test_adding_a_row_writes_it_rather_than_staging_an_intention(self):
        """A staged addition is an intention with nothing on screen to show for it: the row does
        not exist yet, so there is nothing to open and the only evidence is a counter -- which
        reads exactly like the button having done nothing."""
        editor = (Path(__file__).resolve().parent.parent / "scenario_generator" / "webapp"
                  / "static" / "editor.js").read_text(encoding="utf-8")
        adding = editor[editor.index(".erow__new"):]
        self.assertIn("declaration/save", adding[:1200],
                      "adding a row does not write it")

    def test_the_drawing_refits_when_the_frame_changes_size(self):
        """Both when the view is switched and when the overlay opens: the two drawings are
        different sizes and the overlay is a different size again, and a fit computed for the
        frame it is no longer in scrolls over empty space."""
        self.assertGreaterEqual(self.script.count("metric:viewchanged"), 3)

    def test_escape_closes_the_overlay_before_it_clears_the_selection(self):
        """One key press should not both put the window away and drop every open card."""
        self.assertIn("stopPropagation", self.script)

    def test_every_lookup_runs_across_both_drawings(self):
        """The bug this whole area had: bound to the first svg in the canvas, and there are two."""
        self.assertNotIn("canvas.querySelector('svg')", self.script)
        self.assertIn("canvas.querySelectorAll('svg')", self.script)


if __name__ == "__main__":
    unittest.main()
