"""Écriture du classeur Excel : même format que les tableaux « à compléter » (Légende, Attractions, Restaurants, Spectacles, Rapport)."""
from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter as L

LAT, LNG = 'Coordonnées GPS (lat)', 'Coordonnées GPS (lng)'
UUID = 'UUID Themeparks.wiki'
REMOVE = 'À retirer ? (oui)'
SHEETS = [
    ('attractions', 'Attractions', ['Code', UUID, 'Nom', 'Parc', 'Land', 'Type', 'Taille mini', 'Description', LAT, LNG, REMOVE]),
    ('restaurants', 'Restaurants', ['Code', UUID, 'Nom', 'Parc', 'Land', 'Type', 'Tarif', 'Description', 'Menu', LAT, LNG, REMOVE]),
    ('shows', 'Spectacles', ['Code', UUID, 'Nom', 'Parc', 'Land', 'Type', 'Description', LAT, LNG, REMOVE]),
]
EDITABLE = {'Land', 'Type', 'Taille mini', 'Tarif', 'Description', 'Menu'}
READ_ONLY = {'Destination', 'Code', UUID, 'Nom', 'Parc'}
WIDTH = {'Destination': 20, 'Code': 12, UUID: 14, 'Nom': 42, 'Parc': 22, 'Land': 24, 'Type': 22, 'Taille mini': 16, 'Tarif': 8,
         'Description': 60, 'Menu': 36, LAT: 12, LNG: 12, REMOVE: 16}


def write_workbook(destination, rows, report, unused, no_live, path, info=None):
    """rows: {kind: [dict colonne->valeur, '_warn': 'texte', '_nolive': bool]} ; report: {kind: [tuples]} ; unused: {kind: [(nom, url)]}."""
    F = lambda **k: Font(**{'name': 'Arial', 'size': 10, **k})
    hdr = PatternFill('solid', fgColor='1F2A5C'); edit = PatternFill('solid', fgColor='FFF9C4')
    grey = PatternFill('solid', fgColor='EEEEEE'); orange = PatternFill('solid', fgColor='F4B183')
    th = Side(style='thin', color='CCCCCC'); bd = Border(left=th, right=th, top=th, bottom=th)
    wb = Workbook(); lg = wb.active; lg.title = 'Légende'
    n = {k: len(rows.get(k, [])) for k, _, _ in SHEETS}
    for kind, sheet, cols in SHEETS:
        ws = wb.create_sheet(sheet)
        heads = ['Destination'] + cols
        ws.append(heads)
        for row in sorted(rows.get(kind, []), key=lambda r: str(r.get('Nom', '')).casefold()):
            vals = {'Destination': destination, **row}
            ws.append([('' if vals.get(h) is None else vals.get(h)) for h in heads])
            r = ws.max_row
            for i, h in enumerate(heads, 1):
                c = ws.cell(r, i); c.font = F(); c.border = bd
                c.alignment = Alignment(vertical='top', wrap_text=h in ('Nom', 'Description'))
                if h in READ_ONLY:
                    c.fill = grey
                elif h in EDITABLE and vals.get(h) in ('', None):
                    c.fill = edit
            nom = ws.cell(r, heads.index('Nom') + 1)
            if row.get('_nolive') or row.get('_warn'):
                nom.fill = orange
                if row.get('_warn'):
                    nom.comment = Comment(row['_warn'], 'Parc importer')
            if row.get('_desc_warn'):
                ws.cell(r, heads.index('Description') + 1).fill = orange
        for i, h in enumerate(heads, 1):
            c = ws.cell(1, i); c.font = F(bold=True, color='FFFFFF'); c.fill = hdr
            c.alignment = Alignment(vertical='center', wrap_text=True); ws.column_dimensions[L(i)].width = WIDTH.get(h, 16)
        ws.freeze_panes = 'E2'; ws.auto_filter.ref = ws.dimensions
    lines = [f"{destination} : lieux de l'API themeparks.wiki — {n['attractions']} attractions" +
             (f" (dont {len(no_live)} sans statut live)" if no_live else '') + f", {n['restaurants']} restaurants, {n['shows']} spectacles.",
             '',
             'Colonnes grises : repères (Destination, Code, UUID, Nom, Parc) ; ne pas modifier.',
             'Cellules jaunes : informations non trouvées sur le site du parc, à compléter (Land, Type, Taille mini / Tarif, Description, menu).',
             'Noms sur fond orange : à vérifier (attraction SANS statut live dans l\'API, fiche du site appariée avec un doute, ou description restée en anglais). Un commentaire explique pourquoi.',
             'Colonne « À retirer ? » : écrire « oui » pour exclure le lieu.',
             'Feuille « Identifiants » : identifiants themeparks.wiki de la destination et de ses parcs (themeparksId, themeparksParkId).',
             'Feuille « Rapport » : fiche du site utilisée pour chaque ligne, colonnes remplies, fiches non utilisées.',
             '']
    if no_live:
        lines.append('Attractions sans statut live : ' + ' ; '.join(sorted(no_live, key=str.casefold)))
        lines.append('')
    lines += ['Exemples de valeurs attendues', 'Land : Towers Street', 'Type (attraction) : Attractions à sensations',
              'Taille mini : 1,20 m  /  90 cm  /  Pas de restriction', 'Tarif : €']
    for x in lines:
        lg.append([x])
    lg['A1'].font = F(bold=True, size=13); lg.column_dimensions['A'].width = 130
    if info:
        ids = wb.create_sheet('Identifiants', 1)
        ids.append(['Identifiants themeparks.wiki (pour parks.json)'])
        ids.append([])
        ids.append(['Destination', info.get('destination_name', ''), 'themeparksId', info.get('destination_id', '')])
        ids.append(['Fuseau horaire', info.get('timezone', '')])
        ids.append(['Lien API « children »', info.get('children_url', '')])
        ids.append(['Lien API « live »', info.get('live_url', '')])
        ids.append([])
        ids.append(['Parc (valeur de la colonne « Parc »)', 'themeparksParkId', 'Latitude', 'Longitude'])
        for pk in info.get('parks', []):
            ids.append([pk['name'], pk['id'], pk['lat'], pk['lng']])
        if info.get('entity_type') and info['entity_type'] != 'DESTINATION':
            ids.append([])
            ids.append([f"Attention : le lien « children » désigne une entité de type {info['entity_type']} et non une destination : vérifie themeparksId."])
        ids['A1'].font = F(bold=True, size=13)
        for c in ids[8]:
            c.font = F(bold=True)
        for col, w in zip('ABCD', (42, 40, 14, 40)):
            ids.column_dimensions[col].width = w
    rs = wb.create_sheet('Rapport')
    for kind, sheet, _ in SHEETS:
        rs.append([f'— {sheet} —'])
        rs.append(['Ligne du tableau', 'Fiche trouvée', 'Score', 'Colonnes remplies', 'Remarque / URL'])
        for t in report.get(kind, []):
            rs.append(list(t))
        for nm, url in unused.get(kind, []):
            rs.append(['(aucune ligne)', nm, '', 'fiche non utilisée', url])
        rs.append([])
    for c in rs[1]:
        c.font = F(bold=True)
    for col, w in zip('ABCDE', (44, 40, 8, 40, 80)):
        rs.column_dimensions[col].width = w
    wb.save(path)
