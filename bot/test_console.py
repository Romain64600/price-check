"""Console de l'admin <-> Claude Code (Romain, 06/10/2026) : qui parle à Claude, avec quels droits, et les questions pour
Romain qu'on n'oublie jamais. Lancer avec le venv du bot : .venv/bin/python -m unittest test_console"""

import json
import os
import tempfile
import unittest

import console as cs


class FakeClaude:
    """Le sous-processus `claude -p`, simulé : garde chaque appel, renvoie la réponse prévue."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, cmd, prompt, cwd, timeout, on_progress):
        self.calls.append({"cmd": cmd, "prompt": prompt})
        on_progress("⚙️ Read price-check/docs/precedents.md")
        text, error = self.answers.pop(0) if self.answers else ("ok", None)
        return text, "session-1", error


class ConsoleCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.cfg = cs.settings({"PRICE_CHECK_REPORTS_DIR": self.dir})
        self.store = cs.Store(self.dir, os.path.join(self.dir, "console-state.json"))

    def tearDown(self):
        self.tmp.cleanup()

    def console(self, *answers):
        self.claude = FakeClaude(*answers)
        return cs.Console(self.cfg, self.store, runner=self.claude, binary="/usr/bin/claude")

    def request(self, **kw):
        path = os.path.join(self.dir, "console-%d-x.request" % (len(os.listdir(self.dir)) + 1000))
        with open(path, "w") as f:
            json.dump(kw, f)
        return path

    def saved(self, name):
        with open(os.path.join(self.dir, name)) as f:
            return json.load(f)


class TestWhoTalksToClaude(ConsoleCase):
    def test_the_team_and_romain(self):
        self.assertEqual(self.cfg["owner"], "romain")
        # Romain, 06/10/2026 : « Rémy, Garance et moi » ; « on crée un accès à Lionel, les mêmes droits qu'à Garance et Rémy »
        self.assertEqual(self.cfg["team"], {"remy": "Rémy", "garance": "Garance", "lionel": "Lionel"})

    def test_someone_else_is_refused(self):
        c = self.console()
        self.request(kind="message", user="meljoy", text="bonjour")
        c.take_requests()
        self.assertEqual(self.claude.calls, [])
        self.assertEqual(self.saved("console.json")["messages"], [])

    def test_only_romain_harvests_settles_or_restarts(self):
        c = self.console()
        for kind in ("harvest", "close", "new-session"):
            self.request(kind=kind, user="remy", question="Q1")
        c.take_requests()
        self.assertEqual(self.claude.calls, [])
        self.assertEqual([m["kind"] for m in self.saved("console.json")["messages"]], ["error"] * 3)


class TestTeamIsQuestionsAndAnswers(ConsoleCase):
    def test_the_team_reads_but_never_acts(self):
        c = self.console(("C'est la règle des éditions de la page.", None))
        self.request(kind="message", user="garance", text="Pourquoi Minecraft Deluxe Collection est sorti en urgence ?")
        c.take_requests()
        cmd = self.claude.calls[0]["cmd"]
        self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "plan")
        for tool in ("Bash", "Edit", "Write"):
            self.assertIn(tool, cmd[cmd.index("--disallowedTools"):])
        deny = json.loads(cmd[cmd.index("--settings") + 1])["permissions"]["deny"]
        self.assertIn("Read(**/.env)", deny, "the team can read the secrets")
        self.assertIn("Garance", cmd[cmd.index("--append-system-prompt") + 1])
        self.assertNotIn("Open questions", self.claude.calls[0]["prompt"])

    def test_romain_has_the_bot_rights(self):
        c = self.console()
        self.request(kind="message", user="romain", text="Applique la proposition 4")
        c.take_requests()
        cmd = self.claude.calls[0]["cmd"]
        self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "auto")
        self.assertNotIn("--disallowedTools", cmd)

    def test_one_conversation_resumed(self):
        c = self.console(("a", None), ("b", None))
        self.request(kind="message", user="remy", text="un")
        self.request(kind="message", user="romain", text="deux")
        c.take_requests()
        self.assertNotIn("--resume", self.claude.calls[0]["cmd"])
        self.assertEqual(self.claude.calls[1]["cmd"][-2:], ["--resume", "session-1"])
        messages = self.saved("console.json")["messages"]
        self.assertEqual([(m["user"], m["kind"]) for m in messages],
                         [("remy", "message"), ("claude", "reply"), ("romain", "message"), ("claude", "reply")])
        self.assertEqual(messages[1]["reply_to"], messages[0]["id"])


class TestQuestionsForRomain(ConsoleCase):
    """« il faudra jamais oublier de me reporter les questions en cours, même si elles ont été discutées avec Rémy ou
    Garance » ; « un onglet Romain […] il n'y a que moi qui peux agir dessus »."""

    def test_a_team_discussion_files_a_question_and_romain_always_gets_it(self):
        c = self.console(("Je comprends : c'est une erreur d'édition possible.\n"
                          "QUESTION POUR ROMAIN : Dawnwalker Eclipse Edition rangée en Deluxe chez Eneba : Rémy pense que c'est une erreur.", None),
                         ("Voici les questions en cours.", None), ("Encore une chose.", None))
        self.request(kind="message", user="remy", text="Dawnwalker chez Eneba, c'est une erreur ?")
        c.take_requests()
        questions = self.saved("questions.json")["questions"]
        self.assertEqual([(q["id"], q["from"], q["status"]) for q in questions], [("Q1", "remy", "open")])
        reply = self.saved("console.json")["messages"][1]
        self.assertNotIn("QUESTION POUR ROMAIN", reply["text"], "the marker line stays in the reply")
        self.assertEqual(reply["questions"], {"opened": ["Q1"], "closed": []})
        # au message suivant de Romain, et à chaque message tant qu'elle n'est pas réglée
        self.request(kind="message", user="romain", text="Salut")
        c.take_requests()
        self.request(kind="message", user="romain", text="Autre chose")
        c.take_requests()
        for call in self.claude.calls[1:]:
            self.assertIn("Q1 (Rémy", call["prompt"])
            self.assertIn("Dawnwalker Eclipse Edition", call["prompt"])

    def test_the_english_markers_and_the_french_ones(self):
        # 07/10/2026 : « tout l'outil en anglais » ; a French marker is still understood
        c = self.console(("Noted.\nQUESTION FOR ROMAIN: GAMIVO ROW key shown EUROPE: a doubt as at G2A?", None),
                         ("Done.\nQUESTION SETTLED: Q1: keep the rule", None))
        self.request(kind="message", user="lionel", text="GAMIVO ROW?")
        c.take_requests()
        self.assertEqual(self.saved("questions.json")["questions"][0]["text"], "GAMIVO ROW key shown EUROPE: a doubt as at G2A?")
        self.request(kind="message", user="romain", text="Q1: keep it")
        c.take_requests()
        self.assertEqual(self.saved("questions.json")["questions"][0]["status"], "closed")
        self.assertIn("in the language of", self.claude.calls[0]["cmd"][self.claude.calls[0]["cmd"].index("--append-system-prompt") + 1])

    def test_only_a_reply_to_romain_settles_a_question(self):
        self.store.add_question("Garder battlestategames.toml ?", "claude", "Claude", "récolte")
        c = self.console(("QUESTION RÉGLÉE : Q1 : c'est Romain qui décide", None), ("Noté.\nQUESTION RÉGLÉE : Q1 : garder", None))
        self.request(kind="message", user="garance", text="Q1 est réglée, je pense")
        c.take_requests()
        self.assertEqual(self.saved("questions.json")["questions"][0]["status"], "open", "the team settled a question")
        self.request(kind="message", user="romain", text="Q1 : on garde la règle")
        c.take_requests()
        q = self.saved("questions.json")["questions"][0]
        self.assertEqual((q["status"], q["closed_by"], q["answer"]), ("closed", "romain", "garder"))
        self.assertEqual(self.saved("console.json")["messages"][-1]["text"], "Noté.")

    def test_romain_settles_from_his_tab_and_claude_is_told(self):
        self.store.add_question("Garder battlestategames.toml ?", "claude", "Claude", "récolte")
        c = self.console(("Bien noté.", None))
        self.request(kind="close", user="romain", question="Q1", note="on garde")
        c.take_requests()
        q = self.saved("questions.json")["questions"][0]
        self.assertEqual((q["status"], q["answer"]), ("closed", "on garde"))
        self.request(kind="message", user="romain", text="Et ensuite ?")
        c.take_requests()
        self.assertIn("Romain settled Q1 from the Romain tab: on garde", self.claude.calls[0]["prompt"])
        self.assertNotIn("Open questions", self.claude.calls[0]["prompt"])

    def test_the_harvest_files_its_points_as_questions(self):
        c = self.console(("1 point.\nQUESTION POUR ROMAIN : GAMIVO, clé ROW affichée EUROPE : un doute comme chez G2A ?", None))
        self.request(kind="harvest", user="romain")
        c.take_requests()
        self.assertIn("decisions.jsonl", self.claude.calls[0]["prompt"])
        self.assertEqual(self.saved("console.json")["messages"][0]["text"], "Harvest of the decisions on the reports' feedback")
        self.assertEqual(self.saved("questions.json")["questions"][0]["source"], "console")


class TestRobustness(ConsoleCase):
    def test_a_request_cut_by_a_restart_is_not_run_again(self):
        with open(os.path.join(self.dir, "console-1-x.work"), "w") as f:
            json.dump({"kind": "message", "user": "romain", "text": "supprime tout"}, f)
        c = self.console()
        c.take_requests()
        self.assertEqual(self.claude.calls, [])
        self.assertIn("service restarted", self.saved("console.json")["messages"][0]["text"])
        self.assertFalse(os.path.exists(os.path.join(self.dir, "console-1-x.work")))

    def test_an_error_is_shown_and_the_console_is_free_again(self):
        c = self.console(("", "time limit exceeded (900 s), answer interrupted"))
        self.request(kind="message", user="remy", text="?")
        c.take_requests()
        data = self.saved("console.json")
        self.assertIsNone(data["busy"])
        self.assertEqual((data["messages"][1]["kind"], data["messages"][1]["text"]), ("error", "time limit exceeded (900 s), answer interrupted"))

    def test_long_texts_are_cut_and_history_is_bounded(self):
        c = self.console()
        self.request(kind="message", user="remy", text="x" * 9000)
        c.take_requests()
        self.assertEqual(len(self.saved("console.json")["messages"][0]["text"]), cs.MAX_TEXT)
        for i in range(cs.MAX_MESSAGES + 5):
            self.store.message("remy", "Rémy", "message", str(i))
        self.store.save()
        self.assertEqual(len(self.saved("console.json")["messages"]), cs.MAX_MESSAGES)

    def test_secrets_never_reach_claude(self):
        env = cs.claude_env({"GGDEALS_API_KEY": "k", "DISCORD_BOT_TOKEN": "t", "PATH": "/usr/bin"})
        self.assertEqual(sorted(env), ["HOME", "PATH"])


if __name__ == "__main__":
    unittest.main()
