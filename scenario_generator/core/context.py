"""Optional business context supplied alongside the intake.

A plain text or markdown file — typically relevant extracts from the model documentation —
passed to the LLM passes as extra grounding. Never required: everything still works from the
intake alone, and nothing here affects the deterministic graph.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

MAX_CHARS = 20000


def load_context(path: str = None, notes=None) -> str:
    """Assemble the supplementary context: a file if one was given, plus anything typed alongside.

    Both are optional and independent. Notes are what the person running the pipeline knows that
    the documents do not say — a correction, a constraint, a risk worth weighting — and they are
    appended after the file so they read as the more recent word on the subject.

    A missing or unreadable context file is a warning rather than an error; the pipeline
    continues without it.
    """
    note_block = _notes_block(notes)
    if not path:
        return note_block
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read().strip()
    except OSError as exc:
        logger.warning("Could not read context file %s (%s) — continuing without it.", path, exc)
        return note_block

    if len(text) > MAX_CHARS:
        logger.warning("Context file is %d characters; using the first %d.", len(text), MAX_CHARS)
        text = text[:MAX_CHARS]
    return f"{text}\n\n{note_block}".strip() if note_block else text


def _notes_block(notes) -> str:
    """Free-text notes, labelled so a model reads them as the validation team's own input."""
    cleaned = [str(n).strip() for n in (notes or []) if str(n).strip()]
    if not cleaned:
        return ""
    return "NOTES ADDED BY THE VALIDATION TEAM\n\n" + "\n".join(f"- {n}" for n in cleaned)
