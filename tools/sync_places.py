#!/usr/bin/env python3
"""Synchronise data/<parc>/*.json avec l'API themeparks.wiki.

Pour chaque destination de parks.json :
  1. lit l'API (attractions, restaurants, spectacles uniquement) ;
  2. repère les lieux absents des fichiers de données (comparaison par UUID) ;
  3. cherche leur fiche sur le site officiel (version française quand elle existe) pour récupérer
     le nom affiché, la description, la zone, le type et la taille minimale ;
  4. ajoute les nouvelles fiches aux fichiers JSON et écrit un rapport (corps de la pull request).

Règles : aucun texte n'est traduit ni inventé. Le nom d'un lieu est celui de la page du site ; sans page, c'est le nom de l'API.
Une taille minimale n'est écrite que si la page la donne. Les lieux existants ne sont jamais modifiés ni supprimés
(les écarts de nom et les lieux disparus de l'API sont seulement signalés dans le rapport).

Usage : python tools/sync_places.py [--resort ID] [--dry-run] [--no-web] [--browser] [--report sync_report.md]
"""
import argparse
import json
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMP = os.path.join(ROOT, 'tools', 'importer')
sys.path.insert(0, IMP)

import api_client  # noqa: E402
import builder  # noqa: E402
import discover  # noqa: E402
from common import Log, norm_name  # noqa: E402
import detail  # noqa: E402
from detail import analyse  # noqa: E402
import siteprofile  # noqa: E402
from links import annotate_cards  # noqa: E402
from matching import match, similarity, slug_name  # noqa: E402
from net import close_browser, fetch_html  # noqa: E402

KINDS = ('attractions', 'restaurants', 'shows')
CATEGORY_TO_KIND = {'attraction': 'attractions', 'restaurant': 'restaurants', 'spectacle': 'shows'}
UUID = 'UUID Themeparks.wiki'
LAT, LNG = 'Coordonnées GPS (lat)', 'Coordonnées GPS (lng)'
NOSTATUS = 'Statut inconnu'     # « oui » : lieu absent du flux live de l'API (l'appli l'affiche avec « état d'ouverture non connu »)
ORDER = {
    'attractions': ['Code', UUID, 'Nom', 'Description', 'Parc', 'Land', 'Type', 'Taille mini', LAT, LNG, NOSTATUS],
    'restaurants': ['Code', UUID, 'Nom', 'Description', 'Menu', 'Tarif', 'Type', 'Parc', 'Land', LAT, LNG, NOSTATUS],
    'shows': ['Code', UUID, 'Nom', 'Description', 'Type', 'Parc', 'Land', LAT, LNG, NOSTATUS],
}
LABEL = {'attractions': 'Attractions', 'restaurants': 'Restaurants', 'shows': 'Spectacles'}
CLAIM_SCORE = 0.9      # une fiche du site « appartient » déjà à un lieu connu au-delà de ce score de ressemblance


def read_json(path, default=None):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def write_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=2))   # même format que tools/build_park.py (sans retour final)


def synced_kinds(resort):
    """Catégories dont l'API fournit des données pour ce parc (parks.json : noLiveData liste celles qui n'en ont pas)."""
    skip = {CATEGORY_TO_KIND.get(c) for c in resort.get('noLiveData') or []}
    return [k for k in KINDS if k not in skip]


def park_value(resort, ent, parent_names):
    """Valeur de la colonne « Parc » (parks.json → parks[].value) du lieu, ou None si le parc est inconnu."""
    parks = resort.get('parks') or []
    for p in parks:
        if p.get('themeparksParkId') and p['themeparksParkId'] == ent.get('parentId'):
            return p['value']
    pname = norm_name(parent_names.get(ent.get('parentId'), ''))
    for p in parks:
        if pname and pname in (norm_name(p['value']), norm_name(p.get('label', ''))):
            return p['value']
    return parks[0]['value'] if len(parks) == 1 else None


def clean_name(s):
    return re.sub(r'\s+', ' ', re.sub('[\u200b\u200c\u200d\ufeff\u00a0]', ' ', (s or ''))).strip()


AUDIENCE = re.compile(r'^(?:(?:Adultes?|Jeunes adolescents?|Adolescents?|Enfants?|Tout[- ]petits?|Bébés?|Tout âge|All ages)[,\s]*)+', re.I)


def api_slug(name):
    """« Let’s Sing Christmas! » → « lets-sing-christmas » (forme des adresses anglaises du site)."""
    import unicodedata
    t = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode().lower()
    t = re.sub(r"['’`]", '', t)
    return re.sub(r'[^a-z0-9]+', '-', t).strip('-')


def fr_via_english(flat, mt, view, patterns, use_browser, log, overrides=None):
    """Dernier recours : l'adresse anglaise d'un lieu se devine à partir du nom de l'API (patrons de sites.json, `{slug}`) ;
    cette page indique l'adresse de sa version française (<link rel="alternate" hreflang="fr…">), qu'on ajoute aux fiches."""
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin
    for ei, e in enumerate(flat):
        if ei in mt:
            continue
        forced = (overrides or {}).get(e['id'].lower()) or (overrides or {}).get(str(e.get('externalId') or '').lower())
        if forced:      # adresse française indiquée à la main dans sites.json (« url_overrides »)
            view.append(dict(url=forced, name=slug_name(forced), kinds={e['kind']}, names=[slug_name(forced), e['name']], card_lines=[], menu_only=False, slug_only=True))
            mt[ei] = (len(view) - 1, 0.95)
            log(f'   {e["name"]} → {forced} (adresse indiquée dans sites.json)')
            continue
        for pat in patterns.get(e['kind'], []):
            url = pat.replace('{slug}', api_slug(e['name']))
            html = ''
            for browser in (False, True):      # requête simple, puis navigateur intégré (pages en JavaScript)
                try:
                    html = fetch_html(url, use_browser=browser, log=lambda *_: None)
                except Exception:
                    html = ''
                if html and '<h1' in html.lower() and 'hreflang' in html.lower():
                    break
            else:
                log(f'   (page anglaise introuvable : {url})')
                continue
            soup = BeautifulSoup(html, 'html.parser')
            frs = {l['hreflang'].lower(): urljoin(url, l['href']) for l in soup.find_all('link', hreflang=True, href=True)
                   if l['hreflang'].lower().startswith('fr') and 'alternate' in (l.get('rel') or [])}
            fr = frs.get('fr-fr') or frs.get('fr') or next(iter(frs.values()), None)    # France d'abord (pas fr-be / fr-ch)
            if not fr:
                continue
            view.append(dict(url=fr, name=slug_name(fr), kinds={e['kind']}, names=[slug_name(fr), e['name']], card_lines=[], menu_only=False, slug_only=True))
            mt[ei] = (len(view) - 1, 0.95)
            log(f'   {e["name"]} → {fr} (via la page anglaise)')
            break


def next_code(base, used):
    code = base or 'api'
    n = 2
    while code in used:
        code, n = f'{base}-{n}', n + 1
    used.add(code)
    return code


def find_new(resort, ents, existing, ignored, allow_no_live=()):
    """Lieux de l'API absents des données : {kind: [entités]} (uniquement les catégories suivies et ceux qui ont des données live)."""
    known = {k: {str(r.get(UUID, '')).lower() for r in existing[k]} for k in KINDS}
    every = set().union(*known.values())
    out = {k: [] for k in KINDS}
    kinds = synced_kinds(resort)
    for e in ents:
        if e['kind'] not in kinds or e['id'].lower() in every or e['id'].lower() in ignored:
            continue
        if not e['live'] and e['kind'] not in allow_no_live:    # l'appli masque les lieux sans statut dans l'API, sauf s'ils sont marqués « Statut inconnu »
            continue
        out[e['kind']].append(e)
    return out


def sitemap_urls(origin, cap=30000):
    """Toutes les adresses du sitemap du site (utile quand les pages de liste sont chargées en JavaScript)."""
    robots = discover._get_text(origin + '/robots.txt')
    queue = re.findall(r'(?im)^\s*sitemap:\s*(\S+)', robots) or [origin + '/sitemap.xml']
    urls, seen = [], set()
    while queue and len(urls) < cap and len(seen) < 12:
        sm = queue.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        txt = discover._get_text(sm)
        locs = re.findall(r'<loc>\s*([^<\s]+)\s*</loc>', txt)
        if '<sitemapindex' in txt:
            queue += locs[:12]
        else:
            urls += locs
    return urls


def lists_from_sitemap(site, prefixes, kinds, log, keep_folders=None):
    """{kind: [fiches]} d'après les adresses du sitemap placées sous un dossier donné (ex. /fr-fr/divertissements/ → /parc-x/nom-du-spectacle)."""
    from urllib.parse import urlparse
    pu = urlparse(site)
    urls = sitemap_urls(f'{pu.scheme}://{pu.netloc}')
    log(f'  Sitemap : {len(urls)} adresses')
    lists = {}
    for kind, prefix in prefixes.items():
        if kind not in kinds:
            continue
        seen, fiches = set(), []
        for u in urls:
            path = urlparse(u).path
            rest = path[len(prefix):].strip('/') if path.startswith(prefix) else ''
            keep = (keep_folders or {}).get(kind)
            if keep and rest.split('/')[0] not in keep:      # ex. restaurants d'hôtels ou de Disney Village : exclus
                continue
            if rest.count('/') >= 1 and u not in seen:     # un niveau de dossier (parc) puis la fiche
                seen.add(u)
                fiches.append(dict(url=u, name=slug_name(u), card_lines=[], menu_only=False, slug_only=True))
        lists[kind] = fiches
        log(f'  [{kind}] {len(fiches)} fiche(s) dans le sitemap sous {prefix}')
    return lists


def collect_fiches(site, cfg, kinds, use_browser, log):
    """Fiches détaillées du site officiel : [{url, name, kinds, names, card_*}]."""
    list_urls = cfg.get('list_urls')
    lists = {}
    if list_urls and any((v or '').strip() for v in list_urls.values()):
        lists = discover.lists_from_urls(list_urls, use_browser, log, fetch_html)
    elif site and not cfg.get('sitemap_prefixes'):
        lists, _ = discover.discover_lists(site, use_browser, log, fetch_html, discover._get_text)
    if cfg.get('sitemap_prefixes') and site:
        for kind, fiches in lists_from_sitemap(site, cfg['sitemap_prefixes'], kinds, log, cfg.get('keep_folders')).items():
            lists[kind] = lists.get(kind, []) + fiches
    if not lists:
        return []
    pool = {}
    for kind, fiches in lists.items():
        for f in fiches:
            e = pool.setdefault(f['url'], dict(url=f['url'], name=f['name'], card_lines=f['card_lines'], kinds=set(), names=[],
                                               menu_only=bool(f.get('menu_only')), slug_only=bool(f.get('slug_only'))))
            if not f.get('menu_only'):
                e['menu_only'] = False
            e['kinds'].add(kind)
            if f['name'] and f['name'] not in e['names']:
                e['names'].append(f['name'])
    mp = builder.load_mappings()
    fiches = annotate_cards(list(pool.values()), type_keys=(mp.get('types') or {}).keys(), log=log)
    for f in fiches:
        s = '' if f.get('menu_only') else slug_name(f['url'])
        if s and s not in f['names']:
            f['names'].append(s)
    return fiches


def learn_structure(fiches, rid, log, use_browser, sample=8):
    """Avant l'import : télécharge quelques fiches du site et en déduit où lire le land (phrase-modèle qui ne change que d'un mot)."""
    detail.PROFILE.clear()
    pool = [f for f in fiches if not f.get('menu_only') and not f.get('slug_only')]
    if len(pool) < 4:
        return
    step = max(1, len(pool) // sample)
    picks = pool[::step][:sample]
    log(f'   Analyse de la structure du site ({len(picks)} fiches échantillon)…')
    got = builder.download(picks, os.path.join(tempfile.gettempdir(), f'sync_{rid}'), log, lambda: False, lambda *_: None, fetch_html, use_browser)
    pages = []
    for f in picks:
        p = got.get(f['url'])
        if p:
            with open(p, encoding='utf-8', errors='ignore') as fh:
                pages.append(detail._lines(detail._main_scope(detail.soup_of(fh.read()))))
    rx, examples = siteprofile.mine_land_template(pages)
    if rx:
        detail.PROFILE['land_rx'] = rx
        log(f'   Structure des fiches : land lu dans la phrase « {rx.pattern.split("(")[0].replace(chr(92) + "s+", " ").replace(chr(92), "").strip("^ ")} … » (exemples : {", ".join(examples[:4])})')
    else:
        log('   Structure des fiches : aucun modèle de phrase pour le land (lecture classique)')


def claimed_fiches(fiches, existing):
    """Indices des fiches déjà rattachées à un lieu connu (sinon elles risqueraient d'être attribuées à un nouveau lieu)."""
    claimed = set()
    known = [r.get('Nom', '') for k in KINDS for r in existing[k]]
    for i, f in enumerate(fiches):
        if any(similarity(n, fn) >= CLAIM_SCORE for n in known for fn in f['names'] if fn):
            claimed.add(i)
    return claimed


def site_candidates(resort, fiches, claimed, ents, matched, args, log):   # ents : seulement les NOUVEAUX lieux de l'API (ceux déjà connus sont dans `claimed`)
    """Fiches du site qui n'existent ni dans les données ni dans l'API : lieux à ajouter « à la main » (sans UUID)."""
    api_names = [clean_name(e['name']) for e in ents]
    pick = []
    for i, f in enumerate(fiches):
        if i in claimed or i in matched or f.get('menu_only'):
            continue
        if any(similarity(n, an) >= CLAIM_SCORE for n in f['names'] if n for an in api_names):
            continue        # présent dans l'API : traité par la détection normale
        pick.append(i)
    log(f'   {len(pick)} fiche(s) du site absente(s) de l’API et des données')
    out = []
    if pick:
        cache = os.path.join(tempfile.gettempdir(), f'sync_{resort["id"]}_cand')
        got = builder.download([fiches[i] for i in pick], cache, log, lambda: False, lambda *_: None, fetch_html, args.browser)
        mappings = builder.load_mappings()
        types = {k.lower(): v for k, v in (mappings.get('types') or {}).items()}
        lands = {k.lower(): v for k, v in (mappings.get('lands') or {}).items()}
        for i in pick:
            f = fiches[i]
            path = got.get(f['url'])
            pg = {}
            if path:
                with open(path, encoding='utf-8', errors='ignore') as fh:
                    pg = analyse(fh.read(), f['url'], 380, list_info=dict(name=f['name'], card_height=f.get('card_height', ''),
                                                                         card_category=f.get('card_category', ''), card_land=f.get('card_land', '')))
            name = clean_name(pg.get('name')) or (f['names'][0] if f['names'] else '')
            if not name or GENERIC_NAME.search(name):
                name = (f['names'][0] if f['names'] else name)
            land = clean_name(pg.get('land'))
            cat = clean_name(pg.get('category'))
            kinds = [k for k in KINDS if k in f['kinds']]
            out.append(dict(kind=kinds[0] if kinds else '', kinds=kinds, nom=name, url=f['url'], land=lands.get(land.lower(), land) if land else '',
                            type=types.get(cat.lower(), '') if cat else '', category=cat, height=pg.get('height') or '', menu=pg.get('menu') or '',
                            description=AUDIENCE.sub('', pg.get('description') or '').strip()))
    if not args.dry_run:
        os.makedirs(os.path.join(ROOT, 'site-candidates'), exist_ok=True)
        write_json(os.path.join(ROOT, 'site-candidates', f'{resort["id"]}.json'), out)
    return out


def add_english_alternates(view, claimed, kinds, cache_dir, log, use_browser):
    """Le nom français n'a souvent rien à voir avec le nom de l'API (« Let's Sing Christmas! » / « Chantons Noël ! »).
    Les pages françaises indiquent leur équivalent anglais (<link rel="alternate" hreflang="en-…">) : l'adresse de cette page
    reprend en général le nom anglais, qu'on ajoute aux noms de la fiche pour l'appariement avec l'API."""
    from bs4 import BeautifulSoup
    targets = [i for i, f in enumerate(view) if i not in claimed and (f['kinds'] & kinds) and not f.get('menu_only')]
    if not targets:
        return
    got = builder.download([view[i] for i in targets], cache_dir, log, lambda: False, lambda *_: None, fetch_html, use_browser)
    found = 0
    for i in targets:
        path = got.get(view[i]['url'])
        if not path:
            continue
        with open(path, encoding='utf-8', errors='ignore') as fh:
            soup = BeautifulSoup(fh.read(), 'html.parser')
        for link in soup.find_all('link', rel=lambda r: r and 'alternate' in ([r] if isinstance(r, str) else r)):
            lang = (link.get('hreflang') or '').lower()
            if lang.startswith('en') and link.get('href'):
                name = slug_name(link['href'])
                if name and name not in view[i]['names']:
                    view[i]['names'].append(name)
                    found += 1
    log(f'   {found} équivalent(s) anglais relevé(s) sur {len(targets)} fiche(s) du site')


def pair_by_elimination(new_ents, mt, fiches, claimed, used_fi):
    """Un seul lieu sans fiche et une seule fiche libre dans la même catégorie : le nom peut être totalement différent
    (« Pirate Battle » / « La bataille des pirates »), on les associe, avec un avertissement."""
    paired = {}
    for kind in KINDS:
        todo = [ei for ei, e in enumerate(new_ents) if e['kind'] == kind and ei not in mt]
        free = [fi for fi, f in enumerate(fiches)
                if fi not in claimed and fi not in used_fi and kind in f['kinds'] and f['names'] and not f.get('menu_only')]
        if len(todo) == 1 and len(free) == 1:
            paired[todo[0]] = (free[0], 0.0)
            used_fi.add(free[0])
    return paired


GENERIC_NAME = re.compile(r'(?i)^(se restaurer|divertissements|spectacles disney|restaurants?|manger)\b|parades et rencontres')
MEET_API = re.compile(r'(?i)^(meet|rencontre)\b')


def is_listing_url(url):
    """Page de liste (ex. /restaurants/manger-a-disneyland-paris, /divertissements/parc-disneyland) : pas une fiche."""
    from urllib.parse import urlparse
    return len([x for x in urlparse(url or '').path.split('/') if x]) < 4


def build_entry(kind, e, park, pg, mappings, notes):
    """Fiche JSON d'un lieu : données de l'API + ce que la page du site a réellement donné."""
    types = {k.lower(): v for k, v in (mappings.get('types') or {}).items()}
    lands = {k.lower(): v for k, v in (mappings.get('lands') or {}).items()}
    row = {'Code': e['externalId'], UUID: e['id'], 'Nom': e['name'], 'Parc': park, LAT: e['lat'], LNG: e['lng']}
    missing = []
    if pg:
        name = clean_name(pg.get('name'))
        if name and GENERIC_NAME.search(name):      # titre de page de liste, pas le nom du lieu : on garde le nom de l'API
            notes.append(f'nom de la page trop générique (« {name} ») : nom de l’API conservé')
            name = ''
        if name and len(name) <= 90:
            row['Nom'] = name
        land = clean_name(pg.get('land'))
        if land:
            row['Land'] = lands.get(land.lower(), land)
        cat = clean_name(pg.get('category'))
        if cat:
            if types.get(cat.lower()):
                row['Type'] = types[cat.lower()]
            else:
                notes.append(f'Type « {cat} » absent de `tools/importer/mappings.json` (non renseigné)')
        if kind == 'attractions' and pg.get('height'):
            row['Taille mini'] = pg['height']
        if kind == 'restaurants' and pg.get('menu'):
            row['Menu'] = pg['menu']
        if pg.get('description'):
            row['Description'] = AUDIENCE.sub('', pg['description']).strip() or pg['description']
            if pg.get('desc_is_en'):
                notes.append('description en anglais sur la page du site (non traduite)')
    for col in ('Land', 'Type', 'Description') + (('Taille mini',) if kind == 'attractions' else ()):
        if col in ORDER[kind] and not row.get(col):
            missing.append(col)
    if not e['live']:
        row[NOSTATUS] = 'oui'
    if kind == 'shows' and (MEET_API.match(e['name']) or re.match(r'P\dMG', str(e['externalId']))):
        row['Type'] = 'Rencontre avec un personnage'
    out = {k: row[k] for k in ORDER[kind] if row.get(k) not in (None, '')}
    return out, missing


def inspect_resort(resort, ents, existing, parent_names, site_cfg, log):
    """Diagnostic : lieux de l'API absents des données (avec leur parent) et dossiers du sitemap, pour régler les filtres."""
    from collections import Counter
    from urllib.parse import urlparse
    known = {str(r.get(UUID, '')).lower() for k in KINDS for r in existing[k]}
    log('--- Lieux de l\'API absents des données (restaurants / spectacles) ---')
    for e in ents:
        if e['kind'] in ('restaurants', 'shows') and e['id'].lower() not in known:
            log(f'  {e["kind"][:4]} | live={int(bool(e["live"]))} | {e["externalId"]} | parent={parent_names.get(e.get("parentId"), e.get("parentId"))} | {e["name"]}')
    cfg = site_cfg or {}
    if cfg.get('site') and cfg.get('sitemap_prefixes'):
        pu = urlparse(cfg['site'])
        urls = sitemap_urls(f'{pu.scheme}://{pu.netloc}')
        for kind, prefix in cfg['sitemap_prefixes'].items():
            if kind == 'attractions':
                continue
            folders = Counter()
            for u in urls:
                path = urlparse(u).path
                if path.startswith(prefix):
                    rest = path[len(prefix):].strip('/').split('/')
                    folders[rest[0] if len(rest) > 1 else '(page de liste)'] += 1
            log(f'--- Sitemap {prefix} : ' + ', '.join(f'{k}={n}' for k, n in folders.most_common()))
            for u in urls:
                path = urlparse(u).path
                if path.startswith(prefix) and len(path[len(prefix):].strip('/').split('/')) > 1 and kind == 'restaurants':
                    log('    ' + path)


def sync_resort(resort, site_cfg, args, log):
    rid = resort['id']
    path = resort['dataPath']
    if not resort.get('themeparksId'):
        return None
    existing = {k: read_json(os.path.join(ROOT, path, f'{k}.json'), []) for k in KINDS}
    ignored_path = os.path.join(ROOT, path, 'ignored.json')
    ignored = {str(u).lower() for u in (read_json(ignored_path, []) or [])}
    dest = resort['themeparksId']
    log(f'── {resort["name"]} ({rid})')
    ents, _, _, info = api_client.fetch_entities(dest, dest, log)
    parent_names = api_client.resolve_parent_names(ents) if len(resort.get('parks') or []) > 1 else {}
    if args.inspect:
        inspect_resort(resort, ents, existing, parent_names, site_cfg, log)
        return None
    cfg0 = site_cfg or {}
    allow_no_live = set(cfg0.get('add_without_live') or [])
    new = find_new(resort, ents, existing, ignored if not args.include_ignored else set(), allow_no_live)
    if args.kinds:
        new = {k: (v if k in args.kinds else []) for k, v in new.items()}
    flat = [e for k in KINDS for e in new[k]]
    if args.baseline:      # état de référence : les lieux actuels de l'API qui ne sont pas dans les données sont volontairement absents
        ids = sorted(ignored | {e['id'].lower() for e in flat})
        if ids != sorted(ignored) and not args.dry_run:
            write_json(ignored_path, ids)
        log(f'   Référence enregistrée : {len(ids)} lieu(x) ignoré(s) ({len(ids) - len(ignored)} de plus).')
        return dict(resort=resort, added=[], renames=[], gone=[], skipped=[], web=False, baseline=len(ids) - len(ignored))
    api_ids = {e['id'].lower() for e in ents}
    gone = [(k, r.get('Nom', '')) for k in synced_kinds(resort) for r in existing[k]
            if r.get(UUID) and str(r[UUID]).lower() not in api_ids]
    result = dict(resort=resort, added=[], renames=[], gone=gone, skipped=[], web=False)
    site = (site_cfg or {}).get('site')
    cfg = site_cfg or {}
    list_urls = cfg.get('list_urls')
    fiches, mt, pages, view = [], {}, {}, []
    if (flat or args.check_names or args.site_candidates) and not args.no_web and (site or list_urls):
        try:
            fiches = collect_fiches(site, cfg, {e['kind'] for e in flat} or set(KINDS), args.browser, log)
            result['web'] = bool(fiches)
            if fiches:
                learn_structure(fiches, rid, log, args.browser)
        except Exception as ex:   # un site en panne ne doit pas empêcher d'ajouter les lieux avec les seules données de l'API
            log(f'! Site inaccessible ou illisible : {ex}')
    claimed = claimed_fiches(fiches, existing) if fiches else set()
    # Écarts de nom pour les lieux déjà connus (information seulement)
    for k in KINDS:
        for r in existing[k]:
            for f in fiches:
                best = max((similarity(r.get('Nom', ''), n) for n in f['names'] if n), default=0)
                name = f['names'][0] if f['names'] else ''
                if best >= CLAIM_SCORE and name and clean_name(name) != clean_name(r.get('Nom', '')) and not f.get('menu_only') and not f.get('slug_only'):
                    result['renames'].append((k, r.get('Nom', ''), name, f['url']))
                    break
    if not flat:
        if args.site_candidates:
            result['candidates'] = site_candidates(resort, fiches, claimed, [], set(), args, log)
        log('   Aucun nouveau lieu.')
        return result
    log(f'   {len(flat)} nouveau(x) lieu(x) : ' + ', '.join(e['name'] for e in flat))
    if fiches:
        view = [dict(f, names=[] if i in claimed else f['names']) for i, f in enumerate(fiches)]   # fiches déjà prises : invisibles pour l'appariement
        add_english_alternates(view, claimed, {e['kind'] for e in flat}, os.path.join(tempfile.gettempdir(), f'sync_{rid}'), log, args.browser)
        mt = match(flat, view, 0.8)
        builder.guess_missing(flat, mt, view, os.path.join(tempfile.gettempdir(), f'sync_{rid}'), fetch_html, lambda: False, log, 0.8)
        fr_via_english(flat, mt, view, cfg.get('en_patterns') or {}, args.browser, log, {k.lower(): v for k, v in (cfg.get('url_overrides') or {}).items()})
        used = {fi for fi, _ in mt.values()}
        if args.site_candidates:       # fiches rattachées à un nouveau lieu de l'API (par nom, nom anglais ou adresse indiquée) : exclues
            result['candidates'] = site_candidates(resort, fiches, claimed, [e for e in flat], set(used), args, log)
        strict = set(cfg.get('only_site_matched') or [])
        if len(strict) < len(KINDS):
            mt.update({ei: v for ei, v in pair_by_elimination(flat, mt, view, claimed, used).items() if flat[ei]['kind'] not in strict})
        elim = {ei for ei, (fi, s) in mt.items() if s == 0.0}
        need = sorted({fi for fi, _ in mt.values() if not view[fi].get('menu_only')})
        got = builder.download([view[i] for i in need], os.path.join(tempfile.gettempdir(), f'sync_{rid}'), log, lambda: False,
                               lambda *_: None, fetch_html, args.browser)
        for fi in {fi for fi, _ in mt.values()}:
            f = view[fi]
            if f.get('menu_only'):
                pages[fi] = dict(name=f['name'], menu=f['url'], url=f['url'])
                continue
            p = got.get(f['url'])
            if not p:
                continue
            with open(p, encoding='utf-8', errors='ignore') as fh:
                html = fh.read()
            pages[fi] = analyse(html, f['url'], 380, list_info=dict(name=f['name'], card_height=f.get('card_height', ''),
                                                                    card_category=f.get('card_category', ''), card_land=f.get('card_land', '')))
        close_browser()
    else:
        elim = set()
    strict = set(cfg.get('only_site_matched') or [])
    mappings = builder.load_mappings()
    used_codes = {str(r.get('Code')) for k in KINDS for r in existing[k]}
    for ei, e in enumerate(flat):
        kind = e['kind']
        park = park_value(resort, e, parent_names)
        if park is None:
            result['skipped'].append((kind, e['name'], 'parc inconnu dans parks.json'))
            continue
        pg = None
        notes = []
        if kind in strict and not (ei in mt and mt[ei][0] in pages and not is_listing_url(pages[mt[ei][0]].get('url'))):
            result['skipped'].append((kind, e['name'], 'aucune page correspondante dans la liste du site (parcs uniquement)'))
            continue
        if ei in mt and mt[ei][0] in pages:
            pg = pages[mt[ei][0]]
            score = mt[ei][1]
            if ei in elim:
                notes.append('associé à la fiche par élimination : vérifier que c’est la bonne')
            elif score < 0.9:
                notes.append(f'fiche reconnue avec une ressemblance de {score:.2f} : vérifier')
        elif result['web']:
            notes.append('aucune fiche trouvée sur le site : nom de l’API conservé')
        entry, missing = build_entry(kind, e, park, pg, mappings, notes)
        entry['Code'] = next_code(entry.get('Code') or f'api-{e["id"][:8]}', used_codes)
        entry = {k: entry[k] for k in ORDER[kind] if k in entry}
        existing[kind].append(entry)
        result['added'].append(dict(kind=kind, api_name=e['name'], name=entry['Nom'], park=park, url=(pg or {}).get('url', ''),
                                    missing=missing, notes=notes, entry=entry))
    result['existing'] = existing
    return result


def report(results):
    added = [a for r in results for a in r['added']]
    lines = ['## Synchronisation automatique des lieux', '']
    if added:
        lines += [f'{len(added)} nouveau(x) lieu(x) ajouté(s) depuis l’API themeparks.wiki. Relisez les lignes marquées ⚠ avant de fusionner.', '']
    for r in results:
        if not r['added'] and not r['skipped']:
            continue
        lines += [f'### {r["resort"]["name"]}', '']
        for kind in KINDS:
            rows = [a for a in r['added'] if a['kind'] == kind]
            if not rows:
                continue
            lines += [f'**{LABEL[kind]}**', '']
            for a in rows:
                renamed = f' (nom API : « {a["api_name"]} »)' if a['name'] != a['api_name'] else ''
                warn = '⚠ ' if (a['notes'] or a['missing']) else '✅ '
                lines.append(f'- {warn}**{a["name"]}**{renamed} · {a["park"]}' + (f' · [fiche]({a["url"]})' if a['url'] else '') + f' · `{a["entry"][UUID]}`')
                if a['missing']:
                    lines.append(f'  - à compléter : {", ".join(a["missing"])}')
                for n in a['notes']:
                    lines.append(f'  - {n}')
            lines.append('')
        for kind, name, why in r['skipped']:
            lines.append(f'- ⛔ Non ajouté : {name} ({LABEL[kind].lower()}) — {why}')
        lines.append('')
    ren = [(r['resort']['name'], *x) for r in results for x in r['renames']]
    if ren:
        lines += ['### Noms différents sur le site (informatif, rien n’a été modifié)', '']
        lines += [f'- {res} · {LABEL[k]} : « {cur} » dans l’appli, « {site} » sur le site' for res, k, cur, site, _ in ren]
        lines.append('')
    gone = [(r['resort']['name'], *x) for r in results for x in r['gone']]
    if gone:
        lines += ['### Lieux de l’appli absents de l’API (informatif, rien n’a été supprimé)', '']
        lines += [f'- {res} · {LABEL[k]} : {name}' for res, k, name in gone]
        lines.append('')
    lines += ['---', 'Pour ne pas garder un lieu : retirez sa ligne du fichier JSON dans cette pull request **et** ajoutez son UUID (entre accents graves ci-dessus) '
                    'dans `data/<parc>/ignored.json`, sinon il sera reproposé demain.']
    return '\n'.join(lines).strip() + '\n'


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--resort', help='ne traiter que cette destination (id de parks.json)')
    ap.add_argument('--dry-run', action='store_true', help='ne pas écrire les fichiers de données')
    ap.add_argument('--no-web', action='store_true', help='ne pas consulter les sites des parcs')
    ap.add_argument('--inspect', action='store_true', help='diagnostic : affiche les lieux de l’API absents des données et les dossiers du sitemap')
    ap.add_argument('--include-ignored', action='store_true', help='tenir compte aussi des lieux listés dans ignored.json')
    ap.add_argument('--kinds', default='', help='limiter à certaines catégories : attractions,restaurants,shows')
    ap.add_argument('--baseline', action='store_true', help='à lancer une fois : enregistre les lieux actuels de l’API absents des données comme « ignorés » (data/<parc>/ignored.json)')
    ap.add_argument('--check-names', action='store_true', help='comparer aussi les noms des lieux connus avec ceux du site (même sans nouveau lieu)')
    ap.add_argument('--list-url', action='append', default=[], metavar='CATEGORIE=URL', help='page de liste du site pour une catégorie (attractions, restaurants ou shows), répétable ; remplace sites.json pour cette exécution')
    ap.add_argument('--site-candidates', action='store_true', help='écrit site-candidates/<destination>.json : fiches du site absentes de l’API ET des données (ex. restaurants sans fiche dans themeparks.wiki)')
    ap.add_argument('--browser', action='store_true', help='utiliser le navigateur intégré (sites en JavaScript)')
    ap.add_argument('--report', default='sync_report.md', help='fichier du rapport (écrit seulement s’il y a des ajouts)')
    args = ap.parse_args(argv)
    args.kinds = {k.strip() for k in args.kinds.split(',') if k.strip()}
    log = Log(print)
    parks = read_json(os.path.join(ROOT, 'parks.json'))
    sites = read_json(os.path.join(IMP, 'sites.json'), {})
    list_urls = {}
    for item in args.list_url:
        k, _, u = item.partition('=')
        if k.strip() in KINDS and u.strip():
            list_urls[k.strip()] = u.strip()
    results = []
    for resort in parks['resorts']:
        if args.resort and resort['id'] != args.resort:
            continue
        try:
            cfg = dict(sites.get(resort['id']) or {})
            if list_urls:      # les adresses passées à l'exécution complètent (ou remplacent catégorie par catégorie) celles de sites.json
                cfg['list_urls'] = {**(cfg.get('list_urls') or {}), **list_urls}
            r = sync_resort(resort, cfg or None, args, log)
        except Exception as ex:
            log(f'! {resort["id"]} : {ex}')
            continue
        if r:
            results.append(r)
    total = sum(len(r['added']) for r in results)
    if args.baseline:
        n = sum(r.get('baseline', 0) for r in results)
        with open(os.path.join(ROOT, args.report), 'w', encoding='utf-8') as f:
            f.write(f'## Référence initiale\n\n{n} lieu(x) actuellement renvoyé(s) par l’API mais absent(s) de l’appli sont désormais ignorés (`data/<parc>/ignored.json`) : '
                    'ils ne seront ni ajoutés, ni affichés comme « Nouveau ». Seuls les lieux apparus après cette date seront détectés.\n')
        print(f'\nRéférence : {n} lieu(x) ajouté(s) aux lieux ignorés.')
        return 0
    for r in results:
        if r['added'] and not args.dry_run:
            for k in KINDS:
                if any(a['kind'] == k for a in r['added']):
                    write_json(os.path.join(ROOT, r['resort']['dataPath'], f'{k}.json'), r['existing'][k])
    if total:
        with open(os.path.join(ROOT, args.report), 'w', encoding='utf-8') as f:
            f.write(report(results))
    for r in results:
        for k, cur, site, _ in r['renames']:
            print(f'Nom différent sur le site ({r["resort"]["id"]}, {k}) : « {cur} » → « {site} »')
    print(f'\n{total} lieu(x) ajouté(s).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
