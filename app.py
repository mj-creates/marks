"""
app.py — Streamlit UI
======================
Faculty Marks Portal — local dashboard.

Run with:
    streamlit run app.py

Flow:
    Page 1 — Login: faculty enters username, password, batch year, semester
             → logs into that specific batch's portal sub-site
    Page 2 — Dashboard: shows selected batch+semester, generate report → download
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

# ── Batch years — fixed to deployed batches on the portal ─────────────────
BATCH_YEARS = [2024, 2023, 2022, 2021, 2020]

# ── Session state defaults ─────────────────────────────────────────────────
for _key, _default in [
    ("logged_in",        False),
    ("username",         ""),
    ("password",         ""),
    ("batch_year",       None),
    ("semester",         None),
    ("report_bytes",     None),
    ("report_name",      None),
]:
    if _key not in st.session_state:
        st.session_state[_key] = _default


# ── Helpers ────────────────────────────────────────────────────────────────

def _read_file_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


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

            col_year, col_sem = st.columns(2)
            with col_year:
                batch_year = st.selectbox(
                    "Batch Year",
                    options=BATCH_YEARS,
                    index=0,
                    help="Admission year of the batch e.g. 2020",
                )
            with col_sem:
                semester = st.selectbox(
                    "Semester",
                    options=list(range(1, 9)),
                    index=0,
                    format_func=lambda s: f"Semester {s}",
                )

            submit = st.form_submit_button(
                "Login", use_container_width=True, type="primary"
            )

        if submit:
            if not username or not password:
                st.error("Please enter both username and password.")
                return

            with st.spinner(
                f"Logging in to {batch_year} batch · Semester {semester}…"
            ):
                try:
                    auth = PortalAuth(username=username, password=password)
                    auth.login(admission_year=batch_year, semester=semester)

                    # Credentials accepted — store in session
                    st.session_state["logged_in"]  = True
                    st.session_state["username"]   = username
                    st.session_state["password"]   = password
                    st.session_state["batch_year"] = batch_year
                    st.session_state["semester"]   = semester
                    st.rerun()

                except AuthError as exc:
                    st.error(f"❌ Login failed: {exc}")
                except Exception as exc:
                    st.error(f"❌ Could not connect to portal: {exc}")

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
    selected_year     = st.session_state["batch_year"]
    selected_semester = st.session_state["semester"]

    # ── Top bar ────────────────────────────────────────────────────────
    col_title, col_logout = st.columns([5, 1])
    with col_title:
        st.title("🎓 Faculty Marks Portal")
        st.caption(
            f"Logged in as **{st.session_state['username']}** · "
            f"Batch **{selected_year}** · Semester **{selected_semester}** · "
            "VIMS Section Marks Dashboard"
        )
    with col_logout:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("Logout", use_container_width=True):
            for k in ["logged_in", "username", "password",
                      "batch_year", "semester",
                      "report_bytes", "report_name"]:
                st.session_state[k] = False if k == "logged_in" else None
            st.rerun()

    st.divider()

    # ── Summary card ───────────────────────────────────────────────────
    st.info(
        f"📋 Generating master marks sheet for **{selected_year} batch · "
        f"Semester {selected_semester}** — all sections assigned to you."
    )

    st.divider()

    # ── Generate ───────────────────────────────────────────────────────
    st.subheader("Generate Report")

    if st.button("📊 Generate Report", type="primary", use_container_width=True):
        st.session_state["report_bytes"] = None
        st.session_state["report_name"]  = None

        progress_bar = st.progress(0, text="Starting…")
        status_text  = st.empty()
        no_sections  = False

        try:
            auth    = PortalAuth(
                username=st.session_state["username"],
                password=st.session_state["password"],
            )
            scraper = SemesterScraper(auth)

            # Single login — scrape_all_sections discovers sections internally
            # and returns all records in one shot (no separate get_available_sections call)
            status_text.info(
                f"🔍 Logging in and fetching sections for "
                f"{selected_year} · Semester {selected_semester}…"
            )

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
                secs_with_data = len(
                    {r.get("Section") for r in all_records if r.get("Section")}
                )
                st.info(
                    f"Found **{secs_with_data}** section(s) with data."
                )
                status_text.info("💾 Building Excel file…")
                output_path = export_to_excel(
                    records=all_records,
                    admission_year=selected_year,
                    semester=selected_semester,
                    output_dir="output",
                )
                st.session_state["report_bytes"] = _read_file_bytes(output_path)
                st.session_state["report_name"]  = Path(output_path).name
                status_text.empty()
                st.success(
                    f"✅ Done — **{len(all_records)} rows** across "
                    f"**{secs_with_data}** section(s)."
                )
            else:
                status_text.empty()
                no_sections = True

        except AuthError as exc:
            status_text.empty()
            progress_bar.empty()
            st.error(
                f"❌ Session expired — please logout and login again. ({exc})"
            )
        except Exception as exc:
            status_text.empty()
            progress_bar.empty()
            st.error(f"❌ Unexpected error: {exc}")
            logger.exception("Error during report generation")

        if no_sections:
            status_text.empty()
            progress_bar.empty()
            st.warning(
                "⚠️ No sections found for this batch and semester. "
                "Check that marks have been uploaded on the portal."
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
