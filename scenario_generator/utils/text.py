"""Generic text parsing for decision-outcome strings. No dependency on the rest of the package,
so these are safe to reuse anywhere a "DEC-xx=Variant"-shaped string shows up.
"""
from __future__ import annotations

import re

import re
from typing import List, Sequence, Tuple

_PARENTHETICAL = re.compile(r"\(.*?\)")
_REPETITION_SUFFIX = re.compile(r"[x×]\s*\d+\s*$")


def normalise_variant(raw: str) -> str:
    """Canonical outcome form: 'Fail (attempt<2)' and 'Fail x2' both give 'Fail'."""
    text = str(raw or "").split("->")[0].split("→")[0]
    text = _PARENTHETICAL.sub("", text)
    text = _REPETITION_SUFFIX.sub("", text)
    return text.strip()


def split_list(raw: str, separators: str = r"[/,;]") -> List[str]:
    """Split a delimited string into trimmed, non-empty parts."""
    return [p.strip() for p in re.split(separators, str(raw or "")) if p.strip()]


def parse_reached_via(raw: str) -> List[Tuple[str, str]]:
    """'DEC-01=Pass, DEC-02=Match' -> [('DEC-01', 'Pass'), ('DEC-02', 'Match')]"""
    pairs = re.finditer(r"(DEC-\d+)\s*=\s*([^,;]+)", str(raw or ""))
    return [(m.group(1), normalise_variant(m.group(2))) for m in pairs]


def parse_path_str(text: str) -> List[Tuple[str, str]]:
    """'DEC-01=Pass -> DEC-02=Found' -> [('DEC-01', 'Pass'), ('DEC-02', 'Found')]"""
    pairs = []
    for chunk in str(text or "").split("->"):
        match = re.match(r"(DEC-\d+)\s*=\s*(.+)", chunk.strip())
        if match:
            pairs.append((match.group(1), match.group(2).strip()))
    return pairs


def is_yes(value: str) -> bool:
    """Loose truthy check for spreadsheet cells: 'Y', 'Yes', 'yes' all read as True."""
    return str(value or "").strip().lower().startswith("y")


def one_of(value: object, allowed: Sequence[str], default: str = "") -> str:
    """Match a value against a closed vocabulary, ignoring case and surrounding space.

    Every vocabulary in this package is closed on purpose -- a category, a materiality tier, an
    input source -- and anything outside one is dropped rather than guessed at. What must not
    happen is dropping something that *is* in the list and merely arrived capitalised differently,
    which is the common case when the value came from a model or from a hand-edited spreadsheet
    cell. ``"happy path"`` and ``"HAPPY PATH"`` are both ``"Happy path"``; ``"Escalated"`` is not.

    Returns the canonical spelling from ``allowed`` so callers can compare and store one form.
    """
    text = str(value or "").strip().lower()
    return next((item for item in allowed if item.lower() == text), default)


# Words that carry no distinguishing weight in the name of a capability, tool or persona. Two
# names that differ only by these are the same name: "Identity check" and "Identity checking
# service" name one thing, and keeping both splits a block in half.
_NAME_NOISE = {"the", "a", "an", "of", "for", "and", "to", "service", "services", "system", "systems",
          "check", "checking", "checks", "verification", "verify", "verifying", "process",
          "processing", "handler", "handling", "module", "component", "step", "agent", "user"}

_NAME_SPLIT = re.compile(r"[^a-z0-9]+")


def name_key(text: str) -> str:
    """A name reduced to what distinguishes it: lowercase, de-pluralised, noise words dropped.

    Joined with no separator, so a compound written as one word and as two reduces to the same
    key: "Cardmember" and "Card members" are one persona, and keeping both doubles a part of the
    scenario space without widening it. Sorted, so word order does not matter -- "dispute filing"
    and "filing a dispute" are one capability.

    Deliberately aggressive on stop words and deliberately not fuzzy beyond that. Edit distance
    would fold "Identity check" into "Identity checks" and also into "Identify charge", and the
    second is a real distinction the whole scenario space hangs on.
    """
    words = []
    for word in _NAME_SPLIT.split(str(text or "").lower()):
        if not word or word in _NAME_NOISE:
            continue
        if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        # One morphological rule, for the one pair this domain is full of: identify and
        # identification, verify and verification, classify and classification. Nothing broader --
        # general stemming would fold "identity" into "identify" and from there towards "identify
        # charge", and those are distinctions the whole scenario space hangs on.
        if word.endswith("ification"):
            word = word[:-len("ification")] + "ify"
        elif word.endswith("ication"):
            word = word[:-len("ication")] + "y"
        words.append(word)
    return "".join(sorted(set(words)))
