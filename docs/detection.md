# Détection

## Objectif

Contrôler le **premier prix** de chaque page suivie : l'offre la moins chère doit correspondre au produit et à notre marché. Les erreurs visées :

| Erreur | Description |
|---|---|
| Région non affichable | Le bon produit, mais dans une région qu'on n'est pas censé afficher sur notre marché |
| Compte saisi comme clé | Le marchand vend un compte, mais l'offre est saisie comme clé (`account == false`) |
| Mauvais produit ou édition | L'offre ne correspond pas au produit ou à l'édition de la page |

L'écart de prix avec l'offre suivante n'est pas un critère : une offre fautive peut n'être moins chère que d'un centime.

Exemple (échantillon du 28/09/2026) : sur EA SPORTS FC 27, le premier prix Standard est Mmoga à 54,99 €, en région `IN ENGLISH ONLY` (`EA ENG/POL/RUS ONLY`), soit seulement 3 % sous GAMIVO à 56,61 €.

## Questions ouvertes

1. **Régions** : quelles régions sont autorisées sur notre marché (allkeyshop.com, EUR) ? Par exemple, `IN ENGLISH ONLY` est-elle acceptable ? Régions vues dans les échantillons : `GLOBAL`, `EUROPE`, `GIFT`, `GIFT EU`, `IN ENGLISH ONLY`, `XBOX/PC`, `XBOX/PC EU`, `ACCOUNT`.
2. **Compte ou clé** : pour le vérifier, il faut voir la page produit du marchand. Son URL n'apparaît pas dans la page publique, seulement via `/redirection/offer/...`, qui compte des clics. Existe-t-il un accès staff à l'URL marchand de chaque offre (wp-admin, flux marchands) ?
3. **Premier prix** : lequel contrôle-t-on ? Le premier prix global de la page (en haut du tableau), ou le premier prix de chaque édition ?
4. **Alerte** : alerte-t-on à chaque changement de premier prix, pour vérification humaine, ou seulement quand une règle automatique juge l'offre suspecte ?

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
