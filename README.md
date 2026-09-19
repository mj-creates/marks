# Faculty Marks Portal — Local Dashboard

A local tool for faculty to pull internal + external marks for all 8 semesters
of a batch from the VIMS portal, and download them as a single Excel file.

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

### 4. Configure credentials

Copy the example env file and fill in your details:

```bash
copy .env.example .env       # Windows
cp  .env.example .env        # macOS / Linux
```

Open `.env` in any text editor and set:

```
PORTAL_BASE_HOST=192.168.10.10
PORTAL_USERNAME=your_faculty_username
PORTAL_PASSWORD=your_portal_password
```

> Your `.env` file is git-ignored and never leaves your machine.

---

## Running the app

```bash
streamlit run app.py
```

Your browser will open automatically at `http://localhost:8501`.

---

## How to use

1. **Load Sections** — Select a batch year and click "Load Sections".
   The app logs in to the portal and reads which sections are assigned to you.

2. **Select Section** — Pick the section from the dropdown.

3. **Generate Report** — Click the button. The app will:
   - Log in to each of the 8 semester sub-sites
   - Submit the marks form and download the Excel for each semester
   - Merge all 8 into one master Excel file
   - Show a progress bar while working

4. **Download** — Click "Download Master Excel" to save the file.

Generated files are also saved locally in the `output/` folder.

---

## Adjusting login field names

If your portal uses different form field names for login, update these in `.env`:

```
PORTAL_LOGIN_PATH=/j_security_check
PORTAL_LOGIN_USER_FIELD=j_username
PORTAL_LOGIN_PASS_FIELD=j_password
```

---

## URL overrides

If a specific semester's URL is different from the standard pattern
(`a{year}{sem}/RSMSubAll.jsp`), edit `url_config.json`:

```json
{
  "overrides": {
    "2020_3": "https://192.168.10.10/a20203_v2/RSMSubAll.jsp"
  }
}
```

---

## Project structure

```
marks/
├── app.py            Streamlit UI
├── auth.py           Login / session management
├── url_mapper.py     URL builder + override loader
├── scraper.py        Form submission + Excel downloader + parser
├── exporter.py       Merge records → master Excel
├── url_config.json   Optional URL overrides
├── .env.example      Credential template (copy to .env)
├── requirements.txt  Pinned Python dependencies
├── output/           Generated Excel files (git-ignored)
└── .kiro/specs/      Requirements, design, and task docs
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| "Login failed" | Check username/password in `.env`; make sure you're on campus network |
| "No sections found" | Try a different batch year; confirm portal is accessible |
| "No data found for any semester" | The batch may not have marks uploaded yet for all sems |
| SSL errors | Ensure `PORTAL_VERIFY_SSL=false` in `.env` (self-signed cert) |
| `xlrd` error on Excel parse | The portal may return `.xlsx`; the app tries both engines automatically |

---

## Notes

- This tool is **read-only** — it never modifies any portal data.
- It only accesses data you are already authorised to see under your faculty login.
- Credentials are stored only in your local `.env` file and held in memory during the session.
