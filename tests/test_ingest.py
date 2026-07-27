"""Document ingestion: reading files, checking what was read, and assembling it into answers.

The test that matters most here is `test_scattered_facts_are_assembled_into_one_answer`. The
earlier single-pass design could not do that at all — a fact stated nowhere in any single passage
was structurally inexpressible — and that, rather than any prompt wording, is what made
extraction thin on a real document.

The rest guard the two failure directions around it: material invented (a quote that is not in
the document) and material lost (a real quote rejected for crossing a passage boundary).
"""
import json
import tempfile
import unittest
from pathlib import Path

from scenario_generator.core.evidence import REJECTED, VERIFIED
from scenario_generator.ingest import (IngestionFailed, UnreadableDocument,
                                       build_context_document, chunk, extract_documents,
                                       open_questions, read_document, record_from_json,
                                       record_to_json)

# A document that describes one process across three separate sections, the way real
# documentation does. No single section says what the whole flow is.
_SCATTERED = """# Handling a request

The assistant first confirms who it is speaking to before anything else happens.

# Verification

Where confirmation fails the assistant may try again. After the third unsuccessful attempt the
session is locked.

# Escalation

A locked session is passed to a human agent, who takes over the conversation.
"""

# Long enough to be split into several passages, so that a failure on one can be told apart
# from a failure of the whole run.
_LONG = "\n\n".join(
    f"# Section {n}\n\n" + ("The assistant confirms identity before disclosing anything, and "
                            "the session is locked and no further attempts are accepted after "
                            "the third failure. " * 12)
    for n in range(1, 8))

_DOC = """# Identity verification

The assistant must verify the cardmember's identity before any transaction detail is disclosed.
Three consecutive failed attempts lock the session.

# Out of scope

The assistant does not offer legal advice and must hand any such request to a person.
"""


def _write(name, body):
    path = Path(tempfile.mkdtemp()) / name
    path.write_text(body, encoding="utf-8")
    return path


def _stub(observations=None, answer=None):
    """A completion that answers whichever pass it is being asked for.

    The two passes are told apart by their prompt, which is how the real gateway sees them too.
    """
    observations = observations if observations is not None else []
    answer = answer or {"answer": "", "points": [], "unknowns": [], "sources": [],
                        "confidence": "Low"}

    def complete(system, user, **kwargs):
        if "WHAT TO RETURN FOR EACH OBSERVATION" in user:
            return json.dumps({"observations": observations})
        return json.dumps(answer)
    return complete


class TestReaders(unittest.TestCase):
    def test_markdown_headings_become_locators(self):
        reference, segments = read_document(_write("notes.md", _DOC))
        self.assertEqual(reference.units, 2)
        self.assertIn("Identity verification", segments[0].locator)

    def test_an_empty_file_is_refused_with_a_reason(self):
        with self.assertRaises(UnreadableDocument):
            read_document(_write("empty.md", "   "))

    def test_an_unsupported_format_is_refused_by_name(self):
        with self.assertRaises(UnreadableDocument) as caught:
            read_document(_write("thing.zip", "x"))
        self.assertIn("not supported", str(caught.exception))

    def test_chunking_never_splits_a_segment(self):
        _, segments = read_document(_write("notes.md", _DOC))
        for text, _ in chunk(segments, budget=10, overlap=0):
            self.assertIn("[", text)                 # the locator travels with the text

    def test_consecutive_chunks_overlap_so_nothing_falls_down_a_boundary(self):
        _, segments = read_document(_write("notes.md", _SCATTERED))
        spans = [span for _, span in chunk(segments, budget=10, overlap=1)]
        self.assertGreater(len(spans), 1)
        # Every chunk after the first carries the previous chunk's last section with it.
        self.assertTrue(any("–" in span for span in spans[1:]))


class TestSurvey(unittest.TestCase):
    def test_a_supported_observation_survives(self):
        record = extract_documents([_write("notes.md", _DOC)], synthesise=False, complete=_stub([{
            "facet": "policy_constraints",
            "statement": "Identity is verified before any transaction detail is disclosed.",
            "quote": "The assistant must verify the cardmember's identity before any "
                     "transaction detail is disclosed.",
            "locator": "under “Identity verification”"}]))
        kept = record.usable()
        self.assertTrue(kept)
        self.assertEqual(kept[0].status, VERIFIED)
        self.assertEqual(kept[0].source.document, "notes.md")

    def test_every_observation_gets_an_id_so_an_answer_can_cite_it(self):
        record = extract_documents([_write("notes.md", _DOC)], synthesise=False, complete=_stub([{
            "facet": "policy_constraints", "statement": "x",
            "quote": "Three consecutive failed attempts lock the session", "locator": "y"}]))
        self.assertTrue(all(claim.id for claim in record.claims))
        self.assertIn(record.claims[0].id, record.claims_by_id())

    def test_an_invented_quote_is_discarded(self):
        record = extract_documents([_write("notes.md", _DOC)], synthesise=False, complete=_stub([{
            "facet": "policy_constraints",
            "statement": "Disputes may be raised within ninety days.",
            "quote": "Cardmembers may raise a dispute within ninety days of the posting date "
                     "provided the merchant has been contacted first.",
            "locator": "under “Identity verification”"}]))
        self.assertEqual(record.usable(), [])
        self.assertEqual(record.rejected()[0].status, REJECTED)

    def test_a_quote_is_checked_against_the_whole_document_not_one_passage(self):
        """Passages overlap and a quote can straddle a boundary; that is not a fabrication."""
        record = extract_documents([_write("notes.md", _DOC)], synthesise=False, complete=_stub([{
            # This spans two sections, so it is in no single passage.
            "facet": "scope_boundaries",
            "statement": "Legal advice is out of scope.",
            "quote": "Three consecutive failed attempts lock the session. Out of scope The "
                     "assistant does not offer legal advice",
            "locator": "under “Out of scope”"}]))
        self.assertEqual(record.usable()[0].status, VERIFIED)

    def test_a_facet_outside_the_vocabulary_is_ignored(self):
        record = extract_documents([_write("notes.md", _DOC)], synthesise=False, complete=_stub([{
            "facet": "invented_facet", "statement": "x",
            "quote": "The assistant must verify the cardmember's identity", "locator": "x"}]))
        self.assertEqual(record.claims, [])

    def test_an_unreadable_document_is_recorded_and_the_run_continues(self):
        record = extract_documents([_write("empty.md", "  "), _write("notes.md", _DOC)],
                                   synthesise=False, complete=_stub([]))
        kinds = {d.name: d.kind for d in record.documents}
        self.assertEqual(kinds["empty.md"], "unreadable")
        self.assertEqual(kinds["notes.md"], "notes")

    def test_one_failed_passage_does_not_lose_the_rest_of_the_run(self):
        """A gateway hiccup on one passage costs that passage, not the document."""
        attempts = {"n": 0}

        def flaky(system, user, **kwargs):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("gateway hiccup")
            return json.dumps({"observations": [{
                "facet": "policy_constraints", "statement": "Sessions lock after three failures.",
                "quote": "the session is locked and no further attempts are accepted",
                "locator": "x"}]})

        record = extract_documents([_write("long.md", _LONG)], synthesise=False, complete=flaky)
        self.assertGreater(attempts["n"], 1, "the document should span several passages")
        self.assertTrue(record.usable(), "later passages should still have contributed")

    def test_a_run_that_mostly_failed_is_abandoned_rather_than_written(self):
        """A thin context file is indistinguishable from a document that said little, so a run
        that mostly did not happen must not leave one behind."""
        def broken(system, user, **kwargs):
            raise RuntimeError("401 Unauthorized")

        with self.assertRaises(IngestionFailed) as caught:
            extract_documents([_write("notes.md", _DOC)], synthesise=False, complete=broken)
        self.assertIn("failed", str(caught.exception))


class TestDiagrams(unittest.TestCase):
    """A diagram is often the clearest statement of the agent's branching, so it is read rather
    than refused — but nothing read from one can be checked against text."""

    def _png(self):
        # A 1x1 PNG is enough: the reader is stubbed, only the plumbing is under test.
        import base64
        path = Path(tempfile.mkdtemp()) / "flow.png"
        path.write_bytes(base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="))
        return path

    def test_a_diagram_is_read_and_its_observations_marked_for_confirmation(self):
        def describe(system, user, images, **kwargs):
            assert images and images[0][0] == "image/png"
            return json.dumps({"observations": [{
                "facet": "decisions", "statement": "Auth check branches to pass or fail.",
                "quote": "Auth check", "locator": "top left"}]})

        record = extract_documents([self._png()], synthesise=False,
                                   complete=_stub([]), describe_images=describe)
        self.assertEqual(record.documents[0].kind, "diagram")
        claim = record.usable()[0]
        self.assertTrue(claim.needs_confirmation)
        self.assertEqual(claim.source.document, "flow.png")

    def test_an_unreadable_diagram_asks_for_a_description_and_the_pack_still_reads(self):
        """Vision being unavailable costs the diagram, not the documents beside it."""
        def broken(system, user, images, **kwargs):
            raise RuntimeError("vision not enabled")

        record = extract_documents(
            [_write("notes.md", _LONG), self._png()], synthesise=False,
            complete=_stub([{"facet": "policy_constraints",
                             "statement": "Sessions lock after three failures.",
                             "quote": "the session is locked and no further attempts are accepted",
                             "locator": "x"}]),
            describe_images=broken)

        diagram = next(d for d in record.documents if d.name == "flow.png")
        self.assertEqual(diagram.kind, "unreadable")
        self.assertIn("written description", diagram.note)
        self.assertTrue(record.usable(), "the text document should still have been read")


class TestSynthesis(unittest.TestCase):
    """The pass that exists because documentation does not answer a question in one place."""

    def test_scattered_facts_are_assembled_into_one_answer(self):
        observations = [
            {"facet": "decisions",
             "statement": "The assistant confirms identity before anything else.",
             "quote": "The assistant first confirms who it is speaking to",
             "locator": "under “Handling a request”"},
            {"facet": "decisions",
             "statement": "Confirmation may be retried, and locks after the third failure.",
             "quote": "After the third unsuccessful attempt the session is locked",
             "locator": "under “Verification”"},
            {"facet": "decisions",
             "statement": "A locked session goes to a human agent.",
             "quote": "A locked session is passed to a human agent",
             "locator": "under “Escalation”"},
        ]
        assembled = {
            "answer": "The agent verifies identity, retries up to three times, then locks the "
                      "session and hands it to a person.",
            "points": ["Identity is confirmed first.",
                       "Up to three attempts are allowed.",
                       "A locked session is escalated to a human agent."],
            "unknowns": ["What identifiers are accepted for confirmation?"],
            "sources": ["O-001", "O-002", "O-003"],
            "confidence": "High",
        }
        record = extract_documents([_write("notes.md", _SCATTERED)],
                                   complete=_stub(observations, assembled))

        answer = record.answer_for("decisions")
        self.assertIsNotNone(answer)
        self.assertTrue(answer.is_answered)
        # The account states something no single passage of the document says.
        self.assertIn("three times", answer.answer)
        self.assertEqual(len(answer.points), 3)
        self.assertEqual(answer.confidence, "High")

    def test_an_answer_cannot_cite_an_observation_that_does_not_exist(self):
        record = extract_documents([_write("notes.md", _DOC)], complete=_stub(
            [{"facet": "decisions", "statement": "x",
              "quote": "Three consecutive failed attempts lock the session", "locator": "y"}],
            {"answer": "a", "points": [], "unknowns": [], "sources": ["O-001", "O-999"],
             "confidence": "High"}))
        self.assertEqual(record.answer_for("decisions").sources, ["O-001"])

    def test_a_question_with_no_observations_reports_itself_as_unanswered(self):
        record = extract_documents([_write("notes.md", _DOC)], complete=_stub([]))
        self.assertIn("decisions", record.empty_facets())
        self.assertTrue(record.answer_for("decisions").unknowns)

    def test_a_failed_synthesis_falls_back_to_the_raw_observations(self):
        """Losing the assembly is bad; losing the material as well would be worse."""
        def survey_only(system, user, **kwargs):
            if "WHAT TO RETURN FOR EACH OBSERVATION" in user:
                return json.dumps({"observations": [{
                    "facet": "decisions", "statement": "Identity is confirmed first.",
                    "quote": "The assistant first confirms who it is speaking to",
                    "locator": "x"}]})
            raise RuntimeError("gateway down")

        record = extract_documents([_write("notes.md", _SCATTERED)], complete=survey_only)
        answer = record.answer_for("decisions")
        self.assertTrue(answer.points)
        self.assertIn("unassembled", " ".join(answer.unknowns))


class TestContextDocument(unittest.TestCase):
    def _record(self):
        return extract_documents([_write("notes.md", _SCATTERED)], complete=_stub(
            [{"facet": "decisions", "statement": "Identity is confirmed first.",
              "quote": "The assistant first confirms who it is speaking to", "locator": "x"}],
            {"answer": "The agent confirms identity, then retries, then escalates.",
             "points": ["Identity is confirmed first."],
             "unknowns": ["What identifiers are accepted?"],
             "sources": ["O-001"], "confidence": "Medium"}))

    def test_the_assembled_answer_is_what_the_document_leads_with(self):
        document = build_context_document(self._record(), "Disputes assistant")
        self.assertIn("The agent confirms identity, then retries, then escalates.", document)
        self.assertIn("Where it branches", document)

    def test_supporting_observations_are_traceable_to_a_page(self):
        document = build_context_document(self._record())
        self.assertIn("O-001", document)
        self.assertIn("notes.md", document)

    def test_what_an_answer_could_not_settle_is_stated_rather_than_omitted(self):
        document = build_context_document(self._record())
        self.assertIn("Not settled by the documents", document)
        self.assertIn("What identifiers are accepted?", document)

    def test_unanswered_questions_are_listed_separately(self):
        self.assertIn("Not covered by the submitted documents",
                      build_context_document(self._record()))

    def test_specific_unknowns_become_questions_alongside_whole_gaps(self):
        questions = open_questions(self._record())
        kinds = {q["kind"] for q in questions}
        self.assertIn("gap", kinds)
        self.assertIn("unknown", kinds)
        self.assertTrue(any("identifiers" in q["question"] for q in questions))

    def test_the_record_round_trips_through_json(self):
        record = self._record()
        target = Path(tempfile.mkdtemp()) / "evidence.json"
        record_to_json(record, target)
        restored = record_from_json(target)
        self.assertEqual(len(restored.usable()), len(record.usable()))
        self.assertEqual(restored.answer_for("decisions").answer,
                         record.answer_for("decisions").answer)


if __name__ == "__main__":
    unittest.main()
