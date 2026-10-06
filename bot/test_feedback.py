"""Tests du feedback des reports sur Discord, hors réseau et sans discord.py : python3 -m unittest test_feedback (depuis bot/)."""

import json
import os
import sys
import tempfile
import unittest

import feedback as fb

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import price_check as pc  # noqa: E402  (le moniteur relit decisions.jsonl : le format doit lui convenir)

ALERT = ("🔴 **SUSPECT** · **Minecraft Dungeons** (TOP 50 · All Popular #31) · Triple Bundle · 1er prix de l'édition\n"
         "CJS CDKeys · EUROPE (WINDOWS EU) · microsoft-windows · **75.15 €** · offre 140513764 · contrôle : page (Chromium)\n"
         "Raison : région interdite : ar, argentina")


class TestAlerts(unittest.TestCase):
    def test_alert_messages_and_their_offer(self):
        self.assertEqual(fb.alert_offer(ALERT), "140513764")
        self.assertEqual(fb.alert_offer("🚨 **URGENCE PREMIER PRIX** · Price check homepage\n" + ALERT), "140513764")
        self.assertEqual(fb.alert_offer("📌 **Rappel** · report existant (signalé le 2026-09-30 15:54), renvoyé…\n" + ALERT), "140513764")
        # le bandeau d'une boucle (price_check.loop_banner) : pas une alerte, pas de fil
        self.assertIsNone(fb.alert_offer("━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n# 🔄 Nouvelle boucle · Price check top\n"
                                         "-# 03/10/2026 13:20 · les tops : 10 premiers Popular, 5 premiers Coming soon PC"))
        self.assertIsNone(fb.alert_offer("🔁 **Recontrôle des offres signalées** · Price check homepage · offre 140513764 ·"))
        self.assertIsNone(fb.alert_offer("Bonjour, l'offre 140513764 est-elle réparée ?"))
        self.assertEqual(fb.alert_product(ALERT), "Minecraft Dungeons")

    def test_thread_names(self):
        name = fb.thread_name("140513764", "Minecraft Dungeons")
        self.assertEqual(name, "Feedback · Minecraft Dungeons · offre 140513764")
        self.assertEqual(fb.thread_offer(name), "140513764")
        long = fb.thread_name("140513764", "Pokemon Scarlet The Hidden Treasure of Area Zero Nintendo Switch Deluxe Collector Edition")
        self.assertLessEqual(len(long), 100)
        self.assertTrue(long.endswith("… · offre 140513764"), long)
        self.assertEqual(fb.thread_offer(long), "140513764")
        self.assertIsNone(fb.thread_offer("Discussion · offre 140513764"))
        self.assertEqual(fb.webhook_id("https://discord.com/api/webhooks/1555857218870710364/abcDEF"), 1555857218870710364)


class TestDecisions(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(fb.parse_decision("faux : la page AllKeyShop est bien un DLC"), ("faux", "la page AllKeyShop est bien un DLC"))
        self.assertEqual(fb.parse_decision("Faux positif, bonne édition"), ("faux", "bonne édition"))
        self.assertEqual(fb.parse_decision("FP"), ("faux", ""))
        self.assertEqual(fb.parse_decision("❌ autre région"), ("faux", "autre région"))
        self.assertEqual(fb.parse_decision("vrai"), ("vrai", ""))
        self.assertEqual(fb.parse_decision("Vrai positif — Kinguin sert le jeu de base"), ("vrai", "Kinguin sert le jeu de base"))
        self.assertEqual(fb.parse_decision("✅"), ("vrai", ""))
        self.assertEqual(fb.parse_decision("à discuter : on regarde demain"), ("a_discuter", "on regarde demain"))
        self.assertEqual(fb.parse_decision("A discuter"), ("a_discuter", ""))
        self.assertEqual(fb.parse_decision("💬 je ne vois pas le problème"), ("a_discuter", "je ne vois pas le problème"))
        # une discussion ne tranche pas : le mot-clé doit être en tête, et entier
        for text in ("vraiment ?", "fauxpas", "Je pense que c'est faux", "", "ok", "c'est vrai"):
            self.assertIsNone(fb.parse_decision(text), text)
        self.assertEqual(len(fb.parse_decision("faux " + "x" * 5000)[1]), fb.NOTE_MAX)

    def test_the_monitor_reads_what_the_bot_writes(self):
        with tempfile.TemporaryDirectory() as d:
            fb.append_decision(d, "140513764", "faux", "la page dit Europe", "Rémi (Discord)", at="2026-10-03T11:00:00+0200")
            fb.append_decision(d, "138007132", "vrai", "", "romain9102 (Discord)")
            self.assertEqual(oct(os.stat(os.path.join(d, "decisions.jsonl")).st_mode & 0o777), "0o664")  # l'admin (groupe) y écrit aussi
            decisions = pc.read_decisions(d)
            self.assertEqual(decisions["140513764"], {"decision": "faux", "note": "la page dit Europe", "by": "Rémi (Discord)",
                                                      "at": "2026-10-03T11:00:00+0200"})
            self.assertEqual(decisions["138007132"]["decision"], "vrai")
            with self.assertRaises(ValueError):
                fb.append_decision(d, "../etc", "faux", "", "x")
            with self.assertRaises(ValueError):
                fb.append_decision(d, "1", "supprimer", "", "x")

    def test_shared_files_never_through_a_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            victim = os.path.join(d, "victime")
            with open(victim, "w") as f:
                f.write("secret")
            os.symlink(victim, os.path.join(d, "decisions.jsonl"))
            with self.assertRaises(OSError):
                fb.append_decision(d, "1", "faux", "", "x")
            os.symlink(victim, os.path.join(d, "threads.json.tmp"))
            fb.save_threads(d, {"1": {"thread": 2}})
            with open(victim) as f:
                self.assertEqual(f.read(), "secret")
            self.assertEqual(fb.load_threads(d), {"1": {"thread": 2}})
            self.assertEqual(fb.load_threads(os.path.join(d, "absent")), {})


if __name__ == "__main__":
    unittest.main()
