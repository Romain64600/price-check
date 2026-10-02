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
- **Mauvaise édition ou DLC** : Deluxe saisie en Standard, season pass saisi comme le jeu ; et l'inverse, une Complete Edition rangée en Standard (arbitrage du 01/10/2026 : erreur même si l'acheteur reçoit plus).
- **Monnaie de jeu vendue comme le jeu** (règle du 02/10/2026) : des COD Points, des V-Bucks ou une Shark Card sur la page du jeu, dont l'URL contient pourtant le nom.

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
3. Toute offre retenue **jamais contrôlée** est contrôlée une fois : redirection AllKeyShop (`AKS/Staff`) → URL marchand → le nom du produit doit y être, et les mots de région, plateforme, édition et monnaie doivent être compatibles avec l'offre.
   - **L'URL nomme un autre produit** (Titanfall sur la page de Titanfall 2) : alerte tout de suite, sans lire la page (Romain, 02/10/2026 : « on a déjà un problème détecté à la base »).
   - **L'URL ne nomme rien** (un code, un numéro) : le 301 du marchand, puis sa page (HTTP, puis Chromium avec le user agent Chrome).
   - **Deux groupes de marchands pour les redirections** (`merchants/*.toml`, clé `[redirect] means`) : chez la plupart (Instant Gaming, Fanatical), une redirection mène à la fiche actuelle de la même offre et c'est elle qu'on juge ; chez Kinguin, elle mène à **une autre offre** parce que la fiche du lien est en rupture : on ne s'y fie ni pour accuser ni pour blanchir, on juge le lien, et la redirection est signalée (« offre en rupture chez le marchand, le prix reste dans le feed »). Une requête par offre suffit, sans ouvrir la page : avec les en-têtes complets d'un navigateur, Kinguin répond 200 (fiche en stock) ou 301 vers la fiche servie (rupture). D'autres marchands rejoindront ce groupe au fil de l'apprentissage.
   - Si l'URL contredit AllKeyShop sur la plateforme ou la région, la page du marchand peut écarter une URL trompeuse connue (Gamingdragons : `steam-key` pour une clé EA App), jamais retenir une alerte pour autre chose.
4. Verdict : 🟢 `OK`, 🔴 `SUSPECT` (avec la raison), 🟠 `À VÉRIFIER` (impossible de conclure, sur le premier prix d'une page d'un top ou d'un coming soon), ⚪ `NON VÉRIFIABLE` (impossible de conclure ailleurs : noté, sans alerte). SUSPECT et À VÉRIFIER partent sur Discord ; OK et NON VÉRIFIABLE restent dans le journal et l'état (`--unverified` pour la liste).

Détails et exemple d'alerte : [docs/detection.md](docs/detection.md). Le moniteur ne fait que des GET, jamais de wp-admin ; `AKS/Staff` n'est utilisé que sur AllKeyShop.

## Formation

Chaque report jugé (par Romain ou par l'étude) est consigné dans le [registre des précédents](docs/precedents.md), avec sa preuve, la règle qui en découle et un test. Étude du 30/09/2026 : sur les 35 reports envoyés sur Discord, 11 vraies erreurs et 23 faux positifs, tous corrigés sans perdre une vraie erreur ; rejeu des 920 offres en tête : 2 vraies erreurs de plus trouvées. Arbitrages du 01/10/2026 (doc partagé) :

- une mauvaise édition est une erreur même si l'acheteur reçoit plus (GTA 4, Zero Company) ;
- Amazon est ignoré jusqu'à ce que ses pages soient lisibles ;
- chez Kinguin, une redirection mène à une autre offre (fiche en rupture) : Stellaris n'était pas une erreur de saisie, mais une rupture à signaler au marchand (alerte « le prix reste dans le feed » depuis le 02/10) ;
- « Year 1 Season Pass » = « Year 1 Edition » (Farming Simulator 25).

Premier jour de `top-offers` (01–02/10/2026) : 21 alertes sur les 2e et 3e prix, 11 vraies erreurs (dont Titanfall 1 vendu sur la page de Titanfall 2, un autre jeu de la série, et Horse Spirit Valley 2 sur la page de TCG Card Shop Simulator), 10 faux positifs tous devenus des règles. Arbitrages du 02/10/2026 :

- un problème vu dans l'URL suffit : on alerte sans lire la page du marchand ;
- les noms raccourcis par les marchands (« UFC 5 », « Black Ops 6 », « Onimusha: WotS ») sont une règle générale, pas un alias par produit, parce qu'il y aura beaucoup de marchands ;
- deux groupes de marchands pour les redirections (voir Fonctionnement) ;
- la monnaie de jeu vendue comme le jeu est une erreur.

## Trancher les reports : la page Price check de l'admin

Depuis le 02/10/2026, les reports se tranchent dans l'admin de l'executor, onglet **Price check** (`https://169.58.5.63.sslip.io/executor/price-check`) : chaque report y est une carte avec le verdict, la raison, le rang de l'offre dans son édition, la page AllKeyShop et l'URL du marchand, et trois boutons **Vrai positif**, **Faux positif**, **À discuter**, avec une note.

- Le moniteur écrit `reports.json` dans `/var/lib/price-check` à chaque passage ; la page le lit.
- Une décision ajoute une ligne à `decisions.jsonl` (signée de l'identifiant de connexion) ; le moniteur la relit avant son passage suivant et la reporte dans sa mémoire et dans l'export. La dernière décision par offre l'emporte.
- Deux boutons, **Lancer le price check top** et **Lancer le price check homepage**, déclenchent un passage tout de suite au lieu d'attendre l'heure prévue ; la page montre l'état de chaque mode (en cours, avancement, dernier et prochain passage). L'admin dépose une demande dans le dossier partagé, le moniteur la lit en quelques secondes.
- Un **Faux positif** ou un **À discuter** avec sa note devient ensuite une règle, une config marchand ou un alias, avec son test, et une ligne dans le [registre des précédents](docs/precedents.md). Un **Vrai positif** confirme l'alerte.

Format des fichiers : [docs/exploitation.md](docs/exploitation.md#reports-pour-ladmin).

## Couverture des marchands

État au 02/10/2026 : **64 marchands rencontrés** par le moniteur (3 595 offres contrôlées).

- **La plupart se contrôlent par l'URL seule** ; Instant Gaming, Fanatical et Ubisoft Store après leur propre 301 ; EA.com par nom partiel.
- **La page est nécessaire** pour le PS Store (URL = code produit ; page lue en HTTP, en anglais), les packs Steam (`/sub/`, `/bundle/`), les bundles G2A, PlanetPlay (URL = identifiant), LDShop (page multi-produits, option cochée).
- **Pages illisibles** (Driffle, Loaded, Wyrel : blocage anti-robot) : l'URL fait foi, elle nomme presque toujours le produit.
- **Ignoré** : Amazon (arbitrage du 01/10/2026, pages illisibles et URL sans nom).
- **Spécificités** : Wyrel (région dans `region=`), Nintendo eShop (version anglaise de la fiche, anciens domaines `nintendo.es`…), Kinguin (redirections non suivies), LDShop (option cochée), PS Store (JSON de la page, lu en en-gb).

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
| `merchants/` | Une exception par marchand (TOML) : Wyrel (région dans le paramètre `region=`), Amazon (ignoré depuis le 01/10/2026), Nintendo (version anglaise, anciens domaines), PlayStation (page lue en en-gb), LDShop (option cochée d'une page multi-produits), Kinguin (groupe « redirections non suivies »). Voir [docs/marchands.md](docs/marchands.md#configs-marchands-merchantstoml) |
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
