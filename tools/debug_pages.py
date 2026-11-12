"""Diagnostic : télécharge des pages (importateur, navigateur + défilement) et écrit HTML + liens dans debug-pages/."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'importer'))
from net import fetch_html, close_browser, soup_of
from urllib.parse import urljoin
out = 'debug-pages'; os.makedirs(out, exist_ok=True)
for i, u in enumerate(sys.argv[1:]):
    for br in (False, True):
        html = fetch_html(u, use_browser=br, log=print) or ''
        name = f'{i}_{"b" if br else "r"}'
        open(f'{out}/{name}.html', 'w', encoding='utf-8').write(html)
        soup = soup_of(html)
        with open(f'{out}/{name}.links.txt', 'w', encoding='utf-8') as f:
            f.write(u + f'\nlongueur={len(html)}\n')
            for a in soup.find_all('a', href=True):
                f.write(urljoin(u, a['href']) + '\t' + ' | '.join(a.get_text(' ', strip=True).split())[:120] + '\n')
close_browser()
