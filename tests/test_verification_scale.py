"""Checking a quote against a whole submitted pack, at the size a real pack actually is.

Grounding is the guard the rest of the pipeline is built on, and it is the only part of the tool
that does heavy work on the CPU rather than waiting on a model. It was doing that work per
citation against the entire corpus, which a sixty-page pack turns into more than a second each --
a reading with fifty citations spent a minute of a stage's time here with nothing to show for it.

Both properties matter and they pull against each other, so both are pinned: a quote the documents
support is still found however mangled the extraction left it, a quote the documents do not support
is still rejected however common its words, and neither takes time proportional to re-reading the
pack from scratch.
"""
import random
import string
import time
import unittest

from metric.phases.intake.intake.grounding import MATCH_THRESHOLD, Source, locate

# Big enough to be the thing under test -- a sixty-page pack is around this -- and generated rather
# than fixed so no quote can accidentally be findable for the wrong reason.
_WORDS = None
_CORPUS = None


def _corpus() -> str:
    global _WORDS, _CORPUS
    if _CORPUS is None:
        rng = random.Random(20260731)
        _WORDS = ["".join(rng.choices(string.ascii_lowercase, k=rng.randint(3, 9)))
                  for _ in range(4000)]
        _CORPUS = " ".join(rng.choices(_WORDS, k=90_000))
    return _CORPUS


def _mangled(rng) -> str:
    """A real quote as PDF extraction leaves it: a clause dropped out of the middle."""
    start = rng.randint(0, len(_corpus()) - 400)
    quote = _corpus()[start:start + 180]
    return quote[:90] + " " + quote[95:]


class TestWhatSurvivesAndWhatDoesNot(unittest.TestCase):
    def setUp(self):
        self.rng = random.Random(11)
        self.source = Source(_corpus())

    def test_a_real_quote_mangled_by_extraction_is_still_found(self):
        for _ in range(12):
            found, reason = self.source.locate(_mangled(self.rng))
            self.assertTrue(found, reason)

    def test_a_sentence_assembled_from_words_scattered_across_the_pack_is_refused(self):
        """The words are all in the corpus; the sentence is not. This is the case that matters."""
        for _ in range(12):
            invented = " ".join(self.rng.choices(_WORDS, k=28))
            found, _ = self.source.locate(invented)
            self.assertFalse(found)

    def test_a_passage_repeated_in_the_pack_is_matched_against_the_right_copy(self):
        """A policy sentence quoted in a summary and again in the section it summarises."""
        passage = _corpus()[5_000:5_180]
        doubled = _corpus()[:300_000] + passage + _corpus()[300_000:]
        found, reason = Source(doubled).locate(passage[:90] + " " + passage[95:])
        self.assertTrue(found, reason)

    def test_the_prepared_source_agrees_with_checking_one_quote_on_its_own(self):
        quote = _mangled(self.rng)
        self.assertEqual(self.source.locate(quote), locate(quote, _corpus()))

    def test_a_quote_half_rewritten_falls_below_the_threshold(self):
        """Dropping a clause is extraction; replacing a third of the sentence is not the sentence."""
        self.assertTrue(0 < MATCH_THRESHOLD < 1)
        start = self.rng.randint(0, len(_corpus()) - 400)
        real = _corpus()[start:start + 180]
        rewritten = real[:120] + "".join(self.rng.choices("qzxjvk ", k=60))
        self.assertFalse(self.source.locate(rewritten)[0])


class TestItDoesNotRereadThePackPerQuote(unittest.TestCase):
    def test_fifty_citations_are_checked_in_a_second_rather_than_a_minute(self):
        """Not a scenario space -- a ceiling loose enough to pass on a slow machine and still catch a
        return to work that is proportional to corpus size times citation count."""
        rng = random.Random(3)
        quotes = [_mangled(rng) for _ in range(50)]

        source = Source(_corpus())
        started = time.perf_counter()
        results = [source.locate(quote) for quote in quotes]
        elapsed = time.perf_counter() - started

        self.assertTrue(all(found for found, _ in results))
        self.assertLess(elapsed, 5.0,
                        f"50 citations against a {len(_corpus()):,}-character pack took "
                        f"{elapsed:.1f}s")


if __name__ == "__main__":
    unittest.main()
