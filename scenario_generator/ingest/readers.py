"""Turning submitted files into locatable text.

Every reader returns segments rather than one block of text, and every segment carries a locator
-- a page, a slide, a heading. That locator is what a citation points at later, so a reader that
loses it makes the grounding check unable to say where anything came from.

All readers sit behind :func:`read_document`. Adding a format means adding one function and one
entry to the scenario space metadata; nothing else changes. Third-party parsers are imported inside the readers
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


def _rows_to_segments(rows: List[List[str]], sheet: str, per_segment: int) -> List[Segment]:
    """Turn rows into readable passages, keeping the header on each one.

    A spreadsheet read row by row loses the only thing that makes a row mean anything, which is
    the column it sits under. Repeating the header at the top of every passage costs a few lines
    and keeps each passage self-describing, so a fact read out of row 400 still knows what its
    third column was called.
    """
    rows = [[str(cell).strip() if cell is not None else "" for cell in row] for row in rows]
    rows = [row for row in rows if any(row)]
    if not rows:
        return []

    header, body = rows[0], rows[1:]
    if not body:                                           # a single row is its own content
        return [Segment(" | ".join(header), f"sheet “{sheet}”")]

    heading = " | ".join(header)
    segments = []
    for start in range(0, len(body), per_segment):
        block = body[start:start + per_segment]
        first, last = start + 2, start + len(block) + 1    # 1-based, and the header is row 1
        text = "\n".join([heading, "-" * min(len(heading), 80)]
                         + [" | ".join(row) for row in block])
        segments.append(Segment(text, f"sheet “{sheet}”, rows {first}–{last}"))
    return segments


# Rows per passage. Small enough that a locator points at a findable part of the sheet, large
# enough that a table of thresholds is not split across a dozen of them.
ROWS_PER_SEGMENT = 40


def _read_spreadsheet(path: Path) -> List[Segment]:
    """Read a workbook sheet by sheet, or a delimited file as a single table.

    Spreadsheets carry a lot of what a scenario space needs -- decision tables, routing rules, policy
    matrices, term glossaries -- and a team that keeps its rules in one will send it. Reading the
    values rather than the formulae is deliberate: the computed result is the behaviour, and the
    formula is how it happens to be worked out.
    """
    if path.suffix.lower() == ".csv":
        import csv

        with path.open(newline="", encoding="utf-8", errors="replace") as handle:
            sample = handle.read(8192)
            handle.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            except csv.Error:
                dialect = csv.excel
            rows = [list(row) for row in csv.reader(handle, dialect)]
        segments = _rows_to_segments(rows, path.stem, ROWS_PER_SEGMENT)
        if not segments:
            raise UnreadableDocument("the file has no rows.")
        return segments

    from openpyxl import load_workbook

    try:
        book = load_workbook(str(path), data_only=True, read_only=True)
    except Exception as exc:
        raise UnreadableDocument(f"could not open the workbook ({exc})") from exc

    segments = []
    try:
        for name in book.sheetnames:
            rows = [list(row) for row in book[name].iter_rows(values_only=True)]
            segments.extend(_rows_to_segments(rows, name, ROWS_PER_SEGMENT))
    finally:
        book.close()

    if not segments:
        raise UnreadableDocument("every sheet in the workbook is empty.")
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


# Images have no text layer, so they are not read here. They are collected by
# :func:`image_documents` and described by a vision-capable model during ingestion; where vision
# is unavailable the submitter is asked for a written description instead.
IMAGE_MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


def _read_image(path: Path) -> List[Segment]:
    raise UnreadableDocument(
        "an image has no text to read. Diagrams are described by a vision-capable model where one "
        "is available; otherwise supply a short written description of the flow it shows.")


def is_image(path: Path) -> bool:
    return Path(path).suffix.lower() in IMAGE_MEDIA_TYPES


def load_image(path: Path):
    """An image as (media_type, bytes), ready to attach to a model call."""
    path = Path(path)
    media_type = IMAGE_MEDIA_TYPES.get(path.suffix.lower())
    if media_type is None:
        raise UnreadableDocument(f"{path.suffix} is not an image format this reads.")
    return media_type, path.read_bytes()


READERS: Dict[str, Tuple[Callable[[Path], List[Segment]], str]] = {
    ".pdf": (_read_pdf, "PDF"),
    ".docx": (_read_docx, "Word document"),
    ".pptx": (_read_pptx, "presentation"),
    ".xlsx": (_read_spreadsheet, "spreadsheet"),
    ".xlsm": (_read_spreadsheet, "spreadsheet"),
    ".csv": (_read_spreadsheet, "spreadsheet"),
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
    one that is refused, because nobody finds out until the scenario space is thin.
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
