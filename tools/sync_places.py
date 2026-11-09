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
from detail import analyse  # noqa: E402
from links import annotate_cards  # noqa: E402
from matching import match, similarity, slug_name  # noqa: E402
from net import close_browser, fetch_html  # noqa: E402

KINDS = ('attractions', 'restaurants', 'shows')
CATEGORY_TO_KIND = {'attraction': 'attractions', 'restaurant': 'restaurants', 'spectacle': 'shows'}
UUID = 'UUID Themeparks.wiki'
LAT, LNG = 'Coordonnées GPS (lat)', 'Coordonnées GPS (lng)'
ORDER = {
    'attractions': ['Code', UUID, 'Nom', 'Description', 'Parc', 'Land', 'Type', 'Taille mini', LAT, LNG],
    'restaurants': ['Code', UUID, 'Nom', 'Description', 'Menu', 'Tarif', 'Type', 'Parc', 'Land', LAT, LNG],
    'shows': ['Code', UUID, 'Nom', 'Description', 'Type', 'Parc', 'Land', LAT, LNG],
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
    return re.sub(r'\s+', ' ', (s or '').replace(' ', ' ')).strip()


def next_code(base, used):
    code = base or 'api'
    n = 2
    while code in used:
        code, n = f'{base}-{n}', n + 1
    used.add(code)
    return code


def find_new(resort, ents, existing, ignored):
    """Lieux de l'API absents des données : {kind: [entités]} (uniquement les catégories suivies et ceux qui ont des données live)."""
    known = {k: {str(r.get(UUID, '')).lower() for r in existing[k]} for k in KINDS}
    every = set().union(*known.values())
    out = {k: [] for k in KINDS}
    kinds = synced_kinds(resort)
    for e in ents:
        if e['kind'] not in kinds or e['id'].lower() in every or e['id'].lower() in ignored:
            continue
        if not e['live']:        # l'appli masque les lieux sans statut dans l'API : inutile de les ajouter
            continue
        out[e['kind']].append(e)
    return out


def collect_fiches(site, list_urls, use_browser, log):
    """Fiches détaillées du site officiel : [{url, name, kinds, names, card_*}]."""
    if list_urls and any((v or '').strip() for v in list_urls.values()):
        lists = discover.lists_from_urls(list_urls, use_browser, log, fetch_html)
    elif site:
        lists, _ = discover.discover_lists(site, use_browser, log, fetch_html, discover._get_text)
    else:
        return []
    pool = {}
    for kind, fiches in lists.items():
        for f in fiches:
            e = pool.setdefault(f['url'], dict(url=f['url'], name=f['name'], card_lines=f['card_lines'], kinds=set(), names=[],
                                               menu_only=bool(f.get('menu_only'))))
            if not f.get('menu_only'):
                e['menu_only'] = False
            e['kinds'].add(kind)
            if f['name'] and f['name'] not in e['names']:
                e['names'].append(f['name'])
    mp = builder.load_mappings()
    fiches = annotate_cards(list(pool.values()), type_keys=(mp.get('types') or {}).keys())
    for f in fiches:
        s = '' if f.get('menu_only') else slug_name(f['url'])
        if s and s not in f['names']:
            f['names'].append(s)
    return fiches


def claimed_fiches(fiches, existing):
    """Indices des fiches déjà rattachées à un lieu connu (sinon elles risqueraient d'être attribuées à un nouveau lieu)."""
    claimed = set()
    known = [r.get('Nom', '') for k in KINDS for r in existing[k]]
    for i, f in enumerate(fiches):
        if any(similarity(n, fn) >= CLAIM_SCORE for n in known for fn in f['names'] if fn):
            claimed.add(i)
    return claimed


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


def build_entry(kind, e, park, pg, mappings, notes):
    """Fiche JSON d'un lieu : données de l'API + ce que la page du site a réellement donné."""
    types = {k.lower(): v for k, v in (mappings.get('types') or {}).items()}
    lands = {k.lower(): v for k, v in (mappings.get('lands') or {}).items()}
    row = {'Code': e['externalId'], UUID: e['id'], 'Nom': e['name'], 'Parc': park, LAT: e['lat'], LNG: e['lng']}
    missing = []
    if pg:
        name = clean_name(pg.get('name'))
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
            row['Description'] = pg['description']
            if pg.get('desc_is_en'):
                notes.append('description en anglais sur la page du site (non traduite)')
    for col in ('Land', 'Type', 'Description') + (('Taille mini',) if kind == 'attractions' else ()):
        if col in ORDER[kind] and not row.get(col):
            missing.append(col)
    out = {k: row[k] for k in ORDER[kind] if row.get(k) not in (None, '')}
    return out, missing


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
    new = find_new(resort, ents, existing, ignored)
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
    list_urls = (site_cfg or {}).get('list_urls')
    fiches, mt, pages, view = [], {}, {}, []
    if (flat or args.check_names) and not args.no_web and (site or list_urls):
        try:
            fiches = collect_fiches(site, list_urls, args.browser, log)
            result['web'] = bool(fiches)
        except Exception as ex:   # un site en panne ne doit pas empêcher d'ajouter les lieux avec les seules données de l'API
            log(f'! Site inaccessible ou illisible : {ex}')
    claimed = claimed_fiches(fiches, existing) if fiches else set()
    # Écarts de nom pour les lieux déjà connus (information seulement)
    for k in KINDS:
        for r in existing[k]:
            for f in fiches:
                best = max((similarity(r.get('Nom', ''), n) for n in f['names'] if n), default=0)
                name = f['names'][0] if f['names'] else ''
                if best >= CLAIM_SCORE and name and clean_name(name) != clean_name(r.get('Nom', '')) and not f.get('menu_only'):
                    result['renames'].append((k, r.get('Nom', ''), name, f['url']))
                    break
    if not flat:
        log('   Aucun nouveau lieu.')
        return result
    log(f'   {len(flat)} nouveau(x) lieu(x) : ' + ', '.join(e['name'] for e in flat))
    if fiches:
        view = [dict(f, names=[] if i in claimed else f['names']) for i, f in enumerate(fiches)]   # fiches déjà prises : invisibles pour l'appariement
        mt = match(flat, view, 0.8)
        builder.guess_missing(flat, mt, view, os.path.join(tempfile.gettempdir(), f'sync_{rid}'), fetch_html, lambda: False, log, 0.8)
        used = {fi for fi, _ in mt.values()}
        mt.update(pair_by_elimination(flat, mt, view, claimed, used))
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
    ap.add_argument('--baseline', action='store_true', help='à lancer une fois : enregistre les lieux actuels de l’API absents des données comme « ignorés » (data/<parc>/ignored.json)')
    ap.add_argument('--check-names', action='store_true', help='comparer aussi les noms des lieux connus avec ceux du site (même sans nouveau lieu)')
    ap.add_argument('--browser', action='store_true', help='utiliser le navigateur intégré (sites en JavaScript)')
    ap.add_argument('--report', default='sync_report.md', help='fichier du rapport (écrit seulement s’il y a des ajouts)')
    args = ap.parse_args(argv)
    log = Log(print)
    parks = read_json(os.path.join(ROOT, 'parks.json'))
    sites = read_json(os.path.join(IMP, 'sites.json'), {})
    results = []
    for resort in parks['resorts']:
        if args.resort and resort['id'] != args.resort:
            continue
        try:
            r = sync_resort(resort, sites.get(resort['id']), args, log)
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
