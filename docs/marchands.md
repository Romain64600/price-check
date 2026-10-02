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

Toute exception propre à un marchand vit dans un fichier `merchants/<marchand>.toml`, jamais dans le code. Le fichier est trouvé par l'hôte de l'URL marchand (`hosts`), par le nom AllKeyShop (`name`) ou par le début de ce nom (`name_prefixes`). Clés :

| Clé | Défaut | Rôle |
|---|---|---|
| `name` | nom du fichier | Nom AllKeyShop du marchand |
| `hosts` | `[]` | Morceaux d'hôte qui identifient ses URL (`["wyrel.com"]`, `["amazon."]`) |
| `name_prefixes` | `[]` | Débuts de nom AllKeyShop qui désignent le marchand (`["amazon"]` : Amazon.fr, Amazon.de…), reconnus avant tout contrôle |
| `skip` | `false` | `true` : marchand ignoré, ses offres ne sont pas contrôlées (ni alerte, ni ligne NON VÉRIFIABLE, ni report) |
| `browser` | `true` | `false` : ne jamais ouvrir sa page avec Chromium (elle bloque les robots) ; une URL sans nom sort en À VÉRIFIER |
| `[region] from` | `url` | Où lire la région : `url` (mots du chemin), `query` (un paramètre de l'URL, traduit par `map`), `none` (région inconnue, pas de contrôle) |
| `[region] param`, `map` | | Avec `from = "query"` : nom du paramètre et table valeur → mots de région (`global`, `eu`, `row`…) |
| `[product_name] hreflang` | | Boutique localisée : contrôler le nom sur la version de la page dans cette langue, via son lien `<link rel="alternate" hreflang>` (Nintendo : `en-GB`) |
| `[page] parser` | | Lecteur spécial de la page : `playstation` (nom du produit et libellé d'édition dans le JSON du PS Store), `selected-option` (option cochée d'une page multi-produits, LDShop) |
| `[page] locale_from`, `locale_to` | | Réécriture de l'URL avant de lire la page (PS Store : `/es-es/` → `/en-gb/`, pour un titre en anglais) |
| `[redirect] means` | `same-offer` | Les **deux groupes de marchands** pour les redirections (Romain, 02/10/2026). `same-offer` (défaut : Instant Gaming, Fanatical, Ubisoft Store) : une redirection mène à la fiche actuelle de la même offre, c'est l'URL finale qu'on juge. `out-of-stock` (Kinguin) : une redirection, ou une fiche servie sous une autre URL canonique, mène à **une autre offre** parce que celle du lien est en rupture ; on ne s'y fie ni pour accuser (« autre produit ») ni pour blanchir : le lien est jugé tel quel. Une redirection constatée est signalée : « offre en rupture chez le marchand, le prix reste dans le feed » (Romain, 02/10) ; la région n'est plus reprochée si la fiche servie correspond à l'affichage (Stellaris) |
| `localized` | `false` | `true` : titres traduits (Amazon.fr) ; un nom non reconnu dans le titre donne À VÉRIFIER au lieu de SUSPECT |

Exceptions en place :

- **`wyrel.toml`** (formation du 30/09/2026) : le slug de l'URL est générique (`...-starter-pack-bundle-eu-37543` pour une offre Global) ; la région affichée par la page est celle du paramètre `region=` de l'URL : 1 → global, 4 → eu, 5 → row, 19 → germany (relevé sur 38 offres, sans contradiction). Page derrière Cloudflare, `browser = false`.
- **`amazon.toml`** : éditions physiques (région BOX), nom tronqué dans l'URL, page qui bloque Chromium et renvoie souvent un captcha : `browser = false`, `localized = true` (titres français, voir `aliases.toml`). **Ignoré depuis le 01/10/2026** (`skip = true`, arbitrage de Romain : « on skip tous les Amazon jusqu'à modifier notre façon de requêter leurs pages ») : plus de contrôle, d'alerte ni de ligne NON VÉRIFIABLE. Conséquence connue : Elden Ring Xbox Series (Amazon.fr, URL « …-PlayStation ») n'est plus signalé. À retirer quand les pages Amazon seront lisibles.
- **`kinguin.toml`** (Romain, 02/10/2026 : « Kinguin redirige vers une autre offre quand l'offre est out of stock, donc pour Kinguin on ne se fie pas aux redirections ») : `[redirect] means = "out-of-stock"`. Cas fondateur, Stellaris (01/10) : le lien `…/172478/stellaris-starter-pack-eu-steam-cd-key` sert la fiche globale `…-starter-pack-bundle-2023-pc-steam-cd-key` ; pas une erreur de saisie, mais une rupture que le marchand doit passer dans son feed : depuis le 02/10, alerte « offre en rupture chez le marchand … le prix reste dans le feed ». Titanfall 2 (02/10, vrai positif) : le lien `…/25568/titanfall-deluxe-edition-…` nomme le premier Titanfall, alerte sur le lien lui-même. Détection (02/10) : une requête HTTP par offre, sans suivre la redirection, avec les en-têtes complets de Chrome ; 200 = fiche en stock, 301 vers une autre fiche = rupture signalée. Plus besoin de Chromium pour Kinguin. Une redirection n'est une rupture que si la fiche servie dit autre chose de l'offre (nom, région, plateforme, édition) : Kinguin renomme souvent ses fiches (« -steam- » → « -pc-steam- »), ce qui ne donne qu'une note. D'autres marchands au même comportement rejoindront ce groupe : copier le fichier.
- **`nintendo.toml`** : eShop Nintendo FR/IT/DE/ES, URL et titre localisés ; le nom se contrôle sur la version anglaise (`hreflang = "en-GB"`), page lisible en HTTP simple. Depuis le 01/10/2026, la config reconnaît aussi les anciens domaines (`nintendo.es`, `nintendo.de`, `nintendo.it`…, qui redirigent vers `nintendo.com`) et tout marchand « Nintendo eShop … » (`name_prefixes`) : avant, un lien `nintendo.es` sortait en « autre produit » sur son titre espagnol (Ni no Kuni).
- **`ldshop.toml`** (01/10/2026) : une page regroupe plusieurs produits (Standard, Deluxe, Premium Upgrade…), le lien en choisit un par `skuId` ; le titre est celui du jeu de base, on lit en plus l'option cochée (`aria-checked`). Page lisible avec Chromium.
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
| Epic Games | URL | oui (01/10/2026) | — | `store.epicgames.com/p/fc-27-e149fb` | `/p/fc-27-e149fb` : « FC 27 » est reconnu depuis le 01/10/2026 (préfixe « EA Sports » facultatif). Une URL Epic sans nom passe par la page (HTTP, puis Chromium). |

## État au 02/10/2026 : ce que le moniteur a constaté (`--coverage`)

64 marchands rencontrés en deux jours de `both` et un jour de `top-offers` (3 595 offres contrôlées). Méthode qui a marché, nombre de contrôles, dernier contrôle et une URL d'exemple. « URL » = le nom, la région et la plateforme lus dans l'URL ; « URL après 301 marchand » = l'URL finale après la redirection du marchand (groupe `same-offer`) ; « URL de la version en-GB » = Nintendo eShop ; « page (HTTP) » / « page (Chromium) » = la page a dû être lue. Amazon.fr est ignoré depuis le 01/10/2026 (ses contrôles sont antérieurs).

| Marchand | Méthodes (nombre de contrôles) | Dernier contrôle | Exemple d'URL |
|---|---|---|---|
| Allyouplay | URL (8), page (Chromium) (1) | 2026-10-02 00:04 | `https://www.allyouplay.com/xbox/ace-combat-8-wings-of-theve-standard-edition-pre-purchase-xbox-series-xs-game-ep2-88615-ep2-88615` |
| Amazon.fr | URL (14), page (HTTP) (4) | 2026-10-01 07:45 | `https://www.amazon.fr/L%C3%A9gendes-Pok%C3%A9mon-Arceus-Nintendo-Switch/dp/B09FQ8RMMV/` |
| Amazon.Fr | URL (4), page (HTTP) (1) | 2026-10-01 12:54 | `https://www.amazon.fr/Minecraft-Dungeons-2-Deluxe-Edition/dp/B0H4V5NXV9/` |
| Battle.net | URL (3) | 2026-10-02 05:19 | `https://eu.shop.battle.net/en-gb/product/diablo-iv` |
| CJS CDKeys | URL (129), page (Chromium) (1) | 2026-10-02 12:49 | `https://www.cjs-cdkeys.com/products/007-First-Light-PSN-Download-Key-%28Playstation%29-UNITED-STATES.html` |
| Dreamgame EU | URL (9), page (Chromium) (1) | 2026-10-01 16:38 | `https://www.dreamgame.com/en/dynasty-warriors-3-complete-edition-remastered-digital-deluxe-edition` |
| Driffle | URL (261) | 2026-10-02 12:49 | `https://www.driffle.com/the-first-berserker-khazan-row-pc-steam-digital-key-p9933857` |
| EA.com | URL (2) | 2026-09-30 14:26 | `https://www.ea.com/games/ea-sports-fc/fc-27/buy/checkout` |
| Eneba | URL (334), page (HTTP) (4) | 2026-10-02 13:23 | `https://www.eneba.com/steam-wild-west-pioneers-companion-edition-steam-key-pc-global` |
| eTail.Market EU | URL (2) | 2026-10-02 07:19 | `https://etail.market/dredge-complete-edition-3` |
| eTailcard | URL (1) | 2026-09-30 14:37 | `https://etailcard.com/minecraft-java-deluxe-global-minecraft-java-deluxe-edition-2005597` |
| Fanatical | URL après 301 marchand (5) | 2026-10-01 15:27 | `https://www.fanatical.com/en/game/persona-4-revival` |
| G2A | URL (340), page (Chromium) (7) | 2026-10-02 12:19 | `https://www.g2a.com/sid-meiers-civilization-vii-settlers-edition-pc-steam-key-europe-i10000507321066` |
| GameBillet EU | URL (8) | 2026-10-02 04:49 | `https://www.gamebillet.com/persona-4-revival-z` |
| GameBoost | URL (141), page (Chromium) (3) | 2026-10-02 13:23 | `https://gameboost.com/borderlands-4-xbox-series-xs-eu-00-36639` |
| Gamers Outlet | URL (18) | 2026-10-02 05:19 | `https://www.gamers-outlet.net/en/buy-titanfall-2-cd-key-ea-origin-html` |
| GamersGate | URL (3) | 2026-10-02 11:34 | `https://www.gamersgate.com/product/ace-combat-8-wings-of-theve/` |
| GAMESEAL | URL (125) | 2026-10-02 13:20 | `https://gameseal.com/hearts-of-iron-iv-cadet-edition-pc-steam-key-global` |
| Gamesplanet DE | page (Chromium) (1) | 2026-09-30 14:26 | `https://de.gamesplanet.com/game/a-plague-tale-bundle-steam-key--6084-1` |
| Gamesplanet FR | URL (3) | 2026-10-01 15:27 | `https://fr.gamesplanet.com/game/gears-of-war-e-day-microsoft-store-download--8767-1` |
| Gamesplanet UK | URL (3) | 2026-10-01 14:58 | `https://uk.gamesplanet.com/game/stupid-never-dies-steam-key--8727-1` |
| Gamesplanet US | URL (15) | 2026-10-01 19:49 | `https://us.gamesplanet.com/game/hunt-showdown-1896-deluxe-edition-steam-key--3495-10` |
| Gamingdragons | URL (79) | 2026-10-02 11:49 | `http://www.gamingdragons.com/en/game/buy-baldurs-gate-3-xbox-sx.html` |
| GAMIVO | URL (317), page (Chromium) (5) | 2026-10-02 13:05 | `https://www.gamivo.com/product/baldurs-gate-3-xbox-xboxseries-eu-en-standard` |
| Gog.com | URL (3), page (HTTP) (1) | 2026-10-01 14:58 | `https://www.gog.com/en/game/a_plague_tale_bundle` |
| Greenmangaming | URL (7) | 2026-10-01 19:08 | `https://www.greenmangaming.com/games/dynasty-warriors-3-complete-edition-remastered-pc/` |
| HRK | URL (82) | 2026-10-02 13:20 | `https://www.hrkgame.com/en/product/clair-obscur-expedition-33-deluxe-edition-row` |
| Instant Gaming | URL après 301 marchand (84), URL (5), page (HTTP) (1) | 2026-10-02 11:49 | `https://www.instant-gaming.com/en/19062-buy-donkey-kong-bananza-switch-2-nintendo-eshop/` |
| K4G | URL (139), page (Chromium) (1), page (HTTP) (1) | 2026-10-02 13:05 | `https://k4g.com/product/the-first-berserker-khazan-steam-global-cd-key-deluxe-edition-cd-key-37FA2664` |
| Keycense | URL (12) | 2026-10-02 05:19 | `https://www.keycense.com/nba-2k27-europe-steam` |
| KEYEKEY | URL (1) | 2026-09-30 14:37 | `https://www.keyekey.com/official-site/farming-simulator-25-pc-giants-key-global` |
| Kinguin | URL (245), page (Chromium) (6) | 2026-10-02 11:19 | `https://www.kinguin.net/en/category/360568/helldivers-2-super-citizen-edition-eu-xbox-series-x-s-cd-key` |
| LDShop | page (Chromium) (4), URL (1) | 2026-10-01 15:27 | `https://www.ldshop.gg/card/nba-twoktwoseven-xbox.html` |
| Loaded | URL (101) | 2026-10-02 11:34 | `https://www.loaded.com/nba-2k27-deluxe-edition-pc-steam` |
| Lootbar | URL (33) | 2026-10-02 08:49 | `https://www.lootbar.com/game-key/dune-awakening-xbox` |
| Mmoga | URL (29) | 2026-10-02 05:19 | `https://www.mmoga.com/Xbox-Live/Xbox-One-Game-Keys/Diablo-IV-Ultimate-Edition-Xbox-One-Series-XS-Download-Code.html` |
| Muve | URL (7) | 2026-10-02 12:19 | `https://muve.games/p/grand-theft-auto-v-premium-edition-xbox-3e36f5` |
| Nintendo eShop DE | URL (75), URL de la version en-GB (4), page (Chromium) (1), page (HTTP) (1) | 2026-10-02 05:19 | `https://www.nintendo.com/de-de/Spiele/Nintendo-Switch-2-Spiele/CRYMELIGHT-3199777.html` |
| Nintendo eShop ES | URL (14), URL de la version en-GB (2), page (HTTP) (1) | 2026-10-01 16:38 | `https://www.nintendo.com/es-es/Juegos/Juegos-de-Nintendo-Switch-2/Village-in-the-Shade-3171181.html` |
| Nintendo eShop FR | URL (80), URL de la version en-GB (4), page (Chromium) (3) | 2026-10-02 05:19 | `https://www.nintendo.com/fr-fr/Jeux/Jeux-Nintendo-Switch-2/CRYMELIGHT-3199777.html` |
| Nintendo eShop IT | URL (67), URL de la version en-GB (5), page (Chromium) (1), page (HTTP) (1) | 2026-10-02 05:19 | `https://www.nintendo.com/it-it/Giochi/Giochi-per-Nintendo-Switch-2/CRYMELIGHT-3199777.html` |
| PlanetPlay | page (Chromium) (3) | 2026-10-01 14:58 | `https://planetplay.com/store/games/6a6cb903475ecb8f34e03402/` |
| Playerland | URL (10) | 2026-10-01 15:27 | `https://player.land/en/p-5415/8229-fable-pre-purchase-xbox-series-x-s-microsoft-store` |
| PremiumCDkeys | URL (5) | 2026-10-01 14:58 | `https://www.premiumcdkeys.com/products/doom-the-dark-ages-pre-order-bonus-dlc-steam-pc-steam-cd-key-global` |
| PS Store DE | page (HTTP) (60), URL (25), page (Chromium) (1) | 2026-10-01 16:08 | `https://store.playstation.com/de-de/product/EP0082-PPSA37334_00-0139969750985353` |
| PS Store ES | page (HTTP) (49), URL (30), page (Chromium) (29) | 2026-10-01 16:08 | `https://store.playstation.com/es-es/product/JP0700-PPSA09840_00-MAINGAME00000000` |
| PS Store FR | page (HTTP) (37), URL (16) | 2026-10-01 20:49 | `https://store.playstation.com/fr-fr/product/UP0006-PPSA34015_00-27STANDARDBUNDLE` |
| PS Store IT | page (HTTP) (3) | 2026-10-01 16:08 | `https://store.playstation.com/it-it/product/UP6312-PPSA31381_00-0730774904492744` |
| PS Store UK | page (Chromium) (29), page (HTTP) (22), URL (13) | 2026-10-02 01:04 | `https://store.playstation.com/en-gb/product/EP0001-PPSA34056_00-STBSTD0000000000` |
| PS Store US | page (Chromium) (36), URL (21), page (HTTP) (21) | 2026-10-01 16:08 | `https://store.playstation.com/en-us/product/UP6312-PPSA22327_00-0547256477986893` |
| Royal CD Keys | URL (38) | 2026-10-02 13:38 | `https://royalcdkeys.com/products/clair-obscur-expedition-33-deluxe-edition-steam-cd-key` |
| Steam | URL (36), page (Chromium) (13), page (HTTP) (5) | 2026-10-02 05:19 | `https://store.steampowered.com/bundle/60799/Active_Matter__Deluxe_Edition/` |
| Ubisoft Store ES | URL après 301 marchand (1) | 2026-10-01 14:58 | `https://store.ubisoft.com/fr/rayman-legends-retold/69d81be7c446d004fbdc1525.html` |
| Ubisoft Store EU | URL après 301 marchand (1) | 2026-10-01 14:58 | `https://store.ubisoft.com/fr/rayman-legends-retold-%C3%A9dition-deluxe/69fbdb73d696ba353e764433.html` |
| Ubisoft Store FR | URL après 301 marchand (1) | 2026-10-01 14:58 | `https://store.ubisoft.com/fr/rayman-legends-retold-%C3%A9dition-deluxe/69fbdb73d696ba353e764433.html` |
| Vidaplayer | URL (38) | 2026-10-02 05:04 | `https://www.vidaplayer.com/product/game-playstation-5-germany/dragon-ball-sparking-zero-standard-edition` |
| WinGameStore | URL (6) | 2026-10-02 12:49 | `https://www.wingamestore.com/product/18465/ACE-COMBAT-8-WINGS-OF-THEVE/` |
| Wyrel | URL (36), page (Chromium) (1) | 2026-10-02 12:49 | `https://wyrel.com/en/buy-desevyh-titanfall-2-pc-708` |
| Xbox DE | URL (57), page (Chromium) (1) | 2026-10-02 06:19 | `https://www.xbox.com/de-de/games/store/creepy-tale-snow-child/9nv8g3rhnwnt` |
| Xbox ES | URL (8) | 2026-10-01 15:27 | `https://www.xbox.com/es-es/games/store/warrior-cats-clans-of-the-forest/9n9rtllldnd7` |
| Xbox FR | URL (56), page (HTTP) (1) | 2026-10-02 06:19 | `https://www.xbox.com/fr-fr/games/store/creepy-tale-snow-child/9nv8g3rhnwnt` |
| Xbox IT | URL (47) | 2026-10-02 06:19 | `https://www.xbox.com/it-it/games/store/creepy-tale-snow-child/9nv8g3rhnwnt` |
| YUPLAY | URL (36), page (Chromium) (1) | 2026-10-02 12:34 | `https://www.yuplay.com/product/red-dead-redemption/` |

### Pas encore monitorés, ou contrôlés sur leur page seulement

- **PlanetPlay** : URL = identifiant (`/store/games/6a6cb903…`), la page s'ouvre avec Chromium ; 3 contrôles, OK.
- **Steam `/sub/` et `/bundle/`** (packs) : URL = numéro, la page donne le titre (Chromium ou HTTP).
- **PS Store** : URL = code produit, page lue en HTTP (JSON : nom et édition), en en-gb pour les boutiques européennes.
- **Driffle, Loaded, Wyrel** : pages illisibles (anti-robot), l'URL fait foi : elle nomme presque toujours le produit. Une URL Driffle sans nom (code seul) sortirait en NON VÉRIFIABLE.
- **EA.com** : l'analyseur reconnaît le nom en partie (`ea-sports-fc` + `fc-27`), verdict OK avec la note « nom partiel ».
- **Amazon** : ignoré (`skip = true`) depuis l'arbitrage du 01/10/2026.

## Offres non vérifiables

Offres contrôlées que le moniteur n'a pas pu vérifier : ni l'URL ni la page ne donnent le nom. Elles sont notées `NON VÉRIFIABLE`, sans alerte (formation du 30/09/2026), sauf premier prix de toute la page d'un top ou d'un coming soon, y compris quand l'offre le devient plus tard (elle passe alors À VÉRIFIER et part sur Discord). Liste générée par `python3 price_check.py --unverified`, état au 02/10/2026 :

| Jeu | Édition | Marchand | Prix | Pourquoi | URL marchand | Vu le |
|---|---|---|---|---|---|---|
| Escape from Tarkov | Unheard Edition | BattlestateGames | 203.40 € | URL sans nom du produit et page marchand illisible | `https://www.escapefromtarkov.com/preorder-page` | 2026-10-01 14:58 |
| A Plague Tale Requiem | Bundle | G2A | 20.93 € | URL sans nom du produit et page marchand illisible | `https://www.g2a.com/en/a-plague-tale-bundle-pc-steam-key-global-i10000337512001` | 2026-10-01 07:00 |
| Assetto Corsa Competizione | Trilogy Bundle | G2A | 40.60 € | URL sans nom du produit et page marchand illisible | `https://www.g2a.com/assetto-corsa-trilogy-pc-steam-key-europe-i10000511542002` | 2026-10-01 14:58 |
| Assetto Corsa Competizione | Complete Trilogy | G2A | 56.85 € | URL sans nom du produit et page marchand illisible | `https://www.g2a.com/assetto-corsa-complete-trilogy-pc-steam-key-europe-i10000511548002` | 2026-10-01 14:58 |
| Assetto Corsa EVO | Trilogy Bundle | G2A | 40.60 € | URL sans nom du produit et page marchand illisible | `https://www.g2a.com/assetto-corsa-trilogy-pc-steam-key-europe-i10000511542002` | 2026-10-01 14:58 |
| Assetto Corsa EVO | Complete Trilogy | G2A | 56.85 € | URL sans nom du produit et page marchand illisible | `https://www.g2a.com/assetto-corsa-complete-trilogy-pc-steam-key-europe-i10000511548002` | 2026-10-01 14:58 |
| DREDGE | Bundle | G2A | 18.51 € | URL sans nom du produit et page marchand illisible | `https://www.g2a.com/en/dredging-diving-bundle-pc-steam-key-europe-i10000506453005` | 2026-10-01 14:58 |
| EA Sports WRC 2023 | Standard | HRK | 29.57 € | URL sans nom du produit et page marchand illisible | `https://www.hrkgame.com/en/product/wrc-23-origin` | 2026-10-01 15:27 |
| Cyberpunk 2077 PS5 | Ultimate | PS Store FR | 69.99 € | URL sans nom du produit et page marchand illisible | `https://store.playstation.com/fr-fr/product/EP4497-PPSA04029_00-EXPANSION1B00000` | 2026-10-01 15:27 |

Au 02/10/2026 : des bundles G2A (le nom d'un bundle n'est pas celui du jeu : non contrôlé), Escape from Tarkov vendu par son éditeur (URL `preorder-page`), un code produit PS Store illisible, EA Sports WRC chez HRK (`wrc-23-origin`, nom trop court). Les six offres Amazon de la liste du 01/10 sont sorties avec le marchand.

## Lecture des pages marchand (étude du 30/09/2026)

| Marchand | HTTP simple (UA navigateur) | Chromium sans écran |
|---|---|---|
| PS Store | OK : titre et JSON (nom, édition) | titre seul, h1 « Access Denied » |
| Steam, Instant Gaming, Eneba, Gamingdragons, Nintendo eShop, Gamers Outlet, K4G, Keycense, Gog.com | OK | non nécessaire |
| Kinguin | OK depuis le 02/10/2026 avec les en-têtes complets de navigateur (titre, h1, canonique) ; 403 avec un `Accept` minimal | OK |
| GAMIVO | 403 (Cloudflare « Just a moment »), même avec les en-têtes complets | OK |
| LDShop | 200 avec les en-têtes complets, mais sans l'option cochée (rendue en JavaScript) : Chromium reste nécessaire | OK |
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
