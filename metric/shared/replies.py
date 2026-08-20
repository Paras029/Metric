"""Reading what a model returned, safely.

A reply is JSON the model was asked to produce, not JSON anything guarantees. Every pass that
reads one needs the same four things -- a string that is really a string, a list that is really a
list, the entries of a list that are really objects, and a bounded integer -- and each was
reimplementing them privately, identically, in two places at once.

Nothing here validates *meaning*; a closed vocabulary is checked by :func:`utils.text.one_of` and
a structural rule by the pass that owns it. This only makes sure the shape is what the code below
it is about to assume.
"""
from __future__ import annotations

from typing import List


def text(entry: dict, key: str) -> str:
    """One trimmed string. A missing key, a None, or a number all read as text."""
    return str(entry.get(key, "") or "").strip()


def string_list(entry: dict, key: str) -> List[str]:
    """A list of trimmed, non-empty strings, tolerating a single value sent unwrapped."""
    raw = entry.get(key) or []
    if isinstance(raw, str):
        raw = [raw]
    return [str(item).strip() for item in raw if str(item).strip()]


def prose(entry: dict, key: str) -> str:
    """A block of written text, whether the model sent it as one string or as a list of lines.

    Use this for anything a person reads -- a description, a turn plan, a rationale -- rather than
    :func:`text`. A prompt that asks for numbered turns on separate lines is answered with a JSON
    array often enough to matter, and ``str()`` on a list yields its Python repr: the scenario space metadata and
    the page end up carrying ``['Open by naming the charge.', 'Answer the question.']``, brackets
    and quotes and all, which nobody notices until a tester is handed it. Joining on newlines is
    what the prompt asked for in the first place.
    """
    raw = entry.get(key)
    if isinstance(raw, (list, tuple)):
        return "\n".join(str(item).strip() for item in raw if str(item).strip())
    return str(raw or "").strip()


def objects(data: dict, key: str) -> List[dict]:
    """The list at ``key``, keeping only the entries that are objects at all."""
    return [entry for entry in (data.get(key) or []) if isinstance(entry, dict)]


def at_least_one(value: object, default: int = 1) -> int:
    """A count that has to be positive -- a retry bound, usually.

    Anything unreadable falls back to ``default`` rather than to zero: a decision that may be
    attempted no times at all is not a weaker claim than the default, it is an unwalkable graph.
    """
    try:
        number = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default
    return number if number >= 1 else default
