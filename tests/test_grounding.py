"""The grounding check is what makes ingestion safe to build on, so these tests are about the
two ways it can fail: rejecting a real quote because extraction mangled it, and accepting a
quote the document does not support.
"""
import unittest

from scenario_generator.core.evidence import (KIND_HUMAN, KIND_IMAGE, REJECTED, UNVERIFIABLE,
                                              VERIFIED, Claim, EvidenceRecord, SourceRef)
from scenario_generator.core.grounding import locate, normalise, verify

_SOURCE = (
    "3.2 Identity verification\n\n"
    "The assistant must verify the cardmember's identity before any transaction detail is "
    "disclosed. Three consecutive failed attempts lock the session and route the cardmember to "
    "a human agent. The assistant may not disclose the reason for a lock."
)


def _claim(quote, kind="pdf", facet="policy_constraints"):
    return Claim(facet=facet, statement="Identity is verified before disclosure.", quote=quote,
                 source=SourceRef("model_doc.pdf", "p. 12", kind))


class TestNormalisation(unittest.TestCase):
    def test_line_wrapped_hyphenation_is_rejoined(self):
        self.assertIn("verification", normalise("identity veri-\nfication is required"))

    def test_typographic_variants_fold_to_plain_characters(self):
        self.assertEqual(normalise("the “cardmember’s” record"),
                         'the "cardmember\'s" record')

    def test_whitespace_shape_is_irrelevant(self):
        self.assertEqual(normalise("a   b\n\tc"), "a b c")


class TestLocate(unittest.TestCase):
    def test_a_verbatim_quote_is_found(self):
        ok, reason = locate("Three consecutive failed attempts lock the session", _SOURCE)
        self.assertTrue(ok, reason)

    def test_a_quote_mangled_by_pdf_extraction_still_matches(self):
        mangled = "Three  consecutive failed at-\ntempts lock the ses sion"
        ok, reason = locate(mangled, _SOURCE)
        self.assertTrue(ok, reason)

    def test_a_reworded_quote_is_rejected(self):
        ok, reason = locate("The session is locked after several unsuccessful login tries",
                            _SOURCE)
        self.assertFalse(ok)
        self.assertIn("not found", reason)

    def test_an_invented_quote_is_rejected(self):
        ok, _ = locate("Cardmembers may dispute a transaction up to ninety days after posting",
                       _SOURCE)
        self.assertFalse(ok)

    def test_a_quote_too_short_to_mean_anything_is_rejected(self):
        ok, reason = locate("the session", _SOURCE)
        self.assertFalse(ok)
        self.assertIn("shorter than", reason)

    def test_an_empty_source_rejects_rather_than_accepts(self):
        ok, _ = locate("Three consecutive failed attempts lock the session", "")
        self.assertFalse(ok)


class TestVerify(unittest.TestCase):
    def test_supported_claims_are_marked_verified(self):
        claims = verify([_claim("Three consecutive failed attempts lock the session")], _SOURCE)
        self.assertEqual(claims[0].status, VERIFIED)

    def test_unsupported_claims_are_rejected_and_explained(self):
        claims = verify([_claim("Disputes may be raised within ninety days of posting")], _SOURCE)
        self.assertEqual(claims[0].status, REJECTED)
        self.assertTrue(claims[0].note)

    def test_diagram_claims_are_unverifiable_rather_than_rejected(self):
        claims = verify([_claim("", KIND_IMAGE)], _SOURCE)
        self.assertEqual(claims[0].status, UNVERIFIABLE)
        self.assertTrue(claims[0].needs_confirmation)

    def test_human_supplied_claims_survive_without_a_document(self):
        claims = verify([_claim("", KIND_HUMAN)], "")
        self.assertEqual(claims[0].status, UNVERIFIABLE)
        self.assertTrue(claims[0].is_usable)

    def test_rejected_claims_never_reach_anything_downstream(self):
        record = EvidenceRecord(claims=verify(
            [_claim("Three consecutive failed attempts lock the session"),
             _claim("Disputes may be raised within ninety days of posting")], _SOURCE))
        self.assertEqual(len(record.usable()), 1)
        self.assertEqual(len(record.rejected()), 1)

    def test_a_rejection_says_why_rather_than_vanishing(self):
        """A pass discarding a third of what it extracted is telling you something; a claim that
        disappears without a reason attached loses that signal."""
        claim = verify([_claim("Disputes may be raised within ninety days")], _SOURCE)[0]
        self.assertEqual(claim.status, "rejected")
        self.assertTrue(claim.note)


class TestEvidenceRecord(unittest.TestCase):
    def test_facets_with_no_evidence_are_reported_as_gaps(self):
        record = EvidenceRecord(claims=[_claim("x" * 40, facet="use_case")])
        self.assertNotIn("use_case", record.empty_facets())
        self.assertIn("decisions", record.empty_facets())

    def test_the_record_survives_a_json_round_trip(self):
        record = EvidenceRecord(claims=verify(
            [_claim("Three consecutive failed attempts lock the session")], _SOURCE))
        restored = EvidenceRecord.from_dict(record.to_dict())
        self.assertEqual(restored.claims[0].source.document, "model_doc.pdf")
        self.assertEqual(restored.claims[0].status, VERIFIED)


if __name__ == "__main__":
    unittest.main()
