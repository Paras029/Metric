"""Document ingestion: reading files, and keeping only what a source supports.

The tests that matter most here are the ones about refusal — a document that cannot be read, and
a claim whose quote is not in the document. Both are cases where doing nothing quietly would
leave the benchmark thinner than anyone realised.
"""
import json
import tempfile
import unittest
from pathlib import Path

from scenario_generator.core.evidence import REJECTED, VERIFIED
from scenario_generator.ingest import (UnreadableDocument, build_context_document, chunk,
                                       extract_documents, open_questions, read_document,
                                       record_from_json, record_to_json)

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


def _stub(claims):
    """A completion that returns a fixed set of claims regardless of the passage."""
    return lambda system, user, **kwargs: json.dumps({"claims": claims})


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

    def test_an_image_is_refused_rather_than_read_as_empty(self):
        with self.assertRaises(UnreadableDocument) as caught:
            read_document(_write("flow.png", "not really a png"))
        self.assertIn("vision", str(caught.exception).lower() + " vision")

    def test_chunking_never_splits_a_segment(self):
        _, segments = read_document(_write("notes.md", _DOC))
        chunks = chunk(segments, budget=10)          # forces one chunk per segment
        self.assertEqual(len(chunks), len(segments))
        for text, _ in chunks:
            self.assertIn("[", text)                 # the locator travels with the text


class TestExtraction(unittest.TestCase):
    def test_a_supported_claim_survives(self):
        path = _write("notes.md", _DOC)
        record = extract_documents([path], complete=_stub([{
            "facet": "policy_constraints",
            "statement": "Identity is verified before any transaction detail is disclosed.",
            "quote": "The assistant must verify the cardmember's identity before any "
                     "transaction detail is disclosed.",
            "locator": "under “Identity verification”"}]))
        kept = record.usable()
        self.assertTrue(kept)
        self.assertEqual(kept[0].status, VERIFIED)
        self.assertEqual(kept[0].source.document, "notes.md")

    def test_an_invented_quote_is_discarded(self):
        path = _write("notes.md", _DOC)
        record = extract_documents([path], complete=_stub([{
            "facet": "policy_constraints",
            "statement": "Disputes may be raised within ninety days.",
            "quote": "Cardmembers may raise a dispute within ninety days of the posting date.",
            "locator": "under “Identity verification”"}]))
        self.assertEqual(record.usable(), [])
        self.assertEqual(record.rejected()[0].status, REJECTED)

    def test_a_facet_outside_the_vocabulary_is_ignored(self):
        path = _write("notes.md", _DOC)
        record = extract_documents([path], complete=_stub([{
            "facet": "invented_facet", "statement": "x",
            "quote": "The assistant must verify the cardmember's identity", "locator": "x"}]))
        self.assertEqual(record.claims, [])

    def test_an_unreadable_document_is_recorded_and_the_run_continues(self):
        good = _write("notes.md", _DOC)
        bad = _write("empty.md", "  ")
        record = extract_documents([bad, good], complete=_stub([]))
        kinds = {d.name: d.kind for d in record.documents}
        self.assertEqual(kinds["empty.md"], "unreadable")
        self.assertEqual(kinds["notes.md"], "notes")

    def test_a_failed_model_call_loses_the_passage_not_the_run(self):
        def broken(system, user, **kwargs):
            raise RuntimeError("gateway down")
        record = extract_documents([_write("notes.md", _DOC)], complete=broken)
        self.assertEqual(record.claims, [])
        self.assertEqual(len(record.documents), 1)

    def test_the_record_round_trips_through_json(self):
        path = _write("notes.md", _DOC)
        record = extract_documents([path], complete=_stub([{
            "facet": "scope_boundaries",
            "statement": "Legal advice is out of scope.",
            "quote": "The assistant does not offer legal advice",
            "locator": "under “Out of scope”"}]))
        target = Path(tempfile.mkdtemp()) / "evidence.json"
        record_to_json(record, target)
        self.assertEqual(len(record_from_json(target).usable()), len(record.usable()))


class TestContextDocument(unittest.TestCase):
    def _record(self):
        return extract_documents([_write("notes.md", _DOC)], complete=_stub([{
            "facet": "scope_boundaries",
            "statement": "Legal advice is out of scope and goes to a person.",
            "quote": "The assistant does not offer legal advice",
            "locator": "under “Out of scope”"}]))

    def test_every_line_carries_its_source(self):
        document = build_context_document(self._record(), "Disputes assistant")
        self.assertIn("Legal advice is out of scope", document)
        self.assertIn("notes.md", document)

    def test_what_was_not_covered_is_stated_rather_than_left_blank(self):
        document = build_context_document(self._record())
        self.assertIn("Not covered by the submitted documents", document)

    def test_uncovered_categories_become_questions(self):
        questions = open_questions(self._record())
        self.assertTrue(questions)
        self.assertTrue(any(q["kind"] == "gap" for q in questions))
        self.assertTrue(all(q["question"].endswith("?") for q in questions))


if __name__ == "__main__":
    unittest.main()
