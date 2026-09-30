# price-check

Surveille en continu les pages produit du top AllKeyShop et envoie une alerte Discord dès qu'une offre semble mal placée, c'est-à-dire **au moins 30 % moins chère** que la suivante dans la même édition.

Pages suivies (widget top clics de la barre de droite) :
- top 5 **All Popular** (`sidebar.all.popular`)
- top 4 **Coming soon PC** (`sidebar.pc.soon`)

## Démarrage rapide

```
echo 'DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...' > .env
python3 price_check.py --dry-run --once          # test : un passage, sans envoi
sudo cp price-check.service /etc/systemd/system/
sudo systemctl enable --now price-check          # boucle sans fin, relancée si elle plante
journalctl -u price-check -f
```

Python 3 seulement, aucune dépendance.

## Fonctionnement

1. Toutes les 30 min, il relit les listes via l'API JSON `getLists`, en ne gardant que les jeux.
2. Toutes les 2 min 30, il lit les offres de chaque page (`var gamePageTrans` dans le HTML), avec 2 s de pause entre deux GET.
3. Pour chaque édition, il compare les deux offres de clés les moins chères (`priceCard`, sans les offres compte ni les offres « sans prix » à `0.02`).
4. Si l'écart est d'au moins 30 %, il alerte sur Discord, une seule fois par offre et par prix (`alerted.json`).

Le moniteur ne fait que des GET en lecture seule et n'appelle jamais les liens `/redirection/` (ils comptent des clics).

## Documentation

| Document | Contenu |
|---|---|
| [docs/detection.md](docs/detection.md) | Règle de détection, anti-doublon, exemple d'alerte, limites connues |
| [docs/exploitation.md](docs/exploitation.md) | Installation, options, réglages, systemd, journaux, tests |
| [docs/reconnaissance.md](docs/reconnaissance.md) | Analyse du site : API des listes, cache, structure des offres |

## Arborescence

| Fichier | Rôle |
|---|---|
| `price_check.py` | Le moniteur (réglages en tête de fichier) |
| `test_price_check.py` | Tests hors ligne : `python3 -m unittest -v` |
| `price-check.service` | Service systemd |
| `docs/` | Documentation |
| `samples/` | Réponses brutes du site, utilisées par les tests |

### samples/

| Fichier | Contenu |
|---|---|
| `home_staff.html` / `.headers` | Accueil, UA AKS/Staff |
| `home_chrome.html` / `.headers` | Accueil, UA Chrome (identique) |
| `home_topclickTrans.json` | JSON préchargé des top clics |
| `api_topclick_sidebar.json` | Réponse de l'API getLists (all.popular + all.soon), 28/09/2026 |
| `api_topclick_all-popular_pc-soon.json` | Réponse de l'API getLists (all.popular + pc.soon), 30/09/2026 |
| `prod_popular1_ea-fc-27.html` + `_gamePageTrans.json` | Page produit n°1 Popular |
| `prod_soon1_minecraft-dungeons-2.html` + `_gamePageTrans.json` | Page produit n°1 Coming soon |
| `autoptimize.js`, `product_bundle.js` | Bundles JS du site (logique top clics et offres) |
