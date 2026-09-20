"""
debug_scraper.py — Diagnose login / session issues.

Run: python debug_scraper.py

Credentials are entered interactively and never stored anywhere.
"""

import getpass
import os
import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings()

# ── Interactive credential prompt — nothing read from .env ────────────────
print("=== Faculty Marks Portal — Debug Login ===")
print("Credentials are used only for this diagnostic run and are not saved.\n")
USERNAME   = input("Username: ")
PASSWORD   = getpass.getpass("Password (hidden): ")
BASE_HOST  = os.getenv("PORTAL_BASE_HOST", "192.168.10.10")

# ── Target sub-site ───────────────────────────────────────────────────────
# Edit these to target a different batch / study year / semester
BATCH_YEAR  = 2020
STUDY_YEAR  = 1     # 1–4
SEM_DIGIT   = 1     # 1 or 2

from url_mapper import build_subsite_code, get_subsite_url

CODE     = build_subsite_code(BATCH_YEAR, STUDY_YEAR, SEM_DIGIT)
BASE_URL = "https://" + BASE_HOST + "/a" + CODE

print(f"\nTargeting sub-site: a{CODE}  ({BATCH_YEAR} batch, Year {STUDY_YEAR}, Sem {SEM_DIGIT})")

session = requests.Session()
session.verify = False

# ── Step 1: GET login page ────────────────────────────────────────────────
print("\n[1] GET " + BASE_URL + "/login.jsp")
resp = session.get(BASE_URL + "/login.jsp", timeout=30)
print("    Status: " + str(resp.status_code) + "  URL: " + resp.url)
print("    Cookies: " + str(dict(session.cookies)))

soup = BeautifulSoup(resp.text, "lxml")
form = soup.find("form")

print("\n[2] Login form details:")
if form:
    print("    action : " + str(form.get("action")))
    print("    method : " + str(form.get("method")))
    print("    ALL input fields:")
    for inp in form.find_all("input"):
        n = inp.get("name", "")
        t = inp.get("type", "text")
        v = inp.get("value", "")
        print("      name=" + repr(n) + "  type=" + repr(t) + "  value=" + repr(v))
else:
    print("    NO FORM FOUND")

# ── Step 3: Build payload and POST ────────────────────────────────────────
payload = {}
if form:
    for inp in form.find_all("input"):
        name = inp.get("name", "")
        val  = inp.get("value", "")
        if name:
            payload[name] = val

payload["user"] = USERNAME
payload["pwd"]  = PASSWORD
payload.pop("re", None)   # remove Reset button

print("\n[3] Submitting payload (password hidden): " + str(
    {k: ("***" if k == "pwd" else v) for k, v in payload.items()}
))

resp2 = session.post(
    BASE_URL + "/login.jsp",
    data=payload,
    timeout=30,
    allow_redirects=True,
)
print("    Status: " + str(resp2.status_code) + "  Final URL: " + resp2.url)
print("    Cookies: " + str(dict(session.cookies)))

with open("debug_after_login.html", "w", encoding="utf-8") as f:
    f.write(resp2.text)
print("    Saved debug_after_login.html")

# ── Step 4: Check result ──────────────────────────────────────────────────
if resp2.url.lower().endswith("login.jsp"):
    print("\n    ❌ Still on login page — credentials rejected or missing field")
    soup2 = BeautifulSoup(resp2.text, "lxml")
    for tag in soup2.find_all(["p", "span", "div", "td", "font"]):
        text = tag.get_text(strip=True)
        if any(w in text.lower() for w in ["invalid", "error", "wrong", "fail", "incorrect"]):
            print("    Portal message: " + repr(text))
else:
    print("\n    ✅ Login succeeded — landed on: " + resp2.url)

    marks_url = get_subsite_url(BATCH_YEAR, STUDY_YEAR, SEM_DIGIT, BASE_HOST)
    print("\n[4] GET " + marks_url)
    resp3 = session.get(marks_url, timeout=30)
    print("    Status: " + str(resp3.status_code) + "  Final URL: " + resp3.url)
    soup3 = BeautifulSoup(resp3.text, "lxml")
    selects = soup3.find_all("select")
    print("    <select> elements: " + str([s.get("name") for s in selects]))
    with open("debug_rsmsuball.html", "w", encoding="utf-8") as f:
        f.write(resp3.text)
    print("    Saved debug_rsmsuball.html")
