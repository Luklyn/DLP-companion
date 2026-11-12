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


LOAD_MORE_JS = """() => {
  const rx = /^(voir|afficher|charger|chargez|load|show|see|montrer|d[ée]couvrir)\\s+(plus|more|tout|tous|all|davantage|d'autres|les suivants)|^(plus|more|davantage)(\\s+(de|d'|of)\\s+\\S+.*)?$|^(plus|more) results|^afficher la suite|^voir la suite|^load more/i;
  let n = 0;
  for (const el of document.querySelectorAll('button, a, [role=button]')) {
    const t = (el.innerText || el.getAttribute('aria-label') || '').trim();
    if (!t || t.length > 40 || !rx.test(t)) continue;
    const href = el.getAttribute('href');
    if (el.tagName === 'A' && href && !href.startsWith('#') && !href.startsWith('javascript')) continue;   // un vrai lien de navigation n'est pas un « voir plus »
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height || el.disabled) continue;
    el.scrollIntoView({block: 'center'}); el.click(); n++;
  }
  return n;
}"""


def _scroll_to_end(page, log=print, max_seconds=90):
    """Fait défiler la page jusqu'au bout et clique sur les boutons « Voir plus » : charge les contenus qui n'apparaissent qu'au défilement.
    S'arrête quand ni la hauteur de la page ni le nombre de liens n'évoluent pendant 4 tours (ou après max_seconds)."""
    t0 = time.time(); last = None; stable = 0; clicks = 0
    while time.time() - t0 < max_seconds and stable < 4:
        try:
            clicked = page.evaluate(LOAD_MORE_JS) or 0
            clicks += clicked
            state = page.evaluate("() => [document.documentElement.scrollHeight, document.querySelectorAll('a[href]').length, document.querySelectorAll('img').length]")
            page.evaluate("() => window.scrollTo(0, document.documentElement.scrollHeight)")
            page.mouse.wheel(0, 2500)
            page.keyboard.press('End')
            page.wait_for_timeout(700 if clicked else 450)
        except Exception:
            break
        stable = stable + 1 if (state == last and not clicked) else 0
        last = state
    try:
        page.evaluate("() => window.scrollTo(0, 0)")
    except Exception:
        pass
    if clicks or (last and last[1] > 0):
        log(f'   Défilement terminé ({int(time.time() - t0)} s, {last[1] if last else "?"} liens' + (f', {clicks} clic(s) sur « voir plus »' if clicks else '') + ')')


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
            for sel in ('button:has-text("Accepter")', 'button:has-text("Accept")', '#onetrust-accept-btn-handler'):   # bandeau cookies (avant le défilement : il peut le bloquer)
                try:
                    if page.locator(sel).count():
                        page.locator(sel).first.click(timeout=1500)
                        page.wait_for_timeout(800)
                        break
                except Exception:
                    pass
            _scroll_to_end(page, log)
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
