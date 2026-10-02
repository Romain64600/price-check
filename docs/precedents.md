# Registre des précédents

Chaque cas réel jugé, avec sa décision, sa preuve et la règle qui en découle. Deux sources : la **formation** (réponse de Romain à un report) et l'**étude** (analyse des reports par Claude, pages AllKeyShop et pages marchands à l'appui). Chaque précédent a son test dans `test_price_check.py` (classes `TestStudy20260930` et `TestConfirmOnMerchantPage`, et les tests nommés dans les tables).

**À tenir à jour** à chaque nouvelle formation : ajouter la ligne, la règle et le test.

## Principes (tirés des précédents)

1. **La page du marchand fait foi, l'URL n'est qu'un indice.** Wyrel met `-eu-` dans un slug d'offre Global ; Gamingdragons met `steam-key` dans l'URL d'une clé EA App. Quand l'URL contredit AllKeyShop sur la plateforme, la console ou la zone, le moniteur lit la page avant d'alerter. Une page muette ne contredit rien ; une page illisible laisse l'URL faire foi.
2. **On alerte quand le marchand vend moins, ou autre chose, que ce qu'affiche AllKeyShop** : un autre produit, une autre plateforme, une zone plus étroite (clé EU affichée GLOBAL), un DLC seul affiché comme édition du jeu, un compte affiché comme clé, une édition inférieure. **Pas quand il vend plus large** : une clé GLOBAL affichée EUROPE marche en Europe.
3. **Le vrai sens d'une région AllKeyShop est son nom de filtre**, pas son nom affiché : « GIFT » = `STEAM GIFT GLOBAL`, « GERMANY » peut être `STEAM GIFT GERMANY`, « GLOBAL » = `STEAM GLOBAL`, `EA GLOBAL` ou `ROCKSTAR GLOBAL` selon l'offre.
4. **Langue n'est pas région** : `IN ENGLISH ONLY`, `EN/FR`, les listes de langues des URL GAMIVO (`en-de-fr-ru-zh-es`) ne disent rien de la zone.
5. **Une édition est mal rangée quand la page AllKeyShop a l'édition que vend le marchand**, même si l'acheteur reçoit plus (GTA 4 : la page a « Complete », Steam vend la Complete Edition, l'offre est en Standard ; arbitrage du 01/10/2026). Sans cette édition sur la page, un nom différent n'est qu'une différence de nommage.
6. **La fiche que sert le marchand compte, pas l'ancien nom du lien** : quand Kinguin a remplacé la fiche d'un lien (URL canonique différente), c'est la région de la fiche servie qui se compare (Stellaris, arbitrage du 01/10/2026).
7. **Un gift n'a pas de zone comparée** (formation) : K4G « Steam Europe altergift » affiché `STEAM GIFT GLOBAL` est normal.
8. **Dans le doute, on alerte** en `À VÉRIFIER` (page illisible, titre traduit) : jamais de silence. La réponse de l'humain devient une règle, une config marchand ou un alias.
9. **Un problème vu dans l'URL suffit** (Romain, 02/10/2026) : quand l'URL finale du marchand (après son 301 : Instant Gaming redirige un slug périmé vers la fiche actuelle) nomme un autre produit, l'alerte part sans lire la page ; la page ne sert que si l'URL ne nomme rien. Idem pour une plateforme ou une région contredites : la page n'est consultée que pour écarter une URL trompeuse connue (Gamingdragons), jamais pour retenir l'alerte.
10. **Les noms raccourcis par les marchands sont une règle, pas un alias par produit** (Romain, 02/10/2026 : « on va avoir plein de marchands ») : préfixe de franchise facultatif, sigles, nombres collés, réparations de slug. `aliases.toml` ne garde que les vrais autres noms (titre traduit, titre de travail, formulation d'un marchand).
11. **Deux groupes de marchands pour les redirections** (Romain, 02/10/2026) : chez la plupart, une redirection mène à la fiche actuelle de la même offre (Instant Gaming, Fanatical) et c'est l'URL finale qu'on juge ; chez Kinguin, elle mène à une autre offre parce que la fiche du lien est en rupture : on ne s'y fie ni pour accuser ni pour blanchir, et la redirection constatée est elle-même signalée : la fiche du lien est en rupture, le prix reste dans le feed (« on aura quand même une alerte »). Détection : une requête sans suivre la redirection, à chaque offre du groupe, avec les en-têtes complets d'un navigateur (Akamai répond 403 sinon) ; un 301 vers une autre fiche = rupture (« on a juste besoin de suivre les redirections »). Config `[redirect] means`, un fichier par marchand ; d'autres rejoindront le groupe Kinguin au fil de l'apprentissage.
12. **La monnaie de jeu n'est pas le jeu** (Romain, 02/10/2026) : des COD Points, V-Bucks, Shark Cards sur la page d'un jeu sont une erreur, même quand l'URL contient le nom du jeu. Sauf si l'édition AllKeyShop est elle-même cette monnaie (« Standard + Great White Shark Card », « GTA 5 + Criminal + Megalodon »).
13. **Une offre signalée est suivie jusqu'à sa réparation** (Romain, 02/10/2026) : un passage lancé depuis l'admin recontrôle toutes les offres de ses pages, et les offres signalées sont recontrôlées toutes les heures ; une offre trouvée OK ou retirée de sa page est réparée, une offre OK trouvée fausse est une nouvelle alerte.

## Vraies erreurs (doivent alerter)

| Date | Jeu · édition | Marchand | Erreur | Preuve | Source |
|---|---|---|---|---|---|
| 30/09 | DREDGE · Premium | Greenmangaming | **Mauvais produit** : vend *DOOM: The Dark Ages Premium Edition* | URL et titre de la page | étude |
| 30/09 | TORO 2 Nintendo Switch · Standard | Nintendo eShop FR | **Mauvais produit** : la page est celle de *Metal Garden* | version en-GB de la page | étude |
| 30/09 | Crusader Kings 3 · Standard | Driffle | Clé **EU** affichée GLOBAL (10,97 €) | URL `crusader-kings-iii-eu-pc-steam-digital-code` ; page bloquée | étude ; **à discuter** le 01/10 (Romain voit « global » sur la page : erreur du marchand ou fiche remplacée comme chez Kinguin ?) |
| 30/09 | The Blood Of Dawnwalker · Deluxe | Eneba | Clé **EU** affichée `STEAM GLOBAL` | page : « Steam Key (PC) EUROPE · Can be activated in France » ; revue le 01/10 : seule offre de l'édition Deluxe, 71,21 €, région AllKeyShop `STEAM GLOBAL` | étude (Romain ne voyait pas le problème sur AllKeyShop : en attente) |
| 30/09 | F1 25 · 2026 Season Edition | GAMIVO | Version **Xbox Series** affichée `STEAM EU EN ONLY` | page : « … Xbox Series Key Europe » | étude |
| 30/09 | EA SPORTS FC 26 · ICONS Edition | Driffle | Clé **Steam** affichée `EA GLOBAL` (44,81 € contre 53,83 € pour l'offre suivante) | URL `…-global-pc-steam-digital-key` ; page bloquée | étude |
| 30/09 | Elden Ring Xbox Series · Launch Edition | Amazon.fr | Version **PlayStation** sur la page Xbox | URL `…-3391892017632-PlayStation` ; page Amazon illisible | étude ; **plus signalé depuis le 01/10** : Amazon ignoré (arbitrage) |
| 30/09 | Persona 5 Royal Nintendo Switch · Standard | Eneba | Offre saisie `STEAM EU` (plateforme Steam) sur la page Switch | page : « Persona 5 Royal Nintendo key » | étude (alerte du 30/09 au soir) |
| 30/09 | Splatoon Raiders Nintendo Switch 2 · Standard | Gamingdragons | Offre saisie **Xbox** (plateforme et région `XBOX X|S EUROPE`) sur la page Switch 2 | page : « … Switch 2 - Nintendo Switch eStore » | étude (rejeu complet) |
| 30/09 | The Witcher 3 Wild Hunt Xbox Series · Standard | Lootbar | Offre saisie `STEAM ROW` sur la page Xbox, le marchand vend une clé Xbox | URL `…/the-witcher-3-wild-hunt-xbox` ; page muette | étude (rejeu complet) |
| 30/09 | GTA 4 · Standard | Steam | **Complete Edition** rangée en Standard, la page a une édition Complete | titre Steam « Grand Theft Auto IV: The Complete Edition » | étude ; **arbitrage du 01/10** : vrai positif (« vraiment rangé en Standard alors qu'il devrait être en Complete ») |
| 30/09 | STAR WARS Zero Company Xbox Series · Standard + DLC | GAMIVO | **Deluxe + bonus** rangée en « Standard + DLC », la page a « Deluxe + Bonus » | URL `…-global-deluxe-pre-order-bonus` | étude ; **arbitrage du 01/10** : vrai positif (« mauvaise édition = erreur, même si l'acheteur reçoit plus ») |
| 01/10 | Call of Duty Modern Warfare 4 · Vault (3e prix) | Instant Gaming | Clé **Microsoft Store** (PC/Xbox) affichée Steam | URL après 301 `…-xbox-series-x-s-pc-microsoft-store`, page « PC & XBOX Series X\|S (Microsoft Store) » | Top Offers, premier jour |
| 01/10 | Monster Hunter Wilds · Deluxe (2e prix) | G2A | Clé **ROW** affichée EUROPE (`STEAM EU`) | URL `…-steam-key-row-…` | Top Offers |
| 01/10 | Forza Horizon 5 PS5 · Deluxe (2e prix) | Vidaplayer | **Standard** rangée en Deluxe (l'acheteur reçoit moins) | URL `…/forza-horizon-5-standard-edition` | Top Offers |
| 01/10 | Crusader Kings 3 · Starter Edition (2e prix) | Eneba | Clé **EU** affichée `IN ENGLISH ONLY` (clé mondiale selon AllKeyShop : « Global Key with english language available ») | URL `…-steam-key-europe`, page « Steam Key EUROPE » | Top Offers |
| 01/10 | TCG Card Shop Simulator Switch 2 · Standard (2e, 3e prix) | Nintendo eShop IT, ES | **Mauvais produit** : la fiche est *Horse Spirit Valley 2* (3173803) | version en-GB de la fiche | Top Offers |
| 01/10 | Assassin's Creed Black Flag Resynced · Deluxe (3e prix) | Royal CD Keys | **Altergift** (cadeau Steam) affiché en clé `STEAM EU` | URL `…-eu-pc-steam-altergift` | Top Offers |
| 01/10 | Warhammer 40k Space Marine 2 · Gold (3e prix) | Steam | Paquet « 1-Year Anniversary Edition » rangé en **Gold**, la page a cette édition | page Steam `/sub/997629` | Top Offers (arbitrage édition du 01/10) |
| 01/10 | Hunt Showdown · Standard (3e prix) | GAMESEAL | Clé **EMEA** affichée `STEAM ROW` | URL `…-steam-key-emea`, page « REGION EMEA » | Top Offers |
| 02/10 | Titanfall 2 · Deluxe (1er prix) | Kinguin | **Mauvais produit** : le premier *Titanfall* (« Titanfall Deluxe Edition EN Language Only ») | URL `…/25568/titanfall-deluxe-edition-…`, page | Top Offers ; **décision admin « Vrai positif »** (première décision prise dans la page Price check) |
| 02/10 | Call of Duty Black Ops 3 · Limited (2e prix) | GAMIVO | **Gift Europe** affiché `STEAM ROW` | URL `…-zombies-chronicles-edition-eu`, page « Steam Gift Europe » | Top Offers |
| 02/10 | Dying Light The Beast · Standard (2e prix) | GameBoost | Clé **ROW** affichée `STEAM GLOBAL` | URL `…-steam-key-row-…`, page | Top Offers |

## Faux positifs corrigés

| Date | Jeu · édition | Marchand | Motif du faux positif | Règle | Source |
|---|---|---|---|---|---|
| 30/09 | EA SPORTS FC 27 · Standard | Mmoga | `IN ENGLISH ONLY` pris pour une région | langue ≠ région | **formation** |
| 30/09 | GTA 5 · Standard + DLC | Wyrel | slug `-eu-`, page Global | région Wyrel lue dans `region=` (`merchants/wyrel.toml`) | **formation** |
| 30/09 | Screamer 2026 · Deluxe | K4G | « Steam Europe altergift » affiché `STEAM GIFT GLOBAL` | zone d'un gift non comparée | **formation** |
| 30/09 | Diablo 4 Lord of Hatred Xbox Series · Ultimate | Driffle | `dlc` dans l'URL d'un DLC | page DLC (édition « DLC » sur la page) | **formation** |
| 30/09 | Big Walk · Standard | Kinguin | « GERMANY » cru clé, c'est `STEAM GIFT GERMANY` | nom de filtre de la région | étude |
| 30/09 | Mario Kart World Switch 2 · Standard | K4G | clé GLOBAL affichée EUROPE | zone comparée dans un seul sens | étude |
| 30/09 | EA SPORTS FC 26 · Ultimate | Gamingdragons | URL `steam-key`, page « PC - EA App Download » | confirmation sur la page | étude |
| 30/09 | Gran Turismo 7 PS5 · Deluxe | PS Store ES | « ™ » transformé en « TM » par la normalisation | symboles de marque retirés | étude |
| 30/09 | Dragon Quest Monsters PS5 · Deluxe | PS Store ES | titre espagnol « El reino marchito » | PS Store lu en en-gb (`merchants/playstation.toml`) | étude |
| 01/10 | Ni no Kuni Wrath of the White Witch Remastered Switch · Standard (1er, 2e et 3e prix) | Nintendo eShop IT, ES, DE | liens sur `nintendo.es` / `nintendo.de` / `nintendo.it` (308 vers `nintendo.com`) : la config Nintendo ne s'appliquait pas, les titres traduits (« La ira de la bruja blanca », « Der Fluch der weißen Königin ») sortaient en « autre produit » | `merchants/nintendo.toml` : anciens domaines et `name_prefixes = ["nintendo eshop"]` ; nom contrôlé sur la version en-GB (`test_nintendo_old_domains_are_read_in_english`) | étude (Top Offers) |
| 30/09 | Attack on Titan 3 PS5 · Deluxe, Preorder | PS Store UK | titre officiel « A.O.T. 3 » | sigles des premiers mots | étude |
| 01/10 | Onimusha Way of the Sword PS5 · Deluxe, Premium Deluxe (2e et 3e prix, 4 alertes) | PS Store UK, FR | titre officiel « Onimusha: WotS » | sigle de tous les mots après le premier, jamais sur un mot distinctif (`test_names_merchants_shorten_20261001`) | étude (premier jour de Top Offers) |
| 01/10 | EA Sports UFC 5 Xbox Series · Standard (2e et 3e prix) | Eneba, GAMIVO | « Buy UFC® 5 Xbox key! », « Buy UFC 5 Xbox Series Key Europe », URL Eneba `ufc-r-5` | préfixe d'éditeur « EA Sports » facultatif (`OPTIONAL_PREFIXES`) ; rejeu : DREDGE/DOOM, TORO 2/Metal Garden, Transport Fever 3/Nocturne restent signalés | étude (premier jour de Top Offers) |
| 30/09 | Crimson Desert PS5 · Standard | PS Store US | produit nommé « Crimson Desert Enhanced », édition « Standard Edition » | libellé d'édition du JSON PS Store | étude |
| 30/09 | EA Sports UFC 5 PS5 | PS Store US | titre court « UFC® 5 » | titre court contenu dans le nom | étude |
| 30/09 | Pokemon Legends Z-A Mega Dimension · DLC | Dreamgame | « Pokémon » écrit `pokmon` | mot à une lettre près (6 lettres et plus) | étude |
| 30/09 | Rhythm Heaven Groove Switch 2 | Loaded | titre européen « Rhythm Paradise » | `aliases.toml` | étude |
| 02/10 | World of Warcraft: Forever · Heroic Pack (3e prix) | Driffle | URL `warcraft-forever-skyborne-heroic-pack-…` sans « World of », page bloquée : noté NON VÉRIFIABLE avec le message faux « URL sans nom du produit » | préfixe « World of » facultatif (`OPTIONAL_PREFIXES`, d'abord un alias) ; le message dit ce que nomme l'URL (`unverified_reason`) | **décision admin** « À discuter » (« Heroic pack est une édition pour ce DLC, battlenet Gift est correct ») |
| 02/10 | Pokemon Pokopia Switch 2 (Eneba `pokemontm-pokopia`), S.T.A.L.K.E.R. 2 (GAMIVO `s-t-a-l-k-e-r-2`), Kingdom Hearts HD 1.5+2.5 (Kinguin `1-5-2-5`), Zelda Tears of the Kingdom (Instant Gaming `breath-of-the-wild-2`, titre de travail) | divers | OK jusqu'ici grâce à la page ; avec l'alerte immédiate sur l'URL, ils seraient devenus de fausses alertes | réparations de slug (`repair_slug`), nombres décimaux collés, alias Zelda (`test_slug_repairs_and_shortened_names_20261002`) | rejeu des 3 367 offres avant déploiement |
| 02/10 | GTA 5 · GTA 5 + Criminal + Megalodon / Whale / Great White (7 offres) | Kinguin, GAMIVO | règle « monnaie de jeu » en cours d'écriture : `shark-card` dans l'URL d'éditions qui SONT la carte, nommée par son requin | les éditions-monnaie désamorcent la règle (`CURRENCY_EDITION_PHRASES`, `test_currency_that_is_expected`) ; rejeu : 0 alerte sur les 3 595 offres en mémoire | rejeu avant déploiement |
| 02/10 | 22 fiches Kinguin renommées (Dayz, Stardew Valley, Stellaris, RDR2, Hearts of Iron 4… : « -steam- » → « -pc-steam- », « 2021 », « the ») et Crimson Desert ×2 (« Crimson Desert Enhanced ») | Kinguin | la sonde de redirection, écrite d'abord pour Stellaris, les aurait prises pour des ruptures | une redirection n'est une rupture que si la fiche servie dit autre chose de l'offre : nom, région, plateforme, édition (`offer_signature`) ; « Crimson Desert Enhanced » est le nom du jeu (`aliases.toml`). Restent 3 ruptures réelles : Ace Combat 8 (autre fiche), Euro Truck Simulator 2 (gift devenu EU), Rust (« eu » → « de ») | étude des 246 offres Kinguin en mémoire |
| 01/10 | Mario Kart 8 Deluxe Booster Course Pass Switch · DLC (3e prix) | K4G | « Booster Courses Pack » ; page : « This is a DLC and it requires the base game… Booster Course Pass » | `aliases.toml` ; le jeu de base vendu sur la page du DLC reste signalé (`test_booster_courses_pack_alias`) | étude (Top Offers) |
| 30/09 | Kirby, Pokémon Sword, Brilliant Diamond (Switch) | Amazon.fr | titres français | `aliases.toml` ; titre traduit non reconnu → À VÉRIFIER | étude |
| 30/09 | Minecraft Dungeons 2 | GameBoost, YUPLAY | « Dungeons II » | chiffres romains | étude |
| 30/09 | 9 offres Xbox | Instant Gaming, Playerland | `microsoft-store` dans l'URL | Microsoft Store = Xbox | étude |
| 30/09 | NBA 2K26 Xbox Series | Eneba | préfixe `steam-` des URL Eneba | plateformes comparées sur tous les mots | étude |
| 30/09 | Screamer 2026, Ragnarock VR, Metro Awakening VR, … Switch 2 | divers | suffixes « 2026 », « VR », « Nintendo Switch 2 » | suffixes et année retirés du nom | étude |
| 30/09 | S.T.A.L.K.E.R. 2 | G2A | sigle pointé | `S.T.A.L.K.E.R.` = `stalker` | étude |
| 30/09 | Age of Wonders 4, Crusader Kings 3, RimWorld, ETS2, Stellaris | GAMIVO | listes de langues (`ru`, `tr`) | listes de langues ignorées | étude |
| 30/09 | EA FC 27, Ace Combat 8, Assetto Corsa… | Driffle, Kinguin | `pre-order-bonus-dlc`, pack DLC rangé en « Bonus » | édition qui annonce du contenu | étude |
| 30/09 | Call of Duty MW4 · Preorder bonus | Dreamgame | `standard-edition-pre-purchase` | édition de base + « standard » | étude |
| 30/09 | ETS2, Stellaris, RimWorld (bundles) | divers | noms de bundles | éditions génériques non comparées ; mot propre à l'édition (`2024`, `mediterranean`, `starter`) | étude |
| 30/09 | Hunt Showdown · Standard + DLC Bundle | Kinguin | `10-dlc-bundle` | page : « Hunt: Showdown 1896 +10 DLC Bundle » (jeu + DLC) | étude |
| 01/10 | GTA 5 · GTA 5 + Criminal (3e prix, 11,09 €) | Keycense | URL `…-criminal-enterprise-starter-pack-dlc-rockstar` : le « + » du titre a disparu ; la page vend « Grand Theft Auto V + Criminal Enterprise Starter Pack DLC », configuration requise du jeu, prix des autres offres de l'édition (9,97 à 11,86 €) | sur une édition « X + Y », le mot DLC de l'URL se vérifie sur la page : le jeu avant un « + » du titre contredit, un DLC seul confirme (`test_game_plus_content_edition_with_dlc_in_the_url`) | étude (alerte du 01/10 15:07, premier jour de Top Offers) |

| 30/09 | Marvel's Spider-Man 2 · Deluxe | K4G | slug `…-playstation-5-europe-cd-key`, page « Deluxe Edition Steam CD Key », champs `PLATFORM Steam · REGION Global` | champs Région / Plateforme du corps de la page lus pour la confirmation ; une page qui nomme plusieurs zones ne contredit rien | question de Romain (« c'est toi qui m'as reporté… ? ») |
| 01/10 | GTA 4 · Standard | Steam | Complete Edition (seule édition vendue par Steam) rangée en Standard | ~~une édition supérieure vendue sous une édition de base n'alerte plus~~ : **annulé l'après-midi**, GTA 4 est un vrai positif (voir les arbitrages du 01/10) | formation du matin, annulée |
| 01/10 | Stellaris · Bundle 1 | Kinguin | lien `…/172478/stellaris-starter-pack-eu-steam-cd-key`, offre `STEAM GLOBAL` : Kinguin sert la fiche globale (URL canonique `…/172478/stellaris-starter-pack-bundle-2023-pc-steam-cd-key`) | `merchants/kinguin.toml` : la région n'est plus reprochée, mais l'offre est signalée « en rupture chez le marchand, le prix reste dans le feed » depuis le 02/10 (`TestConfirmOnMerchantPage.test_kinguin_serves_another_page_than_the_link`) | **arbitrage** (« C'est bien EU même si maintenant ça redirige sur l'offre globale ») ; signalé vrai le 30/09 sur une formation mal lue (« celui-ci est OK ») |
| 01/10 | Farming Simulator 25 · Year 1 Edition | Loaded | URL `…-year-1-season-pass-pc-steam` | « Year N Season Pass » = l'édition « Year N » (`test_year_one_season_pass_is_the_year_one_edition`) | **arbitrage** (« le jeu est bien inclus, l'offre est bien rentrée ») |
| 01/10 | Forza Horizon 6 Premium Upgrade Bundle Xbox · Upgrade | LDShop | page multi-produits, titre du jeu de base ; l'option cochée par le lien `skuId=16560` est « Forza Horizon 6 Premium Upgrade (Global) » | `merchants/ldshop.toml` : lecture de l'option choisie (`aria-checked`) qui reprend les mots du titre | étude (formation « à discuter ») |
| 30/09 | Call of Duty Black Ops 6 · Standard | Eneba | URL `steam-…-steam-key`, page « (PC) **Windows Store** Key » = région `WINDOWS EU` | confirmation sur la page (faux positif évité au rejeu) | étude |
| 30/09 | STAR WARS Galactic Racer, KCD2, Castlevania | GAMESEAL | `…-steam-key-eu-na` pris pour EU seul, la région est EU/US | `eu-na` = EU/US | étude (rejeu complet) |
| 30/09 | Euro Truck Simulator 2 · Standard + DLC | GameBoost | « Vive la **France** » (un DLC) pris pour une zone | pas de noms de pays comme mots de zone | étude (rejeu complet) |
| 30/09 | Fable Premium Upgrade Bundle Xbox · DLC | Eneba | « bundle » absent de l'URL | « bundle » facultatif dans un nom | étude (rejeu complet) |

## Lisibilité des alertes

| Date | Cas | Règle |
|---|---|---|
| 01/10 | TORO 2 : l'alerte disait « nom du produit absent » alors que l'URL nomme *Metal Garden* (formation : « tu te fous de ma gueule… alors que tu l'as ») | l'alerte dit ce que vend le marchand : « autre produit chez le marchand : « Metal Garden » au lieu de « TORO 2 » » ; « nom du produit introuvable » seulement quand le texte ne nomme rien |

## Faux négatifs évités

| Date | Cas | Règle |
|---|---|---|
| 30/09 | « Pokémon **Bouclier** » passait pour « Pokemon **Sword** Nintendo Switch » : la tolérance d'un mot manquant comptait « Nintendo Switch » | tolérance calculée sur le nom sans suffixe de plateforme |
| 30/09 | Elden Ring Xbox chez Amazon : nom illisible, donc « À VÉRIFIER » sans mentionner la PlayStation | console de la page comparée à l'URL ; le problème vu dans l'URL reste une alerte même si le nom est invérifiable |

| 30/09 | Forza Horizon 6 **Premium Upgrade Bundle** : le titre du jeu de base passait avec la tolérance d'un mot manquant (« upgrade ») | les mots distinctifs (upgrade, DLC, season pass, VR…) ne sont jamais le mot toléré |

Rejeu complet du 30/09/2026 : les 920 offres en tête des 415 pages, repassées dans les nouvelles règles avec leur URL marchand. 12 anciens OK changeaient : 3 vraies erreurs nouvelles (Splatoon Raiders, The Witcher 3 Xbox, Call of Duty Black Ops 6 — ce dernier contredit ensuite par la page), 5 régressions corrigées (ci-dessus), 4 cas normaux (eShop localisés, Amazon : lecture de page).

Revue des 202 offres jugées OK sur une base fragile (nom partiel, titre court, nom non contrôlé) : aucune erreur manquée. Vérifiés un par un : GTA The Trilogy (Switch et Xbox, chacune sur sa page), Among Us VR (Loaded `among-us-3d-vr` : le jeu s'appelle désormais « Among Us 3D: VR » sur Steam, prix au niveau des autres offres).

## Ce qui est parti sur Discord le 30/09/2026 : bilan

35 reports (31 automatiques, 4 envoyés à la main) :

- **11 vraies erreurs** : DREDGE (DOOM), TORO 2 (Metal Garden), Stellaris, The Blood Of Dawnwalker, Crusader Kings 3 (clés EU affichées GLOBAL), F1 25 (Xbox affichée Steam), EA SPORTS FC 26 ICONS (Steam affichée EA App), Farming Simulator 25 (season pass seul), Elden Ring Xbox (version PlayStation), GTA 4 et STAR WARS Zero Company (éditions mal rangées, gravité faible).
- **1 incertain** : Forza Horizon 6 Premium Upgrade Bundle chez LDShop.
- **23 faux positifs**, tous corrigés par une règle ci-dessus : Minecraft Dungeons 2 ×2, Screamer 2026 (K4G), GTA 5 (Wyrel), Diablo 4 (Driffle), Big Walk, EA SPORTS FC 26 Ultimate (Gamingdragons), Crimson Desert, Gran Turismo 7, Dragon Quest Monsters, Attack on Titan 3 ×2, Mario Kart World (K4G), et 10 À VÉRIFIER (9 Amazon, Rhythm Heaven chez Loaded).

Rejoués avec les règles du soir, les 11 vraies erreurs sortent toujours et les 23 faux positifs ne sortent plus. Deux nouvelles vraies erreurs trouvées au rejeu (Splatoon Raiders, The Witcher 3 Xbox) ont été envoyées.

## Offres non vérifiables (formation du 30/09/2026)

« Quand on n'a ni dans l'URL ni sur la page, tu prends note » : une offre invérifiable est notée `NON VÉRIFIABLE` (journal, `state.json`, `python3 price_check.py --unverified`), **sans alerte**, sauf si c'est le premier prix de toute la page sur une page d'un top ou d'un coming soon (listes Popular, Coming soon, Most anticipated) : là, À VÉRIFIER. Depuis le 01/10/2026, une offre notée qui devient plus tard ce premier prix passe À VÉRIFIER et part sur Discord.

« Vraiment un premier prix » (question du 30/09, réponse de Romain du 01/10) : deux modes d'offres. `top-offers` (défaut) contrôle les 3 premiers prix de chaque édition et la règle ci-dessus s'applique dans ce périmètre ; `full-page` contrôle toutes les offres de la page. Voir le README.

Amazon : jusqu'au 01/10/2026, jamais d'alerte pour une offre invérifiable (« ne t'embête pas avec Amazon »). Depuis l'arbitrage du 01/10, **Amazon est ignoré** (« on skip tous les Amazon jusqu'à modifier notre façon de requêter leurs pages ») : plus de contrôle ni de report, Elden Ring compris. Liste à jour : voir [marchands.md](marchands.md#offres-non-vérifiables).

## Arbitrages du 01/10/2026 (doc partagé « reports du 30/09 »)

| Cas | Décision de Romain | Effet dans le moniteur |
|---|---|---|
| GTA 4 (Steam, offre 80523) · STAR WARS Zero Company Xbox Series (GAMIVO, offre 140387715) | vrais positifs : « mauvaise édition = erreur à reporter, même si l'acheteur reçoit plus » | règle du matin annulée, retour à la règle « la page a l'édition vendue » ; les deux offres ressortent en SUSPECT |
| Elden Ring Xbox Series (Amazon.fr) et les 6 offres non vérifiables Amazon | « on skip tous les Amazon jusqu'à modifier notre façon de requêter leurs pages » | `merchants/amazon.toml` : `skip = true` |
| Stellaris Bundle 1 (Kinguin, offre 135046199) | faux positif : Kinguin redirige le lien EU vers l'offre globale ; le marchand doit passer l'offre en rupture | `merchants/kinguin.toml` : la fiche canonique fait foi pour la région |
| Farming Simulator 25 Year 1 Edition (Loaded, offre 139028218) | faux positif : « le jeu est bien inclus » | « Year N Season Pass » = édition « Year N » |
| Euro Truck Simulator 2 · Collection Bundle (Driffle) | faux positif | éditions génériques non comparées (déjà en place) |
| Forza Horizon 6 Premium Upgrade (LDShop) | faux positif, réglé par la config LDShop du matin | aucun changement |
| Crusader Kings 3 (Driffle, offre 135593568) | à discuter : « -eu- dans l'URL mais global sur la page. Erreur marchand ou redirection comme Kinguin ? » | non appliqué ; Driffle bloque le moniteur (HTTP 403, Chromium « Blocked - Driffle ») : impossible de voir sa fiche canonique d'ici |
| The Blood Of Dawnwalker (Eneba, offre 140458058) | pas de décision (« je ne vois pas le pb sur AKS, peut-être déjà fixé ») | non appliqué ; revu le 01/10 : toujours `STEAM GLOBAL` sur AllKeyShop (seule offre Deluxe, 71,21 €), page Eneba « EUROPE · Can be activated in France » |

## Cas à trancher

| Cas | Question |
|---|---|
| Crusader Kings 3 · Standard, Driffle | Fiche « -eu- » qui dit Global : erreur de nommage du marchand, ou fiche remplacée comme chez Kinguin ? (Driffle bloque le moniteur) |
| The Blood Of Dawnwalker · Deluxe, Eneba | Toujours `STEAM GLOBAL` sur AllKeyShop pour une clé EUROPE : à revoir par Romain avec l'offre exacte (Deluxe, 71,21 €) |

## Ce que l'étude a appris sur AllKeyShop

- **Régions** : chaque région a un nom affiché (`region_name`), un nom de filtre (`filter_name`) et une description. Le nom de filtre porte la plateforme et la zone : `STEAM EU`, `EA EUROPE`, `ROCKSTAR ROW`, `XBOX X|S EUROPE`, `STEAM GIFT GERMANY`, `PSN WALLET DE`… 81 régions distinctes relevées sur 63 pages.
- **Éditions** : chaque page a ses propres éditions, parfois très nombreuses (22 sur Euro Truck Simulator 2, 20 sur Stellaris). Les DLC peuvent être rangés comme une édition du jeu (« Bonus » sur Assetto Corsa Competizione).
- **Pages DLC sur console** : les listes et CatalogV2 les typent `game` ; l'édition « DLC » sur la page les trahit.
- **Sites sœurs** (goclecd.fr, keyforsteam.de, cdkeyit.it, clavecd.es…) : les slugs sont en anglais, ils ne donnent pas les titres localisés. Hypothèse vérifiée et réfutée le 30/09/2026.
- **Pages marchands** : PS Store, Steam, Gamingdragons, Instant Gaming, Eneba, Nintendo se lisent en HTTP simple ; Kinguin, GAMIVO, LDShop demandent Chromium ; Driffle et Loaded bloquent même Chromium ; Amazon renvoie un captcha, parfois la page.
