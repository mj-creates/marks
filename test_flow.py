"""
test_flow.py - Trace the full portal flow step by step.
Run: python test_flow.py
Enter credentials when prompted.
"""
import getpass, os, sys, requests, urllib3
from bs4 import BeautifulSoup
urllib3.disable_warnings()

print("=== VIMS Portal Flow Tracer ===")
print("Credentials are used only for this run and never saved.\n")

USERNAME   = input("Username: ")
PASSWORD   = getpass.getpass("Password (hidden): ")
BASE_HOST  = os.getenv("PORTAL_BASE_HOST", "vims.vignan.ac.in")

# Pick batch/year/sem to test
BATCH      = int(input("Batch year (e.g. 2020): ") or "2020")
STUDY_YEAR = int(input("Study year 1-4 (e.g. 1): ") or "1")
SEM_DIGIT  = int(input("Semester 1 or 2 (e.g. 1): ") or "1")

# Build subsite code
PORTAL_YEAR = BATCH + (STUDY_YEAR - 1)
CODE        = f"{PORTAL_YEAR}{SEM_DIGIT}"
BASE_URL    = f"https://{BASE_HOST}/a{CODE}"  # pages appended as /login.jsp etc.

print(f"\nTarget sub-site: a{CODE}")
print(f"  Login URL  : {BASE_URL}/login.jsp")
print(f"  Examhome   : {BASE_URL}/examhome.jsp")
print(f"  RSMSubAll  : {BASE_URL}/RSMSubAll.jsp")

session = requests.Session()
session.verify = True

# STEP 1: GET login page
print(f"\n[1] GET {BASE_URL}/login.jsp")
try:
    r = session.get(f"{BASE_URL}/login.jsp", timeout=30)
    print(f"    Status: {r.status_code}  Final URL: {r.url}")
    print(f"    Cookies: {list(c.name for c in session.cookies)}")
except Exception as e:
    print(f"    ERROR: {e}"); sys.exit(1)

# Read form action
soup = BeautifulSoup(r.text, "lxml")
form = soup.find("form")
if form:
    print(f"    Form action: {form.get('action')}")
    action = form.get("action", "login.jsp").strip()
    if action.startswith("http"):
        post_url = action
    elif action.startswith("/"):
        post_url = f"https://{BASE_HOST}{action}"
    else:
        post_url = f"{BASE_URL}/{action.lstrip('/')}"
else:
    post_url = f"{BASE_URL}/login.jsp"
    print("    No form found - using login.jsp directly")

print(f"    POST URL: {post_url}")

# STEP 2: POST credentials
print(f"\n[2] POST credentials to {post_url}")
payload = {"user": USERNAME, "pwd": PASSWORD, "log": "LOGIN"}
try:
    r2 = session.post(post_url, data=payload, timeout=30, allow_redirects=True)
    print(f"    Status: {r2.status_code}  Final URL: {r2.url}")
    print(f"    Content-Length: {r2.headers.get('Content-Length','?')} bytes")
    print(f"    Cookies after POST: {[(c.name,c.domain,len(c.value)) for c in session.cookies]}")
except Exception as e:
    print(f"    ERROR: {e}"); sys.exit(1)

# Save response
with open("test_login_response.html","w",encoding="utf-8",errors="replace") as f:
    f.write(r2.text)
print("    Saved test_login_response.html")

# Check success
soup2 = BeautifulSoup(r2.text, "lxml")
text2 = soup2.get_text(separator=" ").lower()
if r2.url.lower().endswith("login.jsp"):
    print("    RESULT: Still on login.jsp -> login FAILED")
    sys.exit(1)
elif "name=\"user\"" in r2.text.lower():
    print("    RESULT: Login form still in body -> login FAILED (wrong password?)")
    # Check for error message
    for phrase in ["wrong password","invalid user","login failed"]:
        if phrase in text2:
            print(f"    Portal says: '{phrase}'")
    sys.exit(1)
else:
    print("    RESULT: Login SUCCESS")

# STEP 3: GET examhome
print(f"\n[3] GET {BASE_URL}/examhome.jsp")
try:
    r3 = session.get(f"{BASE_URL}/examhome.jsp", timeout=30)
    print(f"    Status: {r3.status_code}  Final URL: {r3.url}")
    # Find Exams nav link
    soup3 = BeautifulSoup(r3.text, "lxml")
    links = [(a.get_text(strip=True), a["href"]) for a in soup3.find_all("a",href=True)]
    exam_links = [(t,h) for t,h in links if "exam" in t.lower()]
    print(f"    Nav links containing 'exam': {exam_links[:5]}")
except Exception as e:
    print(f"    ERROR: {e}")

# STEP 4: GET RSMSubAll.jsp
print(f"\n[4] GET {BASE_URL}/RSMSubAll.jsp")
try:
    r4 = session.get(f"{BASE_URL}/RSMSubAll.jsp",
                     headers={"Referer": f"{BASE_URL}/examhome.jsp"}, timeout=30)
    print(f"    Status: {r4.status_code}  Final URL: {r4.url}")
    print(f"    Size: {len(r4.content)} bytes")
    soup4 = BeautifulSoup(r4.text, "lxml")
    sel = soup4.find("select", {"name": "subjectcode1"})
    if sel:
        opts = [(o.get_text(strip=True), o.get("value","")) for o in sel.find_all("option") if o.get("value","").strip()]
        print(f"    subjectcode1 options: {opts}")
    else:
        print("    subjectcode1 NOT FOUND")
        if r4.url.lower().endswith("login.jsp"):
            print("    -> Redirected back to login (session not persisting!)")
        # Show all selects found
        for s in soup4.find_all("select"):
            print(f"    Found select: name={s.get('name')}")
    with open("test_rsm_response.html","w",encoding="utf-8",errors="replace") as f:
        f.write(r4.text)
    print("    Saved test_rsm_response.html")
except Exception as e:
    print(f"    ERROR: {e}")

print("\n[Done] Check test_login_response.html and test_rsm_response.html")