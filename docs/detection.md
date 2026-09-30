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

Une offre ne dit pas, côté public, quel produit le marchand vend réellement. Vérifié le 30/09/2026 :

| Source | Ce qu'on y trouve | Titre marchand ? |
|---|---|---|
| Page produit (`gamePageTrans.prices[]`) | id d'offre, marchand, édition, région, plateforme, prix | Non |
| API publique CatalogV2 (`vaks.php?action=CatalogV2`) | nom canonique du produit, `offers_count`, une seule offre (la meilleure) avec `buy_url` = lien `/redirection/` | Non |
| Lien `/redirection/offer/<id>` | la page du marchand | Oui, mais **compte un clic**, interdit |
| wp-admin, `admin.php?page=aks-merchant-feeds-9&search[field]=productId&search[search]=<legacyId>&list=all&store=all` | lignes `tr[data-offer]` avec `{id, name, url, storeId, price}` : `name` est le titre du produit chez le marchand, `url` sa page | **Oui** |
| API historique de prix (`price_history_api.php`) | répond `{"error":"400-2"}` avec l'id ou le slug, paramètres à retrouver | Non |

Seul wp-admin donne donc le titre marchand. La détection automatique d'un mauvais produit passe par là.

## Règle proposée

Trois niveaux, du plus sûr au plus automatique :

1. **Nouveau premier prix → alerte « à vérifier ».** À chaque passage, si l'offre en tête d'une page (id d'offre) change, on alerte avec marchand, région, édition, plateforme, prix et id. Une offre jamais vue sur la page est marquée **NOUVELLE OFFRE** : c'est le scénario redouté, une offre fraîchement ajoutée qui passe devant. Un humain vérifie. Rien ne peut être raté, mais chaque changement de tête alerte.
2. **Titre marchand ≠ produit → « SUSPECT : produit différent ».** Si le moniteur dispose d'un accès wp-admin, il récupère le titre marchand de l'offre en tête (recherche par `productId`), le normalise (minuscules, `&` → `and`, ponctuation retirée, édition retirée en fin de titre, mêmes règles que la saisie des offres) et le compare **strictement** au nom canonique du produit. En cas de différence, l'alerte est marquée suspecte et affiche les deux titres : « Sonic the Hedgehog » contre « Sonic Racing CrossWorlds » se voit d'un coup d'œil.
3. **Signaux automatiques sans wp-admin**, ajoutés en marque « suspect » sur l'alerte : région dans une liste interdite (à définir) ; prix très en dessous des autres offres de la même édition (par exemple sous la moitié de la médiane). Ce dernier signal n'est qu'une aide : une offre fautive peut n'être qu'un centime sous la suivante.

## Questions ouvertes

1. **Accès wp-admin pour le moniteur.** Peut-on lui donner une session staff (compte dédié ou cookie) utilisable depuis le serveur, pour lire `merchant_feeds` ? Et cette liste couvre-t-elle toutes les offres en ligne d'un produit, ou seulement celles venues des flux marchands ?
2. **Régions interdites** sur allkeyshop.com en EUR : lesquelles ? Une liste suffirait.
3. **Quel premier prix ?** Celui de la page (haut du tableau), ou celui de chaque édition ?
4. **Volume d'alertes acceptable.** Alerter à chaque changement de tête, ou seulement sur NOUVELLE OFFRE et SUSPECT ?

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
