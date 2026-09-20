"""
auth.py — Authentication & Session Module
==========================================
Handles login to the VIMS portal for a specific semester context.

Each semester has its own login endpoint:
  https://<BASE_HOST>/a{portal_year}{sem_digit}/login.jsp

Credentials are NEVER read from .env — they must be passed by the caller
(app.py login form). No passwords on disk.
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

        # Credentials supplied by caller only — never from .env
        self.username = username or ""
        self.password = password or ""

        self.user_field = user_field or os.getenv("PORTAL_LOGIN_USER_FIELD", "user")
        self.pass_field = pass_field or os.getenv("PORTAL_LOGIN_PASS_FIELD", "pwd")
        self.login_path = login_path or os.getenv("PORTAL_LOGIN_PATH", "/login.jsp")

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

    def login(self, admission_year: int, study_year: int, sem_digit: int) -> requests.Session:
        """
        POST credentials to the sub-site login page and return an
        authenticated session.

        Raises AuthError on missing credentials, network failure, or
        login rejection.
        """
        if not self.username or not self.password:
            raise AuthError(
                "Credentials not set. Enter your username and password "
                "on the app login screen."
            )

        session = requests.Session()
        session.verify = self.verify_ssl

        from url_mapper import get_subsite_url
        subsite_base   = get_subsite_url(
            admission_year, study_year, sem_digit, self.base_host, page=""
        ).rstrip("/")
        login_page_url = f"{subsite_base}/login.jsp"

        # Step 1: GET login page (picks up initial JSESSIONID cookie)
        try:
            resp = session.get(login_page_url, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise AuthError(
                f"Cannot reach login page {login_page_url}: {exc}"
            ) from exc

        logger.debug("[AUTH] GET login page — cookies: %s",
                     {c.name: len(c.value) for c in session.cookies})

        # Step 2: Build form payload
        from bs4 import BeautifulSoup
        soup    = BeautifulSoup(resp.text, "lxml")
        payload = {}
        form    = soup.find("form")

        if form:
            for hidden in form.find_all("input", {"type": "hidden"}):
                name = hidden.get("name", "")
                value = hidden.get("value", "")
                if name:
                    payload[name] = value
            for inp in form.find_all("input"):
                name     = inp.get("name", "")
                inp_type = inp.get("type", "text").lower()
                if name and inp_type not in ("password", "submit", "button", "image"):
                    if name not in payload:
                        payload[name] = inp.get("value", "")

        payload[self.user_field] = self.username
        payload[self.pass_field] = self.password

        # Remove all non-login submit buttons
        keys_to_remove = []
        if form:
            for inp in form.find_all("input", {"type": "submit"}):
                name = inp.get("name", "")
                val  = inp.get("value", "")
                if name and val and not any(
                    w in val.lower() for w in ["login", "log in", "signin", "sign in"]
                ):
                    keys_to_remove.append(name)
        for k in keys_to_remove:
            payload.pop(k, None)

        login_post_url = f"{subsite_base}{self.login_path}"
        logger.debug("[AUTH] POST %s — payload fields: %s",
                     login_post_url,
                     [k for k in payload if k not in (self.user_field, self.pass_field)])

        # Step 3: POST credentials
        try:
            resp = session.post(
                login_post_url, data=payload, timeout=30, allow_redirects=True
            )
        except requests.RequestException as exc:
            raise AuthError(
                f"Login POST failed for Y{study_year}S{sem_digit}: {exc}"
            ) from exc

        # ── Deep session diagnostics (INFO level — always visible) ────────
        # Redirect chain
        if resp.history:
            logger.info(
                "[AUTH] Login POST — %d redirect(s):", len(resp.history)
            )
            for i, r in enumerate(resp.history):
                loc = r.headers.get("Location", "")
                logger.info(
                    "[AUTH]   hop %d: HTTP %d  %s  → Location: %s",
                    i + 1, r.status_code, r.url, loc
                )
        else:
            logger.info("[AUTH] Login POST — no redirects (single response)")

        logger.info(
            "[AUTH] Final URL after POST: %s  HTTP %d",
            resp.url, resp.status_code
        )

        # All response headers from the final POST response
        logger.info("[AUTH] Response headers from login POST:")
        for h, v in resp.headers.items():
            if h.lower() == "set-cookie":
                # Never log full cookie value — log name + value length only
                logger.info("[AUTH]   %-30s  <value len=%d>", h, len(v))
            else:
                logger.info("[AUTH]   %-30s  %s", h, v)

        # Every cookie now in the session jar
        logger.info("[AUTH] Session cookie jar after login POST:")
        if session.cookies:
            for cookie in session.cookies:
                logger.info(
                    "[AUTH]   name=%-20s  domain=%-25s  path=%-8s  value_len=%d",
                    cookie.name,
                    cookie.domain or "(none)",
                    cookie.path,
                    len(cookie.value),
                )
        else:
            logger.warning(
                "[AUTH]   ⚠ NO COOKIES in session jar — "
                "portal may not have set a session cookie"
            )
        # ── End diagnostics ───────────────────────────────────────────────

        # Step 4: Verify login succeeded
        if self._is_login_page(resp, subsite_base):
            raise AuthError(
                f"Login rejected for Y{study_year}S{sem_digit} (batch {admission_year}). "
                "Check your username and password."
            )

        logger.info(
            "[AUTH] Logged in to Y%dS%d (batch %d)", study_year, sem_digit, admission_year
        )
        return session

    def ensure_logged_in(
        self,
        session: requests.Session,
        response: requests.Response,
        admission_year: int,
        study_year: int,
        sem_digit: int,
    ) -> requests.Session:
        """Re-login if session has expired. Returns refreshed session."""
        from url_mapper import get_subsite_url
        subsite_base = get_subsite_url(
            admission_year, study_year, sem_digit, self.base_host, page=""
        ).rstrip("/")
        if self._is_login_page(response, subsite_base):
            logger.warning(
                "[AUTH] Session expired for Y%dS%d (batch %d) — re-authenticating",
                study_year, sem_digit, admission_year,
            )
            return self.login(admission_year, study_year, sem_digit)
        return session

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _subsite_base(self, admission_year: int, study_year: int, sem_digit: int) -> str:
        from url_mapper import get_subsite_url
        return get_subsite_url(
            admission_year, study_year, sem_digit, self.base_host, page=""
        ).rstrip("/")

    def _is_login_page(self, response: requests.Response, subsite_base: str) -> bool:
        """
        Return True if the response looks like the login page.
        Uses endswith to avoid false positives from redirect params.
        """
        final_url = response.url.lower()
        if final_url.endswith("login.jsp"):
            return True

        body             = response.text.lower()
        user_field_lower = self.user_field.lower()
        if (
            f'name="{user_field_lower}"' in body
            or f"name='{user_field_lower}'" in body
        ) and "<form" in body:
            return True

        return False
