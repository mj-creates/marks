# Requirements — Faculty Marks Portal Scraper & Dashboard

## 1. Purpose

A local Python tool that allows a faculty member to log into the college's
internal student portal using their own valid credentials, select a batch
year and section, and download a consolidated Excel report containing
internal and external marks for all 8 semesters — without clicking through
each semester page manually.

---

## 2. Functional Requirements

### FR-1  Credential Management
- Credentials (username, password, portal base URL) MUST be read from a
  `.env` file at runtime.
- Credentials MUST NOT be hardcoded anywhere in source code.
- If `.env` is absent, the app MUST prompt the user to enter credentials
  via the UI before proceeding.

### FR-2  Authentication & Session
- The tool MUST log into the portal using a POST request with the faculty's
  credentials.
- A single `requests.Session` object MUST be reused for all subsequent
  semester requests to preserve cookies.
- If a mid-run request returns a login redirect (session expired), the tool
  MUST automatically re-authenticate and retry the failed request once.
- SSL verification behaviour MUST be configurable (default: verify=True;
  can be set to False for self-signed internal certs via `.env`).

### FR-3  URL Mapping
- Semester URLs follow the pattern:
  `https://<BASE_HOST>/a{admission_year}{semester_number}/RSMSubAll.jsp`
  where `admission_year` is the 4-digit year the batch was admitted and
  `semester_number` is 1–8.
- The tool MUST derive the correct URL for each of 8 semesters given an
  admission year.
- The mapping MUST be stored in an editable lookup table (`url_config.json`)
  so a college admin can override it without touching Python code.

### FR-4  Year / Section Selection
- The UI MUST present a "Batch Year" dropdown (admission years; configurable
  range, default last 6 years).
- The UI MUST present a "Section" dropdown (A, B, C, D; configurable via
  `.env`).

### FR-5  Scraping
- For each of the 8 semester URLs the tool MUST:
  - Fetch the page HTML via the shared session.
  - Parse internal marks and external marks for every student in the
    selected section using BeautifulSoup.
  - Capture at minimum: Student Name, Roll Number, Subject Code,
    Subject Name, Internal Marks, External Marks, Total Marks.
- Pages are static HTML — JavaScript rendering is NOT required.
- If a semester page is unavailable (HTTP 4xx/5xx, empty table, or future
  semester not yet uploaded), the tool MUST skip it gracefully, log a
  warning, and continue.

### FR-6  Excel Export
- All scraped data MUST be combined into a **single Excel file**.
- Layout: **one sheet**, one row per student per subject per semester
  (Option B as selected by faculty).
- The sheet MUST include a "Semester" column so results can be filtered.
- The file MUST be named:
  `marks_<admission_year>_<section>_<YYYYMMDD_HHMMSS>.xlsx`
- The file MUST be immediately downloadable from the Streamlit UI.

### FR-7  Streamlit UI
- The UI MUST contain:
  - Batch Year dropdown
  - Section dropdown
  - "Generate Report" button
  - A progress bar / status text updated per semester during scraping
  - A "Download Excel" button once the report is ready
- The UI MUST show a clear error message (not a crash traceback) if login
  fails or if no data is found.

---

## 3. Non-Functional Requirements

| ID    | Requirement |
|-------|-------------|
| NFR-1 | Runs entirely locally — no cloud services, no external network calls |
| NFR-2 | Read-only — no POST/PUT/DELETE to student data endpoints |
| NFR-3 | Only the faculty member's own authorised data is accessed |
| NFR-4 | Credentials never written to logs or output files |
| NFR-5 | Each module independently importable and testable |
| NFR-6 | Compatible with Python 3.9+ |

---

## 4. Out of Scope

- Deployment / hosting
- Modifying portal data
- Accessing any account other than the authenticated faculty member's
- JS-rendered portals (not needed per confirmed static-HTML response)
- Multi-faculty / multi-tenant support
