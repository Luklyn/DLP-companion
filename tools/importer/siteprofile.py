"""Analyse de structure d'un site AVANT l'import : on regarde comment les pages sont faites pour savoir où lire chaque information.

- cartes de la page de liste : catégories (étiquettes courtes qui reviennent sur plusieurs cartes) et couples « libellé / nombre »
  (ex. « HAUTEUR MINIMUM SEUL » puis « 130 ») qui donnent les tailles ;
- fiches détaillées : phrases-modèles qui ne changent que d'un mot d'une fiche à l'autre
  (ex. « Découvrez sans tarder <zone> avec ses attractions… ») : le mot qui varie est le land.
Tout est déduit des pages elles-mêmes : un nouveau parc n'a pas besoin de code dédié.
"""
import re
from collections import Counter, defaultdict

NOISE = re.compile(r'^(retiré des favoris|ajouter aux favoris|ajouter|nouveau|new|favoris|voir la page|en savoir plus|plus d.infos?|découvrir|voir plus)$', re.I)
SKIP_FACET = re.compile(r'ferm|pendant|closed|ouvert| / |^\d')
NUM = re.compile(r'^(\d{2,3})(?:\s*cm)?$', re.I)
ROLE_MAX = re.compile(r'maxim', re.I)
ROLE_ALONE = re.compile(r'seul|alone|without|sans|unaccompanied|non accompagn', re.I)
ROLE_ACC = re.compile(r'accompagn|adult|with ', re.I)


def height_role(label):
    if ROLE_MAX.search(label):
        return 'max'
    if ROLE_ALONE.search(label):
        return 'alone'
    if ROLE_ACC.search(label):
        return 'acc'
    return 'alone'


def analyse_cards(fiches):
    """{'facets': [valeurs de catégorie], 'height': {libellé: rôle}} d'après les cartes d'une liste."""
    n = len(fiches)
    out = {'facets': [], 'height': {}}
    if n < 4:
        return out
    # 1) position fixe : la ligne juste avant le nom (hors « Nouveau », « Retiré des favoris »…) a presque toujours la même nature
    before = Counter()
    for f in fiches:
        lines, name = f['card_lines'], f['name']
        pos = next((i for i, l in enumerate(lines) if l == name), None)
        if pos is None:
            continue
        prev = [l for l in lines[:pos] if not NOISE.match(l)]
        if prev and 2 <= len(prev[-1]) <= 28 and not SKIP_FACET.search(prev[-1]) and not re.search(r'[.!?:]$', prev[-1]):
            before[prev[-1]] += 1
    if before and 1 <= len(before) <= 12 and sum(before.values()) >= 0.7 * n:
        out['facets'] = sorted(before)
    else:        # 2) à défaut : étiquettes courtes répétées près du nom
        cnt = Counter()
        for f in fiches:
            lines, name = f['card_lines'], f['name']
            pos = next((i for i, l in enumerate(lines) if l == name), len(lines))
            for l in set(lines[max(0, pos - 3):pos + 1]):
                if l != name and 2 <= len(l) <= 28 and not NOISE.match(l) and not SKIP_FACET.search(l) and not re.search(r'[.!?:]$', l):
                    cnt[l] += 1
        facets = [l for l, c in cnt.items() if 2 <= c <= 0.85 * n]
        if 1 <= len(facets) <= 12:
            out['facets'] = sorted(facets)
    pairs = Counter()
    for f in fiches:
        ls = f['card_lines']
        for i in range(len(ls) - 1):
            if NUM.match(ls[i + 1].strip()) and re.fullmatch(r"[A-Za-zÀ-ÿ' .\-]{4,40}", ls[i]) and not NOISE.match(ls[i]) \
                    and re.search(r'haut|height|taille|size|min|max', ls[i], re.I):
                pairs[ls[i]] += 1
    out['height'] = {l: height_role(l) for l, c in pairs.items() if c >= 2}
    return out


def card_height_text(lines, labels):
    """Lignes d'une carte + libellés détectés -> texte lisible par detail.parse_height."""
    vals = {}
    for i, l in enumerate(lines[:-1]):
        role = labels.get(l)
        m = NUM.match(lines[i + 1].strip()) if role else None
        if m:
            vals[role] = int(m.group(1))
    if not vals:
        return ''
    parts = []
    if 'alone' in vals and 'acc' in vals and vals['acc'] != vals['alone']:
        parts.append(f"Minimum {vals['acc']} cm with an adult, {vals['alone']} cm without an adult.")
    elif 'alone' in vals:
        parts.append(f"Minimum {vals['alone']} cm.")
    elif 'acc' in vals:
        parts.append(f"Minimum {vals['acc']} cm with an adult.")
    if 'max' in vals:
        parts.append(f"Maximum {vals['max'] / 100:.2f} m.")
    return ' '.join(parts)


def mine_land_template(pages_lines):
    """Phrase-modèle commune à plusieurs fiches dont seul un groupe de mots change (le land). Renvoie (regex, exemples) ou (None, [])."""
    found = defaultdict(lambda: [set(), set()])      # clé -> (index de fiches, valeurs)
    for i in range(len(pages_lines)):
        for j in range(i + 1, len(pages_lines)):
            for a in pages_lines[i]:
                if not 25 <= len(a) <= 240:
                    continue
                aw = a.split()
                for b in pages_lines[j]:
                    if b == a or not 25 <= len(b) <= 240:
                        continue
                    bw = b.split()
                    p = 0
                    while p < min(len(aw), len(bw)) and aw[p] == bw[p]:
                        p += 1
                    if p < 2:
                        continue
                    for ai in range(p + 1, min(len(aw) - 2, p + 5)):
                        w = aw[ai]
                        if not w[:1].islower():       # le mot qui suit le land est un mot-outil (« avec », « à », « de »…), pas un nom propre
                            continue
                        bi = next((k for k in range(p + 1, min(len(bw) - 2, p + 5)) if bw[k:k + 3] == aw[ai:ai + 3]), None)
                        if bi is None:
                            continue
                        am, bm = ' '.join(aw[p:ai]), ' '.join(bw[p:bi])
                        if am and bm and am != bm and am[:1].isupper() and bm[:1].isupper():
                            e = found[(tuple(aw[:p]), w)]
                            e[0].update((i, j))
                            e[1].update((am, bm))
    best = None
    for (prefix, w), (idx, vals) in found.items():
        if len(idx) >= 3 and 2 <= len(vals) <= 15 and (best is None or (len(idx), len(vals), -sum(len(v) for v in vals)) > (len(found[best][0]), len(found[best][1]), -sum(len(v) for v in found[best][1]))):
            best = (prefix, w)
    if not best:
        return None, []
    prefix, w = best
    rx = re.compile(r'^' + r'\s+'.join(re.escape(x) for x in prefix) + r'\s+(.{2,40}?)\s+' + re.escape(w) + r'\b', re.I)
    return rx, sorted(found[best][1])[:8]
