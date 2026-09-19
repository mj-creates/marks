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
  8. Tag every row with Semester + Section and return flat list

Confirmed form fields (browser network payload):
    coursecode   = A          (B.Tech — fixed)
    branchcode   = 04         (CSE    — fixed)
    cyear        = 1–4        (derived: sem 1-2→1, 3-4→2, 5-6→3, 7-8→4)
    semester     = 1–8
    subjectcode1 = <numeric>  (section ID — read dynamically from dropdown)
    excel        = YES
    next         = submit
"""

import io
import logging
from urllib.parse import urlparse
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
    Downloads and parses marks for all sections of a given semester.

    Navigates the portal exactly as a human would:
      login → examhome → exams menu → RSMSubAll form → POST per section
    """

    def __init__(self, auth: PortalAuth):
        self.auth = auth

    # ── Public API ─────────────────────────────────────────────────────────

    def get_available_sections(
        self, admission_year: int, semester: int
    ) -> list[dict]:
        """
        Log in and navigate to RSMSubAll.jsp to read the section dropdown.

        Returns:
            [{"label": "11", "value": "11"}, ...]  or [] on failure.
        """
        session = self._login_and_navigate(admission_year, semester)
        if session is None:
            return []

        sem_urls  = get_semester_urls(admission_year, self.auth.base_host)
        marks_url = sem_urls[semester]
        return self._read_sections_from_form(session, marks_url, semester)

    def scrape_all_sections(
        self,
        admission_year: int,
        semester: int,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[dict]:
        """
        Log in once, navigate, then download marks for every section.

        Returns flat list of record dicts, one per student-subject row.
        Sections with no data are skipped gracefully.
        """
        session = self._login_and_navigate(admission_year, semester)
        if session is None:
            return []

        sem_urls  = get_semester_urls(admission_year, self.auth.base_host)
        marks_url = sem_urls[semester]

        sections = self._read_sections_from_form(session, marks_url, semester)
        if not sections:
            logger.warning("No sections found for sem %d", semester)
            return []

        all_records: list[dict] = []
        total = len(sections)

        for idx, sec in enumerate(sections, start=1):
            sec_label = sec["label"]
            sec_value = sec["value"]
            logger.info("Section %d/%d (subjectcode1=%s)…", idx, total, sec_label)

            excel_bytes = self._post_marks_form(
                session, marks_url, semester, sec_value
            )

            if excel_bytes is None:
                logger.warning("  Section %s — no data returned", sec_label)
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
        """Download marks for one specific section. Returns [] on any failure."""
        session = self._login_and_navigate(admission_year, semester)
        if session is None:
            return []

        sem_urls  = get_semester_urls(admission_year, self.auth.base_host)
        marks_url = sem_urls[semester]

        excel_bytes = self._post_marks_form(session, marks_url, semester, section_value)
        if excel_bytes is None:
            return []
        return self._parse_excel(excel_bytes, semester, section_label)

    # ── Navigation ─────────────────────────────────────────────────────────

    def _login_and_navigate(
        self, admission_year: int, semester: int
    ) -> requests.Session | None:
        """
        Full human navigation path. Returns an authenticated session that
        has visited all intermediate pages, or None if login fails.

          1. POST login.jsp          → session cookie
          2. GET  examhome.jsp       → parse once, keep soup
          3. Follow exam nav link    → using soup from step 2 (no extra GET)
        """
        try:
            session = self.auth.login(admission_year, semester)
        except AuthError as exc:
            logger.error("Login failed for sem %d: %s", semester, exc)
            return None

        base_host = self.auth.base_host
        subsite   = f"https://{base_host}/a{admission_year}{semester}"

        # Step 2 — GET examhome.jsp once and parse it
        examhome_url = get_examhome_url(admission_year, semester, base_host)
        examhome_soup = None
        try:
            resp = session.get(examhome_url, timeout=30)
            logger.debug("examhome → %s  status=%d", resp.url, resp.status_code)
            examhome_soup = BeautifulSoup(resp.text, "lxml")
        except Exception as exc:
            logger.warning("Could not reach examhome.jsp: %s", exc)
            # Non-fatal — continue and attempt RSMSubAll.jsp directly

        # Step 3 — find and follow the Exams nav link using the already-parsed soup
        # (no second GET of examhome — reuse soup from step 2)
        try:
            exam_link = self._find_exam_nav_link_from_soup(examhome_soup, subsite)
            if exam_link:
                logger.debug("Following exam nav link: %s", exam_link)
                session.get(exam_link, timeout=30)
            else:
                logger.debug("Exam nav link not found — will try RSMSubAll.jsp directly")
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
        Uses the soup from the examhome GET — no extra network call.

        Looks for <a> tags whose text or href contains exam-related keywords.
        Returns a full absolute URL, or None if not found.
        """
        if soup is None:
            return None

        keywords = ["exam", "section marks", "rsm", "marks"]
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
        semester: int,
    ) -> list[dict]:
        """GET the marks form page and extract subjectcode1 dropdown options."""
        try:
            resp = session.get(marks_url, timeout=30)
            logger.debug(
                "RSMSubAll GET → %s  status=%d  size=%d",
                resp.url, resp.status_code, len(resp.content)
            )

            # Use endswith to avoid false positives from query params
            if resp.url.lower().endswith("login.jsp"):
                logger.warning(
                    "Sem %d — redirected to login.jsp when fetching RSMSubAll. "
                    "Session was not accepted by the portal.",
                    semester,
                )
                return []

            soup   = BeautifulSoup(resp.text, "lxml")
            select = soup.find("select", {"name": "subjectcode1"})
            if not select:
                snippet = resp.text[:500].replace("\n", " ")
                logger.warning(
                    "Sem %d — subjectcode1 dropdown not found on page. "
                    "First 500 chars: %s",
                    semester, snippet
                )
                return []

            options = []
            for opt in select.find_all("option"):
                val   = opt.get("value", "").strip()
                label = opt.get_text(strip=True)
                if val:
                    options.append({"label": label, "value": val})

            logger.info(
                "Sem %d — found %d section(s): %s",
                semester, len(options), [o["label"] for o in options]
            )
            return options

        except Exception as exc:
            logger.warning(
                "Sem %d — error reading section dropdown: %s",
                semester, exc, exc_info=True
            )
            return []

    def _post_marks_form(
        self,
        session: requests.Session,
        url: str,
        semester: int,
        section_value: str,
    ) -> bytes | None:
        """POST the RSMSubAll form and return raw Excel bytes, or None."""
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
            logger.warning("Sem %d — HTTP %d from portal", semester, resp.status_code)
            return None

        content_type = resp.headers.get("Content-Type", "").lower()
        if "html" in content_type:
            # Reconstruct subsite base from URL for session-expiry check
            parsed       = urlparse(url)
            parts        = parsed.path.strip("/").split("/")
            subsite_base = f"{parsed.scheme}://{parsed.netloc}/{parts[0]}"

            if self.auth._is_login_page(resp, subsite_base):
                logger.warning("Sem %d sec %s — session expired mid-run", semester, section_value)
            else:
                logger.warning(
                    "Sem %d sec %s — portal returned HTML instead of Excel "
                    "(marks may not be uploaded yet)",
                    semester, section_value,
                )
            return None

        if len(resp.content) < 512:
            logger.warning(
                "Sem %d sec %s — response only %d bytes, likely empty",
                semester, section_value, len(resp.content)
            )
            return None

        return resp.content

    def _parse_excel(
        self, excel_bytes: bytes, semester: int, section_label: str
    ) -> list[dict]:
        """
        Parse Excel binary into list of dicts.
        Prepends Semester (int) and Section (str) columns to every record.
        Handles both .xls (xlrd) and .xlsx (openpyxl) formats.
        """
        for engine in ("openpyxl", "xlrd"):
            try:
                df = pd.read_excel(
                    io.BytesIO(excel_bytes),
                    engine=engine,
                    dtype=str,   # keep marks as strings — preserves "AB", "--"
                    header=0,
                )
                break
            except Exception:
                continue
        else:
            logger.warning(
                "Sem %d sec %s — could not parse Excel with any engine",
                semester, section_label
            )
            return []

        if df.empty:
            logger.warning("Sem %d sec %s — Excel has no rows", semester, section_label)
            return []

        df.columns = [str(c).strip() for c in df.columns]
        df.dropna(how="all", inplace=True)
        df.fillna("", inplace=True)

        # Insert identifying columns at front: col 0 = Semester, col 1 = Section
        df.insert(0, "Section",  section_label)
        df.insert(0, "Semester", semester)

        return df.to_dict(orient="records")
