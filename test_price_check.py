import copy
import json
import os
import unittest
from unittest import mock

import price_check as pc

SAMPLES = os.path.join(os.path.dirname(__file__), "samples")


def sample(name):
    with open(os.path.join(SAMPLES, name)) as f:
        return f.read()


class TestParsing(unittest.TestCase):
    def test_lists(self):
        targets = pc.parse_lists(json.loads(sample("api_topclick_all-popular_pc-soon.json")))
        popular = [t[2] for t in targets if t[0] == "Popular"]
        soon = [t[2] for t in targets if t[0] == "Coming soon PC"]
        self.assertEqual(popular, ["EA SPORTS FC 27", "The Witcher 3 Wild Hunt", "CONTROL Resonant",
                                   "WARDOGS", "Valheim"])
        self.assertEqual(soon, ["Dynasty Warriors 3 Complete Edition Remastered", "Ace Combat 8",
                                "AION 2", "STAR WARS Galactic Racer"])

    def test_lists_skip_non_games(self):
        data = json.loads(sample("api_topclick_sidebar.json"))
        targets = pc.parse_lists(data, [("all.popular", "Popular", 60)])
        self.assertTrue(targets)
        names = {i["name"] for i in data["sidebar"]["all.popular"]["items"] if i["productType"] != "game"}
        self.assertFalse(names & {t[2] for t in targets})

    def test_game_page_matches_saved_json(self):
        trans = pc.parse_game_page(sample("prod_popular1_ea-fc-27.html"))
        self.assertEqual(trans["prices"], json.loads(sample("prod_popular1_gamePageTrans.json"))["prices"])


class TestAnomalies(unittest.TestCase):
    def setUp(self):
        self.trans = json.loads(sample("prod_popular1_gamePageTrans.json"))

    def standard_offers(self):
        return sorted(
            (p for p in self.trans["prices"]
             if p["edition"] == "1" and not p["account"] and p["price"] != pc.NO_PRICE),
            key=lambda p: p["priceCard"],
        )

    def test_real_samples_have_no_anomaly(self):
        self.assertEqual(pc.find_anomalies(self.trans), [])
        self.assertEqual(pc.find_anomalies(json.loads(sample("prod_soon1_gamePageTrans.json"))), [])

    def test_cheap_offer_triggers(self):
        cheapest = self.standard_offers()[0]
        cheapest["priceCard"] = round(cheapest["priceCard"] * 0.6, 2)
        [a] = pc.find_anomalies(self.trans)
        self.assertEqual(a["offer_id"], cheapest["id"])
        self.assertEqual(a["edition"], "Standard")
        self.assertGreaterEqual(a["gap"], 0.30)

    def test_just_under_threshold_does_not_trigger(self):
        offers = self.standard_offers()
        offers[0]["priceCard"] = round(offers[1]["priceCard"] * 0.71, 2)
        self.assertEqual(pc.find_anomalies(self.trans), [])

    def test_sentinel_and_account_offers_ignored(self):
        offers = self.standard_offers()
        extra = []
        for account, price in ((True, 1.0), (False, pc.NO_PRICE)):
            p = copy.deepcopy(offers[0])
            p.update(id=1, account=account, price=price, priceCard=price)
            extra.append(p)
        self.trans["prices"] += extra
        self.assertEqual(pc.find_anomalies(self.trans), [])


class TestCycle(unittest.TestCase):
    @mock.patch.object(pc, "REQUEST_DELAY", 0)
    @mock.patch.object(pc, "http_get", lambda url: "")
    def test_alerts_once_per_offer_and_price(self):
        html = sample("prod_popular1_ea-fc-27.html")
        trans = pc.parse_game_page(html)
        standard = sorted((p for p in trans["prices"] if p["edition"] == "1" and not p["account"]
                           and p["price"] != pc.NO_PRICE), key=lambda p: p["priceCard"])
        standard[0]["priceCard"] = 10.0

        sent, state = [], {}
        targets = [("Popular", 1, "EA SPORTS FC 27", "https://example/")]
        with mock.patch.object(pc, "parse_game_page", lambda html: trans):
            pc.run_cycle(targets, sent.append, state)
            pc.run_cycle(targets, sent.append, state)
            self.assertEqual(len(sent), 1)
            self.assertIn("10.00 €", sent[0])

            standard[0]["priceCard"] = 9.0  # nouveau prix -> nouvelle alerte
            pc.run_cycle(targets, sent.append, state)
            self.assertEqual(len(sent), 2)


if __name__ == "__main__":
    unittest.main()
