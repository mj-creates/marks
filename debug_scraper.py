"""
debug_scraper.py — Diagnose login issue.
Run: python debug_scraper.py
"""

import os
import requests
import urllib3
from bs4 import BeautifulSoup
from dotenv import load_dotenv

urllib3.disable_warnings()
load_dotenv()

USERNAME   = os.getenv("PORTAL_USERNAME") or input("Username: ")
PASSWORD   = os.getenv("PORTAL_PASSWORD") or input("Password: ")
BASE_HOST  = os.getenv("PORTAL_BASE_HOST", "192.168.10.10")
BATCH_YEAR = 2020
SEMESTER   = 1

BASE_URL = "https://" + BASE_HOST + "/a" + str(BATCH_YEAR) + str(SEMESTER)

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

# ── Step 2: Build payload with ALL fields ─────────────────────────────────
payload = {}
if form:
    for inp in form.find_all("input"):
        name = inp.get("name", "")
        val  = inp.get("value", "")
        if name:
            payload[name] = val

# Set credentials
payload["user"] = USERNAME
payload["pwd"]  = PASSWORD
# Only send the LOGIN submit button, not Reset
payload.pop("re", None)

print("\n[3] Submitting payload: " + str(payload))

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

# Check result
if "login" in resp2.url.lower():
    print("\n    ❌ Still on login page — credentials rejected or missing field")
    soup2 = BeautifulSoup(resp2.text, "lxml")
    for tag in soup2.find_all(["p", "span", "div", "td", "font"]):
        text = tag.get_text(strip=True)
        if any(w in text.lower() for w in ["invalid", "error", "wrong", "fail", "incorrect"]):
            print("    Portal message: " + repr(text))
else:
    print("\n    ✅ Login succeeded — landed on: " + resp2.url)

    from url_mapper import get_semester_urls
    sem_urls = get_semester_urls(BATCH_YEAR, BASE_HOST)
    marks_url = sem_urls[SEMESTER]

    print("\n[4] GET " + marks_url)
    resp3 = session.get(marks_url, timeout=30)
    print("    Status: " + str(resp3.status_code) + "  Final URL: " + resp3.url)
    soup3 = BeautifulSoup(resp3.text, "lxml")
    selects = soup3.find_all("select")
    print("    <select> elements: " + str([s.get("name") for s in selects]))
    with open("debug_rsmsuball.html", "w", encoding="utf-8") as f:
        f.write(resp3.text)
    print("    Saved debug_rsmsuball.html")
