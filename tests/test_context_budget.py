"""How much of the reading actually reaches a model call, and what happens when it will not fit.

The context document is the whole of what a later stage knows about the agent. Anything cut out of
it on the way to a call is a fact the scenario space was built without, and cutting at a character count
is the worst way to do it: it lands mid-sentence, it drops whichever answers happened to be last,
and it is invisible -- a thin reading and a truncated one look identical from the outside.

Three properties.

*What a model is sent is not the same rendering as what a person reads.* The document carries the
quote, source and page behind every claim, which is what an auditor needs and what a model cannot
check. Sending that provenance spends the budget on the one part of the reading that cannot inform
anything.

*A note is never dropped.* Notes are the smallest part of the context and the part somebody typed
deliberately. A budget that spent itself on documents and then cut the correction just given would
be exactly backwards.

*Going over the budget drops whole sections and says so.* A reader can act on that.
"""
import logging
import tempfile
import unittest
from pathlib import Path

from scenario_generator.core.context import MAX_CHARS, load_context
from scenario_generator.core.evidence import (FACETS, Claim, DocumentRef, EvidenceRecord,
                                              FacetAnswer, SourceRef)
from scenario_generator.ingest import build_context_document, build_model_context


def _record(claims_per_facet: int = 6) -> EvidenceRecord:
    """A reading of the kind a real pack produces: every question answered, every answer cited."""
    claims, answers, index = [], [], 0
    for facet in FACETS:
        sources = []
        for _ in range(claims_per_facet):
            index += 1
            claim = Claim(id=f"C{index:03d}", facet=facet,
                          statement=f"Statement {index} about {facet}.",
                          quote=("A verbatim span from the source document, long enough to be "
                                 f"worth checking, number {index}. " * 3),
                          source=SourceRef("model_document.pdf", f"page {index}"))
            claims.append(claim)
            sources.append(claim.id)
        answers.append(FacetAnswer(
            facet=facet, answer=f"The documents establish this about {facet}.",
            points=[f"Specific point {n} about {facet}." for n in range(6)],
            unknowns=[f"Unsettled question {n} about {facet}?" for n in range(2)],
            sources=sources, confidence="High"))
    return EvidenceRecord(
        documents=[DocumentRef("model_document.pdf", "pdf", 61, drawn_on=True)],
        claims=claims, answers=answers)


class TestAModelGetsTheReadingNotTheProvenance(unittest.TestCase):
    def setUp(self):
        self.record = _record()
        self.document = build_context_document(self.record, "Claims Assistant")
        self.model = build_model_context(self.record, "Claims Assistant")

    def test_the_substance_survives(self):
        """Every answer, every specific, and everything left unsettled."""
        for answer in self.record.answers:
            self.assertIn(answer.answer, self.model)
            for point in answer.points:
                self.assertIn(point, self.model)
            for unknown in answer.unknowns:
                self.assertIn(unknown, self.model)

    def test_the_provenance_does_not(self):
        """The per-claim citation is what a person auditing the reading needs -- which claim, from
        which document, on which page. A model cannot check any of it, so carrying it spends the
        budget on the one part of the reading that cannot inform anything it produces."""
        first = self.record.claims[0]
        self.assertIn("Supporting observations", self.document)
        self.assertIn(first.id, self.document)
        self.assertIn("page 1", self.document)

        self.assertNotIn("Supporting observations", self.model)
        self.assertNotIn(first.id, self.model)
        self.assertNotIn("page 1", self.model)

    def test_it_is_substantially_smaller_than_the_document(self):
        self.assertLess(len(self.model), len(self.document) * 0.75)

    def test_what_the_documents_did_not_cover_is_still_said(self):
        """A model that cannot tell a gap in the documentation from an absence in the agent will
        fill it in, which is the one thing ingestion exists to prevent."""
        thin = _record()
        thin.answers = [a for a in thin.answers if a.facet != "tools"]
        model = build_model_context(thin)
        self.assertIn("Not covered by the submitted documents", model)


class TestTheBudgetIsGenerousAndHonest(unittest.TestCase):
    def _file(self, text: str) -> str:
        path = Path(tempfile.mkdtemp()) / "context.md"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def test_a_realistic_reading_is_nowhere_near_the_budget(self):
        """The cap exists to catch an outlier, not to trim every run. A cap a normal pack exceeds
        degrades every scenario space quietly, which is the failure this is set to avoid."""
        model = build_model_context(_record(claims_per_facet=12))
        self.assertLess(len(model), MAX_CHARS / 4)

    def test_nothing_is_cut_when_it_fits(self):
        text = "\n".join(f"## Section {n}\n\nBody of section {n}.\n" for n in range(20))
        loaded = load_context(self._file(text))
        self.assertEqual(loaded.strip(), text.strip())

    def test_going_over_drops_whole_sections_rather_than_cutting_mid_sentence(self):
        body = "x" * 400
        text = "\n".join(f"## Section {n}\n\n{body}\n" for n in range(20))
        loaded = load_context(self._file(text), max_chars=2000)

        self.assertLess(len(loaded), len(text))
        self.assertIn("## Section 0", loaded)
        self.assertNotIn("## Section 19", loaded)
        # Whatever survived, survived whole: every heading kept has its body under it.
        for line in loaded.splitlines():
            if line.startswith("## Section"):
                number = line.split()[-1]
                self.assertIn(f"## Section {number}\n\n{body}", loaded)

    def test_dropping_a_section_is_said_out_loud(self):
        text = "\n".join(f"## Section {n}\n\n{'x' * 400}\n" for n in range(20))
        with self.assertLogs("scenario_generator.core.context", level=logging.WARNING) as logged:
            load_context(self._file(text), max_chars=2000)
        self.assertIn("section(s) were left out", "\n".join(logged.output))

    def test_notes_are_never_the_thing_that_gets_dropped(self):
        """They are the smallest part of the context and the part somebody typed deliberately."""
        text = "\n".join(f"## Section {n}\n\n{'x' * 400}\n" for n in range(20))
        answer = "Disputes over 500 always go to a person."
        loaded = load_context(self._file(text), notes=[answer], max_chars=2000)
        self.assertIn(answer, loaded)
        self.assertIn("NOTES ADDED BY THE VALIDATOR", loaded)

    def test_a_context_with_no_sections_still_says_it_was_cut(self):
        with self.assertLogs("scenario_generator.core.context", level=logging.WARNING) as logged:
            loaded = load_context(self._file("y" * 5000), max_chars=1000)
        self.assertLessEqual(len(loaded), 1000)
        self.assertIn("truncated", "\n".join(logged.output))


class TestANoteReachesTheCallThatNeedsIt(unittest.TestCase):
    """The question behind "will what I typed actually be used": yes, and this is the path."""

    def setUp(self):
        from scenario_generator.webapp.workspace import Workspace

        self.workspace = Workspace.create(Path(tempfile.mkdtemp()), "Answers")
        (self.workspace.root / "ingest_evidence.json").write_text("{}", encoding="utf-8")

    def test_a_note_saved_on_the_page_is_in_what_later_stages_are_given(self):
        from scenario_generator.webapp.runners import _context

        self.workspace.add_note("intake", "DEC-02's outcomes are Eligible / Not eligible.")
        self.assertIn("Eligible / Not eligible", _context(self.workspace))

    def test_an_answer_given_at_one_stage_reaches_the_stages_after_it(self):
        from scenario_generator.webapp.runners import _context

        self.workspace.add_note("intake", "Disputes over 500 always go to a person.")
        self.assertIn("Disputes over 500", _context(self.workspace))

    def test_notes_accumulate_rather_than_replace(self):
        from scenario_generator.webapp.runners import _context

        self.workspace.add_note("intake", "First thing.")
        self.workspace.add_note("intake", "Second thing.")
        context = _context(self.workspace)
        self.assertIn("First thing.", context)
        self.assertIn("Second thing.", context)


if __name__ == "__main__":
    unittest.main()
