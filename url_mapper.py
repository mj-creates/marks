"""
url_mapper.py — URL Builder & Override Loader
===============================================
Generates the 8 semester URLs for a given admission year batch.

URL formula:
    https://<BASE_HOST>/a{admission_year}{semester}/RSMSubAll.jsp

For a 2020 batch:
    sem 1 → https://192.168.10.10/a20201/RSMSubAll.jsp
    sem 2 → https://192.168.10.10/a20202/RSMSubAll.jsp
    ...
    sem 8 → https://192.168.10.10/a20208/RSMSubAll.jsp

Overrides can be placed in url_config.json to handle one-off URL changes
without touching Python code.

Usage:
    from url_mapper import get_semester_urls

    urls = get_semester_urls(2020, "192.168.10.10")
    # {1: "https://192.168.10.10/a20201/RSMSubAll.jsp", 2: ..., ...}
"""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Path to the override config, relative to this file
_DEFAULT_CONFIG_PATH = Path(__file__).parent / "url_config.json"

# Semester → (cyear, semester_number) mapping
# cyear is the "year of study" field the portal form expects
SEMESTER_TO_CYEAR: dict[int, int] = {
    1: 1,
    2: 1,
    3: 2,
    4: 2,
    5: 3,
    6: 3,
    7: 4,
    8: 4,
}


def get_semester_urls(
    admission_year: int,
    base_host: str | None = None,
    config_path: str | Path | None = None,
) -> dict[int, str]:
    """
    Return a mapping of semester number → full RSMSubAll.jsp URL.

    Args:
        admission_year: 4-digit batch year, e.g. 2020
        base_host:      Portal host (default: PORTAL_BASE_HOST env var or 192.168.10.10)
        config_path:    Path to url_config.json (default: same directory as this file)

    Returns:
        dict mapping int 1–8 to URL strings
    """
    if base_host is None:
        base_host = os.getenv("PORTAL_BASE_HOST", "192.168.10.10")

    overrides = load_url_overrides(config_path or _DEFAULT_CONFIG_PATH)

    urls: dict[int, str] = {}
    for sem in range(1, 9):
        override_key = f"{admission_year}_{sem}"
        if override_key in overrides:
            urls[sem] = overrides[override_key]
            logger.debug("Using override URL for %s", override_key)
        else:
            urls[sem] = f"https://{base_host}/a{admission_year}{sem}/RSMSubAll.jsp"

    return urls


def get_login_url(admission_year: int, semester: int, base_host: str | None = None) -> str:
    """
    Return the login.jsp URL for a specific semester sub-site.

    Args:
        admission_year: 4-digit batch year
        semester:       Semester number 1–8
        base_host:      Portal host

    Returns:
        Full login URL string
    """
    if base_host is None:
        base_host = os.getenv("PORTAL_BASE_HOST", "192.168.10.10")
    return f"https://{base_host}/a{admission_year}{semester}/login.jsp"


def get_examhome_url(admission_year: int, semester: int, base_host: str | None = None) -> str:
    """Return the examhome.jsp URL for a specific semester sub-site."""
    if base_host is None:
        base_host = os.getenv("PORTAL_BASE_HOST", "192.168.10.10")
    return f"https://{base_host}/a{admission_year}{semester}/examhome.jsp"


def sem_to_cyear(semester: int) -> int:
    """
    Convert semester number to the portal's 'cyear' (year of study) value.

    Mapping:
        Sem 1, 2 → cyear 1
        Sem 3, 4 → cyear 2
        Sem 5, 6 → cyear 3
        Sem 7, 8 → cyear 4
    """
    if semester not in SEMESTER_TO_CYEAR:
        raise ValueError(
            f"Invalid semester '{semester}'. Must be an integer between 1 and 8."
        )
    return SEMESTER_TO_CYEAR[semester]


def load_url_overrides(config_path: str | Path) -> dict[str, str]:
    """
    Load URL overrides from url_config.json.

    Returns empty dict if file is absent or malformed.

    Override key format: "{admission_year}_{semester}", e.g. "2020_3"
    Override value: full URL string
    """
    config_path = Path(config_path)
    if not config_path.exists():
        logger.debug("url_config.json not found at %s — no overrides applied", config_path)
        return {}

    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        overrides = data.get("overrides", {})
        if overrides:
            logger.info("Loaded %d URL override(s) from %s", len(overrides), config_path)
        return overrides
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read url_config.json (%s) — proceeding without overrides", exc)
        return {}
