# Couverture des marchands

Ce que le moniteur sait vérifier pour chaque marchand, et comment. **À tenir à jour** à chaque nouveau marchand ou changement de méthode, pour qu'on sache toujours quels marchands sont bien monitorés et lesquels ne le sont pas encore.

Le moniteur note dans `state.json` la méthode qui a marché pour chaque marchand rencontré ; `python3 price_check.py --coverage` l'affiche en Markdown, prêt à coller ici.

Méthodes, de la moins coûteuse à la plus coûteuse :

| Méthode | Ce qu'on fait | Coût |
|---|---|---|
| **URL directe** | Le lien de redirection AllKeyShop (UA `AKS/Staff`) donne l'URL marchand, qui contient le nom du produit | 1 requête AllKeyShop |
| **URL après le 301 du marchand** | L'URL marchand n'a qu'un numéro ; une requête chez le marchand (UA Chrome) **sans suivre la redirection** donne, dans `Location`, l'URL complète avec le slug | + 1 requête marchand, sans page |
| **Page à ouvrir** | L'URL ne contient pas le nom : il faut charger la page marchand (Chromium sans écran, UA Chrome) et lire `<title>` / `og:title` / `h1` | + 1 page complète |

## Configs marchands (`merchants/*.toml`)

Toute exception propre à un marchand vit dans un fichier `merchants/<marchand>.toml`, jamais dans le code. Le fichier est trouvé par l'hôte de l'URL marchand (`hosts`) ou par le nom AllKeyShop (`name`). Clés :

| Clé | Défaut | Rôle |
|---|---|---|
| `name` | nom du fichier | Nom AllKeyShop du marchand |
| `hosts` | `[]` | Morceaux d'hôte qui identifient ses URL (`["wyrel.com"]`, `["amazon."]`) |
| `browser` | `true` | `false` : ne jamais ouvrir sa page avec Chromium (elle bloque les robots) ; une URL sans nom sort en À VÉRIFIER |
| `[region] from` | `url` | Où lire la région : `url` (mots du chemin), `query` (un paramètre de l'URL, traduit par `map`), `none` (région inconnue, pas de contrôle) |
| `[region] param`, `map` | | Avec `from = "query"` : nom du paramètre et table valeur → mots de région (`global`, `eu`, `row`…) |
| `[product_name] hreflang` | | Boutique localisée : contrôler le nom sur la version de la page dans cette langue, via son lien `<link rel="alternate" hreflang>` (Nintendo : `en-GB`) |
| `[page] parser` | | Lecteur spécial de la page : `playstation` (nom du produit et libellé d'édition dans le JSON du PS Store) |
| `[page] locale_from`, `locale_to` | | Réécriture de l'URL avant de lire la page (PS Store : `/es-es/` → `/en-gb/`, pour un titre en anglais) |
| `localized` | `false` | `true` : titres traduits (Amazon.fr) ; un nom non reconnu dans le titre donne À VÉRIFIER au lieu de SUSPECT |

Exceptions en place :

- **`wyrel.toml`** (formation du 30/09/2026) : le slug de l'URL est générique (`...-starter-pack-bundle-eu-37543` pour une offre Global) ; la région affichée par la page est celle du paramètre `region=` de l'URL : 1 → global, 4 → eu, 5 → row, 19 → germany (relevé sur 38 offres, sans contradiction). Page derrière Cloudflare, `browser = false`.
- **`amazon.toml`** : éditions physiques (région BOX), nom tronqué dans l'URL, page qui bloque Chromium et renvoie souvent un captcha : `browser = false`, `localized = true` (titres français, voir `aliases.toml`).
- **`nintendo.toml`** : eShop Nintendo FR/IT/DE, URL et titre localisés ; le nom se contrôle sur la version anglaise (`hreflang = "en-GB"`), page lisible en HTTP simple.
- **`playstation.toml`** (étude du 30/09/2026) : l'URL ne contient qu'un code produit ; la page se lit en HTTP simple, son JSON donne le nom et le libellé d'édition (« Crimson Desert Enhanced » + « Standard Edition ») ; les boutiques européennes sont lues en en-gb. Chromium n'est pas utilisé (« Access Denied »).

Pour ajouter un marchand : copier un fichier, ajuster `name`/`hosts`, et ajouter le cas réel dans `test_price_check.py` (`TestMerchantConfigs`).

## État au 30/09/2026

Testé sur les pages EA SPORTS FC 27 (Popular #1) et Dynasty Warriors 3 Complete Edition Remastered (Coming soon PC #1), une offre par marchand, puis complété par le premier passage réel sur les 9 pages (31 offres en tête, 31 OK) : **29 marchands contrôlables par l'URL seule, 1 pas encore couvert**. Les packs et bundles (`/sub/` Steam, trilogie G2A) passent par la page avec Chromium.

| Marchand | Méthode | Nom du produit trouvé | Mots région / plateforme dans l'URL | URL marchand (chemin) | Notes |
|---|---|---|---|---|---|
| Allyouplay | URL directe | oui | — | `www.allyouplay.com/pc/dynasty-warriors-3-complete-edition-remastered` |  |
| CJS CDKeys | URL directe | oui | steam, key | `www.cjs-cdkeys.com/products/EA-Sports-FC-27-Steam-Key.html` |  |
| Driffle | URL directe | oui | global, ea-play, key | `www.driffle.com/ea-sports-fc-27-global-pc-ea-play-digital-key-p9997937` |  |
| Eneba | URL directe | oui | europe, ea-app, key | `www.eneba.com/ea-app-ea-sports-fc-27-ea-app-key-pc-europe` |  |
| G2A | URL directe (page à ouvrir pour un bundle) | oui | europe, ea-app, key | `www.g2a.com/ea-sports-fc-27-pc-ea-app-key-europe-i10000515240002` |  |
| GameBoost | URL directe (écrit « II » pour « 2 », géré) | oui | ea-app | `gameboost.com/ea-sports-fc-27-ea-app-00-79268` |  |
| Gamers Outlet | URL directe | oui | global, ea-app, key | `www.gamers-outlet.net/en/ea-sports-fc-27-pc-ea-app-key-global` | Page lisible en HTTP simple. |
| GamersGate | URL directe | oui | — | `www.gamersgate.com/product/dynasty-warriors-3-complete-edition-remastered/` |  |
| GAMESEAL | URL directe | oui | global, ea-app, key | `gameseal.com/ea-sports-fc-27-pc-ea-app-key-global` |  |
| Gamesplanet DE | URL directe | oui | steam, key | `de.gamesplanet.com/game/dynasty-warriors-3-complete-edition-remastered-steam-key--8450-1` |  |
| Gamesplanet FR | URL directe | oui | steam, key | `fr.gamesplanet.com/game/dynasty-warriors-3-complete-edition-remastered-steam-key--8450-1` |  |
| Gamesplanet UK | URL directe | oui | steam, key | `uk.gamesplanet.com/game/dynasty-warriors-3-complete-edition-remastered-steam-key--8450-1` |  |
| Gamesplanet US | URL directe | oui | steam, key | `us.gamesplanet.com/game/dynasty-warriors-3-complete-edition-remastered-steam-key--8450-1` |  |
| Gamingdragons | URL directe | oui | origin, key | `www.gamingdragons.com/en/game/buy-ea-sports-fc-27-origin-key.html` |  |
| GAMIVO | URL directe | oui | global, gift, steam, standard | `www.gamivo.com/product/ea-sports-fc-27-pc-steam-gift-global-standard` | Page bloquée en HTTP simple (Cloudflare « Just a moment »), lisible avec Chromium. |
| Greenmangaming | URL directe | oui | — | `www.greenmangaming.com/games/dynasty-warriors-3-complete-edition-remastered-pc/` |  |
| HRK | URL directe | oui | ea-app | `www.hrkgame.com/en/product/ea-sports-fc-27-ea-app` |  |
| K4G | URL directe | oui | global, gift, altergift, steam, standard | `k4g.com/product/ea-sports-fc-27-steam-global-altergift-standard-edition-up-to-12-hours-alt` |  |
| Keycense | URL directe | oui | ea-app | `www.keycense.com/ea-sports-fc-27-ea-app` |  |
| Kinguin | URL directe | oui | altergift, steam | `www.kinguin.net/category/609603/ea-sports-fc-27-pc-steam-altergift` | Page bloquée en HTTP simple (Akamai 403), lisible avec Chromium sans écran. |
| Loaded | URL directe | oui | ea-app, standard | `www.loaded.com/ea-sports-fc-27-standard-edition-pc-ea-app` | Lien affilié `go.loaded.com` (403, même avec Chromium) ; la cible est dans le paramètre `u=`. |
| Mmoga | URL directe | oui | ea-app, english-only | `www.mmoga.com/EA-Games/EA-SPORTS-FC-27-EA-App-English-Only.html` | `English-Only` dans l'URL = restriction de langue, autorisée. |
| Steam | URL directe pour `/app/`, **page à ouvrir** pour `/sub/` | oui pour `/app/` | — | `store.steampowered.com/app/4080220/EA_SPORTS_FC_27/` | Nom avec des `_` : `EA_SPORTS_FC_27`. Les packs (`/sub/1675064/`, AION 2 Founder's Pack) n'ont qu'un numéro : Chromium lit le titre, OK au passage réel. |
| YUPLAY | URL directe | oui | xbox | `www.yuplay.com/product/minecraft-dungeons-ii-xbox-series-xs-and-xbox-on-pc/` | Vu au passage réel du 30/09/2026. Écrit « II » pour « 2 » : équivalence gérée depuis. |
| Wyrel | URL directe, région dans le paramètre `region=` (`merchants/wyrel.toml`) | oui | — | `wyrel.com/en/buy-cheap-ea-sports-fc-27-pc-196673` |  |
| Fanatical | URL après le 301 du marchand | oui | — | `www.fanatical.com/en/game/dynasty-warriors-3-complete-edition-remastered` | 301 du marchand vers l'URL avec le slug. |
| Lootbar | URL directe | oui | — | `www.lootbar.com/game-key/ace-combat-8-wings-of-theve-emea` | Vu au passage réel du 30/09/2026 (Ace Combat 8 Deluxe). |
| Instant Gaming | URL après le 301 du marchand | oui | ea-app | `www.instant-gaming.com/en/21656-buy-ea-sports-fc-27-pc-ea-app/` | `/en/21656-/` → 301 vers l'URL avec le slug. Page lisible en HTTP simple. |
| EA.com | URL directe, nom partiel | partiel (`ea-sports-fc` + `fc-27`) | — | `www.ea.com/games/ea-sports-fc/fc-27/buy/checkout` | URL partielle : `/ea-sports-fc/fc-27/buy/checkout`. Les mots `ea`, `sports`, `fc`, `27` y sont tous : nom partiel, accepté avec une note. |
| Epic Games | **page à ouvrir** | **non** | — | `store.epicgames.com/p/fc-27-e149fb` | URL partielle : `/p/fc-27-e149fb`. Il faut ouvrir la page (Chromium) pour lire le titre. Pas encore fait. |

### Pas encore monitorés

- **Epic Games** : URL partielle (`/p/fc-27-e149fb`), la page doit être ouverte avec Chromium. Boutique officielle, risque de mauvais produit faible, mais à couvrir.
- **EA.com** : l'analyseur reconnaît le nom en partie (`ea-sports-fc` + `fc-27`), verdict OK avec la note « nom partiel ».
- **Marchands qui n'ont pas encore eu d'offre en tête** sur les 9 pages : non vérifiés. Le moniteur note la méthode utilisée pour chaque marchand rencontré (`--coverage`) ; compléter cette table quand un nouveau marchand apparaît.

## Lecture des pages marchand (étude du 30/09/2026)

| Marchand | HTTP simple (UA navigateur) | Chromium sans écran |
|---|---|---|
| PS Store | OK : titre et JSON (nom, édition) | titre seul, h1 « Access Denied » |
| Steam, Instant Gaming, Eneba, Gamingdragons, Nintendo eShop, Gamers Outlet | OK | non nécessaire |
| Kinguin, GAMIVO, LDShop | 403 (Akamai, Cloudflare) | OK |
| Driffle | « Blocked - Driffle » | « Blocked - Driffle » |
| Loaded, go.loaded.com | 403 | 403 |
| Amazon.fr / .de | captcha, parfois la page | bloqué |
| Wyrel | Cloudflare | Cloudflare |

Quand la page est illisible, l'URL fait foi (Driffle, Loaded).

## Pages marchand : HTTP simple ou Chromium ?

Utile seulement quand la page doit être ouverte.

| Marchand | HTTP simple (urllib, UA Chrome) | Chromium sans écran |
|---|---|---|
| Instant Gaming | OK, titre « Buy EA Sports FC 27 - PC (EA App) » | non testé |
| Gamers Outlet | OK | OK, « EA SPORTS FC 27 (PC EA App Key - Global) » |
| Kinguin | 403 Akamai | OK, « EA Sports FC 27 PC Steam Altergift » |
| GAMIVO | 403 Cloudflare | OK, « Get EA Sports FC 27 – Steam Gift (Global) » |
| G2A (bundle) | non testé | OK, titre lu au passage réel |
| Steam (`/sub/`) | non testé | OK, « AION 2 » dans le titre |
| go.loaded.com (lien affilié) | 403 | 403 |
