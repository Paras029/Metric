"""Turning submitted files into locatable text.

Every reader returns segments rather than one block of text, and every segment carries a locator
-- a page, a slide, a heading. That locator is what a citation points at later, so a reader that
loses it makes the grounding check unable to say where anything came from.

All readers sit behind :func:`read_document`. Adding a format means adding one function and one
entry to the registry; nothing else changes. Third-party parsers are imported inside the readers
that need them, so a missing optional package disables one format rather than the whole tool --
which matters where packages arrive through an internal mirror one approval at a time.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Tuple

from ..core.evidence import DocumentRef

logger = logging.getLogger(__name__)

# Below this, a document that should contain prose almost certainly has no text layer.
MIN_CHARS_PER_UNIT = 40


class UnreadableDocument(Exception):
    """A file could not be turned into text, with a reason worth showing the person who sent it."""


@dataclass(frozen=True)
class Segment:
    """A passage of a document, and where in that document it sits."""

    text: str
    locator: str


def _read_pdf(path: Path) -> List[Segment]:
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(path))
    except Exception as exc:
        raise UnreadableDocument(f"could not open the PDF ({exc})") from exc

    segments = []
    for number, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:                                  # one broken page is not a broken file
            text = ""
        if text.strip():
            segments.append(Segment(text, f"p. {number}"))

    if not segments:
        raise UnreadableDocument(
            "no text could be extracted. This is usually a scanned PDF, which is an image of a "
            "document rather than a document. Ask for a text-based copy.")
    return segments


def _read_docx(path: Path) -> List[Segment]:
    from docx import Document

    try:
        document = Document(str(path))
    except Exception as exc:
        raise UnreadableDocument(f"could not open the Word document ({exc})") from exc

    # Word has no pages until it is rendered, so headings are the only locator that means
    # anything to someone looking for the passage afterwards.
    segments, heading, buffer = [], "start of document", []

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body:
            segments.append(Segment(body, f"under “{heading}”"))

    for paragraph in document.paragraphs:
        if paragraph.style.name.startswith("Heading") and paragraph.text.strip():
            flush()
            heading, buffer = paragraph.text.strip(), []
        elif paragraph.text.strip():
            buffer.append(paragraph.text)
    flush()

    for index, table in enumerate(document.tables, start=1):
        rows = [" | ".join(cell.text.strip() for cell in row.cells) for row in table.rows]
        body = "\n".join(r for r in rows if r.strip(" |"))
        if body:
            segments.append(Segment(body, f"table {index}"))

    if not segments:
        raise UnreadableDocument("the document contains no readable text.")
    return segments


def _read_pptx(path: Path) -> List[Segment]:
    from pptx import Presentation

    try:
        deck = Presentation(str(path))
    except Exception as exc:
        raise UnreadableDocument(f"could not open the presentation ({exc})") from exc

    segments = []
    for number, slide in enumerate(deck.slides, start=1):
        parts = [shape.text.strip() for shape in slide.shapes
                 if getattr(shape, "has_text_frame", False) and shape.text.strip()]
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame.text.strip():
            parts.append("Speaker notes: " + slide.notes_slide.notes_text_frame.text.strip())
        if parts:
            segments.append(Segment("\n".join(parts), f"slide {number}"))

    if not segments:
        raise UnreadableDocument(
            "no text was found on any slide. A deck of exported images carries no text layer; "
            "ask for the original.")
    return segments


def _read_text(path: Path) -> List[Segment]:
    body = path.read_text(encoding="utf-8", errors="replace")
    if not body.strip():
        raise UnreadableDocument("the file is empty.")

    # Markdown headings are the only structure plain text reliably has.
    segments, heading, buffer = [], "start of document", []

    def flush() -> None:
        text = "\n".join(buffer).strip()
        if text:
            segments.append(Segment(text, f"under “{heading}”"))

    for line in body.splitlines():
        if line.startswith("#") and line.lstrip("#").strip():
            flush()
            heading, buffer = line.lstrip("#").strip(), []
        else:
            buffer.append(line)
    flush()
    return segments or [Segment(body, "whole file")]


def _read_image(path: Path) -> List[Segment]:
    """Images carry no text layer. Reading one needs a vision-capable model, which is a separate
    decision, so this reports what was submitted rather than pretending to have read it."""
    raise UnreadableDocument(
        "an image cannot be read as text. Diagrams need either a vision-capable model or a short "
        "written description of the flow they show.")


READERS: Dict[str, Tuple[Callable[[Path], List[Segment]], str]] = {
    ".pdf": (_read_pdf, "PDF"),
    ".docx": (_read_docx, "Word document"),
    ".pptx": (_read_pptx, "presentation"),
    ".md": (_read_text, "notes"),
    ".txt": (_read_text, "notes"),
    ".png": (_read_image, "image"),
    ".jpg": (_read_image, "image"),
    ".jpeg": (_read_image, "image"),
}

SUPPORTED_EXTENSIONS = tuple(sorted(READERS))


def read_document(path: Path) -> Tuple[DocumentRef, List[Segment]]:
    """Read one file into locatable segments, with a record of what it was.

    Raises :class:`UnreadableDocument` with a reason the person who submitted the pack can act
    on. Failing loudly is the point: a document that silently contributes nothing is worse than
    one that is refused, because nobody finds out until the benchmark is thin.
    """
    path = Path(path)
    entry = READERS.get(path.suffix.lower())
    if entry is None:
        raise UnreadableDocument(
            f"{path.suffix or 'this file type'} is not supported. Supported: "
            f"{', '.join(SUPPORTED_EXTENSIONS)}.")

    reader, kind = entry
    segments = reader(path)

    total = sum(len(s.text) for s in segments)
    note = ""
    if total < MIN_CHARS_PER_UNIT * max(len(segments), 1):
        note = ("very little text for its length; check it is not mostly diagrams or scanned "
                "pages.")
        logger.warning("%s: %s", path.name, note)

    return DocumentRef(name=path.name, kind=kind, units=len(segments), note=note), segments


def chunk(segments: List[Segment], budget: int = 6000) -> List[Tuple[str, str]]:
    """Group segments into passages small enough to send, keeping locators intact.

    Returns (text, locator range) pairs. Segments are never split: a chunk boundary inside a
    sentence costs a quote its verbatim match, and a quote that cannot be matched is a claim
    thrown away.
    """
    chunks: List[Tuple[str, str]] = []
    buffer: List[Segment] = []
    size = 0

    def flush() -> None:
        if not buffer:
            return
        text = "\n\n".join(f"[{s.locator}]\n{s.text}" for s in buffer)
        span = (buffer[0].locator if len(buffer) == 1
                else f"{buffer[0].locator}–{buffer[-1].locator}")
        chunks.append((text, span))

    for segment in segments:
        if buffer and size + len(segment.text) > budget:
            flush()
            buffer, size = [], 0
        buffer.append(segment)
        size += len(segment.text)
    flush()
    return chunks
