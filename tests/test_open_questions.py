"""How many questions reach a person, and which ones.

Every question this pipeline raises is work for someone, and the someone is usually a modelling
team who will take days to answer. A list of fifteen is not fifteen times as useful as a list of
four -- past a certain length it stops being worked through at all, and the questions that
mattered are lost among the ones that did not.

So the reading is put back to the documents more than once, and what survives is divided: the
things only a person can settle, and the things that would not change which scenarios exist. The
tests here pin both halves of that, including the direction it must fail in -- a question nobody
ruled on is asked, never dropped.
"""
import json
import unittest

from scenario_generator.core.evidence import EvidenceRecord, FacetAnswer, summarise
from scenario_generator.ingest import extract_documents, open_questions
from scenario_generator.ingest.extraction import ASK_THE_TEAM, NOT_MATERIAL

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

    def test_only_the_last_sweep_is_told_to_rule_on_what_is_left(self):
        calls = []
        extract_documents([_write()], complete=_stub([], calls=calls), resolve_passes=2)
        sweeps = [c for c in calls if "THE OUTSTANDING QUESTIONS" in c]
        self.assertNotIn("THIS IS THE LAST READING", sweeps[0])
        self.assertIn("THIS IS THE LAST READING", sweeps[1])

    def test_the_sweep_can_be_switched_off(self):
        calls = []
        extract_documents([_write()], complete=_stub([], calls=calls), resolve_passes=0)
        self.assertEqual([c for c in calls if "THE OUTSTANDING QUESTIONS" in c], [])


class TestTriage(unittest.TestCase):
    def _record(self, rulings):
        """`rulings` maps a question to its status, or to (status, blocked intake part)."""
        resolutions = []
        for question, ruling in rulings.items():
            status, blocks = ruling if isinstance(ruling, tuple) else (ruling, "")
            resolutions.append({"question": question, "status": status, "answer": "",
                                "evidence": [], "blocks": blocks})
        return extract_documents([_write()], complete=_stub(resolutions), resolve_passes=1)

    def test_only_what_blocks_the_intake_is_put_to_a_person(self):
        record = self._record({_UNKNOWNS[0]: (ASK_THE_TEAM, "decisions"),
                               _UNKNOWNS[1]: (ASK_THE_TEAM, "states"),
                               _UNKNOWNS[2]: NOT_MATERIAL})
        asked = [q["question"] for q in open_questions(record)]
        self.assertIn(_UNKNOWNS[0], asked)
        self.assertIn(_UNKNOWNS[1], asked)
        self.assertNotIn(_UNKNOWNS[2], asked)

    def test_a_question_that_cannot_name_what_it_blocks_is_not_asked(self):
        """Documentation is always incomplete; wanting to be sure is not a blocked intake."""
        record = self._record({_UNKNOWNS[0]: (ASK_THE_TEAM, ""),
                               _UNKNOWNS[1]: (ASK_THE_TEAM, "who owns it"),
                               _UNKNOWNS[2]: (ASK_THE_TEAM, "decisions")})
        asked = [q["question"] for q in open_questions(record)]
        self.assertEqual([q for q in _UNKNOWNS if q in asked], [_UNKNOWNS[2]])

    def test_the_question_says_what_it_blocks_and_why(self):
        record = self._record({_UNKNOWNS[0]: (ASK_THE_TEAM, "decisions")})
        asked = {q["question"]: q for q in open_questions(record)}
        self.assertEqual(asked[_UNKNOWNS[0]]["blocks"], "decisions")
        self.assertIn("branch cannot be enumerated", asked[_UNKNOWNS[0]]["detail"])

    def test_the_most_blocking_questions_come_first(self):
        record = self._record({_UNKNOWNS[0]: (ASK_THE_TEAM, "tools"),
                               _UNKNOWNS[1]: (ASK_THE_TEAM, "decisions")})
        unknowns = [q for q in open_questions(record) if q["kind"] == "unknown"]
        self.assertEqual(unknowns[0]["question"], _UNKNOWNS[1])

    def test_what_is_set_aside_is_still_recorded(self):
        """Not asked is not the same as thrown away; the context document still says it."""
        record = self._record({_UNKNOWNS[2]: NOT_MATERIAL})
        answer = record.answer_for("decisions")
        self.assertIn(_UNKNOWNS[2], answer.unknowns)
        self.assertNotIn(_UNKNOWNS[2], answer.must_ask)
        self.assertGreaterEqual(summarise(record)["set_aside"], 1)

    def test_a_question_nobody_ruled_on_is_asked(self):
        """The direction this has to fail in: a silent drop is the one unrecoverable outcome."""
        record = self._record({})
        asked = [q["question"] for q in open_questions(record)]
        for unknown in _UNKNOWNS:
            self.assertIn(unknown, asked)

    def test_a_record_from_before_triage_asks_everything(self):
        record = EvidenceRecord(answers=[
            FacetAnswer(facet="decisions", answer="Something", unknowns=["What is the limit?"])])
        self.assertEqual(record.questions_for_people(), [("decisions", "What is the limit?")])

    def test_the_counts_separate_what_is_asked_from_what_is_not(self):
        record = self._record({_UNKNOWNS[0]: (ASK_THE_TEAM, "decisions"),
                               _UNKNOWNS[1]: NOT_MATERIAL, _UNKNOWNS[2]: NOT_MATERIAL})
        counts = summarise(record)
        self.assertEqual(counts["unknowns"], counts["to_ask"] + counts["set_aside"])


if __name__ == "__main__":
    unittest.main()
