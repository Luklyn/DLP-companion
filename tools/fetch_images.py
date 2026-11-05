"""Télécharge les images listées dans data/<resort>/image-sources.json et les convertit en WebP (800 px max, qualité 76), comme les autres parcs."""
import io, json, os, sys
import requests
from PIL import Image

resort = sys.argv[1] if len(sys.argv) > 1 else 'alton-towers-resort'
src = json.load(open(f'data/{resort}/image-sources.json', encoding='utf-8'))
H = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36',
     'Accept': 'image/avif,image/webp,image/*,*/*;q=0.8', 'Referer': 'https://www.altontowers.com/'}
ok = bad = 0
for path, url in src.items():
    if os.path.exists(path):
        continue
    try:
        r = requests.get(url, headers=H, timeout=40)
        r.raise_for_status()
        im = Image.open(io.BytesIO(r.content)).convert('RGB')
        if im.width > 800:
            im = im.resize((800, round(im.height * 800 / im.width)))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        im.save(path, 'WEBP', quality=76, method=6)
        ok += 1
    except Exception as e:
        bad += 1
        print('ÉCHEC', path, url, e)
print(f'{ok} images créées, {bad} échecs')
sys.exit(1 if bad and not ok else 0)
