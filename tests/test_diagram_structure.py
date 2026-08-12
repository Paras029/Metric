"""Reading a workflow diagram as a graph rather than as prose about one.

A diagram *is* the intake's decision and state sheets, so the reading keeps that structure all
the way through: boxes and arrows per image, joined into one graph, checked against itself, and
handed to the intake drafter as structure rather than as sentences describing structure.

The checks in :func:`audit` are the load-bearing part. Every one of them is a property the graph
needs in order to be walkable at all -- not an opinion about the drawing -- which is what makes
them worth putting back to the model with the images still attached.
"""
import json
import tempfile
import unittest
from pathlib import Path

from scenario_generator.ingest import diagram_structure as ds
from scenario_generator.ingest import build_context_document, extract_documents

_PNG = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
        "IQAAAABJRU5ErkJggg==")


def _png(name="flow.png"):
    import base64
    path = Path(tempfile.mkdtemp()) / name
    path.write_bytes(base64.b64decode(_PNG))
    return path


def _whole():
    """A graph with nothing wrong with it."""
    return ds.clean({
        "capabilities": [{"id": "CAP-01", "name": "Authentication", "type": "Gating"}],
        "decisions": [{"id": "DEC-01", "name": "PIN check", "outcomes": ["Pass", "Fail"],
                       "capability_id": "CAP-01"}],
        "states": [
            {"id": "S-00", "reached_via": "Start", "description": "Call opens",
             "next_decisions": ["DEC-01"], "is_terminal": False},
            {"id": "S-01", "reached_via": "DEC-01=Pass", "description": "Verified",
             "is_terminal": True, "outcome_type": "Happy path"},
            {"id": "S-02", "reached_via": "DEC-01=Fail", "description": "Locked",
             "is_terminal": True, "outcome_type": "Termination"}]})


class TestCleaning(unittest.TestCase):
    def test_an_entry_without_a_usable_id_is_dropped_rather_than_given_one(self):
        """An invented DEC-07 would be indistinguishable from a real one in the workbook a person
        then reviews, which is worse than the entry simply not being there."""
        cleaned = ds.clean({"decisions": [{"name": "No id at all", "outcomes": ["A", "B"]},
                                          {"id": "not-a-decision", "name": "Wrong shape"},
                                          {"id": "DEC-01", "name": "Kept", "outcomes": ["A"]}]})
        self.assertEqual([d["id"] for d in cleaned["decisions"]], ["DEC-01"])

    def test_everything_else_gets_a_defensible_default(self):
        """A decision with an unreadable input source is still a decision."""
        decision = ds.clean({"decisions": [{"id": "DEC-01", "input_source": "telepathy",
                                            "max_attempts": "not a number"}]})["decisions"][0]
        self.assertEqual(decision["input_source"], "User")
        self.assertEqual(decision["max_attempts"], 1)

    def test_an_outcome_type_is_kept_only_where_the_interaction_ends(self):
        states = ds.clean({"states": [
            {"id": "S-01", "is_terminal": True, "outcome_type": "escalation"},
            {"id": "S-02", "is_terminal": False, "outcome_type": "Happy path"}]})["states"]
        self.assertEqual(states[0]["outcome_type"], "Escalation")   # matched despite the case
        self.assertEqual(states[1]["outcome_type"], "")

    def test_a_reply_with_nothing_usable_reads_as_empty(self):
        self.assertTrue(ds.is_empty(ds.clean({})))
        self.assertTrue(ds.is_empty(ds.clean({"decisions": ["not an object"]})))


class TestTheAudit(unittest.TestCase):
    def test_a_whole_graph_reports_nothing(self):
        self.assertEqual(ds.audit(_whole()), [])

    def test_an_outcome_that_leads_nowhere_is_named_with_its_decision(self):
        """The most common real hole: an arrow was recorded leaving a box and its destination
        was not."""
        structure = _whole()
        structure["states"] = [s for s in structure["states"] if s["id"] != "S-02"]
        problems = ds.audit(structure)
        self.assertTrue(any("DEC-01" in p and "Fail" in p for p in problems))

    def test_a_branch_with_one_outcome_is_reported(self):
        structure = _whole()
        structure["decisions"][0]["outcomes"] = ["Pass"]
        self.assertTrue(any("only one outcome" in p for p in ds.audit(structure)))

    def test_a_state_reached_via_a_decision_nobody_read_is_reported(self):
        structure = _whole()
        structure["states"].append({"id": "S-09", "reached_via": "DEC-99=Timeout",
                                    "description": "Orphan", "next_decisions": [],
                                    "is_terminal": True, "outcome_type": "Termination"})
        self.assertTrue(any("DEC-99" in p for p in ds.audit(structure)))

    def test_a_state_reached_via_an_outcome_that_decision_does_not_declare_is_reported(self):
        structure = _whole()
        structure["states"][1]["reached_via"] = "DEC-01=Succeeded"
        self.assertTrue(any("Succeeded" in p for p in ds.audit(structure)))

    def test_a_state_that_neither_ends_nor_continues_is_reported(self):
        structure = _whole()
        structure["states"][1]["is_terminal"] = False
        structure["states"][1]["next_decisions"] = []
        self.assertTrue(any("S-01" in p and "nothing follows" in p for p in ds.audit(structure)))

    def test_a_graph_with_no_start_is_reported(self):
        structure = _whole()
        structure["states"][0]["reached_via"] = "DEC-01=Pass"
        self.assertTrue(any("start" in p.lower() for p in ds.audit(structure)))

    def test_an_empty_structure_says_so_once_rather_than_listing_every_rule(self):
        self.assertEqual(len(ds.audit(ds.empty())), 1)


class TestMerging(unittest.TestCase):
    def test_a_repair_replaces_by_id_and_adds_what_is_new(self):
        structure = _whole()
        repair = {"states": [{"id": "S-02", "reached_via": "DEC-01=Fail",
                              "description": "Locked out and escalated", "is_terminal": True,
                              "outcome_type": "Escalation"},
                             {"id": "S-03", "reached_via": "DEC-01=Pass",
                              "description": "Added", "is_terminal": True,
                              "outcome_type": "Happy path"}]}
        merged = ds.merge(structure, repair)

        by_id = {s["id"]: s for s in merged["states"]}
        self.assertEqual(by_id["S-02"]["outcome_type"], "Escalation")
        self.assertIn("S-03", by_id)
        self.assertIn("S-00", by_id)                       # untouched entries survive
        self.assertEqual(len(merged["decisions"]), 1)      # a part the repair omitted is kept

    def test_a_repair_that_returns_nothing_usable_leaves_the_reading_alone(self):
        """The property that matters most: a failed second look must never be able to make the
        first reading worse."""
        structure = _whole()
        self.assertEqual(ds.merge(structure, {}), structure)
        self.assertEqual(ds.merge(structure, {"decisions": ["nonsense"]}), structure)


class TestReadingThroughIngestion(unittest.TestCase):
    """The three passes driven end to end, with the model stubbed."""

    def _boxes(self, holes=False):
        def describe(system, user, images, **kwargs):
            if "WHAT DOES NOT JOIN UP" in user:              # the repair pass
                return json.dumps({
                    "capabilities": [{"id": "CAP-01", "name": "Authentication", "type": "Gating"}],
                    "decisions": [{"id": "DEC-01", "name": "PIN check",
                                   "outcomes": ["Pass", "Fail"], "capability_id": "CAP-01"}],
                    "states": [
                        {"id": "S-00", "reached_via": "Start", "description": "Call opens",
                         "next_decisions": ["DEC-01"], "is_terminal": False},
                        {"id": "S-01", "reached_via": "DEC-01=Pass", "description": "Verified",
                         "is_terminal": True, "outcome_type": "Happy path"},
                        {"id": "S-02", "reached_via": "DEC-01=Fail", "description": "Locked",
                         "is_terminal": True, "outcome_type": "Termination"}]})
            return json.dumps({
                "nodes": [{"ref": "n1", "label": "PIN check", "kind": "decision"}],
                "edges": [{"from": "n1", "to": "n2", "label": "Pass"}],
                "continues_offpage": [{"from": "n1", "label": "Fail", "side": "bottom"}],
                "unreadable": [], "counts": {"boxes": 2, "arrows": 2}})
        return describe

    def _complete(self, structure):
        def complete(system, user, **kwargs):
            if "THE READINGS" in user:
                return json.dumps({**structure, "observations": []})
            if "THE OUTSTANDING QUESTIONS" in user:
                return json.dumps({"resolved": []})
            return json.dumps({})
        return complete

    def test_the_graph_is_carried_on_the_record(self):
        structure = {
            "capabilities": [{"id": "CAP-01", "name": "Authentication", "type": "Gating"}],
            "decisions": [{"id": "DEC-01", "name": "PIN check", "outcomes": ["Pass", "Fail"],
                           "capability_id": "CAP-01"}],
            "states": [
                {"id": "S-00", "reached_via": "Start", "description": "Call opens",
                 "next_decisions": ["DEC-01"], "is_terminal": False},
                {"id": "S-01", "reached_via": "DEC-01=Pass", "description": "Verified",
                 "is_terminal": True, "outcome_type": "Happy path"},
                {"id": "S-02", "reached_via": "DEC-01=Fail", "description": "Locked",
                 "is_terminal": True, "outcome_type": "Termination"}]}

        record = extract_documents([_png()], complete=self._complete(structure),
                                   describe_images=self._boxes(), resolve_passes=0)

        self.assertEqual([d["id"] for d in record.structure["decisions"]], ["DEC-01"])
        self.assertEqual(len(record.structure["states"]), 3)
        self.assertEqual(ds.audit(record.structure), [])

    def test_a_hole_sends_the_images_back_with_the_hole_named(self):
        """The point of the audit: not "read it again" but "this outcome leads nowhere"."""
        with_a_hole = {
            "decisions": [{"id": "DEC-01", "name": "PIN check", "outcomes": ["Pass", "Fail"]}],
            "states": [
                {"id": "S-00", "reached_via": "Start", "description": "Call opens",
                 "next_decisions": ["DEC-01"], "is_terminal": False},
                {"id": "S-01", "reached_via": "DEC-01=Pass", "description": "Verified",
                 "is_terminal": True, "outcome_type": "Happy path"}]}

        seen = []

        def describe(system, user, images, **kwargs):
            seen.append(user)
            return self._boxes()(system, user, images, **kwargs)

        record = extract_documents([_png()], complete=self._complete(with_a_hole),
                                   describe_images=describe, resolve_passes=0)

        repair = [u for u in seen if "WHAT DOES NOT JOIN UP" in u]
        self.assertEqual(len(repair), 1, "the images should go back exactly once")
        self.assertIn("DEC-01", repair[0])
        self.assertIn("Fail", repair[0])
        # ...and the repair's answer was taken, closing the hole.
        self.assertEqual(ds.audit(record.structure), [])

    def test_a_whole_reading_is_not_sent_back_at_all(self):
        whole = {
            "decisions": [{"id": "DEC-01", "name": "PIN check", "outcomes": ["Pass", "Fail"]}],
            "states": [
                {"id": "S-00", "reached_via": "Start", "description": "Opens",
                 "next_decisions": ["DEC-01"], "is_terminal": False},
                {"id": "S-01", "reached_via": "DEC-01=Pass", "description": "Verified",
                 "is_terminal": True, "outcome_type": "Happy path"},
                {"id": "S-02", "reached_via": "DEC-01=Fail", "description": "Locked",
                 "is_terminal": True, "outcome_type": "Termination"}]}
        seen = []

        def describe(system, user, images, **kwargs):
            seen.append(user)
            return self._boxes()(system, user, images, **kwargs)

        extract_documents([_png()], complete=self._complete(whole), describe_images=describe,
                          resolve_passes=0)
        self.assertEqual([u for u in seen if "WHAT DOES NOT JOIN UP" in u], [])

    def test_the_graph_reaches_the_context_document(self):
        structure = {
            "decisions": [{"id": "DEC-01", "name": "PIN check", "outcomes": ["Pass", "Fail"]}],
            "states": [{"id": "S-00", "reached_via": "Start", "description": "Call opens",
                        "next_decisions": ["DEC-01"], "is_terminal": False}]}
        record = extract_documents([_png()], complete=self._complete(structure),
                                   describe_images=self._boxes(), resolve_passes=0)
        context = build_context_document(record, "Test agent")

        self.assertIn("as read from the submitted diagrams", context)
        self.assertIn("DEC-01: PIN check", context)


class TestAnImageNeedNotBeAFlow(unittest.TestCase):
    """Not everything submitted as documentation is a workflow diagram.

    A screenshot of a screen, a table of decline codes, an architecture picture of systems -- all
    of these arrive in a pack, and the reading used to have exactly one thing it was allowed to
    find in an image. A picture with no boxes and arrows returned nothing, was discarded whole, and
    came back as "the diagram could not be read. Supply a written description" -- so what it did
    say was thrown away along with it.
    """

    def _reading(self, reply, synthesis=None):
        def describe(system, user, images, **kwargs):
            return json.dumps(reply)

        def complete(system, user, **kwargs):
            if "THE READINGS" in user:
                return json.dumps(synthesis or {"capabilities": [], "decisions": [], "states": [],
                                                "observations": []})
            if "THE OUTSTANDING QUESTIONS" in user:
                return json.dumps({"resolved": []})
            return json.dumps({})
        return complete, describe

    def test_a_picture_that_is_not_a_flow_is_still_read(self):
        """It reports what it is and what it establishes, and the pack keeps both."""
        complete, describe = self._reading({
            "depicts": "other", "subject": "A table of decline codes",
            "nodes": [], "edges": [], "continues_offpage": [],
            "observations": [{"statement": "Code 51 means insufficient funds.",
                              "quote": "51 — insufficient funds", "locator": "row 3"}],
            "unreadable": [], "counts": {"boxes": 0, "arrows": 0}})

        record = extract_documents([_png()], complete=complete, describe_images=describe,
                                   resolve_passes=0)
        kinds = {d.name: d.kind for d in record.documents}
        self.assertNotIn("unreadable", kinds.values(),
                         "a picture that is not a flow was reported as unreadable")

    def test_it_does_not_have_to_invent_a_workflow_to_be_kept(self):
        """Forcing a table into boxes and arrows invents a flow the agent does not have, and the
        invented one gets enumerated and issued. An empty structure is the honest answer."""
        complete, describe = self._reading({
            "depicts": "other", "subject": "A screenshot of the servicing app",
            "nodes": [], "edges": [], "observations": [{"statement": "The app shows a balance."}],
            "counts": {"boxes": 0, "arrows": 0}})

        record = extract_documents([_png()], complete=complete, describe_images=describe,
                                   resolve_passes=0)
        self.assertTrue(ds.is_empty(record.structure))

    def test_a_picture_carrying_nothing_at_all_is_still_reported_as_unread(self):
        """The failure case has to survive: an image the model could say nothing about at all is
        a file somebody has to be asked about, and swallowing it would hide that."""
        complete, describe = self._reading({})
        record = extract_documents([_png()], complete=complete, describe_images=describe,
                                   resolve_passes=0)
        self.assertEqual([d.kind for d in record.documents], ["unreadable"])


class TestHowTheImagesRelateIsNotAssumed(unittest.TestCase):
    """Several images are not necessarily one flow.

    The synthesis pass used to be told they were -- "one workflow, split across images because it
    did not fit in a single picture" -- so shown three unrelated pictures it went looking for the
    joins. A wrongly joined graph enumerates routes the agent does not have, and every one of them
    is issued to the model owner as a test of behaviour nobody built.
    """

    def _capture(self):
        seen = {}

        def describe(system, user, images, **kwargs):
            return json.dumps({"depicts": "workflow", "subject": "A flow",
                               "nodes": [{"ref": "n1", "label": "PIN check", "kind": "decision"}],
                               "edges": [], "counts": {"boxes": 1, "arrows": 0}})

        def complete(system, user, **kwargs):
            if "THE READINGS" in user:
                seen["prompt"] = user
                return json.dumps({"capabilities": [], "decisions": [], "states": [],
                                   "observations": []})
            if "THE OUTSTANDING QUESTIONS" in user:
                return json.dumps({"resolved": []})
            return json.dumps({})
        return seen, complete, describe

    def test_with_nothing_said_the_reading_is_asked_to_work_it_out(self):
        seen, complete, describe = self._capture()
        extract_documents([_png(), _png("two.png")], complete=complete,
                          describe_images=describe, resolve_passes=0)
        self.assertIn("Nobody has said", seen["prompt"])

    def test_saying_they_are_separate_tells_it_not_to_join_them(self):
        seen, complete, describe = self._capture()
        extract_documents([_png(), _png("two.png")], complete=complete, describe_images=describe,
                          resolve_passes=0, image_relationship="separate")
        self.assertIn("do not continue one another", seen["prompt"])
        self.assertNotIn("Nobody has said", seen["prompt"])

    def test_saying_they_are_one_flow_tells_it_to_look_for_the_joins(self):
        seen, complete, describe = self._capture()
        extract_documents([_png(), _png("two.png")], complete=complete, describe_images=describe,
                          resolve_passes=0, image_relationship="one_flow")
        self.assertIn("one flow split across several", seen["prompt"])

    def test_a_value_nobody_recognises_falls_back_to_working_it_out(self):
        """Rather than rendering a blank where the steer should be, which reads to the model as an
        instruction that was meant to be there and went missing."""
        seen, complete, describe = self._capture()
        extract_documents([_png(), _png("two.png")], complete=complete, describe_images=describe,
                          resolve_passes=0, image_relationship="nonsense")
        self.assertIn("Nobody has said", seen["prompt"])


class TestTheDrafterIsGivenStructure(unittest.TestCase):
    def test_the_graph_is_put_in_front_of_the_drafter_as_structure(self):
        from scenario_generator.ingest.drafting import draft_intake

        seen = {}

        def complete(system, user, **kwargs):
            seen["user"] = user
            return json.dumps({"use_case": {}, "personas": [], "capabilities": [],
                               "decisions": [], "states": [], "tools": []})

        draft_intake("Some prose context.", complete=complete, structure=_whole())

        self.assertIn("DEC-01: PIN check", seen["user"])
        self.assertIn("S-02: Locked", seen["user"])
        self.assertIn("start from it", seen["user"].lower())

    def test_without_a_diagram_the_drafter_is_told_to_work_from_the_prose(self):
        from scenario_generator.ingest.drafting import draft_intake

        seen = {}

        def complete(system, user, **kwargs):
            seen["user"] = user
            return json.dumps({"use_case": {}, "personas": [], "capabilities": [],
                               "decisions": [], "states": [], "tools": []})

        draft_intake("Some prose context.", complete=complete, structure=None)
        self.assertIn("No workflow diagram was submitted", seen["user"])


if __name__ == "__main__":
    unittest.main()
