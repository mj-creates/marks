"""
auth.py — Authentication & Session Module
==========================================
Handles login to the VIMS portal for a specific semester context.

Each semester has its own login endpoint:
  https://<BASE_HOST>/a{admission_year}{sem}/login.jsp

Usage:
    from auth import PortalAuth, AuthError

    auth = PortalAuth()
    session = auth.login(admission_year=2020, semester=1)
    # session is an authenticated requests.Session ready to use
"""

import logging
import os
import urllib3
import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class AuthError(Exception):
    """Raised when portal login fails."""
    pass


class PortalAuth:
    """
    Manages authentication against the VIMS portal.

    Each semester sub-site has its own login page, so login() accepts
    an (admission_year, semester) pair to target the correct endpoint.
    """

    def __init__(
        self,
        base_host: str | None = None,
        username: str | None = None,
        password: str | None = None,
        login_path: str | None = None,
        user_field: str | None = None,
        pass_field: str | None = None,
        verify_ssl: bool | None = None,
    ):
        self.base_host = base_host or os.getenv("PORTAL_BASE_HOST", "192.168.10.10")
        self.username = username or os.getenv("PORTAL_USERNAME", "")
        self.password = password or os.getenv("PORTAL_PASSWORD", "")

        # Login form field names — default to common JSP pattern
        self.user_field = user_field or os.getenv("PORTAL_LOGIN_USER_FIELD", "user")
        self.pass_field = pass_field or os.getenv("PORTAL_LOGIN_PASS_FIELD", "pwd")

        # Login path — portal POSTs back to login.jsp itself
        self.login_path = login_path or os.getenv("PORTAL_LOGIN_PATH", "/login.jsp")

        # SSL verification — default False for internal self-signed certs
        if verify_ssl is not None:
            self.verify_ssl = verify_ssl
        else:
            env_val = os.getenv("PORTAL_VERIFY_SSL", "false").strip().lower()
            self.verify_ssl = env_val == "true"

        if not self.verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def login(self, admission_year: int, semester: int) -> requests.Session:
        """
        Create a new authenticated session for the given semester sub-site.

        Args:
            admission_year: 4-digit batch admission year, e.g. 2020
            semester:        Semester number 1–8

        Returns:
            An authenticated requests.Session

        Raises:
            AuthError: If credentials are missing or login is rejected
        """
        if not self.username or not self.password:
            raise AuthError(
                "Credentials not set. Populate PORTAL_USERNAME and "
                "PORTAL_PASSWORD in your .env file."
            )

        session = requests.Session()
        session.verify = self.verify_ssl

        # Build the sub-site base URL, e.g. https://192.168.10.10/a20201
        subsite_base = self._subsite_base(admission_year, semester)

        # Step 1: GET the login page to collect any hidden tokens / cookies
        login_page_url = f"{subsite_base}/login.jsp"
        try:
            resp = session.get(login_page_url, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise AuthError(f"Cannot reach login page {login_page_url}: {exc}") from exc

        # Step 2: POST credentials to the login action endpoint
        login_post_url = f"{subsite_base}{self.login_path}"

        # Parse any hidden fields from the login page form and include them
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "lxml")
        payload = {}
        form = soup.find("form")
        if form:
            for hidden in form.find_all("input", {"type": "hidden"}):
                name = hidden.get("name", "")
                value = hidden.get("value", "")
                if name:
                    payload[name] = value
            # Also pick up any visible inputs with default values
            for inp in form.find_all("input"):
                name = inp.get("name", "")
                inp_type = inp.get("type", "text").lower()
                if name and inp_type not in ("password", "submit", "button", "image"):
                    if name not in payload:
                        payload[name] = inp.get("value", "")

        # Set the actual credentials (override any pre-filled values)
        payload[self.user_field] = self.username
        payload[self.pass_field] = self.password

        # Keep only the LOGIN submit button — remove Reset and other submits
        # Sending multiple submit buttons confuses some portal implementations
        keys_to_remove = []
        if form:
            for inp in form.find_all("input", {"type": "submit"}):
                name = inp.get("name", "")
                val  = inp.get("value", "")
                # Remove any submit that isn't the login button
                if name and val and not any(
                    w in val.lower() for w in ["login", "log in", "signin", "sign in"]
                ):
                    keys_to_remove.append(name)
        for k in keys_to_remove:
            payload.pop(k, None)

        try:
            resp = session.post(login_post_url, data=payload, timeout=30, allow_redirects=True)
        except requests.RequestException as exc:
            raise AuthError(f"Login POST failed for sem {semester}: {exc}") from exc

        # Step 3: Verify we are actually logged in
        if self._is_login_page(resp, subsite_base):
            raise AuthError(
                f"Login rejected for sem {semester} (year {admission_year}). "
                "Check your username and password."
            )

        logger.info("Logged in to sem %d (year %d)", semester, admission_year)
        return session

    def ensure_logged_in(
        self,
        session: requests.Session,
        response: requests.Response,
        admission_year: int,
        semester: int,
    ) -> requests.Session:
        """
        Check if the response indicates session expiry; if so, re-login once.

        Returns the (possibly refreshed) session.
        """
        subsite_base = self._subsite_base(admission_year, semester)
        if self._is_login_page(response, subsite_base):
            logger.warning(
                "Session expired for sem %d (year %d) — re-authenticating",
                semester,
                admission_year,
            )
            return self.login(admission_year, semester)
        return session

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _subsite_base(self, admission_year: int, semester: int) -> str:
        """Return the sub-site base URL, e.g. https://192.168.10.10/a20201"""
        return f"https://{self.base_host}/a{admission_year}{semester}"

    def _is_login_page(self, response: requests.Response, subsite_base: str) -> bool:
        """
        Detect if we have been redirected back to the login page.
        Uses endswith check to avoid false positives from redirect params.
        """
        final_url = response.url.lower()
        if final_url.endswith("login.jsp"):
            return True

        body = response.text.lower()
        # Use the configured user field name for body check
        user_field_lower = self.user_field.lower()
        if (
            f'name="{user_field_lower}"' in body
            or f"name='{user_field_lower}'" in body
        ) and "<form" in body:
            return True

        return False
