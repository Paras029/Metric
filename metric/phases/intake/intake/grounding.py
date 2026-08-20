"""Checking that an extracted claim is actually supported by the document it cites.

This is the guard that makes document ingestion safe to build on. An extraction pass returns, for
every claim, a verbatim span it says the claim came from. This module checks that span really is
in the source text, and rejects the claim when it is not. A claim whose quote cannot be located
is discarded rather than flagged, because a fabricated citation is worse than no citation: it
carries the appearance of evidence.

The check is deterministic, and deliberately so. It is the same principle the rest of the
pipeline follows -- a model may decide how something is worded, never whether it is true.

Matching is tolerant of how text arrives rather than of what it says. Extracting a PDF breaks
words across lines, turns quotation marks into typographic variants, collapses ligatures and
scatters whitespace; none of that changes meaning, and none of it should reject a real quote. A
quote that has been reworded, however, will not survive, which is the point.
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Iterable, List, Tuple

from metric.phases.intake.intake.evidence import KIND_HUMAN, KIND_IMAGE, REJECTED, UNVERIFIABLE, VERIFIED, Claim

# Below this length a quote matches too much by accident to be evidence of anything.
MIN_QUOTE_CHARS = 16

# How much of the quote must be found, in order, in the source.
#
# The number trades two failures against each other, and they are not equally costly here. A
# fabricated quote is caught at almost any threshold, because invented text shares little with the
# document. A real quote fails when the model tidied punctuation, joined two sentences, or dropped
# a clause -- all of which are faithful readings expressed loosely. Losing those is how extraction
# ends up thin, and a scenario space built on a thin reading of the documentation is the expensive
# outcome, so the threshold sits where loose-but-faithful quotes survive.
MATCH_THRESHOLD = 0.78

_HYPHEN_BREAK = re.compile(r"(\w)[-‐-―]\s*\n\s*(\w)")
_WHITESPACE = re.compile(r"\s+")
_QUOTES = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "–": "-", "—": "-", "―": "-", "−": "-",
    " ": " ", " ": " ", " ": " ",
})


def normalise(text: str) -> str:
    """Reduce text to the form both sides of a comparison can be trusted to share.

    Compatibility decomposition folds ligatures and other presentation forms back to their base
    characters; hyphenation introduced by line wrapping is rejoined before whitespace collapses,
    since afterwards the line break that identifies it is gone.
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", str(text)).translate(_QUOTES)
    text = _HYPHEN_BREAK.sub(r"\1\2", text)
    return _WHITESPACE.sub(" ", text).strip().lower()


def coverage(quote: str, source: str) -> float:
    """The fraction of the quote that appears, in order, in the source.

    Summing the matching blocks rather than taking the single longest one means a stray character
    introduced mid-quote by extraction costs a little of the score instead of halving it.
    """
    if not quote:
        return 0.0
    matcher = SequenceMatcher(None, quote, source, autojunk=False)
    return sum(block.size for block in matcher.get_matching_blocks()) / len(quote)


# The fuzzy match must be local. Checking a quote against a whole submitted pack rather than a
# single passage means matching blocks can be gathered from anywhere in hundreds of thousands of
# characters, and a sentence that appears nowhere can be assembled from fragments scattered
# across the corpus. Both of these bound that: the match is scored inside a window around the
# quote's best anchor, and that anchor must itself be a substantial run rather than a common word.
_WINDOW_MULTIPLE = 2.5
_MIN_ANCHOR_SHARE = 0.25

# How many candidate neighbourhoods are scored before the best is taken. A quote's anchor can
# legitimately appear more than once in a pack -- a policy sentence repeated in a summary and in
# the section it summarises -- and the first occurrence is not always the one the quote came from.
# Scoring a handful and keeping the best is what makes finding the anchor by search rather than by
# exhaustive diff safe; past a few, the extra candidates are the same neighbourhood again.
_MAX_ANCHORS = 6


class Source:
    """A source text prepared once, so many quotes can be checked against it cheaply.

    Normalising a submitted pack costs tens of milliseconds and depends only on the source, so a
    verification pass that normalised per quote would repeat that work once for every citation.
    The prepared text is held here instead, and the quote is the only thing that changes between
    checks.
    """

    def __init__(self, text: str) -> None:
        self.text = normalise(text)

    def __bool__(self) -> bool:
        return bool(self.text)

    def _windows(self, quote: str) -> List[str]:
        """The stretches of source worth scoring this quote against, in no particular order.

        A quote earns a neighbourhood by having a verbatim run of at least ``_MIN_ANCHOR_SHARE`` of
        its own length somewhere in the source. That bar is what stops an invented sentence being
        assembled out of common words scattered across a pack.

        The run is found by searching for it rather than by diffing the quote against the whole
        source. ``SequenceMatcher.find_longest_match`` walks every position at which each character
        of the quote occurs in the source, which against a six-hundred-thousand-character pack runs
        to millions of steps per quote -- and a real reading cites dozens. ``str.find`` answers the
        same question -- is this run present, and where -- in one pass of compiled string search.
        The bar being a *contiguous* run is what makes the two interchangeable: a run either
        appears verbatim or it does not.
        """
        probe = max(MIN_QUOTE_CHARS // 2, int(len(quote) * _MIN_ANCHOR_SHARE))
        if len(quote) < probe:
            return []

        width = int(len(quote) * _WINDOW_MULTIPLE)
        lead = (width - len(quote)) // 2
        starts, windows = set(), []
        for offset in range(0, len(quote) - probe + 1):
            at = self.text.find(quote[offset:offset + probe])
            if at < 0:
                continue
            start = max(0, at - offset - lead)
            if start in starts:
                continue
            starts.add(start)
            windows.append(self.text[start:start + width])
            if len(windows) >= _MAX_ANCHORS:
                break
        return windows

    def locate(self, quote: str, threshold: float = MATCH_THRESHOLD) -> Tuple[bool, str]:
        """Whether this source supports the quote, and why not when it does not."""
        normalised_quote = normalise(quote)

        if not normalised_quote:
            return False, "no quote given"
        if len(normalised_quote) < MIN_QUOTE_CHARS:
            return False, f"quote shorter than {MIN_QUOTE_CHARS} characters"
        if not self.text:
            return False, "no source text to check against"
        if normalised_quote in self.text:
            return True, ""

        windows = self._windows(normalised_quote)
        if not windows:
            return False, "quote not found in the cited source"

        score = max(coverage(normalised_quote, window) for window in windows)
        if score >= threshold:
            return True, ""
        return False, f"quote not found in the cited source (best match {score:.0%})"


def locate(quote: str, source: str, threshold: float = MATCH_THRESHOLD) -> Tuple[bool, str]:
    """Whether a quote is supported by a source text, and why not when it is not.

    For one quote. Checking several against the same source should build a :class:`Source` once
    and call its own ``locate`` -- see that class for why the difference is large.
    """
    return Source(source).locate(quote, threshold)


def verify(claims: Iterable[Claim], source: str,
           threshold: float = MATCH_THRESHOLD) -> List[Claim]:
    """Mark each claim against the text it was drawn from, and return the claims.

    Claims read off a diagram, or supplied by a person answering a gap question, have no source
    text to check. They are marked unverifiable rather than rejected: they carry real information
    that simply cannot be confirmed this way, so they stay in the record and surface for human
    confirmation before anything relies on them.
    """
    checked = []
    prepared = Source(source)
    for claim in claims:
        if claim.source.kind in (KIND_IMAGE, KIND_HUMAN):
            claim.status = UNVERIFIABLE
            claim.note = claim.note or f"read from a {claim.source.kind} source; confirm by hand"
        else:
            ok, reason = prepared.locate(claim.quote, threshold)
            claim.status = VERIFIED if ok else REJECTED
            claim.note = reason if not ok else claim.note
        checked.append(claim)
    return checked
