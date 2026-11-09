"""Utilitaires partagés : normalisation de noms, noms de fichiers, journalisation."""
import re
import unicodedata

_STOP = r"\b(the|le|la|les|l|du|de|des|d|un|une|a|an|at|au|aux|and|et|und|der|die|das|el|los|las|y)\b"


def strip_accents(s):
    return unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode()


def norm_name(s):
    """Nom « canonique » pour comparer : sans accents/™®, minuscules, sans articles ni ponctuation."""
    s = strip_accents(s).lower().replace('&', ' and ')
    s = re.sub(r'[™®©*]', ' ', s)
    s = re.sub(_STOP, ' ', s)
    return re.sub(r'[^a-z0-9 ]+', ' ', s).strip()


def safe_filename(name, max_len=100):
    """Nom de fichier sûr (Windows/Linux) : sans accents ni caractères spéciaux, en minuscules séparées par « - »."""
    s = strip_accents(str(name or '').replace('&', ' and ')).lower()
    s = re.sub(r'[™®©*]', '', s)
    s = re.sub(r'[^a-z0-9]+', '-', s).strip('-')[:max_len].strip('-')
    return s or 'sans-nom'


def clean(text):
    """Espaces/retours à la ligne normalisés."""
    return re.sub(r'\s+', ' ', str(text or '')).strip()


class Log:
    """Journal minimal : appelle un callback (GUI) ou print."""
    def __init__(self, cb=None):
        self.cb = cb or print

    def __call__(self, msg):
        try:
            self.cb(str(msg))
        except Exception:
            pass
