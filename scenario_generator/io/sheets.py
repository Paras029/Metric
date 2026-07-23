"""Minimal spreadsheet helpers: a bold header row, column widths, plain data rows.

Unstyled beyond a bold header and a frozen top row, keeping templates and outputs
easy to read and to edit by hand.
"""
from __future__ import annotations

from typing import Iterable, List

from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

_BOLD = Font(bold=True)


def add_sheet(workbook, name: str, headers: List[str], widths: List[int] = None) -> Worksheet:
    """Create a sheet with a bold, frozen header row."""
    sheet = workbook.create_sheet(name)
    for column, header in enumerate(headers, start=1):
        cell = sheet.cell(row=1, column=column, value=header)
        cell.font = _BOLD
    for column, width in enumerate(widths or [], start=1):
        sheet.column_dimensions[get_column_letter(column)].width = width
    sheet.freeze_panes = "A2"
    return sheet


def write_rows(sheet: Worksheet, rows: Iterable[Iterable], start: int = 2) -> None:
    """Write data rows starting just below the header."""
    for r, row in enumerate(rows, start=start):
        for column, value in enumerate(row, start=1):
            sheet.cell(row=r, column=column, value=value)


def read_rows(sheet: Worksheet) -> List[List[str]]:
    """Read every non-empty data row (row 1 is the header) as trimmed strings."""
    rows = []
    for values in sheet.iter_rows(min_row=2, values_only=True):
        if values and values[0] not in (None, ""):
            rows.append(["" if v is None else str(v).strip() for v in values])
    return rows
