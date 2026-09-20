"""
scraper.py — Semester Marks Downloader
========================================
Follows the exact same navigation path a human takes in the browser:

  1. POST login.jsp          → authenticated session
  2. GET  examhome.jsp       → portal home after login (parsed once, reused)
  3. Follow the Exams-norms menu link from the already-parsed page
  4. GET  RSMSubAll.jsp      → "Section Marks All Subjects" form
  5. Read subjectcode1 dropdown  → sections available to this faculty
  6. POST RSMSubAll.jsp once per section with excel=YES
  7. Parse the downloaded Excel binary with pandas
  8. Tag every row with Semester label + Section and return flat list

Confirmed form fields (browser network payload):
    coursecode   = A            (B.Tech — fixed)
    branchcode   = 04           (CSE    — fixed)
    cyear        = study_year   (1–4, directly — confirmed from portal)
    semester     = sem_digit    (1 or 2)
    subjectcode1 = <numeric>    (section ID — read dynamically from dropdown)
    excel        = YES
    next         = submit

URL formula:
    portal_year = admission_year + (study_year - 1)
    subsite     = a{portal_year}{sem_digit}
    e.g. 2020 batch, Y3 S1 → a20221/RSMSubAll.jsp
"""

import io
import logging
from urllib.parse import urlparse
from typing import Callable

import pandas as pd
import requests
from bs4 import BeautifulSoup

from auth import PortalAuth, AuthError
from url_mapper import (
    get_subsite_url,
    get_login_url,
    get_examhome_url,
    overall_semester_number,
)

logger = logging.getLogger(__name__)

# ── Fixed portal form values ───────────────────────────────────────────────
_COURSE_CODE = "A"    # B.Tech
_BRANCH_CODE = "04"   # CSE


class SemesterScraper:
    """
    Downloads and parses marks for all sections of a given study year + semester.

    Navigates the portal exactly as a human would:
      login → examhome → exams menu → RSMSubAll form → POST per section
    """

    def __init__(self, auth: PortalAuth):
        self.auth = auth

    # ── Public API ─────────────────────────────────────────────────────────

    def get_available_sections(
        self,
        admission_year: int,
        study_year: int,
        sem_digit: int,
    ) -> list[dict]:
        """
        Log in and navigate to RSMSubAll.jsp to read the section dropdown.

        Args:
            admission_year: 4-digit batch year e.g. 2020
            study_year:     1–4
            sem_digit:      1 or 2

        Returns:
            [{"label": "11", "value": "11"}, ...]  or [] on failure.
        """
        session = self._login_and_navigate(admission_year, study_year, sem_digit)
        if session is None:
            return []

        marks_url = get_subsite_url(admission_year, study_year, sem_digit, self.auth.base_host)
        return self._read_sections_from_form(session, marks_url, study_year, sem_digit)

    def scrape_all_sections(
        self,
        admission_year: int,
        study_year: int,
        sem_digit: int,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[dict]:
        """
        Log in once, navigate, then download marks for every section.

        Args:
            admission_year: 4-digit batch year e.g. 2020
            study_year:     1–4
            sem_digit:      1 or 2
            progress_callback: optional callable(current_idx, total_sections)

        Returns:
            Flat list of record dicts, one per student-subject row.
            Each record has 'Semester' (human label) and 'Section' columns.
        """
        session = self._login_and_navigate(admission_year, study_year, sem_digit)
        if session is None:
            return []

        marks_url = get_subsite_url(admission_year, study_year, sem_digit, self.auth.base_host)
        sections  = self._read_sections_from_form(session, marks_url, study_year, sem_digit)

        if not sections:
            logger.warning(
                "No sections found for Y%dS%d (batch %d)",
                study_year, sem_digit, admission_year
            )
            return []

        all_records: list[dict] = []
        total = len(sections)

        for idx, sec in enumerate(sections, start=1):
            sec_label = sec["label"]
            sec_value = sec["value"]
            logger.info(
                "Section %d/%d (subjectcode1=%s)…", idx, total, sec_label
            )

            excel_bytes = self._post_marks_form(
                session, marks_url, study_year, sem_digit, sec_value
            )

            if excel_bytes is None:
                logger.warning("  Section %s — no data returned", sec_label)
            else:
                records = self._parse_excel(
                    excel_bytes, study_year, sem_digit, sec_label
                )
                logger.info("  Section %s — %d rows", sec_label, len(records))
                all_records.extend(records)

            if progress_callback:
                progress_callback(idx, total)

        return all_records

    def scrape_single_section(
        self,
        admission_year: int,
        study_year: int,
        sem_digit: int,
        section_value: str,
        section_label: str,
    ) -> list[dict]:
        """Download marks for one specific section. Returns [] on any failure."""
        session = self._login_and_navigate(admission_year, study_year, sem_digit)
        if session is None:
            return []

        marks_url   = get_subsite_url(admission_year, study_year, sem_digit, self.auth.base_host)
        excel_bytes = self._post_marks_form(
            session, marks_url, study_year, sem_digit, section_value
        )
        if excel_bytes is None:
            return []
        return self._parse_excel(excel_bytes, study_year, sem_digit, section_label)

    # ── Navigation ─────────────────────────────────────────────────────────

    def _login_and_navigate(
        self,
        admission_year: int,
        study_year: int,
        sem_digit: int,
    ) -> requests.Session | None:
        """
        Full human navigation path. Returns authenticated session or None.

          1. POST login.jsp    → session cookie
          2. GET  examhome.jsp → parse once, keep soup
          3. Follow exam nav link from soup (no extra GET)
        """
        try:
            session = self.auth.login(admission_year, study_year, sem_digit)
        except AuthError as exc:
            logger.error(
                "Login failed for Y%dS%d (batch %d): %s",
                study_year, sem_digit, admission_year, exc
            )
            return None

        base_host   = self.auth.base_host
        subsite_url = get_subsite_url(admission_year, study_year, sem_digit, base_host, page="")
        subsite     = subsite_url.rstrip("/")

        # Step 2 — GET examhome.jsp once and parse
        examhome_url  = get_examhome_url(admission_year, study_year, sem_digit, base_host)
        examhome_soup = None
        try:
            resp = session.get(examhome_url, timeout=30)
            logger.debug("examhome → %s  status=%d", resp.url, resp.status_code)
            examhome_soup = BeautifulSoup(resp.text, "lxml")
        except Exception as exc:
            logger.warning("Could not reach examhome.jsp: %s", exc)

        # Step 3 — follow exam nav link using the already-parsed soup
        try:
            exam_link = self._find_exam_nav_link_from_soup(examhome_soup, subsite)
            if exam_link:
                logger.debug("Following exam nav link: %s", exam_link)
                session.get(exam_link, timeout=30)
            else:
                logger.debug("Exam nav link not found — trying RSMSubAll.jsp directly")
        except Exception as exc:
            logger.warning("Error following exam nav link: %s", exc)

        return session

    def _find_exam_nav_link_from_soup(
        self,
        soup: BeautifulSoup | None,
        subsite: str,
    ) -> str | None:
        """
        Find the Exams / Exams-norms nav link from an already-parsed soup.
        Returns a full absolute URL or None.
        """
        if soup is None:
            return None

        keywords       = ["exam", "section marks", "rsm", "marks"]
        parsed_subsite = urlparse(subsite)

        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True).lower()
            href = a["href"]
            if any(kw in text for kw in keywords) or any(kw in href.lower() for kw in keywords):
                if href.startswith("http"):
                    return href
                elif href.startswith("/"):
                    return f"{parsed_subsite.scheme}://{parsed_subsite.netloc}{href}"
                else:
                    return f"{subsite}/{href.lstrip('/')}"
        return None

    # ── Form helpers ───────────────────────────────────────────────────────

    def _read_sections_from_form(
        self,
        session: requests.Session,
        marks_url: str,
        study_year: int,
        sem_digit: int,
    ) -> list[dict]:
        """GET the marks form page and extract subjectcode1 dropdown options."""
        try:
            resp = session.get(marks_url, timeout=30)
            logger.debug(
                "RSMSubAll GET → %s  status=%d  size=%d",
                resp.url, resp.status_code, len(resp.content)
            )

            if resp.url.lower().endswith("login.jsp"):
                logger.warning(
                    "Y%dS%d — redirected to login.jsp when fetching RSMSubAll. "
                    "Session was not accepted by the portal.",
                    study_year, sem_digit,
                )
                return []

            soup   = BeautifulSoup(resp.text, "lxml")
            select = soup.find("select", {"name": "subjectcode1"})
            if not select:
                snippet = resp.text[:500].replace("\n", " ")
                logger.warning(
                    "Y%dS%d — subjectcode1 dropdown not found. Page snippet: %s",
                    study_year, sem_digit, snippet
                )
                return []

            options = []
            for opt in select.find_all("option"):
                val   = opt.get("value", "").strip()
                label = opt.get_text(strip=True)
                if val:
                    options.append({"label": label, "value": val})

            logger.info(
                "Y%dS%d — found %d section(s): %s",
                study_year, sem_digit, len(options),
                [o["label"] for o in options]
            )
            return options

        except Exception as exc:
            logger.warning(
                "Y%dS%d — error reading section dropdown: %s",
                study_year, sem_digit, exc, exc_info=True
            )
            return []

    def _post_marks_form(
        self,
        session: requests.Session,
        url: str,
        study_year: int,
        sem_digit: int,
        section_value: str,
    ) -> bytes | None:
        """POST the RSMSubAll form and return raw Excel bytes, or None."""
        payload = {
            "coursecode":   _COURSE_CODE,
            "branchcode":   _BRANCH_CODE,
            "cyear":        str(study_year),   # directly the study year (1–4)
            "semester":     str(sem_digit),    # 1 or 2
            "subjectcode1": section_value,
            "excel":        "YES",
            "next":         "submit",
        }
        logger.debug("POST %s  payload=%s", url, payload)

        try:
            resp = session.post(url, data=payload, timeout=60)
        except requests.RequestException as exc:
            logger.warning("Y%dS%d — POST failed: %s", study_year, sem_digit, exc)
            return None

        if resp.status_code != 200:
            logger.warning(
                "Y%dS%d — HTTP %d from portal", study_year, sem_digit, resp.status_code
            )
            return None

        content_type = resp.headers.get("Content-Type", "").lower()
        if "html" in content_type:
            parsed       = urlparse(url)
            parts        = parsed.path.strip("/").split("/")
            subsite_base = f"{parsed.scheme}://{parsed.netloc}/{parts[0]}"

            if self.auth._is_login_page(resp, subsite_base):
                logger.warning(
                    "Y%dS%d sec %s — session expired mid-run",
                    study_year, sem_digit, section_value
                )
            else:
                logger.warning(
                    "Y%dS%d sec %s — portal returned HTML instead of Excel "
                    "(marks may not be uploaded yet)",
                    study_year, sem_digit, section_value,
                )
            return None

        if len(resp.content) < 512:
            logger.warning(
                "Y%dS%d sec %s — response only %d bytes, likely empty",
                study_year, sem_digit, section_value, len(resp.content)
            )
            return None

        return resp.content

    def _parse_excel(
        self,
        excel_bytes: bytes,
        study_year: int,
        sem_digit: int,
        section_label: str,
    ) -> list[dict]:
        """
        Parse Excel binary into list of dicts.
        Prepends:
          - 'Semester' : human-readable label e.g. "Year 3 Sem 1"
          - 'Overall Sem': overall number 1–8  e.g. 5
          - 'Section'  : section label from dropdown
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
            logger.warning(
                "Y%dS%d sec %s — could not parse Excel with any engine",
                study_year, sem_digit, section_label
            )
            return []

        if df.empty:
            logger.warning(
                "Y%dS%d sec %s — Excel has no rows",
                study_year, sem_digit, section_label
            )
            return []

        df.columns = [str(c).strip() for c in df.columns]
        df.dropna(how="all", inplace=True)
        df.fillna("", inplace=True)

        overall = overall_semester_number(study_year, sem_digit)
        sem_label = f"Year {study_year} Sem {sem_digit}"

        # Insert identifying columns at front
        df.insert(0, "Section",      section_label)
        df.insert(0, "Overall Sem",  overall)
        df.insert(0, "Semester",     sem_label)

        return df.to_dict(orient="records")
