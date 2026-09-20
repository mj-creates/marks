"""
app.py — Streamlit UI
======================
Faculty Marks Portal — local dashboard.

Run with:
    streamlit run app.py

Flow:
    Page 1 — Login: username, password, batch year, study year (1-4), sem digit (1 or 2)
             → logs into that specific sub-site directly
             → session object cached in st.session_state to prevent double-login on rerun
    Page 2 — Dashboard: shows selection, generate report → download Excel
"""

import logging
import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from auth import PortalAuth, AuthError
from scraper import SemesterScraper
from exporter import export_to_excel

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

load_dotenv()

# Page config
st.set_page_config(
    page_title="Faculty Marks Portal",
    page_icon="🎓",
    layout="centered",
)

# Batch years — fixed to deployed batches on the portal
BATCH_YEARS = [2024, 2023, 2022, 2021, 2020]

# Session state defaults
for _key, _default in [
    ("logged_in",          False),
    ("username",           ""),
    ("password",           ""),
    ("batch_year",         None),
    ("study_year",         None),
    ("sem_digit",          None),
    ("portal_auth",        None),   # cached PortalAuth instance — avoids re-login on rerun
    ("report_bytes",       None),
    ("report_name",        None),
]:
    if _key not in st.session_state:
        st.session_state[_key] = _default


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

            col_year, col_sy, col_sd = st.columns(3)

            with col_year:
                batch_year = st.selectbox(
                    "Batch Year",
                    options=BATCH_YEARS,
                    index=0,
                    help="Admission year of the batch e.g. 2020",
                )

            with col_sy:
                study_year = st.selectbox(
                    "Study Year",
                    options=[1, 2, 3, 4],
                    index=0,
                    format_func=lambda y: f"Year {y}",
                    help="Which year of the B.Tech (1–4)",
                )

            with col_sd:
                sem_digit = st.selectbox(
                    "Semester",
                    options=[1, 2],
                    index=0,
                    format_func=lambda s: f"Sem {s}",
                    help="1st or 2nd semester of that study year",
                )

            submit = st.form_submit_button(
                "Login", use_container_width=True, type="primary"
            )

        if submit:
            if not username or not password:
                st.error("Please enter both username and password.")
                return

            from url_mapper import build_subsite_code
            code = build_subsite_code(batch_year, study_year, sem_digit)
            with st.spinner(
                f"Logging in to portal sub-site a{code} "
                f"({batch_year} batch · Year {study_year} · Sem {sem_digit})…"
            ):
                try:
                    auth = PortalAuth(username=username, password=password)
                    # Login once — cache the auth object so Generate Report
                    # reuses it without triggering a second login
                    auth.login(
                        admission_year=batch_year,
                        study_year=study_year,
                        sem_digit=sem_digit,
                    )
                    st.session_state["logged_in"]    = True
                    st.session_state["username"]     = username
                    st.session_state["password"]     = password
                    st.session_state["batch_year"]   = batch_year
                    st.session_state["study_year"]   = study_year
                    st.session_state["sem_digit"]    = sem_digit
                    st.session_state["portal_auth"]  = auth   # cache for dashboard
                    st.rerun()

                except AuthError as exc:
                    st.error(f"❌ Login failed: {exc}")
                except Exception as exc:
                    st.error(f"❌ Could not connect to portal: {exc}")

        st.markdown(
            "<p style='text-align:center; color:gray; font-size:12px; margin-top:20px;'>"
            "Credentials are used only to access the portal on your behalf "
            "and are never stored or transmitted elsewhere."
            "</p>",
            unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════════════════════════════════
#  PAGE 2 — Dashboard
# ══════════════════════════════════════════════════════════════════════════

def show_main_page():
    batch_year = st.session_state["batch_year"]
    study_year = st.session_state["study_year"]
    sem_digit  = st.session_state["sem_digit"]

    from url_mapper import build_subsite_code, overall_semester_number
    code        = build_subsite_code(batch_year, study_year, sem_digit)
    overall_sem = overall_semester_number(study_year, sem_digit)

    # Top bar
    col_title, col_logout = st.columns([5, 1])
    with col_title:
        st.title("🎓 Faculty Marks Portal")
        st.caption(
            f"Logged in as **{st.session_state['username']}** · "
            f"Batch **{batch_year}** · "
            f"Year {study_year} Sem {sem_digit} "
            f"(Overall Sem {overall_sem}, sub-site a{code}) · "
            "VIMS Section Marks Dashboard"
        )
    with col_logout:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("Logout", use_container_width=True):
            for k in ["logged_in", "username", "password",
                      "batch_year", "study_year", "sem_digit",
                      "portal_auth", "report_bytes", "report_name"]:
                st.session_state[k] = False if k == "logged_in" else None
            st.rerun()

    st.divider()

    st.info(
        f"📋 Generating master marks sheet for **{batch_year} batch · "
        f"Year {study_year} · Semester {sem_digit}** "
        f"(Overall Sem {overall_sem}) — all sections assigned to you."
    )

    st.divider()

    # Generate
    st.subheader("Generate Report")

    if st.button("📊 Generate Report", type="primary", use_container_width=True):
        st.session_state["report_bytes"] = None
        st.session_state["report_name"]  = None

        progress_bar = st.progress(0, text="Starting…")
        status_text  = st.empty()
        no_data      = False

        try:
            # Reuse the cached PortalAuth from login — do NOT create a new one.
            # Creating a new PortalAuth and calling scrape_all_sections would
            # trigger a second login(). Instead we pass the cached auth object
            # which already has all config set from the login page.
            auth = st.session_state.get("portal_auth")
            if auth is None:
                # Fallback: recreate from stored credentials (e.g. after page refresh)
                auth = PortalAuth(
                    username=st.session_state["username"],
                    password=st.session_state["password"],
                )

            scraper = SemesterScraper(auth)

            status_text.info(
                f"🔍 Logging in to a{code} and fetching sections…"
            )

            def on_progress(idx, total):
                progress_bar.progress(
                    idx / total,
                    text=f"Section {idx} of {total} done",
                )
                status_text.info(f"⏳ Processing section {idx} of {total}…")

            all_records = scraper.scrape_all_sections(
                admission_year=batch_year,
                study_year=study_year,
                sem_digit=sem_digit,
                progress_callback=on_progress,
            )

            progress_bar.progress(1.0, text="✅ All sections processed")

            if all_records:
                secs_with_data = len(
                    {r.get("Section") for r in all_records if r.get("Section")}
                )
                st.info(f"Found **{secs_with_data}** section(s) with data.")
                status_text.info("💾 Building Excel file…")

                output_path = export_to_excel(
                    records=all_records,
                    admission_year=batch_year,
                    study_year=study_year,
                    sem_digit=sem_digit,
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
                no_data = True

        except AuthError as exc:
            status_text.empty()
            progress_bar.empty()
            st.error(f"❌ Session expired — please logout and login again. ({exc})")
        except Exception as exc:
            status_text.empty()
            progress_bar.empty()
            st.error(f"❌ Unexpected error: {exc}")
            logger.exception("Error during report generation")

        if no_data:
            status_text.empty()
            progress_bar.empty()
            st.warning(
                "⚠️ No data returned for any section. "
                "Check that marks have been uploaded on the portal "
                "for this batch, study year, and semester."
            )

    # Download
    if st.session_state.get("report_bytes"):
        st.divider()
        st.download_button(
            label="⬇️ Download Master Excel",
            data=st.session_state["report_bytes"],
            file_name=st.session_state["report_name"],
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            use_container_width=True,
        )
        st.caption(f"File: `{st.session_state['report_name']}`")

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
