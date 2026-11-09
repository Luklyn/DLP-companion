"""Appariement des lieux de l'API avec les fiches du site (similarité de noms)."""
import re
from difflib import SequenceMatcher
from urllib.parse import urlparse

from common import norm_name


def similarity(a, b):
    na, nb = norm_name(a), norm_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    A, B = set(na.split()), set(nb.split())
    common = A & B
    ratio = SequenceMatcher(None, na, nb).ratio()
    if common:
        if A <= B or B <= A:                      # un nom est inclus dans l'autre (« Skyride » / « Skyride: Alton Towers Cable Car »)
            small = min(A, B, key=len)
            ratio = max(ratio, 0.95 if len(small) >= 2 else 0.88)
        elif len(common) >= 2:
            ratio = max(ratio, 0.92 * len(common) / min(len(A), len(B)))
    return ratio


def slug_name(url):
    seg = [x for x in urlparse(url).path.split('/') if x]
    return re.sub(r'[-_]+', ' ', seg[-1]).strip() if seg else ''


def match(ents, fiches, threshold=0.8, cross_kind=0.8):
    """ents: [{name, kind}] ; fiches: [{names, kinds}]. Renvoie {indice entité: (indice fiche, score)}.
    1re passe : un-pour-un (meilleurs scores d'abord) ; 2e passe : une fiche peut servir plusieurs lieux si le score est très bon.
    Une fiche venue d'une autre catégorie (ex. un spectacle listé avec les attractions) est pénalisée."""
    pairs = []
    for ei, e in enumerate(ents):
        ne = set(norm_name(e['name']).split())
        for fi, f in enumerate(fiches):
            if not any(set(norm_name(n).split()) & ne for n in f['names'] if n):
                continue                                        # aucun mot en commun : inutile de comparer
            s = max(similarity(e['name'], n) for n in f['names'] if n)
            if e['kind'] not in f['kinds']:
                s *= cross_kind
            pairs.append((s, ei, fi))
    pairs.sort(reverse=True)
    res, used_e, used_f = {}, set(), set()
    for s, ei, fi in pairs:
        if s < threshold:
            break
        if ei in used_e or fi in used_f:
            continue
        res[ei] = (fi, s); used_e.add(ei); used_f.add(fi)
    for s, ei, fi in pairs:
        if s < max(threshold, 0.9):
            break
        if ei not in used_e:
            res[ei] = (fi, s); used_e.add(ei)
    return res
