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
        self.login_path = login_path or os.getenv('PORTAL_LOGIN_PATH', '/login.jsp')
        if verify_ssl is not None:
            self.verify_ssl = verify_ssl
        else:
            self.verify_ssl = os.getenv('PORTAL_VERIFY_SSL','false').strip().lower() == 'true'
        if not self.verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def login(self, admission_year, study_year, sem_digit):
        if not self.username or not self.password:
            raise AuthError('Credentials not set. Enter on the app login screen.')
        session = requests.Session()
        session.verify = self.verify_ssl
        from url_mapper import get_subsite_url
        subsite_base   = get_subsite_url(admission_year, study_year, sem_digit, self.base_host, page='').rstrip('/')
        login_page_url = f'{subsite_base}/login.jsp'
        try:
            resp = session.get(login_page_url, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise AuthError(f'Cannot reach login page {login_page_url}: {exc}') from exc
        logger.debug('[AUTH] GET login cookies: %s', {c.name: len(c.value) for c in session.cookies})
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, 'lxml')
        payload = {}
        form = soup.find('form')
        if form:
            for h in form.find_all('input', {'type': 'hidden'}):
                if h.get('name'): payload[h['name']] = h.get('value', '')
            for inp in form.find_all('input'):
                n = inp.get('name', ''); t = inp.get('type', 'text').lower()
                if n and t not in ('password','submit','button','image') and n not in payload:
                    payload[n] = inp.get('value', '')
        payload[self.user_field] = self.username
        payload[self.pass_field] = self.password
        if form:
            for inp in form.find_all('input', {'type': 'submit'}):
                n = inp.get('name', ''); v = inp.get('value', '')
                if n and v and not any(w in v.lower() for w in ['login','log in','signin','sign in']):
                    payload.pop(n, None)
        login_post_url = f'{subsite_base}{self.login_path}'
        logger.debug('[AUTH] POST %s  fields: %s', login_post_url,
                     [k for k in payload if k not in (self.user_field, self.pass_field)])
        try:
            resp = session.post(login_post_url, data=payload, timeout=30, allow_redirects=True)
        except requests.RequestException as exc:
            raise AuthError(f'Login POST failed for Y{study_year}S{sem_digit}: {exc}') from exc
        # Deep diagnostics
        if resp.history:
            logger.info('[AUTH] Login POST - %d redirect(s):', len(resp.history))
            for i, r in enumerate(resp.history):
                logger.info('[AUTH]   hop %d: HTTP %d  %s  -> Location: %s',
                             i+1, r.status_code, r.url, r.headers.get('Location',''))
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
        if self._is_login_page(resp, subsite_base):
            raise AuthError(f'Login rejected for Y{study_year}S{sem_digit} (batch {admission_year}). Check username/password.')
        logger.info('[AUTH] Logged in to Y%dS%d (batch %d)', study_year, sem_digit, admission_year)
        return session

    def ensure_logged_in(self, session, response, admission_year, study_year, sem_digit):
        from url_mapper import get_subsite_url
        subsite_base = get_subsite_url(admission_year, study_year, sem_digit, self.base_host, page='').rstrip('/')
        if self._is_login_page(response, subsite_base):
            logger.warning('[AUTH] Session expired Y%dS%d - re-authenticating', study_year, sem_digit)
            return self.login(admission_year, study_year, sem_digit)
        return session

    def _subsite_base(self, admission_year, study_year, sem_digit):
        from url_mapper import get_subsite_url
        return get_subsite_url(admission_year, study_year, sem_digit, self.base_host, page='').rstrip('/')

    def _is_login_page(self, response, subsite_base):
        if response.url.lower().endswith('login.jsp'): return True
        body = response.text.lower(); u = self.user_field.lower()
        if (('name="' + u + '"') in body or ("name='" + u + "'") in body) and '<form' in body:
            return True
        return False
