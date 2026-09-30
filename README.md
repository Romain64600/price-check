# price-check

Surveille en continu le **premier prix** des pages produit du top AllKeyShop, et alerte sur Discord quand l'offre en tête ne correspond pas au produit, à la région, à la plateforme ou à l'édition affichées.

Deux modes, qui tournent dans le même processus avec la même mémoire des offres contrôlées (`--mode`, ou `PRICE_CHECK_MODE`) :

| Mode | Pages suivies | Passage |
|---|---|---|
| `top-games` | top 5 **All Popular** + top 4 **Coming soon PC** du widget TOP 50 (9 pages) | toutes les 2 min 30 |
| `homepage` | **tous les jeux des top clics de la home** : les 10 widgets de jeux (Most anticipated, Recently released, FPS, RPG, Strategy, Action, Adventure, Management, Racing, VR) et les 10 listes du TOP 50 (Popular et Coming soon × All, PC, Xbox, PlayStation, Nintendo), soit ~415 pages le 30/09/2026 | toutes les 15 min |

## Objectif

Une offre peut être ajoutée sur une page produit alors qu'elle ne devrait pas y être :

- **Autre produit** : Sonic 1 ou un vieux Mario sur la page du dernier Sonic. **C'est surtout ce cas qui fait peur.**
- **Région non affichable** : le bon produit, mais dans une région qu'on n'est pas censé afficher sur notre marché.
- **Compte saisi comme clé** : sur la page du marchand, c'est un compte, mais nous l'avons saisi en tant que clé.
- **Mauvaise édition ou DLC** : Deluxe saisie en Standard, season pass saisi comme le jeu.

Si cette offre est la moins chère, elle devient le premier prix affiché, et ce premier prix est faux. L'écart avec l'offre suivante n'est pas un critère : il peut être d'un centime. Il faut donc contrôler l'offre elle-même, ce que permet son lien de redirection AllKeyShop : il donne l'URL du marchand, qui contient presque toujours le nom du produit, la région et la plateforme.

## Démarrage rapide

```
echo 'DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...' > .env
python3 price_check.py --dry-run --once          # test : un passage, verdicts affichés sans envoi
sudo cp price-check.service /etc/systemd/system/
sudo systemctl enable --now price-check          # boucle sans fin, relancée si elle plante
journalctl -u price-check -f
```

Python 3 seulement, aucune dépendance. Chromium (déjà sur le serveur) sert de dernier repli pour ouvrir la page d'un marchand dont l'URL ne dit rien.

## Fonctionnement

1. Toutes les 30 min, il relit les listes de chaque mode via l'API JSON `getLists`, en ne gardant que les jeux, une seule fois par page.
2. À chaque passage du mode, il lit les offres de chaque page produit (`var gamePageTrans` dans le HTML, user agent `AKS/Staff`) et prend, pour chaque édition, l'offre de clé la moins chère (`priceCard`, sans les offres compte ni les offres « sans prix » à `0.02`).
3. Toute offre en tête **jamais contrôlée** est contrôlée une fois : redirection AllKeyShop (`AKS/Staff`) → URL marchand → le nom du produit doit y être, et les mots de région, plateforme et édition doivent être compatibles avec l'offre. Si l'URL ne dit rien : le 301 du marchand, puis en dernier recours sa page ouverte avec Chromium (user agent Chrome).
4. Verdict : 🟢 `OK`, 🔴 `SUSPECT` (avec la raison), 🟠 `À VÉRIFIER` (impossible de conclure). SUSPECT et À VÉRIFIER partent sur Discord ; les OK ne sont que dans le journal (`NOTIFY_OK=0` en production depuis le 30/09/2026).

Détails et exemple d'alerte : [docs/detection.md](docs/detection.md). Le moniteur ne fait que des GET, jamais de wp-admin ; `AKS/Staff` n'est utilisé que sur AllKeyShop.

## Couverture des marchands

État au 30/09/2026, sur 30 marchands testés :

- **29 contrôlables par l'URL seule**, dont 2 après le 301 du marchand lui-même (Instant Gaming, Fanatical) et 1 par nom partiel (EA.com).
- **1 pas encore couvert** : Epic Games (URL partielle, page à ouvrir avec Chromium).
- **Packs et bundles** (Steam `/sub/`, trilogie G2A) : page ouverte avec Chromium, OK au passage réel.
- **Non vérifiés** : les marchands qui n'ont pas encore eu d'offre en tête.

Premier passage réel du 30/09/2026 sur les 9 pages : 31 offres en tête contrôlées, 31 OK, 0 alerte.

Table détaillée, méthode par méthode, et configs marchands : [docs/marchands.md](docs/marchands.md). Elle doit être mise à jour à chaque marchand qui oblige à ouvrir sa page, pour qu'on sache toujours qui est monitoré et qui ne l'est pas encore. `python3 price_check.py --coverage` affiche ce que le moniteur a constaté.

## Documentation

| Document | Contenu |
|---|---|
| [docs/detection.md](docs/detection.md) | Objectif, user agents, lien de redirection, règle en place, verdicts, limites, questions ouvertes |
| [docs/precedents.md](docs/precedents.md) | Registre des précédents : chaque cas réel jugé (formation ou étude), sa décision, sa preuve, les principes et les cas à trancher |
| [docs/marchands.md](docs/marchands.md) | Couverture par marchand : méthode de contrôle, marchands non couverts |
| [docs/exploitation.md](docs/exploitation.md) | Installation, options, réglages, systemd, journaux, état, tests |
| [docs/reconnaissance.md](docs/reconnaissance.md) | Analyse du site : API des listes, cache, structure des offres |

## Arborescence

| Fichier | Rôle |
|---|---|
| `price_check.py` | Le moniteur (réglages et listes de mots en tête de fichier) |
| `test_price_check.py` | Tests hors ligne : `python3 -m unittest -v` |
| `bot/` | Bot Discord : parler à Claude Code depuis le salon des alertes et développer le projet depuis Discord. Voir [bot/README.md](bot/README.md) |
| `aliases.toml` | Autres noms des produits (titre européen, titres français d'Amazon), appris au fil de la formation |
| `merchants/` | Une exception par marchand (TOML) : Wyrel (région dans le paramètre `region=`), Amazon (pas de Chromium, titres traduits), Nintendo (version anglaise), PlayStation (page lue en en-gb). Voir [docs/marchands.md](docs/marchands.md#configs-marchands-merchantstoml) |
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
| `redirection_kinguin.html` | Page de redirection AllKeyShop d'une offre Kinguin, 30/09/2026 |
| `merchant_urls.json` | Les 28 URL marchand relevées le 30/09/2026, avec l'offre AllKeyShop correspondante |
| `autoptimize.js`, `product_bundle.js` | Bundles JS du site (logique top clics et offres) |
