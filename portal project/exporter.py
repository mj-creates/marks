"""
exporter.py — Master Excel Exporter with Intelligent Column Ordering
====================================================================
Generates a polished, professional multi-sheet Master Excel file
containing all students sorted by Registration Number:
  - Sheet 1: Master (All Subjects) with 2-tier Hierarchical Header (Subject in Row 1, Assessment in Row 2)
  - Sheets 2..8: Individual Subject Tabs matching Image 1
  - Sheet 9: Master (Single Header) for easy scripting/analysis
"""

import logging
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

import pandas as pd
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from scraper import clean_subject_header

logger = logging.getLogger(__name__)

# Styling Constants
_HEADER_FILL = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
_SUB_HEADER_FILL = PatternFill(start_color="285E8E", end_color="285E8E", fill_type="solid")
_HEADER_FONT = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
_SUB_HEADER_FONT = Font(name="Calibri", size=10, bold=True, color="FFFFFF")

_ZEBRA_FILL = PatternFill(start_color="F2F4F8", end_color="F2F4F8", fill_type="solid")
_WHITE_FILL = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")
_DATA_FONT  = Font(name="Calibri", size=10, bold=False, color="000000")

_BORDER_THIN = Border(
    left=Side(style="thin", color="D9D9D9"),
    right=Side(style="thin", color="D9D9D9"),
    top=Side(style="thin", color="D9D9D9"),
    bottom=Side(style="thin", color="D9D9D9"),
)

_ALIGN_CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
_ALIGN_LEFT   = Alignment(horizontal="left", vertical="center")
_ALIGN_RIGHT  = Alignment(horizontal="right", vertical="center")


def _clean_comp_name(raw_comp: str) -> str:
    """Format assessment components into clean, standardized title like 'Week Test-1 Max:10'."""
    comp = raw_comp.strip()
    comp = re.sub(r'Max\s*(\d+)', r'Max:\1', comp, flags=re.I)
    comp = re.sub(r'\bweek test', 'Week Test', comp, flags=re.I)
    comp = re.sub(r'\bmid', 'Mid', comp, flags=re.I)
    comp = re.sub(r'\bexperiment', 'Experiment', comp, flags=re.I)
    comp = re.sub(r'\binternal lab', 'Internal Lab', comp, flags=re.I)
    comp = re.sub(r'\bminor project', 'Minor Project', comp, flags=re.I)
    comp = re.sub(r'(\d)(Max:)', r'\1 \2', comp)
    return comp


def _natural_sort_key(s: str) -> list:
    """Natural alphanumeric sort key (e.g. Experiment-1 before Experiment-10)."""
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', str(s))]


def _style_merged_range(ws, min_r, min_c, max_r, max_c, font=None, fill=None, align=None, border=None):
    for r in range(min_r, max_r + 1):
        for c in range(min_c, max_c + 1):
            cell = ws.cell(row=r, column=c)
            if font: cell.font = font
            if fill: cell.fill = fill
            if align: cell.alignment = align
            if border: cell.border = border


def _build_hierarchical_master(ws, df_master: pd.DataFrame, subject_map: Dict[str, List[Tuple[str, str]]]):
    """Creates a 2-tier Hierarchical Header matching Image 1 for all subjects."""
    ws.row_dimensions[1].height = 26
    ws.row_dimensions[2].height = 28

    # 1. Primary Identifiers (merged A1:A2, B1:B2, C1:C2)
    col_id_labels = [(1, "REGD.NO"), (2, "NAME"), (3, "SECTION")]
    for col_idx, label in col_id_labels:
        ws.merge_cells(start_row=1, start_column=col_idx, end_row=2, end_column=col_idx)
        ws.cell(row=1, column=col_idx, value=label)
        _style_merged_range(ws, 1, col_idx, 2, col_idx, font=_HEADER_FONT, fill=_HEADER_FILL, align=_ALIGN_CENTER, border=_BORDER_THIN)

    # 2. Subject Header Grouping
    current_col = 4
    for subj in sorted(subject_map.keys(), key=_natural_sort_key):
        items = subject_map[subj]
        start_col = current_col
        end_col = current_col + len(items) - 1

        ws.merge_cells(start_row=1, start_column=start_col, end_row=1, end_column=end_col)
        ws.cell(row=1, column=start_col, value=subj.upper())
        _style_merged_range(ws, 1, start_col, 1, end_col, font=_HEADER_FONT, fill=_HEADER_FILL, align=_ALIGN_CENTER, border=_BORDER_THIN)

        for raw_u, comp_clean in items:
            cell_r2 = ws.cell(row=2, column=current_col, value=comp_clean)
            cell_r2.font = _SUB_HEADER_FONT
            cell_r2.fill = _SUB_HEADER_FILL
            cell_r2.alignment = _ALIGN_CENTER
            cell_r2.border = _BORDER_THIN
            current_col += 1

    # 3. Data Rows
    num_rows = len(df_master)
    for row_idx, row_data in enumerate(df_master.itertuples(index=False), start=3):
        ws.row_dimensions[row_idx].height = 20
        row_fill = _WHITE_FILL if (row_idx % 2 == 1) else _ZEBRA_FILL

        row_vals = ["" if pd.isna(v) else v for v in row_data]
        for col_idx, val in enumerate(row_vals, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.fill = row_fill
            cell.font = _DATA_FONT
            cell.border = _BORDER_THIN

            if col_idx == 1:
                cell.alignment = _ALIGN_CENTER
            elif col_idx == 2:
                cell.alignment = _ALIGN_LEFT
            elif col_idx == 3:
                cell.alignment = _ALIGN_CENTER
            else:
                if isinstance(val, (int, float)):
                    cell.alignment = _ALIGN_RIGHT
                else:
                    cell.alignment = _ALIGN_CENTER

    ws.freeze_panes = "D3"

    ws.column_dimensions["A"].width = 16
    ws.column_dimensions["B"].width = 30
    ws.column_dimensions["C"].width = 12

    col_idx = 4
    for subj in sorted(subject_map.keys(), key=_natural_sort_key):
        for raw_u, comp_clean in subject_map[subj]:
            col_letter = get_column_letter(col_idx)
            hdr_len = len(comp_clean)
            sample_lens = [len(str(ws.cell(row=r, column=col_idx).value or '')) for r in range(3, min(num_rows + 3, 25))]
            max_data = max(sample_lens) if sample_lens else 0
            ws.column_dimensions[col_letter].width = max(12, min(36, max(hdr_len, max_data) + 4))
            col_idx += 1


def _style_single_sheet(ws, df: pd.DataFrame):
    """Standard single-header sheet styling."""
    headers = list(df.columns)
    ws.append(headers)
    ws.row_dimensions[1].height = 32

    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = _ALIGN_CENTER
        cell.border = _BORDER_THIN

    for row_idx, row_data in enumerate(df.itertuples(index=False), start=2):
        ws.row_dimensions[row_idx].height = 20
        row_fill = _WHITE_FILL if (row_idx % 2 == 0) else _ZEBRA_FILL

        row_vals = ["" if pd.isna(v) else v for v in row_data]
        ws.append(row_vals)

        for col_idx, val in enumerate(row_vals, start=1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.fill = row_fill
            cell.font = _DATA_FONT
            cell.border = _BORDER_THIN

            if col_idx == 1:
                cell.alignment = _ALIGN_CENTER
            elif col_idx == 2:
                cell.alignment = _ALIGN_LEFT
            elif col_idx == 3:
                cell.alignment = _ALIGN_CENTER
            else:
                if isinstance(val, (int, float)):
                    cell.alignment = _ALIGN_RIGHT
                else:
                    cell.alignment = _ALIGN_CENTER

    ws.freeze_panes = "D2"

    for col_idx, col_name in enumerate(headers, start=1):
        col_letter = get_column_letter(col_idx)
        if col_name == "REGD.NO":
            ws.column_dimensions[col_letter].width = 16
        elif col_name == "NAME":
            ws.column_dimensions[col_letter].width = 30
        elif col_name == "SECTION":
            ws.column_dimensions[col_letter].width = 12
        else:
            hdr_len = len(str(col_name))
            sample_lens = [len(str(ws.cell(row=r, column=col_idx).value or '')) for r in range(2, min(len(df) + 2, 25))]
            max_data = max(sample_lens) if sample_lens else 0
            ws.column_dimensions[col_letter].width = max(12, min(36, max(hdr_len, max_data) + 4))


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
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Master (All Subjects)"
        df_empty = pd.DataFrame(columns=["REGD.NO", "NAME", "SECTION", "STATUS"])
        df_empty.loc[0] = ["-", "No records found", "-", "No Data"]
        _style_single_sheet(ws, df_empty)
        wb.save(filepath)
        return filepath

    df_raw = pd.DataFrame(records)

    # 1. Primary Identifiers Identification
    regd_col = next((c for c in df_raw.columns if "regd" in str(c).lower() or "htno" in str(c).lower()), "Regd No")
    sec_col = next((c for c in df_raw.columns if "section" == str(c).strip().lower() or "sec" == str(c).strip().lower()), "Section")
    
    # Name columns across all sections
    name_cols = [c for c in df_raw.columns if "NAME" in str(c).upper()]
    
    def extract_name(row):
        for c in name_cols:
            val = row.get(c)
            if pd.notna(val) and str(val).strip():
                return str(val).strip().upper()
        return ""

    name_series = df_raw.apply(extract_name, axis=1) if name_cols else (df_raw["Name"].astype(str).str.upper() if "Name" in df_raw.columns else pd.Series([""] * len(df_raw)))

    # 2. Header Unification: strip section prefixes
    prefix_regex = re.compile(r'^VFSTR\s*B\.?\s*TECH.*?\d+[-_ ]Section\s*[-_ ]*', re.I)
    ignored_cols = set([regd_col, sec_col, "Overall - EXT", "overall - ext"] + name_cols)

    col_mapping = {}
    for col in df_raw.columns:
        if col in ignored_cols:
            continue
        clean_hdr = prefix_regex.sub('', str(col)).strip()
        if clean_hdr and clean_hdr.lower() not in ["s.no", "sl.no", "sno", "semester", "overall sem", "status", "name", "regd no", "section"]:
            col_mapping[col] = clean_hdr

    raw_unified_headers = sorted(list(set(col_mapping.values())), key=_natural_sort_key)

    # Merge marks per unified header across sections
    unified_values = {}
    for u_hdr in raw_unified_headers:
        matching_cols = [c for c, u in col_mapping.items() if u == u_hdr]
        sub_df = df_raw[matching_cols]
        unified_values[u_hdr] = sub_df.bfill(axis=1).iloc[:, 0]

    # Organize by Subject
    subject_map: Dict[str, List[Tuple[str, str]]] = {}
    for u_hdr in raw_unified_headers:
        parts = u_hdr.split(' ', 1)
        subj = parts[0]
        comp_raw = parts[1] if len(parts) > 1 else ""
        comp_clean = _clean_comp_name(comp_raw)
        if subj not in subject_map:
            subject_map[subj] = []
        subject_map[subj].append((u_hdr, comp_clean))

    # Build Master DataFrame
    master_dict = {
        "REGD.NO": df_raw[regd_col].astype(str).str.strip().str.upper() if regd_col in df_raw.columns else "",
        "NAME": name_series,
        "SECTION": df_raw[sec_col].astype(str).str.strip() if sec_col in df_raw.columns else "",
    }
    
    for subj in sorted(subject_map.keys(), key=_natural_sort_key):
        for raw_u, comp_clean in subject_map[subj]:
            col_label = f"{subj} - {comp_clean}"
            master_dict[col_label] = unified_values[raw_u]

    df_master = pd.DataFrame(master_dict)
    df_master = df_master.sort_values(by="REGD.NO", ascending=True).reset_index(drop=True)

    # Create Multi-Sheet Workbook
    wb = openpyxl.Workbook()
    wb.remove(wb.active)  # remove default sheet

    # 1. Primary Sheet: 2-tier Hierarchical Master
    ws_master = wb.create_sheet(title="Master (All Subjects)")
    _build_hierarchical_master(ws_master, df_master, subject_map)

    # 2. Individual Subject Tabs (looks exactly like Image 1)
    for subj in sorted(subject_map.keys(), key=_natural_sort_key):
        subj_tab_name = subj[:31]
        ws_subj = wb.create_sheet(title=subj_tab_name)

        subj_dict = {
            "REGD.NO": df_master["REGD.NO"],
            "NAME": df_master["NAME"],
            "SECTION": df_master["SECTION"],
        }
        for raw_u, comp_clean in subject_map[subj]:
            subj_dict[comp_clean] = unified_values[raw_u]

        df_subj = pd.DataFrame(subj_dict)
        mark_cols = [c for c in df_subj.columns if c not in ["REGD.NO", "NAME", "SECTION"]]
        has_marks = df_subj[mark_cols].notna().any(axis=1)
        df_subj_filtered = df_subj[has_marks].sort_values(by="REGD.NO").reset_index(drop=True)
        _style_single_sheet(ws_subj, df_subj_filtered)

    # 3. Flat Single-Row Master Tab
    ws_flat = wb.create_sheet(title="Master (Single Header)")
    _style_single_sheet(ws_flat, df_master)

    wb.save(filepath)

    # Also update deliverables
    master_copies = [
        os.path.join(output_dir, "Consolidated_Master_Marks.xlsx"),
        r"c:\Users\spjee\marks\Consolidated_Master_Marks.xlsx",
        os.path.expanduser(r"~\Downloads\Consolidated_Master_Marks.xlsx"),
    ]
    for c_path in master_copies:
        try:
            shutil.copyfile(filepath, c_path)
        except Exception:
            pass

    logger.info("[EXPORTER] Successfully exported Master Excel: %s (%d rows, %d sheets)", filepath, len(df_master), len(wb.sheetnames))
    return filepath
