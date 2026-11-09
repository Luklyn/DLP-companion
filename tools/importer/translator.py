"""Traduction automatique des descriptions anglaises vers le français (deep-translator / Google Translate, sans clé)."""
import re
import time

_EN = set('the and with for you your our are is this that from have will can all of in to a an on at by be as it their they which into over more'.split())
_FR = set('le la les un une des du de et en pour avec vous votre notre est sont cette ce ces dans sur par au aux qui que sa son ses plus il elle nous'.split())


def is_english(text):
    """Heuristique : plus de mots-outils anglais que français dans le texte."""
    words = re.findall(r"[a-zà-ÿ']+", (text or '').lower())
    if len(words) < 6:
        return False
    en = sum(w in _EN for w in words)
    fr = sum(w in _FR for w in words)
    return en >= 2 and en > fr * 1.5


class Translator:
    """Traduit en français les textes détectés comme anglais ; met en cache ; ne plante jamais (renvoie '' en cas d'échec)."""
    def __init__(self, target='fr', log=print, translate_fn=None):
        self.target, self.log, self.cache, self.failed = target, log, {}, 0
        self._fn = translate_fn
        if self._fn is None:
            try:
                from deep_translator import GoogleTranslator
                tr = GoogleTranslator(source='auto', target=target)
                self._fn = tr.translate
            except Exception as e:
                self.log(f'! Traduction indisponible (pip install deep-translator) : {e}')

    def __call__(self, text):
        text = (text or '').strip()
        if not text or not self._fn or not is_english(text):
            return ''
        if text in self.cache:
            return self.cache[text]
        out = ''
        for attempt in range(2):
            try:
                out = (self._fn(text[:4500]) or '').strip()
                break
            except Exception as e:
                if attempt:
                    self.failed += 1
                    if self.failed <= 3:
                        self.log(f'  ! Traduction échouée ({e})')
                time.sleep(1.5)
        self.cache[text] = out
        time.sleep(0.15)
        return out
