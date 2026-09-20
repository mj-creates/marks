"""
exporter.py — Excel Aggregation Module
========================================
Combines marks records from all sections of a given semester into one
master Excel file.

Layout (single sheet):
    One row per student-subject entry.
    Columns: Semester | Overall Sem | Section | <portal columns...>
    Sorted by Section → roll/name columns.

Output file name:
    marks_{admission_year}_y{study_year}s{sem_digit}_{YYYYMMDD_HHMMSS}.xlsx

Usage:
    from exporter import export_to_excel

    path = export_to_excel(
        records=all_records,
        admission_year=2020,
        study_year=3,
        sem_digit=1,
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

_HEADER_FILL = PatternFill(start_color="BDD7E7", end_color="BDD7E7", fill_type="solid")
_HEADER_FONT = Font(bold=True)
_ANNOT_FILL  = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
_ANNOT_FONT  = Font(italic=True, color="7F7F7F")


def export_to_excel(
    records: list[dict],
    admission_year: int,
    study_year: int,
    sem_digit: int,
    output_dir: str = "output",
) -> str:
    """
    Write all section records for one semester to a single-sheet Excel file.

    Args:
        records:        Flat list of dicts from SemesterScraper.scrape_all_sections()
        admission_year: 4-digit batch year e.g. 2020
        study_year:     1–4
        sem_digit:      1 or 2
        output_dir:     Directory to write the file into

    Returns:
        Absolute path to the written .xlsx file.
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename  = f"marks_{admission_year}_y{study_year}s{sem_digit}_{timestamp}.xlsx"
    filepath  = os.path.join(output_dir, filename)

    # ── Build DataFrame ────────────────────────────────────────────────
    if records:
        df = pd.DataFrame(records)
        # Priority columns come first
        priority_cols = [
            c for c in ["Semester", "Overall Sem", "Section"] if c in df.columns
        ]
        other_cols = [c for c in df.columns if c not in priority_cols]
        df = df[priority_cols + other_cols]

        # Sort by Section, then roll/name columns
        sort_cols = priority_cols + [
            c for c in df.columns
            if any(k in c.lower() for k in ["roll", "name", "rno", "regno"])
        ]
        if sort_cols:
            df.sort_values(by=sort_cols, inplace=True, ignore_index=True)
    else:
        df = pd.DataFrame(columns=["Semester", "Overall Sem", "Section", "Note"])

    # ── Write via pandas + format inside ExcelWriter context ───────────
    with pd.ExcelWriter(filepath, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Marks", index=False)
        ws = writer.sheets["Marks"]

        _format_header(ws)
        _autofit_columns(ws)

        if not records:
            note = (
                f"Year {study_year} Sem {sem_digit} "
                f"(batch {admission_year}) — no data available"
            )
            ws.cell(row=2, column=1, value=note)
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




