"""Analyse d'une fiche détaillée (HTML) : nom, land, taille mini, description, image, menu, type."""
import re
from urllib.parse import urljoin

from common import clean
from net import soup_of
from translator import is_english

STOP_LINES = re.compile(r'^(ride stats|can (you|i) ride\??|know before you go|did you know\??|sign up to our newsletter|related|you might also like|'
                        r'reviews?|opening times|allerg|accessibility|plan your visit|about alton|share|close \(esc\)|'
                        r'caractéristiques|infos pratiques|horaires|à savoir)', re.I)
SKIP_LINES = re.compile(r'^(book now|book your|buy now|what will you discover\??|know before you go|directions|accessibility|search page|popular searches|'
                        r'view menu|menu|back|next|start|explore|ride features|top tip|theme park top tip|rollercoaster top tip|thrill seekers top tip|now open|new!?)\b', re.I)
LAND_LINES = [re.compile(r'^(?:location|land|zone|area|secteur|univers|bereich|themenwelt)\s*:?\s*(.*)$', re.I),
              re.compile(r'^(?:theme park|parc),\s*(.+?)\s*$', re.I)]
TYPE_WORDS = ['fast food', 'snacks', 'table service', 'buffet', 'restaurant', 'bar', 'coffee']
NOISE_BLOCKS = re.compile(r'cookie|consent|onetrust|privacy|gdpr|modal|dialog|popup|newsletter|breadcrumb|search|megamenu|navigation', re.I)


def _fmt_height(cm):
    if cm < 100:
        return f'{cm} cm'
    s = f'{cm / 100:.2f}'.replace('.', ',')
    return '1 m' if s == '1,00' else f'{s} m'


def _cms(text):
    out = []
    for m in re.finditer(r'\b(\d)\s*[,.]\s*(\d{1,2})\s*(?:m\b|metres?\b|meters?\b)', text, re.I):
        out.append(round(float(f'{m.group(1)}.{m.group(2)}') * 100))
    for m in re.finditer(r'(?<![\d.,])(\d)\s*(?:m\b|metres?\b|meters?\b)', text, re.I):
        out.append(int(m.group(1)) * 100)
    for m in re.finditer(r'\b(\d{2,3})\s*cm\b', text, re.I):
        out.append(int(m.group(1)))
    return [c for c in out if 70 <= c <= 210]


def parse_height(text):
    """Texte de restriction -> « 90 cm (1,25 m pour monter sans adulte) », « 1,40 m », « Pas de restriction »… ('' si rien d'exploitable)."""
    t = clean(text)
    if not t:
        return ''
    if re.search(r'no height|no restriction|any height|all heights|of all ages', t, re.I) and not _cms(t):
        return 'Pas de restriction'
    mx = re.search(r"(?:maximum|can.?t ride|cannot ride|no more than)\D{0,30}?(\d[.,]\d{1,2})\s*m", t, re.I) or \
        re.search(r"(\d[.,]\d{1,2})\s*m(?:etres?)?\s*or over[^.]{0,20}can.?t", t, re.I)
    maxc = round(float(mx.group(1).replace(',', '.')) * 100) if mx else None
    cms = sorted({c for c in _cms(re.sub(r"(?:maximum|can.?t ride|cannot ride)[^.]*", '', t, flags=re.I)) if c != maxc})
    if not cms:
        return ''
    low = cms[0]
    adult = re.search(r'adult|accompan|guardian', t, re.I)
    only_adult = re.search(r'(?:under|below|children under)\s+\d[.,]?\d*\s*m(?:etres?)?\s+must ride with an adult', t, re.I) and not re.search(r'(?:at least|minimum|must be)\s+\d', t, re.I)
    if only_adult:                                   # « Children under 1m must ride with an adult » : pas de taille mini
        return f'Pas de restriction (moins de {_fmt_height(low)} : accompagné d’un adulte)'
    out = _fmt_height(low)
    if adult and len(cms) > 1:
        hi = cms[-1]
        if re.search(r'without an adult|without adult|sans adulte|unaccompanied|on your own', t, re.I) or re.search(r'or over to ride,?\s*and', t, re.I):
            out += f' ({_fmt_height(hi)} pour monter sans adulte)'
        else:
            out += f' (accompagné d’un adulte sous {_fmt_height(hi)})'
    elif adult and re.search(r'with an adult|accompan', t, re.I) and not re.search(r'adult \(\d', t):
        pass
    if maxc:
        out += f' ({_fmt_height(maxc)} maximum)' if '(' not in out else f' – {_fmt_height(maxc)} maximum'
    return out


def _main_scope(soup):
    for t in soup(['script', 'style', 'noscript', 'template', 'svg', 'iframe', 'form', 'nav', 'header', 'footer']):
        t.decompose()
    for t in soup.find_all(True, attrs={'class': NOISE_BLOCKS}):
        try:
            t.decompose()
        except Exception:
            pass
    for t in soup.find_all(True, attrs={'id': NOISE_BLOCKS}):
        try:
            t.decompose()
        except Exception:
            pass
    return soup.find('main') or soup.body or soup


INLINE = ['a', 'strong', 'em', 'b', 'i', 'span', 'u', 'mark', 'small', 'abbr', 'cite', 'sup', 'sub', 'time', 'font']


def _merge_inline(scope):
    """Fusionne les balises « en ligne » (liens, gras…) dans le texte du paragraphe : sans cela, leur texte devenait une ligne à part
    (souvent très courte, donc ignorée) et la description perdait des mots (« les sommets des . »)."""
    from bs4 import NavigableString
    for el in scope.find_all('p'):
        kids = list(el.children)
        if not any(isinstance(c, NavigableString) and c.strip() for c in kids):
            continue
        if any(getattr(c, 'name', None) and c.name not in INLINE and c.name != 'br' for c in kids):
            continue                                   # contient des blocs : on ne touche pas
        for c in kids:
            if getattr(c, 'name', None) in INLINE:
                c.unwrap()
        el.smooth()


def _lines(scope):
    _merge_inline(scope)
    return [clean(x) for x in scope.get_text('\n').split('\n') if clean(x)]


def clean_title(t):
    t = clean(re.sub(r'\s*[|–—]\s*.*$', '', t or '')) if '|' in (t or '') else clean(t)
    return clean(re.sub(r'^(new!?|now open!?)\s*', '', t, flags=re.I))


def cut_description(text, max_chars=380):
    """Coupe à la fin d'une phrase pour rester sous max_chars."""
    text = clean(text)
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    i = max(cut.rfind('. '), cut.rfind('! '), cut.rfind('? '))
    return cut[:i + 1] if i > 80 else cut.rsplit(' ', 1)[0] + '…'


def analyse(html, url='', max_chars=380, list_info=None):
    """Dictionnaire des informations d'une fiche. `list_info` : infos déjà lues dans la carte de la liste (secours)."""
    list_info = list_info or {}
    full = soup_of(html)
    meta = lambda **kw: clean((full.find('meta', attrs=kw) or {}).get('content', ''))
    h1 = full.find('h1')
    name = clean_title(h1.get_text(' ') if h1 else '') or clean_title(meta(property='og:title')) or clean_title(full.title.get_text() if full.title else '')
    image = (meta(property='og:image') or meta(name='twitter:image')).split('?')[0]
    image = urljoin(url, image) if image else ''
    meta_desc = meta(property='og:description') or meta(name='description')
    # liens de menu avant le nettoyage du DOM
    menu = ''
    for a in full.find_all('a', href=True):
        txt, href = clean(a.get_text()), a['href']
        if href.startswith(('javascript', '#', 'mailto', 'tel')):
            continue
        if re.fullmatch(r'(view|see|voir|consulter|download|télécharger)?\s*(the |our |le |la |notre )?(full )?(menu|carte)s?( .{0,25})?', txt, re.I) or re.search(r'tenkites|/menu', href, re.I):
            menu = urljoin(url, href)
            break
    scope = _main_scope(full)
    lines = _lines(scope)
    # nom -> début de la description
    start = 0
    for i, l in enumerate(lines):
        if h1 and clean(h1.get_text(' ')) and clean(h1.get_text(' ')).lower() in l.lower() and len(l) < len(name) + 25:
            start = i + 1
            break
    stats_i = next((i for i, l in enumerate(lines) if i >= start and re.match(r'^(ride stats|can (you|i) ride)', l, re.I)), None)
    desc_lines = []
    for l in lines[start:]:
        if STOP_LINES.match(l):
            break
        if SKIP_LINES.match(l) or len(l) < 40:
            continue
        if re.search(r'we recommend booking|accessibility information|opens in a new window|all menus and restaurant information|subject to availability', l, re.I):
            continue
        if l in desc_lines:
            continue
        desc_lines.append(l)
        if sum(len(x) for x in desc_lines) > max_chars * 1.6:
            break
    desc = cut_description(' '.join(desc_lines), max_chars) if desc_lines else ''
    if len(desc) < 60:
        desc = cut_description(meta_desc, max_chars) or desc
    # land
    land = ''
    for i, l in enumerate(lines):
        for rx in LAND_LINES:
            m = rx.match(l)
            if m:
                val = clean(m.group(1)) or (lines[i + 1] if i + 1 < len(lines) else '')
                val = re.sub(r'\s+(fast food|snacks|vegetarian|vegan|not gluten.*|gluten.*)$', '', val, flags=re.I)
                if val and len(val) <= 40 and not re.search(r'\d{3}', val):
                    land = clean(val)
                    break
        if land:
            break
    if not land and start >= 2:      # lieu (hôtel, zone) affiché juste au-dessus du titre
        cand = lines[start - 2]
        if len(cand) <= 40 and cand.lower() != name.lower() and not re.search(r'\d{3}|book|menu|search|skip', cand, re.I):
            land = cand
    land = land or list_info.get('card_land', '')
    # taille mini
    hbox = ''
    for i, l in enumerate(lines):
        if re.match(r'^can (you|i) ride', l, re.I):
            hbox = ' '.join(lines[i + 1:i + 3])
            break
    height = parse_height(hbox) if hbox else ''
    if not height:
        height = parse_height(list_info.get('card_height', ''))
    # type (restaurants) : mots-clés de la ligne « Theme Park, Land  Fast Food  Vegetarian… »
    cat = ''
    blob = ' '.join(l for l in lines if re.match(r'^(theme park|parc),', l, re.I)) + ' ' + ' '.join(lines[max(0, start):start + 14])
    for w in TYPE_WORDS:
        if re.search(rf'\b{w}\b', blob, re.I):
            cat = w
            break
    cat = cat or list_info.get('card_category', '')
    return dict(name=name, land=land, height=height, description=desc, desc_is_en=is_english(desc), image=image, menu=menu,
                category=cat, url=url, has_stats=stats_i is not None)
