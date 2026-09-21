"""
exporter.py — Master Excel Exporter with Intelligent Column Ordering
====================================================================
Generates a polished, professional single-sheet Master Excel file
containing all students of that year and semester sorted by Registration Number.

Features:
  1. Combines all sections into one master sheet (no fragmented sections).
  2. Dynamically organizes subject columns (e.g. EPCS vs. BEEE cycle subjects)
     grouping components logically: INT -> EXT -> TOTAL -> GRADE.
  3. Sorts all rows ascending by Regd No (e.g., 201FA04001, 201FA04002...).
  4. Formats headers with Navy Blue theme, auto-fitted widths, and frozen panes.
"""

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

import pandas as pd
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from scraper import clean_subject_header

logger = logging.getLogger(__name__)

# Styling Constants
_HEADER_FILL = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
_HEADER_FONT = Font(name="Calibri", size=11, bold=True, color="FFFFFF")

_ZEBRA_FILL = PatternFill(start_color="F2F5F9", end_color="F2F5F9", fill_type="solid")
_WHITE_FILL = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")

_BORDER_THIN = Border(
    left=Side(style="thin", color="D9D9D9"),
    right=Side(style="thin", color="D9D9D9"),
    top=Side(style="thin", color="D9D9D9"),
    bottom=Side(style="thin", color="D9D9D9"),
)

_ALIGN_CENTER = Alignment(horizontal="center", vertical="center")
_ALIGN_LEFT   = Alignment(horizontal="left", vertical="center")
_ALIGN_RIGHT  = Alignment(horizontal="right", vertical="center")


def _subject_sort_key(col_name: str) -> tuple:
    """Group subject columns together and order components: INT -> EXT -> TOT -> others."""
    parts = col_name.split(" - ")
    subj = parts[0].strip().lower()
    comp = parts[1].strip().lower() if len(parts) > 1 else ""
    
    if any(k in comp for k in ["int", "mid", "sess"]):
        c_order = 1
    elif any(k in comp for k in ["ext", "sem", "end"]):
        c_order = 2
    elif any(k in comp for k in ["tot", "final"]):
        c_order = 3
    elif any(k in comp for k in ["grade", "gpa"]):
        c_order = 4
    else:
        c_order = 5
        
    return (subj, c_order, comp)


def export_to_excel(
    records: List[Dict[str, Any]],
    admission_year: int,
    study_year: int,
    sem_digit: int,
    output_dir: str = "output",
    filename_prefix: Optional[str] = None,
) -> str:
    """
    Build and save the consolidated Master Excel report.
    
    Returns:
        Absolute path to the created .xlsx file.
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = filename_prefix or f"master_marks_batch{admission_year}_y{study_year}s{sem_digit}"
    filename = f"{prefix}_{timestamp}.xlsx"
    filepath = os.path.abspath(os.path.join(output_dir, filename))

    if not records:
        df = pd.DataFrame(columns=["Regd No", "Name", "Section", "Status"])
        df.loc[0] = ["-", "No records found", "-", "No Data"]
    else:
        # Clean and normalize all column keys in each record
        cleaned_records = []
        for r in records:
            new_r = {}
            for k, v in r.items():
                cleaned_k = clean_subject_header(k)
                new_r[cleaned_k] = v
            if "Student Name" in new_r and "Name" not in new_r:
                new_r["Name"] = new_r["Student Name"]
            cleaned_records.append(new_r)

        df = pd.DataFrame(cleaned_records)
        
        # 1. Sort by Registration Number ascending
        if "Regd No" in df.columns:
            df["_sort_regd"] = df["Regd No"].astype(str).str.strip().str.upper()
            df = df.sort_values(by="_sort_regd", ascending=True).reset_index(drop=True)
            df.drop(columns=["_sort_regd"], inplace=True)
            
        # 2. Organize Columns strictly according to requested layout:
        # Columns: Regd No, Name, Section, followed linearly by clean subjects
        lead_cols = [c for c in ["Regd No", "Name", "Section"] if c in df.columns]
        
        summary_keywords = ["grand total", "total marks", "result", "sgpa", "cgpa", "percentage", "credits"]
        summary_cols = [c for c in df.columns if c not in lead_cols and any(k == c.lower().strip() for k in summary_keywords)]
        
        # Omit extraneous metadata columns from master sheet
        meta_cols = ["s.no", "sl.no", "sno", "semester", "overall sem", "status", "student name"]
        subject_cols = [c for c in df.columns if c not in lead_cols and c not in summary_cols and c.lower() not in meta_cols]
        subject_cols_sorted = sorted(subject_cols, key=_subject_sort_key)
        
        final_column_order = lead_cols + subject_cols_sorted + summary_cols
        df = df[final_column_order]

    # Write to Excel and apply styling via openpyxl
    with pd.ExcelWriter(filepath, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Master_Marks", index=False)
        ws = writer.sheets["Master_Marks"]
        
        # Freeze panes so row 1 stays visible
        ws.freeze_panes = "A2"
        ws.row_dimensions[1].height = 26

        # Format Headers
        for cell in ws[1]:
            cell.font = _HEADER_FONT
            cell.fill = _HEADER_FILL
            cell.alignment = _ALIGN_CENTER
            cell.border = _BORDER_THIN

        # Format Data Cells
        num_rows = len(df)
        num_cols = len(df.columns)
        
        col_names = list(df.columns)
        for r_idx in range(2, num_rows + 2):
            ws.row_dimensions[r_idx].height = 20
            row_fill = _ZEBRA_FILL if r_idx % 2 == 0 else _WHITE_FILL
            for c_idx in range(1, num_cols + 1):
                cell = ws.cell(row=r_idx, column=c_idx)
                cell.border = _BORDER_THIN
                cell.fill = row_fill
                
                col_name = col_names[c_idx - 1]
                val = cell.value
                
                # Alignment rules
                if col_name in ["Regd No", "Section"]:
                    cell.alignment = _ALIGN_CENTER
                elif col_name in ["Name", "Student Name"]:
                    cell.alignment = _ALIGN_LEFT
                else:
                    # Marks columns
                    if isinstance(val, (int, float)) or (isinstance(val, str) and val.replace(".", "", 1).isdigit()):
                        cell.alignment = _ALIGN_RIGHT
                    else:
                        cell.alignment = _ALIGN_CENTER

        # Auto-fit Column Widths
        for col_cells in ws.columns:
            max_len = 0
            col_letter = get_column_letter(col_cells[0].column)
            for cell in col_cells:
                try:
                    txt = str(cell.value) if cell.value is not None else ""
                    if len(txt) > max_len:
                        max_len = len(txt)
                except Exception:
                    pass
            ws.column_dimensions[col_letter].width = max(min(max_len + 4, 45), 11)

    logger.info("[EXPORTER] Successfully exported Master Excel: %s (%d rows)", filepath, len(df))
    return filepath
