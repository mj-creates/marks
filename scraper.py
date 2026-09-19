"""
scraper.py — Semester Marks Downloader
========================================
For a given semester this module:
  1. Logs in to that semester's sub-site  (separate login per sem)
  2. GETs examhome.jsp  (sets referrer/session state the portal may need)
  3. GETs RSMSubAll.jsp to dynamically read the subjectcode1 dropdown
     (these are the sections available to this faculty)
  4. POSTs the form once per section with excel=YES to download each
     section's Excel binary
  5. Reads each Excel into a list of row-dicts using pandas
  6. Merges all sections into one flat list

Confirmed form fields (browser network payload):
    coursecode   = A          (B.Tech — fixed)
    branchcode   = 04         (CSE    — fixed)
    cyear        = 1–4        (derived: sem 1-2→1, 3-4→2, 5-6→3, 7-8→4)
    semester     = 1–8
    subjectcode1 = <numeric>  (section ID — fetched dynamically per faculty)
    excel        = YES
    next         = submit

Usage:
    from scraper import SemesterScraper
    from auth import PortalAuth

    auth    = PortalAuth()
    scraper = SemesterScraper(auth)

    # Fetch available sections for sem 1 of 2020 batch
    sections = scraper.get_available_sections(admission_year=2020, semester=1)
    # e.g. [{"label": "11", "value": "11"}, {"label": "15", "value": "15"}, ...]

    # Scrape ALL sections for semester 1 of 2020 batch
    records = scraper.scrape_all_sections(
        admission_year=2020,
        semester=1,
        progress_callback=lambda sec_idx, total: print(f"section {sec_idx}/{total}")
    )
"""

import io
import logging
from typing import Callable

import pandas as pd
import requests
from bs4 import BeautifulSoup

from auth import PortalAuth, AuthError
from url_mapper import get_semester_urls, get_examhome_url, sem_to_cyear

logger = logging.getLogger(__name__)

# ── Fixed portal form values ───────────────────────────────────────────────
_COURSE_CODE = "A"    # B.Tech
_BRANCH_CODE = "04"   # CSE


class SemesterScraper:
    """
    Downloads and parses marks data for all sections of a given semester.

    One login per semester sub-site. After login, reads the subjectcode1
    dropdown to discover all sections assigned to this faculty, then POSTs
    the form once per section and merges results into one flat list.
    """

    def __init__(self, auth: PortalAuth):
        self.auth = auth

    # ── Public API ─────────────────────────────────────────────────────────

    def get_available_sections(
        self, admission_year: int, semester: int
    ) -> list[dict]:
        """
        Log in to a semester sub-site and scrape the subjectcode1 dropdown
        to discover which sections are available for this faculty.

        Args:
            admission_year: 4-digit batch year e.g. 2020
            semester:       Semester number 1–8

        Returns:
            List of dicts: [{"label": "11", "value": "11"}, ...]
            Returns [] if login fails or dropdown cannot be found.
        """
        try:
            session = self.auth.login(admission_year, semester)
        except AuthError as exc:
            logger.error("Cannot fetch sections — login failed: %s", exc)
            return []

        sem_urls = get_semester_urls(admission_year, self.auth.base_host)
        marks_url = sem_urls[semester]

        try:
            examhome_url = get_examhome_url(
                admission_year, semester, self.auth.base_host
            )
            session.get(examhome_url, timeout=30)

            resp = session.get(marks_url, timeout=30)
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "lxml")
            select = soup.find("select", {"name": "subjectcode1"})
            if not select:
                logger.warning(
                    "subjectcode1 dropdown not found on %s", marks_url
                )
                return []

            options = []
            for opt in select.find_all("option"):
                val = opt.get("value", "").strip()
                label = opt.get_text(strip=True)
                if val:
                    options.append({"label": label, "value": val})

            logger.info(
                "Found %d section(s) for year %d sem %d: %s",
                len(options),
                admission_year,
                semester,
                [o["label"] for o in options],
            )
            return options

        except Exception as exc:
            logger.warning("Could not fetch section list: %s", exc, exc_info=True)
            return []

    def scrape_all_sections(
        self,
        admission_year: int,
        semester: int,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[dict]:
        """
        Scrape marks for ALL sections of a single semester and return a
        flat list of records (one master dataset for that semester).

        Args:
            admission_year:     4-digit batch year e.g. 2020
            semester:           Semester number 1–8
            progress_callback:  Optional callable(current_idx, total_sections)

        Returns:
            Flat list of record dicts across all sections.
            Each record has a 'Section' key with the subjectcode1 label.
        """
        # One login gives us the session + the section list
        try:
            session = self.auth.login(admission_year, semester)
        except AuthError as exc:
            logger.error("Login failed for sem %d: %s", semester, exc)
            return []

        sem_urls = get_semester_urls(admission_year, self.auth.base_host)
        marks_url = sem_urls[semester]

        # Visit examhome first
        try:
            examhome_url = get_examhome_url(
                admission_year, semester, self.auth.base_host
            )
            session.get(examhome_url, timeout=30)
        except Exception:
            pass

        # Read available sections from the form dropdown
        sections = self._read_sections_from_form(session, marks_url, semester)
        if not sections:
            logger.warning("No sections found for sem %d — nothing to scrape", semester)
            return []

        all_records: list[dict] = []
        total = len(sections)

        for idx, sec in enumerate(sections, start=1):
            sec_label = sec["label"]
            sec_value = sec["value"]
            logger.info(
                "Section %d/%d (subjectcode1=%s) …", idx, total, sec_label
            )

            excel_bytes = self._post_marks_form(
                session, marks_url, semester, sec_value
            )

            if excel_bytes is None:
                logger.warning("  Section %s — no data", sec_label)
            else:
                records = self._parse_excel(excel_bytes, semester, sec_label)
                logger.info("  Section %s — %d rows", sec_label, len(records))
                all_records.extend(records)

            if progress_callback:
                progress_callback(idx, total)

        return all_records

    def scrape_single_section(
        self,
        admission_year: int,
        semester: int,
        section_value: str,
        section_label: str,
    ) -> list[dict]:
        """
        Download marks for one specific section of one semester.
        Useful if the faculty wants to re-run just one section.
        Returns [] on any failure.
        """
        try:
            session = self.auth.login(admission_year, semester)
        except AuthError as exc:
            logger.warning("Sem %d login failed: %s", semester, exc)
            return []

        sem_urls = get_semester_urls(admission_year, self.auth.base_host)
        marks_url = sem_urls[semester]

        try:
            examhome_url = get_examhome_url(
                admission_year, semester, self.auth.base_host
            )
            session.get(examhome_url, timeout=30)

            excel_bytes = self._post_marks_form(
                session, marks_url, semester, section_value
            )
            if excel_bytes is None:
                return []
            return self._parse_excel(excel_bytes, semester, section_label)

        except Exception as exc:
            logger.warning("Sem %d sec %s error: %s", semester, section_label, exc, exc_info=True)
            return []

    # ── Private helpers ────────────────────────────────────────────────────

    def _read_sections_from_form(
        self,
        session: requests.Session,
        marks_url: str,
        semester: int,
    ) -> list[dict]:
        """GET the marks form page and extract subjectcode1 options."""
        try:
            resp = session.get(marks_url, timeout=30)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")
            select = soup.find("select", {"name": "subjectcode1"})
            if not select:
                logger.warning("subjectcode1 dropdown not found on %s", marks_url)
                return []
            options = []
            for opt in select.find_all("option"):
                val = opt.get("value", "").strip()
                label = opt.get_text(strip=True)
                if val:
                    options.append({"label": label, "value": val})
            return options
        except Exception as exc:
            logger.warning("Sem %d — could not read section dropdown: %s", semester, exc)
            return []

    def _post_marks_form(
        self,
        session: requests.Session,
        url: str,
        semester: int,
        section_value: str,
    ) -> bytes | None:
        """
        POST the RSMSubAll form and return the raw Excel file bytes.
        Returns None if the response is not a valid spreadsheet.
        """
        payload = {
            "coursecode":   _COURSE_CODE,
            "branchcode":   _BRANCH_CODE,
            "cyear":        str(sem_to_cyear(semester)),
            "semester":     str(semester),
            "subjectcode1": section_value,
            "excel":        "YES",
            "next":         "submit",
        }

        logger.debug("POST %s  payload=%s", url, payload)

        try:
            resp = session.post(url, data=payload, timeout=60)
        except requests.RequestException as exc:
            logger.warning("Sem %d — POST failed: %s", semester, exc)
            return None

        if resp.status_code != 200:
            logger.warning(
                "Sem %d — HTTP %d from %s", semester, resp.status_code, url
            )
            return None

        content_type = resp.headers.get("Content-Type", "").lower()
        if "html" in content_type:
            # Derive subsite base robustly from the URL
            from urllib.parse import urlparse
            parsed = urlparse(url)
            # path is like /a20201/RSMSubAll.jsp — take up to the second slash
            parts = parsed.path.strip("/").split("/")
            subsite_path = "/" + parts[0] if parts else "/"
            subsite_base = f"{parsed.scheme}://{parsed.netloc}{subsite_path}"

            if self.auth._is_login_page(resp, subsite_base):
                logger.warning("Sem %d — session expired mid-run", semester)
            else:
                logger.warning(
                    "Sem %d — portal returned HTML instead of Excel "
                    "(data may not be available yet)",
                    semester,
                )
            return None

        content = resp.content
        if len(content) < 512:
            logger.warning(
                "Sem %d — response too small (%d bytes), likely empty",
                semester, len(content)
            )
            return None

        logger.debug("Sem %d — received %d bytes", semester, len(content))
        return content

    def _parse_excel(
        self, excel_bytes: bytes, semester: int, section_label: str
    ) -> list[dict]:
        """
        Parse the Excel binary into a list of dicts.
        Adds 'Semester' and 'Section' columns to every record.
        """
        for engine in ("openpyxl", "xlrd"):
            try:
                df = pd.read_excel(
                    io.BytesIO(excel_bytes),
                    engine=engine,
                    dtype=str,
                    header=0,
                )
                break
            except Exception:
                continue
        else:
            logger.warning("Sem %d sec %s — could not parse Excel", semester, section_label)
            return []

        if df.empty:
            return []

        df.columns = [str(c).strip() for c in df.columns]
        df.dropna(how="all", inplace=True)
        df.fillna("", inplace=True)

        # Prepend Semester and Section columns
        df.insert(0, "Section", section_label)
        df.insert(0, "Semester", semester)

        return df.to_dict(orient="records")
