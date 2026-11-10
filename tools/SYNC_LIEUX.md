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

## Page d'administration (`/admin/`)
Page statique (même site GitHub Pages) : https://dlp-companion.fr/admin/

- **Accès** : au premier passage sur un appareil, coller un jeton GitHub « fine-grained » (dépôt seul ; *Contents*, *Pull requests*, *Actions* en lecture/écriture) et choisir un mot de passe (10 caractères minimum). Le jeton est chiffré (AES-GCM, clé PBKDF2) dans le navigateur, jamais écrit en clair ni dans le dépôt. Ensuite, seul le mot de passe est demandé ; la page se verrouille après 30 min d'inactivité ou avec « Verrouiller ». « Oublier cet appareil » supprime le jeton chiffré. Mot de passe oublié : « Oublier cet appareil » puis nouvelle configuration.
- **Parcs et lieux** : « Vérifier l'API » compare themeparks.wiki et `data/<parc>/` (par UUID, hors lieux ignorés) directement dans le navigateur. « Récupérer les informations » lance le workflow *Synchronisation des lieux* (avec les 3 pages de liste saisies) puis relit la branche `auto/sync-places` pour remplir le tableau ; le reste se complète à la main.
- **Ajouter un parc** : teste les liens de l'API, puis prépare `parks.json`, `tools/importer/sites.json` et `data/<id>/*.json`.
- **Déploiement** : les modifications validées sont envoyées en un seul commit sur `main` (ou en pull request). La publication refuse d'écraser un fichier modifié entre-temps.
- Statistiques : seuls les téléchargements de l'APK sont réels (releases GitHub) ; le flux du site et les ajouts iOS sont des chiffres d'exemple.
