"""Optional business context supplied alongside the intake.

A plain text or markdown file — typically the context document ingestion wrote, or relevant
extracts from the model documentation — passed to the model-using passes as extra grounding.
Never required: everything still works from the intake alone, and nothing here affects the
deterministic graph.

There is a cap on how much is passed, and the interesting part is what happens when it is hit.
Cutting at a character count is the wrong answer: it lands mid-sentence, drops whichever facts
happened to be last, and says nothing about having done so, which makes a thin reading and a
truncated one indistinguishable. So the cap is set where a real context document fits under it
comfortably, and going over it drops **whole sections**, from the end, and says which ones. A
reader can then act on it -- trim the pack, or raise the cap -- rather than wondering why a
threshold that is plainly in the documentation never reached the benchmark.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Roughly 100k tokens at four characters to a token, against models that hold a million. The
# generosity is the point: a context document assembled from a sixty-page pack runs to tens of
# thousands of characters, and a cap a normal pack exceeds is a cap that silently degrades every
# run rather than one that catches an outlier.
#
# The built-in default. ``max_chars`` overrides it per call, which is how the configured
# LLM_MAX_CONTEXT_CHARS reaches here -- this module is in ``core`` and does not read settings,
# because ``core`` does not depend on the model layer that owns them.
MAX_CHARS = 400_000


def load_context(path: str = None, notes=None, max_chars: int = None) -> str:
    """Assemble the supplementary context: a file if one was given, plus anything typed alongside.

    Both are optional and independent. Notes are what the person running the pipeline knows that
    the documents do not say — a correction, a constraint, an answer to a question raised about
    the declaration — and they are appended after the file so they read as the more recent word
    on the subject.

    Notes are never dropped, whatever the file's size. They are the smallest part of this and the
    part somebody typed deliberately; a budget that spent itself on the documents and then cut the
    answer a person had just given would be exactly backwards.

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

    text = _trim(text, (max_chars or MAX_CHARS) - len(note_block))
    return f"{text}\n\n{note_block}".strip() if note_block else text


def _trim(text: str, budget: int) -> str:
    """``text`` cut to ``budget`` on a section boundary, saying what was left out.

    Markdown headings are the only structure this can rely on, and they are enough: a context
    document is a sequence of ``##`` sections, and dropping whole ones from the end loses complete
    answers rather than the tail of every answer. Where there are no headings to cut on, this
    falls back to a paragraph boundary, and only then to a hard cut.
    """
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
