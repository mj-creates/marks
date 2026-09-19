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
        self.user_field = user_field or os.getenv("PORTAL_LOGIN_USER_FIELD", "j_username")
        self.pass_field = pass_field or os.getenv("PORTAL_LOGIN_PASS_FIELD", "j_password")

        # Login path relative to the semester sub-site root
        # e.g. "/j_security_check" or "/login.jsp" depending on the portal
        self.login_path = login_path or os.getenv("PORTAL_LOGIN_PATH", "/j_security_check")

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
        payload = {
            self.user_field: self.username,
            self.pass_field: self.password,
        }

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
        Heuristic: detect if we have been redirected back to the login page.

        Checks:
          - Final URL contains 'login' in the path
          - Response body contains the login form field names
        """
        final_url = response.url.lower()
        if "login" in final_url:
            return True

        body = response.text.lower()
        # Check for the presence of password field name in the form
        if self.pass_field.lower() in body and "<form" in body:
            return True

        return False
