"""Pipeline : API (children + live) + site du parc -> tableau Excel prêt à compléter."""
import os
import re
import time
from dataclasses import dataclass, field

import api_client
import discover
from common import Log, safe_filename
from detail import analyse
from matching import match, similarity, slug_name
from urllib.parse import urlparse
from net import close_browser, fetch_html
from translator import Translator
from xlsx_out import LAT, LNG, UUID, write_workbook

HERE = os.path.dirname(os.path.abspath(__file__))
KINDS = ('attractions', 'restaurants', 'shows')


@dataclass
class Params:
    api_children: str
    api_live: str = ''
    site_url: str = ''
    out_dir: str = '.'
    name: str = ''                      # nom de la destination (sinon : nom donné par l'API)
    use_browser: bool = False           # forcer le navigateur intégré (sites rendus en JavaScript)
    translate: bool = True
    threshold: float = 0.8
    guess: bool = True                  # deviner l'adresse des fiches que les listes ne donnent pas (…/dossier/nom-du-lieu)
    list_urls: dict = field(default_factory=dict)   # option avancée : {'attractions': url, 'restaurants': url, 'shows': url}


def load_mappings():
    import json
    try:
        with open(os.path.join(HERE, 'mappings.json'), encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {'types': {}, 'lands': {}}


def folder_name(name):
    n = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', name or '').strip(' .')
    return n or 'Nouveau parc'


def download(fiches, cache_dir, log, stop, progress, fetch, use_browser=False, delay=0.8):
    """{url: chemin} ; cache disque ; navigateur intégré en secours si la requête simple échoue."""
    os.makedirs(cache_dir, exist_ok=True)
    out = {}
    for i, f in enumerate(fiches, 1):
        if stop():
            log('Arrêt demandé.')
            break
        progress(i - 1, len(fiches))
        path = os.path.join(cache_dir, safe_filename(f['url'].split('//')[-1])[:120] + '.html')
        if os.path.exists(path) and os.path.getsize(path) > 2000:
            out[f['url']] = path
            log(f'[{i}/{len(fiches)}] (cache) {f["name"]}')
            continue
        log(f'[{i}/{len(fiches)}] Téléchargement : {f["name"]}')
        html = ''
        try:
            html = fetch(f['url'], use_browser=use_browser, log=log)
        except Exception as e:
            log(f'   ✗ {e}')
        if (len(html or '') < 3000 or '<h1' not in html.lower()) and not use_browser:
            log('   → nouvel essai avec le navigateur intégré')
            try:
                html = fetch(f['url'], use_browser=True, log=log) or html
            except Exception as e:
                log(f'   ✗ {e}')
        if html and len(html) > 2000:
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write(f'<!-- saved from url=(0000){f["url"]} -->\n' + html)
            out[f['url']] = path
        else:
            log('   ✗ fiche non récupérée')
        time.sleep(delay)
    progress(len(fiches), len(fiches))
    return out


def _slug_variants(name):
    base = re.sub(r"[’'`]", '', name or '')
    no_par = re.sub(r'\([^)]*\)', '', base)
    out = []
    for v in (base, no_par, re.sub(r'^the\s+', '', no_par, flags=re.I)):
        s = safe_filename(v)
        if s and s not in out:
            out.append(s)
    return out


def _quiet(*_):
    pass


def guess_missing(ents, mt, fiches_all, cache_dir, fetch, stop, log, threshold, max_tries=400):
    """Pour les lieux sans fiche : essaie …/<dossier des fiches>/<nom-du-lieu> et valide la page par son titre (h1)."""
    folders = {}
    for f in fiches_all:
        if f.get('menu_only'):
            continue
        pu = urlparse(f['url'])
        folder = f"{pu.scheme}://{pu.netloc}" + '/'.join(pu.path.rstrip('/').split('/')[:-1])
        for k in f['kinds']:
            folders.setdefault(k, {}).setdefault(folder, 0)
            folders[k][folder] += 1
    tried, found = 0, 0
    todo = [ei for ei in range(len(ents)) if ei not in mt]
    if not todo or not folders:
        return found
    log(f'     Recherche directe des fiches manquantes ({len(todo)} lieux)…')
    os.makedirs(cache_dir, exist_ok=True)
    for ei in todo:
        if stop() or tried >= max_tries:
            break
        e = ents[ei]
        order = [k for k in (e['kind'],) if k in folders] + [k for k in folders if k != e['kind']]
        cand_folders = []
        for k in order:
            for fo, _ in sorted(folders[k].items(), key=lambda x: -x[1])[:1]:
                if fo not in cand_folders:
                    cand_folders.append(fo)
        done = False
        for fo in cand_folders:
            for sl in _slug_variants(e['name']):
                url = f'{fo}/{sl}'
                tried += 1
                try:
                    html = fetch(url, use_browser=False, log=_quiet, retries=0, timeout=15)
                except Exception:
                    html = ''
                if not html or len(html) < 3000 or '<h1' not in html.lower():
                    continue
                pg = analyse(html, url, 380)
                score = similarity(e['name'], pg['name']) if pg['name'] else 0
                if score < threshold:
                    continue
                path = os.path.join(cache_dir, safe_filename(url.split('//')[-1])[:120] + '.html')
                with open(path, 'w', encoding='utf-8') as fh:
                    fh.write(f'<!-- saved from url=(0000){url} -->\n' + html)
                fiches_all.append(dict(url=url, name=pg['name'], card_lines=[], kinds={e['kind']}, names=[pg['name']], menu_only=False,
                                       card_height='', card_category='', card_land=''))
                mt[ei] = (len(fiches_all) - 1, score)
                found += 1
                log(f'     ✓ {e["name"]} → {url}')
                done = True
                break
            if done:
                break
            time.sleep(0.2)
    log(f'     {found} fiches supplémentaires trouvées par adresse directe.')
    return found


def build(p: Params, log=print, stop=lambda: False, progress=lambda i, n: None,
          get_json=api_client._get_json, fetch=fetch_html, get_text=discover._get_text):
    """Renvoie (chemin du xlsx, compteurs). Les erreurs partielles sont journalisées, pas levées."""
    log = log if isinstance(log, Log) else Log(log)
    if not p.api_children.strip():
        raise ValueError("Le lien API « children » est obligatoire")
    # 1) API
    log('1/5  API themeparks.wiki…')
    ents, dest, _, info = api_client.fetch_entities(p.api_children, p.api_live, log, get_json)
    if not ents:
        raise RuntimeError("L'API n'a renvoyé aucune attraction, restaurant ou spectacle (lien « children » correct ?)")
    parents = api_client.resolve_parent_names(ents, get_json)
    destination = (p.name or dest or 'Nouveau parc').strip()
    root = os.path.join(p.out_dir, folder_name(destination))
    os.makedirs(root, exist_ok=True)
    # 2) pages de liste du site
    pool = {}
    if p.list_urls and any((v or '').strip() for v in p.list_urls.values()):
        log('2/5  Pages de liste indiquées…')
        lists = discover.lists_from_urls(p.list_urls, p.use_browser, log, fetch)
    elif p.site_url.strip():
        log('2/5  Recherche des pages de liste sur le site…')
        lists, _ = discover.discover_lists(p.site_url, p.use_browser, log, fetch, get_text)
    else:
        log('2/5  Aucun site indiqué : le tableau ne contiendra que les données de l\'API.')
        lists = {}
    for kind, fiches in lists.items():
        for f in fiches:
            e = pool.setdefault(f['url'], dict(url=f['url'], name=f['name'], card_lines=f['card_lines'], kinds=set(), names=[],
                                               menu_only=bool(f.get('menu_only'))))
            if not f.get('menu_only'):
                e['menu_only'] = False
            e['kinds'].add(kind)
            if f['name'] and f['name'] not in e['names']:
                e['names'].append(f['name'])
    from links import annotate_cards
    mp = load_mappings()
    fiches_all = annotate_cards(list(pool.values()), type_keys=(mp.get('types') or {}).keys())
    for f in fiches_all:
        s = '' if f.get('menu_only') else slug_name(f['url'])
        if s and s not in f['names']:
            f['names'].append(s)
    log(f'     {len(fiches_all)} fiches détaillées repérées sur le site.')
    if stop():
        return None, {}
    # 3) appariement
    log('3/5  Appariement des lieux de l\'API avec les fiches…')
    mt = match(ents, fiches_all, p.threshold) if fiches_all else {}
    log(f'     {len(mt)}/{len(ents)} lieux appariés.')
    if p.guess and fiches_all and not stop():
        guess_missing(ents, mt, fiches_all, os.path.join(root, 'fiches_telechargees'), fetch, stop, log, p.threshold)
        log(f'     {len(mt)}/{len(ents)} lieux appariés au total.')
    # 4) téléchargement + analyse des fiches utiles
    used_idx = sorted({fi for fi, _ in mt.values()})
    need = [fiches_all[i] for i in used_idx if not fiches_all[i].get('menu_only')]
    log(f'4/5  Téléchargement et analyse de {len(need)} fiches…')
    got = download(need, os.path.join(root, 'fiches_telechargees'), log, stop, progress, fetch, p.use_browser)
    pages = {}
    for i in used_idx:
        f = fiches_all[i]
        if f.get('menu_only'):                      # carte de la liste dont le lien sort du site : on ne garde que le lien de menu
            pages[i] = dict(name=f['name'], land='', height='', description='', desc_is_en=False, menu=f['url'], category='', url=f['url'])
            continue
        path = got.get(f['url'])
        if not path:
            continue
        with open(path, 'r', encoding='utf-8', errors='ignore') as fh:
            html = fh.read()
        pg = analyse(html, f['url'], 380, list_info=dict(name=f['name'], card_height=f.get('card_height', ''),
                                                          card_category=f.get('card_category', ''), card_land=f.get('card_land', '')))
        pages[i] = pg
        log(f'  ✓ {pg["name"] or f["name"]}  | land={pg["land"] or "?"} | taille={pg["height"] or "-"}')
    close_browser()
    # 5) lignes + Excel
    log('5/5  Création du tableau Excel…')
    tr = Translator('fr', log) if p.translate else None
    types_map = {k.lower(): v for k, v in (mp.get('types') or {}).items()}
    lands_map = {k.lower(): v for k, v in (mp.get('lands') or {}).items()}
    rows = {k: [] for k in KINDS}
    report = {k: [] for k in KINDS}
    no_live = []
    for ei, e in enumerate(ents):
        row = {'Code': e['externalId'], UUID: e['id'], 'Nom': e['name'], 'Parc': parents.get(e['parentId'], '') or destination,
               LAT: e['lat'], LNG: e['lng'], 'Land': '', 'Type': '', 'Description': ''}
        if e['kind'] == 'attractions' and not e['live']:
            row['_nolive'] = True
            no_live.append(e['name'])
        filled, warn, remark = [], '', ''
        if ei in mt and mt[ei][0] in pages:
            fi, score = mt[ei]
            f, pg = fiches_all[fi], pages[fi]
            cat = (pg.get('category') or '').strip()
            if cat and types_map.get(cat.lower()):
                row['Type'] = types_map[cat.lower()]
            elif cat:
                log(f'  ⚠ Type « {cat} » absent de mappings.json (lieu {e["name"]})')
            land = pg.get('land') or ''
            row['Land'] = lands_map.get(land.lower(), land)
            if e['kind'] == 'attractions':
                row['Taille mini'] = pg.get('height') or ''
            if e['kind'] == 'restaurants':
                row['Menu'] = pg.get('menu') or ''
            d = pg.get('description') or ''
            if d and pg.get('desc_is_en'):
                fr = tr(d) if tr else ''
                if fr and fr != d:
                    d = fr
                else:
                    row['_desc_warn'] = True
                    remark = 'description non traduite (anglais)'
            row['Description'] = d
            filled = [c for c in ('Land', 'Type', 'Taille mini', 'Menu', 'Description') if row.get(c)]
            if score < 0.9 or remark:
                warn = f'À vérifier : fiche « {f["names"][0]} » (score {score:.2f}) {remark}'.strip()
            report[e['kind']].append((e['name'], pg.get('name') or f['names'][0], round(score, 2), ', '.join(filled) or '(rien à remplir)',
                                      remark or f['url']))
        else:
            report[e['kind']].append((e['name'], '—', 0, 'aucune fiche trouvée', ''))
        if score_low(warn):
            row['_warn'] = warn
        rows[e['kind']].append(row)
    used_urls = {fiches_all[i]['url'] for i in {fi for fi, _ in mt.values()}}
    unused = {k: [] for k in KINDS}
    for f in fiches_all:
        if f['url'] not in used_urls:
            for k in (sorted(f['kinds']) or ['attractions'])[:1]:
                unused[k].append((f['names'][0] if f['names'] else '?', f['url']))
    path = os.path.join(root, f'{folder_name(destination)}_infos_a_completer.xlsx')
    write_workbook(destination, rows, report, unused, no_live, path, info)
    counts = {k: len(v) for k, v in rows.items()}
    log(f"Identifiants themeparks.wiki : destination {info['destination_id']} ; parcs : " + ', '.join(f"{x['name']} = {x['id']}" for x in info['parks']))
    log(f'Terminé : {path}  ({counts["attractions"]} attractions, {counts["restaurants"]} restaurants, {counts["shows"]} spectacles)')
    return path, counts


def score_low(warn):
    return bool(warn)
