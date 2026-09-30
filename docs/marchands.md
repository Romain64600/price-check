# Couverture des marchands

Ce que le moniteur sait vérifier pour chaque marchand, et comment. **À tenir à jour** à chaque nouveau marchand ou changement de méthode, pour qu'on sache toujours quels marchands sont bien monitorés et lesquels ne le sont pas encore.

Méthodes, de la moins coûteuse à la plus coûteuse :

| Méthode | Ce qu'on fait | Coût |
|---|---|---|
| **URL directe** | Le lien de redirection AllKeyShop (UA `AKS/Staff`) donne l'URL marchand, qui contient le nom du produit | 1 requête AllKeyShop |
| **URL après le 301 du marchand** | L'URL marchand n'a qu'un numéro ; une requête chez le marchand (UA Chrome) **sans suivre la redirection** donne, dans `Location`, l'URL complète avec le slug | + 1 requête marchand, sans page |
| **Page à ouvrir** | L'URL ne contient pas le nom : il faut charger la page marchand (Chromium sans écran, UA Chrome) et lire `<title>` / `og:title` / `h1` | + 1 page complète |

## État au 30/09/2026

Testé sur les pages EA SPORTS FC 27 (Popular #1) et Dynasty Warriors 3 Complete Edition Remastered (Coming soon PC #1), une offre par marchand : **26 marchands contrôlables par l'URL seule, 2 pas encore couverts**.

| Marchand | Méthode | Nom du produit trouvé | Mots région / plateforme dans l'URL | URL marchand (chemin) | Notes |
|---|---|---|---|---|---|
| Allyouplay | URL directe | oui | — | `www.allyouplay.com/pc/dynasty-warriors-3-complete-edition-remastered` |  |
| CJS CDKeys | URL directe | oui | steam, key | `www.cjs-cdkeys.com/products/EA-Sports-FC-27-Steam-Key.html` |  |
| Driffle | URL directe | oui | global, ea-play, key | `www.driffle.com/ea-sports-fc-27-global-pc-ea-play-digital-key-p9997937` |  |
| Eneba | URL directe | oui | europe, ea-app, key | `www.eneba.com/ea-app-ea-sports-fc-27-ea-app-key-pc-europe` |  |
| G2A | URL directe | oui | europe, ea-app, key | `www.g2a.com/ea-sports-fc-27-pc-ea-app-key-europe-i10000515240002` |  |
| GameBoost | URL directe | oui | ea-app | `gameboost.com/ea-sports-fc-27-ea-app-00-79268` |  |
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
| Steam | URL directe | oui | — | `store.steampowered.com/app/4080220/EA_SPORTS_FC_27/` | Nom avec des `_` : `EA_SPORTS_FC_27`. Normaliser le chemin avant de comparer. |
| Wyrel | URL directe | oui | — | `wyrel.com/en/buy-cheap-ea-sports-fc-27-pc-196673` |  |
| Fanatical | URL après le 301 du marchand | oui | — | `www.fanatical.com/en/game/dynasty-warriors-3-complete-edition-remastered` | 301 du marchand vers l'URL avec le slug. |
| Instant Gaming | URL après le 301 du marchand | oui | ea-app | `www.instant-gaming.com/en/21656-buy-ea-sports-fc-27-pc-ea-app/` | `/en/21656-/` → 301 vers l'URL avec le slug. Page lisible en HTTP simple. |
| EA.com | **page à ouvrir** | **non** | — | `www.ea.com/games/ea-sports-fc/fc-27/buy/checkout` | URL partielle : `/ea-sports-fc/fc-27/buy/checkout`. Il faut ouvrir la page. Pas encore fait. |
| Epic Games | **page à ouvrir** | **non** | — | `store.epicgames.com/p/fc-27-e149fb` | URL partielle : `/p/fc-27-e149fb`. Il faut ouvrir la page (Chromium) pour lire le titre. Pas encore fait. |

### Pas encore monitorés

- **Epic Games** et **EA.com** : URL partielle, la page doit être ouverte avec Chromium. Boutiques officielles, risque de mauvais produit faible, mais à couvrir.
- **Marchands des 7 autres pages suivies** : pas encore testés. Le moniteur journalise la méthode utilisée pour chaque marchand ; compléter cette table au premier passage.

## Pages marchand : HTTP simple ou Chromium ?

Utile seulement quand la page doit être ouverte.

| Marchand | HTTP simple (urllib, UA Chrome) | Chromium sans écran |
|---|---|---|
| Instant Gaming | OK, titre « Buy EA Sports FC 27 - PC (EA App) » | non testé |
| Gamers Outlet | OK | OK, « EA SPORTS FC 27 (PC EA App Key - Global) » |
| Kinguin | 403 Akamai | OK, « EA Sports FC 27 PC Steam Altergift » |
| GAMIVO | 403 Cloudflare | OK, « Get EA Sports FC 27 – Steam Gift (Global) » |
| go.loaded.com (lien affilié) | 403 | 403 |
