"""What each stage's page shows, and as of when.

Two rules, and they solve the same complaint from opposite directions. A stage shows only the
columns it has actually produced, because most of these fields carry a default -- every scenario
is born "Medium" -- and a default rendered as a verdict is worse than a blank. And a stage shows
its own reading rather than the live registry, because every stage from the benchmark onward
rewrites one file, so going back would otherwise show what later stages have since made of it.
"""
import json
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from scenario_generator.core.probes import build_probes
from scenario_generator.webapp.app import create_app
from scenario_generator.webapp.scenarios import build_rows

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_stage_runners import _intake_workbook                      # noqa: E402

_IDS = re.compile(r'"id": "([A-Z-]+-\d+)"')


def _ids(user):
    return _IDS.findall(user)


class TestColumnScoping(unittest.TestCase):
    """build_rows on its own, without a workspace behind it."""

    def setUp(self):
        from scenario_generator.core.models import Decision, IntakeData, Persona, State, Tool
        intake = IntakeData(
            use_case={"Use case name": "T", "Business objective": "O"},
            personas=[Persona("P1", "Default", [], True)],
            capabilities=[], decisions=[Decision("DEC-01", "Auth", "", "", ["Pass"])],
            states=[State("S-00", "Start", "Start", ["DEC-01"], False)], tools=[])
        self.scenarios = build_probes(intake)[:4]

    def test_the_text_stage_shows_no_tier(self):
        """Every scenario carries "Medium" from the moment it is built. Showing that on the page
        that writes the text would read as an assessment nothing has made yet."""
        rows = build_rows(self.scenarios, view="all", stage="text")
        self.assertNotIn("materiality", rows["shows"])
        self.assertIn("text", rows["shows"])

    def test_each_stage_adds_to_the_one_before_it(self):
        for stage, expected in (("text", {"text"}),
                                ("materiality", {"text", "materiality"}),
                                ("review", {"text", "materiality", "review"}),
                                ("coverage", {"text", "materiality", "review", "coverage"})):
            self.assertEqual(build_rows(self.scenarios, view="all", stage=stage)["shows"],
                             expected, stage)

    def test_the_tier_views_are_offered_only_once_there_are_tiers(self):
        text = build_rows(self.scenarios, stage="text")
        later = build_rows(self.scenarios, stage="materiality")
        self.assertEqual([key for key, _ in text["views"]], ["all"])
        self.assertIn("high", [key for key, _ in later["views"]])

    def test_a_view_the_stage_cannot_offer_falls_back_rather_than_emptying_the_page(self):
        rows = build_rows(self.scenarios, view="high", stage="text")
        self.assertEqual(rows["view"], "all")
        self.assertEqual(len(rows["rows"]), len(self.scenarios))

    def test_scenarios_keep_their_own_order_until_a_tier_can_rank_them(self):
        rows = build_rows(self.scenarios, view="all", stage="text")["rows"]
        self.assertEqual([r["id"] for r in rows], sorted(r["id"] for r in rows))


class TestSnapshots(unittest.TestCase):
    """Driven through the interface, since the snapshot is written by the stage runner."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.client = create_app(self.root).test_client()
        self.client.post("/workspaces", data={"name": "Snapshots"})

        workbook = _intake_workbook(Path(tempfile.mkdtemp()))
        with open(workbook, "rb") as handle:
            self.client.post("/stage/intake/upload",
                             data={"files": (handle, "intake.xlsx")},
                             content_type="multipart/form-data")

        stubs = {
            "scenario_generator.llm.writer.ask_llm":
                lambda s, u, **k: json.dumps(
                    {i: {"description": f"Text for {i}.", "turn_plan": "1. Do it."}
                     for i in _ids(u)}),
            "scenario_generator.llm.materiality.ask_llm":
                lambda s, u, **k: json.dumps(
                    {i: {"materiality": "Critical", "confidence": "High", "rationale": "costly"}
                     for i in _ids(u)}),
            "scenario_generator.llm.reviewer.ask_llm":
                lambda s, u, **k: (json.dumps({"proposals": []}) if '{"proposals"' in u
                                   else json.dumps(
                                       {i: {"materiality": "Low",
                                            "rationale": "duplicated elsewhere", "flag": ""}
                                        for i in _ids(u)})),
        }
        patches = [mock.patch(target, stub) for target, stub in stubs.items()]
        for patch in patches:
            patch.start()
        try:
            for key in ("intake", "benchmark", "text", "materiality", "review"):
                self.client.post(f"/stage/{key}/run")
                self._settle(key)
        finally:
            for patch in patches:
                patch.stop()

    def _settle(self, key, timeout=20.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.client.get(f"/stage/{key}/progress").get_json()["status"] != "running":
                return
            time.sleep(0.02)
        self.fail(f"{key} was still running after {timeout}s")

    def _page(self, key):
        return self.client.get(f"/stage/{key}?view=all").data.decode()

    def test_every_scenario_stage_keeps_its_own_copy_of_the_registry(self):
        workspace = next(p for p in self.root.iterdir() if (p / "workspace.json").exists())
        kept = sorted(p.name for p in workspace.glob("registry.*.xlsx"))
        self.assertEqual(kept, ["registry.benchmark.xlsx", "registry.materiality.xlsx",
                                "registry.review.xlsx", "registry.text.xlsx"])

    def test_going_back_shows_what_that_stage_assessed_not_what_came_after(self):
        """The review downgraded everything to Low. The materiality page must still show the
        Critical it assigned, or the page is reporting someone else's verdict as its own."""
        materiality = self._page("materiality")
        self.assertIn("mark--tier-critical", materiality)
        self.assertNotIn("mark--tier-low", materiality)

    def test_the_later_stage_shows_its_own_revision(self):
        review = self._page("review")
        self.assertIn("mark--tier-low", review)
        self.assertIn("duplicated elsewhere", review)

    def test_the_writing_stage_shows_text_but_no_tier_and_no_run_count(self):
        text = self._page("text")
        self.assertIn("Text for ", text)
        self.assertNotIn("mark--tier", text)
        self.assertNotIn("requested", text)

    def test_clearing_a_stage_takes_its_snapshot_with_it(self):
        workspace = next(p for p in self.root.iterdir() if (p / "workspace.json").exists())
        self.assertTrue((workspace / "registry.review.xlsx").exists())

        self.client.post("/stage/review/reset", data={"purge": "1"})
        self.assertFalse((workspace / "registry.review.xlsx").exists())
        # ...and the stages before it keep theirs.
        self.assertTrue((workspace / "registry.materiality.xlsx").exists())


if __name__ == "__main__":
    unittest.main()
