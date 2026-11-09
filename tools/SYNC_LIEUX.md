# Synchronisation automatique des lieux

Chaque jour (≈ 5 h, heure de Paris), l'action **Synchronisation des lieux** (`.github/workflows/sync-places.yml`) :

1. lit l'API themeparks.wiki (attractions, restaurants, spectacles seulement, et uniquement les lieux qui ont des données live) ;
2. repère ceux qui ne sont pas dans `data/<parc>/*.json` (comparaison par UUID) ;
3. cherche leur fiche sur le site officiel (`tools/importer/sites.json`, adresse française) :
   - **nom** = celui de la page du site (nom anglais conservé s'il n'y a pas de page, ou si la page garde le nom anglais) ;
   - description, zone, type, taille minimale, lien de menu = uniquement ce que la page donne (rien n'est traduit ni inventé) ;
4. ouvre / met à jour la pull request `auto/sync-places` avec les nouvelles fiches. **Rien n'est publié avant la fusion.**

En attendant la fusion, l'appli affiche le lieu tout de suite avec un badge « Nouveau » (nom, statut, attente) grâce au flux live.

## Mise en route (une seule fois)
1. GitHub → Settings → Actions → General → *Allow GitHub Actions to create and approve pull requests*.
2. Actions → *Synchronisation des lieux* → *Run workflow* avec **baseline** coché : les lieux actuels de l'API qui ne sont pas dans l'appli
   sont enregistrés comme volontairement exclus (`data/<parc>/ignored.json`). Fusionner la PR obtenue.
3. Vérifier/corriger les adresses dans `tools/importer/sites.json` (version française de chaque site).

## Au quotidien
- Lieu à ne pas garder : retirer sa ligne du JSON dans la PR **et** ajouter son UUID dans `data/<parc>/ignored.json`.
- Cases ⚠ de la PR : à compléter à la main dans le JSON avant de fusionner.
- *Run workflow* avec **check_names** : liste aussi les lieux connus dont le nom diffère de celui du site (informatif).
- En local : `python tools/sync_places.py --dry-run [--resort toverland]`.
