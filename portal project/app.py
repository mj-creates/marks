"""
app.py — VIMS Faculty Marks Portal Scraper & Master Excel Dashboard
====================================================================
Streamlit Application providing:
  1. Live Web Scraper for vims.vignan.ac.in (Course: B.Tech, Branch: CSE)
     - Logs in, visits examhome.jsp -> RSMSubAll.jsp
     - Scrapes all sections, dynamically aligns subject variations (EPCS vs BEEE)
     - Generates a single consolidated Master Excel sorted by Regd No
  2. Offline File Uploader & Merger
     - Drag & drop multiple section HTML/Excel files to instantly merge them
"""

import io
import logging
import os
from pathlib import Path
import re

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from auth import PortalAuth, AuthError
from scraper import SemesterScraper, parse_marks_html
from exporter import export_to_excel
from url_mapper import build_subsite_code, overall_semester_number, get_subsite_url

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)
load_dotenv()

st.set_page_config(
    page_title="VIMS Faculty Marks Portal",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Session State Initialization ──────────────────────────────────────────
_CONSOLIDATED_MASTER_PATH = os.path.abspath(r"c:\Users\spjee\marks\portal project\output\Consolidated_Master_Marks.xlsx")

init_bytes = None
init_filename = None
init_df = None

if os.path.exists(_CONSOLIDATED_MASTER_PATH):
    try:
        with open(_CONSOLIDATED_MASTER_PATH, "rb") as fh:
            init_bytes = fh.read()
        init_filename = "Consolidated_Master_Marks.xlsx"
        init_df = pd.read_excel(_CONSOLIDATED_MASTER_PATH, sheet_name="Master (Single Header)")
    except Exception as e:
        logger.warning("Could not pre-load master file: %s", e)

for key, default in [
    ("report_bytes", init_bytes),
    ("report_filename", init_filename),
    ("preview_df", init_df),
    ("upload_report_bytes", None),
    ("upload_report_filename", None),
    ("upload_preview_df", None),
]:
    if key not in st.session_state:
        st.session_state[key] = default


# ── Portal Archive Options (from vims.vignan.ac.in/index.html) ──────────────
ARCHIVE_YEARS = [2025, 2024, 2023, 2022, 2021, 2020, 2019, 2018, 2017, 2016]
BATCH_YEARS = [2024, 2023, 2022, 2021, 2020, 2019, 2018, 2017, 2016]


def main():
    st.markdown(
        """
        <div style="background: linear-gradient(135deg, #1F4E79 0%, #015D6A 100%); padding: 22px 28px; border-radius: 10px; color: white; margin-bottom: 24px;">
            <h1 style="margin: 0; font-size: 30px; font-weight: 700;">🎓 VIMS Faculty Marks Portal & Master Generator</h1>
            <p style="margin: 6px 0 0 0; opacity: 0.9; font-size: 15px;">
                Automated multi-section marks scraper & intelligent subject alignment for <b>B.Tech — CSE</b>
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    tab_scrape, tab_upload, tab_info = st.tabs([
        "🌐 Live Portal Scraper",
        "📁 File Uploader & Merger (Offline)",
        "ℹ️ Instructions & Help",
    ])

    with tab_scrape:
        render_scraper_tab()

    with tab_upload:
        render_uploader_tab()

    with tab_info:
        render_info_tab()


def render_scraper_tab():
    st.subheader("1. Portal Selection & Authentication")
    
    col1, col2 = st.columns([1, 1], gap="large")
    
    with col1:
        st.markdown("##### 🔐 Authentication Method")
        auth_mode = st.radio(
            "Select how you want to connect to VIMS:",
            [
                "🌟 Active Browser Session (JSESSIONID) [Recommended]",
                "🔑 Faculty Login (Username & Password)"
            ],
            index=0,
            help="Since historical portal archives expire faculty logins, using your active Chrome JSESSIONID connects immediately."
        )
        
        jsessionid_val = ""
        username = ""
        password = ""
        
        if "JSESSIONID" in auth_mode:
            jsessionid_val = st.text_input(
                "Browser Session Cookie (JSESSIONID)",
                placeholder="e.g. 5A92C1D4F8... (copy from Chrome)",
                help="Copy the JSESSIONID cookie from Chrome where you are already logged into the portal."
            ).strip()
            
            with st.expander("💡 How to get your JSESSIONID from Chrome (takes 10 seconds)", expanded=not bool(jsessionid_val)):
                st.markdown(
                    """
                    1. In Google Chrome, go to your open VIMS tab (`examhome.jsp` or `RSMSubAll.jsp`).
                    2. Press **F12** on your keyboard (or right-click anywhere and click **Inspect**).
                    3. Click on the **Application** tab at the top (if hidden, click the `>>` icon).
                    4. In the left panel under **Storage**, expand **Cookies** and click `https://vims.vignan.ac.in`.
                    5. Double-click the Value next to **`JSESSIONID`**, press **Ctrl + C** to copy, and paste it here!
                    """
                )
        else:
            default_user = os.getenv("PORTAL_USERNAME", "")
            username = st.text_input("Portal Username / Faculty Login ID", value=default_user, placeholder="e.g. 02507")
            password = st.text_input("Portal Password", type="password", placeholder="Enter your portal password")
            st.caption("🔒 Credentials are used in-memory for this scraping session only and are never saved.")

    with col2:
        st.markdown("##### 🎯 Target Semester")
        sel_mode = st.radio("Selection Mode", ["By Admission Batch Year", "By Direct Archive Subsite"], horizontal=True)
        
        if sel_mode == "By Admission Batch Year":
            col_b, col_y, col_s = st.columns(3)
            with col_b:
                batch_year = st.selectbox("Batch Year", BATCH_YEARS, index=BATCH_YEARS.index(2020) if 2020 in BATCH_YEARS else 0, help="Year students were admitted (e.g. 2020 for 201FA04...)")
            with col_y:
                study_year = st.selectbox("Study Year", [1, 2, 3, 4], index=0, format_func=lambda y: f"Year {y}")
            with col_s:
                sem_digit = st.selectbox("Semester", [1, 2], index=0, format_func=lambda s: f"Sem {s}")
            code = build_subsite_code(batch_year, study_year, sem_digit)
        else:
            col_a, col_y = st.columns(2)
            with col_a:
                archive_choice = st.selectbox(
                    "Archive Subsite",
                    [f"{y} Sem-{s} (a{y}{s})" for y in ARCHIVE_YEARS for s in [1, 2]],
                    index=ARCHIVE_YEARS.index(2020) * 2 if 2020 in ARCHIVE_YEARS else 0
                )
                m = re.search(r'a(\d{4})(\d)', archive_choice)
                portal_yr = int(m.group(1)) if m else 2020
                sem_digit = int(m.group(2)) if m else 1
            with col_y:
                study_year = st.selectbox("Study Year in that Semester", [1, 2, 3, 4], index=0, format_func=lambda y: f"Year {y}")
            batch_year = portal_yr - (study_year - 1)
            code = f"{portal_yr}{sem_digit}"

        overall_sem = overall_semester_number(study_year, sem_digit)
        st.info(f"Target Subsite: **a{code}** (`https://vims.vignan.ac.in/a{code}/`) · **Year {study_year} Sem {sem_digit}** (Overall Sem {overall_sem}) · Course: **B.Tech (A)** · Branch: **CSE (04)**")
        
        with st.expander("⚙️ Section Settings (Optional)", expanded=False):
            manual_sec_input = st.text_input(
                "Specific Sections (comma separated)",
                placeholder="Leave blank to auto-detect all sections (or enter e.g. 11, 12, 13, 14, 15)",
                help="If you want to scrape specific sections or if the portal form is dynamic, enter them here."
            )
            manual_secs = [s.strip() for s in manual_sec_input.split(",") if s.strip()] if manual_sec_input else None

    st.markdown("<br>", unsafe_allow_html=True)
    start_btn = st.button("🚀 Start Scraper & Generate Master Excel", type="primary", use_container_width=True)

    if start_btn:
        custom_session = None
        auth = None
        
        if "JSESSIONID" in auth_mode:
            if not jsessionid_val:
                st.error("⚠️ Please paste your active `JSESSIONID` from Chrome into the field above.")
                return
            import requests
            custom_session = requests.Session()
            custom_session.verify = False
            custom_session.cookies.set("JSESSIONID", jsessionid_val, domain="vims.vignan.ac.in", path=f"/a{code}/")
            custom_session.cookies.set("JSESSIONID", jsessionid_val, domain="vims.vignan.ac.in", path="/")
        else:
            if not username or not password:
                st.error("⚠️ Please enter your faculty username and password above.")
                return
            auth = PortalAuth(
                base_host="vims.vignan.ac.in",
                username=username,
                password=password,
                verify_ssl=False
            )

        progress_bar = st.progress(0, text="Initializing session...")
        status_box = st.empty()
        
        try:
            status_box.info(f"Connecting to `https://vims.vignan.ac.in/a{code}/`...")
            scraper = SemesterScraper(auth=auth, base_host="vims.vignan.ac.in")
            
            def on_progress(idx, total, sec_label):
                pct = idx / total
                progress_bar.progress(pct, text=f"Scraping section {idx} of {total} (Section {sec_label})...")
                status_box.info(f"⏳ Processing Section **{sec_label}** ({idx}/{total})...")

            all_records = scraper.scrape_all_sections(
                admission_year=batch_year,
                study_year=study_year,
                sem_digit=sem_digit,
                progress_callback=on_progress,
                custom_session=custom_session,
                manual_sections=manual_secs,
            )
            
            progress_bar.progress(1.0, text="Scraping completed!")
            
            if not all_records:
                status_box.empty()
                st.warning(
                    "⚠️ No student records were returned. "
                    "Please verify that marks have been uploaded on the portal for this semester, "
                    "or enter section numbers (e.g. 11, 12, 13) in 'Section Settings' above."
                )
                return

            status_box.info("Aligning subject columns and generating Master Excel report...")
            
            excel_path = export_to_excel(
                records=all_records,
                admission_year=batch_year,
                study_year=study_year,
                sem_digit=sem_digit,
                output_dir="output",
            )
            
            with open(excel_path, "rb") as fh:
                file_bytes = fh.read()
                
            st.session_state["report_bytes"] = file_bytes
            st.session_state["report_filename"] = Path(excel_path).name
            st.session_state["preview_df"] = pd.read_excel(excel_path)
            
            status_box.empty()
            st.success(f"🎉 Successfully scraped **{len(all_records)} student rows** across all sections into a single Master Excel!")

        except AuthError as exc:
            progress_bar.empty()
            status_box.empty()
            st.error(f"❌ Portal Authentication / Session Error:\n\n{exc}")
            if "JSESSIONID" not in auth_mode:
                st.info("💡 **Tip**: Since older archive faculty logins are expired by the DEO, switch to **'🌟 Active Browser Session (JSESSIONID)'** above, paste your cookie from Chrome, and click Scrape to proceed without login issues!")
        except Exception as exc:
            progress_bar.empty()
            status_box.empty()
            st.error(f"❌ Scraping Failed: {exc}")
            logger.exception("Scraping failure")

    # ── Display Download & Preview ────────────────────────────────────────
    if st.session_state["report_bytes"] and st.session_state["report_filename"]:
        st.divider()
        st.markdown("### 📥 Download Consolidated Report")
        
        c_dl, _ = st.columns([1, 2])
        with c_dl:
            st.download_button(
                label=f"⬇️ Download Master Excel ({st.session_state['report_filename']})",
                data=st.session_state["report_bytes"],
                file_name=st.session_state["report_filename"],
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
                use_container_width=True,
            )

        if st.session_state["preview_df"] is not None:
            st.markdown("##### 🔍 Master Dataset Preview (Student Names Directly Beside REGD.NO)")
            preview = st.session_state["preview_df"].copy()
            if "REGD.NO" in preview.columns:
                preview = preview.sort_values(by="REGD.NO").reset_index(drop=True)
            elif "Regd No" in preview.columns:
                preview = preview.sort_values(by="Regd No").reset_index(drop=True)
            
            # Ensure PyArrow serialization safety across all columns
            for c in preview.columns:
                if c not in ["REGD.NO", "NAME", "SECTION", "Regd No", "Name", "Section"]:
                    preview[c] = pd.to_numeric(preview[c], errors="coerce")
                else:
                    preview[c] = preview[c].astype(str)

            st.dataframe(preview.head(50), use_container_width=True)


def render_uploader_tab():
    st.subheader("📁 Upload Section Marks Files (.html, .htm, .xls)")
    st.markdown(
        """
        If you have already downloaded the section HTML pages or Excel exports from the portal, 
        you can upload all of them together here. The app will:
        - **Intelligently align subjects** (such as EPCS vs. BEEE differences).
        - **Interleave and sort all students by Registration Number** (`201FA04001`, `201FA04002`...).
        - **Export the consolidated Master Excel sheet** instantly without any login needed!
        """
    )
    
    col_opt1, col_opt2 = st.columns(2)
    with col_opt1:
        u_batch = st.selectbox("Batch Year (for naming)", BATCH_YEARS, index=BATCH_YEARS.index(2020) if 2020 in BATCH_YEARS else 0, key="u_batch")
    with col_opt2:
        u_sy = st.selectbox("Study Year", [1, 2, 3, 4], index=0, key="u_sy", format_func=lambda y: f"Year {y}")
        u_sd = st.selectbox("Semester", [1, 2], index=0, key="u_sd", format_func=lambda s: f"Sem {s}")

    uploaded_files = st.file_uploader(
        "Select or drop all section files here",
        type=["html", "htm", "xls", "xlsx"],
        accept_multiple_files=True,
    )

    if uploaded_files:
        st.write(f"📂 **{len(uploaded_files)} file(s) selected**: {', '.join(f.name for f in uploaded_files)}")
        
        if st.button("✨ Merge All Section Files into Master Sheet", type="primary", use_container_width=True):
            all_records = []
            
            with st.spinner("Processing and aligning section files..."):
                for idx, file_obj in enumerate(uploaded_files, start=1):
                    sec_label = Path(file_obj.name).stem.replace("RSMSubAll", "").replace("marks", "").strip("_- ") or f"Sec_{idx}"
                    content = file_obj.read()
                    
                    # Try HTML parsing first (most VIMS JSP 'excel' exports are HTML tables)
                    try:
                        html_text = content.decode("utf-8", errors="replace")
                        records, _ = parse_marks_html(html_text, study_year=u_sy, sem_digit=u_sd, section_label=sec_label)
                        if records:
                            all_records.extend(records)
                            continue
                    except Exception:
                        pass
                        
                    # Fallback to pandas read_excel if binary
                    try:
                        file_obj.seek(0)
                        df_sec = pd.read_excel(file_obj)
                        df_sec["Section"] = sec_label
                        df_sec["Semester"] = f"Year {u_sy} Sem {u_sd}"
                        all_records.extend(df_sec.to_dict(orient="records"))
                    except Exception as exc:
                        st.warning(f"Could not parse `{file_obj.name}`: {exc}")

            if all_records:
                excel_path = export_to_excel(
                    records=all_records,
                    admission_year=u_batch,
                    study_year=u_sy,
                    sem_digit=u_sd,
                    output_dir="output",
                    filename_prefix=f"master_marks_batch{u_batch}_merged",
                )
                with open(excel_path, "rb") as fh:
                    st.session_state["upload_report_bytes"] = fh.read()
                st.session_state["upload_report_filename"] = Path(excel_path).name
                st.session_state["upload_preview_df"] = pd.read_excel(excel_path)
                st.success(f"🎉 Successfully merged **{len(all_records)} student rows** from {len(uploaded_files)} files!")
            else:
                st.error("No student records could be extracted from the uploaded files.")

    if st.session_state["upload_report_bytes"]:
        st.divider()
        st.download_button(
            label=f"⬇️ Download Merged Master Excel ({st.session_state['upload_report_filename']})",
            data=st.session_state["upload_report_bytes"],
            file_name=st.session_state["upload_report_filename"],
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            use_container_width=True,
        )
        if st.session_state["upload_preview_df"] is not None:
            preview = st.session_state["upload_preview_df"].copy()
            if "REGD.NO" in preview.columns:
                preview = preview.sort_values(by="REGD.NO").reset_index(drop=True)
            elif "Regd No" in preview.columns:
                preview = preview.sort_values(by="Regd No").reset_index(drop=True)
            for c in preview.columns:
                if c not in ["REGD.NO", "NAME", "SECTION", "Regd No", "Name", "Section"]:
                    preview[c] = pd.to_numeric(preview[c], errors="coerce")
                else:
                    preview[c] = preview[c].astype(str)
            st.dataframe(preview.head(50), use_container_width=True)


def render_info_tab():
    st.subheader("ℹ️ Portal & Workflow Details")
    st.markdown(
        """
        ### 📌 Navigation Flow in VIMS
        1. **Archives**: `https://vims.vignan.ac.in/index.html#` contains archives from 2016 to 2025.
        2. **Sub-site URL formula**:
           $$\\text{portal\\_year} = \\text{admission\\_year} + (\\text{study\\_year} - 1)$$
           $$\\text{subsite} = \\text{a}\\{\\text{portal\\_year}\\}\\{\\text{sem\\_digit}\\}$$
           *Example (2020 Batch)*: Year 1 Sem 1 $\\rightarrow$ `a20201`, Year 1 Sem 2 $\\rightarrow$ `a20202`.
        3. **Login**: `https://vims.vignan.ac.in/a{code}/login.jsp`
        4. **Exams Home**: `https://vims.vignan.ac.in/a{code}/examhome.jsp`
        5. **Marks Form**: `https://vims.vignan.ac.in/a{code}/RSMSubAll.jsp`
           - **Course**: B.Tech (`A`)
           - **Branch**: CSE (`04`)
           - **Year**: 1, 2, 3, 4
           - **Semester**: 1 or 2
           - **Section**: Dropdown (`subjectcode1`)
           - **Export to Excel**: Left **unchecked** so the portal renders the full HTML table.
        
        ### 🔄 Subject Variation Handling
        In some semesters (e.g. 1st year cycle subjects), Section 11 has `EPCS` first while Section 12 has `BEEE` first.
        This app uses dynamic 2D header expansion:
        - Automatically resolves hierarchical headers (e.g. `EPCS - INT`, `EPCS - EXT`, `EPCS - TOT` vs `BEEE - INT`, `BEEE - EXT`, `BEEE - TOT`).
        - Places each student's marks under their respective subject columns.
        - Interleaves and sorts all students of that entire year in sequential order by **Registration Number** (`201FA04001`, `201FA04002`...).
        """
    )


if __name__ == "__main__":
    main()
