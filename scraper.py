import io, logging
from urllib.parse import urlparse
from typing import Callable
import pandas as pd
import requests
from bs4 import BeautifulSoup
from auth import PortalAuth, AuthError
from url_mapper import get_subsite_url, get_login_url, get_examhome_url, overall_semester_number

logger = logging.getLogger(__name__)
_COURSE_CODE = 'A'
_BRANCH_CODE = '04'

class SemesterScraper:
    def __init__(self, auth):
        self.auth = auth

    def get_available_sections(self, admission_year, study_year, sem_digit):
        session = self._login_and_navigate(admission_year, study_year, sem_digit)
        if session is None: return []
        marks_url    = get_subsite_url(admission_year, study_year, sem_digit, self.auth.base_host)
        examhome_url = get_examhome_url(admission_year, study_year, sem_digit, self.auth.base_host)
        return self._read_sections_from_form(session, marks_url, examhome_url, study_year, sem_digit)

    def scrape_all_sections(self, admission_year, study_year, sem_digit, progress_callback=None):
        session = self._login_and_navigate(admission_year, study_year, sem_digit)
        if session is None: return []
        marks_url    = get_subsite_url(admission_year, study_year, sem_digit, self.auth.base_host)
        examhome_url = get_examhome_url(admission_year, study_year, sem_digit, self.auth.base_host)
        sections     = self._read_sections_from_form(session, marks_url, examhome_url, study_year, sem_digit)
        if not sections:
            logger.warning('No sections found for Y%dS%d (batch)', study_year, sem_digit)
            return []
        all_records = []; total = len(sections)
        for idx, sec in enumerate(sections, start=1):
            logger.info('Section %d/%d (subjectcode1=%s)', idx, total, sec['label'])
            excel_bytes = self._post_marks_form(session, marks_url, study_year, sem_digit, sec['value'])
            if excel_bytes is None:
                logger.warning('  Section %s - no data returned', sec['label'])
            else:
                records = self._parse_excel(excel_bytes, study_year, sem_digit, sec['label'])
                logger.info('  Section %s - %d rows', sec['label'], len(records))
                all_records.extend(records)
            if progress_callback: progress_callback(idx, total)
        return all_records

    def scrape_single_section(self, admission_year, study_year, sem_digit, section_value, section_label):
        session = self._login_and_navigate(admission_year, study_year, sem_digit)
        if session is None: return []
        marks_url   = get_subsite_url(admission_year, study_year, sem_digit, self.auth.base_host)
        excel_bytes = self._post_marks_form(session, marks_url, study_year, sem_digit, section_value)
        if excel_bytes is None: return []
        return self._parse_excel(excel_bytes, study_year, sem_digit, section_label)

    def _login_and_navigate(self, admission_year, study_year, sem_digit):
        try:
            session = self.auth.login(admission_year, study_year, sem_digit)
        except AuthError as exc:
            logger.error('Login failed Y%dS%d: %s', study_year, sem_digit, exc)
            return None
        base_host    = self.auth.base_host
        subsite      = get_subsite_url(admission_year, study_year, sem_digit, base_host, page='').rstrip('/')
        examhome_url = get_examhome_url(admission_year, study_year, sem_digit, base_host)
        examhome_soup = None
        try:
            resp = session.get(examhome_url, timeout=30)
            logger.debug('examhome -> %s  status=%d', resp.url, resp.status_code)
            logger.debug('[SCRAPER] Cookies sent to examhome: %s',
                         {c.name: len(c.value) for c in session.cookies})
            examhome_soup = BeautifulSoup(resp.text, 'lxml')
        except Exception as exc:
            logger.warning('Could not reach examhome.jsp: %s', exc)
        try:
            exam_link = self._find_exam_nav_link_from_soup(examhome_soup, subsite)
            if exam_link:
                logger.debug('Following exam nav link: %s', exam_link)
                session.get(exam_link, timeout=30)
            else:
                logger.debug('Exam nav link not found - trying RSMSubAll.jsp directly')
        except Exception as exc:
            logger.warning('Error following exam nav link: %s', exc)
        return session

    def _find_exam_nav_link_from_soup(self, soup, subsite):
        if soup is None: return None
        keywords = ['exam', 'section marks', 'rsm', 'marks']
        parsed   = urlparse(subsite)
        for a in soup.find_all('a', href=True):
            text = a.get_text(strip=True).lower(); href = a['href']
            if any(kw in text for kw in keywords) or any(kw in href.lower() for kw in keywords):
                if href.startswith('http'): return href
                elif href.startswith('/'): return f'{parsed.scheme}://{parsed.netloc}{href}'
                else: return f'{subsite}/{href.lstrip("/")}'
        return None

    def _read_sections_from_form(self, session, marks_url, examhome_url, study_year, sem_digit):
        try:
            cookie_header = '; '.join(f'{c.name}=<len{len(c.value)}>' for c in session.cookies)
            logger.info('[SCRAPER] GET RSMSubAll - cookies in jar: %s', cookie_header or '(empty)')
            headers = {'Referer': examhome_url}
            logger.info('[SCRAPER] GET RSMSubAll - Referer: %s', examhome_url)
            resp = session.get(marks_url, headers=headers, timeout=30)
            logger.info('[SCRAPER] RSMSubAll GET -> final URL: %s  status: %d  size: %d bytes',
                        resp.url, resp.status_code, len(resp.content))
            if resp.url.lower().endswith('login.jsp'):
                logger.warning('[SCRAPER] Y%dS%d - redirected to login.jsp. Session not accepted.',
                               study_year, sem_digit)
                for h, v in resp.headers.items():
                    if h.lower() == 'set-cookie':
                        logger.info('[SCRAPER] Portal Set-Cookie on redirect: value_len=%d', len(v))
                return []
            soup   = BeautifulSoup(resp.text, 'lxml')
            select = soup.find('select', {'name': 'subjectcode1'})
            if not select:
                snippet = resp.text[:500].replace('\n', ' ')
                logger.warning('[SCRAPER] Y%dS%d - subjectcode1 not found. Snippet: %s',
                               study_year, sem_digit, snippet)
                return []
            options = []
            for opt in select.find_all('option'):
                val = opt.get('value', '').strip(); label = opt.get_text(strip=True)
                if val: options.append({'label': label, 'value': val})
            logger.info('[SCRAPER] Y%dS%d - found %d section(s): %s',
                        study_year, sem_digit, len(options), [o['label'] for o in options])
            return options
        except Exception as exc:
            logger.warning('[SCRAPER] Y%dS%d - error reading sections: %s',
                           study_year, sem_digit, exc, exc_info=True)
            return []

    def _post_marks_form(self, session, url, study_year, sem_digit, section_value):
        payload = {'coursecode': _COURSE_CODE, 'branchcode': _BRANCH_CODE,
                   'cyear': str(study_year), 'semester': str(sem_digit),
                   'subjectcode1': section_value, 'excel': 'YES', 'next': 'submit'}
        logger.debug('[SCRAPER] POST %s  payload=%s', url, payload)
        try:
            resp = session.post(url, data=payload, timeout=60)
        except requests.RequestException as exc:
            logger.warning('[SCRAPER] Y%dS%d POST failed: %s', study_year, sem_digit, exc)
            return None
        if resp.status_code != 200:
            logger.warning('[SCRAPER] Y%dS%d HTTP %d', study_year, sem_digit, resp.status_code)
            return None
        content_type = resp.headers.get('Content-Type', '').lower()
        if 'html' in content_type:
            parsed = urlparse(url); parts = parsed.path.strip('/').split('/')
            subsite_base = f'{parsed.scheme}://{parsed.netloc}/{parts[0]}'
            if self.auth._is_login_page(resp, subsite_base):
                logger.warning('[SCRAPER] Y%dS%d sec %s - session expired mid-run',
                               study_year, sem_digit, section_value)
            else:
                logger.warning('[SCRAPER] Y%dS%d sec %s - HTML returned (marks not uploaded?)',
                               study_year, sem_digit, section_value)
            return None
        if len(resp.content) < 512:
            logger.warning('[SCRAPER] Y%dS%d sec %s - only %d bytes, likely empty',
                           study_year, sem_digit, section_value, len(resp.content))
            return None
        return resp.content

    def _parse_excel(self, excel_bytes, study_year, sem_digit, section_label):
        for engine in ('openpyxl', 'xlrd'):
            try:
                df = pd.read_excel(io.BytesIO(excel_bytes), engine=engine, dtype=str, header=0)
                break
            except Exception: continue
        else:
            logger.warning('[SCRAPER] Y%dS%d sec %s - could not parse Excel',
                           study_year, sem_digit, section_label)
            return []
        if df.empty:
            logger.warning('[SCRAPER] Y%dS%d sec %s - Excel has no rows',
                           study_year, sem_digit, section_label)
            return []
        df.columns = [str(c).strip() for c in df.columns]
        df.dropna(how='all', inplace=True); df.fillna('', inplace=True)
        overall = overall_semester_number(study_year, sem_digit)
        sem_label = f'Year {study_year} Sem {sem_digit}'
        df.insert(0, 'Section',     section_label)
        df.insert(0, 'Overall Sem', overall)
        df.insert(0, 'Semester',    sem_label)
        return df.to_dict(orient='records')
