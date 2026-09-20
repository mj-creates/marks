import logging, os, urllib3, requests
from dotenv import load_dotenv
load_dotenv()
logger = logging.getLogger(__name__)

class AuthError(Exception):
    pass

class PortalAuth:
    def __init__(self, base_host=None, username=None, password=None,
                 login_path=None, user_field=None, pass_field=None, verify_ssl=None):
        self.base_host  = base_host  or os.getenv('PORTAL_BASE_HOST', '192.168.10.10')
        self.username   = username   or ''
        self.password   = password   or ''
        self.user_field = user_field or os.getenv('PORTAL_LOGIN_USER_FIELD', 'user')
        self.pass_field = pass_field or os.getenv('PORTAL_LOGIN_PASS_FIELD', 'pwd')
        if verify_ssl is not None:
            self.verify_ssl = verify_ssl
        else:
            self.verify_ssl = os.getenv('PORTAL_VERIFY_SSL','false').strip().lower() == 'true'
        if not self.verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def login(self, admission_year, study_year, sem_digit):
        """
        POST credentials to login.jsp and return an authenticated session.
        POST endpoint is read from the form action attribute — never hardcoded.
        Payload: exactly {user_field: username, pass_field: password, log: LOGIN}
        Success check: HTTP 200 AND body does not contain login form AND no failure text.
        """
        if not self.username or not self.password:
            raise AuthError('Credentials not set. Enter on the app login screen.')

        session = requests.Session()
        session.verify = self.verify_ssl

        from url_mapper import get_subsite_url
        subsite_base   = get_subsite_url(admission_year, study_year, sem_digit,
                                         self.base_host, page='').rstrip('/')
        login_page_url = f'{subsite_base}/login.jsp'

        # Step 1: GET login page to get JSESSIONID and read the form action
        try:
            resp = session.get(login_page_url, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise AuthError(f'Cannot reach login page {login_page_url}: {exc}') from exc

        logger.debug('[AUTH] GET login page OK  cookies: %s',
                     {c.name: len(c.value) for c in session.cookies})

        # Step 2: Read form action from the page (do NOT hardcode the endpoint)
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, 'lxml')
        form = soup.find('form')

        # Derive POST URL from form action attribute
        if form and form.get('action'):
            action = form['action'].strip()
            if action.startswith('http'):
                login_post_url = action
            elif action.startswith('/'):
                from urllib.parse import urlparse
                parsed = urlparse(subsite_base)
                login_post_url = f'{parsed.scheme}://{parsed.netloc}{action}'
            else:
                # Relative path — resolve against subsite base
                login_post_url = f'{subsite_base}/{action.lstrip("/")}'
        else:
            # Fallback: POST to the same login.jsp URL
            login_post_url = login_page_url

        logger.info('[AUTH] Form action resolved to: %s', login_post_url)

        # Step 3: Build payload — confirmed fields: user, pwd, log=LOGIN
        # Use exactly the three fields seen in working browser payload.
        # 'log' is the LOGIN submit button value — must be included.
        payload = {
            self.user_field: self.username,
            self.pass_field: self.password,
            'log': 'LOGIN',
        }

        logger.debug('[AUTH] POST %s  fields: %s', login_post_url,
                     [k for k in payload if k not in (self.user_field, self.pass_field)])

        # Step 4: POST credentials
        try:
            resp = session.post(login_post_url, data=payload, timeout=30, allow_redirects=True)
        except requests.RequestException as exc:
            raise AuthError(f'Login POST failed for Y{study_year}S{sem_digit}: {exc}') from exc

        # Deep diagnostics — always log at INFO so they appear in terminal
        if resp.history:
            logger.info('[AUTH] Login POST - %d redirect(s):', len(resp.history))
            for i, r in enumerate(resp.history):
                logger.info('[AUTH]   hop %d: HTTP %d  %s  -> Location: %s',
                             i+1, r.status_code, r.url, r.headers.get('Location', ''))
        else:
            logger.info('[AUTH] Login POST - no redirects (direct response)')

        logger.info('[AUTH] Final URL: %s  HTTP %d', resp.url, resp.status_code)

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
                             cookie.name, cookie.domain or '(none)', cookie.path, len(cookie.value))
        else:
            logger.warning('[AUTH]   WARNING: NO COOKIES in session jar after login POST')

        # Step 5: Strict success check — all three conditions must pass
        # FIX: HTTP status is checked FIRST. A 404 or any non-200 is an
        # immediate failure regardless of URL or body content.
        if resp.status_code != 200:
            raise AuthError(
                f'[AUTH] FAILED: HTTP {resp.status_code} on POST to {login_post_url} '
                f'for Y{study_year}S{sem_digit}. Wrong endpoint or server error.'
            )

        body = resp.text.lower()

        # Check for known failure messages in the response body
        failure_phrases = ['wrong  password', 'wrong password', 'invalid user',
                           'invalid login', 'login failed', 'incorrect password']
        for phrase in failure_phrases:
            if phrase in body:
                raise AuthError(
                    f'[AUTH] FAILED: portal returned failure message '
                    f'"{phrase}" for Y{study_year}S{sem_digit}. Check credentials.'
                )

        # Check if login form is still present (means login was rejected silently)
        u = self.user_field.lower()
        form_still_present = (
            (('name="' + u + '"') in body or ("name='" + u + "'") in body)
            and '<form' in body
        )
        if form_still_present:
            raise AuthError(
                f'[AUTH] FAILED: login form still present in response body '
                f'for Y{study_year}S{sem_digit}. Credentials rejected without error message.'
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
        Return True if the response looks like the login page.
        Checks URL suffix AND body content. Does NOT check HTTP status —
        callers that care about status code check it separately.
        """
        if response.url.lower().endswith('login.jsp'):
            return True
        body = response.text.lower(); u = self.user_field.lower()
        if (('name="' + u + '"') in body or ("name='" + u + "'") in body) and '<form' in body:
            return True
        return False
