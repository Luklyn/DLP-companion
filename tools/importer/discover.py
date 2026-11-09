"""Découverte automatique des pages de liste (attractions, restaurants, spectacles) à partir de l'adresse du site du parc."""
import math
import re
from urllib.parse import urljoin, urlparse, urldefrag

import requests

from common import clean, strip_accents
from links import extract_links
from net import HEADERS, fetch_html, soup_of

KEYS = {
    'attractions': {'attraction', 'attractions', 'ride', 'rides', 'thrill', 'thrills', 'coaster', 'coasters', 'manege', 'maneges',
                    'fahrgeschaft', 'fahrgeschafte', 'attrazioni', 'atracciones', 'atraccion', 'atracoes', 'experiences', 'activites'},
    'restaurants': {'restaurant', 'restaurants', 'dining', 'food', 'eat', 'eating', 'restauration', 'manger', 'gastronomie', 'gastronomy',
                    'essen', 'ristoranti', 'ristorante', 'restaurantes', 'drink', 'drinks'},
    'shows': {'show', 'shows', 'entertainment', 'spectacle', 'spectacles', 'live', 'veranstaltungen', 'spettacoli', 'espectaculos',
              'events', 'parade', 'parades', 'performances'},
}
BAD = {'ticket', 'tickets', 'hotel', 'hotels', 'book', 'booking', 'offers', 'offer', 'stay', 'accommodation', 'blog', 'news', 'faq',
       'contact', 'privacy', 'cookie', 'cookies', 'careers', 'press', 'terms', 'jobs', 'login', 'account', 'basket', 'cart', 'gift',
       'gifts', 'voucher', 'vouchers', 'pass', 'passes', 'groups', 'schools', 'corporate', 'weddings', 'meetings', 'map', 'maps'}
MIN_FICHES = 3


def tokens(s):
    return [t for t in re.split(r'[^a-z0-9]+', strip_accents(s).lower()) if t]


def _norm(url):
    p = urlparse(urldefrag(url)[0])
    return f'{p.scheme}://{p.netloc.lower()}{p.path.rstrip("/")}'


def _score(kind, path, text=''):
    segs = [s for s in urlparse(path).path.split('/') if s]
    if not segs:
        return 0
    ptoks = [tokens(s) for s in segs]
    if any(t in BAD for ts in ptoks for t in ts):
        return 0
    keys = KEYS[kind]
    hits = sum(1 for ts in ptoks for t in ts if t in keys)
    last = 3 if any(t in keys for t in ptoks[-1]) else 0
    txt = 2 if any(t in keys for t in tokens(text)) and len(text) <= 40 else 0
    if hits + txt == 0:
        return 0
    return hits + last + txt - 0.25 * len(segs)


def home_candidates(html, base, host):
    """{kind: {url: score}} d'après les liens de la page d'accueil (menus compris)."""
    soup = soup_of(html)
    out = {k: {} for k in KEYS}
    for a in soup.find_all('a', href=True):
        raw = a['href'].strip()
        if not raw or raw.lower().startswith(('javascript:', 'mailto:', 'tel:', '#')):
            continue
        url = urldefrag(urljoin(base, raw))[0]
        pu = urlparse(url)
        if pu.scheme not in ('http', 'https') or pu.netloc.lower().replace('www.', '') != host:
            continue
        if re.search(r'\.(pdf|jpe?g|png|webp|gif|svg|zip)(\?|$)', pu.path, re.I):
            continue
        text = clean(a.get_text())
        for k in KEYS:
            s = _score(k, url, text)
            if s > 0:
                out[k][url] = max(out[k].get(url, 0), s)
    return out


def _get_text(url, timeout=20):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        return r.text if r.ok else ''
    except Exception:
        return ''


def sitemap_candidates(origin, host, log=print, get_text=_get_text, cap=6000):
    """{kind: {url: score}} d'après les dossiers du sitemap (nombreuses fiches sous un dossier « rides », « dining »…)."""
    out = {k: {} for k in KEYS}
    robots = get_text(origin + '/robots.txt')
    maps = re.findall(r'(?im)^\s*sitemap:\s*(\S+)', robots) or [origin + '/sitemap.xml']
    urls, queue, seen = [], list(maps), set()
    while queue and len(urls) < cap and len(seen) < 8:
        sm = queue.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        txt = get_text(sm)
        locs = re.findall(r'<loc>\s*([^<\s]+)\s*</loc>', txt)
        if '<sitemapindex' in txt:
            queue += locs[:8]
        else:
            urls += locs
    if not urls:
        return out
    log(f'  Sitemap : {len(urls)} adresses')
    folders = {}
    for u in urls:
        pu = urlparse(u)
        if pu.netloc.lower().replace('www.', '') != host:
            continue
        segs = [s for s in pu.path.split('/') if s]
        for i in range(1, len(segs)):
            folders[(pu.scheme, pu.netloc, '/' + '/'.join(segs[:i]))] = folders.get((pu.scheme, pu.netloc, '/' + '/'.join(segs[:i])), 0) + 1
    for (sch, net_, path), n in folders.items():
        if n < MIN_FICHES:
            continue
        for k in KEYS:
            s = _score(k, path)
            if s > 0:
                out[k][f'{sch}://{net_}{path}/'] = s + math.log(n, 10)
    return out


def merge(*cands):
    out = {k: {} for k in KEYS}
    for c in cands:
        for k, d in c.items():
            for u, s in d.items():
                key = _norm(u)
                prev = next((x for x in out[k] if _norm(x) == key), None)
                if prev:
                    out[k][prev] = max(out[k][prev], s)
                else:
                    out[k][u] = s
    return out


def _with_menus(kind, fiches, menus):
    """Restaurants : les cartes dont le lien sort du dossier des fiches (souvent un menu externe) deviennent des fiches « menu seul »."""
    if kind != 'restaurants':
        return fiches
    have = {f['url'] for f in fiches}
    return fiches + [dict(url=m['url'], name=m['name'], card_lines=m['card_lines'], menu_only=True) for m in menus if m['url'] not in have]


def discover_lists(site_url, use_browser=False, log=print, fetch=fetch_html, get_text=_get_text, top=3):
    """Renvoie ({kind: [fiches]}, {kind: url de liste retenue}). fiches = sorties de links.extract_links."""
    site_url = site_url.strip()
    if not re.match(r'https?://', site_url):
        site_url = 'https://' + site_url
    pu = urlparse(site_url)
    origin, host = f'{pu.scheme}://{pu.netloc}', pu.netloc.lower().replace('www.', '')
    log(f'Découverte des pages du site {origin}…')
    html = fetch(site_url, use_browser=use_browser, log=log)
    if len(soup_of(html).find_all('a', href=True)) < 15 and not use_browser:
        log('  Accueil peu fourni : nouvel essai avec le navigateur intégré')
        html = fetch(site_url, use_browser=True, log=log) or html
    cands = merge(home_candidates(html, site_url, host), sitemap_candidates(origin, host, log, get_text))
    lists, chosen = {}, {}
    for kind in ('attractions', 'restaurants', 'shows'):
        ranked = sorted(cands[kind].items(), key=lambda x: -x[1])[:top]
        best, best_n = None, 0
        for url, sc in ranked:
            log(f'  [{kind}] essai : {url}')
            page = fetch(url, use_browser=use_browser, log=log)
            fiches, menus = extract_links(page, url, lambda *_: None) if page else ([], [])
            if len(fiches) < MIN_FICHES and not use_browser and page:
                page2 = fetch(url, use_browser=True, log=log)
                f2, m2 = extract_links(page2, url, lambda *_: None) if page2 else ([], [])
                if len(f2) > len(fiches):
                    fiches, menus = f2, m2
            fiches = _with_menus(kind, fiches, menus)
            if len(fiches) > best_n:
                best, best_n, lists[kind] = url, len(fiches), fiches
        if best:
            chosen[kind] = best
            log(f'  [{kind}] page retenue : {best} ({best_n} fiches)')
        else:
            log(f'  [{kind}] aucune page de liste trouvée automatiquement (voir « Options avancées »)')
    return lists, chosen


def lists_from_urls(urls, use_browser=False, log=print, fetch=fetch_html):
    """Mode manuel : {kind: url de liste} -> {kind: [fiches]}."""
    lists = {}
    for kind, url in urls.items():
        if not (url or '').strip():
            continue
        page = fetch(url.strip(), use_browser=use_browser, log=log)
        fiches, menus = extract_links(page, url.strip(), log) if page else ([], [])
        if len(fiches) < MIN_FICHES and not use_browser and page:
            f2, m2 = extract_links(fetch(url.strip(), use_browser=True, log=log), url.strip(), lambda *_: None)
            if len(f2) > len(fiches):
                fiches, menus = f2, m2
        fiches = _with_menus(kind, fiches, menus)
        lists[kind] = fiches
        log(f'  [{kind}] {len(fiches)} fiches dans {url}')
    return lists
