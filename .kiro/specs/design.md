# Design — Faculty Marks Portal Scraper & Dashboard

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                     app.py  (Streamlit UI)               │
│   Dropdowns → Generate Report → Progress → Download      │
└────────────────────────┬────────────────────────────────┘
                         │ orchestrates
         ┌───────────────┼───────────────┐
         ▼               ▼               ▼
   auth.py         url_mapper.py    scraper.py
  (login /         (build 8 URLs    (fetch + parse
  re-auth)         from batch year)  each page)
                                         │
                                         ▼
                                   exporter.py
                                (combine → Excel)
                                         │
                                         ▼
                              marks_<year>_<sec>_<ts>.xlsx
```

All modules are independent Python files at the project root and can be
imported or tested in isolation.

---

## 2. Module Design

### 2.1  `auth.py` — Authentication & Session

**Responsibility:** Create and maintain a `requests.Session` that is
authenticated with the portal.

```
class PortalAuth
    ├── __init__(base_url, username, password, verify_ssl)
    ├── login() → Session          # POST to login endpoint, raise on failure
    ├── is_logged_in(session) → bool   # heuristic: check for login-page redirect
    └── ensure_logged_in(session) → Session   # re-login if session expired
```

**Key decisions:**
- Session cookies are held in-memory only — never persisted to disk.
- `verify_ssl=False` supported for the self-signed cert on `192.168.10.x`.
- Login failure raises `AuthError(message)` — caught and surfaced in the UI.
- Re-auth is attempted once per expired request before raising.

**Login endpoint assumption:**
The portal likely uses a form POST. The exact endpoint and field names
(e.g. `username`, `password`) must be configured in `.env` as
`PORTAL_LOGIN_PATH` and `PORTAL_LOGIN_USER_FIELD` / `PORTAL_LOGIN_PASS_FIELD`.
Defaults: `/j_security_check`, `j_username`, `j_password` (common JSP pattern).

---

### 2.2  `url_mapper.py` — URL Builder

**Responsibility:** Given a batch admission year, return the 8 semester URLs.

```
def get_semester_urls(admission_year: int, base_host: str) -> dict[int, str]
    # Returns {1: "https://host/a20201/RSMSubAll.jsp", 2: ..., ..., 8: ...}

def load_url_overrides(config_path: str) -> dict
    # Reads url_config.json; if a batch/sem combo is listed, uses that URL
    # instead of the auto-generated one.
```

**URL formula:**
```
url = f"https://{base_host}/a{admission_year}{sem}/RSMSubAll.jsp"
```
where `sem` is zero-padded to 1 digit (1–8, no padding needed since max is 8).

**Override file (`url_config.json`) structure:**
```json
{
  "overrides": {
    "2020_1": "https://192.168.10.10/a20201/RSMSubAll.jsp",
    "2020_2": "https://192.168.10.10/a20202/RSMSubAll.jsp"
  }
}
```
If an override key matches `{year}_{sem}`, that URL is used verbatim.
This lets the admin correct a one-off URL without touching Python.

---

### 2.3  `scraper.py` — Page Fetcher & Parser

**Responsibility:** Given a session + URL, return structured mark records.

```
class SemesterScraper
    ├── __init__(session: requests.Session, auth: PortalAuth)
    ├── fetch_page(url: str) → BeautifulSoup | None
    │     # GET url, handle redirect → re-auth, return None on 4xx/5xx
    ├── parse_marks(soup: BeautifulSoup, semester: int) → list[dict]
    │     # Extract rows from the marks table; return [] if table absent
    └── scrape_semester(url: str, semester: int) → list[dict]
          # fetch_page + parse_marks, returns [] on any failure (logs warning)
```

**Record schema** (one dict per student-subject row):
```python
{
    "semester":        int,
    "roll_number":     str,
    "student_name":    str,
    "subject_code":    str,
    "subject_name":    str,
    "internal_marks":  str,   # kept as str to preserve "AB" / "--" values
    "external_marks":  str,
    "total_marks":     str,
}
```

**Parsing strategy:**
- Target the first `<table>` that contains a header row with keywords like
  "Internal", "External", or "Marks" (case-insensitive).
- Column indices are detected dynamically from the `<th>` row so the code
  survives minor table layout changes.
- Rows where all mark cells are empty are skipped.

**Error handling:**
| Condition | Behaviour |
|-----------|-----------|
| HTTP 4xx / 5xx | Log warning, return `[]` |
| Session expired (redirect to login) | Re-auth once, retry once, then `[]` |
| Table not found in HTML | Log warning, return `[]` |
| Network timeout | Log warning, return `[]` |

---

### 2.4  `exporter.py` — Excel Aggregation

**Responsibility:** Accept a list of records from all semesters and write
a single-sheet Excel file.

```
def export_to_excel(records: list[dict], admission_year: int,
                    section: str, output_dir: str) -> str
    # Returns the file path of the created .xlsx
```

**Sheet layout (Option B — single sheet):**

| Semester | Roll Number | Student Name | Subject Code | Subject Name | Internal | External | Total |
|----------|-------------|--------------|--------------|--------------|----------|----------|-------|
| 1        | 20CS001     | Alice        | CS101        | Maths        | 25       | 68       | 93    |
| 1        | 20CS002     | Bob          | CS101        | Maths        | 22       | 71       | 93    |
| ...      |             |              |              |              |          |          |       |
| 2        | 20CS001     | Alice        | CS201        | Physics      | 24       | 65       | 89    |

**Formatting:**
- Header row: bold, light-blue fill.
- Column widths auto-fitted to content.
- Semester column uses integer type where possible.
- Missing semester data noted as a comment row:
  `"Semester X — data not available"` spanning columns A–H.

**File naming:**
```
marks_{admission_year}_{section}_{YYYYMMDD_HHMMSS}.xlsx
```

---

### 2.5  `app.py` — Streamlit UI

**Screen flow:**
```
[Sidebar / main panel]
  ┌──────────────────────────────────┐
  │  Faculty Marks Portal            │
  │  ─────────────────────────────── │
  │  Batch Year   [2020 ▼]           │
  │  Section      [A    ▼]           │
  │                                  │
  │  [ Generate Report ]             │
  │                                  │
  │  Progress: ████████░░  6/8 sems  │
  │  Status:   Fetching semester 7…  │
  │                                  │
  │  ✅ Done! 847 rows across 7 sems │
  │  [ ⬇ Download Excel ]            │
  └──────────────────────────────────┘
```

**State management:**
- Uses `st.session_state` to hold the generated file bytes so the
  download button works without re-running the scrape.
- Credentials read from `.env` at startup via `python-dotenv`; if absent,
  a sidebar expander shows text inputs for manual entry (password type).

**Progress updates:**
- `st.progress()` bar incremented after each semester completes.
- `st.status()` text shows which semester is currently being fetched.

---

## 3. Configuration & Secrets

| Key | File | Description |
|-----|------|-------------|
| `PORTAL_BASE_HOST` | `.env` | e.g. `192.168.10.10` |
| `PORTAL_USERNAME` | `.env` | Faculty login username |
| `PORTAL_PASSWORD` | `.env` | Faculty login password |
| `PORTAL_LOGIN_PATH` | `.env` | Login POST path (default `/j_security_check`) |
| `PORTAL_LOGIN_USER_FIELD` | `.env` | Form field name for username (default `j_username`) |
| `PORTAL_LOGIN_PASS_FIELD` | `.env` | Form field name for password (default `j_password`) |
| `PORTAL_VERIFY_SSL` | `.env` | `true`/`false` (default `false` for internal IP) |
| `PORTAL_SECTIONS` | `.env` | Comma-separated list (default `A,B,C,D`) |
| `PORTAL_BATCH_START_YEAR` | `.env` | Earliest batch year in dropdown (default `2018`) |

---

## 4. File & Folder Layout

```
marks/
├── app.py                # Streamlit entry point
├── auth.py               # Authentication module
├── url_mapper.py         # URL builder / override loader
├── scraper.py            # Fetcher + BeautifulSoup parser
├── exporter.py           # pandas + openpyxl Excel writer
├── url_config.json       # Editable URL override table
├── .env.example          # Template — copy to .env and fill in
├── .env                  # Actual secrets — git-ignored
├── requirements.txt      # Pinned Python dependencies
├── README.md             # Setup and run instructions
├── output/               # Generated Excel files land here
└── .kiro/
    └── specs/
        ├── requirements.md
        ├── design.md
        └── tasks.md
```

---

## 5. Data Flow (end-to-end)

```
User picks year=2020, section=A
        │
        ▼
app.py reads .env → PortalAuth.login() → Session
        │
        ▼
url_mapper.get_semester_urls(2020) →
  {1: ".../a20201/...", 2: ".../a20202/...", ..., 8: ".../a20208/..."}
        │
        ▼  (loop, sem 1→8)
scraper.scrape_semester(url, sem)  ← session reused, re-auth if needed
  → list[dict] records  (or [] if unavailable)
        │
        ▼
all_records = flatten(sem1_records + ... + sem8_records)
        │
        ▼
exporter.export_to_excel(all_records, 2020, "A", "output/")
  → "output/marks_2020_A_20260919_143022.xlsx"
        │
        ▼
st.download_button(file_bytes)  → user downloads file
```

---

## 6. Dependency Decisions

| Library | Version (pinned) | Purpose |
|---------|-----------------|---------|
| `requests` | 2.31.0 | HTTP session + scraping |
| `beautifulsoup4` | 4.12.3 | HTML parsing |
| `lxml` | 5.2.1 | Fast BS4 parser backend |
| `pandas` | 2.2.2 | DataFrame aggregation |
| `openpyxl` | 3.1.2 | Excel write |
| `streamlit` | 1.35.0 | Local web UI |
| `python-dotenv` | 1.0.1 | `.env` loading |
| `urllib3` | 2.2.1 | Used by requests; pinned to suppress SSL warnings |
