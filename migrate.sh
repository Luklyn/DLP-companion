#!/usr/bin/env bash
# À lancer à la racine du repo (copie locale) : range les données par parc.
set -e
mkdir -p data/disneyland-paris data/parc-asterix
git mv attractions.json restaurants.json shows.json data/disneyland-paris/
for f in attractions restaurants shows; do echo '[]' > data/parc-asterix/$f.json; done
git add parks.json data index.html sw.js
git commit -m "Réorganisation multi-parcs : parks.json + data/<parc>/"
