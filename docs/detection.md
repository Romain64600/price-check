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
3. **Page marchand** : en dernier recours, Chromium sans écran ouvre la page (UA Chrome) et on analyse `<title>`, `og:title` et `<h1>`.

### 4. Analyse d'un texte marchand (chemin d'URL ou titre)

Tout est normalisé en minuscules, lettres et chiffres seulement, séparés par `-` (`EA_SPORTS_FC_27` → `ea-sports-fc-27`). Les segments de langue de l'URL (`/en/`, `/en-us/`) sont ignorés.

| Contrôle | Règle | Raison affichée |
|---|---|---|
| Nom du produit | Le nom AllKeyShop, sans séparateurs, doit être dans le texte (`easportsfc27`). Les chiffres et les chiffres romains sont équivalents (« Minecraft Dungeons 2 » reconnaît `minecraft-dungeons-ii`, et inversement), et le suffixe plateforme du nom (« GTA 6 **PS5** », « … Xbox Series ») est facultatif chez le marchand. À défaut, tous ses mots significatifs (hors *the*, *of*, *edition*, *remastered*, mots d'édition…) doivent y être : nom **partiel**, accepté avec une note. `NAME_ALIASES` couvre les abréviations, dans les deux sens (« GTA 6 PS5 » reconnaît `grand-theft-auto-vi-ps5`, « Call of Duty » reconnaît `cod`). | `nom du produit absent` |
| Compte | `account`, `accounts`… dans le texte pour une offre saisie en clé | `compte chez le marchand, saisi en clé` |
| Région interdite | `ru`, `cis`, `asia`, `latam`, `india`, `tr`, `cn`, `ar`, `br`, `jp`, `kr`… (`FORBIDDEN_REGION_WORDS`) | `région interdite : …` |
| Famille de région | Familles GLOBAL (`global`, `worldwide`), EU (`eu`, `europe`) et ROW (`row`) : AllKeyShop affiche une famille et le texte marchand en nomme une autre. Validé par formation le 30/09/2026 (Stellaris : Kinguin EU affiché GLOBAL). Un marchand peut porter la région ailleurs que dans le chemin de l'URL : voir `merchants/*.toml` (Wyrel : paramètre `region=`). | `région : AllKeyShop X, marchand Y` |
| Gift | `gift`/`altergift` dans le texte pour une offre dont la région AllKeyShop n'est pas GIFT | `gift chez le marchand, affiché en clé` |
| Plateforme | Le texte nomme une plateforme (`steam`, `ea-app`/`origin`, `epic`, `gog`, `xbox`…) d'une autre famille que `activationPlatform` | `plateforme : AllKeyShop X, marchand Y` |
| Édition | Le texte nomme une édition (`deluxe`, `ultimate`, `goty`…) qui n'est pas celle d'AllKeyShop. Seulement si l'édition AllKeyShop est elle-même connue : « Preorder bonus », « Early Access », « Supporter Edition » ne se comparent pas. `standard` pour « Standard + Bonus » passe, `goty` pour « Game of the Year » aussi (synonymes canonisés des deux côtés). | `édition : AllKeyShop X, marchand Y` |
| Contenu additionnel | `dlc`, `season-pass`, `expansion`, `soundtrack`, `upgrade`, sauf si le texte contient aussi `bonus` (`pre-order-bonus-dlc` est le bonus de précommande vendu avec le jeu) ou si l'édition AllKeyShop annonce du contenu en plus (« Standard + DLC Bundle », « … + Bonus ») | `contenu additionnel : …` |

Les mots du nom du produit sont retirés avant les contrôles de région, plateforme et édition : « Dynasty Warriors 3 Complete Edition Remastered » ne déclenche pas le contrôle d'édition.

**Éditions bundle** (`Bundle`, `Pack`, `Collection`, `Trilogy` dans le nom de l'édition) : le marchand vend un lot qui porte un autre nom (« The Witcher Trilogy Pack » sur la page de The Witcher 3), le nom du produit n'est donc pas contrôlé, seulement la région, la plateforme, le compte et le gift. L'alerte le dit en note.

Ces règles viennent des premiers passages réels du 30/09/2026 : mode top-games, 29 offres en tête, 5 faux SUSPECT (bonus DLC, GOTY, bundle), puis 2 sur Minecraft Dungeons 2 (« II ») ; calibrage du mode homepage, faux SUSPECT sur GTA 6 PS5 (alias « GTA », suffixe « PS5 »), « Preorder bonus » contre `standard-edition`, « Standard + DLC Bundle » contre `dlc`. Aucune vraie anomalie.

### 5. Verdict et alerte Discord

| Verdict | Quand | Discord |
|---|---|---|
| 🟢 `OK` | Nom trouvé, aucune raison | Envoyé si `NOTIFY_OK` (oui par défaut, à couper une fois l'outil rodé) |
| 🔴 `SUSPECT` | Au moins une raison | Toujours |
| 🟠 `À VÉRIFIER` | Impossible de conclure : URL sans nom et page illisible, ou redirection AllKeyShop en échec 3 fois | Toujours |

Format d'une alerte :

```
🔴 SUSPECT · Sonic Racing CrossWorlds (Popular #3) · Standard
Kinguin · GLOBAL · steam · 2.99 € · offre 140380343 · contrôle : URL
Raison : nom du produit absent (URL)
Marchand : <https://www.kinguin.net/category/1/sonic-the-hedgehog-pc-steam>
Page : <https://www.allkeyshop.com/blog/buy-sonic-racing-crossworlds-cd-key-compare-prices/>
```

Un contrôle ou un envoi Discord raté est retenté au passage suivant ; rien n'est marqué contrôlé tant que l'alerte n'est pas partie.

## Limites connues

- **Nom partiel accepté.** « witcher-3-wild-hunt » passe pour « The Witcher 3 Wild Hunt ». Un autre jeu qui reprendrait tous les mots significatifs du nom passerait aussi.
- **URL muette.** Quand ni l'URL ni le 301 du marchand ne donnent le nom, tout repose sur le titre de la page, donc sur Chromium et sur les protections anti-robot du marchand.
- **Listes de mots à compléter.** Régions interdites, familles de plateformes, éditions : en tête de `price_check.py`, à enrichir au fil des alertes.
- **Un contrôle par offre.** Une offre déjà jugée OK n'est pas recontrôlée si le marchand change sa page derrière la même URL.
- **Retard lié au cache** : jusqu'à environ 2 min sur les pages produit, 30 min sur la composition des listes.

## Questions ouvertes

1. **Régions acceptées** sur allkeyshop.com en EUR : la liste des mots interdits est un premier jet, à valider.
2. **Éditions** : faut-il alerter quand l'URL dit `deluxe` pour une offre Standard, ou est-ce trop bruyant ?
