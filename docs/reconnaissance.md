# Reconnaissance du site AllKeyShop

Reconnaissance faite le 28/09/2026 en lecture seule (7 GET, UA `AKS/Staff`), complétée le 30/09/2026 pour la liste `pc.soon`, puis le 02/10/2026 pour l'affichage des offres (code JS de la page produit, section 4). Les réponses brutes sont dans [`samples/`](../samples).

## 1. Listes à surveiller

Les listes Popular et Coming soon sont les onglets du widget top clics de la barre de droite. Pas besoin de parser le HTML pour les obtenir, une API JSON publique les renvoie :

```
GET https://api.allkeyshop.com/videogame/api/topClick/getLists/eur/allkeyshop.com?lists[]=sidebar.all.popular&lists[]=sidebar.pc.soon
```

- Id de liste : `<widget>.<liste>`. Barre latérale (widget « TOP 50 ») : `sidebar.<all|pc|xbox|playstation|nintendo>.<popular|soon>`. Widgets de la home : `<nom>.default` avec `nom` ∈ mostAnticipated, recentlyReleased, subscription, giftCard, fps, rpg, strategy, action, adventure, management, racing, vr (8 éléments chacun ; les onglets plateforme de ces widgets n'existent pas côté API). On peut demander plusieurs listes dans un seul appel en répétant `lists[]=`, mais **un seul id inconnu fait répondre 503 « Backend fetch failed » à tout l'appel**.
- Réponse : `{<widget>: {"<liste>": {items: [...], data: {listId, legacyIds, ...}}}}`.
- Le 30/09/2026, les 22 listes de la home totalisent 415 pages produit de jeux uniques (`samples/api_topclick_home.json`).

| Liste | `listId` | Éléments (30/09/2026) |
|---|---|---|
| `all.popular` | 57 | 60 |
| `all.soon` | 56 | 38 |
| `pc.soon` | 48 | 36 |

- Champs d'un item : `index` (rang, à partir de 1), `name`, `legacyId` (id produit, = `sku` du JSON-LD), `urls["allkeyshop.com.eur"]`, `platform`, `productType`, `releaseDates`, `merchant`, `price`.
- En-têtes : `Cache-Control: max-age=1800`, `X-RateLimit-Limit: 60`.
- Popular contient aussi des logiciels (Windows, Office). Pour ne garder que les jeux, filtrer sur `productType == "game"`.
- Côté HTML : `div.content-box.topclick[data-type="sidebar"] > .tab-content.topclick-list.sidebar`, rendu en JS. `all.popular` est préchargée dans `var topclickTrans = {..., preloadData}`.

## 2. Cache

- Serveur Apache, pas de Cloudflare. Un cache HTTP est placé devant le site : `Cache-Control: max-age=120`, avec un en-tête `Age`.
- `AKS/Staff` ne contourne pas le cache : la page renvoyée est identique octet pour octet à celle servie à Chrome. Il faut donc compter jusqu'à environ 2 minutes de retard sur les pages produit, et 30 minutes sur les listes.

## 3. Offres d'une page produit

Les offres sont déjà dans le HTML, sans AJAX, dans `<script id="aks-offers-js-extra">var gamePageTrans = {...};` :

- `prices[]` : `id` (id d'offre), `merchant`, `merchantName`, `edition`, `region`, `activationPlatform`, `account`, `originalPrice`, `voucher_code`, `voucher_discount_value`, `price` (après coupon), `pricePaypal` / `feesPaypal`, `priceCard` / `feesCard`, `dispo`, `isOfficial` (liste complète en section 4)
- `editions{id: {name}}`, `regions{id: {region_name, ...}}`, `merchants{id: {name, rating, ...}}`

Extraction : regex `var gamePageTrans = (\{.*?\});\n` (flag DOTALL), puis `json.loads`.

Par défaut, le tableau affiché (`#offerTable`, rendu par DataTables) montre `priceCard`, et seulement les offres de clés, sans les offres compte (détail en section 4).

**Sentinelle : `price == 0.02` veut dire « pas de prix ».** Le JS du site trie ces offres en dernier, et le JSON-LD les exclut. Il faut les ignorer, sinon on aura des faux positifs massifs (47 offres sur 147 pour EA FC 27).

Autres sources, non utilisées par le moniteur :
- JSON-LD `AggregateOffer` (`lowPrice`, `offerCount`), sans édition ni région, et qui inclut les offres compte.
- Historique de prix : `https://www.allkeyshop.com/api/price_history_api.php?normalised_name=<id>&currency=eur&database=allkeyshop.com&v2=1`

## 4. Ce que le visiteur voit (code de la page lu le 02/10/2026)

Lu dans le bundle JS de la page produit (`autoptimize_*.js`, objet `__offersData`) et dans le HTML des filtres, sur Stellaris (196 offres), puis mesuré sur 25 pages suivies (1 291 offres).

**Tri.** `order: [{name: 'priceCard', dir: 'asc'}]` et `body.fees-card` : les offres sont triées par prix carte (prix après coupon + frais carte), du moins cher au plus cher. C'est le tri du moniteur. Le filtre « Fees » change la colonne de tri : sans frais (`price`), PayPal (`pricePaypal`), carte (`priceCard`), « lowest » (le moins cher des deux). La sentinelle `0.02` est triée en dernier ; si toutes les offres sont à `0.02`, le tableau affiche le formulaire « prévenez-moi ».

**Filtres par défaut** (cases cochées du HTML, `aks-select` par filtre, lus par `OfferFilter`) :

| Filtre (`name`) | Par défaut | Effet |
|---|---|---|
| `productType` | `key` coché, `account` décoché | les offres compte sont masquées ; le bouton « show N account offers » (`.offers-show-account-btn`) coche les deux. Le moniteur fait pareil en Top Offers (comptes exclus) ; Full Page les prend. |
| `fees` | `card` | les offres dont le marchand refuse la carte (`allowCard: false`) sont masquées. Mesure du 02/10 : aucune sur 1 291 offres. Le moniteur les garde, puisqu'elles s'affichent en mode PayPal. |
| `store`, `platform`, `region`, `edition` | rien de coché | toutes les offres. `?editions=<id,…>` et `?regions=<id,…>` dans l'URL de la page pré-filtrent le tableau (liens partagés). |

**Pagination.** 25 lignes (`pageLength: 25`) ; « Show more » en ajoute 25, « Show all » affiche tout. Les éditions ne sont pas séparées : une seule liste, toutes éditions mêlées, d'où le choix des 3 premiers prix **par édition** (une offre fautive d'une petite édition peut être en 40e ligne de la page et en tête de son édition).

**Une offre** (`gamePageTrans.prices[]`) : `id`, `merchant` / `merchantName` / `merchantIcon`, `edition`, `region`, `activationPlatform`, `account`, `originalPrice` (avant coupon), `voucher_code` / `voucher_discount_value` / `voucher_discount_type` (`%` ou `#` pour un montant), `price` (après coupon), `pricePaypal` / `feesPaypal`, `priceCard` / `feesCard`, `allowCard`, `allowPaypal`, `dispo`, `isOfficial`, `isFirstParty`.
- `dispo` vaut 1 sur toutes les offres mesurées : la page ne publie que des offres en vente (le moniteur ignore quand même une offre à `dispo` 0). Une rupture chez le marchand n'apparaît donc pas dans le JSON : c'est le cas Kinguin (le prix reste dans le feed, voir [detection.md](detection.md)).
- Le coupon est déjà déduit de `price` ; le lien d'achat ajoute `&coupon=<code>`.

**Le reste de `gamePageTrans`** :
- `merchants{id}` : `name`, `logo`, `rating {score, count, maximum}`, `official`, `paypal`, `card`, `recomended`, `review_link`, `note` ; un marchand peut porter `giftCardReminder`, qui ajoute une ligne « Tip » sous chacune de ses offres.
- `regions{id}` : `region_name` (« GLOBAL »), `filter_name` (le nom affiché dans le filtre : « STEAM GLOBAL », celui qui dit le vrai sens de la région), `region_short_description`, `region_long_description`.
- `editions{id: {name}}` (ids numériques ou textes : `"1"` Standard, `"anniversary"`…), `officialMerchants` (ids des boutiques officielles), `isAccPage` (`"1"` : page d'un produit vendu en compte ; un clic sur une offre compte ouvre un avertissement).
- Le graphe d'historique tient sa propre liste de régions « compte » : `301`, `412`, `433`, `454`, `466`, `477` à `481`, `993`, `99ac`, `99acus`, `88ac`, `24ac`.

**Lien d'achat.** `https://www.allkeyshop.com/redirection/offer/<devise>/<id>?locale=<locale>&merchant=<id marchand>[&coupon=<code>]`.

## 5. Lien de redirection

Le lien `/redirection/offer/...` compte un clic. La reconnaissance du 28/09 conseillait de ne pas l'appeler ; Romain a tranché le 30/09/2026 (« t'as juste à suivre l'URL de redirection ») : c'est le chemin voulu pour voir ce que vend le marchand. Le moniteur l'appelle avec `AKS/Staff`, une fois par offre contrôlée ou recontrôlée, jamais plus.
