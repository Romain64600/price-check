# Bot Discord : parler à Claude Code depuis le salon des alertes

Le bot lit les messages du salon où arrivent les alertes du moniteur, les transmet à Claude Code
(`claude -p`, sur ce serveur, dans le dépôt) et renvoie la réponse dans le salon. La conversation
est reprise d'un message à l'autre (session Claude Code), et la mémoire du projet est celle des
sessions terminal (même répertoire de travail `/root/price-checker`).

## Sécurité

- Un seul utilisateur autorisé : `DISCORD_OWNER_ID`. Les autres membres du salon sont ignorés.
  Si la variable est vide, **le premier humain qui écrit dans le salon devient le propriétaire**
  (appairage, journalisé) : à ne faire que tant que le salon n'est pas partagé.
- Les messages de robots et de webhooks (les alertes) sont ignorés.
- Claude Code tourne en mode de permission `auto` : aucune question n'est posée, le
  classificateur approuve ou refuse chaque action (`--dangerously-skip-permissions` est de toute
  façon refusé en root). Une action refusée est signalée dans la réponse.
- Le jeton du bot est dans `../.env` (root seulement, jamais dans git).

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

Les messages envoyés pendant un traitement sont mis en file (réaction 🕒) et traités dans l'ordre.

## Tests

```
cd bot && .venv/bin/python -m unittest -v
```
