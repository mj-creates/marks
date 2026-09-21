"""
url_mapper.py — URL Builder and Override Loader
================================================
Builds portal sub-site URLs using the confirmed formula:

    portal_year = admission_year + (study_year - 1)
    subsite     = a{portal_year}{sem_digit}

Portal host: vims.vignan.ac.in (public HTTPS, valid cert)

Example — 2020 batch:
    Y1 S1 -> portal_year=2020 -> a20201
    Y1 S2 -> portal_year=2020 -> a20202
    Y2 S1 -> portal_year=2021 -> a20211
    Y2 S2 -> portal_year=2021 -> a20212
    Y3 S1 -> portal_year=2022 -> a20221
    Y3 S2 -> portal_year=2022 -> a20222
    Y4 S1 -> portal_year=2023 -> a20231
    Y4 S2 -> portal_year=2023 -> a20232

RSMSubAll.jsp form field cyear = study_year directly (1-4).
"""

import json, logging, os
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = Path(__file__).parent / "url_config.json"

ALL_SEMESTERS = [
    (1, 1), (1, 2),
    (2, 1), (2, 2),
    (3, 1), (3, 2),
    (4, 1), (4, 2),
]


def build_subsite_code(admission_year, study_year, sem_digit):
    """Return 5-digit subsite code e.g. "20221"."""
    if study_year not in (1, 2, 3, 4):
        raise ValueError(f"study_year must be 1-4, got {study_year}")
    if sem_digit not in (1, 2):
        raise ValueError(f"sem_digit must be 1 or 2, got {sem_digit}")
    portal_year = admission_year + (study_year - 1)
    return f"{portal_year}{sem_digit}"


def overall_semester_number(study_year, sem_digit):
    """Y1S1->1, Y1S2->2, Y2S1->3 ... Y4S2->8"""
    return (study_year - 1) * 2 + sem_digit


def get_subsite_url(admission_year, study_year, sem_digit,
                    base_host=None, page="RSMSubAll.jsp", config_path=None):
    """Return full URL for a specific study year + sem digit."""
    if base_host is None:
        base_host = os.getenv("PORTAL_BASE_HOST", "vims.vignan.ac.in")
    overrides    = load_url_overrides(config_path or _DEFAULT_CONFIG_PATH)
    override_key = f"{admission_year}_y{study_year}s{sem_digit}"
    if override_key in overrides:
        logger.debug("Using override URL for %s", override_key)
        return overrides[override_key]
    code = build_subsite_code(admission_year, study_year, sem_digit)
    return f"https://{base_host}/a{code}/{page}"


def get_all_subsite_urls(admission_year, base_host=None, config_path=None):
    """Return {(study_year, sem_digit): url} for all 8 semesters."""
    return {
        (sy, sd): get_subsite_url(admission_year, sy, sd, base_host, config_path=config_path)
        for sy, sd in ALL_SEMESTERS
    }


def get_login_url(admission_year, study_year, sem_digit, base_host=None):
    return get_subsite_url(admission_year, study_year, sem_digit, base_host, page="login.jsp")


def get_examhome_url(admission_year, study_year, sem_digit, base_host=None):
    return get_subsite_url(admission_year, study_year, sem_digit, base_host, page="examhome.jsp")


def load_url_overrides(config_path):
    config_path = Path(config_path)
    if not config_path.exists():
        return {}
    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        overrides = data.get("overrides", {})
        if overrides:
            logger.info("Loaded %d URL override(s)", len(overrides))
        return overrides
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read url_config.json (%s)", exc)
        return {}