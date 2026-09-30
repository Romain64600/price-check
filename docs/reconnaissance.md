# Reconnaissance du site AllKeyShop

Reconnaissance faite le 28/09/2026 en lecture seule (7 GET, UA `AKS/Staff`), puis complétée le 30/09/2026 pour la liste `pc.soon`. Les réponses brutes sont dans [`samples/`](../samples).

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

- `prices[]` : `id` (id d'offre), `merchant`, `merchantName`, `edition`, `region`, `activationPlatform`, `account`, `originalPrice`, `voucher_code`, `voucher_discount_value`, `price` (après coupon), `pricePaypal` / `feesPaypal`, `priceCard` / `feesCard`, `dispo`, `isOfficial`
- `editions{id: {name}}`, `regions{id: {region_name, ...}}`, `merchants{id: {name, rating, ...}}`

Extraction : regex `var gamePageTrans = (\{.*?\});\n` (flag DOTALL), puis `json.loads`.

Par défaut, le tableau affiché (`#offerTable`, rendu par DataTables) montre `priceCard`, et seulement les offres de clés, sans les offres compte.

**Sentinelle : `price == 0.02` veut dire « pas de prix ».** Le JS du site trie ces offres en dernier, et le JSON-LD les exclut. Il faut les ignorer, sinon on aura des faux positifs massifs (47 offres sur 147 pour EA FC 27).

Autres sources, non utilisées par le moniteur :
- JSON-LD `AggregateOffer` (`lowPrice`, `offerCount`), sans édition ni région, et qui inclut les offres compte.
- Historique de prix : `https://www.allkeyshop.com/api/price_history_api.php?normalised_name=<id>&currency=eur&database=allkeyshop.com&v2=1`

## 4. À ne pas faire

Ne pas appeler les liens `/redirection/offer/...` : ils comptent des clics.
