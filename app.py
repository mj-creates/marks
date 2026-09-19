"""
app.py — Streamlit UI
======================
Faculty Marks Portal — local dashboard.

Run with:
    streamlit run app.py

Flow:
    Page 1 — Login: faculty enters username + password (verified against portal)
    Page 2 — Dashboard: pick batch year + semester → Generate Report → Download
"""

import logging
import os
from datetime import datetime
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from auth import PortalAuth, AuthError
from scraper import SemesterScraper
from exporter import export_to_excel

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

load_dotenv()  # no-op if .env absent

# ── Page config ────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Faculty Marks Portal",
    page_icon="🎓",
    layout="centered",
)

# ── Session state defaults ─────────────────────────────────────────────────
for _key, _default in [
    ("logged_in",    False),
    ("username",     ""),
    ("password",     ""),
    ("report_bytes", None),
    ("report_name",  None),
]:
    if _key not in st.session_state:
        st.session_state[_key] = _default


# ── Helpers ────────────────────────────────────────────────────────────────

def _get_batch_years():
    """Return list of admission years newest-first, up to last year."""
    current_year = datetime.now().year
    try:
        start_year = int(os.getenv("PORTAL_BATCH_START_YEAR", "2018"))
    except ValueError:
        start_year = 2018
    # Stop at current_year - 1: current calendar year batch not deployed yet
    end_year = current_year - 1
    return list(range(end_year, start_year - 1, -1))


def _read_file_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def _verify_credentials(username, password, batch_years):
    """
    Verify credentials by trying to log in to semester 1 of known batch years.
    Tries oldest years first since they are most reliably deployed on the portal.

    Returns (True, None) on success, (False, error_message) on failure.
    """
    auth = PortalAuth(username=username, password=password)

    # Try oldest → newest; stop as soon as one sub-site responds
    for year in reversed(batch_years):
        try:
            auth.login(admission_year=year, semester=1)
            return True, None          # credentials accepted
        except AuthError as exc:
            # Portal responded but rejected credentials — no point trying others
            return False, str(exc)
        except Exception:
            # Sub-site not reachable — try next year
            continue

    return False, (
        "Could not reach any batch sub-site on the portal. "
        "Make sure you are connected to the college network."
    )


# ══════════════════════════════════════════════════════════════════════════
#  PAGE 1 — Login
# ══════════════════════════════════════════════════════════════════════════

def show_login_page():
    _, col, _ = st.columns([1, 2, 1])

    with col:
        st.markdown("<br><br>", unsafe_allow_html=True)
        st.markdown(
            """
            <div style='text-align:center; padding:10px 0 20px 0;'>
                <h2 style='margin-bottom:4px;'>🎓 Faculty Marks Portal</h2>
                <p style='color:gray; font-size:14px;'>
                    VIMS — Section Marks Dashboard
                </p>
            </div>
            """,
            unsafe_allow_html=True,
        )

        with st.form("login_form"):
            username = st.text_input(
                "Portal Username",
                placeholder="Enter your faculty username",
            )
            password = st.text_input(
                "Portal Password",
                type="password",
                placeholder="Enter your portal password",
            )
            submit = st.form_submit_button(
                "Login", use_container_width=True, type="primary"
            )

        if submit:
            if not username or not password:
                st.error("Please enter both username and password.")
                return

            with st.spinner("Verifying credentials with portal…"):
                ok, err = _verify_credentials(username, password, _get_batch_years())

            if ok:
                st.session_state["logged_in"] = True
                st.session_state["username"]  = username
                st.session_state["password"]  = password
                st.rerun()
            else:
                st.error(f"❌ {err}")

        st.markdown(
            "<p style='text-align:center; color:gray; font-size:12px;"
            " margin-top:20px;'>"
            "Credentials are used only to access the portal on your behalf "
            "and are never stored or transmitted elsewhere."
            "</p>",
            unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════════════════════════════════
#  PAGE 2 — Dashboard
# ══════════════════════════════════════════════════════════════════════════

def show_main_page():
    # ── Top bar ────────────────────────────────────────────────────────
    col_title, col_logout = st.columns([5, 1])
    with col_title:
        st.title("🎓 Faculty Marks Portal")
        st.caption(
            f"Logged in as **{st.session_state['username']}** · "
            "VIMS Section Marks Dashboard"
        )
    with col_logout:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("Logout", use_container_width=True):
            st.session_state["logged_in"]    = False
            st.session_state["username"]     = ""
            st.session_state["password"]     = ""
            st.session_state["report_bytes"] = None
            st.session_state["report_name"]  = None
            st.rerun()

    st.divider()

    # ── Selectors ──────────────────────────────────────────────────────
    st.subheader("Select Batch & Semester")

    col_year, col_sem = st.columns(2)
    with col_year:
        selected_year = st.selectbox(
            "Admission Year",
            options=_get_batch_years(),
            index=0,
            help="The year this batch was admitted e.g. 2020",
        )
    with col_sem:
        selected_semester = st.selectbox(
            "Semester",
            options=list(range(1, 9)),
            index=0,
            format_func=lambda s: f"Semester {s}",
        )

    st.caption(
        f"Will fetch marks for **all sections** assigned to you in "
        f"Semester **{selected_semester}** of the **{selected_year}** batch."
    )

    st.divider()

    # ── Generate ───────────────────────────────────────────────────────
    st.subheader("Generate Report")

    if st.button("📊 Generate Report", type="primary", use_container_width=True):
        st.session_state["report_bytes"] = None
        st.session_state["report_name"]  = None

        progress_bar = st.progress(0, text="Starting…")
        status_text  = st.empty()
        no_sections  = False      # flag used outside try block

        try:
            auth    = PortalAuth(
                username=st.session_state["username"],
                password=st.session_state["password"],
            )
            scraper = SemesterScraper(auth)

            # Step 1 — discover sections for this semester
            status_text.info(
                f"🔍 Fetching sections for {selected_year} · "
                f"Semester {selected_semester}…"
            )
            sections = scraper.get_available_sections(
                admission_year=selected_year,
                semester=selected_semester,
            )

            if not sections:
                no_sections = True      # handle after try block
            else:
                total_sections = len(sections)
                st.info(
                    f"Found **{total_sections}** section(s): "
                    + ", ".join(s["label"] for s in sections)
                )

                # Step 2 — scrape every section
                def on_progress(idx, total):
                    progress_bar.progress(
                        idx / total,
                        text=f"Section {idx} of {total} done",
                    )
                    status_text.info(f"⏳ Processing section {idx} of {total}…")

                all_records = scraper.scrape_all_sections(
                    admission_year=selected_year,
                    semester=selected_semester,
                    progress_callback=on_progress,
                )

                progress_bar.progress(1.0, text="✅ All sections processed")

                if all_records:
                    status_text.info("💾 Building Excel file…")
                    output_path = export_to_excel(
                        records=all_records,
                        admission_year=selected_year,
                        semester=selected_semester,
                        output_dir="output",
                    )
                    st.session_state["report_bytes"] = _read_file_bytes(output_path)
                    st.session_state["report_name"]  = Path(output_path).name

                    secs_with_data = len(
                        {r.get("Section") for r in all_records if r.get("Section")}
                    )
                    status_text.empty()
                    st.success(
                        f"✅ Done — **{len(all_records)} rows** across "
                        f"**{secs_with_data}** section(s)."
                    )
                else:
                    status_text.empty()
                    st.warning(
                        "⚠️ No data returned for any section. "
                        "The semester data may not be uploaded yet on the portal."
                    )

        except AuthError as exc:
            status_text.empty()
            progress_bar.empty()
            st.error(
                f"❌ Session expired during scraping — please logout and "
                f"login again. ({exc})"
            )
        except Exception as exc:
            status_text.empty()
            progress_bar.empty()
            st.error(f"❌ Unexpected error: {exc}")
            logger.exception("Error during report generation")

        # Handle no-sections case outside the try block
        # (avoids st.stop() being caught by the except handler)
        if no_sections:
            status_text.empty()
            progress_bar.empty()
            st.warning(
                "⚠️ No sections found for this batch and semester. "
                "Check that the correct year and semester are selected "
                "and that marks have been uploaded on the portal."
            )

    # ── Download ───────────────────────────────────────────────────────
    if st.session_state.get("report_bytes"):
        st.divider()
        st.download_button(
            label="⬇️ Download Master Excel",
            data=st.session_state["report_bytes"],
            file_name=st.session_state["report_name"],
            mime=(
                "application/vnd.openxmlformats-officedocument"
                ".spreadsheetml.sheet"
            ),
            type="primary",
            use_container_width=True,
        )
        st.caption(f"File: `{st.session_state['report_name']}`")

    # ── Footer ─────────────────────────────────────────────────────────
    st.divider()
    st.caption(
        "Runs locally only · Read-only access · "
        "Uses your authorised portal credentials · No data stored remotely."
    )


# ══════════════════════════════════════════════════════════════════════════
#  Router
# ══════════════════════════════════════════════════════════════════════════

if st.session_state["logged_in"]:
    show_main_page()
else:
    show_login_page()
