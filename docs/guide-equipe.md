# Price check — guide de l'équipe

État au 03/10/2026. Pour l'équipe, ce guide est une page de l'admin, « 📘 Guide équipe » sur la page Price check
(`/executor/price-check-guide`), générée depuis ce fichier par `tools/guide_html.py`. English version:
[team-guide.md](team-guide.md).

## À quoi sert le price check

Le price check vérifie en continu que les premiers prix des pages produit les plus vues d'AllKeyShop vendent bien ce que la page affiche, et alerte sur Discord dès qu'une offre ne correspond pas.

- **Les pages suivies** : les tops (5 premiers Popular, 4 premiers Coming soon PC), toutes les 2 min 30 ; toute la homepage (environ 430 pages : widgets de la home et TOP 50 de chaque plateforme), toutes les 15 min.
- **Les offres contrôlées** : sur chaque page, les 3 offres de clé les moins chères de chaque édition. Ce sont les « premiers prix ».
- **Le contrôle** : le moniteur suit le lien de chaque offre jusqu'au marchand, lit l'URL (et la page si besoin), puis compare avec le produit, l'édition, la région et la plateforme affichés par AllKeyShop.

| Erreur cherchée | Exemple réel |
| --- | --- |
| Mauvais produit | Titanfall 1 vendu sur la page de Titanfall 2 (Kinguin) |
| Région plus étroite | Clé ROW affichée EUROPE (Monster Hunter Wilds, G2A) |
| Autre plateforme | Clé Microsoft Store affichée Steam (Call of Duty MW4, Instant Gaming) |
| Mauvaise édition | Complete Edition rangée en Standard (GTA 4, Steam) |
| Gift ou compte vendu comme une clé | Steam altergift affiché en clé EU (AC Black Flag Resynced, Royal CD Keys) |
| DLC ou monnaie de jeu vendus comme le jeu | COD Points sur la page du jeu |
| Rupture chez le marchand | Kinguin redirige le lien vers une autre fiche, mais le prix reste dans le feed (Stellaris) |

## Les trois salons Discord

Chaque alerte part dans un seul salon : les urgences d'abord, puis les tops, puis la homepage.

| Salon | Ce qui y arrive | Priorité |
| --- | --- | --- |
| #aks_price_emergencies | Les urgences premiers prix : un problème avéré (SUSPECT) sur l'une des 3 offres les moins chères d'une édition, que la page soit dans les tops ou dans la homepage. L'en-tête dit d'où vient l'alerte. | À traiter en premier |
| #aks_price_checker | Les autres alertes des tops (À VÉRIFIER, offres plus bas dans l'édition) et les récapitulatifs de recontrôle des tops. C'est aussi le salon du bot. | Ensuite |
| #aks_top_price_checker | Les autres alertes de la homepage et leurs récapitulatifs. | Ensuite |

Un ancien report renvoyé dans son bon salon porte la mention « Report existant (signalé le …) » : ce n'est pas une nouvelle détection.

## Lire une alerte

Une alerte dit quelle offre est en cause, où elle s'affiche, et pourquoi le moniteur la croit fausse. Exemple réel, reçu dans #aks_price_emergencies le 03/10 :

```
🚨 URGENCE PREMIER PRIX · Price check homepage
📌 Report existant (signalé le 2026-10-01 14:58), renvoyé dans le salon des urgences premiers prix
🔴 SUSPECT · Monster Hunter Wilds (Home · RPG #8) · Deluxe · 2e prix de l'édition
G2A · EUROPE (STEAM EU) · steam · 44.10 € · offre 136209040 · contrôle : URL
Raison : région : AllKeyShop EUROPE, marchand ROW
Marchand : <lien de l'offre chez G2A>
Page : <lien de la page AllKeyShop>
```

| Ligne | Ce qu'elle dit |
| --- | --- |
| URGENCE PREMIER PRIX | Un problème avéré sur l'un des 3 premiers prix de l'édition, et le mode qui l'a trouvé (top ou homepage) |
| Report existant | Une ancienne alerte, renvoyée une seule fois dans son bon salon (absente d'une alerte neuve) |
| Verdict · jeu (liste #rang) · édition · rang | Le verdict, la page, la liste où elle figure, l'édition où l'offre est rangée et son rang dans cette édition |
| Marchand · région · plateforme · prix | Ce qu'affiche AllKeyShop : la région avec son nom de filtre entre parenthèses (le vrai sens de la région), le prix frais carte compris, l'id de l'offre, et comment le moniteur a contrôlé (URL, page) |
| Raison | Ce qui ne va pas : ici, AllKeyShop affiche une clé EUROPE, le marchand vend une clé ROW (reste du monde, sans l'Europe) |
| Note | Quand il y en a une : ce que la page du marchand a confirmé ou contredit |
| Marchand, Page | Les deux liens pour vérifier |

Les verdicts :

- **SUSPECT** : un problème est trouvé, l'alerte part.
- **À VÉRIFIER** : impossible de conclure (page du marchand illisible), sur le premier prix d'une page des tops ou d'un coming soon. Un humain vérifie.
- **NON VÉRIFIABLE** : le même cas ailleurs. Noté dans l'admin, sans alerte.

Les messages « Recontrôle … » sont des bilans : offres toujours en erreur, réparées, faux positifs levés par une règle.

## Donner son feedback dans le fil de l'alerte

Chaque alerte a son fil « Feedback · jeu · offre id » : on y tranche en une ligne, et la décision arrive aussitôt dans l'admin.

1. Ouvrir le fil sous l'alerte.
2. Répondre en commençant par l'un de ces mots :
    - `vrai` (ou `vp`, ✅) : l'erreur est réelle.
    - `faux` (ou `fp`, ❌) : l'offre est correcte, l'alerte n'aurait pas dû partir.
    - `à discuter` (ou 💬) : on en parle avant de trancher.
3. Ajouter après le mot une note qui dit pourquoi, par exemple `faux : la page AllKeyShop est bien un DLC`.
4. Le bot confirme dans le fil : « Décision enregistrée : Faux positif — par … ».

- **Qui peut trancher** : les personnes autorisées sur le bot. Romain les ajoute avec `!allow @nom` dans #aks_price_checker. Les autres reçoivent un rappel, et leur message reste dans le fil.
- **Discuter sans trancher** : un message qui ne commence pas par l'un de ces mots ne décide rien.
- **Ce que fait un « faux »** : l'offre n'est plus recontrôlée ni alertée. La note sert à corriger les règles du moniteur, pour tous les marchands.
- **Ce que le fil reçoit ensuite** : les suites de l'offre (réparée, faux positif levé par une règle, de nouveau en erreur) et les décisions prises dans l'admin.

## Que faire face à une alerte

Vérifier sur les deux pages, trancher dans le fil, puis faire corriger l'offre si l'erreur est réelle.

1. **Les urgences d'abord** (#aks_price_emergencies) : un premier prix faux, c'est ce que voient les visiteurs.
2. **Sur la page AllKeyShop** (lien « Page ») : l'édition où l'offre est rangée, les autres éditions de la page, le nom de filtre de la région (STEAM EU, STEAM GLOBAL, XBOX X|S EUROPE…), la plateforme.
3. **Chez le marchand** (lien « Marchand ») : le produit, l'édition, la région et la plateforme réellement vendus.
4. **Trancher dans le fil** : vrai, faux ou à discuter, avec une note.
5. **Si c'est vrai** : faire corriger l'offre sur AllKeyShop (édition, région, plateforme, rattachement à la page) ou la faire retirer ; pour une rupture chez Kinguin, c'est au marchand de sortir l'offre de son feed. Au recontrôle suivant (moins d'une heure), le moniteur classe l'offre « réparée » et l'écrit dans le fil.

Ce qui n'est pas une erreur :

- une clé GLOBAL affichée EUROPE : le marchand vend plus large que ce qui est affiché ;
- une restriction de langue (IN ENGLISH ONLY, EN/FR) : ce n'est pas une région ;
- la zone d'un gift : elle n'est pas comparée.

## L'admin Price check

L'admin montre tous les reports au même endroit, avec les mêmes décisions que les fils Discord : <https://169.58.5.63.sslip.io/executor/price-check> (identifiant de l'admin).

- **Une carte par report** : le verdict, les pastilles TOP ou HOMEPAGE (d'où vient le problème) et PREMIER PRIX (l'une des 3 offres les moins chères de l'édition), le jeu, l'édition, le rang, le marchand, le prix, la raison, et trois liens : page AllKeyShop, offre chez le marchand, fil Discord.
- **Trancher** : les boutons Vrai positif, Faux positif, À discuter, avec une note. Même effet qu'une réponse dans le fil ; une décision prise sur Discord s'affiche signée « (Discord) ».
- **Filtres** : verdict (dont Réparées, Faux positifs levés par une règle, Vérifiées OK), mode (Price check top ou homepage), décision, recherche libre, « encore en tête seulement », « premiers prix seulement ».
- **Compteurs** : sans décision, tops à trancher, homepage à trancher, premiers prix en erreur, réparées.
- **Lancer un passage** : les boutons « Lancer le price check top » et « Lancer le price check homepage » recontrôlent tout de suite toutes les offres de leurs pages. Compter quelques minutes pour les tops, environ 2 h 30 pour la homepage.

## Ce que le moniteur fait tout seul

Une offre signalée est recontrôlée toutes les heures jusqu'à sa réparation, et chaque suite s'écrit dans son fil.

```mermaid
flowchart LR
    D["Détection<br/>tops : 2 min 30<br/>homepage : 15 min"] --> A["Alerte<br/>urgences, tops<br/>ou homepage"]
    A --> F["Fil de feedback<br/>ouvert par le bot"]
    F --> C["Décision<br/>vrai, faux ou<br/>à discuter + note"]
    A -- offre signalée --> R["Recontrôle<br/>toutes les heures<br/>tant qu'en erreur"]
    R -- OK --> P["Réparée<br/>l'offre a changé<br/>ou quitté la page"]
    P -- écrite dans le fil --> F
    C -- faux --> X["Faux positif<br/>plus recontrôlée,<br/>la règle corrigée"]
```

| Résultat du recontrôle | Ce que ça veut dire |
| --- | --- |
| Réparée | L'offre a changé (URL, région, plateforme, édition) ou a quitté la page |
| Faux positif levé par une règle | Rien n'a changé : une règle ajoutée depuis la blanchit |
| Vérifiée OK | Elle n'avait pas pu être vérifiée, elle l'est maintenant |
| Toujours en erreur | Rien n'a bougé : recontrôlée l'heure suivante |
| De nouveau en erreur | Une offre OK devenue fausse : nouvelle alerte |

Une offre jugée faux positif n'est plus recontrôlée. Les boutons de l'admin lancent un recontrôle complet sans attendre l'heure.

## Cas déjà jugés, pour se caler

Ces décisions font jurisprudence : le moniteur a déjà été corrigé pour les faux positifs, et il alerte toujours sur les vraies erreurs. Le registre complet est dans [precedents.md](precedents.md).

| Cas | Décision | Pourquoi |
| --- | --- | --- |
| Titanfall 2 Deluxe, Kinguin vend le premier Titanfall | Vrai positif | Un autre jeu de la série n'est jamais le jeu |
| Monster Hunter Wilds Deluxe, G2A vend une clé ROW affichée EUROPE | Vrai positif | ROW (reste du monde) ne couvre pas l'Europe |
| GTA 4, la Complete Edition de Steam rangée en Standard | Vrai positif | Mauvaise édition, même si l'acheteur reçoit plus : la page a une édition Complete |
| STAR WARS Zero Company Xbox, une Deluxe rangée en « Standard + DLC » | Vrai positif | La page a une édition Deluxe |
| Stellaris Bundle 1, Kinguin redirige le lien vers une autre fiche | Rupture à signaler | La fiche du lien est en rupture, le prix reste dans le feed |
| Pokémon Scarlet, DLC « The Hidden Treasure of Area Zero », GameBoost vend la version Violet | Faux positif | Cette page couvre le DLC des deux versions ; cas propre à Pokémon, sans généralisation |
| Minecraft Dungeons Triple Bundle, CJS CDKeys « région Argentine » | Faux positif | Le lien choisit la variante Europe ; la page montre l'Argentine par défaut |
| Mario Kart World, K4G vend une clé GLOBAL affichée EUROPE | Faux positif | Le marchand vend plus large que l'affichage |
| EA SPORTS FC 27, Mmoga « IN ENGLISH ONLY » | Faux positif | Une langue n'est pas une région |
| Farming Simulator 25 Year 1 Edition, Loaded vend le Year 1 Season Pass | Faux positif | Le jeu est inclus dans ce pass |

## Questions fréquentes et contacts

- **Le bot ne prend pas ma décision.** Il faut être autorisé : demander à Romain un `!allow @vous`. Vérifier aussi que le message commence par `vrai`, `faux` ou `à discuter`.
- **Je ne sais pas trancher.** Répondre `à discuter`, avec ce que vous voyez sur les deux pages.
- **L'offre a été corrigée.** Rien à faire : le recontrôle horaire la classe « réparée » et l'écrit dans son fil.
- **Une alerte revient alors qu'elle était réglée.** L'offre est redevenue fausse (nouvelle saisie, fiche du marchand changée) : c'est une nouvelle alerte, à trancher comme les autres.
- **Je veux vérifier une offre tout de suite.** Le bouton « Lancer le price check top » de l'admin recontrôle les tops en quelques minutes.
- **Parler au bot.** Dans #aks_price_checker, en le mentionnant (personnes autorisées seulement) ; `!help` donne les commandes. Attention : une personne autorisée a aussi accès à Claude sur le serveur du moniteur.
- **Contact** : Romain, pour les droits, les règles et tout cas qui ne rentre dans aucune case.
