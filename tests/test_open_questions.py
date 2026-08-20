"""What the documents could not settle, and what becomes of it.

The reading is put back to the documents more than once, because a question asked directly is
often answered by material a general reading had no reason to connect, and every question that
survives to the model owner costs days. What the sweeps still cannot settle is carried forward
whole rather than filtered.

Deciding which leftovers matter is deliberately not a model's job. The intake stage reads what
is structurally missing off the declaration itself (see core.gaps), which is both more direct and
less fallible than asking a model to guess whether a leftover "blocks" anything. What these tests
pin is the direction that must hold either way: nothing the documents left open is dropped on the
way through.
"""
import json
import unittest

from metric.phases.intake.intake.evidence import EvidenceRecord, FacetAnswer, summarise
from metric.phases.intake.intake import extract_documents, open_questions

_DOCUMENT = """# Identity

The assistant confirms who it is speaking to. After the third unsuccessful attempt the session is
locked and passed to a human agent.
"""

_UNKNOWNS = ["Which identifiers are accepted for confirmation?",
             "What is the lockout period?",
             "Who owns the identity service?"]


def _reading(unknowns):
    return {"answer": "The agent confirms identity, then locks after three attempts.",
            "points": ["Identity is confirmed first."],
            "unknowns": list(unknowns),
            "evidence": [{"quote": "the session is locked", "document": "notes.md",
                          "locator": "under “Identity”"}],
            "confidence": "High"}


def _stub(resolutions, calls=None, facets=("decisions",)):
    """A completion that reads, then rules on what it could not answer."""
    def complete(system, user, **kwargs):
        if calls is not None:
            calls.append(user)
        if "THE OUTSTANDING QUESTIONS" in user:
            return json.dumps({"resolved": [r for r in resolutions
                                            if r["question"] in user]})
        return json.dumps({facet: _reading(_UNKNOWNS) for facet in facets
                           if f"- {facet}:" in user})
    return complete


def _write(tmp_path=None):
    import tempfile
    from pathlib import Path
    path = Path(tempfile.mkdtemp()) / "notes.md"
    path.write_text(_DOCUMENT, encoding="utf-8")
    return path


class TestTheResolutionLoop(unittest.TestCase):
    def test_the_documents_are_asked_more_than_once(self):
        calls = []
        extract_documents([_write()], complete=_stub([], calls=calls), resolve_passes=2)
        sweeps = [c for c in calls if "THE OUTSTANDING QUESTIONS" in c]
        self.assertEqual(len(sweeps), 2)

    def test_a_later_sweep_only_carries_what_is_still_open(self):
        """The second pass must not re-ask what the first one settled."""
        calls = []
        settled = [{"question": _UNKNOWNS[0], "status": "answered",
                    "answer": "A card number and a date of birth.", "evidence": []}]
        extract_documents([_write()], complete=_stub(settled, calls=calls), resolve_passes=2)

        sweeps = [c for c in calls if "THE OUTSTANDING QUESTIONS" in c]
        opening = sweeps[1].split("THE DOCUMENTS")[0]
        self.assertNotIn(_UNKNOWNS[0], opening)
        self.assertIn(_UNKNOWNS[1], opening)

    def test_an_answer_found_in_the_documents_is_never_asked_of_a_person(self):
        settled = [{"question": _UNKNOWNS[0], "status": "answered",
                    "answer": "A card number and a date of birth.", "evidence": []}]
        record = extract_documents([_write()], complete=_stub(settled), resolve_passes=2)

        asked = [q["question"] for q in open_questions(record)]
        self.assertNotIn(_UNKNOWNS[0], asked)
        answer = record.answer_for("decisions")
        self.assertIn("A card number and a date of birth.", answer.points)

    def test_the_sweep_can_be_switched_off(self):
        calls = []
        extract_documents([_write()], complete=_stub([], calls=calls), resolve_passes=0)
        self.assertEqual([c for c in calls if "THE OUTSTANDING QUESTIONS" in c], [])


class TestWhatSurvivesTheSweeps(unittest.TestCase):
    def _record(self, resolved):
        """`resolved` maps a question to the status the sweep returned for it."""
        resolutions = [{"question": question, "status": status, "answer": "", "evidence": []}
                       for question, status in resolved.items()]
        return extract_documents([_write()], complete=_stub(resolutions), resolve_passes=1)

    def test_everything_the_documents_left_open_reaches_a_person(self):
        """The direction this has to fail in: a silent drop is the one unrecoverable outcome."""
        record = self._record({})
        asked = [q["question"] for q in open_questions(record)]
        for unknown in _UNKNOWNS:
            self.assertIn(unknown, asked)

    def test_a_question_the_sweep_answered_is_not_asked_again(self):
        record = extract_documents([_write()], complete=_stub([
            {"question": _UNKNOWNS[0], "status": "answered",
             "answer": "A card number or the last four of an SSN.", "evidence": []}]),
            resolve_passes=1)
        asked = [q["question"] for q in open_questions(record)]
        self.assertNotIn(_UNKNOWNS[0], asked)
        self.assertIn(_UNKNOWNS[1], asked)

    def test_an_answered_question_becomes_part_of_the_answer(self):
        record = extract_documents([_write()], complete=_stub([
            {"question": _UNKNOWNS[1], "status": "answered",
             "answer": "Thirty minutes.", "evidence": []}]), resolve_passes=1)
        answer = record.answer_for("decisions")
        self.assertIn("Thirty minutes.", answer.points)
        self.assertNotIn(_UNKNOWNS[1], answer.unknowns)

    def test_the_facets_that_block_most_are_read_first(self):
        """Only an ordering -- a branch with unnamed outcomes cannot be enumerated at all."""
        record = EvidenceRecord(answers=[
            FacetAnswer(facet="tools", answer="Something", unknowns=["Which service?"]),
            FacetAnswer(facet="decisions", answer="Something", unknowns=["What is the limit?"])])
        unknowns = [q for q in open_questions(record) if q["kind"] == "unknown"]
        self.assertEqual(unknowns[0]["facet"], "decisions")

    def test_the_counts_add_up(self):
        record = self._record({})
        counts = summarise(record)
        self.assertEqual(counts["to_ask"], counts["unknowns"])


if __name__ == "__main__":
    unittest.main()
