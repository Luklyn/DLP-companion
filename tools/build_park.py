"""Construit data/<parc>/*.json depuis le tableau Excel complété (à relancer si le tableau change).
Usage : python tools/build_park.py tableau.xlsx data/<parc>/"""
import json, re, sys, unicodedata
import openpyxl

SRC = sys.argv[1]
OUT = sys.argv[2] if len(sys.argv) > 2 else 'data/alton-towers-resort/'
if not OUT.endswith('/'):
    OUT += '/'
import os
os.makedirs(OUT, exist_ok=True)
ORDER = {'attractions': ['Code', 'UUID Themeparks.wiki', 'Nom', 'Description', 'Parc', 'Land', 'Type', 'Taille mini', 'Coordonnées GPS (lat)', 'Coordonnées GPS (lng)'],
         'restaurants': ['Code', 'UUID Themeparks.wiki', 'Nom', 'Description', 'Menu', 'Tarif', 'Type', 'Parc', 'Land', 'Coordonnées GPS (lat)', 'Coordonnées GPS (lng)'],
         'shows': ['Code', 'UUID Themeparks.wiki', 'Nom', 'Description', 'Type', 'Parc', 'Land', 'Coordonnées GPS (lat)', 'Coordonnées GPS (lng)']}
SHEET = {'attractions': 'Attractions', 'restaurants': 'Restaurants', 'shows': 'Spectacles'}


def slug(s):
    s = unicodedata.normalize('NFKD', s.replace('&', ' and ')).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+', '-', re.sub(r'[™®©*]', '', s)).strip('-')[:100].strip('-')


wb = openpyxl.load_workbook(SRC)
for kind, sheet in SHEET.items():
    ws = wb[sheet]
    head = [c.value for c in ws[1]]
    out = []
    for r in range(2, ws.max_row + 1):
        row = {h: ws.cell(r, i + 1).value for i, h in enumerate(head) if h}
        if not row.get('Nom') or str(row.get('À retirer ? (oui)') or '').strip().lower() in ('oui', 'x', 'yes'):
            continue
        e = {}
        for k in ORDER[kind]:
            v = row.get(k)
            if v is None or str(v).strip() == '':
                continue
            e[k] = str(v).strip() if isinstance(v, str) else v
        out.append(e)
    json.dump(out, open(OUT + kind + '.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print(kind, len(out))
