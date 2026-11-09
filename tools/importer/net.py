"""Téléchargement des pages HTML (requests, avec repli automatique sur un navigateur Chromium pour les sites en JavaScript)."""
import re
import time

import requests
from bs4 import BeautifulSoup

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
    'Accept-Language': 'fr-FR,fr;q=0.9,en;q=0.8,de;q=0.7',
}


# ----------------------------------------------------------------------------- téléchargement des pages
_BROWSER = {'pw': None, 'b': None}
BROWSER_HELP = "pip install playwright  puis  python -m playwright install chromium"


def _browser():
    """Chromium partagé (démarré à la première utilisation, fermé par close_browser())."""
    if _BROWSER['b'] is None:
        from playwright.sync_api import sync_playwright
        _BROWSER['pw'] = sync_playwright().start()
        _BROWSER['b'] = _BROWSER['pw'].chromium.launch()
    return _BROWSER['b']


def close_browser():
    try:
        if _BROWSER['b']:
            _BROWSER['b'].close()
        if _BROWSER['pw']:
            _BROWSER['pw'].stop()
    except Exception:
        pass
    _BROWSER['pw'] = _BROWSER['b'] = None


def fetch_page(url, use_browser=False, log=print, retries=2, timeout=30, want_json=False):
    """(html, [réponses JSON]) d'une page. Navigateur : défilement (lazy-loading) et capture des réponses JSON (XHR/fetch)."""
    bodies = []
    if use_browser:
        page = None
        try:
            ctx = _browser().new_context(user_agent=HEADERS['User-Agent'], locale='fr-FR', viewport={'width': 1366, 'height': 900})
            page = ctx.new_page()

            def on_resp(r):
                try:
                    if want_json and 'json' in r.headers.get('content-type', '') and len(bodies) < 80:
                        bodies.append(r.json())
                except Exception:
                    pass
            page.on('response', on_resp)
            page.goto(url, wait_until='domcontentloaded', timeout=timeout * 1000)
            try:
                page.wait_for_load_state('networkidle', timeout=15000)
            except Exception:
                pass
            for _ in range(10):                       # défilement : déclenche le chargement paresseux des cartes
                page.mouse.wheel(0, 3500)
                page.wait_for_timeout(350)
            for sel in ('button:has-text("Accepter")', 'button:has-text("Accept")', '#onetrust-accept-btn-handler'):   # bandeau cookies
                try:
                    if page.locator(sel).count():
                        page.locator(sel).first.click(timeout=1500)
                        page.wait_for_timeout(800)
                        break
                except Exception:
                    pass
            html = page.content()
            ctx.close()
            return html, bodies
        except ImportError:
            log(f'! Playwright non installé : {BROWSER_HELP}')
        except Exception as e:
            log(f'! Navigateur en échec pour {url} ({e})')
            try:
                if page:
                    page.context.close()
            except Exception:
                pass
    for i in range(retries + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            r.raise_for_status()
            r.encoding = r.apparent_encoding if not r.encoding or r.encoding.lower() == 'iso-8859-1' else r.encoding
            return r.text, bodies
        except Exception as e:
            if i == retries:
                log(f'! Page inaccessible : {url} ({e})')
            else:
                time.sleep(1 + i)
    return '', bodies


def fetch_html(url, use_browser=False, log=print, retries=2, timeout=30):
    """HTML d'une page ('' si échec)."""
    return fetch_page(url, use_browser, log, retries, timeout)[0]


def soup_of(html):
    for parser in ('lxml', 'html.parser'):
        try:
            return BeautifulSoup(html or '', parser)
        except Exception:
            continue
    return BeautifulSoup('', 'html.parser')
