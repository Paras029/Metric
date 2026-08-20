"""A pack of pictures is a pack. The declaration has to arrive whether or not prose came with it.

A workflow diagram is read deterministically: box by box, into the intake's own vocabulary,
audited against itself and repaired against its own image. By the time drafting starts, the graph
already exists in exactly the shape the workbook wants.

It then had exactly one way of getting there — a model call asked, in a prompt, to start from it.
When that call underperformed, everything read off the picture was dropped in silence: the intake
came out with no decisions and no states, the run reported success, and the only reading of that
from the outside was that the tool must need a written document. It does not. Any combination of
documents and diagrams is a submission, and a diagram on its own has to produce a declaration.

So the carry-through is enforced rather than requested, and these are the cases that decides.
"""
import base64
import json
import tempfile
import unittest
from pathlib import Path

from metric.domain import read_intake
from metric.phases.intake.intake.drafting import carry_diagram_through
from metric.phases.intake.intake.extraction import extract_documents
from metric.pipeline import draft_intake_workbook

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")

# What a diagram of a small identity-gated agent reads as, after cleaning and repair.
_DRAWN = {
    "capabilities": [{"id": "CAP-01", "name": "Identity", "type": "Gating"}],
    "decisions": [
        {"id": "DEC-01", "name": "Identity check", "capability_id": "CAP-01", "inputs": "SSN",
         "outcomes": ["Pass", "Fail"], "input_source": "User", "max_attempts": 2,
         "outcome_condition": "last four match"},
    ],
    "states": [
        {"id": "S-00", "reached_via": "Start", "description": "The chat opens",
         "next_decisions": ["DEC-01"], "is_terminal": False, "outcome_type": ""},
        {"id": "S-01", "reached_via": "DEC-01=Pass", "description": "Verified and served",
         "next_decisions": [], "is_terminal": True, "outcome_type": "Happy path"},
        {"id": "S-02", "reached_via": "DEC-01=Fail", "description": "Locked out",
         "next_decisions": [], "is_terminal": True, "outcome_type": "Termination"},
    ],
}

_NOTHING = {"use_case": {}, "personas": [], "capabilities": [], "decisions": [], "states": [],
            "tools": [], "confidence": {}, "review_notes": []}


def _draft(**changes):
    data = json.loads(json.dumps(_NOTHING))
    data.update(changes)
    return data


class TestWhatTheDraftDroppedComesBack(unittest.TestCase):
    def test_a_draft_that_declared_nothing_still_yields_the_drawn_graph(self):
        merged = carry_diagram_through(_draft(), _DRAWN)
        self.assertEqual([d["id"] for d in merged["decisions"]], ["DEC-01"])
        self.assertEqual([s["id"] for s in merged["states"]], ["S-00", "S-01", "S-02"])
        self.assertEqual([c["id"] for c in merged["capabilities"]], ["CAP-01"])

    def test_it_says_so_where_a_reviewer_will_see_it(self):
        """A row nobody can tell came from a picture is a row nobody knows to check."""
        note = carry_diagram_through(_draft(), _DRAWN)["review_notes"][0]
        self.assertIn("DEC-01", note["note"])
        self.assertIn("diagram", note["note"].lower())

    def test_a_dropped_outcome_comes_back_even_where_its_decision_survived(self):
        """The quietest version: every row is present and one branch label is missing, so a route
        disappears while the declaration still looks complete."""
        halved = _draft(decisions=[dict(_DRAWN["decisions"][0], outcomes=["Pass"])],
                        states=list(_DRAWN["states"]))
        self.assertEqual(carry_diagram_through(halved, _DRAWN)["decisions"][0]["outcomes"],
                         ["Pass", "Fail"])

    def test_the_drafts_own_version_of_a_row_wins(self):
        """Correcting the picture against the prose is what the drafting call is for."""
        corrected = _draft(
            decisions=list(_DRAWN["decisions"]),
            states=[dict(s, description="Locked out after two attempts") if s["id"] == "S-02"
                    else s for s in _DRAWN["states"]])
        merged = carry_diagram_through(corrected, _DRAWN)
        settled = next(s for s in merged["states"] if s["id"] == "S-02")
        self.assertEqual(settled["description"], "Locked out after two attempts")

    def test_what_the_draft_added_beyond_the_picture_survives(self):
        added = {"id": "S-09", "reached_via": "DEC-01=Fail", "description": "Handed to an agent",
                 "next_decisions": [], "is_terminal": True, "outcome_type": "Escalation"}
        merged = carry_diagram_through(_draft(states=_DRAWN["states"] + [added]), _DRAWN)
        self.assertIn("S-09", [s["id"] for s in merged["states"]])

    def test_a_state_the_draft_folded_into_another_is_not_put_back(self):
        """The one thing this must not do, and the reason it cannot be a plain union.

        A draft that reads two drawn boxes as one position rewrites the survivor's `reached_via`
        to name both arrows. Restoring the folded-away box then leaves two states claiming the
        same arrow, and the walk takes the first — so the restored one is reached by nothing and
        the declaration grows a question about a state somebody merged on purpose. No branch is
        lost by leaving it out; it is already on the survivor.
        """
        folded = _draft(
            decisions=list(_DRAWN["decisions"]),
            states=[_DRAWN["states"][0],
                    {"id": "S-01", "reached_via": "DEC-01=Pass, DEC-01=Fail",
                     "description": "The session ends", "next_decisions": [],
                     "is_terminal": True, "outcome_type": "Happy path"}])
        self.assertEqual([s["id"] for s in carry_diagram_through(folded, _DRAWN)["states"]],
                         ["S-00", "S-01"])

    def test_where_both_claim_the_same_arrow_the_walk_takes_the_drafts_state(self):
        """Two states can claim one arrow without either being droppable, and order settles it.

        A diagram draws two outcomes of one decision landing in the same box; the draft reads
        them as two different endings and declares only the first. The drawn box still has a route
        nobody else claims, so it is carried rather than folded away — and now the draft's state
        and the carried one both say they are reached by that first outcome. The walk takes the
        first it finds, so the carried row going in ahead of the draft's would quietly overrule a
        reading the drafting call was asked to make.
        """
        from metric.domain.graph import DecisionGraph
        from metric.domain.models import Decision, State

        drawn = {
            "capabilities": [],
            "decisions": [{"id": "DEC-02", "name": "Eligible", "capability_id": "",
                           "inputs": "", "outcomes": ["Ok", "No"], "input_source": "User",
                           "max_attempts": 1, "outcome_condition": ""}],
            "states": [{"id": "S-03", "reached_via": "DEC-02=Ok, DEC-02=No",
                        "description": "The session ends", "next_decisions": [],
                        "is_terminal": True, "outcome_type": "Happy path"}],
        }
        split = _draft(decisions=drawn["decisions"],
                       states=[{"id": "S-08", "reached_via": "DEC-02=Ok",
                                "description": "Filed", "next_decisions": [],
                                "is_terminal": True, "outcome_type": "Happy path"}])

        merged = carry_diagram_through(split, drawn)
        self.assertEqual([s["id"] for s in merged["states"]], ["S-08", "S-03"])

        graph = DecisionGraph(
            [Decision(d["id"], d["name"], "", "", list(d["outcomes"]))
             for d in merged["decisions"]],
            [State(s["id"], s["reached_via"], s["description"], list(s["next_decisions"]),
                   s["is_terminal"], s["outcome_type"]) for s in merged["states"]])
        self.assertEqual(graph.successor("DEC-02", "Ok"), "S-08")

    def test_an_unreferenced_capability_is_not_carried(self):
        """A capability row nothing points at is noise in the sheet a reviewer opens first."""
        unused = {"capabilities": _DRAWN["capabilities"] + [
            {"id": "CAP-09", "name": "Never used", "type": "Advisory"}],
            "decisions": _DRAWN["decisions"], "states": _DRAWN["states"]}
        merged = carry_diagram_through(_draft(), unused)
        self.assertEqual([c["id"] for c in merged["capabilities"]], ["CAP-01"])

    def test_no_diagram_leaves_the_draft_exactly_as_it_was(self):
        prose_only = _draft(decisions=list(_DRAWN["decisions"]), states=list(_DRAWN["states"]))
        for structure in (None, {}, {"capabilities": [], "decisions": [], "states": []}):
            self.assertEqual(carry_diagram_through(prose_only, structure), prose_only)

    def test_it_does_not_mutate_what_it_reads(self):
        before = json.dumps(_DRAWN, sort_keys=True)
        carry_diagram_through(_draft(), _DRAWN)
        self.assertEqual(json.dumps(_DRAWN, sort_keys=True), before)


class TestADiagramOnlySubmissionEndToEnd(unittest.TestCase):
    """Through the workbook, because what matters is what a later stage can read off disk."""

    def _run(self, draft_reply):
        directory = Path(tempfile.mkdtemp())
        context = directory / "pack_context.md"
        context.write_text("# Context\n\nOne workflow diagram was submitted and nothing else.",
                           encoding="utf-8")
        (directory / "pack_evidence.json").write_text(
            json.dumps({"documents": [], "claims": [], "answers": [], "structure": _DRAWN}),
            encoding="utf-8")

        replies = list(draft_reply)

        def complete(system, user, **kwargs):
            return json.dumps(replies.pop(0) if len(replies) > 1 else replies[0])

        path = directory / "intake.xlsx"
        draft_intake_workbook(str(context), str(path), complete=complete)
        return read_intake(str(path))

    def test_a_drafting_call_that_returned_nothing_still_produces_a_walkable_intake(self):
        intake = self._run([_NOTHING])
        self.assertEqual([d.id for d in intake.decisions], ["DEC-01"])
        self.assertEqual(sorted(s.id for s in intake.states), ["S-00", "S-01", "S-02"])
        self.assertEqual(sorted(intake.decisions[0].variants), ["Fail", "Pass"])

    def test_and_the_scenario_space_built_off_it_walks_both_branches(self):
        from metric.pipeline import build_scenario_space
        self.assertEqual(len(build_scenario_space(self._run([_NOTHING]), with_probes=False)), 2)


class TestAPackWithNoProseInItAsksTheDocumentsNothing(unittest.TestCase):
    """The questions go to the documents. Where there are none, asking anyway is pure waste.

    Three facet-group calls and then two resolution sweeps, each one putting a long list of
    questions to an empty corpus and being told, at judgement-tier cost, that nothing was found.
    Five calls and the wall-clock that goes with them, on the pack shape that has least to spare.
    """

    def _read(self, files):
        directory = Path(tempfile.mkdtemp())
        made = []
        for name, body in files:
            path = directory / name
            path.write_bytes(body) if isinstance(body, bytes) else path.write_text(
                body, encoding="utf-8")
            made.append(path)

        self.text_calls = 0
        self.bar = []

        def text(system, user, **kwargs):
            self.text_calls += 1
            return json.dumps({"answer": "Something", "points": [], "unknowns": [],
                               "sources": [], "confidence": "Medium"})

        def images(system, user, images=None, **kwargs):
            return json.dumps({**_DRAWN, "observations": [
                {"facet": "decisions", "statement": "It branches on identity.",
                 "quote": "", "locator": "top"}], "unresolved": []})

        return extract_documents(
            made, complete=text, describe_images=images,
            progress=lambda message, done, total: self.bar.append((done, total)))

    def test_a_diagram_only_pack_spends_no_call_reading_prose_that_is_not_there(self):
        record = self._read([("flow.png", _PNG)])
        self.assertEqual(self.text_calls, 0)
        self.assertEqual([d["id"] for d in record.structure["decisions"]], ["DEC-01"])

    def test_and_what_the_diagram_established_still_reaches_the_answers(self):
        """Skipping the prose passes must not skip the reading. The observations are the point."""
        record = self._read([("flow.png", _PNG)])
        decisions = record.answer_for("decisions")
        self.assertIn("It branches on identity.", decisions.points)

    def test_a_pack_with_prose_in_it_is_still_read_the_way_it_always_was(self):
        self._read([("notes.md", "# Agent\n\nIt verifies the cardmember, then files a dispute.\n")])
        self.assertGreaterEqual(self.text_calls, 3)

    def test_the_bar_reaches_its_own_end_on_every_shape_of_pack(self):
        """A bar that stops at a third is read as a run that failed quietly.

        Two estimates were wrong in opposite directions and only showed up together: an image was
        counted as a file to be parsed, when nothing parses an image, and the look-again pass over
        the diagrams was counted as certain when it only happens where the reading left a hole.
        """
        for pack in ([("flow.png", _PNG)],
                     [("notes.md", "# Agent\n\nIt verifies, then files.\n")],
                     [("notes.md", "# Agent\n\nIt verifies, then files.\n"), ("flow.png", _PNG)]):
            with self.subTest(pack=[name for name, _ in pack]):
                self._read(pack)
                done, total = self.bar[-1]
                self.assertEqual(done, total)


if __name__ == "__main__":
    unittest.main()
