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

from .evidence import KIND_HUMAN, KIND_IMAGE, REJECTED, UNVERIFIABLE, VERIFIED, Claim

# Below this length a quote matches too much by accident to be evidence of anything.
MIN_QUOTE_CHARS = 25

# How much of the quote must be found, in order, in the source. Set high: the tolerance exists
# for extraction artefacts, not for paraphrase.
MATCH_THRESHOLD = 0.90

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


def locate(quote: str, source: str, threshold: float = MATCH_THRESHOLD) -> Tuple[bool, str]:
    """Whether a quote is supported by a source text, and why not when it is not.

    Returns the reason alongside the verdict so a rejection can be recorded and explained rather
    than leaving a claim to vanish without trace.
    """
    normalised_quote = normalise(quote)
    normalised_source = normalise(source)

    if not normalised_quote:
        return False, "no quote given"
    if len(normalised_quote) < MIN_QUOTE_CHARS:
        return False, f"quote shorter than {MIN_QUOTE_CHARS} characters"
    if not normalised_source:
        return False, "no source text to check against"
    if normalised_quote in normalised_source:
        return True, ""

    score = coverage(normalised_quote, normalised_source)
    if score >= threshold:
        return True, ""
    return False, f"quote not found in the cited source (best match {score:.0%})"


def verify(claims: Iterable[Claim], source: str,
           threshold: float = MATCH_THRESHOLD) -> List[Claim]:
    """Mark each claim against the text it was drawn from, and return the claims.

    Claims read off a diagram, or supplied by a person answering a gap question, have no source
    text to check. They are marked unverifiable rather than rejected: they carry real information
    that simply cannot be confirmed this way, so they stay in the record and surface for human
    confirmation before anything relies on them.
    """
    checked = []
    for claim in claims:
        if claim.source.kind in (KIND_IMAGE, KIND_HUMAN):
            claim.status = UNVERIFIABLE
            claim.note = claim.note or f"read from a {claim.source.kind} source; confirm by hand"
        else:
            ok, reason = locate(claim.quote, source, threshold)
            claim.status = VERIFIED if ok else REJECTED
            claim.note = reason if not ok else claim.note
        checked.append(claim)
    return checked


def rejection_report(claims: Iterable[Claim]) -> List[str]:
    """One line per rejected claim, for the run log and the gap report.

    Rejections are reported rather than silently dropped. A pass that discards a third of what it
    extracted is telling you something about the documents or the prompt, and that signal is lost
    if the count never surfaces.
    """
    return [f"{claim.source}: {claim.note or 'unsupported'} — {claim.statement[:120]}"
            for claim in claims if claim.status == REJECTED]
