#!/usr/bin/env python3
"""Surveille l'API themeparks.wiki et sort un xlsx des nouveaux lieux.

Usage :
  python tools/check_new_places.py                 # vérifie tous les parcs surveillés
  python tools/check_new_places.py --only walt-disney-world
  python tools/check_new_places.py --all           # inclut aussi les lieux sans statut live
  python tools/check_new_places.py --search "Efteling"            # cherche l'id d'une destination
  python tools/check_new_places.py --add-park "Efteling" --id <uuid> [--country NL]
  python tools/check_new_places.py --list          # affiche la liste surveillée

Surveillés = destinations de parks.json + tools/watchlist.json (ajouts manuels).
Un lieu est « nouveau » si son UUID n'est dans aucun JSON de data/<destination>/
et pas dans tools/ignored.json. Par défaut seuls les lieux avec un statut live
sont retenus (les autres sont souvent des entités fantômes).
Dépendances : pip install requests openpyxl
"""
import argparse, datetime, json, os, re, sys, unicodedata
import requests
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter as L
from openpyxl.worksheet.datavalidation import DataValidation

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = 'https://api.themeparks.wiki/v1'
LAT, LNG = 'Coordonnées GPS (lat)', 'Coordonnées GPS (lng)'
UUID = 'UUID Themeparks.wiki'
KINDS = {  # clé du JSON -> (type API, feuille, colonnes, colonne photo)
    'attractions': ('ATTRACTION', 'Attractions', ['Code', UUID, 'Nom', 'Parc', 'Land', 'Type', 'Taille mini', 'Description', 'URL image', LAT, LNG]),
    'restaurants': ('DINING', 'Restaurants', ['Code', UUID, 'Nom', 'Parc', 'Land', 'Type', 'Tarif', 'Description', 'Menu', 'Photo', LAT, LNG]),
    'shows': ('SHOW', 'Spectacles', ['Code', UUID, 'Nom', 'Parc', 'Land', 'Type', 'Description', 'Photo', LAT, LNG]),
}
RO = {'Destination', 'Code', UUID, 'Nom', 'Parc'}
WIDTH = {'Destination': 20, 'Code': 14, UUID: 14, 'Nom': 42, 'Parc': 22, 'Land': 24, 'Type': 22, 'Description': 60,
         'Taille mini': 16, 'Tarif': 8, 'Menu': 36, 'URL image': 36, 'Photo': 36, LAT: 12, LNG: 12}


def get(path):
    r = requests.get(API + path, timeout=60, headers={'User-Agent': 'theme-parks-companion-checker'})
    r.raise_for_status()
    return r.json()


def slug(s):
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+', '-', s).strip('-')


def load(path, default):
    p = os.path.join(ROOT, path)
    return json.load(open(p, encoding='utf-8')) if os.path.exists(p) else default


def save_watchlist(w):
    json.dump(w, open(os.path.join(ROOT, 'tools/watchlist.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=2)


def destinations():
    """-> liste de dict(id, name, themeparksId, dataPath, parks{parkId:nom})"""
    out = {}
    for r in load('parks.json', {}).get('resorts', []):
        out[r['themeparksId']] = dict(id=r['id'], name=r['name'], themeparksId=r['themeparksId'],
                                      dataPath=r.get('dataPath') or f"data/{r['id']}/",
                                      parks={p['themeparksParkId']: p['value'] for p in r.get('parks', []) if p.get('themeparksParkId')})
    for w in load('tools/watchlist.json', {'extra': []})['extra']:
        if w['themeparksId'] not in out:
            out[w['themeparksId']] = dict(id=w['id'], name=w['name'], themeparksId=w['themeparksId'],
                                          dataPath=w.get('dataPath') or f"data/{w['id']}/", parks={})
    return list(out.values())


def known_uuids(dest):
    known = {k: {} for k in KINDS}
    d = os.path.join(ROOT, dest['dataPath'])
    for k in KINDS:
        f = os.path.join(d, k + '.json')
        if os.path.exists(f):
            for e in json.load(open(f, encoding='utf-8')):
                if e.get(UUID):
                    known[k][e[UUID]] = e.get('Nom')
    return known


def scan(dest, include_all, ignored, park_cache):
    kids = get(f"/entity/{dest['themeparksId']}/children")['children']
    live = {x['id']: x for x in get(f"/entity/{dest['themeparksId']}/live").get('liveData', [])}
    known = known_uuids(dest)
    all_known = {u for k in known.values() for u in k}
    api_ids = {c['id'] for c in kids}
    new = {k: [] for k in KINDS}
    for c in kids:
        for k, (typ, _, _) in KINDS.items():
            if c.get('entityType') != typ or c['id'] in all_known or c['id'] in ignored:
                continue
            if not include_all and c['id'] not in live:
                continue
            pid = c.get('parentId')
            if pid not in dest['parks'] and pid not in park_cache:
                try:
                    park_cache[pid] = get(f'/entity/{pid}')['name']
                except Exception:
                    park_cache[pid] = ''
            loc = c.get('location') or {}
            new[k].append(dict(**{
                'Code': c.get('externalId') or 'new_' + c['id'][:8], UUID: c['id'], 'Nom': c['name'],
                'Parc': dest['parks'].get(pid) or park_cache.get(pid, ''),
                LAT: loc.get('latitude', ''), LNG: loc.get('longitude', ''), '_status': (live.get(c['id']) or {}).get('status', '')}))
    gone = []
    if kids:
        for k, m in known.items():
            gone += [(KINDS[k][1], n, u) for u, n in m.items() if u not in api_ids]
    return new, gone


def write_xlsx(rows, gone, path):
    F = lambda **k: Font(**{'name': 'Arial', 'size': 10, **k})
    hdr = PatternFill('solid', fgColor='1F2A5C'); edit = PatternFill('solid', fgColor='FFF9C4'); grey = PatternFill('solid', fgColor='EEEEEE')
    th = Side(style='thin', color='CCCCCC'); bd = Border(left=th, right=th, top=th, bottom=th)
    wb = Workbook(); lg = wb.active; lg.title = 'Légende'
    total = 0
    for k, (_, sheet, cols) in KINDS.items():
        ws = wb.create_sheet(sheet); heads = ['Destination'] + cols + ['À retirer ? (oui)']; ws.append(heads)
        types = set()
        for dname, e in rows[k]:
            ws.append([dname] + [e.get(c, '') for c in cols] + [''])
            r = ws.max_row
            for i, h in enumerate(heads, 1):
                c = ws.cell(r, i); c.font = F(); c.border = bd
                c.alignment = Alignment(vertical='top', wrap_text=h in ('Nom', 'Description'))
                c.fill = grey if h in RO else (edit if e.get(h, '') in ('', None) else PatternFill())
        total += ws.max_row - 1
        for i, h in enumerate(heads, 1):
            c = ws.cell(1, i); c.font = F(bold=True, color='FFFFFF'); c.fill = hdr
            c.alignment = Alignment(vertical='center', wrap_text=True); ws.column_dimensions[L(i)].width = WIDTH.get(h, 16)
        ws.freeze_panes = 'E2'; ws.auto_filter.ref = ws.dimensions
        # listes déroulantes Type / Land / Tarif issues des JSON existants
        for col in ('Type', 'Land'):
            vals = set()
            for dd in destinations():
                f = os.path.join(ROOT, dd['dataPath'], k + '.json')
                if os.path.exists(f):
                    vals |= {str(x[col]).strip().replace(',', ' ') for x in json.load(open(f, encoding='utf-8')) if x.get(col)}
            s = '"' + ','.join(sorted(vals)) + '"'
            if vals and len(s) <= 255 and col in heads:
                v = DataValidation(type='list', formula1=s, allow_blank=True, showErrorMessage=False)
                ws.add_data_validation(v); v.add(f'{L(heads.index(col) + 1)}2:{L(heads.index(col) + 1)}500')
    if gone:
        ws = wb.create_sheet('À vérifier (disparus)'); ws.append(['Destination', 'Catégorie', 'Nom', UUID])
        for g in gone: ws.append(list(g))
        for i, w in enumerate((20, 14, 42, 40), 1): ws.column_dimensions[L(i)].width = w
        for c in ws[1]: c.font = F(bold=True, color='FFFFFF'); c.fill = hdr
    leg = ['Nouveaux lieux détectés dans l\'API themeparks.wiki (absents des JSON du repo).', '',
           'Colonnes grises : repères (Destination, Code, UUID, Nom, Parc) ; ne pas modifier.',
           'Cellules jaunes : informations à compléter (Land, Type, Description, photo, etc.).',
           'Code : identifiant officiel de l\'API (externalId) ; à garder tel quel.',
           'Colonne « À retirer ? » : écrire « oui » pour ignorer le lieu.',
           'Feuille « À vérifier (disparus) » (si présente) : lieux de tes JSON que l\'API ne renvoie plus.']
    for x in leg: lg.append([x])
    lg['A1'].font = F(bold=True, size=13); lg.column_dimensions['A'].width = 110
    for row in lg.iter_rows(min_row=2):
        for c in row: c.font = F()
    wb.save(path)
    return total


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--only', help='id d\'une destination (ex. walt-disney-world)')
    ap.add_argument('--all', action='store_true', help='inclure les lieux sans statut live')
    ap.add_argument('--out', default=os.path.join(ROOT, 'tools/output'))
    ap.add_argument('--search'); ap.add_argument('--list', action='store_true')
    ap.add_argument('--add-park'); ap.add_argument('--id'); ap.add_argument('--country', default='')
    a = ap.parse_args()

    if a.search:
        for d in get('/destinations')['destinations']:
            if a.search.lower() in d['name'].lower() or any(a.search.lower() in p['name'].lower() for p in d.get('parks', [])):
                print(f"{d['id']}  {d['name']}  [{', '.join(p['name'] for p in d.get('parks', []))}]")
        return
    if a.list:
        for d in destinations(): print(f"{d['id']:20} {d['themeparksId']}  {d['name']}")
        return
    if a.add_park:
        if not a.id: sys.exit('Précise --id <uuid> (trouve-le avec --search "nom").')
        w = load('tools/watchlist.json', {'extra': []})
        w['extra'].append(dict(id=slug(a.add_park), name=a.add_park, themeparksId=a.id, country=a.country, dataPath=f'data/{slug(a.add_park)}/'))
        save_watchlist(w); print(f'Ajouté : {a.add_park}. Il sera inclus au prochain contrôle.'); return

    ignored = set(load('tools/ignored.json', []))
    rows = {k: [] for k in KINDS}; gone = []; park_cache = {}
    for d in destinations():
        if a.only and d['id'] != a.only: continue
        try:
            new, g = scan(d, a.all, ignored, park_cache)
        except Exception as e:
            print(f"! {d['name']} : erreur API ({e})", file=sys.stderr); continue
        n = sum(len(v) for v in new.values())
        print(f"{d['name']}: {n} nouveau(x)" + (f", {len(g)} disparu(s)" if g else ''))
        for k in KINDS: rows[k] += [(d['name'], e) for e in new[k]]
        gone += [(d['name'],) + x for x in g]
    total = sum(len(v) for v in rows.values())
    gh = os.environ.get('GITHUB_OUTPUT')
    path = ''
    if total or gone:
        os.makedirs(a.out, exist_ok=True)
        path = os.path.join(a.out, f'Nouveautes_{datetime.date.today()}.xlsx')
        write_xlsx(rows, gone, path)
        print(f'\n{total} nouveau(x) lieu(x) -> {path}')
    else:
        print('\nRien de nouveau.')
    if gh:
        with open(gh, 'a') as f: f.write(f'new_count={total}\nxlsx={path}\n')


if __name__ == '__main__':
    main()
