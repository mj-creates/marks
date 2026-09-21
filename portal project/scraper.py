"""
scraper.py — Section Marks Scraper and Dynamic Subject Alignment Engine
========================================================================
Handles:
  1. Portal session navigation: login -> examhome.jsp -> RSMSubAll.jsp
  2. Automatic section discovery for the given semester/study year
  3. Form submission per section (WITHOUT excel checkbox to retrieve HTML table)
  4. Robust 2D grid table parsing that resolves multi-tier headers (colspan/rowspan)
  5. Dynamic subject alignment: prevents misaligning subjects like EPCS and BEEE
     when sections have different subject sequences
  6. Alphanumeric Regd No extraction and normalization (e.g., 201FA04001)
"""

import logging
import re
from urllib.parse import urlparse
from typing import Callable, Optional, List, Dict, Tuple, Any

import requests
from bs4 import BeautifulSoup

from auth import PortalAuth, AuthError
from url_mapper import get_subsite_url, get_examhome_url, overall_semester_number

logger = logging.getLogger(__name__)

_COURSE_CODE = "A"   # B.Tech
_BRANCH_CODE = "04"  # CSE

# Pattern for Vignan registration numbers: e.g. 201FA04001, 211FA04001, 221FA04001
REGD_PATTERN = re.compile(r'\b\d{2}[0-9A-Za-z]{8}\b', re.IGNORECASE)
FALLBACK_REGD_PATTERN = re.compile(r'\b\d{2}[0-9A-Za-z]{2,3}[0-9A-Za-z]{4,6}\b', re.IGNORECASE)


def parse_marks_html(html: str, study_year: int = 1, sem_digit: int = 1, section_label: str = "") -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Parse a section marks HTML response into structured records with canonical subject columns.
    
    Uses 2D grid matrix expansion to resolve multi-level headers with colspan/rowspan.
    Returns:
        records: list of dicts, each representing one student with all their subject marks.
        column_headers: list of resolved column names.
    """
    soup = BeautifulSoup(html, "lxml")
    
    # 1. Locate the marks table
    table = None
    all_tables = soup.find_all("table")
    for t in all_tables:
        text = t.get_text(separator=" ").lower()
        if "regd" in text or "roll" in text or "htno" in text or REGD_PATTERN.search(text):
            table = t
            break
            
    if table is None:
        if all_tables:
            table = max(all_tables, key=lambda tbl: len(tbl.find_all("tr")))
        else:
            logger.warning("[SCRAPER] No table found in HTML response")
            return [], []
            
    rows = table.find_all("tr")
    if len(rows) < 2:
        logger.warning("[SCRAPER] Table has insufficient rows (%d)", len(rows))
        return [], []
        
    # 2. Identify the first data row by scanning for a registration number
    first_data_idx = None
    for idx, r in enumerate(rows):
        cells = r.find_all(["td", "th"])
        for cell in cells:
            txt = cell.get_text(strip=True)
            if REGD_PATTERN.search(txt) or FALLBACK_REGD_PATTERN.search(txt):
                first_data_idx = idx
                break
        if first_data_idx is not None:
            break
            
    if first_data_idx is None:
        # Fallback: check if row 1 or 2 contains digits in cell 0 (S.No)
        for idx in range(1, min(4, len(rows))):
            cells = rows[idx].find_all(["td", "th"])
            if cells and cells[0].get_text(strip=True).isdigit():
                first_data_idx = idx
                break
                
    if first_data_idx is None or first_data_idx == 0:
        first_data_idx = 1
        
    header_rows = rows[:first_data_idx]
    if not header_rows:
        header_rows = [rows[0]]
        first_data_idx = 1
        
    # 3. Build 2D Grid of Headers resolving colspan and rowspan
    H = len(header_rows)
    grid: List[List[Optional[str]]] = [[] for _ in range(H)]
    
    for r_idx in range(H):
        col_idx = 0
        for cell in header_rows[r_idx].find_all(["th", "td"]):
            while col_idx < len(grid[r_idx]) and grid[r_idx][col_idx] is not None:
                col_idx += 1
                
            rowspan = 1
            colspan = 1
            try:
                rowspan = int(cell.get("rowspan", 1))
            except (ValueError, TypeError):
                pass
            try:
                colspan = int(cell.get("colspan", 1))
            except (ValueError, TypeError):
                pass
                
            text = cell.get_text(strip=True).replace("\xa0", " ")
            
            for r in range(H):
                while len(grid[r]) < col_idx + colspan:
                    grid[r].append(None)
                    
            for dr in range(rowspan):
                if r_idx + dr < H:
                    for dc in range(colspan):
                        grid[r_idx + dr][col_idx + dc] = text
            col_idx += colspan

    W = max(len(r) for r in grid) if grid else 0
    column_headers = []
    
    for c in range(W):
        parts = []
        for r in range(H):
            val = grid[r][c] if c < len(grid[r]) else None
            if val and (not parts or val != parts[-1]):
                parts.append(val)
        col_name = " - ".join(parts).strip() if parts else f"Col_{c+1}"
        
        # Normalize core identity headers
        c_lower = col_name.lower()
        if any(k in c_lower for k in ["regd", "roll", "htno", "h.t.no"]) and not any(k in c_lower for k in ["int", "ext", "tot", "mid", "grade"]):
            col_name = "Regd No"
        elif any(k in c_lower for k in ["student name", "candidate name", "name of the student"]) or (c_lower == "name"):
            col_name = "Student Name"
        elif c_lower in ["s.no", "sl.no", "sno", "si.no", "s. no", "sl no"]:
            col_name = "S.No"
            
        column_headers.append(col_name)
        
    # Ensure unique column names if duplicates exist
    seen_cols: Dict[str, int] = {}
    unique_headers = []
    for col in column_headers:
        if col not in seen_cols:
            seen_cols[col] = 1
            unique_headers.append(col)
        else:
            seen_cols[col] += 1
            unique_headers.append(f"{col}_{seen_cols[col]}")
    column_headers = unique_headers

    # 4. Parse Student Data Rows
    records = []
    sem_label = f"Year {study_year} Sem {sem_digit}"
    overall = overall_semester_number(study_year, sem_digit)
    
    for r in rows[first_data_idx:]:
        cells = r.find_all(["td", "th"])
        if not cells:
            continue
        texts = [c.get_text(strip=True).replace("\xa0", " ") for c in cells]
        if all(t == "" for t in texts):
            continue
            
        row_dict: Dict[str, Any] = {}
        for c_idx, val in enumerate(texts):
            if c_idx < len(column_headers):
                row_dict[column_headers[c_idx]] = val
                
        # Locate Regd No
        regd_val = row_dict.get("Regd No", "").strip().upper()
        if not regd_val or not (REGD_PATTERN.search(regd_val) or FALLBACK_REGD_PATTERN.search(regd_val)):
            for v in texts:
                m = REGD_PATTERN.search(v) or FALLBACK_REGD_PATTERN.search(v)
                if m:
                    regd_val = m.group(0).upper()
                    row_dict["Regd No"] = regd_val
                    break
                    
        # Filter for valid students
        if regd_val and (REGD_PATTERN.search(regd_val) or FALLBACK_REGD_PATTERN.search(regd_val)):
            row_dict["Regd No"] = regd_val
            row_dict["Section"] = section_label
            row_dict["Semester"] = sem_label
            row_dict["Overall Sem"] = overall
            records.append(row_dict)
            
    logger.info("[SCRAPER] Parsed %d data rows for section %s", len(records), section_label)
    return records, column_headers


class SemesterScraper:
    """
    Orchestrates portal scraping for all sections of a given semester.
    """

    def __init__(self, auth: PortalAuth):
        self.auth = auth

    def get_available_sections(self, admission_year: int, study_year: int, sem_digit: int) -> List[Dict[str, str]]:
        """Login and query subjectcode1 section options on RSMSubAll.jsp."""
        session = self._login_and_navigate(admission_year, study_year, sem_digit)
        if session is None:
            return []
        marks_url = get_subsite_url(admission_year, study_year, sem_digit, self.auth.base_host)
        examhome_url = get_examhome_url(admission_year, study_year, sem_digit, self.auth.base_host)
        return self._read_sections_from_form(session, marks_url, examhome_url, study_year, sem_digit)

    def scrape_all_sections(
        self,
        admission_year: int,
        study_year: int,
        sem_digit: int,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Main entry point:
          1. Log into sub-site
          2. Discover all sections
          3. Query each section's HTML table
          4. Parse and align subject columns
          5. Return flat list of all records across sections
        """
        session = self._login_and_navigate(admission_year, study_year, sem_digit)
        if session is None:
            return []

        marks_url = get_subsite_url(admission_year, study_year, sem_digit, self.auth.base_host)
        examhome_url = get_examhome_url(admission_year, study_year, sem_digit, self.auth.base_host)
        sections = self._read_sections_from_form(session, marks_url, examhome_url, study_year, sem_digit)

        if not sections:
            logger.warning("[SCRAPER] No sections found for Y%dS%d", study_year, sem_digit)
            return []

        all_records = []
        total = len(sections)
        for idx, sec in enumerate(sections, start=1):
            sec_val = sec["value"]
            sec_label = sec["label"]
            logger.info("[SCRAPER] Fetching section %d/%d (%s)", idx, total, sec_label)
            
            records = self._scrape_single_section_html(
                session, marks_url, examhome_url, study_year, sem_digit, sec_val, sec_label
            )
            if records:
                logger.info("  -> Section %s yielded %d student rows", sec_label, len(records))
                all_records.extend(records)
            else:
                logger.warning("  -> Section %s returned no records", sec_label)
                
            if progress_callback:
                progress_callback(idx, total, sec_label)

        logger.info("[SCRAPER] Completed scraping all %d sections: %d total student rows", total, len(all_records))
        return all_records

    def _login_and_navigate(self, admission_year: int, study_year: int, sem_digit: int) -> Optional[requests.Session]:
        """Perform login and navigate through examhome to establish session."""
        try:
            session = self.auth.login(admission_year, study_year, sem_digit)
        except AuthError as exc:
            logger.error("[SCRAPER] Authentication failed Y%dS%d: %s", study_year, sem_digit, exc)
            return None

        base_host = self.auth.base_host
        subsite_base = get_subsite_url(admission_year, study_year, sem_digit, base_host, page="").rstrip("/")
        examhome_url = get_examhome_url(admission_year, study_year, sem_digit, base_host)

        # GET examhome.jsp with Referer
        try:
            resp = session.get(examhome_url, headers={"Referer": f"{subsite_base}/login.jsp"}, timeout=30)
            logger.debug("[SCRAPER] examhome GET status: %d", resp.status_code)
        except Exception as exc:
            logger.warning("[SCRAPER] Could not visit examhome.jsp: %s", exc)

        return session

    def _read_sections_from_form(
        self, session: requests.Session, marks_url: str, examhome_url: str, study_year: int, sem_digit: int
    ) -> List[Dict[str, str]]:
        """Fetch RSMSubAll.jsp and parse all section options from subjectcode1."""
        try:
            resp = session.get(marks_url, headers={"Referer": examhome_url}, timeout=30)
            if resp.url.lower().endswith("login.jsp"):
                logger.warning("[SCRAPER] Session redirect to login.jsp on RSMSubAll")
                return []

            soup = BeautifulSoup(resp.text, "lxml")
            select = soup.find("select", {"name": "subjectcode1"})
            
            options = []
            if select:
                for opt in select.find_all("option"):
                    val = opt.get("value", "").strip()
                    label = opt.get_text(strip=True)
                    if val:
                        options.append({"label": label or val, "value": val})

            # If no options found on GET, try POSTing Course & Branch to trigger dropdown population
            if not options:
                logger.info("[SCRAPER] No sections on GET, submitting Course/Branch/Year/Sem to populate...")
                populate_payload = {
                    "coursecode": _COURSE_CODE,
                    "branchcode": _BRANCH_CODE,
                    "cyear": str(study_year),
                    "semester": str(sem_digit),
                    "next": "submit"
                }
                resp_pop = session.post(marks_url, data=populate_payload, headers={"Referer": marks_url}, timeout=30)
                soup_pop = BeautifulSoup(resp_pop.text, "lxml")
                select_pop = soup_pop.find("select", {"name": "subjectcode1"})
                if select_pop:
                    for opt in select_pop.find_all("option"):
                        val = opt.get("value", "").strip()
                        label = opt.get_text(strip=True)
                        if val:
                            options.append({"label": label or val, "value": val})

            logger.info("[SCRAPER] Found %d section(s) for Y%dS%d: %s",
                        len(options), study_year, sem_digit, [o["label"] for o in options])
            return options
        except Exception as exc:
            logger.warning("[SCRAPER] Error reading sections: %s", exc, exc_info=True)
            return []

    def _scrape_single_section_html(
        self,
        session: requests.Session,
        marks_url: str,
        examhome_url: str,
        study_year: int,
        sem_digit: int,
        section_value: str,
        section_label: str,
    ) -> List[Dict[str, Any]]:
        """Submit the marks form without excel checkbox to receive the HTML table."""
        payload = {
            "coursecode": _COURSE_CODE,
            "branchcode": _BRANCH_CODE,
            "cyear": str(study_year),
            "semester": str(sem_digit),
            "subjectcode1": section_value,
            # No 'excel' parameter so portal renders the full HTML table
            "next": "submit",
        }
        
        try:
            resp = session.post(
                marks_url,
                data=payload,
                headers={"Referer": marks_url},
                timeout=60,
            )
        except requests.RequestException as exc:
            logger.warning("[SCRAPER] POST failed for sec %s: %s", section_label, exc)
            return []

        if resp.status_code != 200:
            logger.warning("[SCRAPER] HTTP %d for sec %s", resp.status_code, section_label)
            return []

        if resp.url.lower().endswith("login.jsp"):
            logger.warning("[SCRAPER] Session expired during sec %s POST", section_label)
            return []

        records, _ = parse_marks_html(resp.text, study_year, sem_digit, section_label)
        return records