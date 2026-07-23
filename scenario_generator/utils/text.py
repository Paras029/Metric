"""Generic text parsing for decision-outcome strings. No dependency on the rest of the package,
so these are safe to reuse anywhere a "DEC-xx=Variant"-shaped string shows up.
"""
from __future__ import annotations

import re
from typing import List, Tuple

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
