# Bot Discord : parler à Claude Code depuis le salon des alertes

Le bot lit les messages du salon où arrivent les alertes du moniteur, les transmet à Claude Code
(`claude -p`, sur ce serveur, dans le dépôt) et renvoie la réponse dans le salon. La conversation
est reprise d'un message à l'autre (session Claude Code), et la mémoire du projet est celle des
sessions terminal (même répertoire de travail `/root/price-checker`).

## Sécurité

- Un propriétaire : `DISCORD_OWNER_ID`. Si la variable est vide, **le premier humain qui écrit dans le
  salon devient le propriétaire** (appairage, journalisé) : à ne faire que tant que le salon n'est pas partagé.
- Le propriétaire autorise d'autres membres avec `!allow @membre` (`!deny` pour retirer, `!who` pour
  lister ; ou `DISCORD_ALLOWED_IDS` dans `.env`). Toute personne autorisée a les mêmes pouvoirs sur
  Claude Code que le propriétaire (dépôt, serveur). Les autres membres sont ignorés en silence.
- Une seule conversation, partagée par le salon : chaque message envoyé à Claude est signé
  `[Prénom] …`, il sait donc qui parle.
- Salon partagé : `!mention on` (ou `DISCORD_REQUIRE_MENTION=1`) pour que le bot ne traite que les
  messages qui le mentionnent, répondent à un de ses messages ou commencent par `!`, et laisse les
  humains discuter entre eux.
- Les messages de robots et de webhooks (les alertes) sont ignorés.
- Claude Code tourne en mode de permission `auto` : aucune question n'est posée, le
  classificateur approuve ou refuse chaque action (`--dangerously-skip-permissions` est de toute
  façon refusé en root). Une action refusée est signalée dans la réponse.
- Le jeton du bot est dans `../.env` (root seulement, jamais dans git).
- `claude -p` ne reçoit ni le jeton du bot ni les webhooks (variables `*TOKEN*`, `*WEBHOOK*`, `*SECRET*`,
  `*PASSWORD*` retirées de son environnement, audit du 02/10/2026).
- Les réponses du bot ne peuvent notifier ni `@everyone`, ni `@here`, ni un rôle (`allowed_mentions`).

## Installation

1. **Créer le bot** sur https://discord.com/developers/applications : *New Application* →
   onglet *Bot* → *Reset Token* → copier le jeton. Dans *Privileged Gateway Intents*, activer
   **Message Content Intent** (obligatoire pour lire les messages).
2. **L'inviter sur le serveur** : onglet *OAuth2* → *URL Generator* → scope `bot` → permissions
   *View Channels*, *Send Messages*, *Read Message History*, *Add Reactions*, *Attach Files* →
   ouvrir l'URL générée et choisir le serveur.
3. **Configurer** `../.env` :
   ```
   DISCORD_BOT_TOKEN=...            # jeton du bot
   DISCORD_CHANNEL_ID=1554820760365965444   # salon des alertes (celui du webhook)
   DISCORD_OWNER_ID=...             # votre identifiant Discord (Paramètres → Avancés → Mode développeur, puis clic droit sur votre nom → Copier l'identifiant) ; vide = appairage au premier message
   ```
   Le bot doit aussi avoir accès au salon : sur un salon privé, ajoutez-le dans *Modifier le salon → Permissions*
   (Voir le salon, Envoyer des messages, Lire l'historique, Ajouter des réactions, Joindre des fichiers) ;
   au démarrage, le journal signale les permissions manquantes.
   Optionnel : `CLAUDE_CWD` (défaut `/root/price-checker`), `CLAUDE_PERMISSION_MODE` (défaut `auto`),
   `CLAUDE_TIMEOUT` (défaut 1800 s).
4. **Lancer** :
   ```
   sudo cp price-check-bot.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now price-check-bot
   journalctl -u price-check-bot -f
   ```

Le venv `.venv` (discord.py) est créé avec `python3 -m venv .venv && .venv/bin/pip install 'discord.py>=2.3'`.

## Utilisation

Écrire dans le salon comme dans le terminal. Le bot réagit ⏳ pendant le travail, affiche l'outil
en cours (« ⚙️ Bash python3 -m unittest »), puis répond (en plusieurs messages si besoin, avec un
fichier joint au-delà de 6 000 caractères) et réagit ✅, ou ❌ avec l'erreur.

| Commande | Effet |
|---|---|
| `!new` | Nouvelle session Claude Code (la mémoire du projet reste) |
| `!stop` | Interrompre le traitement en cours |
| `!status` | Session, traitement en cours, file d'attente, mode |
| `!help` | Aide |
| `!allow @membre`, `!deny @membre`, `!who` | Propriétaire : qui peut parler au bot |
| `!mention on\|off` | Propriétaire : mention obligatoire ou non |

Les messages envoyés pendant un traitement sont mis en file (réaction 🕒) et traités dans l'ordre.

## Feedback des reports dans les fils Discord

Depuis le 03/10/2026 (Romain : « envoyer le feedback sur un thread du report sur Discord … ou les 2 ? » — les deux, avec
l'admin) :

- Chaque alerte des webhooks du moniteur (salons des top games, de la homepage, des urgences premiers prix) reçoit un fil
  **« Feedback · <jeu> · offer <id> »** (« offre <id> » avant le 07/10/2026 ; les deux sont reconnus), avec la consigne. Au démarrage, le bot ouvre aussi un fil sur les alertes récentes
  (150 derniers messages par salon) dont l'offre est encore signalée, sans décision, sur l'alerte la plus récente.
- Dans le fil, une personne **autorisée** (le propriétaire, ou ajoutée par `!allow`) répond **true** / **vrai** (`tp`,
  `vp`, ✅), **false** / **faux** (`fp`, ❌) ou **discuss** / **to discuss** / **à discuter** (💬), suivi si besoin d'une
  note : « false: the AllKeyShop page really is a DLC » (d'accord avec l'erreur décrite sur le report : **true** suffit, il
  n'y a rien à commenter). Les messages du bot sont en anglais depuis le 07/10/2026 (« Decision saved: … »). Le bot
  ajoute la décision à `/var/lib/price-check/decisions.jsonl` (le fichier de l'admin, signée « <nom> (Discord) ») et
  confirme dans le fil. Un autre membre reçoit un rappel ; une discussion sans mot-clé en tête ne tranche rien.
- Le moniteur poste dans le même fil les suites de l'offre (réparée, faux positif levé par une règle, vérifiée, de
  nouveau en erreur) et y recopie une décision prise dans l'admin. `threads.json`, dans le dossier partagé, garde le
  dernier fil de chaque offre ; l'admin affiche le lien.
- Rien de ces fils ne passe par Claude : le bot ne reconnaît que les mots-clés (une alerte cite des pages marchands).
- Droits nécessaires dans les trois salons : « Créer des fils publics », « Envoyer des messages dans les fils », « Voir
  l'historique ». Le journal du bot le vérifie au démarrage (`feedback : #salon (mode) ; fils OK`).

## Console de l'admin (service `price-check-console`)

`console.py` relie la console de l'onglet Price check de l'admin à Claude Code (Romain, 06/10/2026) : Romain avec les
droits de ce bot, Rémy, Garance et Lionel en questions-réponses, en lecture seule ; les questions qui demandent la
décision de Romain vont dans l'onglet Romain de l'admin. Détail : [docs/exploitation.md](../docs/exploitation.md).

```
cp price-check-console.service /etc/systemd/system/ && systemctl daemon-reload && systemctl enable --now price-check-console
journalctl -u price-check-console -f
```

## Tests

```
cd bot && .venv/bin/python -m unittest -v   # test_discord_bot, test_feedback, test_console
```
