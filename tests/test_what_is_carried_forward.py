"""Narrowing a pack is a decision somebody makes, and it has to survive being made.

Three things have to hold, and each of them has been wrong at some point in a tool of this shape.
A scenario set aside must stay on the record, because a deleted scenario is indistinguishable from
one the walk never produced -- and telling those apart is the whole reason the enumeration exists.
It must leave the issued pack, because a scenario in the pack is a scenario somebody is being
asked to run. And the ruling must be visible on the page it was made from, which is the one that
was quietly broken: every stage page reads its own snapshot, and a ruling written only to the live
metadata workbook vanished the moment the page reloaded.
"""
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from scenario_generator.core.intake import read_intake
from scenario_generator.io.workbooks import read_scenarios, write_data_template, write_space_metadata
from scenario_generator.webapp.app import create_app

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "intakes" / \
    "1_disputes_three_blocks.xlsx"
METADATA = "scenario_space_metadata.xlsx"


def _workspace() -> tuple:
    """A workspace with the graph walked, tiers assigned by hand, and the materiality page live."""
    root = Path(tempfile.mkdtemp())
    app = create_app(root)
    client = app.test_client()
    client.post("/workspaces", data={"name": "Disputes"})
    with open(EXAMPLE, "rb") as handle:
        client.post("/stage/intake/upload",
                    data={"files": (handle, EXAMPLE.name), "group": "intake_workbook"},
                    content_type="multipart/form-data")
    for stage in ("intake", "workflow"):
        client.post(f"/stage/{stage}/run")
        time.sleep(0.6)
        for _ in range(600):
            if client.get(f"/stage/{stage}/progress").get_json()["status"] != "running":
                break
            time.sleep(0.05)

    workspace = root / "disputes"
    intake = read_intake(str(workspace / EXAMPLE.name))
    scenarios = read_scenarios(str(workspace / METADATA), intake)
    for index, scenario in enumerate(scenarios):
        scenario.materiality = ("Low", "Medium", "High")[index % 3]
    write_space_metadata(str(workspace / METADATA), intake, scenarios)
    shutil.copyfile(workspace / METADATA, workspace / "space_metadata.materiality.xlsx")

    state = json.loads((workspace / "workspace.json").read_text())
    for key in ("scenarios", "variations", "materiality"):
        state["stages"][key]["status"] = "complete"
        state["stages"][key]["artifacts"] = {"space_metadata": METADATA}
    (workspace / "workspace.json").write_text(json.dumps(state))
    return client, workspace, intake


class TestSettingScenariosAside(unittest.TestCase):
    def setUp(self):
        self.client, self.workspace, self.intake = _workspace()
        self.ids = [s.id for s in read_scenarios(str(self.workspace / METADATA), self.intake)][:4]

    def _held(self, name: str = METADATA):
        return read_scenarios(str(self.workspace / name), self.intake)

    def test_a_ruling_shows_on_the_page_it_was_made_from(self):
        """The page reads its own stage snapshot, so a ruling that reaches only the live workbook
        is invisible the instant the page reloads."""
        page = self.client.post("/stage/materiality/carry-forward",
                                data={"id": self.ids, "excluded": "Low materiality"},
                                follow_redirects=True).get_data(as_text=True)
        self.assertEqual(page.count("mark--aside"), len(self.ids))

    def test_a_scenario_set_aside_stays_on_the_record(self):
        self.client.post("/stage/materiality/carry-forward",
                         data={"id": self.ids, "excluded": "Low materiality"})
        held = self._held()
        self.assertEqual(len(held), len(read_scenarios(
            str(self.workspace / "space_metadata.materiality.xlsx"), self.intake)))
        self.assertEqual(sorted(s.id for s in held if s.is_excluded), sorted(self.ids))
        self.assertEqual({s.excluded for s in held if s.is_excluded}, {"Low materiality"})

    def test_a_scenario_set_aside_leaves_the_issued_pack(self):
        self.client.post("/stage/materiality/carry-forward",
                         data={"id": self.ids, "excluded": "Low materiality"})
        held = self._held()
        pack = self.workspace / "pack.xlsx"
        write_data_template(str(pack), self.intake, held)
        from scenario_generator.io import sheets
        with sheets.open_for_reading(str(pack), "a pack") as workbook:
            issued = {row[0] for row in sheets.read_rows(workbook["Scenarios"]) if row}
        self.assertEqual(issued & set(self.ids), set())
        self.assertEqual(len(issued), len(held) - len(self.ids))

    def test_it_can_be_undone(self):
        self.client.post("/stage/materiality/carry-forward",
                         data={"id": self.ids, "excluded": "Low materiality"})
        self.client.post("/stage/materiality/carry-forward",
                         data={"id": self.ids, "excluded": ""})
        self.assertEqual([s.id for s in self._held() if s.is_excluded], [])


class TestRulingOnOneScenario(unittest.TestCase):
    def setUp(self):
        self.client, self.workspace, self.intake = _workspace()
        self.id = read_scenarios(str(self.workspace / METADATA), self.intake)[0].id

    def _one(self):
        return next(s for s in read_scenarios(str(self.workspace / METADATA), self.intake)
                    if s.id == self.id)

    def test_the_tag_is_editable_rather_than_only_dismissable(self):
        self.client.post(f"/stage/materiality/scenario/{self.id}",
                         data={"materiality": "", "flag": "Redundant", "excluded": ""})
        self.assertEqual(self._one().review_flag, "Redundant")
        self.client.post(f"/stage/materiality/scenario/{self.id}",
                         data={"materiality": "", "flag": "", "excluded": ""})
        self.assertEqual(self._one().review_flag, "")

    def test_materiality_and_scope_are_set_together(self):
        self.client.post(f"/stage/materiality/scenario/{self.id}",
                         data={"materiality": "High", "flag": "", "excluded": "Redundant"})
        ruled = self._one()
        self.assertEqual(ruled.materiality_override, "High")
        self.assertEqual(ruled.excluded, "Redundant")


class TestFilteringByBlock(unittest.TestCase):
    def setUp(self):
        self.client, self.workspace, self.intake = _workspace()

    def test_every_block_with_scenarios_is_offered(self):
        page = self.client.get("/stage/materiality").get_data(as_text=True)
        walked = {s.capability_id for s
                  in read_scenarios(str(self.workspace / METADATA), self.intake)
                  if s.capability_id}
        for capability in walked:
            self.assertIn(f'value="{capability}"', page)

    def test_picking_one_narrows_to_it(self):
        scenarios = read_scenarios(str(self.workspace / METADATA), self.intake)
        block = next(s.capability_id for s in scenarios if s.capability_id)
        page = self.client.get(f"/stage/materiality?block={block}").get_data(as_text=True)
        for scenario in scenarios:
            if scenario.capability_id and scenario.capability_id != block:
                self.assertNotIn(f'id="{scenario.id}"', page)

    def test_an_unknown_block_is_ignored_rather_than_showing_nothing(self):
        """A stale link is a worse failure than a wide view: an empty page reads as a pack that
        lost its scenarios."""
        page = self.client.get("/stage/materiality?block=CAP-99").get_data(as_text=True)
        self.assertIn('class="card', page)


if __name__ == "__main__":
    unittest.main()
