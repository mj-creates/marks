import logging, os, urllib3, requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
load_dotenv()
logger = logging.getLogger(__name__)

# Failure phrases that indicate the portal rejected the login.
# Matched against BeautifulSoup get_text() output (tag-stripped),
# NOT raw HTML — portal wraps these words in separate tags so raw
# substring search never matches.
_FAILURE_PHRASES = [
    'wrong  password', 'wrong password',
    'invalid user', 'invalid login',
    'login failed', 'incorrect password',
    'authentication failed',
]

class AuthError(Exception):
    pass

class PortalAuth:
    def __init__(self, base_host=None, username=None, password=None,
                 login_path=None, user_field=None, pass_field=None, verify_ssl=None):
        self.base_host  = base_host  or os.getenv('PORTAL_BASE_HOST', 'vims.vignan.ac.in')
        self.username   = username   or ''
        self.password   = password   or ''
        self.user_field = user_field or os.getenv('PORTAL_LOGIN_USER_FIELD', 'user')
        self.pass_field = pass_field or os.getenv('PORTAL_LOGIN_PASS_FIELD', 'pwd')
        if verify_ssl is not None:
            self.verify_ssl = verify_ssl
        else:
            self.verify_ssl = os.getenv('PORTAL_VERIFY_SSL', 'false').strip().lower() == 'true'
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def login(self, admission_year, study_year, sem_digit):
        """
        GET login.jsp, read the form action, POST credentials, verify result.

        POST endpoint:  read from <form action=...> — never hardcoded.
        Payload:        {user_field: username, pass_field: password, log: LOGIN}
        Success checks (in order):
          1. HTTP status must be 200          (catches 404 j_security_check etc.)
          2. No failure phrases in stripped text  (catches "Wrong Password" in tags)
          3. Login form not still present in body (catches silent rejection)
        Response body is saved to last_login_response.html for inspection.
        """
        if not self.username or not self.password:
            raise AuthError('Credentials not set. Enter on the app login screen.')

        session = requests.Session()
        session.verify = self.verify_ssl

        from url_mapper import get_subsite_url
        subsite_base   = get_subsite_url(admission_year, study_year, sem_digit,
                                         self.base_host, page='').rstrip('/')
        login_page_url = f'{subsite_base}/login.jsp'

        # Step 1: GET login page — picks up initial JSESSIONID cookie
        try:
            resp = session.get(login_page_url, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise AuthError(f'Cannot reach login page {login_page_url}: {exc}') from exc

        logger.debug('[AUTH] GET login page OK  cookies: %s',
                     {c.name: len(c.value) for c in session.cookies})

        # Step 2: Read POST URL from form action — never hardcode
        soup = BeautifulSoup(resp.text, 'lxml')
        form = soup.find('form')

        if form and form.get('action'):
            action = form['action'].strip()
            if action.startswith('http'):
                login_post_url = action
            elif action.startswith('/'):
                from urllib.parse import urlparse
                p = urlparse(subsite_base)
                login_post_url = f'{p.scheme}://{p.netloc}{action}'
            else:
                login_post_url = f'{subsite_base}/{action.lstrip("/")}'
        else:
            login_post_url = login_page_url   # fallback: same URL

        logger.info('[AUTH] Form action resolved to: %s', login_post_url)

        # Step 3: Build payload — exactly: user, pwd, log=LOGIN
        payload = {
            self.user_field: self.username,
            self.pass_field: self.password,
            'log': 'LOGIN',
        }
        logger.debug('[AUTH] POST %s  extra fields: %s', login_post_url,
                     [k for k in payload if k not in (self.user_field, self.pass_field)])

        # Step 4: POST credentials
        try:
            resp = session.post(login_post_url, data=payload, timeout=30, allow_redirects=True)
        except requests.RequestException as exc:
            raise AuthError(f'Login POST failed for Y{study_year}S{sem_digit}: {exc}') from exc

        # Diagnostics — always at INFO level
        if resp.history:
            logger.info('[AUTH] Login POST - %d redirect(s):', len(resp.history))
            for i, r in enumerate(resp.history):
                logger.info('[AUTH]   hop %d: HTTP %d  %s  -> Location: %s',
                             i+1, r.status_code, r.url, r.headers.get('Location', ''))
        else:
            logger.info('[AUTH] Login POST - no redirects (direct response)')

        logger.info('[AUTH] Final URL: %s  HTTP %d  Content-Length: %s',
                    resp.url, resp.status_code,
                    resp.headers.get('Content-Length', 'unknown'))

        logger.info('[AUTH] Response headers from login POST:')
        for h, v in resp.headers.items():
            if h.lower() == 'set-cookie':
                logger.info('[AUTH]   %-30s  value_len=%d', h, len(v))
            else:
                logger.info('[AUTH]   %-30s  %s', h, v)

        logger.info('[AUTH] Session cookie jar after login POST:')
        if session.cookies:
            for cookie in session.cookies:
                logger.info('[AUTH]   name=%-20s  domain=%-25s  path=%-8s  value_len=%d',
                             cookie.name, cookie.domain or '(none)',
                             cookie.path, len(cookie.value))
        else:
            logger.warning('[AUTH]   WARNING: NO COOKIES in session jar after login POST')

        # Save response body for inspection — overwritten each run, gitignored
        try:
            with open('last_login_response.html', 'w', encoding='utf-8', errors='replace') as fh:
                fh.write(resp.text)
            logger.info('[AUTH] Response body saved to last_login_response.html')
        except OSError as exc:
            logger.warning('[AUTH] Could not save response body: %s', exc)

        # Step 5: Strict success checks
        # Check 1: HTTP status — 404/non-200 is always a hard failure
        if resp.status_code != 200:
            raise AuthError(
                f'[AUTH] FAILED: HTTP {resp.status_code} on POST to {login_post_url} '
                f'for Y{study_year}S{sem_digit}. Wrong endpoint or server error.'
            )

        # Parse response with BeautifulSoup to get tag-stripped text
        # IMPORTANT: failure phrases like "Wrong Password" appear inside table
        # cells/spans in separate tags — raw HTML substring search misses them.
        # get_text() strips all markup so "Wrong  Password" becomes findable.
        resp_soup      = BeautifulSoup(resp.text, 'lxml')
        stripped_text  = resp_soup.get_text(separator=' ').lower()

        # Check 2: failure phrases in stripped text
        for phrase in _FAILURE_PHRASES:
            if phrase in stripped_text:
                err_detail = phrase
                for tag in resp_soup.find_all(['font', 'strong', 'b', 'span', 'p']):
                    t = tag.get_text(separator=' ').strip()
                    t_lower = t.lower()
                    if phrase in t_lower or 'expired' in t_lower or 'deo' in t_lower:
                        err_detail = ' '.join(t.split())
                        break
                raise AuthError(
                    f'Portal returned: "{err_detail}". '
                    f'If this account is expired on this archive, use your active browser session (JSESSIONID) instead.'
                )

        # Check 3: login form still present in raw HTML
        # (use raw HTML here — we're checking for HTML attributes, not visible text)
        raw_body = resp.text.lower()
        u = self.user_field.lower()
        form_still_present = (
            (('name="' + u + '"') in raw_body or ("name='" + u + "'") in raw_body)
            and '<form' in raw_body
        )
        if form_still_present:
            raise AuthError(
                f'[AUTH] FAILED: login form still present in response '
                f'for Y{study_year}S{sem_digit}. Credentials rejected (no error message shown).'
            )

        logger.info('[AUTH] SUCCESS: confirmed logged in to Y%dS%d (batch %d)',
                    study_year, sem_digit, admission_year)
        return session

    def ensure_logged_in(self, session, response, admission_year, study_year, sem_digit):
        if self._is_login_page(response):
            logger.warning('[AUTH] Session expired Y%dS%d - re-authenticating',
                           study_year, sem_digit)
            return self.login(admission_year, study_year, sem_digit)
        return session

    def _subsite_base(self, admission_year, study_year, sem_digit):
        from url_mapper import get_subsite_url
        return get_subsite_url(admission_year, study_year, sem_digit,
                               self.base_host, page='').rstrip('/')

    def _is_login_page(self, response, subsite_base=None):
        """
        Return True if response looks like the login page.

        Checks (in order):
          1. URL ends with login.jsp
          2. Failure phrases found in tag-STRIPPED text (not raw HTML)
          3. Login form field present in raw HTML
        """
        if response.url.lower().endswith('login.jsp'):
            return True

        # Use get_text() so failure phrases split across tags are found
        try:
            page_soup     = BeautifulSoup(response.text, 'lxml')
            stripped_text = page_soup.get_text(separator=' ').lower()
        except Exception:
            stripped_text = response.text.lower()

        for phrase in _FAILURE_PHRASES:
            if phrase in stripped_text:
                return True

        # Also check for login form presence in raw HTML
        raw_body = response.text.lower()
        u = self.user_field.lower()
        if (('name="' + u + '"') in raw_body or ("name='" + u + "'") in raw_body) and '<form' in raw_body:
            return True

        return False