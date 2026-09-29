# price-check

Moniteur des pages produit du top AllKeyShop (top 10 Popular, top 4 Coming soon, top clics de la barre de droite). Le but est de repérer rapidement une offre mal placée et beaucoup moins chère.

Ce dépôt contient pour l'instant la reconnaissance du site, faite le 28/09/2026 en lecture seule (7 GET, UA `AKS/Staff`). Les fichiers bruts sont dans `samples/`.

## 1. Listes à surveiller

Les listes Popular et Coming soon sont les deux onglets du widget top clics de la barre de droite. On n'a pas besoin de parser le HTML pour les obtenir, une API JSON publique les renvoie :

```
GET https://api.allkeyshop.com/videogame/api/topClick/getLists/eur/allkeyshop.com?lists[]=sidebar.all.popular&lists[]=sidebar.all.soon
```

- Réponse : `{sidebar: {"all.popular": {items: [...], data: {listId: 57}}, "all.soon": {items: [...], data: {listId: 56}}}}`
- Champs d'un item : `index`, `name`, `legacyId` (id produit, = `sku` JSON-LD), `urls["allkeyshop.com.eur"]`, `platform`, `productType`, `releaseDates`, `merchant`, `price`.
- Id de liste : `sidebar.<all|pc|xbox|playstation|nintendo>.<popular|soon>`.
- En-têtes : `Cache-Control: max-age=1800`, `X-RateLimit-Limit: 60`.
- Popular contient aussi des logiciels (Windows, Office). Pour ne garder que les jeux, filtrer sur `productType == "game"`.
- Côté HTML : `div.content-box.topclick[data-type="sidebar"] > .tab-content.topclick-list.sidebar`, rendu en JS. `all.popular` est préchargée dans `var topclickTrans = {..., preloadData}`.

## 2. Cache

- Serveur Apache, pas de Cloudflare. Il y a un cache HTTP devant le site : `Cache-Control: max-age=120` avec un en-tête `Age`.
- `AKS/Staff` ne contourne pas le cache : la page renvoyée est identique octet pour octet à celle servie à Chrome. Il faut donc compter jusqu'à environ 2 minutes de retard.

## 3. Offres d'une page produit

Les offres sont déjà dans le HTML, sans AJAX, dans `<script id="aks-offers-js-extra">var gamePageTrans = {...};` :

- `prices[]` : `id` (id d'offre), `merchant`, `merchantName`, `edition`, `region`, `activationPlatform`, `account`, `originalPrice`, `voucher_code`, `voucher_discount_value`, `price` (après coupon), `pricePaypal` / `feesPaypal`, `priceCard` / `feesCard`, `dispo`, `isOfficial`
- `editions{id: {name}}`, `regions{id: {region_name, ...}}`, `merchants{id: {name, rating, ...}}`

Extraction : regex `var gamePageTrans = (\{.*?\});\n` (flag DOTALL), puis `json.loads`.

Par défaut, le tableau affiché (`#offerTable`, rendu par DataTables) montre `priceCard` et seulement les offres de clés, sans les offres compte.

**Sentinelle : `price == 0.02` veut dire « pas de prix ».** Le JS du site trie ces offres en dernier et le JSON-LD les exclut. Il faut les ignorer, sinon on aura des faux positifs massifs (47 offres sur 147 pour EA FC 27).

Autres sources :
- JSON-LD `AggregateOffer` (`lowPrice`, `offerCount`), sans édition ni région, et qui inclut les offres compte.
- Historique de prix : `https://www.allkeyshop.com/api/price_history_api.php?normalised_name=<id>&currency=eur&database=allkeyshop.com&v2=1`

Ne pas appeler les liens `/redirection/offer/...` : ils comptent des clics.

## samples/

| Fichier | Contenu |
|---|---|
| `home_staff.html` / `.headers` | Accueil, UA AKS/Staff |
| `home_chrome.html` / `.headers` | Accueil, UA Chrome (identique) |
| `home_topclickTrans.json` | JSON préchargé des top clics |
| `api_topclick_sidebar.json` | Réponse de l'API getLists (popular + soon) |
| `prod_popular1_ea-fc-27.html` + `_gamePageTrans.json` | Page produit n°1 Popular |
| `prod_soon1_minecraft-dungeons-2.html` + `_gamePageTrans.json` | Page produit n°1 Coming soon |
| `autoptimize.js`, `product_bundle.js` | Bundles JS du site (logique top clics et offres) |
