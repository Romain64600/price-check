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

**Périmètre : les pages produit publiques, rien d'autre.** Pas de wp-admin, pas de lien `/redirection/` (il compte un clic).

Ce que la page produit donne pour chaque offre (`gamePageTrans.prices[]`) : id d'offre, marchand, édition, région, plateforme d'activation, prix, disponibilité. Elle ne dit pas quel produit le marchand vend réellement (vérifié le 30/09/2026, ni dans le HTML ni dans l'API publique CatalogV2). Un mauvais produit ne peut donc pas être identifié automatiquement : le moniteur doit **signaler ce qui vient d'arriver en tête**, et un humain vérifie sur le site.

## Règle proposée

Le scénario redouté est toujours le même : **une offre ajoutée à la page devient le premier prix**. C'est détectable sans regarder le prix :

1. **Mémoire des offres de chaque page.** À chaque passage, le moniteur note les id d'offre présents sur la page. Au premier passage, toutes les offres présentes servent de base.
2. **Nouvelle offre en tête → alerte.** Si l'offre en premier prix a un id jamais vu sur cette page, alerte Discord avec marchand, région, édition, plateforme, prix, id d'offre et lien de la page. Cela marche même si elle n'est qu'un centime sous la suivante.
3. **Indices ajoutés à l'alerte**, pour aider l'humain, sans jamais conditionner l'envoi :
   - prix très en dessous des autres offres de la même édition (un vieux Sonic à 2 € sur un jeu à 50 €) ;
   - marchand jamais vu sur cette page ;
   - région jamais vue sur cette page, ou dans une liste interdite (à définir).

Option, à décider : alerter aussi quand une offre déjà connue passe en tête (changement de premier prix), en alerte de moindre priorité.

## Questions ouvertes

1. **Quel premier prix ?** Celui de la page (haut du tableau), ou celui de chaque édition ? Je propose chaque édition : une offre fautive dans « Deluxe » n'est pas en tête de page, mais elle est bien en tête de son édition.
2. **Changement de premier prix entre offres connues** : alerter aussi, ou seulement sur nouvelle offre ?
3. **Régions interdites** sur allkeyshop.com en EUR : lesquelles ?

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
