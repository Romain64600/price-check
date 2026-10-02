"""Pont Discord <-> Claude Code : parler au moniteur, et le développer, depuis le salon des alertes.

Un seul utilisateur autorisé (DISCORD_OWNER_ID). Chaque message qu'il écrit dans le salon est
transmis à `claude -p` (mode de permission « auto » : pas de question posée, le classificateur
tranche), dans une session reprise d'un message à l'autre, et la réponse revient dans le salon.

D'autres personnes du salon peuvent être autorisées par le propriétaire (!allow @membre) : tout le
monde partage la même conversation, chaque message est signé de son auteur. Avec « !mention on »,
le bot ne traite que les messages qui le mentionnent, répondent à lui ou commencent par « ! ».

Commandes : !new (nouvelle session), !stop (interrompre), !status, !help ;
propriétaire : !allow @membre, !deny @membre, !who, !mention on|off.
Réglages dans ../.env : DISCORD_BOT_TOKEN, DISCORD_CHANNEL_ID, DISCORD_OWNER_ID, DISCORD_ALLOWED_IDS
(ids séparés par des virgules), DISCORD_REQUIRE_MENTION (1 = mention obligatoire au départ),
CLAUDE_CWD (défaut /root/price-checker), CLAUDE_PERMISSION_MODE (défaut auto), CLAUDE_TIMEOUT (s).
"""

import asyncio
import io
import json
import logging
import os
import re
import sys
import time

import shutil

import discord

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.environ.get("PRICE_CHECK_ENV", os.path.join(os.path.dirname(HERE), ".env"))
STATE_PATH = os.path.join(HERE, "state.json")
DISCORD_LIMIT = 2000  # taille maximale d'un message Discord
FILE_THRESHOLD = 6000  # au-delà, la réponse est aussi jointe en fichier .md
PROGRESS_EVERY = 5  # secondes entre deux mises à jour du message de progression

SYSTEM_PROMPT = (
    "Tu réponds dans un salon Discord, pas dans un terminal : messages courts et directs, en français ; "
    "pas de tableaux Markdown (Discord ne les affiche pas), utilise des listes ; les blocs de code sont bien rendus. "
    "Le salon reçoit aussi les alertes du moniteur price-check via un webhook."
)

log = logging.getLogger("price-check-bot")


def load_env(path):
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip())
    except OSError:
        pass


def load_state():
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=1)
    os.replace(tmp, STATE_PATH)


# ---- Qui peut parler au bot ------------------------------------------------------

def is_authorized(author_id, owner_id, allowed):
    return author_id == owner_id or author_id in allowed


def wants_bot(content, mentioned, is_reply_to_bot, require_mention):
    """Avec la mention obligatoire, seuls les messages qui mentionnent le bot, répondent à un de
    ses messages ou commencent par « ! » lui sont adressés ; sinon, tous."""
    return (not require_mention) or mentioned or is_reply_to_bot or content.lstrip().startswith("!")


def strip_mention(content, bot_id, role_ids=()):
    """Retire la mention du bot (<@id>) et celles de ses rôles (<@&id>, le rôle « Price Checker »)."""
    content = re.sub(r"<@!?%d>" % bot_id, "", content)
    for role_id in role_ids:
        content = re.sub(r"<@&%d>" % role_id, "", content)
    return content.strip()


# ---- Découpage des réponses pour Discord -------------------------------------

def split_message(text, limit=DISCORD_LIMIT):
    """Découpe un texte en morceaux de `limit` caractères au plus, sur des fins de ligne,
    sans casser un bloc de code : un bloc ouvert est refermé et rouvert dans le morceau suivant."""
    chunks, current, fence = [], "", None
    for line in text.split("\n"):
        while len(line) > limit - 10:  # ligne trop longue à elle seule
            chunks.append(current + line[:limit - 10]) if not current else chunks.extend([current, line[:limit - 10]])
            current, line = "", line[limit - 10:]
        m = re.match(r"^```(\w*)", line)
        candidate = current + ("\n" if current else "") + line
        closing = "\n```" if fence is not None and not m else ""
        if len(candidate) + len(closing) > limit:
            chunks.append(current + closing if fence is not None and not m else current)
            current = ("```%s\n" % fence if fence is not None and not m else "") + line
        else:
            current = candidate
        if m:
            fence = None if fence is not None else m.group(1)
    if current:
        chunks.append(current)
    return [c for c in chunks if c.strip()] or ["(réponse vide)"]


def progress_line(event):
    """Une ligne de progression lisible pour un événement stream-json, ou None."""
    if event.get("type") != "assistant":
        return None
    for block in event.get("message", {}).get("content", []):
        if block.get("type") != "tool_use":
            continue
        name, inp = block.get("name", "?"), block.get("input") or {}
        detail = inp.get("command") or inp.get("file_path") or inp.get("pattern") or inp.get("description") or inp.get("query") or ""
        detail = re.sub(r"\s+", " ", str(detail))[:90]
        return "⚙️ %s %s" % (name, detail)
    return None


# ---- Claude Code en sous-processus ---------------------------------------------

def find_claude():
    """L'exécutable Claude Code : CLAUDE_BIN, sinon le PATH, sinon les emplacements habituels
    (sous systemd, le PATH est minimal)."""
    candidates = [os.environ.get("CLAUDE_BIN") or "claude", "/root/.local/bin/claude", "/usr/local/bin/claude",
                  os.path.expanduser("~/.claude/local/claude"), os.path.expanduser("~/.npm-global/bin/claude")]
    for c in candidates:
        found = shutil.which(c) if os.sep not in c else (c if os.access(c, os.X_OK) else None)
        if found:
            return found
    return None


# Jamais transmis à `claude -p` : le jeton du bot, les webhooks (audit du 02/10/2026). Claude n'en a pas besoin, et une
# commande qu'il lancerait ne doit pas les trouver dans son environnement.
SECRET_ENV_RE = re.compile(r"TOKEN|WEBHOOK|SECRET|PASSWORD", re.IGNORECASE)


def claude_env(environ):
    env = {k: v for k, v in environ.items() if not SECRET_ENV_RE.search(k)}
    env["HOME"] = environ.get("HOME", "/root")
    return env


class ClaudeRunner:
    def __init__(self, cwd, permission_mode, timeout):
        self.cwd, self.permission_mode, self.timeout = cwd, permission_mode, timeout
        self.process = None
        self.binary = find_claude()

    async def run(self, prompt, session_id, on_progress):
        """Lance `claude -p`, suit la progression, renvoie (texte, session_id, erreur)."""
        if not self.binary:
            return "", session_id, "exécutable claude introuvable (CLAUDE_BIN dans .env)"
        cmd = [self.binary, "-p", "--output-format", "stream-json", "--verbose",
               "--permission-mode", self.permission_mode, "--permission-prompts", "none",
               "--append-system-prompt", SYSTEM_PROMPT]
        if session_id:
            cmd += ["--resume", session_id]
        env = claude_env(os.environ)
        self.process = await asyncio.create_subprocess_exec(
            *cmd, cwd=self.cwd, env=env, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        self.process.stdin.write(prompt.encode())
        self.process.stdin.close()
        result, new_session, error = None, session_id, None
        try:
            async with asyncio.timeout(self.timeout):
                async for raw in self.process.stdout:
                    try:
                        event = json.loads(raw)
                    except ValueError:
                        continue
                    if event.get("session_id"):
                        new_session = event["session_id"]
                    line = progress_line(event)
                    if line:
                        await on_progress(line)
                    if event.get("type") == "result":
                        result = event.get("result") or ""
                        if event.get("is_error"):
                            error = result or "erreur Claude Code"
                stderr = (await self.process.stderr.read()).decode(errors="replace")
                await self.process.wait()
        except TimeoutError:
            self.process.kill()
            error = "délai dépassé (%d s), session interrompue" % self.timeout
            stderr = ""
        finally:
            self.process = None
        if result is None and not error:
            error = "pas de réponse de Claude Code" + (" : " + stderr.strip()[-500:] if stderr.strip() else "")
        return result or "", new_session, error

    def stop(self):
        if self.process:
            self.process.kill()
            return True
        return False


# ---- Le bot --------------------------------------------------------------------

class Bot(discord.Client):
    def __init__(self, channel_id, owner_id, runner):
        intents = discord.Intents.default()
        intents.message_content = True
        # pas de @everyone, @here ni de rôle dans les réponses : un texte de Claude ne notifie jamais tout le salon
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True,
                                                                                  replied_user=True))
        self.channel_id, self.owner_id, self.runner = channel_id, owner_id, runner
        self.state = load_state()
        if not self.owner_id:
            self.owner_id = int(self.state.get("owner_id") or 0)
        self.state.setdefault("allowed", [])
        for raw in os.environ.get("DISCORD_ALLOWED_IDS", "").split(","):
            if raw.strip().isdigit() and int(raw) not in self.state["allowed"]:
                self.state["allowed"].append(int(raw))
        self.state.setdefault("require_mention", os.environ.get("DISCORD_REQUIRE_MENTION") == "1")
        self.queue = asyncio.Queue()
        self.busy = None  # message en cours de traitement
        self.started = time.time()

    async def setup_hook(self):
        self.loop.create_task(self.worker())

    async def on_ready(self):
        log.info("connecté : %s ; salon %s ; propriétaire %s ; session %s",
                 self.user, self.channel_id, self.owner_id or "à définir", self.state.get("session_id"))
        channel = self.get_channel(self.channel_id)
        if channel is None:
            log.warning("salon %s introuvable : le bot est-il invité sur le serveur, avec accès au salon ?", self.channel_id)
        else:
            perms = channel.permissions_for(channel.guild.me)
            missing = [name for name, ok in (("view_channel", perms.view_channel), ("send_messages", perms.send_messages),
                                             ("read_message_history", perms.read_message_history),
                                             ("add_reactions", perms.add_reactions), ("attach_files", perms.attach_files)) if not ok]
            log.info("salon trouvé : #%s (%s)%s", channel.name, channel.guild.name,
                     " ; PERMISSIONS MANQUANTES : " + ", ".join(missing) if missing else " ; permissions OK")

    async def on_message(self, message):
        log.info("message reçu : salon %s, auteur %s (%s)%s, %d caractères", message.channel.id, message.author,
                 message.author.id, " [webhook]" if message.webhook_id else (" [bot]" if message.author.bot else ""),
                 len(message.content))
        if message.channel.id != self.channel_id or message.author.bot or message.webhook_id:
            return
        if message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return  # message système (« a épinglé un message », arrivée d'un membre...)
        if not self.owner_id:  # appairage : le premier humain du salon devient le propriétaire
            self.owner_id = message.author.id
            self.state["owner_id"] = self.owner_id
            save_state(self.state)
            log.warning("propriétaire appairé : %s (%s)", message.author, self.owner_id)
            await message.reply("👋 Appairé : je ne réponds qu'à toi ici. Envoie `!help` pour les commandes.")
        if not is_authorized(message.author.id, self.owner_id, self.state["allowed"]):
            log.info("ignoré : %s (%s) n'est pas autorisé", message.author, message.author.id)
            return
        my_roles = [r.id for r in getattr(message.guild.me, "roles", [])] if message.guild else []
        mentioned = self.user in message.mentions or any(r.id in my_roles for r in message.role_mentions)  # @Price Checker, utilisateur ou rôle
        replied = message.reference is not None and getattr(message.reference.resolved, "author", None) == self.user
        if not wants_bot(message.content, mentioned, replied, self.state["require_mention"]):
            return
        text = strip_mention(message.content, self.user.id, my_roles)
        if text.startswith("!"):
            handled = await self.command(message, text)
            if handled:
                return
        if not text:
            return
        await self.queue.put((message, text))
        if self.busy:
            await message.add_reaction("🕒")  # en file d'attente

    async def command(self, message, text):
        word = text.split()[0].lower()
        owner_only = word in ("!allow", "!deny", "!who", "!mention")
        if owner_only and message.author.id != self.owner_id:
            await message.reply("Commande réservée au propriétaire du bot.")
            return True
        if word == "!allow" or word == "!deny":
            people = [m for m in message.mentions if m != self.user]
            if not people:
                await message.reply("Mentionne la ou les personnes : `%s @membre`" % word)
                return True
            for member in people:
                if word == "!allow" and member.id not in self.state["allowed"] and member.id != self.owner_id:
                    self.state["allowed"].append(member.id)
                if word == "!deny" and member.id in self.state["allowed"]:
                    self.state["allowed"].remove(member.id)
            save_state(self.state)
            log.warning("%s par %s : %s", word, message.author, [(m.name, m.id) for m in people])
            await message.reply("✅ %s : %s" % ("autorisé(s)" if word == "!allow" else "retiré(s)", ", ".join(m.mention for m in people)))
        elif word == "!who":
            names = []
            for uid in [self.owner_id] + self.state["allowed"]:
                member = message.guild.get_member(uid) if message.guild else None
                names.append("%s%s" % (member.mention if member else "`%s`" % uid, " (propriétaire)" if uid == self.owner_id else ""))
            await message.reply("Peuvent me parler : " + ", ".join(names) + "\nMention obligatoire : %s" % ("oui" if self.state["require_mention"] else "non"))
        elif word == "!mention":
            arg = (text.split() + [""])[1].lower()
            if arg not in ("on", "off"):
                await message.reply("Usage : `!mention on` (je ne réponds qu'aux messages qui me mentionnent, me répondent ou commencent par `!`) ou `!mention off`.")
                return True
            self.state["require_mention"] = arg == "on"
            save_state(self.state)
            await message.reply("Mention obligatoire : %s." % ("oui" if arg == "on" else "non"))
        elif word == "!new":
            self.state["session_id"] = None
            save_state(self.state)
            await message.reply("🆕 Nouvelle session : la suivante repart de zéro (mémoire du projet conservée).")
        elif word == "!stop":
            await message.reply("🛑 Interrompu." if self.runner.stop() else "Rien en cours.")
        elif word == "!status":
            await message.reply("session : `%s`\nen cours : %s\nen attente : %d\nmode : %s\nautorisés : %d + propriétaire, mention obligatoire : %s\nactif depuis : %s" % (
                self.state.get("session_id") or "aucune", "oui" if self.busy else "non", self.queue.qsize(),
                self.runner.permission_mode, len(self.state["allowed"]), "oui" if self.state["require_mention"] else "non",
                time.strftime("%d/%m %H:%M", time.localtime(self.started))))
        elif word == "!help":
            await message.reply("Écris-moi ici comme dans le terminal ; la conversation est partagée par tout le salon et chaque message est signé.\n"
                                "Commandes : `!new` nouvelle session, `!stop` interrompre, `!status` état, `!help`.\n"
                                "Propriétaire : `!allow @membre`, `!deny @membre`, `!who`, `!mention on|off`.")
        else:
            return False
        return True

    async def worker(self):
        await self.wait_until_ready()
        while True:
            message, text = await self.queue.get()
            self.busy = message
            try:
                await self.handle(message, text)
            except Exception:
                log.exception("traitement raté")
                try:
                    await message.add_reaction("❌")
                except discord.HTTPException:
                    pass
            finally:
                self.busy = None

    async def handle(self, message, text):
        await message.add_reaction("⏳")
        status = await message.channel.send("⚙️ je réfléchis…")
        last = {"text": None, "at": 0.0}

        async def on_progress(line):
            now = time.monotonic()
            if line != last["text"] and now - last["at"] >= PROGRESS_EVERY:
                last.update(text=line, at=now)
                try:
                    await status.edit(content=line)
                except discord.HTTPException:
                    pass

        started = time.monotonic()
        prompt = "[%s] %s" % (message.author.display_name, text)  # signé : Claude sait qui parle
        try:
            async with message.channel.typing():
                text, session_id, error = await self.runner.run(prompt, self.state.get("session_id"), on_progress)
        except Exception:
            try:
                await status.delete()  # pas de « je réfléchis… » orphelin
            except discord.HTTPException:
                pass
            raise
        if session_id:
            self.state["session_id"] = session_id
            save_state(self.state)
        try:
            await status.delete()
        except discord.HTTPException:
            pass
        elapsed = int(time.monotonic() - started)
        if error and not text:
            await message.reply("❌ %s" % error[:1500])
            await message.add_reaction("❌")
            return
        chunks = split_message(text)
        first = True
        for chunk in chunks:
            if first:
                await message.reply(chunk, mention_author=False)
                first = False
            else:
                await message.channel.send(chunk)
        if len(text) > FILE_THRESHOLD:
            await message.channel.send("📎 réponse complète en pièce jointe",
                                       file=discord.File(io.BytesIO(text.encode()), filename="reponse.md"))
        if error:
            await message.channel.send("⚠️ %s" % error[:1500])
        log.info("répondu en %d s (%d caractères, %d morceaux)", elapsed, len(text), len(chunks))
        await message.remove_reaction("⏳", self.user)
        await message.add_reaction("✅")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_env(ENV_PATH)
    token = os.environ.get("DISCORD_BOT_TOKEN")
    channel_id = os.environ.get("DISCORD_CHANNEL_ID")
    if not token or not channel_id:
        sys.exit("DISCORD_BOT_TOKEN et DISCORD_CHANNEL_ID manquants dans %s" % ENV_PATH)
    runner = ClaudeRunner(cwd=os.environ.get("CLAUDE_CWD", "/root/price-checker"),
                          permission_mode=os.environ.get("CLAUDE_PERMISSION_MODE", "auto"),
                          timeout=int(os.environ.get("CLAUDE_TIMEOUT", "1800")))
    log.info("claude : %s ; répertoire : %s ; mode : %s", runner.binary or "INTROUVABLE", runner.cwd, runner.permission_mode)
    Bot(int(channel_id), int(os.environ.get("DISCORD_OWNER_ID", "0") or 0), runner).run(token, log_handler=None)


if __name__ == "__main__":
    main()
