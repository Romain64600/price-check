# price-check

Surveille en continu les **premiers prix** des pages produit du top AllKeyShop, et alerte sur Discord quand une offre en tête ne correspond pas au produit, à la région, à la plateforme ou à l'édition affichées.

## Modes

Deux modes de **pages**, qui tournent dans le même processus avec la même mémoire des offres contrôlées (`--mode`, ou `PRICE_CHECK_MODE` ; défaut `both`) :

| Mode | Pages suivies | Passage | Alertes Discord |
|---|---|---|---|
| `top-games` | top 5 **All Popular** + top 4 **Coming soon PC** du widget TOP 50 (9 pages) | toutes les 2 min 30, y compris au milieu d'un passage `homepage` | webhook `DISCORD_WEBHOOK_URL` |
| `homepage` (« Top Clicks Homepage ») | **tous les jeux des top clics de la home** : les 10 widgets de jeux (Most anticipated, Recently released, FPS, RPG, Strategy, Action, Adventure, Management, Racing, VR) et les 10 listes du TOP 50 (Popular et Coming soon × All, PC, Xbox, PlayStation, Nintendo), soit ~430 pages le 01/10/2026 | toutes les 15 min | webhook `DISCORD_WEBHOOK_URL_HOMEPAGE` (son propre salon depuis le 01/10/2026 ; à défaut, celui des top games) |

Deux modes d'**offres** : quelles offres de chaque page sont contrôlées (`--offers`, ou `PRICE_CHECK_OFFERS`). Décision de Romain du 01/10/2026, en réponse à la question « vraiment un premier prix » :

| Mode | Offres contrôlées sur chaque page |
|---|---|
| `top-offers` (défaut) | les **3 premiers prix de chaque édition** (offres de clé, `priceCard`, sans les offres « sans prix » à `0.02`). La règle des offres non vérifiables s'applique dans ce périmètre : seule l'offre qui est le premier prix de toute la page, sur une page d'un top ou d'un coming soon, part en À VÉRIFIER, y compris quand elle le devient plus tard. |
| `full-page` | **toutes les offres en vente** de la page, comptes compris (rang compté à part pour les comptes). Plus long : environ 70 à 150 offres par page. |

Chaque alerte dit le rang de l'offre dans son édition (« 2e prix de l'édition »).

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
2. À chaque passage du mode, il lit les offres de chaque page produit (`var gamePageTrans` dans le HTML, user agent `AKS/Staff`) et retient celles du mode d'offres : les 3 offres de clé les moins chères de chaque édition (`top-offers`), ou toutes les offres en vente (`full-page`).
3. Toute offre retenue **jamais contrôlée** est contrôlée une fois : redirection AllKeyShop (`AKS/Staff`) → URL marchand → le nom du produit doit y être, et les mots de région, plateforme et édition doivent être compatibles avec l'offre. Si l'URL ne dit rien : le 301 du marchand, puis sa page (HTTP, puis Chromium avec le user agent Chrome). Si l'URL contredit AllKeyShop sur la plateforme ou la région, la page du marchand tranche avant l'alerte.
4. Verdict : 🟢 `OK`, 🔴 `SUSPECT` (avec la raison), 🟠 `À VÉRIFIER` (impossible de conclure, sur le premier prix d'une page d'un top ou d'un coming soon), ⚪ `NON VÉRIFIABLE` (impossible de conclure ailleurs : noté, sans alerte). SUSPECT et À VÉRIFIER partent sur Discord ; OK et NON VÉRIFIABLE restent dans le journal et l'état (`--unverified` pour la liste).

Détails et exemple d'alerte : [docs/detection.md](docs/detection.md). Le moniteur ne fait que des GET, jamais de wp-admin ; `AKS/Staff` n'est utilisé que sur AllKeyShop.

## Formation

Chaque report jugé (par Romain ou par l'étude) est consigné dans le [registre des précédents](docs/precedents.md), avec sa preuve, la règle qui en découle et un test. Étude du 30/09/2026 : sur les 35 reports envoyés sur Discord, 11 vraies erreurs et 23 faux positifs, tous corrigés sans perdre une vraie erreur ; rejeu des 920 offres en tête : 2 vraies erreurs de plus trouvées. Arbitrages du 01/10/2026 (doc partagé) :

- une mauvaise édition est une erreur même si l'acheteur reçoit plus (GTA 4, Zero Company) ;
- Amazon est ignoré jusqu'à ce que ses pages soient lisibles ;
- chez Kinguin, la fiche servie fait foi quand elle a remplacé celle du lien (Stellaris) ;
- « Year 1 Season Pass » = « Year 1 Edition » (Farming Simulator 25).

Les reports sont aussi tranchés dans l'admin de l'executor (page « Price check »).

## Couverture des marchands

État au 30/09/2026, sur 30 marchands testés :

- **29 contrôlables par l'URL seule**, dont 2 après le 301 du marchand lui-même (Instant Gaming, Fanatical) et 1 par nom partiel (EA.com).
- **Epic Games**, longtemps non couvert (URL partielle `/p/fc-27-e149fb`) : reconnu par l'URL depuis le 01/10/2026, « FC 27 » valant « EA SPORTS FC 27 ».
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
| `aliases.toml` | Autres noms des produits qu'aucune règle ne peut deviner (titre européen, titre de travail, formulation d'un marchand), appris au fil de la formation ; les préfixes omis par les marchands (« EA Sports », « Call of Duty »…) sont une règle générale, pas des alias |
| `merchants/` | Une exception par marchand (TOML) : Wyrel (région dans le paramètre `region=`), Amazon (ignoré depuis le 01/10/2026), Nintendo (version anglaise), PlayStation (page lue en en-gb), LDShop (option cochée d'une page multi-produits), Kinguin (fiche canonique). Voir [docs/marchands.md](docs/marchands.md#configs-marchands-merchantstoml) |
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
| `api_topclick_home.json` | Réponse de l'API getLists pour les listes de la home, 30/09/2026 |
| `k4g_spider-man-2_deluxe.html` | Page K4G aux champs PLATFORM / REGION (Spider-Man 2), 30/09/2026 |
| `ldshop_forza-horizon-6_sku16560.html` | Page multi-produits LDShop, option « Premium Upgrade » cochée, 01/10/2026 |
| `kinguin_stellaris_172478.html` | Page Kinguin servie par un lien « …-eu-… » : sa fiche canonique est la globale, 01/10/2026 |
| `merchant_urls.json` | Les 28 URL marchand relevées le 30/09/2026, avec l'offre AllKeyShop correspondante |
| `autoptimize.js`, `product_bundle.js` | Bundles JS du site (logique top clics et offres) |
