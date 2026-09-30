# Détection

## Objectif

Contrôler le **premier prix** de chaque page suivie : l'offre la moins chère doit correspondre au produit et à notre marché. Les erreurs visées :

| Erreur | Description |
|---|---|
| Région non affichable | Le bon produit, mais dans une région qu'on n'est pas censé afficher sur notre marché |
| Compte saisi comme clé | Le marchand vend un compte, mais l'offre est saisie comme clé (`account == false`) |
| Mauvais produit ou édition | L'offre ne correspond pas au produit ou à l'édition de la page |

L'écart de prix avec l'offre suivante n'est pas un critère : une offre fautive peut n'être moins chère que d'un centime.

Attention à ne pas confondre région et langue. Sur EA SPORTS FC 27 (échantillon du 28/09/2026), le premier prix Standard est Mmoga à 54,99 € en `IN ENGLISH ONLY` (`EA ENG/POL/RUS ONLY`) : c'est une offre **légitime**, car `IN ENGLISH ONLY` n'est qu'une restriction de langue, pas de région.

## Régions déjà tranchées

| Région (`region_name`) | `filter_name` | Verdict |
|---|---|---|
| `IN ENGLISH ONLY` | `EA ENG/POL/RUS ONLY` | Autorisée : restriction de langue, pas de région |

## Cas principal : mauvais produit

C'est surtout ce cas qui fait peur : sur la page du dernier Sonic, un marchand ajoute une offre qui est en fait Sonic 1, ou un vieux Mario. Elle est moins chère, donc elle devient le premier prix du comparateur.

La page produit ne dit pas quel produit le marchand vend réellement (`gamePageTrans.prices[]` : id d'offre, marchand, édition, région, plateforme, prix). Pour le savoir, on suit le lien de redirection de l'offre.

## User agents

| Où | User agent |
|---|---|
| Pages AllKeyShop : page produit, API des listes, lien de redirection | `AKS/Staff` |
| Chez le marchand | un navigateur normal, jamais `AKS/Staff` |

Pas de wp-admin.

## Le lien de redirection (vérifié le 30/09/2026)

```
GET https://www.allkeyshop.com/redirection/offer/eur/<id d'offre>?locale=en&merchant=<id marchand>     (UA AKS/Staff)
```

`id d'offre` et `id marchand` sont les champs `id` et `merchant` de l'offre dans `gamePageTrans`. Le tableau des offres étant construit en JS, ce lien n'est pas dans le HTML, il faut le composer.

Réponse : HTTP 200, page intermédiaire « Redirecting... » qui contient l'URL marchand à trois endroits : `<script id="appData">` (JSON, clé `redirectionUrl`), `<meta http-equiv="refresh">` et un lien « click here ». On lit `appData.redirectionUrl`.

**L'URL marchand suffit presque toujours** : elle contient le nom du produit, et souvent la région, la plateforme et l'édition. Test sur les 6 premières offres Standard d'EA SPORTS FC 27 :

| Marchand | Offre AKS (édition / région / plateforme) | URL marchand (chemin) | Nom du produit dans l'URL | Mots région / plateforme |
|---|---|---|---|---|
| Kinguin | Standard / GIFT / steam | `kinguin.net/category/609603/ea-sports-fc-27-pc-steam-altergift` | oui | steam, altergift |
| GAMIVO | Standard / GIFT / steam | `gamivo.com/product/ea-sports-fc-27-pc-steam-gift-global-standard` | oui | steam, gift, global, standard |
| Gamers Outlet | Standard / GLOBAL / ea-app | `gamers-outlet.net/en/ea-sports-fc-27-pc-ea-app-key-global` | oui | ea-app, key, global |
| Loaded | Standard / GLOBAL / ea-app | `go.loaded.com/c/…?u=https://www.loaded.com/ea-sports-fc-27-standard-edition-pc-ea-app` (lien affilié, cible dans `u=`) | oui | standard, ea-app |
| Driffle | Standard / GLOBAL / ea-app | `driffle.com/ea-sports-fc-27-global-pc-ea-play-digital-key-p9997937` | oui | global, ea-play, key |
| Instant Gaming | Standard / GLOBAL / ea-app | `instant-gaming.com/en/21656-/`, mais **301 vers** `instant-gaming.com/en/21656-buy-ea-sports-fc-27-pc-ea-app/` | oui, après la redirection du marchand | ea-app |

Quand l'URL ne dit rien (Instant Gaming : un simple numéro), une requête chez le marchand **sans suivre la redirection** (user agent Chrome) suffit souvent : l'en-tête `Location` du `301` donne l'URL complète avec le slug. Ce n'est qu'en dernier recours qu'il faut ouvrir la page marchand. En HTTP simple (urllib/curl, user agent Chrome), Instant Gaming, Gamers Outlet et Driffle répondent, mais Kinguin (Akamai), GAMIVO (Cloudflare, « Just a moment... ») et le lien affilié de Loaded renvoient 403. Avec **Chromium sans écran** (installé sur le serveur, `chromium --headless=new --dump-dom`, user agent Chrome), Kinguin, GAMIVO et Gamers Outlet donnent leur titre : « EA Sports FC 27 PC Steam Altergift », « Get EA Sports FC 27 – Steam Gift (Global) », « EA SPORTS FC 27 (PC EA App Key - Global) ». Seul le lien affilié de Loaded reste bloqué, mais son URL contient déjà la cible.

## Règle proposée : l'URL d'abord

1. **Premier prix.** Sur chaque page suivie, l'offre de clé la moins chère de chaque édition (`priceCard`, sans offres compte ni `0.02`).
2. **Nouvelle offre en tête → contrôle.** Le moniteur garde les id d'offre déjà contrôlés. Une offre en tête jamais contrôlée déclenche le contrôle ; les autres ne coûtent rien.
3. **Contrôle par l'URL.** Redirection AKS → `redirectionUrl` (cible extraite de `u=` pour un lien affilié). Puis :
   - **nom du produit** : le nom AKS normalisé (minuscules, `&` → `and`, ponctuation → `-` : `ea-sports-fc-27`) doit apparaître dans le chemin de l'URL ;
   - **région et plateforme** : les mots de l'URL (global, eu, europe, gift, altergift, row, steam, ea-app, ea-play, epic, xbox, ps5, key, account…) doivent être compatibles avec l'offre AKS. Une URL qui dit `account` pour une offre saisie en clé, ou `ru`, `asia`, `latam`, `tr`… pour une région GLOBAL/EU, est suspecte ;
   - **édition** : `deluxe`, `ultimate`… dans l'URL pour une offre Standard est suspect.
4. **Replis quand l'URL n'a pas de nom**, dans l'ordre :
   - une requête sur l'URL marchand sans suivre la redirection (user agent Chrome) : si le marchand répond `301`/`302`, on contrôle l'URL de l'en-tête `Location` (Instant Gaming : `/en/21656-/` → `/en/21656-buy-ea-sports-fc-27-pc-ea-app/`) ;
   - sinon, Chromium sans écran, user agent navigateur, lecture de `<title>` / `og:title` / `h1`, mêmes contrôles sur le titre.
5. **Verdict et alerte Discord** :
   - `SUSPECT` : nom absent de l'URL et du titre, ou région / plateforme / édition incompatible → alerte ;
   - `À VÉRIFIER` : impossible de conclure (URL muette et page bloquée) → alerte, un humain regarde ;
   - `OK` : tout concorde → journal seulement.

Chaque alerte donne le jeu, l'édition, le marchand, le prix, l'URL marchand et la raison.

## Questions ouvertes

1. **Premier prix de chaque édition** (proposé) ou seulement de la page ?
2. **Régions acceptées** sur allkeyshop.com en EUR : quels mots d'URL sont normaux (global, eu, gift…) et lesquels sont interdits (ru, cis, asia, latam, tr, cn…) ? Une liste suffirait, on peut la compléter au fil des alertes.
3. **Alerter aussi les `OK`** en faible priorité, pour voir l'outil travailler au début ?

## Règle actuelle (à remplacer)

La version en place ne détecte qu'un écart de prix d'au moins 30 %. Elle ne couvre pas l'objectif ci-dessus. Son fonctionnement est décrit ci-dessous.

### Pages suivies

Réglage `LISTS` dans `price_check.py` :

| Liste | Libellé dans l'alerte | Jeux suivis |
|---|---|---|
| `sidebar.all.popular` | Popular | top 5 |
| `sidebar.pc.soon` | Coming soon PC | top 4 |

Seuls les jeux comptent (`productType == "game"`), classés par `index`. Les logiciels sont retirés avant de prendre le top : si Windows est 3e, c'est le 6e élément qui entre dans le top 5.

### Offres comparées

Sur chaque page, le moniteur garde les offres qui correspondent au tableau affiché par défaut :

- offres de clés seulement (`account == false`) ;
- pas les offres sans prix (`price == 0.02`, voir [reconnaissance](reconnaissance.md#3-offres-dune-page-produit)) ;
- disponibles (`dispo` non nul) ;
- prix comparé : `priceCard` (paiement par carte, frais compris).

### Règle

Pour chaque **édition** (Standard, Deluxe, Ultimate…), les offres sont triées par prix. Une alerte part si :

```
prix de la moins chère ≤ 70 % du prix de la 2e moins chère
```

soit un écart d'au moins 30 % (`THRESHOLD = 0.30`). Une édition qui n'a qu'une seule offre n'est pas évaluée.

Les régions sont volontairement mélangées au sein d'une édition : une offre dont la région est mal renseignée est justement un cas à repérer.

### Anti-doublon

- Une offre (`id`) n'est alertée qu'une fois pour un prix donné. Si son prix change et qu'elle reste anormale, une nouvelle alerte part.
- Une offre qui n'est plus anormale est oubliée, et pourra de nouveau alerter plus tard.
- Cette mémoire est enregistrée dans `alerted.json` (id d'offre → prix), elle survit donc à un redémarrage.
- Si l'envoi Discord échoue, l'offre n'est pas marquée comme envoyée : l'envoi est retenté au passage suivant.

### Exemple d'alerte Discord

```
**EA SPORTS FC 27** (Popular #1) - Standard
Mmoga (IN ENGLISH ONLY, ea) : **32.99 €**, soit -40% face à GAMIVO à 54.99 €
Offre 140289123 - <https://www.allkeyshop.com/blog/buy-ea-sports-fc-27-key-compare-prices/>
```

### Limites connues

- **Comparaison au sein d'une seule édition.** Une clé Standard rangée par erreur dans « Ultimate » au prix d'une Standard n'est pas détectée, car on ne compare pas les éditions entre elles.
- **Seulement la moins chère contre la 2e.** Si deux offres anormales ont un prix proche, l'écart entre elles est faible et aucune alerte ne part.
- **Retard lié au cache** : jusqu'à environ 2 min sur les pages produit, et 30 min sur la composition des listes.
- Les offres compte et les prix PayPal ne sont pas surveillés.
