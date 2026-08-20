"""Reading what a model returned, safely."""
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
    """A block of written text, whether the model sent it as one string or as a list of lines."""
    raw = entry.get(key)
    if isinstance(raw, (list, tuple)):
        return "\n".join(str(item).strip() for item in raw if str(item).strip())
    return str(raw or "").strip()


def objects(data: dict, key: str) -> List[dict]:
    """The list at ``key``, keeping only the entries that are objects at all."""
    return [entry for entry in (data.get(key) or []) if isinstance(entry, dict)]


def at_least_one(value: object, default: int = 1) -> int:
    """A count that has to be positive -- a retry bound, usually."""
    try:
        number = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default
    return number if number >= 1 else default
