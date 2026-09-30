"""Tests du bot, hors réseau : .venv/bin/python -m unittest -v (depuis bot/)."""

import unittest

import discord_bot as db


class TestSplitMessage(unittest.TestCase):
    def test_short_text_is_one_chunk(self):
        self.assertEqual(db.split_message("bonjour"), ["bonjour"])
        self.assertEqual(db.split_message(""), ["(réponse vide)"])

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


if __name__ == "__main__":
    unittest.main()
