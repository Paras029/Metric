"""Optional business context supplied alongside the intake."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Roughly 100k tokens at four characters to a token, against models that hold a million. The
# generosity is the point: a context document assembled from a sixty-page pack runs to tens of
# thousands of characters, and a cap a normal pack exceeds is a cap that silently degrades every
# run rather than one that catches an outlier.
MAX_CHARS = 400_000


def load_context(path: str = None, notes=None, max_chars: int = None) -> str:
    """Assemble the supplementary context: a file if one was given, plus anything typed alongside."""
    note_block = _notes_block(notes)
    if not path:
        return note_block
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read().strip()
    except OSError as exc:
        logger.warning("Could not read context file %s (%s) — continuing without it.", path, exc)
        return note_block

    text = _trim(text, (max_chars or MAX_CHARS) - len(note_block))
    return f"{text}\n\n{note_block}".strip() if note_block else text


def _trim(text: str, budget: int) -> str:
    """``text`` cut to ``budget`` on a section boundary, saying what was left out."""
    if budget <= 0 or len(text) <= budget:
        return text

    sections = text.split("\n## ")
    if len(sections) > 1:
        kept, used = [sections[0]], len(sections[0])
        for section in sections[1:]:
            if used + len(section) + 4 > budget:
                break
            kept.append(section)
            used += len(section) + 4
        dropped = len(sections) - len(kept)
        if dropped and len(kept) > 1:
            logger.warning(
                "The context is %d characters against a %d budget; the last %d section(s) were "
                "left out. Trim the submitted pack, or raise LLM_MAX_CONTEXT_CHARS.",
                len(text), budget, dropped)
            return "\n## ".join(kept).strip()

    cut = text.rfind("\n\n", 0, budget)
    logger.warning("The context is %d characters against a %d budget and has no sections to cut "
                   "on; it was truncated. Raise LLM_MAX_CONTEXT_CHARS.", len(text), budget)
    return text[:cut if cut > budget // 2 else budget].strip()


def _notes_block(notes) -> str:
    """Free-text notes, labelled so a model reads them as the validator's own input."""
    cleaned = [str(n).strip() for n in (notes or []) if str(n).strip()]
    if not cleaned:
        return ""
    return "NOTES ADDED BY THE VALIDATOR\n\n" + "\n".join(f"- {n}" for n in cleaned)
