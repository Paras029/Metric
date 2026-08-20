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

from metric.phases.intake.intake import diagram_structure as ds
from metric.phases.intake.intake import build_context_document, extract_documents

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

    def _boxes(self, holes=False, first=None):
        """A reading in whichever shape the prompt asks for.

        Three prompts can reach a vision call: the per-image boxes-and-arrows reading, the
        single-image reading that goes straight to the intake's vocabulary, and the repair. The
        repair always answers with the complete graph, since that is what a repair is for; ``first``
        is what the single-image reading returns, which for a test about the repair has to be the
        broken one.
        """
        whole = {
            "capabilities": [{"id": "CAP-01", "name": "Authentication", "type": "Gating"}],
            "decisions": [{"id": "DEC-01", "name": "PIN check",
                           "outcomes": ["Pass", "Fail"], "capability_id": "CAP-01"}],
            "states": [
                {"id": "S-00", "reached_via": "Start", "description": "Call opens",
                 "next_decisions": ["DEC-01"], "is_terminal": False},
                {"id": "S-01", "reached_via": "DEC-01=Pass", "description": "Verified",
                 "is_terminal": True, "outcome_type": "Happy path"},
                {"id": "S-02", "reached_via": "DEC-01=Fail", "description": "Locked",
                 "is_terminal": True, "outcome_type": "Termination"}]}

        def describe(system, user, images, **kwargs):
            if "WHAT DOES NOT JOIN UP" in user:                 # the repair pass
                return json.dumps(whole)
            if "THE VOCABULARY TO WRITE IT IN" in user:         # the single-image reading
                return json.dumps({**(first if first is not None else whole),
                                   "observations": []})
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
                                   describe_images=self._boxes(first=structure),
                                   resolve_passes=0)

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
            return self._boxes(first=with_a_hole)(system, user, images, **kwargs)

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
            return self._boxes(first=whole)(system, user, images, **kwargs)

        extract_documents([_png()], complete=self._complete(whole), describe_images=describe,
                          resolve_passes=0)
        self.assertEqual([u for u in seen if "WHAT DOES NOT JOIN UP" in u], [])

    def test_the_graph_reaches_the_context_document(self):
        structure = {
            "decisions": [{"id": "DEC-01", "name": "PIN check", "outcomes": ["Pass", "Fail"]}],
            "states": [{"id": "S-00", "reached_via": "Start", "description": "Call opens",
                        "next_decisions": ["DEC-01"], "is_terminal": False}]}
        record = extract_documents([_png()], complete=self._complete(structure),
                                   describe_images=self._boxes(first=structure),
                                   resolve_passes=0)
        context = build_context_document(record, "Test agent")

        self.assertIn("as read from the submitted diagrams", context)
        self.assertIn("DEC-01: PIN check", context)


class TestOneImageIsJustExtraction(unittest.TestCase):
    """A single diagram has nothing to be joined to, and is read as though that were true.

    It used to go through the same three passes as a pack of them: read into boxes and arrows,
    then a second call to turn those boxes and arrows into the intake's vocabulary, through a
    prompt whose opening paragraphs are about arrows running off the page into other pictures.
    That is a call spent arriving where the first one could have finished, and a prompt describing
    a situation that does not exist.
    """

    _WHOLE = {
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

    def _watch(self):
        vision, text = [], []

        def describe(system, user, images, **kwargs):
            vision.append(user)
            return json.dumps({**self._WHOLE, "observations": []})

        def complete(system, user, **kwargs):
            text.append(user)
            if "THE OUTSTANDING QUESTIONS" in user:
                return json.dumps({"resolved": []})
            return json.dumps({})
        return vision, text, complete, describe

    def test_the_workflow_comes_off_the_image_in_one_call(self):
        vision, text, complete, describe = self._watch()
        record = extract_documents([_png()], complete=complete, describe_images=describe,
                                   resolve_passes=0)
        self.assertEqual(len(vision), 1, "one image should take one look")
        self.assertEqual([d["id"] for d in record.structure["decisions"]], ["DEC-01"])

    def test_nothing_is_asked_to_join_it_to_anything(self):
        vision, text, complete, describe = self._watch()
        extract_documents([_png()], complete=complete, describe_images=describe, resolve_passes=0)
        self.assertEqual([u for u in text if "THE READINGS" in u], [],
                         "a single image was sent to the pass that puts several together")

    def test_the_prompt_does_not_talk_about_the_other_images(self):
        """It is the only one. Telling it that an arrow off the edge is picked up elsewhere invites
        it to leave a route unresolved that the picture in front of it actually resolves."""
        vision, text, complete, describe = self._watch()
        extract_documents([_png()], complete=complete, describe_images=describe, resolve_passes=0)
        self.assertNotIn("continues_offpage", vision[0])
        self.assertIn("nothing continuing off the page", vision[0])

    def test_it_is_still_checked_against_the_image_where_it_does_not_join_up(self):
        """The shorter path drops the joining pass, not the audit. A single diagram read with a
        hole in it is exactly as unwalkable as several."""
        holed = {"decisions": [{"id": "DEC-01", "name": "PIN check",
                                "outcomes": ["Pass", "Fail"]}],
                 "states": [{"id": "S-00", "reached_via": "Start", "description": "Opens",
                             "next_decisions": ["DEC-01"], "is_terminal": False},
                            {"id": "S-01", "reached_via": "DEC-01=Pass", "description": "Verified",
                             "is_terminal": True, "outcome_type": "Happy path"}]}
        seen = []

        def describe(system, user, images, **kwargs):
            seen.append(user)
            if "WHAT DOES NOT JOIN UP" in user:
                return json.dumps(self._WHOLE)
            return json.dumps({**holed, "observations": []})

        def complete(system, user, **kwargs):
            return json.dumps({"resolved": []} if "THE OUTSTANDING QUESTIONS" in user else {})

        record = extract_documents([_png()], complete=complete, describe_images=describe,
                                   resolve_passes=0)
        self.assertEqual(len([u for u in seen if "WHAT DOES NOT JOIN UP" in u]), 1)
        self.assertEqual(ds.audit(record.structure), [])


class TestSeveralImagesArePutTogetherAsTold(unittest.TestCase):
    """Pieces of one picture and separate drawings of one flow need opposite readings.

    Stitching is right for a flow cut into pieces and wrong for two drawings of one flow, where it
    welds the end of the first onto the start of the second and enumerates routes the agent does
    not have. Reconciling is right for two drawings and wrong for pieces, where it folds the end of
    one picture into the start of the next as the same step under a different label. Which one it
    is is not visible in the result without reading the whole graph back against the pictures.
    """

    def _capture(self):
        seen = {}

        def describe(system, user, images, **kwargs):
            return json.dumps({"nodes": [{"ref": "n1", "label": "PIN check", "kind": "decision"}],
                               "edges": [], "counts": {"boxes": 1, "arrows": 0}})

        def complete(system, user, **kwargs):
            if "THE READINGS" in user:
                seen["prompt"] = user
                return json.dumps({"capabilities": [], "decisions": [], "states": [],
                                   "observations": []})
            return json.dumps({"resolved": []} if "THE OUTSTANDING QUESTIONS" in user else {})
        return seen, complete, describe

    def test_pieces_of_one_picture_are_followed_from_image_to_image(self):
        seen, complete, describe = self._capture()
        extract_documents([_png(), _png("two.png")], complete=complete, describe_images=describe,
                          resolve_passes=0, diagram_mode="split")
        self.assertIn("continues_offpage", seen["prompt"])
        self.assertIn("split across images", seen["prompt"])

    def test_separate_drawings_of_one_flow_are_reconciled_instead(self):
        seen, complete, describe = self._capture()
        extract_documents([_png(), _png("two.png")], complete=complete, describe_images=describe,
                          resolve_passes=0, diagram_mode="same_flow")
        self.assertIn("each show the same workflow", seen["prompt"])
        self.assertIn("not to stitch them end to end", seen["prompt"])

    def test_the_reconciling_pass_is_told_not_to_chain_them(self):
        """The specific failure it exists to prevent, named in the prompt so it is not left to be
        inferred from "reconcile"."""
        seen, complete, describe = self._capture()
        extract_documents([_png(), _png("two.png")], complete=complete, describe_images=describe,
                          resolve_passes=0, diagram_mode="same_flow")
        self.assertIn("one step, not two", seen["prompt"])
        self.assertIn("Do not chain one image onto the end of another", seen["prompt"])

    def test_stitching_is_what_happens_when_nobody_says(self):
        """The commoner case, and the one the reading was built for."""
        seen, complete, describe = self._capture()
        extract_documents([_png(), _png("two.png")], complete=complete, describe_images=describe,
                          resolve_passes=0)
        self.assertIn("split across images", seen["prompt"])

    def test_a_mode_nobody_offered_falls_back_to_stitching(self):
        seen, complete, describe = self._capture()
        extract_documents([_png(), _png("two.png")], complete=complete, describe_images=describe,
                          resolve_passes=0, diagram_mode="nonsense")
        self.assertIn("split across images", seen["prompt"])


class TestTheDrafterIsGivenStructure(unittest.TestCase):
    def test_the_graph_is_put_in_front_of_the_drafter_as_structure(self):
        from metric.phases.intake.intake.drafting import draft_intake

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
        from metric.phases.intake.intake.drafting import draft_intake

        seen = {}

        def complete(system, user, **kwargs):
            seen["user"] = user
            return json.dumps({"use_case": {}, "personas": [], "capabilities": [],
                               "decisions": [], "states": [], "tools": []})

        draft_intake("Some prose context.", complete=complete, structure=None)
        self.assertIn("No workflow diagram was submitted", seen["user"])


if __name__ == "__main__":
    unittest.main()
