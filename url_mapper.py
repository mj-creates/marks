"""
url_mapper.py — URL Builder & Override Loader
===============================================
Builds portal sub-site URLs using the correct 3-input formula:

    portal_year = admission_year + (study_year - 1)
    subsite     = a{portal_year}{sem_digit}

Where:
    admission_year  = 4-digit year the batch was admitted (e.g. 2020)
    study_year      = 1, 2, 3, or 4  (which year of the B.Tech course)
    sem_digit       = 1 or 2          (1st or 2nd semester of that study year)

Example — 2020 batch:
    Y1 S1 → portal_year=2020 → a20201/RSMSubAll.jsp
    Y1 S2 → portal_year=2020 → a20202/RSMSubAll.jsp
    Y2 S1 → portal_year=2021 → a20211/RSMSubAll.jsp
    Y2 S2 → portal_year=2021 → a20212/RSMSubAll.jsp
    Y3 S1 → portal_year=2022 → a20221/RSMSubAll.jsp
    Y3 S2 → portal_year=2022 → a20222/RSMSubAll.jsp
    Y4 S1 → portal_year=2023 → a20231/RSMSubAll.jsp
    Y4 S2 → portal_year=2023 → a20232/RSMSubAll.jsp

The RSMSubAll.jsp form field 'cyear' maps directly to study_year (1–4).

Usage:
    from url_mapper import get_subsite_url, get_all_subsite_urls

    url = get_subsite_url(2020, study_year=3, sem_digit=1)
    # → "https://192.168.10.10/a20221/RSMSubAll.jsp"

    all_urls = get_all_subsite_urls(2020)
    # → {(1,1): "...a20201...", (1,2): "...a20202...", ..., (4,2): "...a20232..."}
"""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = Path(__file__).parent / "url_config.json"

# All (study_year, sem_digit) combinations for a 4-year B.Tech
ALL_SEMESTERS = [
    (1, 1), (1, 2),
    (2, 1), (2, 2),
    (3, 1), (3, 2),
    (4, 1), (4, 2),
]


# ── Core formula ───────────────────────────────────────────────────────────

def build_subsite_code(
    admission_year: int,
    study_year: int,
    sem_digit: int,
) -> str:
    """
    Return the 5-digit subsite code string, e.g. "20221".

    Args:
        admission_year: 4-digit batch year e.g. 2020
        study_year:     1–4 (which year of the B.Tech)
        sem_digit:      1 or 2

    Returns:
        String like "20221" (no leading "a")

    Raises:
        ValueError: if study_year or sem_digit are out of range
    """
    if study_year not in (1, 2, 3, 4):
        raise ValueError(f"study_year must be 1–4, got {study_year}")
    if sem_digit not in (1, 2):
        raise ValueError(f"sem_digit must be 1 or 2, got {sem_digit}")

    portal_year = admission_year + (study_year - 1)
    return f"{portal_year}{sem_digit}"


def overall_semester_number(study_year: int, sem_digit: int) -> int:
    """
    Convert (study_year, sem_digit) to an overall semester number 1–8.

    Y1S1→1, Y1S2→2, Y2S1→3, Y2S2→4, Y3S1→5, Y3S2→6, Y4S1→7, Y4S2→8
    """
    return (study_year - 1) * 2 + sem_digit


# ── URL builders ───────────────────────────────────────────────────────────

def get_subsite_url(
    admission_year: int,
    study_year: int,
    sem_digit: int,
    base_host: str | None = None,
    page: str = "RSMSubAll.jsp",
    config_path: str | Path | None = None,
) -> str:
    """
    Return the full URL for a specific study year + sem digit.

    Args:
        admission_year: 4-digit batch year
        study_year:     1–4
        sem_digit:      1 or 2
        base_host:      Portal host (default: PORTAL_BASE_HOST env var)
        page:           JSP page name (default RSMSubAll.jsp)
        config_path:    Path to url_config.json for overrides

    Returns:
        Full URL string e.g. "https://192.168.10.10/a20221/RSMSubAll.jsp"
    """
    if base_host is None:
        base_host = os.getenv("PORTAL_BASE_HOST", "192.168.10.10")

    overrides = load_url_overrides(config_path or _DEFAULT_CONFIG_PATH)
    override_key = f"{admission_year}_y{study_year}s{sem_digit}"

    if override_key in overrides:
        logger.debug("Using override URL for %s", override_key)
        return overrides[override_key]

    code = build_subsite_code(admission_year, study_year, sem_digit)
    return f"https://{base_host}/a{code}/{page}"


def get_all_subsite_urls(
    admission_year: int,
    base_host: str | None = None,
    config_path: str | Path | None = None,
) -> dict[tuple[int, int], str]:
    """
    Return a dict of all 8 semester URLs for a batch.

    Returns:
        {(study_year, sem_digit): url, ...}
        e.g. {(1,1): "...a20201...", (1,2): "...a20202...", ..., (4,2): "...a20232..."}
    """
    return {
        (sy, sd): get_subsite_url(admission_year, sy, sd, base_host, config_path=config_path)
        for sy, sd in ALL_SEMESTERS
    }


def get_login_url(
    admission_year: int,
    study_year: int,
    sem_digit: int,
    base_host: str | None = None,
) -> str:
    """Return the login.jsp URL for a specific sub-site."""
    return get_subsite_url(admission_year, study_year, sem_digit, base_host, page="login.jsp")


def get_examhome_url(
    admission_year: int,
    study_year: int,
    sem_digit: int,
    base_host: str | None = None,
) -> str:
    """Return the examhome.jsp URL for a specific sub-site."""
    return get_subsite_url(admission_year, study_year, sem_digit, base_host, page="examhome.jsp")


# ── Override loader ────────────────────────────────────────────────────────

def load_url_overrides(config_path: str | Path) -> dict[str, str]:
    """
    Load URL overrides from url_config.json.

    Override key format: "{admission_year}_y{study_year}s{sem_digit}"
    e.g. "2020_y3s1" → "https://192.168.10.10/a20221/RSMSubAll.jsp"

    Returns empty dict if file is absent or malformed.
    """
    config_path = Path(config_path)
    if not config_path.exists():
        logger.debug("url_config.json not found — no overrides applied")
        return {}
    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        overrides = data.get("overrides", {})
        if overrides:
            logger.info("Loaded %d URL override(s)", len(overrides))
        return overrides
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read url_config.json (%s) — no overrides", exc)
        return {}
