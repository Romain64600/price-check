# Détection

## Objectif

Contrôler le **premier prix** de chaque page suivie : l'offre la moins chère de chaque édition doit correspondre au produit, à la région, à la plateforme et à l'édition affichées. Les erreurs visées :

| Erreur | Description |
|---|---|
| **Mauvais produit** | L'offre est un autre jeu : Sonic 1 ou un vieux Mario sur la page du dernier Sonic. **C'est surtout ce cas qui fait peur.** |
| Région non affichable | Le bon produit, mais dans une région qu'on n'est pas censé afficher sur notre marché |
| Compte saisi comme clé | Le marchand vend un compte, mais l'offre est saisie comme clé |
| Mauvaise édition, contenu additionnel | Deluxe saisie en Standard, DLC ou season pass saisi comme le jeu |

L'écart de prix avec l'offre suivante n'est pas un critère : une offre fautive peut n'être moins chère que d'un centime. On contrôle donc l'offre elle-même.

Attention à ne pas confondre région et langue : `IN ENGLISH ONLY` (`EA ENG/POL/RUS ONLY`) n'est qu'une restriction de langue, c'est autorisé.

## User agents

| Où | User agent |
|---|---|
| Pages AllKeyShop : page produit, API des listes, lien de redirection | `AKS/Staff` |
| Chez le marchand | un navigateur normal (`BROWSER_UA`, Chrome), jamais `AKS/Staff` |

Pas de wp-admin.

## Le lien de redirection (vérifié le 30/09/2026)

```
GET https://www.allkeyshop.com/redirection/offer/eur/<id d'offre>?locale=en&merchant=<id marchand>     (UA AKS/Staff)
```

`id d'offre` et `id marchand` sont les champs `id` et `merchant` de l'offre dans `gamePageTrans`. Le tableau des offres étant construit en JS, ce lien n'est pas dans le HTML, il faut le composer.

Réponse : HTTP 200, page intermédiaire « Redirecting... » qui contient l'URL marchand dans `<script id="appData">` (JSON, clé `redirectionUrl`), dans un `<meta http-equiv="refresh">` et dans un lien « click here ». On lit `appData.redirectionUrl`, le meta refresh en secours. Exemple : `samples/redirection_kinguin.html`.

**L'URL marchand suffit presque toujours** : elle contient le nom du produit, et souvent la région, la plateforme et l'édition. Voir la table par marchand dans [marchands.md](marchands.md). Un lien affilié (`go.loaded.com/...?u=https://www.loaded.com/...`) porte la vraie cible dans un paramètre, on la déballe.

## Règle en place

### 1. Premier prix

Sur chaque page suivie, pour chaque édition, l'offre de clé la moins chère (`priceCard`) parmi les offres disponibles, hors offres compte (`account`) et hors offres « sans prix » (`price == 0.02`). Une édition avec une seule offre compte aussi : une offre fautive dans « Deluxe » est en tête de son édition.

### 2. Nouvelle offre en tête → contrôle

Le moniteur garde dans `state.json` les id d'offre déjà contrôlés, avec leur verdict. Une offre en tête déjà contrôlée ne coûte rien. Une offre jamais vue déclenche le contrôle. Les verdicts sont oubliés 30 jours après la dernière fois où l'offre a été vue en tête.

### 3. Contrôle, du moins cher au plus cher

1. **URL directe** : redirection AllKeyShop → URL marchand → analyse de son chemin.
2. **URL après le 301 du marchand** : si le chemin n'a pas le nom du produit (Instant Gaming : `/en/21656-/`), une requête chez le marchand sans suivre la redirection ; l'en-tête `Location` donne l'URL complète (`/en/21656-buy-ea-sports-fc-27-pc-ea-app/`).
3. **Page marchand** : en HTTP simple d'abord (UA navigateur ; PS Store lu en en-gb, nom et édition pris dans son JSON), puis Chromium sans écran si la config le permet ; on analyse `<title>`, `og:title` et `<h1>`. Une page anti-robot (« Just a moment », « Access Denied », captcha Amazon) compte comme illisible.
4. **Confirmation sur la page** : quand l'URL contient le nom mais contredit AllKeyShop sur la **plateforme**, la **console** ou la **zone**, la page marchand est lue avant l'alerte. Le moniteur lit son titre, son `og:title`, son `h1` et les champs « Region / Platform » du corps (K4G : `PLATFORM Steam REGION Global`) ; une page qui nomme plusieurs zones (menu de filtres) ne contredit rien. Si elle dit explicitement ce qu'affiche AllKeyShop (Gamingdragons : URL `steam-key`, page « PC - EA App »), la raison tombe ; si elle confirme l'URL, l'alerte le dit (« confirmé par la page ») ; si elle est muette ou illisible, l'URL fait foi.
5. **Nom invérifiable** : si ni l'URL ni la page ne donnent le nom, l'offre sort en À VÉRIFIER, ou en SUSPECT si l'URL montre déjà un autre problème (Elden Ring Xbox chez Amazon : `PlayStation`). Sur une boutique au titre traduit (`localized = true`, Amazon), un nom non reconnu dans le titre donne À VÉRIFIER.

### 4. Analyse d'un texte marchand (chemin d'URL ou titre)

Tout est normalisé en minuscules, lettres et chiffres seulement, séparés par `-` (`EA_SPORTS_FC_27` → `ea-sports-fc-27`). Les segments de langue de l'URL (`/en/`, `/en-us/`) sont ignorés.

| Contrôle | Règle | Raison affichée |
|---|---|---|
| Nom du produit | Le nom AllKeyShop, sans séparateurs, doit être dans le texte (`easportsfc27`). Les symboles ™ ® © sont retirés (`Gran Turismo™ 7`), un sigle des premiers mots est accepté (« A.O.T. 3 » = « Attack on Titan 3 »), un mot de 6 lettres et plus peut différer d'une lettre (`pokmon` = `pokemon`), et `aliases.toml` donne les autres noms connus (titre européen, titres français d'Amazon). La tolérance « un mot manquant » ne vaut que pour le nom sans suffixe de plateforme : « Pokémon Bouclier » ne passe pas pour « Pokemon Sword Nintendo Switch ». Les chiffres et les chiffres romains sont équivalents (« Minecraft Dungeons 2 » reconnaît `minecraft-dungeons-ii`, et inversement) ; le suffixe plateforme du nom (« GTA 6 **PS5** », « … Xbox Series », « … Nintendo Switch 2 », « … VR »), l'année de désambiguïsation (« Screamer **2026** ») et les sigles pointés (« S.T.A.L.K.E.R. » = `stalker`) sont gérés ; « The Official Game » ne compte pas. Sur un titre de page, un titre court entièrement contenu dans le nom (« UFC® 5 » pour « EA Sports UFC 5 PS5 », deux mots au moins) est accepté. À défaut, tous ses mots significatifs (hors *the*, *of*, *edition*, *remastered*, mots d'édition…) doivent y être : nom **partiel**, accepté avec une note. `NAME_ALIASES` couvre les abréviations, dans les deux sens (« GTA 6 PS5 » reconnaît `grand-theft-auto-vi-ps5`, « Call of Duty » reconnaît `cod`). | `autre produit chez le marchand : « Metal Garden » au lieu de « TORO 2 »` ; `nom du produit introuvable` quand le texte ne nomme aucun produit (codes seulement) |
| Compte | `account`, `accounts`… dans le texte pour une offre saisie en clé | `compte chez le marchand, saisi en clé` |
| Région interdite | `ru`, `cis`, `asia`, `latam`, `india`, `tr`, `cn`, `ar`, `br`, `jp`, `kr`… (`FORBIDDEN_REGION_WORDS`) | `région interdite : …` |
| Zone | La zone AllKeyShop se lit dans le **nom de filtre** de la région (`STEAM EU` → EU, `XBOX GERMANY CODE` → Allemagne, `EA ENG/POL/RUS ONLY` → mondiale, langue seulement). Le marchand nomme une zone (`global`, `eu`/`europe`, `row`, `emea`, `usa`, `uk`, `germany`, `france`…) : l'offre est suspecte si cette zone **ne couvre pas** toute la zone affichée (clé EU affichée GLOBAL, Stellaris, formation du 30/09/2026). Une zone plus large chez le marchand (clé GLOBAL affichée EUROPE, Mario Kart World chez K4G) est sans danger. Un gift n'a pas de zone comparée (formation, K4G Screamer 2026). La zone peut venir d'ailleurs que du chemin de l'URL : `merchants/*.toml` (Wyrel : paramètre `region=`). | `région : AllKeyShop X, marchand Y` |
| Gift | `gift`/`altergift` dans le texte pour une offre dont la région AllKeyShop n'est pas un gift (nom affiché **ou** nom de filtre : « GERMANY » = `STEAM GIFT GERMANY` est un gift) | `gift chez le marchand, affiché en clé` |
| Plateforme | Le texte nomme une plateforme (`steam`, `ea-app`/`origin`, `epic`, `gog`, `xbox`, `microsoft-store`…) d'une autre famille que celle de l'offre, et aucune de la bonne famille. La famille de l'offre vient de `activationPlatform` **et** du nom de filtre de la région (`EA GLOBAL` → EA App). Sur une page console (« … Xbox Series », « … PS5 », « … Nintendo Switch »), une URL qui nomme une autre console est suspecte (Elden Ring Xbox chez Amazon : `PlayStation`). Contrôle fait sur tous les mots du texte, nom du produit compris (« EA SPORTS FC 27 **Xbox Series** »). `microsoft-store` avec `xbox` = famille Xbox ; le préfixe `steam-` des URL Eneba ne compte pas s'il y a aussi `xbox-live`. | `plateforme : AllKeyShop X, marchand Y` |
| Édition | Le texte nomme une édition (`deluxe`, `ultimate`, `goty`…) qui n'est pas celle d'AllKeyShop. Suspect seulement si **la page AllKeyShop a l'édition du marchand** (GTA 4 : Complete Edition rangée en Standard alors que la page a « Complete »), ou si l'édition affichée est supérieure à celle vendue (Ultimate affichée, `standard` vendu). Pas de comparaison : éditions génériques (« Bundle », « Bundle 2 », « Bonus », « DLC Bundle »…), mot propre à l'édition présent chez le marchand (« 2024 Edition », « Mediterranean Bundle »), édition de base vendue `standard` (« Preorder bonus »). `goty` = « Game of the Year ». | `édition : rangée en X, le marchand vend Y (la page a une édition Z)` |
| Contenu additionnel | `dlc`, `season-pass`, `expansion`, `soundtrack`, `upgrade`, sauf si le texte contient aussi `bonus` (`pre-order-bonus-dlc` est le bonus de précommande vendu avec le jeu), si l'édition AllKeyShop annonce du contenu en plus (« Standard + DLC Bundle », « … + Bonus »), ou si **la page AllKeyShop est celle d'un DLC** : une de ses éditions s'appelle « DLC », ou son nom contient DLC / Expansion / Season Pass (Diablo 4 Lord of Hatred, formation du 30/09/2026 ; les listes et CatalogV2 typent ces pages « game » sur console) | `contenu additionnel : …` |

Les mots du nom du produit sont retirés avant les contrôles de région, plateforme et édition : « Dynasty Warriors 3 Complete Edition Remastered » ne déclenche pas le contrôle d'édition.

**Éditions bundle** (`Bundle`, `Pack`, `Collection`, `Trilogy` dans le nom de l'édition) : le marchand vend un lot qui porte un autre nom (« The Witcher Trilogy Pack » sur la page de The Witcher 3), le nom du produit n'est donc pas contrôlé, seulement la région, la plateforme, le compte et le gift. L'alerte le dit en note.

Toutes ces règles et leurs cas réels sont dans le [registre des précédents](precedents.md). Elles viennent des premiers passages réels du 30/09/2026 : mode top-games, 29 offres en tête, 5 faux SUSPECT (bonus DLC, GOTY, bundle), puis 2 sur Minecraft Dungeons 2 (« II ») ; calibrage du mode homepage, faux SUSPECT sur GTA 6 PS5 (alias « GTA », suffixe « PS5 »), « Preorder bonus » contre `standard-edition`, « Standard + DLC Bundle » contre `dlc`. Aucune vraie anomalie.

### 5. Verdict et alerte Discord

| Verdict | Quand | Discord |
|---|---|---|
| 🟢 `OK` | Nom trouvé, aucune raison | Envoyé si `NOTIFY_OK` (oui par défaut, à couper une fois l'outil rodé) |
| 🔴 `SUSPECT` | Au moins une raison | Toujours |
| 🟠 `À VÉRIFIER` | Impossible de conclure (URL sans nom et page illisible, titre traduit, redirection AllKeyShop en échec 3 fois) **et** l'offre est le premier prix de toute la page, sur une page d'un top ou d'un coming soon | Toujours |
| ⚪ `NON VÉRIFIABLE` | Impossible de conclure, dans les autres cas (autre édition, page hors top/coming soon, Amazon toujours) : on prend note, liste par `--unverified` | Jamais (formation du 30/09/2026) |

Format d'une alerte :

```
🔴 SUSPECT · Sonic Racing CrossWorlds (Popular #3) · Standard
Kinguin · GLOBAL · steam · 2.99 € · offre 140380343 · contrôle : URL
Raison : nom du produit absent (URL)
Marchand : <https://www.kinguin.net/category/1/sonic-the-hedgehog-pc-steam>
Page : <https://www.allkeyshop.com/blog/buy-sonic-racing-crossworlds-cd-key-compare-prices/>
```

Un contrôle ou un envoi Discord raté est retenté au passage suivant ; rien n'est marqué contrôlé tant que l'alerte n'est pas partie.

### Boutiques localisées

Sur les eShop Nintendo (FR, IT, DE…), l'URL et le titre sont dans la langue de la boutique. `merchants/nintendo.toml` fait contrôler le nom sur la **version anglaise** de la page, donnée par son lien `hreflang="en-GB"` : « Le Chat Chapeauté Pagaille sous la pluie » redevient *The Cat in the Hat Rainy Day Mayhem*, et « TORO 2 → Metal Garden » reste suspect. Le PS Store se lit en en-gb (`merchants/playstation.toml`) : « El reino marchito » redevient *The Withered World*. Amazon (éditions physiques) : titres français reconnus par `aliases.toml` ; un titre traduit inconnu ou une page captcha donne À VÉRIFIER. Les sites sœurs d'AllKeyShop (goclecd.fr, keyforsteam.de…) ont des slugs anglais : ils ne donnent pas les titres localisés.

## Limites connues

- **Nom partiel accepté.** « witcher-3-wild-hunt » passe pour « The Witcher 3 Wild Hunt ». Un autre jeu qui reprendrait tous les mots significatifs du nom passerait aussi.
- **URL muette.** Quand ni l'URL ni le 301 du marchand ne donnent le nom, tout repose sur le titre de la page, donc sur Chromium et sur les protections anti-robot du marchand.
- **Listes de mots à compléter.** Régions interdites, familles de plateformes, éditions : en tête de `price_check.py`, à enrichir au fil des alertes.
- **Un contrôle par offre.** Une offre déjà jugée OK n'est pas recontrôlée si le marchand change sa page derrière la même URL.
- **Retard lié au cache** : jusqu'à environ 2 min sur les pages produit, 30 min sur la composition des listes.

## Questions ouvertes

1. **Régions acceptées** sur allkeyshop.com en EUR : la liste des mots interdits est un premier jet, à valider.
2. **Éditions** : faut-il alerter quand l'URL dit `deluxe` pour une offre Standard, ou est-ce trop bruyant ?
