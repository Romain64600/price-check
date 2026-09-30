# price-check

Surveille en continu le **premier prix** des pages produit du top AllKeyShop, et alerte sur Discord quand ce premier prix ne correspond pas au produit ou à notre marché.

Pages suivies (widget top clics de la barre de droite) :
- top 5 **All Popular** (`sidebar.all.popular`)
- top 4 **Coming soon PC** (`sidebar.pc.soon`)

## Objectif

Une offre peut être ajoutée sur une page produit alors qu'elle ne devrait pas y être :

- **Région non affichable** : c'est bien le produit, mais dans une région qu'on n'est pas censé afficher sur notre marché.
- **Compte saisi comme clé** : sur la page du marchand, c'est un compte, mais nous l'avons saisi en tant que clé normale.
- **Autre produit** : l'offre ne correspond pas au produit de la page, par exemple Sonic 1 ou un vieux Mario sur la page du dernier Sonic. **C'est surtout ce cas qui fait peur.**

Si cette offre est la moins chère, elle devient le premier prix affiché, et ce premier prix est faux. **L'écart avec l'offre suivante n'est pas un critère** : il peut être de 30 %, comme d'un centime. Il faut donc contrôler l'offre elle-même, pas son écart de prix.

> **État actuel** : la version en place ne détecte qu'un écart d'au moins 30 % entre les deux offres les moins chères. Elle ne répond pas à l'objectif et doit être revue. La règle proposée est dans [docs/detection.md](docs/detection.md#règle-proposée--lurl-dabord) : pour toute nouvelle offre en premier prix, suivre son lien de redirection AllKeyShop (UA `AKS/Staff`) et contrôler **l'URL marchand**, qui contient presque toujours le nom du produit, la région et la plateforme. Chromium sans écran en repli quand l'URL ne dit rien.

## Démarrage rapide

```
echo 'DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...' > .env
python3 price_check.py --dry-run --once          # test : un passage, sans envoi
sudo cp price-check.service /etc/systemd/system/
sudo systemctl enable --now price-check          # boucle sans fin, relancée si elle plante
journalctl -u price-check -f
```

Python 3 seulement, aucune dépendance.

## Fonctionnement actuel (à revoir)

1. Toutes les 30 min, il relit les listes via l'API JSON `getLists`, en ne gardant que les jeux.
2. Toutes les 2 min 30, il lit les offres de chaque page (`var gamePageTrans` dans le HTML), avec 2 s de pause entre deux GET.
3. Pour chaque édition, il compare les deux offres de clés les moins chères (`priceCard`, sans les offres compte ni les offres « sans prix » à `0.02`).
4. Si l'écart est d'au moins 30 %, il alerte sur Discord, une seule fois par offre et par prix (`alerted.json`).

Le moniteur ne fait que des GET en lecture seule et n'appelle jamais les liens `/redirection/` (ils comptent des clics).

## Couverture des marchands

Le contrôle passe par l'URL marchand que donne le lien de redirection AllKeyShop. État au 30/09/2026, sur 28 marchands testés :

- **26 contrôlables par l'URL seule**, dont 2 après le 301 du marchand lui-même (Instant Gaming, Fanatical).
- **2 pas encore couverts** : Epic Games et EA.com (URL partielle, page à ouvrir avec Chromium).
- **Non testés** : les marchands des 7 autres pages suivies.

Table détaillée, méthode par méthode : [docs/marchands.md](docs/marchands.md). Elle doit être mise à jour à chaque marchand qui oblige à ouvrir sa page, pour qu'on sache toujours qui est monitoré et qui ne l'est pas encore.

## Documentation

| Document | Contenu |
|---|---|
| [docs/detection.md](docs/detection.md) | Objectif, user agents, lien de redirection, règle proposée « l'URL d'abord », règle actuelle, questions ouvertes |
| [docs/exploitation.md](docs/exploitation.md) | Installation, options, réglages, systemd, journaux, tests |
| [docs/marchands.md](docs/marchands.md) | Couverture par marchand : méthode de contrôle, marchands non couverts |
| [docs/reconnaissance.md](docs/reconnaissance.md) | Analyse du site : API des listes, cache, structure des offres |

## Arborescence

| Fichier | Rôle |
|---|---|
| `price_check.py` | Le moniteur (réglages en tête de fichier) |
| `test_price_check.py` | Tests hors ligne : `python3 -m unittest -v` |
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
| `autoptimize.js`, `product_bundle.js` | Bundles JS du site (logique top clics et offres) |
