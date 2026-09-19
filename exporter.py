"""
exporter.py — Excel Aggregation Module
========================================
Combines marks records from all sections of a single semester into one
master Excel file.

Layout (single sheet):
    One row per student-subject entry.
    A "Section" column identifies which section each row belongs to.
    Sorted by Section → then by whatever roll/name columns exist.

Output file name:
    marks_{admission_year}_sem{semester}_{YYYYMMDD_HHMMSS}.xlsx

Usage:
    from exporter import export_to_excel

    path = export_to_excel(
        records=all_records,        # list of dicts from scraper
        admission_year=2020,
        semester=1,
        output_dir="output",
    )
    print(f"Saved to {path}")
"""

import logging
import os
from datetime import datetime
from pathlib import Path

import pandas as pd
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

logger = logging.getLogger(__name__)

# Header fill colour — light blue
_HEADER_FILL = PatternFill(
    start_color="BDD7E7", end_color="BDD7E7", fill_type="solid"
)
_HEADER_FONT = Font(bold=True)

# Annotation row fill — light yellow for "no data" rows
_ANNOT_FILL = PatternFill(
    start_color="FFF2CC", end_color="FFF2CC", fill_type="solid"
)
_ANNOT_FONT = Font(italic=True, color="7F7F7F")


def export_to_excel(
    records: list[dict],
    admission_year: int,
    semester: int,
    output_dir: str = "output",
) -> str:
    """
    Write all section records for one semester to a single-sheet Excel file.

    Args:
        records:        Flat list of dicts from SemesterScraper.scrape_all_sections()
        admission_year: 4-digit batch year e.g. 2020
        semester:       Semester number 1–8
        output_dir:     Directory to write the file into

    Returns:
        Absolute path to the written .xlsx file.
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"marks_{admission_year}_sem{semester}_{timestamp}.xlsx"
    filepath = os.path.join(output_dir, filename)

    # ── Build DataFrame ────────────────────────────────────────────────
    if records:
        df = pd.DataFrame(records)
        # Ensure Semester and Section columns come first
        priority_cols = [c for c in ["Semester", "Section"] if c in df.columns]
        other_cols = [c for c in df.columns if c not in priority_cols]
        df = df[priority_cols + other_cols]

        # Sort by Section first, then any roll/name columns if present
        sort_cols = priority_cols + [
            c for c in df.columns
            if any(k in c.lower() for k in ["roll", "name", "rno", "regno"])
        ]
        if sort_cols:
            df.sort_values(by=sort_cols, inplace=True, ignore_index=True)
    else:
        df = pd.DataFrame(columns=["Semester", "Section", "Note"])

    # ── Write via pandas ───────────────────────────────────────────────
    with pd.ExcelWriter(filepath, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Marks", index=False)
        ws = writer.sheets["Marks"]

        # Apply formatting inside the writer context (single write)
        _format_header(ws)
        _autofit_columns(ws)

        # Add "no data" annotation when records is empty
        if not records:
            ws.cell(row=2, column=1, value=f"Semester {semester} — no data available")
            cell = ws.cell(row=2, column=1)
            cell.font = _ANNOT_FONT
            cell.fill = _ANNOT_FILL

    logger.info("Excel saved: %s  (%d data rows)", filepath, len(df))
    return filepath


# ── Formatting helpers ─────────────────────────────────────────────────────

def _format_header(ws) -> None:
    """Apply bold + blue fill to the header row."""
    for cell in ws[1]:
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center")


def _autofit_columns(ws) -> None:
    """Set each column width to the longest cell value + 2 padding."""
    for col_cells in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col_cells[0].column)
        for cell in col_cells:
            try:
                cell_len = len(str(cell.value)) if cell.value is not None else 0
                if cell_len > max_len:
                    max_len = cell_len
            except Exception as exc:
                logger.debug("autofit skipped cell: %s", exc)
        ws.column_dimensions[col_letter].width = min(max_len + 2, 50)




