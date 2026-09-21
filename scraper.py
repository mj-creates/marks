"""
scraper.py — Section Marks HTML Scraper
=========================================
Navigation path (confirmed from live portal):

  1. POST login.jsp          -> authenticated session
  2. GET  examhome.jsp       -> portal home, parse once
  3. Click "Exams" nav link  -> follow to exam menu
  4. GET  RSMSubAll.jsp      -> marks form
  5. Read subjectcode1 dropdown -> all sections for this faculty
  6. For each section: POST form WITHOUT excel checkbox
     -> portal returns an HTML page with a marks table
  7. Parse the HTML table with BeautifulSoup
  8. Merge all sections -> flat list of row dicts

Portal: https://vims.vignan.ac.in
Form fields confirmed:
    coursecode   = A        (B.Tech)
    branchcode   = 04       (CSE)
    cyear        = study_year  (1-4)
    semester     = sem_digit   (1 or 2)
    subjectcode1 = <section value>
    (NO excel field -- we want the HTML table, not Excel download)
    next         = submit
"""

import logging
from urllib.parse import urlparse
from typing import Callable

import requests
from bs4 import BeautifulSoup

from auth import PortalAuth, AuthError
from url_mapper import get_subsite_url, get_examhome_url, overall_semester_number

logger = logging.getLogger(__name__)

_COURSE_CODE = "A"
_BRANCH_CODE = "04"


class SemesterScraper:
    """
    Scrapes marks for all sections of a given semester from the VIMS portal.
    Submits the RSMSubAll form WITHOUT the Export to Excel option to get
    an HTML table, then parses that table with BeautifulSoup.
    Merges all sections into one flat list of row dicts.
    """

    def __init__(self, auth):
        self.auth = auth

    # ── Public API ─────────────────────────────────────────────────────

    def get_available_sections(self, admission_year, study_year, sem_digit):
        """
        Login, navigate, read subjectcode1 dropdown.
        Returns [{"label": "11", "value": "11"}, ...] or [].
        """
        session = self._login_and_navigate(admission_year, study_year, sem_digit)
        if session is None:
            return []
        marks_url    = get_subsite_url(admission_year, study_year, sem_digit, self.auth.base_host)
        examhome_url = get_examhome_url(admission_year, study_year, sem_digit, self.auth.base_host)
        return self._read_sections_from_form(session, marks_url, examhome_url, study_year, sem_digit)

    def scrape_all_sections(self, admission_year, study_year, sem_digit, progress_callback=None):
        """
        Login once, discover all sections, scrape each one, merge results.
        Returns flat list of row dicts with Semester, Overall Sem, Section prepended.
        """
        session = self._login_and_navigate(admission_year, study_year, sem_digit)
        if session is None:
            return []

        marks_url    = get_subsite_url(admission_year, study_year, sem_digit, self.auth.base_host)
        examhome_url = get_examhome_url(admission_year, study_year, sem_digit, self.auth.base_host)
        sections     = self._read_sections_from_form(session, marks_url, examhome_url, study_year, sem_digit)

        if not sections:
            logger.warning("No sections found for Y%dS%d", study_year, sem_digit)
            return []

        all_records = []
        total = len(sections)
        for idx, sec in enumerate(sections, start=1):
            logger.info("Section %d/%d (subjectcode1=%s)", idx, total, sec["label"])
            records = self._scrape_section(
                session, marks_url, examhome_url,
                study_year, sem_digit, sec["value"], sec["label"]
            )
            if records:
                logger.info("  Section %s -> %d rows", sec["label"], len(records))
            else:
                logger.warning("  Section %s -> no data", sec["label"])
            all_records.extend(records)
            if progress_callback:
                progress_callback(idx, total)

        logger.info("Total rows across all sections: %d", len(all_records))
        return all_records

    # ── Navigation ──────────────────────────────────────────────────────

    def _login_and_navigate(self, admission_year, study_year, sem_digit):
        """Login, visit examhome, follow Exams nav link. Returns session or None."""
        try:
            session = self.auth.login(admission_year, study_year, sem_digit)
        except AuthError as exc:
            logger.error("Login failed Y%dS%d: %s", study_year, sem_digit, exc)
            return None

        base_host    = self.auth.base_host
        subsite      = get_subsite_url(admission_year, study_year, sem_digit, base_host, page="").rstrip("/")
        examhome_url = get_examhome_url(admission_year, study_year, sem_digit, base_host)

        # GET examhome once — parse and reuse soup
        examhome_soup = None
        try:
            resp = session.get(examhome_url, timeout=30)
            logger.debug("examhome -> %s  status=%d", resp.url, resp.status_code)
            logger.debug("[SCRAPER] Cookies after examhome: %s",
                         {c.name: len(c.value) for c in session.cookies})
            examhome_soup = BeautifulSoup(resp.text, "lxml")
        except Exception as exc:
            logger.warning("Could not reach examhome.jsp: %s", exc)

        # Follow Exams nav link (keyword: "exams" — confirmed from portal screenshot)
        try:
            exam_link = self._find_nav_link(examhome_soup, subsite, ["exams"])
            if exam_link:
                logger.debug("Following Exams nav link: %s", exam_link)
                session.get(exam_link, timeout=30)
            else:
                logger.debug("Exams nav link not found - will try RSMSubAll directly")
        except Exception as exc:
            logger.warning("Error following Exams nav link: %s", exc)

        return session

    def _find_nav_link(self, soup, subsite, keywords):
        """Find first <a> whose text matches any keyword. Return absolute URL."""
        if soup is None:
            return None
        parsed = urlparse(subsite)
        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True).lower()
            href = a["href"]
            if any(kw in text for kw in keywords):
                if href.startswith("http"):
                    return href
                elif href.startswith("/"):
                    return f"{parsed.scheme}://{parsed.netloc}{href}"
                else:
                    return f"{subsite}/{href.lstrip('/')}"
        return None

    # ── Form and table helpers ──────────────────────────────────────────

    def _read_sections_from_form(self, session, marks_url, examhome_url, study_year, sem_digit):
        """GET marks form with Referer, extract subjectcode1 options."""
        try:
            cookie_str = "; ".join(f"{c.name}=<len{len(c.value)}>" for c in session.cookies)
            logger.info("[SCRAPER] GET RSMSubAll - cookies: %s", cookie_str or "(empty)")
            logger.info("[SCRAPER] GET RSMSubAll - Referer: %s", examhome_url)

            resp = session.get(marks_url, headers={"Referer": examhome_url}, timeout=30)
            logger.info("[SCRAPER] RSMSubAll -> %s  status=%d  size=%d",
                        resp.url, resp.status_code, len(resp.content))

            if resp.url.lower().endswith("login.jsp"):
                logger.warning("[SCRAPER] Y%dS%d - redirected to login. Session not accepted.",
                               study_year, sem_digit)
                return []

            soup   = BeautifulSoup(resp.text, "lxml")
            select = soup.find("select", {"name": "subjectcode1"})
            if not select:
                snippet = resp.text[:400].replace("\n", " ")
                logger.warning("[SCRAPER] Y%dS%d - subjectcode1 not found. Snippet: %s",
                               study_year, sem_digit, snippet)
                return []

            options = []
            for opt in select.find_all("option"):
                val   = opt.get("value", "").strip()
                label = opt.get_text(strip=True)
                if val:
                    options.append({"label": label, "value": val})

            logger.info("[SCRAPER] Y%dS%d - %d section(s): %s",
                        study_year, sem_digit, len(options), [o["label"] for o in options])
            return options

        except Exception as exc:
            logger.warning("[SCRAPER] Y%dS%d - error reading sections: %s",
                           study_year, sem_digit, exc, exc_info=True)
            return []

    def _scrape_section(self, session, marks_url, examhome_url,
                        study_year, sem_digit, section_value, section_label):
        """
        POST the form WITHOUT the excel checkbox, parse the HTML marks table.
        Returns list of row dicts with Semester/Overall Sem/Section prepended.
        """
        payload = {
            "coursecode":   _COURSE_CODE,
            "branchcode":   _BRANCH_CODE,
            "cyear":        str(study_year),
            "semester":     str(sem_digit),
            "subjectcode1": section_value,
            # No "excel" field -> portal returns HTML table
            "next":         "submit",
        }
        logger.debug("[SCRAPER] POST %s payload=%s", marks_url, payload)

        try:
            resp = session.post(
                marks_url, data=payload,
                headers={"Referer": examhome_url},
                timeout=60
            )
        except requests.RequestException as exc:
            logger.warning("[SCRAPER] POST failed sec %s: %s", section_label, exc)
            return []

        if resp.status_code != 200:
            logger.warning("[SCRAPER] HTTP %d for sec %s", resp.status_code, section_label)
            return []

        if resp.url.lower().endswith("login.jsp"):
            logger.warning("[SCRAPER] Session expired during POST sec %s", section_label)
            return []

        # Parse the HTML marks table
        return self._parse_marks_table(resp.text, study_year, sem_digit, section_label)

    def _parse_marks_table(self, html, study_year, sem_digit, section_label):
        """
        Parse the marks HTML table returned by RSMSubAll.jsp after form submit.

        The table has a dynamic header row — columns vary per section because
        subjects differ between sections. We detect column indices from the
        <th> row dynamically rather than assuming fixed positions.

        Returns list of row dicts. Every dict has:
            Semester, Overall Sem, Section, Regd No, Student Name,
            plus one column per subject (internal/external/total).
        """
        soup = BeautifulSoup(html, "lxml")

        # Find the marks table — look for table containing "Regd" or roll-no pattern
        table = None
        for t in soup.find_all("table"):
            text = t.get_text(separator=" ").lower()
            if "regd" in text or "roll" in text or "201fa" in text or "211fa" in text:
                table = t
                break

        if table is None:
            # Fallback: take the largest table on the page
            tables = soup.find_all("table")
            if tables:
                table = max(tables, key=lambda t: len(t.find_all("tr")))
            else:
                logger.warning("[SCRAPER] No table found for sec %s Y%dS%d",
                               section_label, study_year, sem_digit)
                return []

        rows = table.find_all("tr")
        if len(rows) < 2:
            logger.warning("[SCRAPER] Table too small for sec %s", section_label)
            return []

        # Extract headers from first row containing <th> or the first <tr>
        header_row = None
        for row in rows:
            ths = row.find_all(["th", "td"])
            if ths and len(ths) > 3:
                header_row = ths
                break

        if header_row is None:
            logger.warning("[SCRAPER] Could not find header row for sec %s", section_label)
            return []

        headers = [cell.get_text(strip=True) for cell in header_row]
        logger.debug("[SCRAPER] sec %s headers: %s", section_label, headers)

        overall   = overall_semester_number(study_year, sem_digit)
        sem_label = f"Year {study_year} Sem {sem_digit}"

        records = []
        for row in rows[rows.index(header_row[0].parent) + 1:]:
            cells = row.find_all(["td", "th"])
            if not cells or len(cells) < 2:
                continue
            values = [cell.get_text(strip=True) for cell in cells]
            # Skip rows that are empty or sub-headers
            if all(v == "" for v in values):
                continue
            # Build dict from headers
            if len(values) < len(headers):
                values += [""] * (len(headers) - len(values))
            row_dict = dict(zip(headers, values[:len(headers)]))
            # Prepend identifying columns
            row_dict = {
                "Semester":    sem_label,
                "Overall Sem": overall,
                "Section":     section_label,
                **row_dict,
            }
            records.append(row_dict)

        logger.debug("[SCRAPER] sec %s -> %d data rows parsed", section_label, len(records))
        return records