"""Optional business context supplied alongside the intake.

A plain text or markdown file — typically relevant extracts from the model documentation —
passed to the LLM passes as extra grounding. Never required: everything still works from the
intake alone, and nothing here affects the deterministic graph.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

MAX_CHARS = 20000


def load_context(path: str = None) -> str:
    """Read a context file if one was given. A missing or unreadable file is a warning, not an
    error — the pipeline continues without it."""
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read().strip()
    except OSError as exc:
        logger.warning("Could not read context file %s (%s) — continuing without it.", path, exc)
        return ""

    if len(text) > MAX_CHARS:
        logger.warning("Context file is %d characters; using the first %d.", len(text), MAX_CHARS)
        text = text[:MAX_CHARS]
    return text
