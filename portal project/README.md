# Faculty Marks Portal — Local Dashboard

A local tool for faculty to pull internal + external marks for a semester
from the VIMS portal and download a master Excel file covering all sections.

---

## Prerequisites

- Python 3.9 or later
- Network access to the college portal (must be on campus / VPN)

---

## Setup

### 1. Clone / copy the project

```
cd marks
```

### 2. Create a virtual environment (recommended)

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure the portal host

Copy the example env file:

```bash
copy .env.example .env       # Windows
cp  .env.example .env        # macOS / Linux
```

Open `.env` and set the portal host:

```
PORTAL_BASE_HOST=192.168.10.10
```

That is the only required setting. **Do not add your username or password
to this file** — credentials are entered only through the app's login screen
and are never written to disk.

---

## Running the app

```bash
streamlit run app.py
```

Your browser will open automatically at `http://localhost:8501`.

---

## How to use

1. **Login screen** — Enter your faculty username and password, select the
   batch year, study year (1–4), and semester (1 or 2), then click **Login**.
   The app logs you in directly to the correct portal sub-site.

2. **Generate Report** — Click the button. The app will:
   - Discover all sections assigned to you for that semester
   - Download marks for each section from the portal
   - Merge everything into one master Excel file
   - Show a progress bar while working

3. **Download** — Click **Download Master Excel** to save the file.

Generated files are also saved locally in the `output/` folder.

> Credentials are held only in browser session memory for the duration of
> your session. They are never written to disk, logged, or transmitted
> anywhere other than the college portal.

---

## .env settings reference

| Setting | Required | Default | Description |
|---------|----------|---------|-------------|
| `PORTAL_BASE_HOST` | Yes | `192.168.10.10` | Portal IP or hostname |
| `PORTAL_LOGIN_PATH` | No | `/login.jsp` | Login form POST path |
| `PORTAL_LOGIN_USER_FIELD` | No | `user` | Username field name |
| `PORTAL_LOGIN_PASS_FIELD` | No | `pwd` | Password field name |
| `PORTAL_VERIFY_SSL` | No | `false` | SSL cert verification |

---

## URL overrides

If a specific semester's URL differs from the standard pattern, edit
`url_config.json`:

```json
{
  "overrides": {
    "2020_y3s1": "https://192.168.10.10/a20221_v2/RSMSubAll.jsp"
  }
}
```

Key format: `{admission_year}_y{study_year}s{sem_digit}`

---

## Project structure

```
marks/
├── app.py            Streamlit UI — only entry point for credentials
├── auth.py           Login / session management
├── url_mapper.py     URL builder + override loader
├── scraper.py        Form submission + Excel downloader + parser
├── exporter.py       Merge records → master Excel
├── url_config.json   Optional URL overrides
├── debug_scraper.py  Diagnostic script (prompts for credentials interactively)
├── .env.example      Config template (no credentials)
├── requirements.txt  Pinned Python dependencies
├── output/           Generated Excel files (git-ignored)
└── .kiro/specs/      Requirements, design, and task docs
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| "Login failed" | Check username/password; make sure you're on the college network |
| "No sections found" | Confirm the batch year, study year, and semester are correct |
| "No data returned" | Marks may not be uploaded on the portal yet for that semester |
| SSL errors | Ensure `PORTAL_VERIFY_SSL=false` in `.env` (self-signed cert) |
| `xlrd` error on Excel parse | The portal may return `.xlsx`; the app tries both engines automatically |

---

## Notes

- This tool is **read-only** — it never modifies any portal data.
- It only accesses data you are already authorised to see under your faculty login.
- Credentials are never stored on disk or committed to version control.
