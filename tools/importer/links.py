"""Extraction des liens de fiches détaillées depuis la page « liste » (attractions, restaurants ou spectacles) enregistrée en HTML."""
import re
from collections import Counter
from urllib.parse import urljoin, urlparse, urldefrag

from common import clean, norm_name
from net import soup_of

SKIP_SCHEMES = ('javascript:', 'mailto:', 'tel:', '#', 'data:')
SKIP_EXT = re.compile(r'\.(pdf|jpe?g|png|webp|gif|svg|zip|css|js)(\?|$)', re.I)
HEIGHT_LINE = re.compile(r'^(minimum|min\b|height|taille|hauteur|no height|any height|no restriction|pas de restriction)', re.I)
GENERIC = re.compile(r'^(see|read|find out|learn|discover|view|explore|voir|découvrir|en savoir|lire|more|plus|click|book|menu|new\b|\.{2,})', re.I)


def detect_base(html, given=''):
    """URL de base pour résoudre les liens relatifs : champ saisi > commentaire « saved from url » > canonical > og:url."""
    if given.strip():
        return given.strip()
    m = re.search(r'saved from url=\(\d+\)\s*(\S+)', html[:2000])
    if m:
        return m.group(1)
    s = soup_of(html)
    for sel in ({'rel': 'canonical'},):
        t = s.find('link', attrs=sel)
        if t and t.get('href'):
            return t['href']
    t = s.find('meta', attrs={'property': 'og:url'})
    return t.get('content', '') if t else ''


def _key(url):
    p = urlparse(urldefrag(url)[0])
    return f'{p.scheme}://{p.netloc.lower()}{p.path.rstrip("/")}'


def _card(a):
    """(nom, lignes de texte) de la « carte » qui entoure le lien : plus grand ancêtre ne contenant que ce lien."""
    href = a.get('href')
    node, best = a, a
    for _ in range(6):
        par = node.parent
        if par is None or par.name in ('body', 'html', 'main'):
            break
        hrefs = {x.get('href') for x in par.find_all('a', href=True)}
        if hrefs - {href}:
            break
        node = best = par
    lines = [clean(x) for x in best.get_text('\n').split('\n') if clean(x)]
    head = best.find(['h1', 'h2', 'h3', 'h4', 'h5'])
    name = clean(head.get_text()) if head else ''
    if not name:
        name = clean(a.get('aria-label') or a.get('title') or a.get_text())
    if (not name or GENERIC.match(name)) and lines:
        name = next((l for l in lines if not GENERIC.match(l) and len(l) <= 60 and not HEIGHT_LINE.match(l)), name)
    return name, lines


def _slug_name(url):
    seg = [x for x in urlparse(url).path.split('/') if x]
    return re.sub(r'[-_]+', ' ', seg[-1]).strip().title() if seg else ''


def extract_links(html, base_url='', log=print):
    """Renvoie (fiches, menus) :
    fiches = [{url, name, card_lines}] : liens de fiches (même site, même « dossier » que la majorité des cartes)
    menus  = [{url, name}] : cartes de la liste dont le lien sort du dossier des fiches (souvent un menu / une page externe)."""
    base = detect_base(html, base_url)
    soup = soup_of(html)
    for t in soup(['script', 'style', 'noscript', 'template']):
        t.decompose()
    host = urlparse(base).netloc.lower().replace('www.', '')
    items, seen = [], {}
    for a in soup.find_all('a', href=True):
        raw = a['href'].strip()
        if not raw or raw.lower().startswith(SKIP_SCHEMES) or SKIP_EXT.search(raw):
            continue
        if any(p.name in ('nav', 'header', 'footer') for p in a.parents):
            continue
        url = urljoin(base, raw)
        if not url.startswith('http'):
            continue
        name, lines = _card(a)
        k = _key(url)
        if k in seen:
            if len(lines) > len(seen[k]['card_lines']):
                seen[k].update(name=name or seen[k]['name'], card_lines=lines)
            continue
        seen[k] = dict(url=urldefrag(url)[0], name=name, card_lines=lines, _host=urlparse(url).netloc.lower().replace('www.', ''), _path=urlparse(url).path)
        items.append(seen[k])
    if not items:
        return [], []
    # Le « dossier » des fiches : parent de chemin le plus fréquent parmi les liens du même site
    same = [i for i in items if i['_host'] == host or not host]
    parents = Counter('/'.join(i['_path'].rstrip('/').split('/')[:-1]) for i in same if len([x for x in i['_path'].split('/') if x]) >= 2)
    if not parents:
        return [], []
    folder, n = parents.most_common(1)[0]
    bp = urlparse(base).path.rstrip('/') if base else ''
    # La page de liste elle-même est la meilleure indication : ses fiches sont « un cran en dessous »
    if bp and parents.get(bp):
        folder, n = bp, parents[bp]
    log(f'  Dossier des fiches détecté : {folder or "/"}  ({n} liens)')
    base_path = base and urlparse(base).path.rstrip('/')
    # Dossiers « frères » : une page « /attractions » dont les fiches sont rangées par thème (attractions-famille, attractions-enfants,
    # attractions-frissons…) doit toutes les reprendre, pas seulement la plus fournie.
    folders = {folder}
    if bp:
        stem, par = bp.rsplit('/', 1)[-1], bp.rsplit('/', 1)[0]
        if folder == bp or (folder.rsplit('/', 1)[0] == par and folder.rsplit('/', 1)[-1].startswith(stem + '-')):
            for f2, n2 in parents.items():
                if f2 != folder and n2 >= 2 and f2.rsplit('/', 1)[0] == par and (f2 == bp or f2.rsplit('/', 1)[-1].startswith(stem + '-')):
                    folders.add(f2)
            if len(folders) > 1:
                log(f'  Dossiers voisins repris : {", ".join(sorted(folders - {folder}))}')
    fiches, menus = [], []
    for i in items:
        parent = '/'.join(i['_path'].rstrip('/').split('/')[:-1])
        entry = dict(url=i['url'], name=i['name'] or _slug_name(i['url']), card_lines=i['card_lines'])
        if i['_host'] == host and parent in folders and i['_path'].rstrip('/') != base_path:
            entry['name'] = entry['name'] or _slug_name(i['url'])
            fiches.append(entry)
        elif i['name'] and len(i['card_lines']) >= 2:
            menus.append(entry)
    return fiches, menus


NOISE_LINES = re.compile(r'^(retiré des favoris|ajouter aux favoris|ajouter|nouveau|new|favoris|voir la page|en savoir plus)$', re.I)
H_ALONE = re.compile(r'^hauteur minimum seul', re.I)
H_ACC = re.compile(r'^hauteur minimum accompagn', re.I)
H_MAX = re.compile(r'^hauteur maxim', re.I)


def card_height_text(lines):
    """Cartes du type « HAUTEUR MINIMUM SEUL » / « 130 » (valeur sur la ligne suivante) -> texte lisible par detail.parse_height."""
    vals = {}
    for i, l in enumerate(lines[:-1]):
        key = 'alone' if H_ALONE.match(l) else 'acc' if H_ACC.match(l) else 'max' if H_MAX.match(l) else None
        m = re.fullmatch(r'(\d{2,3})(?:\s*cm)?', lines[i + 1].strip()) if key else None
        if m:
            vals[key] = int(m.group(1))
    if not vals:
        return ''
    out = []
    if 'alone' in vals and 'acc' in vals and vals['acc'] != vals['alone']:
        out.append(f"Minimum {vals['acc']} cm with an adult, {vals['alone']} cm without an adult.")
    elif 'alone' in vals:
        out.append(f"Minimum {vals['alone']} cm.")
    elif 'acc' in vals:
        out.append(f"Minimum {vals['acc']} cm with an adult.")
    if 'max' in vals:
        out.append(f"Maximum {vals['max'] / 100:.2f} m.")
    return ' '.join(out)


def annotate_cards(fiches, type_keys=()):
    """Déduit de chaque carte : taille (lignes évoquant une hauteur), catégorie (lignes connues de type_keys) et land (ligne courte répétée sur plusieurs cartes)."""
    cnt = Counter(l for f in fiches for l in set(f['card_lines']))
    tk = {k.lower() for k in type_keys}
    common = 0.6 * len(fiches) if len(fiches) >= 5 else 10 ** 9      # une ligne présente sur presque toutes les cartes est une étiquette de page, pas un land
    for f in fiches:
        height = cat = land = ''
        for l in f['card_lines']:
            if l == f['name'] or len(l) > 80 or NOISE_LINES.match(l):
                continue
            if l.lower() in tk:
                cat = cat or l
            elif HEIGHT_LINE.match(l):
                height = height or l
            elif cnt[l] >= 2 and cnt[l] < common and len(l) <= 40 and not land and not re.fullmatch(r'\d{2,3}', l) and not re.search(r'ferm|pendant|closed|ouvert| / ', l, re.I):
                land = re.sub(r'^Theme Park,\s*', '', l)
        height = card_height_text(f['card_lines']) or height
        f.update(card_height=height, card_category=cat, card_land=land)
    return fiches
