"""Tests du bot, hors réseau : .venv/bin/python -m unittest -v (depuis bot/)."""

import unittest

import discord_bot as db


class TestSplitMessage(unittest.TestCase):
    def test_short_text_is_one_chunk(self):
        self.assertEqual(db.split_message("bonjour"), ["bonjour"])
        self.assertEqual(db.split_message(""), ["(empty answer)"])

    def test_splits_on_lines_under_the_limit(self):
        text = "\n".join("ligne %03d" % i for i in range(500))
        chunks = db.split_message(text, limit=200)
        self.assertTrue(all(len(c) <= 200 for c in chunks))
        self.assertEqual("\n".join(chunks), text)

    def test_code_fence_is_closed_and_reopened(self):
        text = "avant\n```python\n" + "\n".join("x = %d" % i for i in range(100)) + "\n```\naprès"
        chunks = db.split_message(text, limit=300)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertEqual(chunk.count("```") % 2, 0, chunk)  # chaque morceau a ses blocs fermés
            self.assertLessEqual(len(chunk), 300)
        self.assertTrue(chunks[1].startswith("```python\n"))
        self.assertTrue(chunks[-1].endswith("après"))

    def test_very_long_line(self):
        chunks = db.split_message("a" * 5000, limit=2000)
        self.assertTrue(all(len(c) <= 2000 for c in chunks))
        self.assertEqual("".join(chunks), "a" * 5000)


class TestWhoCanTalk(unittest.TestCase):
    def test_authorized(self):
        self.assertTrue(db.is_authorized(1, 1, []))
        self.assertTrue(db.is_authorized(2, 1, [2, 3]))
        self.assertFalse(db.is_authorized(4, 1, [2, 3]))

    def test_mention_requirement(self):
        self.assertTrue(db.wants_bot("salut", False, False, require_mention=False))
        self.assertFalse(db.wants_bot("salut Rémy", False, False, require_mention=True))
        self.assertTrue(db.wants_bot("<@1> salut", True, False, require_mention=True))
        self.assertTrue(db.wants_bot("oui", False, True, require_mention=True))
        self.assertTrue(db.wants_bot("!status", False, False, require_mention=True))

    def test_strip_mention(self):
        self.assertEqual(db.strip_mention("<@1554843596358946937> où en est le passage ?", 1554843596358946937), "où en est le passage ?")
        self.assertEqual(db.strip_mention("<@!42> !status", 42), "!status")
        # mention du rôle « Price Checker » (30/09/2026 : c'est ainsi que Discord a complété @Price Checker)
        self.assertEqual(db.strip_mention("<@&1554844856441110540> Tu as trouvé des erreurs ?", 42, [1554844856441110540]),
                         "Tu as trouvé des erreurs ?")


class TestProgressLine(unittest.TestCase):
    def test_tool_use(self):
        event = {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "je lance"},
            {"type": "tool_use", "name": "Bash", "input": {"command": "python3   -m unittest", "description": "tests"}}]}}
        self.assertEqual(db.progress_line(event), "⚙️ Bash python3 -m unittest")
        event = {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": "/x/y.py"}}]}}
        self.assertEqual(db.progress_line(event), "⚙️ Edit /x/y.py")

    def test_other_events(self):
        self.assertIsNone(db.progress_line({"type": "result", "result": "ok"}))
        self.assertIsNone(db.progress_line({"type": "assistant", "message": {"content": [{"type": "text", "text": "x"}]}}))



class TestClaudeEnvironment(unittest.TestCase):
    def test_secrets_never_reach_claude(self):
        # audit du 02/10/2026 : le sous-processus `claude -p` héritait du jeton du bot et des webhooks
        # 06/10/2026 : la clé de l'API gg.deals non plus
        env = db.claude_env({"DISCORD_BOT_TOKEN": "t", "DISCORD_WEBHOOK_URL": "w", "DISCORD_WEBHOOK_URL_HOMEPAGE": "h",
                             "GGDEALS_API_KEY": "k", "PATH": "/usr/bin", "LANG": "C.UTF-8", "DISCORD_CHANNEL_ID": "1", "HOME": "/root"})
        self.assertEqual(sorted(env), ["DISCORD_CHANNEL_ID", "HOME", "LANG", "PATH"])
        self.assertEqual(db.claude_env({})["HOME"], "/root")

    def test_no_mass_mentions(self):
        # audit du 02/10/2026 : une réponse de Claude contenant @everyone ou un rôle notifierait tout le salon
        from unittest import mock
        with mock.patch.object(db, "load_state", return_value={}):
            bot = db.Bot(1, 2, None)
        self.assertFalse(bot.allowed_mentions.everyone)
        self.assertFalse(bot.allowed_mentions.roles)


class TestFilters(unittest.TestCase):
    """Audit des tests du 02/10/2026 : rien ne vérifiait que les alertes du webhook et les non-propriétaires n'atteignent
    pas `claude -p` (mode auto, service root)."""

    @staticmethod
    def message(**kw):
        from types import SimpleNamespace
        base = dict(channel=SimpleNamespace(id=10), author=SimpleNamespace(bot=False, id=1), webhook_id=None,
                    type=db.discord.MessageType.default)
        base.update(kw)
        return SimpleNamespace(**base)

    def test_only_humans_in_the_channel(self):
        from types import SimpleNamespace
        self.assertTrue(db.is_human_message(self.message(), 10))
        self.assertTrue(db.is_human_message(self.message(type=db.discord.MessageType.reply), 10))
        self.assertFalse(db.is_human_message(self.message(webhook_id=99), 10))  # une alerte du moniteur
        self.assertFalse(db.is_human_message(self.message(author=SimpleNamespace(bot=True, id=2)), 10))
        self.assertFalse(db.is_human_message(self.message(channel=SimpleNamespace(id=11)), 10))
        self.assertFalse(db.is_human_message(self.message(type=db.discord.MessageType.pins_add), 10))

    def test_owner_only_commands(self):
        for word in ("!allow", "!deny", "!who", "!mention"):
            self.assertTrue(db.is_owner_only(word), word)
        for word in ("!help", "!new", "!stop", "!status"):
            self.assertFalse(db.is_owner_only(word), word)


if __name__ == "__main__":
    unittest.main()
