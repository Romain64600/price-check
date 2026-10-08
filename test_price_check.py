"""Tests hors ligne, sur les fichiers de samples/ : python3 -m unittest -v"""

import copy
import datetime
import json
import os
import re
import time
import unittest
import urllib.request
from unittest import mock

import price_check as pc

SAMPLES = os.path.join(os.path.dirname(__file__), "samples")


def sample(name):
    with open(os.path.join(SAMPLES, name), encoding="utf-8") as f:
        return f.read()


def offer(**kw):
    base = {"id": 1, "merchant": 47, "merchantName": "Kinguin", "edition": "Standard",
            "region": "GLOBAL", "platform": "steam", "price": 10.0, "account": False}
    base.update(kw)
    return base


class TestAllKeyShopParsing(unittest.TestCase):
    def test_lists(self):
        targets = pc.parse_lists(json.loads(sample("api_topclick_all-popular_pc-soon.json")), pc.TOP_GAMES_LISTS)
        popular = [t[2] for t in targets if t[0] == "Popular"]
        soon = [t[2] for t in targets if t[0] == "Coming soon PC"]
        # Romain, 06/10/2026 : top 10 Popular + top 5 Coming soon PC (avant : 5 et 4)
        self.assertEqual(popular, ["EA SPORTS FC 27", "The Witcher 3 Wild Hunt", "CONTROL Resonant",
                                   "WARDOGS", "Valheim", "Bodycam", "The Blood Of Dawnwalker", "Onimusha Way of the Sword",
                                   "GTA 6 PS5", "How to Fish"])
        self.assertEqual(soon, ["Dynasty Warriors 3 Complete Edition Remastered", "Ace Combat 8",
                                "AION 2", "STAR WARS Galactic Racer", "Gears of War E-Day"])

    def test_lists_skip_non_games(self):
        data = json.loads(sample("api_topclick_sidebar.json"))
        targets = pc.parse_lists(data, [("sidebar.all.popular", "Popular", None)])
        self.assertTrue(targets)
        software = {i["name"] for i in data["sidebar"]["all.popular"]["items"] if i["productType"] != "game"}
        self.assertFalse(software & {t[2] for t in targets})

    def test_homepage_lists(self):
        data = json.loads(sample("api_topclick_home.json"))
        targets = pc.parse_lists(data, pc.HOMEPAGE_LISTS)
        self.assertEqual(len(targets), 415)
        self.assertEqual(len({t[3] for t in targets}), 415)  # une seule fois par page
        self.assertEqual(targets[0][:3], ("Home · Most anticipated", 1, "GTA 6 PS5"))
        self.assertEqual({t[0] for t in targets} - {label for _, label, _ in pc.HOMEPAGE_LISTS}, set())
        self.assertTrue(all(t[2] and t[3].startswith("https://www.allkeyshop.com/") for t in targets))

    def test_missing_list_is_skipped(self):
        data = json.loads(sample("api_topclick_all-popular_pc-soon.json"))
        with self.assertLogs(pc.log, level="WARNING"):
            targets = pc.parse_lists(data, [("sidebar.xbox.popular", "Xbox", 5)] + list(pc.TOP_GAMES_LISTS))
        self.assertEqual(len(targets), 15)

    def test_fetch_lists_in_batches_tolerates_a_failed_batch(self):
        full = json.loads(sample("api_topclick_home.json"))
        calls = []

        def fake_get(url, ua, follow=True, timeout=30):
            ids = re.findall(r"lists\[\]=([^&]+)", url)
            calls.append(ids)
            if "sidebar.xbox.popular" in ids:
                return 503, None, ""
            data = {}
            for lid in ids:
                w, n = lid.split(".", 1)
                data.setdefault(w, {})[n] = full[w][n]
            return 200, None, json.dumps(data)

        with mock.patch.object(pc, "http_get", side_effect=fake_get), mock.patch.object(pc, "PAGE_DELAY", 0), \
             self.assertLogs(pc.log, level="WARNING"):
            data = pc.fetch_lists([lid for lid, _, _ in pc.HOMEPAGE_LISTS])
        self.assertEqual(len(calls), 4)  # 20 listes par lots de 6
        self.assertTrue(all(len(c) <= pc.LISTS_PER_CALL for c in calls))
        self.assertIn("mostAnticipated", data)
        self.assertIn("all.popular", data["sidebar"])
        self.assertNotIn("xbox.popular", data["sidebar"])

    def test_game_page_matches_saved_json(self):
        trans = pc.parse_game_page(sample("prod_popular1_ea-fc-27.html"))
        self.assertEqual(trans["prices"], json.loads(sample("prod_popular1_gamePageTrans.json"))["prices"])

    def test_first_prices_one_per_edition(self):
        trans = json.loads(sample("prod_popular1_gamePageTrans.json"))
        firsts = pc.first_prices(trans)
        self.assertEqual([f["edition"] for f in firsts], ["Standard", "Standard + Bonus", "Ultimate", "Ultimate Plus"])
        standard = firsts[0]
        self.assertEqual((standard["merchantName"], standard["price"], standard["region"], standard["platform"]),
                         ("Mmoga", 54.99, "IN ENGLISH ONLY", "ea-app"))
        self.assertFalse(any(f["account"] for f in firsts))
        self.assertFalse(any(f["price"] == pc.NO_PRICE for f in firsts))

    def test_first_prices_ignore_sentinel_and_accounts(self):
        trans = json.loads(sample("prod_popular1_gamePageTrans.json"))
        cheap = copy.deepcopy(trans["prices"][0])
        cheap.update(id=1, edition="1", account=False, price=1.0, priceCard=1.0, dispo=1)
        sentinel = dict(cheap, id=2, price=pc.NO_PRICE, priceCard=pc.NO_PRICE)
        account = dict(cheap, id=3, account=True, price=0.5, priceCard=0.5)
        unavailable = dict(cheap, id=4, price=0.4, priceCard=0.4, dispo=0)
        trans["prices"] += [sentinel, account, unavailable]
        self.assertEqual(pc.first_prices(trans)[0]["merchantName"], "Mmoga")
        trans["prices"].append(cheap)
        self.assertEqual(pc.first_prices(trans)[0]["id"], 1)


class TestRedirection(unittest.TestCase):
    KINGUIN = "https://www.kinguin.net/category/609603/ea-sports-fc-27-pc-steam-altergift?r=3445&nosalesbooster=1&currency=EUR"

    def test_merchant_url_from_app_data(self):
        self.assertEqual(pc.merchant_url(sample("redirection_kinguin.html")), self.KINGUIN)

    def test_merchant_url_falls_back_to_meta_refresh(self):
        page = sample("redirection_kinguin.html").replace('id="appData"', 'id="other"')
        self.assertEqual(pc.merchant_url(page), self.KINGUIN)
        self.assertIsNone(pc.merchant_url("<html></html>"))

    def test_unwrap_affiliate(self):
        loaded = ("https://go.loaded.com/c/1297091/2640470/18216"
                  "?u=https%3A%2F%2Fwww.loaded.com%2Fea-sports-fc-27-standard-edition-pc-ea-app")
        self.assertEqual(pc.unwrap_affiliate(loaded), "https://www.loaded.com/ea-sports-fc-27-standard-edition-pc-ea-app")
        self.assertEqual(pc.unwrap_affiliate(self.KINGUIN), self.KINGUIN)  # r=3445 n'est pas une URL

    def test_url_text_drops_locale_segments(self):
        self.assertEqual(pc.url_text("https://www.instant-gaming.com/en/21656-buy-ea-sports-fc-27-pc-ea-app/?igr=1"),
                         "21656-buy-ea-sports-fc-27-pc-ea-app")
        self.assertEqual(pc.url_text("https://store.epicgames.com/en-US/p/fc-27-e149fb"), "p fc-27-e149fb")


class TestAnalyzeRealUrls(unittest.TestCase):
    """Les 28 URL marchand relevées le 30/09/2026 (samples/merchant_urls.json)."""

    def setUp(self):
        self.rows = json.loads(sample("merchant_urls.json"))

    def analyze(self, row):
        o = offer(merchantName=row["merchant"], edition=row["edition"], region=row["region"], platform=row["platform"])
        return pc.analyze(row["product"], o, pc.url_text(pc.unwrap_affiliate(row["url"])), "URL")

    def test_named_urls_are_ok(self):
        named = [r for r in self.rows if r["name_in_url"]]
        self.assertGreaterEqual(len(named), 25)
        for row in named:
            res = self.analyze(row)
            self.assertIsNotNone(res["match"], row["merchant"])
            self.assertEqual(res["reasons"], [], (row["merchant"], row["url"]))

    def test_steam_underscores(self):
        row = next(r for r in self.rows if r["merchant"] == "Steam")
        self.assertEqual(self.analyze(row)["match"], "exact")

    def test_epic_url_names_the_game_without_its_publisher_prefix(self):
        # « /p/fc-27-e149fb » : depuis le 01/10/2026, « FC 27 » est reconnu pour « EA SPORTS FC 27 »
        row = next(r for r in self.rows if r["merchant"] == "Epic Games")
        res = self.analyze(row)
        self.assertEqual((res["match"], res["reasons"]), ("exact", []))

    def test_ea_com_url_is_partial(self):
        row = next(r for r in self.rows if r["merchant"] == "EA.com")
        res = self.analyze(row)
        # « ea-sports-fc » + « fc-27 » : partiel jusqu'au 01/10/2026, exact depuis (« FC 27 » sans « EA Sports »)
        self.assertEqual((res["match"], res["reasons"]), ("exact", []))


class TestAnalyzeSuspects(unittest.TestCase):
    def reasons(self, product, url, **kw):
        return pc.analyze(product, offer(**kw), pc.url_text(url), "URL")["reasons"]

    def test_wrong_product(self):
        r = self.reasons("Sonic Racing CrossWorlds", "https://www.kinguin.net/category/1/sonic-the-hedgehog-pc-steam")
        self.assertEqual(r, ["another product at the merchant: « Sonic The Hedgehog » instead of « Sonic Racing CrossWorlds » (URL)"])

    def test_account_sold_as_key(self):
        r = self.reasons("EA SPORTS FC 27", "https://shop.example/ea-sports-fc-27-pc-steam-account-global")
        self.assertIn("account at the merchant, entered as a key", r)
        self.assertEqual(self.reasons("EA SPORTS FC 27", "https://shop.example/ea-sports-fc-27-account", account=True), [])

    def test_forbidden_region(self):
        r = self.reasons("EA SPORTS FC 27", "https://shop.example/ea-sports-fc-27-pc-steam-key-ru-cis")
        self.assertEqual(r, ["forbidden region: ru, cis"])

    def test_japan_allowed_on_playstation_only(self):
        # Romain, 08/10/2026 : « We will allow Japan for PlayStation, but PlayStation only » (Spider-Man 2 PS5, Kinguin JP
        # affichée JAPAN, offre 136075794)
        url = "https://kinguin.net/en/category/217145/marvel-s-spider-man-2-jp-ps5-cd-key"
        self.assertEqual(self.reasons("Marvel's Spider-Man 2 PS5", url, region="JAPAN", region_filter="JAPAN",
                                      platform="playstation-store"), [])
        # la page nomme la console, sans activationPlatform
        self.assertEqual(self.reasons("Marvel's Spider-Man 2 PS5", url, region="JAPAN", region_filter="JAPAN", platform=""), [])
        # une clé JP affichée autrement reste une erreur
        self.assertEqual(self.reasons("Marvel's Spider-Man 2 PS5", url, region="GLOBAL", platform="playstation-store"),
                         ["forbidden region: jp"])
        # Japon sur PC (Steam) : toujours interdit, même affiché JAPAN
        self.assertEqual(self.reasons("Elden Ring", "https://shop.example/elden-ring-pc-steam-key-japan",
                                      region="JAPAN", region_filter="STEAM JAPAN", platform="steam"),
                         ["forbidden region: japan"])
        # un autre pays sur PlayStation : interdit
        self.assertEqual(self.reasons("Marvel's Spider-Man 2 PS5", "https://shop.example/marvels-spider-man-2-ps5-kr",
                                      region="KOREA", region_filter="KOREA", platform="playstation-store"),
                         ["forbidden region: kr"])

    def test_region_family_mismatch(self):
        self.assertEqual(self.reasons("EA SPORTS FC 27", "https://shop.example/ea-sports-fc-27-steam-key-europe", region="GLOBAL"),
                         ["region: AllKeyShop GLOBAL, merchant EU"])
        # l'inverse est sans danger : une clé GLOBAL affichée EUROPE marche en Europe
        self.assertEqual(self.reasons("EA SPORTS FC 27", "https://shop.example/ea-sports-fc-27-steam-key-global", region="EUROPE"), [])
        self.assertEqual(self.reasons("EA SPORTS FC 27", "https://shop.example/ea-sports-fc-27-steam-key-europe", region="GIFT EU"), [])

    def test_us_key_allowed_global_everywhere_europe_on_console(self):
        # Romain, 08/10/2026 : « same for the US. It's allowed on AllKeyShop.com EU and US », « if you have a US offer on an
        # EURO page for PC it's not okay, but for console, it's okay to be displayed »
        pc_us = "https://shop.example/ea-sports-fc-27-pc-steam-key-united-states"
        self.assertEqual(self.reasons("EA SPORTS FC 27", pc_us, region="GLOBAL"), [])
        self.assertEqual(self.reasons("EA SPORTS FC 27", pc_us, region="EUROPE", region_filter="STEAM EU"),
                         ["region: AllKeyShop EUROPE, merchant US"])
        self.assertEqual(self.reasons("EA SPORTS FC 27", pc_us, region="GERMANY", region_filter="STEAM GERMANY"),
                         ["region: AllKeyShop GERMANY, merchant US"])
        console_us = "https://shop.example/ea-sports-fc-27-xbox-series-x-s-key-united-states"
        self.assertEqual(self.reasons("EA SPORTS FC 27 Xbox Series", console_us, region="EUROPE", region_filter="XBOX EU", platform="xbox"), [])
        self.assertEqual(self.reasons("EA SPORTS FC 27 Xbox Series", console_us, region="GLOBAL", platform="xbox"), [])
        self.assertEqual(self.reasons("EA SPORTS FC 27 PS5", "https://shop.example/ea-sports-fc-27-ps5-usa",
                                      region="EUROPE", region_filter="PSN EU", platform="playstation-store"), [])
        # un pays d'Europe affiché sur console : toujours comparé
        self.assertEqual(self.reasons("EA SPORTS FC 27 Xbox Series", console_us, region="GERMANY", region_filter="XBOX GERMANY CODE", platform="xbox"),
                         ["region: AllKeyShop GERMANY, merchant US"])
        # une clé Europe seule affichée GLOBAL : inchangé (Stellaris, formation du 30/09/2026)
        self.assertEqual(self.reasons("EA SPORTS FC 27", "https://shop.example/ea-sports-fc-27-steam-key-europe", region="GLOBAL"),
                         ["region: AllKeyShop GLOBAL, merchant EU"])

    def test_gift_region_has_no_geography(self):
        # Faux positif du 30/09/2026 : Screamer 2026 Deluxe chez K4G, « steam-europe-instant-altergift » affiché GIFT
        url = "https://k4g.com/product/screamer-steam-europe-instant-altergift-digital-deluxe-edition-alter-gift-X9G4J5WN?r=aks"
        res = pc.analyze("Screamer 2026", offer(edition="Deluxe", region="GIFT"), pc.url_text(url), "URL")
        self.assertEqual(res["reasons"], [])
        self.assertEqual(self.reasons("EA SPORTS FC 27", "https://shop.example/ea-sports-fc-27-steam-gift-global", region="GIFT"), [])
        self.assertEqual(self.reasons("EA SPORTS FC 27", "https://shop.example/ea-sports-fc-27-steam-gift-europe", region="GIFT"), [])

    def test_gift_region_is_not_compared(self):

        # K4G, 30/09/2026 (formation) : « steam-europe … altergift » affiché GIFT, c'est normal

        url = "https://k4g.com/product/screamer-steam-europe-instant-altergift-digital-deluxe-edition-alter-gift-X9G4J5WN?r=aks"

        self.assertEqual(self.reasons("Screamer 2026", url, edition="Deluxe", region="GIFT"), [])

        self.assertEqual(self.reasons("Screamer 2026", url, edition="Deluxe", region="GIFT EU"), [])

        self.assertEqual(self.reasons("Screamer 2026", url, edition="Deluxe", region="GLOBAL"),

                         ["region: AllKeyShop GLOBAL, merchant EU", "gift at the merchant, shown as a key GLOBAL"])


    def test_gift_sold_as_key(self):
        self.assertEqual(self.reasons("EA SPORTS FC 27", "https://shop.example/ea-sports-fc-27-steam-altergift", region="GLOBAL"),
                         ["gift at the merchant, shown as a key GLOBAL"])
        self.assertEqual(self.reasons("EA SPORTS FC 27", "https://shop.example/ea-sports-fc-27-steam-gift-global", region="GIFT"), [])

    def test_platform_mismatch(self):
        self.assertEqual(self.reasons("EA SPORTS FC 27", "https://shop.example/ea-sports-fc-27-pc-steam-key", platform="ea-app"),
                         ["platform: AllKeyShop ea-app, merchant steam"])
        self.assertEqual(self.reasons("EA SPORTS FC 27", "https://shop.example/ea-sports-fc-27-origin-key", platform="ea-app"), [])
        self.assertEqual(self.reasons("EA SPORTS FC 27", "https://shop.example/ea-sports-fc-27-xbox-pc-key", platform="xbox-play-anywhere"), [])
        self.assertEqual(self.reasons("EA SPORTS FC 27", "https://shop.example/ea-sports-fc-27-pc-key", platform="mystery-platform"), [])

    def test_edition_mismatch(self):
        # édition supérieure vendue sous Standard alors que la page a une édition Deluxe : erreur (arbitrage du 01/10/2026)
        self.assertEqual(self.reasons("EA SPORTS FC 27", "https://shop.example/ea-sports-fc-27-deluxe-edition-pc-steam", edition="Standard",
                                      page_editions=["Standard", "Deluxe"]),
                         ["edition: filed under Standard, the merchant sells deluxe (the page has the edition Deluxe)"])
        # pas d'édition Deluxe sur la page : l'acheteur a plus que ce qui est affiché, pas d'alerte
        self.assertEqual(self.reasons("EA SPORTS FC 27", "https://shop.example/ea-sports-fc-27-deluxe-edition-pc-steam", edition="Standard"), [])
        self.assertEqual(self.reasons("EA SPORTS FC 27", "https://shop.example/ea-sports-fc-27-standard-edition", edition="Ultimate"),
                         ["edition: AllKeyShop Ultimate, merchant standard"])
        self.assertEqual(self.reasons("EA SPORTS FC 27", "https://shop.example/ea-sports-fc-27-standard-edition", edition="Standard + Bonus"), [])
        self.assertEqual(self.reasons("EA SPORTS FC 27", "https://shop.example/ea-sports-fc-27-goty", edition="Game of the Year"), [])
        self.assertEqual(self.reasons("EA SPORTS FC 27", "https://shop.example/ea-sports-fc-27-digital-deluxe", edition="Deluxe"), [])

    def test_edition_words_of_the_product_name_are_ignored(self):
        product = "Dynasty Warriors 3 Complete Edition Remastered"
        self.assertEqual(self.reasons(product, "https://shop.example/dynasty-warriors-3-complete-edition-remastered-steam-key"), [])

    def test_dlc(self):
        self.assertEqual(self.reasons("Valheim", "https://shop.example/valheim-season-pass-dlc"),
                         ["additional content: dlc, season-pass"])

    def test_preorder_bonus_dlc_sold_with_the_game(self):
        # Driffle, 30/09/2026, édition AllKeyShop « Standard + Bonus »
        url = "https://www.driffle.com/ea-sports-fc-27-pre-order-bonus-dlc-global-pc-ea-play-digital-key-p10001977"
        self.assertEqual(self.reasons("EA SPORTS FC 27", url, edition="Standard + Bonus", platform="ea-app"), [])

    def test_goty_both_ways(self):
        # Instant Gaming, 30/09/2026, édition AllKeyShop « GOTY »
        url = "https://www.instant-gaming.com/en/1497-buy-key-gogcom-the-witcher-3-wild-hunt-goty/"
        self.assertEqual(self.reasons("The Witcher 3 Wild Hunt", url, edition="GOTY", platform="gog"), [])
        self.assertEqual(self.reasons("The Witcher 3 Wild Hunt", url, edition="Standard", platform="gog",
                                      page_editions=["Complete", "GOTY", "Standard", "Bundle"]),
                         ["edition: filed under Standard, the merchant sells goty (the page has the edition GOTY)"])

    def test_bundle_edition_has_another_name(self):
        # G2A, 30/09/2026, édition AllKeyShop « Bundle » : The Witcher Trilogy Pack
        url = "https://www.g2a.com/en/the-witcher-trilogy-pack-steam-gift-global-i10000000746004"
        res = pc.analyze("The Witcher 3 Wild Hunt", offer(edition="Bundle", region="GIFT"), pc.url_text(url), "URL")
        self.assertEqual((res["match"], res["reasons"], res["notes"]),
                         (None, [], ["edition Bundle: name not fully checked (one word of the name present)"]))
        res = pc.analyze("The Witcher 3 Wild Hunt", offer(edition="Bundle", region="GLOBAL"), pc.url_text(url), "URL")
        self.assertEqual(res["reasons"], ["gift at the merchant, shown as a key GLOBAL"])
        # audit du 02/10/2026 : sans un mot du nom, un lot n'est pas blanchi (Sonic en « Starter Pack » sur The Witcher 3)
        sonic = "https://www.g2a.com/sonic-the-hedgehog-4-episode-1-steam-key-global-i10000000001"
        for edition in ("Starter Pack", "Bundle", "Complete Collection"):
            res = pc.analyze("The Witcher 3 Wild Hunt", offer(edition=edition), pc.url_text(sonic), "URL")
            self.assertTrue(res["reasons"] and res["reasons"][0].startswith("another product at the merchant"), (edition, res["reasons"]))
        # DREDGE vendu « dredging-the-depths-bundle » : un mot du nom par ses 5 premières lettres suffit
        self.assertEqual(pc.analyze("DREDGE", offer(edition="Bundle"), pc.url_text("https://shop.example/dredging-the-depths-bundle"),
                                    "URL")["reasons"], [])

    def test_language_restriction_is_fine(self):
        self.assertEqual(self.reasons("EA SPORTS FC 27", "https://www.mmoga.com/EA-Games/EA-SPORTS-FC-27-EA-App-English-Only.html",
                                      region="IN ENGLISH ONLY", platform="ea-app"), [])

    def test_roman_numerals_both_ways(self):
        # GameBoost et YUPLAY, 30/09/2026 : « Minecraft Dungeons 2 » écrit « II »
        for url in ("https://gameboost.com/minecraft-dungeons-ii-xbox-series-x-pc-xbox-live-key-global-00-71537?cmpid=206",
                    "https://www.yuplay.com/product/minecraft-dungeons-ii-xbox-series-xs-and-xbox-on-pc/?partner=2334725"):
            self.assertEqual(self.reasons("Minecraft Dungeons 2", url, region="XBOX/PC", platform="xbox-play-anywhere"), [])
        self.assertEqual(self.reasons("Final Fantasy VII Rebirth", "https://shop.example/final-fantasy-7-rebirth-pc-steam"), [])
        with mock.patch.object(pc, "product_aliases", lambda: {}):  # sans l'alias du titre Steam complet (07/10)
            self.assertEqual(pc.name_variants("Ace Combat 8"), ("Ace Combat 8", "ace combat viii"))

    def test_platform_suffix_of_the_product_name(self):
        # PS Store US, 30/09/2026 : « GTA 6 PS5 », URL EP1004-PPSA01547_00-GTAVIULTIMATE001, titre sans « PS5 »
        url = "https://store.playstation.com/en-us/product/EP1004-PPSA01547_00-GTAVIULTIMATE001?partner=allkeyshopcom"
        # depuis l'audit du 02/10/2026, le nom n'est plus cherché au milieu d'un mot : « gtavi » dans « gtaviultimate001 »
        # ne prouve rien (« ron » était trouvé dans « iron ») ; au PS Store, c'est le JSON de la page qui décide
        self.assertEqual(self.reasons("GTA 6 PS5", url, edition="Ultimate", region="PS5", platform="playstation-store"),
                         ["product name not found (URL)"])
        res = pc.analyze("GTA 6 PS5", offer(edition="Ultimate", region="PS5", platform="playstation-store"),
                         "Grand Theft Auto VI Ultimate Edition | PlayStation Store", "page title")
        self.assertEqual((res["match"], res["reasons"]), ("exact", []))
        self.assertIn("gta 6", pc.name_variants("GTA 6 PS5"))
        self.assertIn("ea sports fc 27", pc.name_variants("EA SPORTS FC 27 Xbox Series"))
        self.assertNotIn("", pc.name_variants("PC"))

    def test_unknown_aks_edition_is_not_compared(self):
        # Dreamgame EU, 30/09/2026 : édition AllKeyShop « Preorder bonus », URL standard-edition-pre-purchase
        url = "https://www.dreamgame.com/en/call-of-duty-modern-warfare-4-standard-edition-pre-purchase?affiliate=allkeyshop"
        self.assertEqual(self.reasons("Call of Duty Modern Warfare 4", url, edition="Preorder bonus",
                                      region="XBOX/PC", platform="xbox-play-anywhere"), [])
        self.assertEqual(self.reasons("WARDOGS", "https://shop.example/wardogs-deluxe-edition-pc-steam", edition="Early Access"), [])
        self.assertEqual(self.reasons("WARDOGS", "https://shop.example/wardogs-deluxe-edition-pc-steam", edition="Standard",
                                      page_editions=["Standard", "Deluxe"]),
                         ["edition: filed under Standard, the merchant sells deluxe (the page has the edition Deluxe)"])
        # une édition supérieure affichée, l'édition de base vendue : alerte aussi
        self.assertEqual(self.reasons("WARDOGS", "https://shop.example/wardogs-standard-edition-pc-steam", edition="Deluxe"),
                         ["edition: AllKeyShop Deluxe, merchant standard"])

    def test_edition_announcing_dlc(self):
        # Kinguin, 30/09/2026 : édition AllKeyShop « Standard + DLC Bundle »
        url = "https://www.kinguin.net/en/category/553797/hunt-showdown-1896-10-dlc-bundle-pc-steam-cd-key?r=3445"
        self.assertEqual(self.reasons("Hunt Showdown", url, edition="Standard + DLC Bundle"), [])
        self.assertEqual(self.reasons("Hunt Showdown", url, edition="Standard"), ["additional content: dlc"])

    def test_language_lists_are_not_regions(self):
        # GAMIVO, 30/09/2026 : les langues de la clé dans l'URL, dont « ru » et « tr »
        url = "https://www.gamivo.com/product/rimworld-stareter-pack-pc-steam-global-en-de-fr-it-pl-cs-nl-ja-ko-no-pt-ru-zh-es-sv-tr-zh-hu-da-ro-fi-uk-standard"
        self.assertEqual(self.reasons("RimWorld", url, edition="Starter Pack"), [])
        url = "https://www.gamivo.com/product/age-of-wonders-4-pc-steam-global-en-de-fr-pl-ja-ko-ru-zh-es-standard"
        self.assertEqual(self.reasons("Age of Wonders 4", url), [])
        self.assertEqual(self.reasons("Age of Wonders 4", "https://shop.example/age-of-wonders-4-steam-key-ru-cis"),
                         ["forbidden region: ru, cis"])
        self.assertEqual(self.reasons("Age of Wonders 4", "https://shop.example/age-of-wonders-4-pc-eu-key", region="GLOBAL"),
                         ["region: AllKeyShop GLOBAL, merchant EU"])

    def test_soundtrack_edition(self):
        # Steam, 30/09/2026 : Stray « Soundtrack Edition »
        res = pc.analyze("Stray", offer(edition="Soundtrack Edition"), "Stray + Soundtrack Bundle on Steam", "page title")
        self.assertEqual(res["reasons"], [])
        self.assertEqual(self.reasons("Stray", "https://shop.example/stray-soundtrack-dlc", edition="Standard"),
                         ["additional content: dlc, soundtrack"])

    def test_long_name_tolerates_one_missing_word(self):
        # Amazon.fr, 30/09/2026 : URL tronquée « Nintendo-Legend-Zelda-Kingdom-Collector »
        product = "The Legend of Zelda Tears of the Kingdom Nintendo Switch"
        o = offer(edition="Collector", region="BOX", platform="physical-medium")
        res = pc.analyze(product, o, pc.url_text("https://www.amazon.fr/Nintendo-Legend-Zelda-Kingdom-Collector/dp/B0BVBNDRL9/"), "URL")
        self.assertEqual((res["match"], res["reasons"]), ("partial", []))
        res = pc.analyze(product, o, pc.url_text("https://www.amazon.fr/Nintendo-Legend-Zelda-Tears-Kingdom/dp/B0BVW3SJMF/"), "URL")
        self.assertEqual((res["match"], res["reasons"]), ("partial", []))
        # mais un numéro qui manque, c'est un autre jeu
        self.assertEqual(self.reasons("Call of Duty Modern Warfare 4", "https://shop.example/call-of-duty-modern-warfare-3-pc"),
                         ["another product at the merchant: « Call Of Duty Modern Warfare 3 » instead of « Call of Duty Modern Warfare 4 » (URL)"])
        self.assertEqual(self.reasons("Red Dead Redemption 2", "https://shop.example/red-dead-redemption-pc-rockstar-key",
                                      platform="rockstar"), ["another product at the merchant: « Red Dead Redemption » instead of « Red Dead Redemption 2 » (URL)"])

    def test_console_names_and_stores(self):
        # Instant Gaming et Playerland, 30/09/2026 : « microsoft-store » dans l'URL d'une offre Xbox
        url = "https://www.instant-gaming.com/en/23587-buy-ea-sports-fc-27-ultimate-edition-xbox-series-x-s-xbox-one-microsoft-store/"
        self.assertEqual(self.reasons("EA SPORTS FC 27 Xbox Series", url, edition="Ultimate + Bonus", region="XBOX X|S", platform="xbox"), [])
        url = "https://www.instant-gaming.com/en/22985-buy-fable-pc-xbox-series-x-s-microsoft-store/"
        self.assertEqual(self.reasons("Fable Xbox Series", url, edition="Preorder bonus", region="XBOX/PC", platform="xbox-play-anywhere"), [])
        # Eneba préfixe ses URL par « steam- » même pour une clé Xbox Live
        url = "https://www.eneba.com/steam-nba-2k26-superstar-edition-xbox-series-x-s-xbox-live-key-europe"
        self.assertEqual(self.reasons("NBA 2K26 Xbox Series", url, edition="Superstar Edition", region="EU IN ENGLISH ONLY", platform="xbox"), [])
        # mais une clé Steam affichée EA App reste suspecte
        self.assertEqual(self.reasons("EA SPORTS FC 26", "https://www.gamingdragons.com/en/game/buy-ea-sports-fc-26-ultimate-edition-steam-key.html",
                                      edition="Ultimate", platform="ea-app"), ["platform: AllKeyShop ea-app, merchant steam"])

    def test_suffixes_year_vr_switch2_and_acronyms(self):
        self.assertEqual(self.reasons("Screamer 2026", "https://store.steampowered.com/app/2814990/Screamer/"), [])
        self.assertEqual(self.reasons("Ragnarock VR", "https://www.kinguin.net/category/89340/ragnarock-steam-cd-key"), [])
        self.assertEqual(self.reasons("Metro Awakening VR", "https://gameboost.com/metro-awakening-deluxe-edition-pc-steam-key-global-00-17545",
                                      edition="Deluxe"), [])
        self.assertEqual(self.reasons("Azure Striker Gunvolt Trilogy Enhanced Nintendo Switch 2",
                                      "https://www.nintendo.com/de-de/Spiele/Nintendo-Switch-Download-Software/Azure-Striker-Gunvolt-Trilogy-Enhanced-3000000.html",
                                      region="GLOBAL", platform="nintendo-eshop"), [])
        self.assertEqual(self.reasons("S.T.A.L.K.E.R. 2 Heart of Chornobyl",
                                      "https://www.g2a.com/en/stalker-2-heart-of-chernobyl-deluxe-edition-pc-steam-key-global-i10000255894008",
                                      edition="Deluxe"), [])
        self.assertEqual(self.reasons("MXGP 26 The Official Game Xbox Series",
                                      "https://www.xbox.com/de-de/games/store/mxgp-26-fox-holeshot-edition/9p730qptxz4q",
                                      edition="Fox Holeshot Edition", region="XBOX X|S", platform="xbox"), [])
        self.assertEqual(pc.norm("S.T.A.L.K.E.R. 2"), "stalker-2")
        self.assertIn("screamer", pc.name_variants("Screamer 2026"))

    def test_short_page_title_contained_in_the_name(self):
        # PS Store US, 30/09/2026 : <title> « UFC® 5 » pour « EA Sports UFC 5 PS5 »
        o = offer(region="PS5", platform="playstation-store")
        res = pc.analyze("EA Sports UFC 5 PS5", o, "UFC® 5 | Access Denied", "page title")
        self.assertEqual((res["match"], res["reasons"]), ("exact", []))  # « UFC 5 » sans « EA Sports », depuis le 01/10/2026
        res = pc.analyze("Ace Combat 8 Wings of Theve", o, "Wings of Theve | Bandai Namco", "page title")
        self.assertEqual((res["match"], res["reasons"]), ("partial", []))  # titre court contenu dans le nom
        res = pc.analyze("Pokemon Sword Nintendo Switch", o, "Pokémon Shield | Nintendo", "page title")
        self.assertIsNone(res["match"])
        res = pc.analyze("Sonic Racing CrossWorlds", o, "Sonic | SEGA", "page title")
        self.assertIsNone(res["match"])  # un seul mot, pas assez

    def test_dlc_page(self):
        # Driffle, 30/09/2026 (formation) : la page AllKeyShop de Diablo 4 Lord of Hatred est un DLC
        url = "https://www.driffle.com/diablo-iv-lord-of-hatred-ultimate-edition-dlc-global-xbox-one-xbox-series-xs-xbox-live-digital-key-p9990076"
        o = offer(edition="Ultimate", region="XBOX X|S", platform="xbox", page_dlc=True)
        self.assertEqual(pc.analyze("Diablo 4 Lord of Hatred Xbox Series", o, pc.url_text(url), "URL")["reasons"], [])
        o["page_dlc"] = False
        self.assertEqual(pc.analyze("Diablo 4 Lord of Hatred Xbox Series", o, pc.url_text(url), "URL")["reasons"], ["additional content: dlc"])
        trans = {"editions": {"1": {"name": "Standard"}, "16": {"name": "DLC"}, "21": {"name": "Ultimate"}}}
        self.assertTrue(pc.is_dlc_page(trans, "Diablo 4 Lord of Hatred Xbox Series"))
        self.assertFalse(pc.is_dlc_page({"editions": {"1": {"name": "Standard"}}}, "Diablo 4"))
        self.assertTrue(pc.is_dlc_page({"editions": {}}, "Farming Simulator 25 Year 1 Season Pass"))

    def test_wrong_product_dredge_doom(self):
        # Greenmangaming, 30/09/2026 : DOOM The Dark Ages en premier prix « Premium » de la page DREDGE
        self.assertEqual(self.reasons("DREDGE", "https://www.greenmangaming.com/games/doom-the-dark-ages-premium-edition-pc/",
                                      edition="Premium"), ["another product at the merchant: « Doom The Dark Ages Premium Edition » instead of « DREDGE » (URL)"])

    def test_alias(self):
        self.assertEqual(self.reasons("GTA 6", "https://shop.example/grand-theft-auto-vi-ps5", platform="playstation"), [])
        # Wyrel, 30/09/2026 : « GTA 6 PS5 » écrit « grand-theft-auto-vi-ps5 »
        url = "https://wyrel.com/en/buy-cheap-grand-theft-auto-vi-ps5-193995?referal=allkeyshop&marketplace_id=5"
        self.assertEqual(self.reasons("GTA 6 PS5", url, region="EUROPE", platform="playstation-store"), [])
        self.assertEqual(self.reasons("Grand Theft Auto V", "https://shop.example/gta-5-pc-rockstar-key", platform="rockstar"), [])
        self.assertEqual(self.reasons("Call of Duty Black Ops 7", "https://shop.example/cod-black-ops-7-pc-steam"), [])
        self.assertIn("grand theft auto vi ps5", pc.name_variants("GTA 6 PS5"))

    def test_partial_name(self):
        res = pc.analyze("The Witcher 3 Wild Hunt", offer(page_editions=["Standard", "GOTY"]), "witcher-3-wild-hunt-goty-steam-key", "URL")
        self.assertEqual((res["match"], res["notes"]), ("partial", ["partial name"]))
        self.assertEqual(res["reasons"], ["edition: filed under Standard, the merchant sells goty (the page has the edition GOTY)"])


class TestPageTitle(unittest.TestCase):
    def test_title_from_html(self):
        dom = ('<html><head><title>EA Sports FC 27 PC Steam Altergift | Buy cheap on Kinguin.net</title>'
               '<meta property="og:title" content="EA Sports FC 27 PC Steam Altergift"></head>'
               '<body><h1>EA Sports <b>FC 27</b> PC Steam Altergift</h1></body></html>')
        title = pc.page_title_from_html(dom)
        self.assertIn("EA Sports FC 27 PC Steam Altergift | Buy cheap on Kinguin.net", title)
        self.assertNotIn("<b>", title)
        self.assertIsNone(pc.page_title_from_html("<html></html>"))

    def test_page_title_without_chromium(self):
        with mock.patch.object(pc.shutil, "which", return_value=None):
            self.assertIsNone(pc.page_title("https://shop.example/"))

class TestMerchantConfigs(unittest.TestCase):
    def test_repo_configs_load(self):
        names = {c["name"] for c in pc.load_merchant_configs()}
        self.assertTrue({"Wyrel", "Amazon"} <= names)

    def test_matching_by_host_or_name(self):
        self.assertEqual(pc.merchant_config("https://wyrel.com/en/buy-cheap-x-1?region=1", "Wyrel")["name"], "Wyrel")
        self.assertEqual(pc.merchant_config("https://www.amazon.fr/dp/B0/", "Amazon.fr")["name"], "Amazon")
        self.assertEqual(pc.merchant_config("https://shop.example/x", "Wyrel")["name"], "Wyrel")  # par nom AllKeyShop
        self.assertEqual(pc.merchant_config("https://www.kinguin.net/x", "Kinguin")["name"], "Kinguin")  # depuis le 01/10/2026
        self.assertEqual(pc.merchant_config("https://www.gamivo.com/product/x", "GAMIVO"), {})
        self.assertEqual(pc.merchant_config("", "Amazon.de")["name"], "Amazon")  # par début de nom

    def test_wyrel_region_from_query(self):
        # Formation du 30/09/2026 : le slug dit « -eu- », la page (paramètre region=1) dit Global
        url = ("https://wyrel.com/en/buy-cheap-grand-theft-auto-v-and-criminal-enterprise-starter-pack-bundle-eu-37543"
               "?referal=allkeyshop&marketplace_id=11&edition_id=780&region=1")
        cfg = pc.merchant_config(url, "Wyrel")
        o = offer(merchantName="Wyrel", edition="Standard + DLC", region="GLOBAL", platform="rockstar")
        res = pc.analyze("GTA 5", o, pc.url_text(url), "URL", region=pc.region_text(url, cfg))
        self.assertEqual(res["reasons"], [])
        res = pc.analyze("GTA 5", o, pc.url_text(url.replace("region=1", "region=4")), "URL",
                         region=pc.region_text(url.replace("region=1", "region=4"), cfg))
        self.assertEqual(res["reasons"], ["region: AllKeyShop GLOBAL, merchant EU"])
        url2 = "https://wyrel.com/en/buy-cheap-ea-sports-fc-27-pc-196673?referal=allkeyshop&marketplace_id=2&edition_id=780&region=4"
        res = pc.analyze("EA SPORTS FC 27", offer(merchantName="Wyrel", region="GIFT EU"), pc.url_text(url2), "URL",
                         region=pc.region_text(url2, cfg))
        self.assertEqual(res["reasons"], [])
        self.assertEqual(pc.region_text(url.replace("region=1", "region=9"), cfg), "")  # inconnue : pas de contrôle
        # region=5 = ROW : normal pour une offre affichée ROW, suspect pour une offre affichée GLOBAL
        url5 = "https://wyrel.com/en/buy-cheap-kingdom-come-deliverance-ii-pc-146507?referal=allkeyshop&region=5"
        for region, expected in (("ROW", []), ("GLOBAL", ["region: AllKeyShop GLOBAL, merchant ROW"])):
            res = pc.analyze("Kingdom Come Deliverance 2", offer(merchantName="Wyrel", region=region), pc.url_text(url5), "URL",
                             region=pc.region_text(url5, cfg))
            self.assertEqual(res["reasons"], expected, region)

    def test_alternate_url(self):
        page = ('<html><head><link rel="alternate" hreflang="fr-FR" href="https://www.nintendo.com/fr-fr/x.html">'
                '<link href="https://www.nintendo.com/en-gb/Games/Nintendo-Switch-download-software/Metal-Garden-3177422.html" '
                'rel="alternate" hreflang="en-GB"></head></html>')
        self.assertEqual(pc.alternate_url(page, "en-GB"), "https://www.nintendo.com/en-gb/Games/Nintendo-Switch-download-software/Metal-Garden-3177422.html")
        self.assertIsNone(pc.alternate_url("<html></html>", "en-GB"))

    @mock.patch.object(pc, "REQUEST_DELAY", 0)
    def test_nintendo_name_checked_on_the_english_page(self):
        # Nintendo eShop FR, 30/09/2026 : TORO 2 renvoie sur Metal Garden ; Le Chat Chapeauté est bien The Cat in the Hat
        cases = [("TORO 2 Nintendo Switch", "Metal-Garden-3177422", "Metal-Garden-3177422", "SUSPECT"),
                 ("The Cat in the Hat Rainy Day Mayhem Nintendo Switch", "Le-Chat-Chapeaute-Pagaille-sous-la-pluie-3110607",
                  "The-Cat-in-the-Hat-Rainy-Day-Mayhem-3110607", "OK")]
        for product, fr_slug, en_slug, expected in cases:
            fr = "https://www.nintendo.com/fr-fr/Jeux/Jeux-Nintendo-Switch/%s.html" % fr_slug
            page = TestCheckOffer.INTERSTITIAL.replace(TestRedirection.KINGUIN, fr).replace(
                TestRedirection.KINGUIN.replace("/", "\\/"), fr.replace("/", "\\/"))
            nintendo = '<html><head><link rel="alternate" hreflang="en-GB" href="https://www.nintendo.com/en-gb/Games/Nintendo-Switch-games/%s.html"></head></html>' % en_slug
            with mock.patch.object(pc, "http_get", side_effect=[(200, None, page), (200, None, nintendo)]), \
                 mock.patch.object(pc, "page_title") as title:
                res = pc.check_offer(product, offer(merchantName="Nintendo eShop FR", region="GLOBAL", platform="nintendo-eshop"))
            title.assert_not_called()
            self.assertEqual((res["verdict"], res["method"]), (expected, "URL of the en-GB version"), product)
            if expected == "SUSPECT":  # le message dit ce que vend le marchand (formation du 01/10/2026)
                self.assertEqual(res["reasons"], ["another product at the merchant: « Metal Garden » instead of « TORO 2 Nintendo Switch » (URL of the en-GB version)"])

    @mock.patch.object(pc, "REQUEST_DELAY", 0)
    def test_amazon_never_opens_a_browser(self):
        page = TestCheckOffer.INTERSTITIAL.replace(TestRedirection.KINGUIN, "https://www.amazon.fr/Nintendo-Zelda/dp/B0BVW3SJMF/").replace(
            TestRedirection.KINGUIN.replace("/", "\\/"), "https://www.amazon.fr/Nintendo-Zelda/dp/B0BVW3SJMF/".replace("/", "\\/"))
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, page), (200, None, ""), (200, None, "<title>Amazon.fr</title>")]), \
             mock.patch.object(pc, "page_title") as title:
            res = pc.check_offer("The Legend of Zelda Tears of the Kingdom Nintendo Switch",
                                 offer(merchantName="Amazon.fr", region="BOX", platform="physical-medium"))
        title.assert_not_called()
        self.assertEqual(res["verdict"], "À VÉRIFIER")


class TestStudy20260930(unittest.TestCase):
    """Étude des 80 reports du 30/09/2026 (docs/precedents.md) : chaque cas réel, avec la région telle
    qu'AllKeyShop la définit (nom affiché + nom de filtre)."""

    def reasons(self, product, url, **kw):
        return pc.analyze(product, offer(**kw), pc.url_text(pc.unwrap_affiliate(url)), "URL")["reasons"]

    # -- vraies erreurs : doivent sortir --
    def test_eu_key_shown_global(self):
        self.assertEqual(self.reasons("Stellaris", "https://kinguin.net/category/172478/stellaris-starter-pack-eu-steam-cd-key",
                                      edition="Bundle 1", region="GLOBAL", region_filter="STEAM GLOBAL"),
                         ["region: AllKeyShop GLOBAL, merchant EU"])
        self.assertEqual(self.reasons("The Blood Of Dawnwalker",
                                      "https://www.eneba.com/steam-the-blood-of-dawnwalker-eclipse-edition-deluxe-steam-key-pc-europe",
                                      edition="Deluxe", region="GLOBAL", region_filter="STEAM GLOBAL"),
                         ["region: AllKeyShop GLOBAL, merchant EU"])

    def test_wrong_platform(self):
        self.assertEqual(self.reasons("F1 25", "https://www.gamivo.com/product/f1-25-xbox-xbox-series-eu-2026-season",
                                      edition="2026 Season Edition", region="EU ENGLISH ONLY", region_filter="STEAM EU EN ONLY"),
                         ["platform: AllKeyShop steam, merchant xbox"])
        self.assertEqual(self.reasons("EA SPORTS FC 26", "https://www.driffle.com/ea-sports-fc-26-icons-edition-global-pc-steam-digital-key-p9990128",
                                      edition="ICONS Edition", region="GLOBAL", region_filter="EA GLOBAL", platform="ea-app"),
                         ["platform: AllKeyShop ea-app, merchant steam"])

    def test_platform_from_region_filter(self):
        # activationPlatform vide, mais la région dit EA GLOBAL
        self.assertEqual(self.reasons("EA SPORTS FC 26", "https://shop.example/ea-sports-fc-26-pc-steam-key",
                                      region="GLOBAL", region_filter="EA GLOBAL", platform=""),
                         ["platform: AllKeyShop ea, merchant steam".replace("ea,", "ea-app,")])

    def test_console_of_the_page(self):
        # Amazon.fr, Elden Ring Xbox Series : l'URL parle de PlayStation
        url = "https://www.amazon.fr/Bandai-Namco-Entertainment-3391892017632-PlayStation/dp/B0977LKSQ6/"
        res = pc.analyze("Elden Ring Xbox Series", offer(edition="Launch Edition", region="BOX", region_filter="BOX", platform="physical-medium"),
                         pc.url_text(url), "URL")
        self.assertIn("platform: AllKeyShop page Xbox, merchant PlayStation", res["reasons"])
        # la bonne console, ou une URL qui n'en parle pas : rien
        self.assertEqual(self.reasons("GTA The Trilogy The Definitive Edition Xbox Series", "https://www.amazon.fr/GTA-Trilogy-Definition-Xbox-X/dp/B09KGZ37M1/",
                                      region="BOX", platform="physical-medium"), [])

    def test_year_one_season_pass_is_the_year_one_edition(self):
        # Loaded, « Year 1 Season Pass » rangé dans « Year 1 Edition » : signalé le 30/09, faux positif selon
        # l'arbitrage du 01/10/2026 (« le jeu est bien inclus, l'offre est bien rentrée »)
        fs25 = ["Standard", "Highlands Fishing Edition", "Year 1 Bundle", "Year 1 Edition"]
        url = "https://www.loaded.com/farming-simulator-25-year-1-season-pass-pc-steam"
        self.assertEqual(self.reasons("Farming Simulator 25", url, edition="Year 1 Edition", page_editions=fs25), [])
        # le même pass rangé ailleurs, ou le pass d'une autre année, reste du contenu additionnel ; rangé en Standard, il est
        # aussi dans la mauvaise édition (06/10/2026 : les éditions de la page nommées par l'URL)
        self.assertEqual(self.reasons("Farming Simulator 25", url, edition="Standard", page_editions=fs25),
                         ["edition: filed under Standard, the merchant sells year (the page has the edition Year 1 Bundle or Year 1 Edition)",
                          "additional content: season-pass"])
        self.assertEqual(self.reasons("Farming Simulator 25", "https://www.loaded.com/farming-simulator-25-year-2-season-pass-pc-steam",
                                      edition="Year 1 Edition", page_editions=fs25),
                         ["additional content: season-pass"])

    def test_edition_misfiled_while_the_page_has_it(self):
        # arbitrage du 01/10/2026 : mauvaise édition = erreur, même si l'acheteur reçoit plus (annule la formation
        # du matin, qui tenait GTA 4 pour un faux positif)
        gta4 = ["Complete", "Standard", "Collection", "Complete Bundle", "Complete Pack", "Bundle"]
        res = pc.analyze("GTA 4", offer(edition="Standard", page_editions=gta4), "Grand Theft Auto IV: The Complete Edition on Steam", "page title")
        self.assertEqual(res["reasons"], ["edition: filed under Standard, the merchant sells complete (the page has the edition Complete)"])
        zero = ["Standard", "Deluxe", "Deluxe + Bonus", "Bonus", "Standard + DLC"]
        self.assertEqual(self.reasons("STAR WARS Zero Company Xbox Series",
                                      "https://www.gamivo.com/product/star-wars-zero-company-xbox-xbox-series-global-deluxe-pre-order-bonus",
                                      edition="Standard + DLC", region="XBOX X|S", region_filter="XBOX X|S GLOBAL", platform="xbox", page_editions=zero),
                         ["edition: filed under Standard + DLC, the merchant sells deluxe (the page has the edition Deluxe)"])
        # la page n'a pas l'édition vendue : rien de mieux où ranger l'offre, pas d'alerte
        alone = pc.analyze("GTA 4", offer(edition="Standard", page_editions=["Standard"]),
                           "Grand Theft Auto IV: The Complete Edition on Steam", "page title")
        self.assertEqual(alone["reasons"], [])

    # -- faux positifs : ne doivent plus sortir --
    def test_gift_germany_is_a_gift(self):
        # Kinguin Big Walk : région « GERMANY » = STEAM GIFT GERMANY, le marchand vend un gift DE
        self.assertEqual(self.reasons("Big Walk", "https://www.kinguin.net/category/713896/big-walk-de-pc-steam-altergift",
                                      region="GERMANY", region_filter="STEAM GIFT GERMANY"), [])

    def test_wider_zone_is_fine(self):
        # K4G Mario Kart World : clé GLOBAL affichée EUROPE
        self.assertEqual(self.reasons("Mario Kart World Nintendo Switch 2",
                                      "https://k4g.com/product/mario-kart-world-nintendo-switch-2-global-instant-cd-key-cd-key-6N1RIW9B",
                                      region="EUROPE", region_filter="EUROPE", region_desc="NINTENDO Download Code for Europe.", platform="nintendo-eshop"), [])

    def test_playstation_titles(self):
        o = offer(region="PS5", region_filter="PS5", platform="playstation-store")
        for product, edition, title in (
                ("Gran Turismo 7 PS5", "Deluxe", "Gran Turismo™ 7 25th Anniversary Digital Deluxe Edition | Digital Deluxe Edition"),
                ("DRAGON QUEST MONSTERS The Withered World PS5", "Deluxe", "DRAGON QUEST MONSTERS: The Withered World - Digital Deluxe Edition | Digital Deluxe Edition"),
                ("Attack on Titan 3 PS5", "Deluxe", "A.O.T. 3 Digital Deluxe Edition | Digital Deluxe Edition"),
                ("Crimson Desert PS5", "Standard", "Crimson Desert Enhanced | Standard Edition")):
            o["edition"] = edition
            o["page_editions"] = ["Standard", "Enhanced", "Deluxe"]
            res = pc.analyze(product, o, title, "page title")
            self.assertIsNotNone(res["match"], product)
            self.assertEqual(res["reasons"], [], product)

    def test_playstation_parser_and_locale(self):
        page = ('<script>{"Product:UP4162-PPSA25286_00-0653729629077452":{"id":"UP4162-PPSA25286_00-0653729629077452",'
                '"__typename":"Product","concept":{"__ref":"Concept:10002363"},"edition":{"__typename":"ProductEdition",'
                '"name":"Standard Edition"},"name":"Crimson Desert Enhanced","description":"x"}}</script><title>Crimson Desert Enhanced</title>')
        self.assertEqual(pc.playstation_text(page, "https://store.playstation.com/en-us/product/UP4162-PPSA25286_00-0653729629077452"),
                         "Crimson Desert Enhanced | Standard Edition")
        cfg = pc.merchant_config("https://store.playstation.com/es-es/product/X", "PS Store ES")
        seen = []
        with mock.patch.object(pc, "http_get", side_effect=lambda url, ua, follow=True, timeout=30: (seen.append(url), (200, None, page))[1]), \
             mock.patch.object(pc, "REQUEST_DELAY", 0):
            text, method = pc.merchant_page_text("https://store.playstation.com/es-es/product/EP9001-PPSA01316_00-GT7DDE0000000PS5", cfg)
        self.assertEqual(seen, ["https://store.playstation.com/en-gb/product/EP9001-PPSA01316_00-GT7DDE0000000PS5"])
        self.assertEqual(method, "page (HTTP)")

    def test_accent_eaten_by_the_merchant(self):
        # Dreamgame : « Pokémon » devient « pokmon »
        self.assertEqual(self.reasons("Pokemon Legends: Z-A Mega Dimension Nintendo Switch 2",
                                      "https://www.dreamgame.com/en/pokmon-legends-z-a-mega-dimension-dlc",
                                      edition="DLC", region="EUROPE", platform="nintendo-eshop", page_dlc=True), [])

    def test_one_letter_tolerance_is_narrow(self):
        self.assertEqual(self.reasons("Portal 2", "https://shop.example/mortal-kombat-2-pc-steam"),
                         ["another product at the merchant: « Mortal Kombat 2 » instead of « Portal 2 » (URL)"])
        self.assertEqual(self.reasons("Horizon Forbidden West Complete Edition", "https://shop.example/horizon-forbidden-west-complete-edition-pc-steam",
                                      edition="Complete"), [])
        self.assertFalse(pc.is_block_page("Horizon Forbidden West™ Complete Edition"))

    def test_full_replay_regressions(self):
        # rejeu des 920 offres en tête, 30/09/2026 : trois faux positifs créés par les nouvelles règles
        self.assertEqual(self.reasons("STAR WARS Galactic Racer", "https://gameseal.com/star-wars-galactic-racer-pc-steam-key-eu-na",
                                      region="EU/US", region_filter="EU/US"), [])
        self.assertEqual(self.reasons("Euro Truck Simulator 2", "https://gameboost.com/euro-truck-simulator-2-vive-la-france-1-00-34663",
                                      edition="Standard + DLC", region="GLOBAL", region_filter="STEAM GLOBAL"), [])
        self.assertEqual(self.reasons("Fable Premium Upgrade Bundle Xbox Series",
                                      "https://www.eneba.com/xbox-fable-premium-upgrade-dlc-windows-xbox-series-x-s-xbox-live-key-europe",
                                      edition="DLC", region="XBOX/PC EU", region_filter="XBOX/PC  EUROPE", platform="xbox-play-anywhere", page_dlc=True), [])

    def test_steam_offer_on_a_switch_page(self):
        # Eneba, 30/09/2026 : Persona 5 Royal, offre saisie STEAM EU sur la page Nintendo Switch
        self.assertEqual(self.reasons("Persona 5 Royal Nintendo Switch", "https://www.eneba.com/nintendo-persona-5-royal-nintendo-switch-eshop-key-europe",
                                      region="EUROPE", region_filter="STEAM EU", platform="steam"),
                         ["platform: AllKeyShop steam, merchant nintendo"])

    def test_full_replay_new_catches(self):
        # et trois vraies erreurs que les anciennes règles ne voyaient pas
        self.assertEqual(self.reasons("Call of Duty Black Ops 6", "https://www.eneba.com/steam-call-of-duty-r-black-ops-6-pc-steam-key-europe",
                                      region="EUROPE", region_filter="WINDOWS EU", platform="microsoft-windows"),
                         ["platform: AllKeyShop microsoft-windows, merchant steam"])
        self.assertEqual(self.reasons("The Witcher 3 Wild Hunt Xbox Series", "https://www.lootbar.com/game-key/the-witcher-3-wild-hunt-xbox",
                                      region="ROW", region_filter="STEAM ROW", platform="steam"),
                         ["platform: AllKeyShop steam, merchant xbox"])
        # Splatoon Raiders : offre saisie Xbox (plateforme et région) sur la page Nintendo Switch 2
        self.assertEqual(self.reasons("Splatoon Raiders Nintendo Switch 2", "https://www.gamingdragons.com/en/game/buy-splatoon-raiders-switch-2-code.html",
                                      region="EU XBOX X|S", region_filter="XBOX X|S EUROPE", platform="xbox"),
                         ["platform: AllKeyShop xbox, merchant nintendo"])

    def test_distinctive_word_never_missing(self):
        # LDShop : page du jeu de base pour l'offre de l'upgrade (cas à trancher, doit rester signalé)
        res = pc.analyze("Forza Horizon 6 Premium Upgrade Bundle Xbox Series", offer(edition="Upgrade", region="XBOX/PC", platform="xbox-play-anywhere"),
                         "Forza Horizon 6 CD-Key for Xbox & PC – Safe & Fast | Forza Horizon 6 Global Key (Xbox/PC)", "page title")
        self.assertEqual(res["reasons"], ["another product at the merchant: « Forza Horizon 6 CD-Key for Xbox & PC » instead of « Forza Horizon 6 Premium Upgrade Bundle Xbox Series » (page title)"])

    def test_european_title_alias(self):
        self.assertEqual(self.reasons("Rhythm Heaven Groove Nintendo Switch 2", "https://www.loaded.com/rhythm-paradise-groove-switch-eu",
                                      region="EUROPE", region_filter="EUROPE", platform="nintendo-eshop"), [])

    def test_study_ok_cases(self):
        self.assertEqual(self.reasons("Hunt Showdown", "https://www.kinguin.net/en/category/553797/hunt-showdown-1896-10-dlc-bundle-pc-steam-cd-key",
                                      edition="Standard + DLC Bundle"), [])
        self.assertEqual(self.reasons("Assetto Corsa Competizione",
                                      "https://kinguin.net/category/193397/assetto-corsa-competizione-2023-gt-world-challenge-pack-dlc-steam-cd-key",
                                      edition="Bonus"), [])
        self.assertEqual(self.reasons("Among Us VR", "https://www.loaded.com/among-us-3d-vr-pc-steam"), [])
        self.assertEqual(self.reasons("Diablo 4 Lord of Hatred Xbox Series",
                                      "https://www.instant-gaming.com/en/21849-buy-diablo-iv-age-of-hatred-collection-xbox-one-xbox-series-x-s-microsoft-store/",
                                      edition="Hatred Edition", region="XBOX X|S", region_filter="XBOX X|S GLOBAL", platform="xbox"), [])
        self.assertEqual(self.reasons("Call of Duty Modern Warfare 4",
                                      "https://www.dreamgame.com/en/call-of-duty-modern-warfare-4-standard-edition-pre-purchase",
                                      edition="Preorder bonus", region="XBOX/PC", platform="xbox-play-anywhere",
                                      page_editions=["Standard", "Vault Edition", "Preorder bonus"]), [])
        ets2 = ["Bundle", "Collection Bundle", "Collectors Bundle Edition", "Gold", "Gold Bundle", "Mediterranean Bundle", "Standard"]
        self.assertEqual(self.reasons("Euro Truck Simulator 2", "https://www.kinguin.net/category/2749/euro-truck-simulator-2-gold-bundle-steam-cd-key/",
                                      edition="Bundle", page_editions=ets2), [])
        self.assertEqual(self.reasons("Stellaris", "https://www.g2a.com/en/stellaris-ultimate-bundle-2024-edition-pc-steam-key-global-i10000253362006",
                                      edition="2024 Edition", page_editions=["2024 Edition", "Ultimate Bundle", "Standard"]), [])
        self.assertEqual(self.reasons("RimWorld", "https://www.gamivo.com/product/rimworld-stareter-pack-pc-steam-global-en-de-fr-it-pl-cs-nl-ja-ko-no-pt-ru-zh-es-sv-tr-standard",
                                      edition="Starter Pack", page_editions=["Standard", "Starter Pack", "Deluxe"]), [])

    def test_french_amazon_titles(self):
        o = offer(region="BOX", region_filter="BOX", platform="physical-medium")
        for product, title in (("Kirby and the Forgotten Land Nintendo Switch", "Amazon.fr : Kirby et le monde oublié (Nintendo Switch) : Jeux vidéo"),
                               ("Pokemon Sword Nintendo Switch", "Pokémon Épée (Nintendo Switch) : Amazon.fr"),
                               ("Pokémon Brilliant Diamond Nintendo Switch", "Pokémon Diamant Étincelant - Nintendo Switch : Amazon.fr")):
            self.assertEqual(pc.analyze(product, o, title, "page title")["reasons"], [], product)
        # mais Épée n'est pas Bouclier
        self.assertEqual(pc.analyze("Pokemon Sword Nintendo Switch", o, "Pokémon Bouclier (Nintendo Switch)", "page title")["reasons"],
                         ["another product at the merchant: « Pokémon Bouclier (Nintendo Switch) » instead of « Pokemon Sword Nintendo Switch » (page title)"])

    def test_names_merchants_shorten_20261001(self):
        # premier jour de Top Offers (étude) : « UFC 5 » sans le préfixe d'éditeur, chez Eneba (URL « ufc-r-5 », ® écrit r)
        # et GAMIVO ; « Onimusha: WotS », sigle des derniers mots, au PS Store (2 éditions, boutiques UK et FR)
        ufc = offer(edition="Standard", region="EU XBOX X|S", region_filter="XBOX X|S EUROPE", platform="xbox")
        for text, source in (("xbox-ufc-r-5-xbox-series-x-s-xbox-live-key-europe", "URL"),
                             ("Buy UFC® 5 Xbox key! Cheap price | Eneba", "page title"),
                             ("Buy UFC 5 Xbox Series Key Europe", "page title"),
                             (pc.url_text("https://www.gamivo.com/product/ufc-5-xbox-xboxseries-eu-en-standard"), "URL")):
            with self.subTest(text=text):
                res = pc.analyze("EA Sports UFC 5 Xbox Series", ufc, text, source)
                self.assertNotIn("name", res["kinds"], res["reasons"])
        oni = offer(edition="Deluxe", region="PS5", platform="playstation-store")
        self.assertEqual(pc.analyze("Onimusha Way of the Sword PS5", oni, "Onimusha: WotS | Deluxe Edition", "page title")["reasons"], [])
        # un autre jeu de la série, ou un autre UFC, reste un autre produit
        self.assertEqual(pc.analyze("Onimusha Way of the Sword PS5", oni, "Onimusha 2: Samurai's Destiny", "page title")["kinds"], ["name"])
        self.assertEqual(pc.analyze("EA Sports UFC 5 Xbox Series", ufc, "Buy UFC 4 Xbox key! Cheap price", "page title")["kinds"], ["name"])
        self.assertIn("ufc 5 xbox series", pc.name_variants("EA Sports UFC 5 Xbox Series"))
        self.assertIn("onimusha wots", pc.name_variants("Onimusha Way of the Sword PS5"))

    def test_booster_courses_pack_alias(self):
        # K4G, 01/10/2026 (étude, 3e prix de l'édition DLC) : « Booster Courses Pack » = le Booster Course Pass
        pc._PRODUCT_ALIASES = None  # relit aliases.toml
        o = offer(edition="DLC", region="EUROPE", platform="nintendo-eshop", page_dlc=True)
        url = "https://k4g.com/product/mario-kart-8-deluxe-booster-courses-pack-nintendo-switch-europe-cd-key-D492FAEB"
        self.assertEqual(pc.analyze("Mario Kart 8 Deluxe Booster Course Pass Nintendo Switch", o, pc.url_text(url), "URL")["reasons"], [])
        self.assertEqual(pc.analyze("Mario Kart 8 Deluxe Booster Course Pass Nintendo Switch", o,
                                    "Buy Mario Kart 8 Deluxe - Booster Courses Pack - cheap | K4G.com", "page title")["reasons"], [])
        # le jeu de base vendu sur la page du DLC reste un autre produit
        self.assertEqual(pc.analyze("Mario Kart 8 Deluxe Booster Course Pass Nintendo Switch", o,
                                    "Buy Mario Kart 8 Deluxe Nintendo Switch Europe - cheap | K4G.com", "page title")["kinds"], ["name"])

    def test_multi_product_page_selected_option(self):
        # LDShop, 01/10/2026 (formation « à discuter ») : la page Forza Horizon 6 a l'option « Premium Upgrade » cochée
        dom = sample("ldshop_forza-horizon-6_sku16560.html")
        self.assertEqual(pc.selected_option_text(dom), "Forza Horizon 6 Premium Upgrade (Global)")
        # une option de prix cochée avant le produit (page réelle) ne doit pas être prise pour le produit
        priced = dom.replace("<section>", '<section><li role="radio" aria-checked="true">€ 40,97 € 49,99 Direct purchase</li>')
        self.assertEqual(pc.selected_option_text(priced), "Forza Horizon 6 Premium Upgrade (Global)")
        text = pc.page_text_from_dom(dom, "https://www.ldshop.gg/card/forza-horizon-6.html?skuId=16560", "selected-option")
        res = pc.analyze("Forza Horizon 6 Premium Upgrade Bundle Xbox Series", offer(edition="Upgrade", region="XBOX/PC", platform="xbox-play-anywhere"),
                         text, "page title")
        self.assertEqual(res["reasons"], [])
        self.assertEqual(pc.merchant_config("https://www.ldshop.gg/card/x.html", "LDShop")["page"]["parser"], "selected-option")

    def test_block_pages(self):
        for t in ("Just a moment...", "Blocked - Driffle", "Amazon.fr", "Tut uns Leid!", "Access Denied", ""):
            self.assertTrue(pc.is_block_page(t), t)
        self.assertFalse(pc.is_block_page("UFC® 5 | Access Denied"))
        self.assertFalse(pc.is_block_page("Buy F1 25 2026 Season Edition Xbox Series Key Europe | GAMIVO"))


@mock.patch.object(pc, "REQUEST_DELAY", 0)
class TestConfirmOnMerchantPage(unittest.TestCase):
    """Quand l'URL contredit AllKeyShop, la page marchand tranche avant l'alerte."""
    INTERSTITIAL = sample("redirection_kinguin.html")

    def page(self, url):
        return self.INTERSTITIAL.replace(TestRedirection.KINGUIN, url).replace(
            TestRedirection.KINGUIN.replace("/", "\\/"), url.replace("/", "\\/"))

    def run_check(self, product, o, merchant, merchant_html):
        # l'offre par défaut est chez Kinguin (groupe out-of-stock) : 2e réponse = la sonde de redirection (200, pas de
        # Location), puis la page lue pour la confirmation, puis l'URL canonique
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, self.page(merchant)), (200, None, merchant_html),
                                                            (200, None, merchant_html), (200, None, merchant_html)]), \
             mock.patch.object(pc, "page_title", return_value=None), mock.patch.object(pc, "chromium_dom", return_value=None):
            return pc.check_offer(product, o)

    def test_url_contradicted_by_the_page(self):
        # Gamingdragons : URL « steam-key », page « PC - EA App Download »
        res = self.run_check("EA SPORTS FC 26", offer(edition="Ultimate", region="GLOBAL", region_filter="EA GLOBAL", platform="ea-app"),
                             "https://www.gamingdragons.com/en/game/buy-ea-sports-fc-26-ultimate-edition-steam-key.html",
                             "<title>Acheter EA SPORTS FC 26 Ultimate Edition Jeu PC | PC - EA App Download</title>")
        self.assertEqual(res["verdict"], "OK")
        self.assertTrue(any(n.startswith("URL contradicted by the page") for n in res["notes"]))

    def test_url_confirmed_by_the_page(self):
        res = self.run_check("F1 25", offer(edition="2026 Season Edition", region="EU ENGLISH ONLY", region_filter="STEAM EU EN ONLY"),
                             "https://www.gamivo.com/product/f1-25-xbox-xbox-series-eu-2026-season",
                             "<title>Buy F1 25 2026 Season Edition Xbox Series Key Europe | GAMIVO</title>")
        self.assertEqual((res["verdict"], res["reasons"]), ("SUSPECT", ["platform: AllKeyShop steam, merchant xbox"]))
        self.assertTrue(any(n.startswith("confirmed by the page") for n in res["notes"]))
        res = self.run_check("The Blood Of Dawnwalker", offer(edition="Deluxe", region="GLOBAL", region_filter="STEAM GLOBAL"),
                             "https://www.eneba.com/steam-the-blood-of-dawnwalker-eclipse-edition-deluxe-steam-key-pc-europe",
                             "<title>Buy The Blood of Dawnwalker Eclipse Edition (Deluxe) Steam key PC! Cheap price</title>"
                             "<h1>The Blood of Dawnwalker Eclipse Edition (Deluxe) Steam Key (PC) EUROPE</h1>")
        self.assertEqual(res["verdict"], "SUSPECT")

    def test_game_plus_content_edition_with_dlc_in_the_url(self):
        # Keycense, 01/10/2026 (étude, 3e prix de l'édition « GTA 5 + Criminal », 11,09 € comme les autres offres) :
        # l'URL « …-criminal-enterprise-starter-pack-dlc-rockstar » a perdu le « + » du titre, la page vend le jeu + le pack
        url = "https://www.keycense.com/grand-theft-auto-v-criminal-enterprise-starter-pack-dlc-rockstar"
        o = offer(edition="GTA 5 + Criminal", region="GLOBAL", region_filter="ROCKSTAR GLOBAL", platform="rockstar")
        res = self.run_check("GTA 5", o, url,
                             "<title>GTA V + Criminal Enterprise - Buy Now on Keycense | Keycense</title>"
                             "<h1>Grand Theft Auto V + Criminal Enterprise Starter Pack DLC | Rockstar</h1>")
        self.assertEqual((res["verdict"], res["reasons"]), ("OK", []))
        self.assertTrue(any(n.startswith("URL contradicted by the page") for n in res["notes"]))
        # le DLC seul, dans la même édition : l'alerte reste, confirmée par la page
        res = self.run_check("GTA 5", o, url, "<title>GTA V: Criminal Enterprise Starter Pack DLC | Keycense</title>")
        self.assertEqual(res["reasons"], ["additional content: dlc"])
        self.assertTrue(any(n.startswith("confirmed by the page") for n in res["notes"]))
        # un « + » qui ne suit pas le jeu (le pack plus une carte) ne vaut pas le jeu
        res = self.run_check("GTA 5", o, url, "<title>Criminal Enterprise Starter Pack + Great White Shark Card DLC</title>")
        self.assertEqual(res["reasons"], ["additional content: dlc"])

    def test_nintendo_old_domains_are_read_in_english(self):
        # 01/10/2026 (étude, Top Offers) : liens eShop ES et DE sur nintendo.es / nintendo.de (308 vers nintendo.com) ;
        # la config Nintendo ne s'appliquait pas et le titre traduit sortait en « autre produit »
        es = "https://www.nintendo.es/Juegos/Nintendo-Switch/Ni-no-Kuni-La-ira-de-la-bruja-blanca-1575919.html"
        for url, name in ((es, "Nintendo eShop ES"),
                          ("https://www.nintendo.de/Spiele/Nintendo-Switch/Ni-no-Kuni-Der-Fluch-der-weissen-Konigin-1575919.html", "Nintendo eShop DE"),
                          ("https://www.nintendo.com/fr-fr/Jeux/Jeux-a-telecharger/x-1.html", "Nintendo eShop FR")):
            self.assertEqual(pc.merchant_config(url, name)["product_name"]["hreflang"], "en-GB", url)
        self.assertEqual(pc.merchant_config("", "Nintendo eShop IT")["name"], "Nintendo eShop")
        page = ('<title>Ni no Kuni: La ira de la bruja blanca | Juegos de Nintendo Switch | Juegos | Nintendo ES</title>'
                '<link rel="alternate" href="https://www.nintendo.com/en-gb/Games/Nintendo-Switch-games/'
                'Ni-No-Kuni-Remastered-Wrath-of-the-White-Witch-1575919.html" hreflang="en-GB">')
        o = offer(merchantName="Nintendo eShop ES", region="GLOBAL", platform="nintendo-eshop")
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, self.page(es)), (200, None, page)]):
            res = pc.check_offer("Ni no Kuni Wrath of the White Witch Remastered Nintendo Switch", o)
        self.assertEqual((res["verdict"], res["method"]), ("OK", "URL of the en-GB version"))

    def test_unverifiable_reason_says_what_the_url_names(self):
        # 02/10/2026, World of Warcraft: Forever (Heroic Pack) chez Driffle : la page bloque le moniteur ; l'ancien
        # message « URL sans nom du produit » était faux, l'URL nomme « warcraft forever skyborne heroic pack »
        o = offer(edition="Heroic Pack", region="BATTLENET GIFT", region_filter="BATTLENET GIFT", platform="battle-net")
        url = "https://www.driffle.com/warcraft-forever-skyborne-heroic-pack-dlc-global-pc-mac-battlenet-gift-p10001673"
        self.assertEqual(pc.unverified_reason(o, url),
                         "edition Heroic Pack: name not checked in the URL (it names « Warcraft Forever Skyborne Heroic Pack Dlc Mac »), "
                         "merchant page unreadable")
        self.assertEqual(pc.unverified_reason(offer(), "https://www.hrkgame.com/en/product/12345/"),
                         "product name not found in the URL, merchant page unreadable")
        # l'alias : « Warcraft Forever » est le produit, l'offre se vérifie par l'URL
        pc._PRODUCT_ALIASES = None
        res = pc.analyze("World of Warcraft: Forever", o, pc.url_text(url), "URL")
        self.assertEqual((res["match"], res["reasons"]), ("exact", []))

    def test_url_naming_another_product_alerts_without_the_page(self):
        # Romain, 02/10/2026 : « je vois pas pourquoi tu veux vérifier la page quand on a déjà un problème détecté à
        # la base ». Titanfall 2 : l'URL nomme le premier Titanfall ; une seule requête, la redirection AllKeyShop
        url = "https://www.driffle.com/titanfall-deluxe-edition-en-language-only-ea-app-cd-key-p123456"
        # deux requêtes seulement : la redirection AllKeyShop et le 301 éventuel du marchand (ici un 403) ; jamais la page
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, self.page(url)), (403, None, "")]) as get:
            res = pc.check_offer("Titanfall 2", offer(merchantName="Driffle", edition="Deluxe", region="IN ENGLISH ONLY",
                                                     region_filter="EA ENG/POL/RUS ONLY", platform="ea-app"))
        self.assertEqual((res["verdict"], res["method"], get.call_count), ("SUSPECT", "URL", 2))
        self.assertTrue(res["reasons"][0].startswith("another product at the merchant: « Titanfall Deluxe Edition"), res["reasons"])
        # audit du 02/10/2026 : un slug périmé que le marchand redirige (301) vers la fiche du bon produit n'alerte pas
        # (Instant Gaming garde l'id et change le slug : « /en/4860-buy-key-…-breath-of-the-wild-2/ »)
        zelda = "The Legend of Zelda Tears of the Kingdom Nintendo Switch"
        stale = "https://www.instant-gaming.com/en/4860-buy-key-some-old-name/"
        good = "https://www.instant-gaming.com/en/4860-buy-the-legend-of-zelda-tears-of-the-kingdom-switch-game-nintendo-eshop-europe/"
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, self.page(stale)), (301, good, "")]):
            res = pc.check_offer(zelda, offer(merchantName="Instant Gaming", region="EUROPE", platform="nintendo-eshop"))
        self.assertEqual((res["verdict"], res["method"]), ("OK", "URL after the merchant's 301"))
        # et un lien sans nom dont le 301 mène à une fiche d'un autre produit alerte, sans lire la page
        other = "https://www.instant-gaming.com/en/4860-buy-the-legend-of-zelda-breath-of-the-wild-switch/"
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, self.page("https://www.instant-gaming.com/en/4860-/")), (301, other, "")]):
            res = pc.check_offer(zelda, offer(merchantName="Instant Gaming", region="EUROPE", platform="nintendo-eshop"))
        self.assertEqual((res["verdict"], res["method"]), ("SUSPECT", "URL after the merchant's 301"))
        self.assertIn("Breath Of The Wild", res["reasons"][0])
        # une boutique contrôlée sur sa version anglaise (Nintendo) n'alerte jamais sur son slug traduit
        fr = "https://www.nintendo.com/fr-fr/Jeux/Jeux-a-telecharger/Le-Chat-Chapeaute-Pagaille-sous-la-pluie-3177999.html"
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, self.page(fr)), (503, None, ""), (503, None, ""), (503, None, "")]), \
             mock.patch.object(pc, "page_title", return_value=None), mock.patch.object(pc, "chromium_dom", return_value=None):
            res = pc.check_offer("The Cat in the Hat Rainy Day Mayhem Nintendo Switch", offer(merchantName="Nintendo eShop FR", platform="nintendo-eshop"))
        self.assertNotEqual(res["verdict"], "SUSPECT")
        # une URL qui ne nomme rien (un code) va toujours lire la page
        self.assertIsNone(pc.merchant_label("bundle 27059", "URL"))  # Steam « /bundle/27059/ »
        self.assertIsNone(pc.merchant_label("preorder-page", "URL"))  # Escape from Tarkov, page de l'éditeur

    def test_slug_repairs_and_shortened_names_20261002(self):
        # rejeu du 02/10/2026 : six offres OK grâce à la page seraient devenues de fausses alertes immédiates
        self.assertEqual(pc.repair_slug("nintendo-pokemontm-pokopia-nintendo-switch-2"), "nintendo-pokemon-pokopia-nintendo-switch-2")
        self.assertEqual(pc.repair_slug("s-t-a-l-k-e-r-2-heart-of-chernobyl"), "stalker-2-heart-of-chernobyl")
        self.assertEqual(pc.repair_slug("xbox-series-x-s-key"), "xbox-series-x-s-key")  # deux lettres : pas un sigle
        self.assertEqual(pc.norm("Kingdom Hearts HD 1.5+2.5 ReMIX"), "kingdom-hearts-hd-1-5-2-5-remix")
        self.assertIn("kingdom hearts hd 15 25 remix", [pc.norm(n).replace("-", " ") for n in pc.name_variants("Kingdom Hearts HD 1.5+2.5 ReMIX")])
        cases = (("Pokemon Pokopia Nintendo Switch 2", "https://www.eneba.com/nintendo-pokemontm-pokopia-nintendo-switch-2-nintendo-eshop-key-europe"),
                 ("S.T.A.L.K.E.R. 2 Heart of Chornobyl", "https://www.gamivo.com/product/s-t-a-l-k-e-r-2-heart-of-chernobyl-steam-gift"),
                 ("Kingdom Hearts HD 1.5+2.5 ReMIX Xbox Series", "https://www.kinguin.net/category/204807/kingdom-hearts-1-5-2-5-hd-remix-eu-xbox-one-xbox-series-x-s-cd-key"),
                 ("The Legend of Zelda Tears of the Kingdom Nintendo Switch", "https://www.instant-gaming.com/en/4860-buy-key-nintendo-the-legend-of-zelda-breath-of-the-wild-2/"),
                 ("Call of Duty Black Ops 6", "https://www.g2a.com/black-ops-6-vault-edition-pc-steam-key-global-i100"),
                 ("Marvel’s Spider-Man 2", "https://www.gamivo.com/product/spider-man-2-pc-steam-global"),
                 ("World of Warcraft: Forever", "https://www.driffle.com/warcraft-forever-skyborne-heroic-pack-dlc-global-pc-mac-battlenet-gift-p10001673"))
        pc._PRODUCT_ALIASES = None
        for product, url in cases:
            with self.subTest(product=product):
                self.assertIsNotNone(pc.analyze(product, offer(edition="Standard"), pc.url_text(url), "URL")["match"])
        # le préfixe omis garde le numéro et deux mots au moins
        self.assertIsNone(pc.name_match(pc.name_variants("Call of Duty Black Ops 6"), pc.norm("black-ops-7-vault-edition-pc")))
        self.assertNotIn("warcraft", [pc.norm(n) for n in pc.name_variants("World of Warcraft")])
        # audit du 02/10/2026 : le suffixe de plateforme ne compte pas pour les « deux mots au moins »
        self.assertNotIn("avengers ps5", [pc.norm(n).replace("-", " ") for n in pc.name_variants("Marvel’s Avengers PS5")])
        self.assertIn("spider man 2 ps5", [pc.norm(n).replace("-", " ") for n in pc.name_variants("Marvel’s Spider-Man 2 PS5")])
        self.assertIsNone(pc.name_match(pc.name_variants("The Legend of Zelda Tears of the Kingdom Nintendo Switch"),
                                        pc.norm("the-legend-of-zelda-breath-of-the-wild-nintendo-switch")))

    def test_two_merchant_groups_for_redirects(self):
        # Romain, 02/10/2026 : « Kinguin redirige vers une autre offre quand l'offre est out of stock, donc pour Kinguin
        # on ne se fie pas aux redirections ; il nous faudra deux groupes »
        self.assertTrue(pc.redirect_untrusted(pc.merchant_config("https://www.kinguin.net/category/1/x", "Kinguin")))
        self.assertFalse(pc.redirect_untrusted(pc.merchant_config("https://www.instant-gaming.com/en/1-/", "Instant Gaming")))
        o = offer(merchantName="Kinguin", edition="Deluxe", region="IN ENGLISH ONLY", region_filter="EA ENG/POL/RUS ONLY", platform="ea-app")
        link = "https://www.kinguin.net/category/25568/titanfall-deluxe-edition-en-language-only-ea-app-cd-key"
        right = "https://www.kinguin.net/category/25568/titanfall-2-deluxe-edition-ea-app-cd-key"
        # le lien nomme un autre produit : alerte, même si une redirection mène à une fiche du bon produit (autre offre)
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, self.page(link)), (301, right, "")]):
            res = pc.check_offer("Titanfall 2", o)
        self.assertEqual(res["verdict"], "SUSPECT")
        self.assertIn("Titanfall Deluxe Edition", res["reasons"][0])
        self.assertTrue(res["reasons"][1].startswith("offer out of stock at the merchant"))  # et la redirection est signalée
        # Romain, 02/10/2026 : « on a juste besoin de suivre les redirections » — à chaque offre Kinguin, une requête sans
        # suivre la redirection : 200 = fiche en stock, 301 vers une autre fiche = rupture ; un 301 qui ne retire que le
        # segment de langue (« /en/category/… » -> « /category/… ») est la même fiche
        ok = offer(merchantName="Kinguin", edition="Deluxe", region="IN ENGLISH ONLY", region_filter="EA ENG/POL/RUS ONLY", platform="ea-app")
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, self.page(right)), (200, None, "")]) as get:
            res = pc.check_offer("Titanfall 2", ok)
        self.assertEqual((res["verdict"], get.call_count), ("OK", 2))
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, self.page(right)), (301, "/category/25568/titanfall-2-pc-ea-app-key", "")]):
            res = pc.check_offer("Titanfall 2", ok)
        self.assertEqual(res["verdict"], "SUSPECT")
        self.assertEqual(res["reasons"], ["offer out of stock at the merchant: the link redirects to another page "
                                          "(https://www.kinguin.net/category/25568/titanfall-2-pc-ea-app-key), but the price stays in the feed"])
        # une fiche seulement RENOMMÉE (même nom, région, plateforme, édition : 24 des 25 redirections en mémoire le 02/10)
        # n'est pas une rupture : une note, pas d'alerte
        dayz = "https://www.kinguin.net/category/55338/dayz-eu-steam-altergift/"
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, self.page(dayz)), (301, "/category/55338/dayz-eu-pc-steam-altergift", "")]):
            res = pc.check_offer("Dayz", offer(merchantName="Kinguin", region="GIFT EU", region_filter="STEAM GIFT EU"))
        self.assertEqual((res["verdict"], res["reasons"]), ("OK", []))
        self.assertTrue(any(n.startswith("page renamed at the merchant") for n in res["notes"]), res["notes"])
        # le jeu renommé par son éditeur (« Crimson Desert Enhanced », aliases.toml) : un renommage, pas une rupture
        pc._PRODUCT_ALIASES = None
        crimson = "https://www.kinguin.net/en/category/471878/crimson-desert-deluxe-edition-eu-xbox-series-x-s-cd-key"
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, self.page(crimson)),
                                                            (301, "/en/category/471878/crimson-desert-enhanced-deluxe-edition-eu-xbox-series-x-s-cd-key", "")]):
            res = pc.check_offer("Crimson Desert Xbox Series", offer(merchantName="Kinguin", edition="Deluxe", region="EU XBOX X|S",
                                                                      region_filter="XBOX X|S EUROPE", platform="xbox"))
        self.assertEqual((res["verdict"], res["reasons"]), ("OK", []))
        # mais « eu » -> « de » (Rust) ou un autre identifiant de fiche (Ace Combat 8) : une autre offre, alerte
        rust = "https://www.kinguin.net/category/55259/rust-eu-steam-altergift/"
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, self.page(rust)), (301, "/category/55259/rust-de-pc-steam-altergift", "")]):
            res = pc.check_offer("Rust", offer(merchantName="Kinguin", region="GIFT EU", region_filter="STEAM GIFT EU"))
        self.assertEqual(res["verdict"], "SUSPECT")
        self.assertTrue(res["reasons"][0].startswith("offer out of stock at the merchant"), res)
        en = "https://www.kinguin.net/en/category/360568/helldivers-2-super-citizen-edition-eu-xbox-series-x-s-cd-key"
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, self.page(en)), (301, "/category/360568/helldivers-2-super-citizen-edition-eu-xbox-series-x-s-cd-key", "")]):
            res = pc.check_offer("Helldivers 2 Xbox Series", offer(merchantName="Kinguin", edition="Super Citizen", region="EU XBOX X|S",
                                                                   region_filter="XBOX X|S EUROPE", platform="xbox"))
        self.assertEqual((res["verdict"], res["reasons"]), ("OK", []))
        # le lien ne nomme rien, la redirection mène à une fiche du produit : pas blanchi non plus, la redirection est ignorée
        bare = "https://www.kinguin.net/category/25568/x"
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, self.page(bare)), (301, right, ""), (503, None, "")]), \
             mock.patch.object(pc, "page_title", return_value=None), mock.patch.object(pc, "chromium_dom", return_value=None):
            res = pc.check_offer("Titanfall 2", o)
        # la cible ne blanchit pas le nom, et la redirection elle-même est signalée : fiche en rupture, prix dans le feed
        self.assertEqual(res["verdict"], "SUSPECT")
        self.assertTrue(res["reasons"][0].startswith("offer out of stock at the merchant: the link redirects to another page"), res)

    def test_kinguin_probe_keeps_what_it_saw_and_never_passes_silently(self):
        # 02/10/2026 : la fiche servie à la place du lien est gardée pour le recontrôle (evidence) ; une sonde en erreur
        # n'est pas une fiche en stock : contrôle raté, retenté au passage suivant (À VÉRIFIER au 3e échec)
        rust = "https://www.kinguin.net/category/55259/rust-eu-steam-altergift/"
        o = offer(merchantName="Kinguin", region="GIFT EU", region_filter="STEAM GIFT EU")
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, self.page(rust)), (301, "/category/55259/rust-de-pc-steam-altergift", "")]):
            res = pc.check_offer("Rust", o)
        self.assertEqual(res["evidence"], {"served": pc.norm(pc.url_text("https://www.kinguin.net/category/55259/rust-de-pc-steam-altergift")),
                                           "page": "", "via": ""})
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, self.page(rust)), (200, None, "")]):
            res = pc.check_offer("Rust", o)
        self.assertEqual((res["verdict"], res["evidence"]), ("OK", {"served": "", "page": "", "via": ""}))
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, self.page(rust)), OSError("timed out")]):
            with self.assertRaises(pc.CheckError):
                pc.check_offer("Rust", o)

    def test_a_merchant_whose_page_decides_is_never_alerted_on_its_url_alone(self):
        # LDShop, 01/10/2026 : l'URL « …/card/forza-horizon-6.html » nomme le jeu de base, l'option cochée de la page est
        # le Premium Upgrade. Pour un marchand dont la page décide (lecteur « selected-option », PS Store), l'URL qui
        # nomme un autre produit ne suffit pas : la page est lue (audit des tests du 02/10/2026 : rien ne le vérifiait)
        url = "https://www.ldshop.gg/card/forza-horizon-6.html?compare=ak&skuId=16560&skuLabelId=1"
        dom = sample("ldshop_forza-horizon-6_sku16560.html")
        o = offer(merchantName="LDShop", edition="Upgrade", region="XBOX/PC", platform="xbox-play-anywhere")
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, self.page(url)), (200, None, "<title>Forza Horizon 6</title>"),
                                                            (200, None, "<title>Forza Horizon 6</title>")] + [(200, None, "")] * 4), \
             mock.patch.object(pc, "chromium_dom", return_value=dom):
            res = pc.check_offer("Forza Horizon 6 Premium Upgrade Bundle Xbox Series", o)
        self.assertEqual((res["verdict"], res["method"]), ("OK", "page (Chromium)"), res)

    def test_cjs_cdkeys_region_is_the_variation_chosen_by_the_link(self):
        # alerte du 02/10/2026 à 23:31, faux positif : Minecraft Dungeons « Triple Bundle », CJS CDKeys, affichée EUROPE
        # (WINDOWS EU). Le lien choisit « ?variation=699 » = Europe (SKU …_EU_ONLY) ; le champ « Region: » de la page
        # montre la variante mise en avant, « AR (Argentina) ». merchants/cjs-cdkeys.toml
        dom = sample("cjs_minecraft-triple-bundle_variations.html")
        base = "https://www.cjs-cdkeys.com/products/Minecraft-Triple-Bundle-PC-Windows-Key-%28Digital-Download%29.html?variation="
        self.assertEqual([pc.url_variation_label(dom, base + v) for v in ("699", "700", "701", "725")],
                         ["Europe", "USA", "AR (Argentina)", "United Kingdom"])
        self.assertIsNone(pc.url_variation_label(dom, base.split("?")[0]))
        self.assertIsNone(pc.url_variation_label(dom, base + "999"))
        text = pc.page_text_from_dom(dom, base + "699", "url-variation")
        self.assertIn("REGION Europe", text)
        self.assertNotIn("Argentina", text)
        o = offer(merchantName="CJS CDKeys", edition="Triple Bundle", region="EUROPE", region_filter="WINDOWS EU", platform="microsoft-windows")
        self.assertEqual(pc.analyze("Minecraft Dungeons", o, text, "page title")["reasons"], [])
        argentina = pc.page_text_from_dom(dom, base + "701", "url-variation")
        self.assertEqual(pc.analyze("Minecraft Dungeons", o, argentina, "page title")["reasons"], ["forbidden region: ar, argentina"])

        # de bout en bout : l'URL nomme le produit mais pas la région ; avec « variation= », la page est lue pour la variante
        def check(variation):
            with mock.patch.object(pc, "http_get", side_effect=[(200, None, self.page(base + variation)), (403, None, "")] + [(403, None, "")] * 4), \
                 mock.patch.object(pc, "chromium_dom", return_value=dom):
                return pc.check_offer("Minecraft", offer(merchantName="CJS CDKeys", region="EUROPE", region_filter="WINDOWS EU",
                                                         platform="microsoft-windows"))
        res = check("699")
        self.assertEqual(res["verdict"], "OK", res)
        self.assertIn("region read on the variant chosen by the link: Europe", res["notes"])
        res = check("701")  # la clé argentine affichée EUROPE : une vraie erreur, que l'URL seule laissait passer
        self.assertEqual((res["verdict"], res["reasons"]), ("SUSPECT", ["forbidden region: ar, argentina"]))

    def test_browser_headers_and_moved(self):
        # 02/10/2026 : Akamai (Kinguin) répond 403 à l'Accept minimal, 200 ou 301 au jeu complet d'en-têtes de Chrome
        h = pc.request_headers(pc.BROWSER_UA)
        self.assertEqual(h["Sec-Fetch-Dest"], "document")
        self.assertIn('v="150"', h["sec-ch-ua"])
        self.assertNotIn("Sec-Fetch-Dest", pc.request_headers(pc.AKS_UA))  # AllKeyShop : UA AKS/Staff, en-têtes simples
        base = "https://www.kinguin.net/en/category/360568/helldivers-2-eu-xbox-cd-key"
        self.assertFalse(pc.moved(base, "/category/360568/helldivers-2-eu-xbox-cd-key"))  # segment de langue retiré
        self.assertFalse(pc.moved(base, None))
        self.assertTrue(pc.moved(base, "https://www.kinguin.net/category/360568/helldivers-2-global-xbox-cd-key"))

    def test_ldshop_http_body_without_the_option_falls_back_to_chromium(self):
        # 02/10/2026 : avec les en-têtes complets, LDShop répond 200 en HTTP, mais l'option cochée est rendue en JavaScript
        url = "https://www.ldshop.gg/card/forza-horizon-6.html?compare=ak&skuId=16560&skuLabelId=1"
        cfg = pc.merchant_config(url, "LDShop")
        with mock.patch.object(pc, "http_get", return_value=(200, None, "<title>Forza Horizon 6 CD-Key for Xbox & PC – Safe & Fast</title>")), \
             mock.patch.object(pc, "page_title", return_value="Forza Horizon 6 CD-Key | option choisie : Forza Horizon 6 Premium Upgrade (Global)") as chromium:
            text, method = pc.merchant_page_text(url, cfg)
        self.assertEqual(method, "page (Chromium)")
        self.assertIn("option choisie", text)
        chromium.assert_called_once()

    def test_kinguin_serves_another_page_than_the_link(self):
        # arbitrage du 01/10/2026, Stellaris (offre 135046199) : le lien « …-starter-pack-eu-steam-cd-key » sert la
        # fiche globale « …-starter-pack-bundle-2023-pc-steam-cd-key » (URL canonique) : la fiche servie fait foi
        dom = sample("kinguin_stellaris_172478.html")
        link = "https://www.kinguin.net/category/172478/stellaris-starter-pack-eu-steam-cd-key"
        o = offer(edition="Bundle 1", region="GLOBAL", region_filter="STEAM GLOBAL")
        # 02/10/2026 : Kinguin répond 301 du lien vers la fiche servie (avec les en-têtes complets de navigateur) : la
        # sonde suffit, sans ouvrir la page (Romain : « on a juste besoin de suivre les redirections »)
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, self.page(link)),
                                                            (301, "/category/172478/stellaris-starter-pack-bundle-2023-pc-steam-cd-key", "")]) as get, \
             mock.patch.object(pc, "page_title", return_value=None):
            res = pc.check_offer("Stellaris", o)
        self.assertEqual(get.call_count, 2)
        # Romain, 02/10/2026 : « on aura quand même une alerte » — en rupture, le prix reste dans le feed ; la région
        # (lue sur la fiche servie, globale comme l'affichage) n'est plus reprochée
        self.assertEqual(res["verdict"], "SUSPECT")
        self.assertEqual(res["reasons"], ["offer out of stock at the merchant: the link redirects to another page "
                                          "(https://www.kinguin.net/category/172478/stellaris-starter-pack-bundle-2023-pc-steam-cd-key), "
                                          "but the price stays in the feed"])
        self.assertTrue(any("page served" in n for n in res["notes"]))
        # repli : pas de redirection vue (200), mais la page servie a une autre URL canonique : même alerte
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, self.page(link)), (200, None, ""), (200, None, dom), (200, None, dom)]), \
             mock.patch.object(pc, "page_title", return_value=None):
            res2 = pc.check_offer("Stellaris", o)
        self.assertEqual(res2["reasons"], res["reasons"])
        # une fiche canonique qui est bien celle du lien, toujours EU : l'alerte de région reste
        same = dom.replace("stellaris-starter-pack-bundle-2023-pc-steam-cd-key", "stellaris-starter-pack-eu-steam-cd-key")
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, self.page(link)), (200, None, ""), (200, None, same), (200, None, same)]), \
             mock.patch.object(pc, "page_title", return_value=None):
            res = pc.check_offer("Stellaris", o)
        self.assertEqual(res["reasons"], ["region: AllKeyShop GLOBAL, merchant EU"])
        self.assertEqual(pc.merchant_config(link, "Kinguin")["redirect"]["means"], "out-of-stock")

    def test_region_field_in_the_page_body(self):
        # K4G, 30/09/2026 : slug « playstation-5-europe », page « Steam CD Key », champs PLATFORM Steam / REGION Global
        dom = sample("k4g_spider-man-2_deluxe.html")
        text = pc.page_title_from_html(dom)
        self.assertTrue(text.startswith("Buy Marvel's Spider-Man 2 Deluxe Edition Steam CD Key"))
        self.assertIn("REGION Global", text)
        res = self.run_check("Marvel’s Spider-Man 2", offer(edition="Deluxe", region="GLOBAL", region_filter="STEAM GLOBAL"),
                             "https://k4g.com/product/marvel-s-spider-man-2-playstation-5-europe-cd-key-83338621", dom)
        self.assertEqual((res["verdict"], res["reasons"]), ("OK", []))
        self.assertTrue(any(n.startswith("URL contradicted by the page") for n in res["notes"]))

    def test_region_menu_does_not_contradict(self):
        # une page qui liste plusieurs zones (menu de filtres) ne lève pas une vraie clé EU affichée GLOBAL
        res = self.run_check("Stellaris", offer(edition="Bundle 1", region="GLOBAL", region_filter="STEAM GLOBAL"),
                             "https://kinguin.net/category/172478/stellaris-starter-pack-eu-steam-cd-key",
                             "<title>Stellaris Starter Pack | Shop</title><div>REGION Global Europe North America</div><p>Region: EUROPE</p>")
        self.assertEqual(res["verdict"], "SUSPECT")

    def test_blocked_page_keeps_the_url_evidence(self):
        res = self.run_check("EA SPORTS FC 26", offer(edition="ICONS Edition", region="GLOBAL", region_filter="EA GLOBAL", platform="ea-app"),
                             "https://www.driffle.com/ea-sports-fc-26-icons-edition-global-pc-steam-digital-key-p9990128",
                             "<title>Blocked - Driffle</title>")
        self.assertEqual((res["verdict"], res["reasons"]), ("SUSPECT", ["platform: AllKeyShop ea-app, merchant steam"]))

    def test_localized_title_gives_manual_check(self):
        url = "https://www.amazon.fr/gp/product/B09XXXXXXX/"
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, self.page(url)), (200, None, ""),
                                                            (200, None, "<title>Amazon.fr : Le Jeu Inconnu (Nintendo Switch)</title>")]):
            res = pc.check_offer("Some Game Nintendo Switch", offer(merchantName="Amazon.fr", region="BOX", platform="physical-medium"))
        self.assertEqual(res["verdict"], "À VÉRIFIER")
        self.assertTrue(res["reasons"][0].startswith("merchant title in another language"))

    def test_silent_page_is_not_a_confirmation(self):
        res = self.run_check("Stellaris", offer(edition="Bundle 1", region="GLOBAL", region_filter="STEAM GLOBAL"),
                             "https://kinguin.net/category/172478/stellaris-starter-pack-eu-steam-cd-key",
                             "<title>Stellaris: Starter Pack Bundle 2023 PC Steam CD Key | Buy cheap on Kinguin.net</title>")
        self.assertEqual(res["verdict"], "SUSPECT")
        self.assertTrue(any(n.startswith("the page says nothing on this point") for n in res["notes"]))

    def test_name_unverifiable_but_wrong_console(self):
        # Elden Ring Xbox Series chez Amazon : nom illisible, mais l'URL dit PlayStation
        url = "https://www.amazon.fr/Bandai-Namco-Entertainment-3391892017632-PlayStation/dp/B0977LKSQ6/"
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, self.page(url)), (200, None, ""), (200, None, "<title>Amazon.fr</title>")]), \
             mock.patch.object(pc, "page_title") as title:
            res = pc.check_offer("Elden Ring Xbox Series", offer(merchantName="Amazon.fr", edition="Launch Edition", region="BOX",
                                                                region_filter="BOX", platform="physical-medium"))
        title.assert_not_called()
        self.assertEqual((res["verdict"], res["reasons"]), ("SUSPECT", ["platform: AllKeyShop page Xbox, merchant PlayStation"]))


@mock.patch.object(pc, "REQUEST_DELAY", 0)
class TestCheckOffer(unittest.TestCase):
    INTERSTITIAL = sample("redirection_kinguin.html")

    def interstitial(self, url):
        """La page de redirection Kinguin, avec une autre URL marchand (formes HTML et JSON)."""
        page = self.INTERSTITIAL.replace(TestRedirection.KINGUIN, url)
        return page.replace(TestRedirection.KINGUIN.replace("/", "\\/"), url.replace("/", "\\/"))

    def test_url_direct(self):
        with mock.patch.object(pc, "http_get", return_value=(200, None, self.INTERSTITIAL)) as get:
            res = pc.check_offer("EA SPORTS FC 27", offer(region="GIFT"))
        self.assertEqual((res["verdict"], res["method"], res["reasons"]), ("OK", "URL", []))
        self.assertTrue(res["url"].startswith("https://www.kinguin.net/"))
        # deux requêtes : la redirection AllKeyShop (UA AKS/Staff), puis la sonde de redirection de Kinguin (groupe
        # out-of-stock, UA navigateur, sans suivre la redirection) ; jamais la page
        self.assertEqual(get.call_count, 2)
        self.assertEqual(get.call_args_list[0], mock.call(pc.REDIRECTION_URL % (1, 47), pc.AKS_UA))
        self.assertEqual(get.call_args_list[1], mock.call(res["url"], pc.BROWSER_UA, follow=False))

    def test_merchant_redirect_fallback(self):
        page = self.interstitial("https://www.instant-gaming.com/en/21656-/?igr=289098")
        full = "https://www.instant-gaming.com/en/21656-buy-ea-sports-fc-27-pc-ea-app/?igr=289098"

        def fake_get(url, ua, follow=True, timeout=30):
            if "allkeyshop.com" in url:
                return 200, None, page
            self.assertEqual((ua, follow), (pc.BROWSER_UA, False))
            return 301, full, ""

        with mock.patch.object(pc, "http_get", side_effect=fake_get):
            res = pc.check_offer("EA SPORTS FC 27", offer(merchantName="Instant Gaming", platform="ea-app"))  # groupe « même offre »
        self.assertEqual((res["verdict"], res["method"], res["url"]), ("OK", "URL after the merchant's 301", full))

    def test_page_fallback(self):
        page = self.interstitial("https://store.epicgames.com/p/e149fb")  # URL sans nom : la page tranche
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, page), (200, None, ""), (200, None, "")]), \
             mock.patch.object(pc, "page_title", return_value="EA SPORTS FC 27 | Download and Buy Today - Epic Games Store"):
            res = pc.check_offer("EA SPORTS FC 27", offer(platform="epic-store"))
        self.assertEqual((res["verdict"], res["method"]), ("OK", "page (Chromium)"))

    def test_page_fallback_wrong_product(self):
        page = self.interstitial("https://store.epicgames.com/p/abc-123")
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, page), (200, None, ""), (200, None, "")]), \
             mock.patch.object(pc, "page_title", return_value="Sonic the Hedgehog - Epic Games Store"):
            res = pc.check_offer("Sonic Racing CrossWorlds", offer(platform="epic-store"))
        self.assertEqual((res["verdict"], res["reasons"]),
                         ("SUSPECT", ["another product at the merchant: « Sonic the Hedgehog » instead of « Sonic Racing CrossWorlds » (page title)"]))

    def test_unreadable_page(self):
        page = self.interstitial("https://store.epicgames.com/p/abc-123")
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, page), (403, None, ""), (403, None, "")]), \
             mock.patch.object(pc, "page_title", return_value=None):
            res = pc.check_offer("EA SPORTS FC 27", offer())
        self.assertEqual((res["verdict"], res["method"]), ("À VÉRIFIER", "none"))

    def test_redirection_failure_is_retryable(self):
        with mock.patch.object(pc, "http_get", return_value=(503, None, "")) as get:
            with self.assertRaises(pc.CheckError):
                pc.check_offer("EA SPORTS FC 27", offer())
        self.assertEqual(get.call_count, 2)  # un second essai sur un 5xx

    def test_transient_503_then_ok(self):
        with mock.patch.object(pc, "http_get", side_effect=[(503, None, ""), (200, None, self.INTERSTITIAL), (200, None, "")]):
            res = pc.check_offer("EA SPORTS FC 27", offer(region="GIFT"))  # 3e réponse : la sonde Kinguin
        self.assertEqual(res["verdict"], "OK")
        with mock.patch.object(pc, "http_get", return_value=(200, None, "<html>rien</html>")):
            with self.assertRaises(pc.CheckError):
                pc.check_offer("EA SPORTS FC 27", offer())


@mock.patch.object(pc, "REQUEST_DELAY", 0)
@mock.patch.object(pc, "PAGE_DELAY", 0)
class TestGameCurrency20261002(unittest.TestCase):
    """Romain, 02/10/2026 : « ajoute la règle pour la monnaie de jeu » — points, coins, V-Bucks, Shark Cards vendus sur la
    page d'un jeu, dont l'URL contient le nom."""

    def reasons(self, product, url, **kw):
        return pc.analyze(product, offer(**kw), pc.url_text(url), "URL")["reasons"]

    def test_currency_sold_as_the_game(self):
        cod = "Call of Duty Black Ops 6"
        self.assertEqual(self.reasons(cod, "https://shop.example/call-of-duty-black-ops-6-5000-cod-points-xbox-live", platform="xbox", region="XBOX X|S"),
                         ["in-game currency at the merchant: cod-points"])
        self.assertEqual(self.reasons(cod, "https://shop.example/black-ops-6-2400-points-pc-battle-net", platform="battle-net"),
                         ["in-game currency at the merchant: points"])  # mot seul + quantité
        self.assertEqual(self.reasons("Fortnite", "https://shop.example/fortnite-1000-v-bucks-pc", platform="epic-store"),
                         ["in-game currency at the merchant: v-bucks"])
        self.assertEqual(self.reasons("EA SPORTS FC 26", "https://shop.example/ea-sports-fc-26-12000-fc-points-pc-ea-app", platform="ea-app"),
                         ["in-game currency at the merchant: fc-points"])
        self.assertEqual(self.reasons("Elden Ring", "https://shop.example/elden-ring-steam-wallet-code-50-eur"),
                         ["in-game currency at the merchant: wallet"])

    def test_currency_that_is_expected(self):
        # l'édition AllKeyShop est la monnaie (GTA 5) ; un bonus promo (G2A : 2 gold coins) ; une offre WALLET (PS Store via recharge)
        self.assertEqual(self.reasons("GTA 5", "https://www.gamivo.com/product/grand-theft-auto-v-gta-5-premium-online-edition-and-great-white-shark-card-bundle",
                                      edition="Premium + Great White Card", platform="rockstar"), [])
        self.assertEqual(self.reasons("GTA 5", "https://www.eneba.com/steam-grand-theft-auto-v-great-white-shark-cash-card-rockstar-social-club-key-europe",
                                      edition="GTA 5 + GTAO Great White Shark Cash Card", region="EUROPE", region_filter="ROCKSTAR EUROPE", platform="rockstar"), [])
        self.assertEqual(self.reasons("Euro Truck Simulator 2", "https://www.g2a.com/euro-truck-simulator-2-gold-edition-steam-key-global-2-gold-coins-i10000044284002",
                                      edition="Gold"), [])
        self.assertEqual(self.reasons("GTA 5", "https://www.kinguin.net/category/65946/grand-theft-auto-v-criminal-enterprise-starter-pack-megalodon-shark-card",
                                      edition="GTA 5 + Criminal + Megalodon", platform="rockstar"), [])  # la carte nommée par son requin
        self.assertEqual(self.reasons("EA SPORTS FC 26 PS5", "https://shop.example/ea-sports-fc-26-ps5-psn-wallet-top-up-de",
                                      region="WALLET DE", region_filter="PSN WALLET DE", platform="playstation-store"), [])
        # un jeu dont le nom contient un mot de monnaie (Tarot Tokens) : les mots du nom ne comptent pas
        self.assertEqual(self.reasons("Tarot Tokens Nintendo Switch", "https://www.nintendo.com/fr-fr/Jeux/Tarot-Tokens-3185728.html",
                                      platform="nintendo-eshop"), [])
        # la monnaie seule, sans le nom du jeu : « autre produit », pas un doublon
        res = pc.analyze(cod := "Call of Duty Black Ops 6", offer(platform="battle-net"), pc.url_text("https://shop.example/cod-points-5000-pc"), "URL")
        self.assertEqual(res["kinds"], ["name", "currency"])


@mock.patch.object(pc, "REQUEST_DELAY", 0)
@mock.patch.object(pc, "PAGE_DELAY", 0)
class TestOfferModes20261001(unittest.TestCase):
    """Deux modes d'offres (Romain, 01/10/2026) : Top Offers (3 premiers prix de chaque édition) et Full Page
    (toutes les offres de la page) ; un webhook par mode de pages ; les top games passent pendant la homepage ;
    les arbitrages du doc partagé (Amazon ignoré)."""
    PAGE = sample("prod_popular1_ea-fc-27.html")
    TARGETS = [("Popular", 1, "EA SPORTS FC 27", "https://www.allkeyshop.com/blog/buy-ea-sports-fc-27-key-compare-prices/")]

    def setUp(self):
        pc.FAILURES.clear()
        self.trans = pc.parse_game_page(self.PAGE)

    @staticmethod
    def ok(product, o):
        return {"verdict": "OK", "reasons": [], "notes": [], "url": "https://shop.example/x", "method": "URL"}

    def test_top_offers_are_the_three_first_prices_of_each_edition(self):
        offers = pc.page_offers(self.trans, 3)
        by_edition = {}
        for o in offers:
            by_edition.setdefault(o["edition"], []).append(o)
        # Standard + Bonus n'a que 2 offres de clé avec un prix
        self.assertEqual({e: len(l) for e, l in by_edition.items()},
                         {"Standard": 3, "Standard + Bonus": 2, "Ultimate": 3, "Ultimate Plus": 3})
        for l in by_edition.values():
            self.assertEqual([o["edition_rank"] for o in l], list(range(1, len(l) + 1)))
            self.assertEqual([o["price"] for o in l], sorted(o["price"] for o in l))
        self.assertFalse(any(o["account"] for o in offers))  # les comptes ne sont pas des premiers prix
        self.assertEqual([(o["edition"], o["price"]) for o in offers if o["page_first"]], [("Standard", 54.99)])
        # un prix par édition : le contrôle d'origine
        self.assertEqual([o["id"] for o in pc.first_prices(self.trans)],
                         [o["id"] for o in offers if o["edition_rank"] == 1])

    def test_full_page_takes_every_offer_on_sale_accounts_included(self):
        offers = pc.page_offers(self.trans, None)
        on_sale = [p for p in self.trans["prices"] if p["dispo"] and p["price"] != pc.NO_PRICE]
        self.assertEqual(len(offers), len(on_sale))  # 74 clés + 26 comptes
        self.assertEqual(sum(o["account"] for o in offers), 26)
        accounts = [o for o in offers if o["account"] and o["edition"] == "Standard"]
        self.assertEqual([o["edition_rank"] for o in accounts], list(range(1, len(accounts) + 1)))  # rang à part
        self.assertEqual(sum(o["page_first"] for o in offers), 1)

    def test_alert_names_the_rank_in_the_edition(self):
        o = pc.page_offers(self.trans, 3)[1]
        msg = pc.format_alert("Popular", 1, "EA SPORTS FC 27", self.TARGETS[0][3], o,
                              {"verdict": "SUSPECT", "reasons": ["x"], "notes": [], "url": None, "method": "URL"})
        self.assertEqual(msg.splitlines()[0], pc.URGENT_PREFIX)  # un SUSPECT sur le 2e prix : une urgence premier prix
        self.assertIn("· Standard · 2nd price of the edition", msg.splitlines()[1])
        self.assertEqual(pc.rank_label({"edition_rank": 1, "account": True}), "1st price of the edition (account)")
        self.assertEqual(pc.rank_label({}), "")

    def test_run_cycle_checks_the_offers_of_the_mode(self):
        checked = []

        def checker(product, o):
            checked.append(o["id"])
            return self.ok(product, o)

        state = pc.load_state("/nonexistent")
        with mock.patch.object(pc, "http_get", return_value=(200, None, self.PAGE)), mock.patch.object(pc, "NOTIFY_OK", False):
            pc.run_cycle(self.TARGETS, lambda m: None, state, checker, per_edition=3)
            self.assertEqual(len(checked), 11)
            pc.run_cycle(self.TARGETS, lambda m: None, state, checker, per_edition=None)
        self.assertEqual(len(checked), 100)  # Full Page : les 89 autres offres ; les 11 déjà vues ne repassent pas
        e = state["checked"][str(pc.page_offers(self.trans, 3)[1]["id"])]
        self.assertEqual((e["edition_rank"], e["page_first"], e["account"]), (2, False, False))

    def test_between_pages_hook(self):
        calls = []
        targets = [("Home · FPS", i, "EA SPORTS FC 27", "https://www.allkeyshop.com/blog/p%d/" % i) for i in range(1, 4)]
        with mock.patch.object(pc, "http_get", return_value=(200, None, self.PAGE)):
            pc.run_cycle(targets, lambda m: None, pc.load_state("/nonexistent"), self.ok, between=lambda: calls.append(1))
        self.assertEqual(len(calls), 2)  # entre les pages 1-2 et 2-3

    def test_top_games_pass_in_the_middle_of_a_homepage_pass(self):
        """Mesure du 01/10/2026 : pendant les ~9 min d'un passage homepage, aucun passage top games. Les top
        games sont « urgents » : ils passent entre deux pages de la homepage dès que leur heure est venue."""
        import sys
        import tempfile
        home = [("Home · FPS", i, "Jeu %d" % i, "https://www.allkeyshop.com/blog/home-%d/" % i) for i in range(1, 7)]
        top = [("Popular", 1, "EA SPORTS FC 27", "https://www.allkeyshop.com/blog/top/")]
        fetched, clock = [], [0.0]

        def fake_get(url, ua, follow=True, timeout=30):
            fetched.append(url.rstrip("/").rsplit("/", 1)[-1])
            return 200, None, self.PAGE

        def monotonic():
            clock[0] += 40  # chaque lecture de l'horloge : 40 s de plus
            return clock[0]

        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(pc, "fetch_targets", side_effect=lambda lists: top if lists is pc.TOP_GAMES_LISTS else home), \
                mock.patch.object(pc, "http_get", side_effect=fake_get), mock.patch.object(pc, "check_offer", self.ok), \
                mock.patch.object(pc.time, "monotonic", monotonic), mock.patch.object(pc.time, "sleep"), \
                mock.patch.object(pc, "REPORTS_DIR", ""), mock.patch.object(pc, "NOTIFY_OK", False), \
                mock.patch.object(sys, "argv", ["price_check.py", "--mode", "both", "--once", "--dry-run",
                                                "--state", os.path.join(d, "state.json")]):
            pc.main()
        first, last = fetched.index("home-1"), fetched.index("home-6")
        self.assertEqual(fetched[0], "top")
        self.assertIn("top", fetched[first:last])  # un passage top games au milieu de la homepage

    def test_admin_requests_and_status(self):
        """Romain, 02/10/2026 : deux boutons dans l'admin, « Price check top » et « Price check homepage ». L'admin dépose
        run-<mode>.request dans le dossier partagé ; le moniteur le lit, lance le passage et publie status.json."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "run-top-games.request"), "w") as f:
                f.write('{"by": "romain", "at": "2026-10-02T15:00:00+02:00"}')
            with open(os.path.join(d, "run-ailleurs.request"), "w") as f:
                f.write("{}")  # un mode inconnu n'est pas lu
            self.assertEqual(pc.take_requests(d, ["top-games", "homepage"]), [("top-games", "romain")])
            self.assertFalse(os.path.exists(os.path.join(d, "run-top-games.request")))  # consommé
            self.assertEqual(pc.take_requests(d, ["top-games", "homepage"]), [])
            with open(os.path.join(d, "run-homepage.request"), "w") as f:
                f.write("pas du json")
            self.assertEqual(pc.take_requests(d, ["top-games", "homepage"]), [("homepage", "admin")])
            pc.write_status(d, {"offers": "top-offers", "modes": {}})
            with open(os.path.join(d, "status.json"), encoding="utf-8") as f:
                status = json.load(f)
            self.assertEqual(status["offers"], "top-offers")
            self.assertRegex(status["updated_at"], r"^\d{4}-\d{2}-\d{2}T")
        self.assertEqual(pc.take_requests("", ["top-games"]), [])  # pas de dossier partagé : rien

    def test_status_published_during_a_pass(self):
        import sys
        import tempfile
        top = [("Popular", 1, "EA SPORTS FC 27", "https://www.allkeyshop.com/blog/top/")]
        seen = []

        def progress_spy(count, total):
            seen.append((count, total))

        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(pc, "fetch_targets", return_value=top), \
                mock.patch.object(pc, "http_get", return_value=(200, None, self.PAGE)), mock.patch.object(pc, "check_offer", self.ok), \
                mock.patch.object(pc.time, "sleep"), mock.patch.object(pc, "REPORTS_DIR", d), mock.patch.object(pc, "NOTIFY_OK", False), \
                mock.patch.object(sys, "argv", ["price_check.py", "--mode", "top-games", "--once", "--dry-run",
                                                "--state", os.path.join(d, "state.json")]):
            with open(os.path.join(d, "run-top-games.request"), "w") as f:
                f.write('{"mode": "top-games", "by": "romain"}')  # un clic dans l'admin
            pc.main()
            self.assertFalse(os.path.exists(os.path.join(d, "run-top-games.request")))  # demande consommée
            with open(os.path.join(d, "status.json"), encoding="utf-8") as f:
                status = json.load(f)
        st = status["modes"]["top-games"]
        self.assertEqual((st["label"], st["pages"], st["running"], st["progress"]), ("Price check top", 1, False, None))
        self.assertEqual((st["requested_by"], st["last_requested_by"]), (None, "romain"))  # qui a lancé le dernier passage
        self.assertEqual(st["last_checked"], 11)  # les 11 offres Top Offers de la page
        self.assertTrue(st["last_start"] and st["last_end"] and st["next_at"])
        self.assertEqual(status["offers"], "top-offers")
        # run_cycle appelle progress(count, total) à chaque page
        with mock.patch.object(pc, "http_get", return_value=(200, None, self.PAGE)), mock.patch.object(pc, "NOTIFY_OK", False):
            pc.run_cycle(self.TARGETS * 3, lambda m: None, pc.load_state("/nonexistent"), self.ok, progress=progress_spy)
        self.assertEqual(seen, [(1, 3), (2, 3), (3, 3)])

    def test_rechecks(self):
        """Romain, 02/10/2026 : « il faut qu'il contrôle les offres déjà vues, comme ça on saura si elles sont réparées ou
        pas … toutes les offres concernées par le top check ». Passage demandé = recontrôle complet ; en automatique,
        les offres signalées toutes les heures."""
        import tempfile
        page = self.TARGETS[0][3]
        offers = pc.page_offers(self.trans, 3)
        fixed_id, still_id, faux_id, nv_id, ok_id = (str(o["id"]) for o in offers[:5])
        flagged = lambda **kw: dict({"verdict": "SUSPECT", "product": "EA SPORTS FC 27", "edition": "Standard", "merchant": "X",
                                     "reasons": ["region: AllKeyShop GLOBAL, merchant EU"], "page": page, "at": "2026-10-01 10:00",
                                     "seen": pc.time.time(), "sent_to": "urgent"}, **kw)  # déjà dans le bon salon
        def fresh_state():
            st = pc.load_state("/nonexistent")
            st["checked"] = {fixed_id: flagged(), still_id: flagged(), "999999": flagged(merchant="Retiré"),
                             faux_id: flagged(decision={"decision": "faux", "by": "romain"}),
                             nv_id: flagged(verdict="NON VÉRIFIABLE", reasons=["product name not found in the URL, merchant page unreadable"]),
                             ok_id: flagged(verdict="OK", reasons=[])}
            return st
        suspect = {"verdict": "SUSPECT", "reasons": ["region: AllKeyShop GLOBAL, merchant EU"], "notes": [], "url": "https://x/eu", "method": "URL"}
        checked = []

        def checker(product, o):
            checked.append(str(o["id"]))
            return self.ok(product, o) if str(o["id"]) == fixed_id else dict(suspect)

        # 1. recontrôle des seules offres signalées (automatique, toutes les heures)
        state, sent = fresh_state(), []
        with mock.patch.object(pc, "http_get", return_value=(200, None, self.PAGE)), mock.patch.object(pc, "NOTIFY_OK", False):
            outcome = pc.run_cycle(self.TARGETS, sent.append, state, checker, per_edition=3, recheck="flagged")
        self.assertEqual([checked.count(k) for k in (fixed_id, still_id, nv_id, faux_id, ok_id)], [1, 1, 1, 0, 0])
        # rien n'a changé pour l'offre « réparée » de ce test (pas d'URL ni de région en mémoire) : faux positif levé
        self.assertEqual(([e["merchant"] for e in outcome["removed"]], [e["fixed_kind"] for e in outcome["rules"]], outcome["checked"]),
                         (["Retiré"], ["rule"], 3))
        self.assertEqual((state["checked"][fixed_id]["fixed_from"], state["checked"][fixed_id]["verdict"]), ("SUSPECT", "OK"))
        self.assertEqual(state["checked"]["999999"]["fixed_how"], "offer removed from the page")
        self.assertEqual((len(outcome["still"]), state["checked"][nv_id]["verdict"], outcome["new"]), (2, "SUSPECT", []))
        self.assertTrue(state["checked"][still_id]["still_wrong_at"])
        # alertes : la NON VÉRIFIABLE devenue SUSPECT, plus les 6 offres de la page jamais vues (SUSPECT dans ce test)
        self.assertEqual(len(sent), 1 + 6)
        recap = pc.format_recheck("Price check top", "", outcome)
        self.assertIn("Re-check of the flagged offers** · Price check top · 3 offer(s)", recap)
        self.assertIn("✅ Repaired (1)", recap)
        self.assertIn("🧹 Old false positives cleared by the rules, nothing changed (1)", recap)
        self.assertIn("🔴 Still wrong (2)", recap)
        self.assertIn("Retiré — offer removed from the page", recap)
        with tempfile.TemporaryDirectory() as d:
            pc.export_reports(state, d)
            with open(os.path.join(d, "reports.json"), encoding="utf-8") as f:
                reports = {r["offer"]: r for r in json.load(f)["reports"]}
        self.assertEqual((reports[fixed_id]["fixed_kind"], reports["999999"]["fixed_from"], reports[still_id]["fixed_at"]),
                         ("rule", "SUSPECT", None))
        # 2. passage demandé depuis l'admin : TOUTES les offres retenues, l'offre OK comprise (nouvelle erreur, alertée)
        state, sent, checked[:] = fresh_state(), [], []
        with mock.patch.object(pc, "http_get", return_value=(200, None, self.PAGE)), mock.patch.object(pc, "NOTIFY_OK", False):
            outcome = pc.run_cycle(self.TARGETS, sent.append, state, checker, per_edition=3, recheck="all")
        self.assertEqual(checked.count(ok_id), 1)
        self.assertEqual(checked.count(faux_id), 0)  # un faux positif décidé reste hors recontrôle
        self.assertEqual(outcome["checked"], 4)  # fixed, still, nv, ok
        self.assertEqual([e["verdict"] for e in outcome["new"]], ["SUSPECT"])
        self.assertEqual(state["checked"][ok_id]["at"], state["checked"][ok_id]["last_recheck"])  # nouvelle erreur : date du jour
        self.assertEqual(len(sent), 1 + 1 + 6)  # la nouvelle erreur, la NV devenue SUSPECT, les 6 jamais vues
        recap = pc.format_recheck("Price check top", "romain", outcome, full=True)
        self.assertIn("Re-check of every offer** · Price check top (requested from the admin by romain) · 4 offer(s)", recap)
        self.assertIn("🆕 New errors (1)", recap)
        # 02/10/2026 : « réparée » seulement si l'offre a changé (URL, région, plateforme, édition) ; sinon c'est un
        # ancien faux positif que les règles ont levé (UFC 5 chez Eneba, Onimusha au PS Store US, recontrôle de 18:37)
        state = fresh_state()
        state["checked"][fixed_id].update(url="https://shop.example/ea-sports-fc-27-eu-key", region="EUROPE")
        with mock.patch.object(pc, "http_get", return_value=(200, None, self.PAGE)), mock.patch.object(pc, "NOTIFY_OK", False):
            outcome = pc.run_cycle(self.TARGETS, lambda m: None, state,
                                   lambda p, o: dict(self.ok(p, o), url="https://shop.example/ea-sports-fc-27-global-key")
                                   if str(o["id"]) == fixed_id else dict(suspect), per_edition=3, recheck="flagged")
        e = state["checked"][fixed_id]
        self.assertEqual(e["fixed_kind"], "repaired")
        self.assertTrue(e["fixed_how"].startswith("re-check OK, the offer changed (URL"), e["fixed_how"])
        # un recontrôle qui ne conclut pas (page illisible) ne défait pas un OK vérifié (G2A Witcher, 02/10/2026)
        state, sent = fresh_state(), []
        unverifiable = {"verdict": "À VÉRIFIER", "reasons": ["edition Bundle: name not checked in the URL, merchant page unreadable"],
                        "notes": [], "url": None, "method": "aucune"}
        with mock.patch.object(pc, "http_get", return_value=(200, None, self.PAGE)), mock.patch.object(pc, "NOTIFY_OK", False):
            outcome = pc.run_cycle(self.TARGETS, sent.append, state, lambda p, o: dict(unverifiable), per_edition=3, recheck="all")
        self.assertEqual(state["checked"][ok_id]["verdict"], "OK")
        self.assertEqual(state["checked"][still_id]["verdict"], "SUSPECT")
        self.assertEqual((outcome["new"], outcome["fixed"], outcome["still"]), ([], [], []))
        self.assertEqual(len(outcome["unknown"]), 4)  # fixed, still, ok, et la NON VÉRIFIABLE, qui le reste
        self.assertEqual(state["checked"][nv_id]["verdict"], "NON VÉRIFIABLE")
        recap = pc.format_recheck("Price check top", "romain", outcome, full=True)
        self.assertIn("⚪ Re-check without a conclusion, verdict unchanged (4)", recap)
        self.assertNotIn("Nouvelles erreurs", recap)
        # 3. sans recontrôle : rien
        with mock.patch.object(pc, "http_get", return_value=(200, None, self.PAGE)), mock.patch.object(pc, "NOTIFY_OK", False):
            outcome = pc.run_cycle(self.TARGETS, lambda m: None, pc.load_state("/nonexistent"), self.ok)
        self.assertEqual({k: v for k, v in outcome.items() if k != "first_checked"},
                         {"checked": 0, "fixed": [], "removed": [], "rules": [], "verified": [], "still": [], "new": [], "unknown": []})
        self.assertIn("Nothing to report", pc.format_recheck("Price check top", "", outcome))

    def test_recheck_tells_a_repair_behind_the_same_link(self):
        """02/10/2026 : une fiche Kinguin signalée en rupture puis de nouveau en stock garde le même lien, la même région,
        la même plateforme : c'est le marchand qui a réparé, pas une règle qui blanchit un faux positif. Le contrôle garde
        ce qu'il a vu au-delà du lien (fiche servie, titre de la page) et le recontrôle le compare."""
        link = "https://www.kinguin.net/category/55259/rust-eu-steam-altergift/"
        stock = pc.out_of_stock_reason("https://www.kinguin.net/category/55259/rust-de-pc-steam-altergift")
        o = offer(merchantName="Kinguin", region="GIFT EU", region_filter="STEAM GIFT EU", edition_rank=1, page_first=True)

        def recheck(entry, res):
            outcome = {"checked": 0, "fixed": [], "removed": [], "rules": [], "still": [], "new": [], "unknown": []}
            pc.apply_recheck(entry, "Popular", 1, "Rust", "https://aks/rust", o, res, lambda m: None, "2026-10-02 21:00",
                             pc.time.time(), outcome)
            return entry
        base = lambda **kw: dict({"verdict": "SUSPECT", "url": link, "region": "GIFT EU", "region_filter": "STEAM GIFT EU",
                                  "platform": "steam", "edition": "Standard", "reasons": [stock]}, **kw)
        ok = lambda ev: {"verdict": "OK", "reasons": [], "notes": [], "url": link, "method": "URL", "evidence": ev}
        nothing = pc.evidence_of(None, None)
        # 1. alerte d'avant le 02/10 (rien gardé de ce qui avait été vu) : seule la raison « en rupture » le dit
        e = recheck(base(), ok(nothing))
        self.assertEqual((e["fixed_kind"], e["fixed_how"]), ("repaired", "re-check OK, the offer changed (page served)"))
        self.assertEqual(e["evidence"], nothing)  # désormais gardé
        # 2. la fiche servie est gardée : elle était une autre, le lien sert de nouveau sa fiche
        served = pc.evidence_of("https://www.kinguin.net/category/55259/rust-de-pc-steam-altergift", None)
        self.assertEqual(recheck(base(evidence=served), ok(nothing))["fixed_kind"], "repaired")
        # 3. même lien, autre titre de page (le marchand a corrigé sa fiche) : réparée
        title = lambda t: pc.evidence_of(None, t)
        e = recheck(base(reasons=["another product at the merchant: « Nocturne » instead of « Rust » (page title)"],
                         evidence=title("Nocturne PC Steam CD Key | Kinguin")), ok(title("Rust EU Steam Altergift | Kinguin")))
        self.assertEqual((e["fixed_kind"], e["fixed_how"]), ("repaired", "re-check OK, the offer changed (merchant page)"))
        # 4. rien n'a changé, ni le lien ni ce qui a été vu : une règle a levé un faux positif
        e = recheck(base(reasons=["region: AllKeyShop GIFT EU, merchant GLOBAL"], evidence=title("Rust | Kinguin")),
                    ok(title("Rust | Kinguin")))
        self.assertEqual(e["fixed_kind"], "rule")
        e = recheck(base(reasons=["region: AllKeyShop GIFT EU, merchant GLOBAL"]), ok(nothing))  # d'avant le 02/10, sans rupture
        self.assertEqual(e["fixed_kind"], "rule")

    def test_recap_due(self):
        """Après un passage automatique, le récapitulatif ne part que s'il y a du nouveau ; un ancien faux
        positif levé par une règle en est (l'alerte envoyée sur Discord n'est plus valable)."""
        empty = {"checked": 5, "fixed": [], "removed": [], "rules": [], "still": [{}], "new": [], "unknown": [{}]}
        self.assertFalse(pc.recap_due(None, empty))
        self.assertTrue(pc.recap_due("romain", empty))
        for key in ("fixed", "removed", "rules", "new"):
            self.assertTrue(pc.recap_due(None, dict(empty, **{key: [{}]})), key)

    def test_webhook_per_mode(self):
        with mock.patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": "https://hook/top", "DISCORD_WEBHOOK_URL_HOMEPAGE": "https://hook/home"}):
            self.assertEqual((pc.webhook_for("top-games"), pc.webhook_for("homepage")), ("https://hook/top", "https://hook/home"))
        with mock.patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": "https://hook/top"}, clear=True):
            self.assertEqual(pc.webhook_for("homepage"), "https://hook/top")  # repli : le salon des top games

    def test_amazon_is_skipped_until_its_pages_are_readable(self):
        """Arbitrage du 01/10/2026 : « on skip tous les Amazon jusqu'à modifier notre façon de requêter leurs
        pages ». Ni contrôle, ni alerte, ni ligne « non vérifiable » ; les reports déjà notés disparaissent."""
        import tempfile
        for name in ("Amazon.fr", "Amazon.de", "Amazon.it"):
            self.assertTrue(pc.merchant_config("", name).get("skip"), name)
        self.assertFalse(pc.merchant_config("", "Kinguin").get("skip"))
        trans = copy.deepcopy(self.trans)
        cheapest = min((p for p in trans["prices"] if p["dispo"] and p["price"] != pc.NO_PRICE and not p["account"]),
                       key=lambda p: p["priceCard"])
        cheapest["merchantName"] = "Amazon.fr"
        checked, state = [], pc.load_state("/nonexistent")
        state["checked"][str(cheapest["id"])] = {"verdict": "NON VÉRIFIABLE", "merchant": "Amazon.fr", "product": "EA SPORTS FC 27"}
        state["checked"]["999"] = {"verdict": "SUSPECT", "merchant": "Amazon.de", "product": "Elden Ring Xbox Series"}
        with mock.patch.object(pc, "parse_game_page", return_value=trans), mock.patch.object(pc, "http_get", return_value=(200, None, "")), \
                mock.patch.object(pc, "NOTIFY_OK", False):
            pc.run_cycle(self.TARGETS, lambda m: None, state, lambda p, o: checked.append(o["merchantName"]) or self.ok(p, o))
        self.assertTrue(checked)
        self.assertNotIn("Amazon.fr", checked)
        self.assertNotIn(str(cheapest["id"]), state["checked"])
        with tempfile.TemporaryDirectory() as d:
            pc.export_reports(state, d)
            with open(os.path.join(d, "reports.json"), encoding="utf-8") as f:
                self.assertEqual(json.load(f)["reports"], [])  # l'Amazon.de déjà noté non plus

    def test_an_unverifiable_offer_that_becomes_the_first_price_is_sent(self):
        page = self.TARGETS[0][3]
        first = pc.page_offers(self.trans, 3)[0]
        state, sent = pc.load_state("/nonexistent"), []
        state["checked"][str(first["id"])] = {"verdict": "NON VÉRIFIABLE", "unverifiable": "first-price", "product": "EA SPORTS FC 27",
                                              "reasons": ["product name not found in the URL, merchant page unreadable"], "notes": [],
                                              "url": "https://shop.example/dp/B0", "method": "aucune"}
        with mock.patch.object(pc, "http_get", return_value=(200, None, self.PAGE)), mock.patch.object(pc, "NOTIFY_OK", False), \
                mock.patch.dict(pc.PAGE_LISTS, {page: {"Popular"}}, clear=True):
            pc.run_cycle(self.TARGETS, sent.append, state, self.ok, per_edition=3)
            self.assertEqual(state["checked"][str(first["id"])]["verdict"], "À VÉRIFIER")
            self.assertEqual(len(sent), 1)
            self.assertIn("🟠 **TO CHECK**", sent[0])
            self.assertIn("became the page's first price", sent[0])
            pc.run_cycle(self.TARGETS, sent.append, state, self.ok, per_edition=3)
        self.assertEqual(len(sent), 1)  # une seule fois
        # une entrée d'avant le 01/10/2026, sans politique enregistrée : celle de la config du marchand (aucune : first-price)
        state["checked"][str(first["id"])].update(verdict="NON VÉRIFIABLE", unverifiable=None, merchant="G2A")
        with mock.patch.object(pc, "http_get", return_value=(200, None, self.PAGE)), mock.patch.object(pc, "NOTIFY_OK", False), \
                mock.patch.dict(pc.PAGE_LISTS, {page: {"Popular"}}, clear=True):
            pc.run_cycle(self.TARGETS, sent.append, state, self.ok, per_edition=3)
        self.assertEqual(len(sent), 2)
        # une politique « note » ne passe jamais en À VÉRIFIER
        state["checked"][str(first["id"])].update(verdict="NON VÉRIFIABLE", unverifiable="note")
        with mock.patch.object(pc, "http_get", return_value=(200, None, self.PAGE)), mock.patch.object(pc, "NOTIFY_OK", False), \
                mock.patch.dict(pc.PAGE_LISTS, {page: {"Popular"}}, clear=True):
            pc.run_cycle(self.TARGETS, sent.append, state, self.ok, per_edition=3)
        self.assertEqual(len(sent), 2)

    def test_export_carries_the_rank_and_the_last_time_seen(self):
        import tempfile
        state = pc.load_state("/nonexistent")
        with mock.patch.object(pc, "http_get", return_value=(200, None, self.PAGE)), mock.patch.object(pc, "NOTIFY_OK", False):
            pc.run_cycle(self.TARGETS, lambda m: None, state,
                         lambda p, o: {"verdict": "SUSPECT", "reasons": ["x"], "notes": [], "url": None, "method": "URL"}, per_edition=3)
        with tempfile.TemporaryDirectory() as d:
            pc.export_reports(state, d)
            with open(os.path.join(d, "reports.json"), encoding="utf-8") as f:
                reports = json.load(f)["reports"]
        self.assertEqual(len(reports), 11)
        self.assertEqual(sorted({r["edition_rank"] for r in reports}), [1, 2, 3])
        self.assertTrue(all(isinstance(r["seen"], float) for r in reports))
        self.assertEqual(sum(bool(r["page_first"]) for r in reports), 1)


def aks_page(prices, product="Jeu"):
    """Une page produit AllKeyShop minimale : gamePageTrans avec ces offres (édition Standard, région STEAM GLOBAL)."""
    base = {"merchant": 47, "merchantName": "Kinguin", "edition": "1", "region": "412", "dispo": 1, "account": False,
            "activationPlatform": "steam"}
    trans = {"editions": {"1": {"name": "Standard"}}, "regions": {"412": {"region_name": "GLOBAL", "filter_name": "STEAM GLOBAL"}},
             "merchants": {}, "prices": [dict(base, priceCard=p.get("price", 10.0), **p) for p in prices]}
    return "<html><script>var gamePageTrans = %s;\n</script></html>" % json.dumps(trans)


@mock.patch.object(pc, "REQUEST_DELAY", 0)
@mock.patch.object(pc, "PAGE_DELAY", 0)
class TestLoopAudit20261002(unittest.TestCase):
    """Audit de la boucle, de l'état et du recontrôle (02/10/2026)."""
    PAGE = "https://www.allkeyshop.com/blog/buy-jeu-cd-key-compare-prices/"
    TARGETS = [("Popular", 1, "Jeu", PAGE)]

    def setUp(self):
        pc.FAILURES.clear()

    @staticmethod
    def flagged(**kw):
        return dict({"verdict": "SUSPECT", "product": "Jeu", "edition": "Standard", "merchant": "Kinguin", "page": TestLoopAudit20261002.PAGE,
                     "reasons": ["another product at the merchant: « Autre » instead of « Jeu » (URL)"], "url": "https://www.kinguin.net/category/1/autre",
                     "region": "GLOBAL", "region_filter": "STEAM GLOBAL", "platform": "steam", "at": "2026-10-01 10:00", "seen": pc.time.time()}, **kw)

    def cycle(self, page_html, state, checker, recheck=False, sent=None):
        with mock.patch.object(pc, "http_get", return_value=(200, None, page_html)), mock.patch.object(pc, "NOTIFY_OK", False):
            return pc.run_cycle(self.TARGETS, (sent if sent is not None else []).append, state, checker, per_edition=3, recheck=recheck)

    def test_an_offer_without_price_is_not_repaired_and_a_removed_one_is_rechecked_when_back(self):
        # constat 1 : une offre signalée passée « sans prix » (0.02) devenait « réparée », puis n'était plus contrôlée
        state = pc.load_state("/nonexistent")
        state["checked"]["1001"] = self.flagged()
        calls = []
        wrong = lambda product, o: calls.append(o["id"]) or {"verdict": "SUSPECT", "reasons": ["another product"], "notes": [],
                                                              "url": "https://www.kinguin.net/category/1/autre", "method": "URL"}
        other = {"id": 1002, "price": 12.0}
        outcome = self.cycle(aks_page([{"id": 1001, "price": 0.02}, other]), state, wrong, recheck="flagged")
        e = state["checked"]["1001"]
        self.assertEqual((e["verdict"], e.get("fixed_at"), outcome["removed"]), ("SUSPECT", None, []))
        self.assertEqual(outcome["unknown"][0][1], "offer without a price on the page for now")
        # page servie sans offres : rien n'est « retiré »
        outcome = self.cycle(aks_page([]), state, wrong, recheck="flagged")
        self.assertEqual((state["checked"]["1001"]["verdict"], outcome["removed"]), ("SUSPECT", []))
        # vraiment retirée de la page : réparée, et marquée pour être recontrôlée si elle revient
        outcome = self.cycle(aks_page([other]), state, wrong, recheck="flagged")
        self.assertEqual((e["verdict"], e["fixed_how"], bool(e.get("removed_at"))), ("OK", "offer removed from the page", True))
        # elle revient au premier prix : recontrôlée tout de suite, même sans recontrôle prévu, et alertée si elle est fausse
        calls.clear()
        sent = []
        outcome = self.cycle(aks_page([{"id": 1001, "price": 9.0}, other]), state, wrong, recheck=False, sent=sent)
        self.assertIn(1001, calls)
        self.assertEqual(([x["product"] for x in outcome["new"]], e["verdict"], e.get("fixed_at"), "removed_at" in e),
                         (["Jeu"], "SUSPECT", None, False))
        self.assertTrue(any("**Jeu**" in m for m in sent))

    def test_an_unexpected_error_never_stops_the_pass(self):
        # constat 2 : une exception autre que CheckError (IncompleteRead, InvalidURL…) arrêtait le passage, à chaque fois
        state = pc.load_state("/nonexistent")
        state["checked"]["1001"] = self.flagged()
        seen = []

        def checker(product, o):
            seen.append(o["id"])
            if o["id"] == 1001:
                raise ValueError("URL can't contain control characters")
            return {"verdict": "OK", "reasons": [], "notes": [], "url": "https://x.example/jeu", "method": "URL"}
        outcome = self.cycle(aks_page([{"id": 1001, "price": 9.0}, {"id": 1002, "price": 12.0}]), state, checker, recheck="all")
        self.assertEqual(seen, [1001, 1002])  # la suite du passage a eu lieu
        self.assertEqual((pc.FAILURES.get("1001"), outcome["unknown"][0][1][:12]), (1, "check failed"))
        self.assertEqual(state["checked"]["1001"]["verdict"], "SUSPECT")
        with mock.patch.object(pc.urllib.request.OpenerDirector, "open", side_effect=pc.http.client.IncompleteRead(b"x", 10)):
            with self.assertRaises(OSError):
                pc.http_get("https://www.kinguin.net/category/1/x", pc.BROWSER_UA)
        self.assertEqual(pc.quote_url("https://www.example.com/p/12345 6?q=é&r=%20"), "https://www.example.com/p/12345%206?q=%C3%A9&r=%20")

    def test_a_failed_discord_send_is_queued_never_lost(self):
        # constat 4 : une nouvelle erreur trouvée au recontrôle dont l'envoi échouait n'était jamais renvoyée
        state = pc.load_state("/nonexistent")
        with mock.patch.object(pc, "MUTE_UNTIL", ""), mock.patch.object(pc, "send_discord", side_effect=OSError("503")) as send:
            notify = pc.make_notifier("https://hook", state, "homepage")
            notify("alerte 1")
            notify("alerte 2")  # la file n'est pas vide : elle passe après, sans essai
        self.assertEqual((state["queued"], send.call_count), ([["homepage", "alerte 1"], ["homepage", "alerte 2"]], 1))
        sent = []

        def send_or_refuse(msg, channel):
            if msg == "alerte 1":
                raise pc.urllib.error.HTTPError("https://hook", 400, "Bad Request", {}, None)
            sent.append(msg)
        with mock.patch.object(pc.time, "sleep"):
            pc.flush_queue(state, send_or_refuse)
        self.assertEqual((sent, state["queued"]), (["alerte 2"], []))  # refusée pour de bon : retirée, pas bloquante

    def test_send_discord_cuts_long_messages_and_waits_on_429(self):
        bodies = []

        def urlopen(req, timeout=30):
            bodies.append(json.loads(req.data)["content"])
            if len(bodies) == 1:
                raise pc.urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, __import__("io").BytesIO(b'{"retry_after": 0.01}'))
            return mock.MagicMock()
        with mock.patch.object(pc.urllib.request, "urlopen", side_effect=urlopen), mock.patch.object(pc.time, "sleep") as sleep:
            pc.send_discord("https://hook", "x" * 5000)
        self.assertEqual((len(bodies), len(bodies[-1])), (2, 2000))
        sleep.assert_called_once()

    def test_a_corrupt_state_is_kept_aside_and_taken_from_the_backup(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            self.assertEqual(pc.load_state(path)["checked"], {})  # absent : vide (on recontrôle tout), jamais la copie
            with open(path + ".bak", "w") as f:
                json.dump({"checked": {"1": {"verdict": "OK"}}}, f)
            self.assertEqual(pc.load_state(path)["checked"], {})
            with open(path, "w") as f:
                f.write('{"checked": {"1": ')  # coupé en pleine écriture
            self.assertEqual(pc.load_state(path)["checked"], {"1": {"verdict": "OK"}})
            self.assertTrue(any(n.startswith("state.json.corrupt-") for n in os.listdir(d)))

    def test_shared_files_never_block_nor_crash(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            os.mkfifo(os.path.join(d, "run-homepage.request"))  # un tube nommé bloquait la boucle
            self.assertEqual(pc.take_requests(d, ["homepage"]), [("homepage", "admin")])
            with open(os.path.join(d, "run-top-games.request"), "w") as f:
                f.write("[]")  # JSON valide mais pas un objet : plantait le service en boucle
            self.assertEqual(pc.take_requests(d, ["top-games"]), [("top-games", "admin")])
            with open(os.path.join(d, "decisions.jsonl"), "wb") as f:
                f.write(b'{"offer": "1", "decision": "faux", "note": "caf\xe9"}\n{"offer": "2", "decision": "vrai"}\n')
            self.assertEqual(sorted(pc.read_decisions(d)), ["1", "2"])  # un octet non UTF-8 ne coupe plus tout

    def test_recheck_classification_fixes(self):
        o = offer(region="GLOBAL", region_filter="STEAM GLOBAL", edition_rank=1, page_first=True)
        ok = {"verdict": "OK", "reasons": [], "notes": [], "url": "https://www.kinguin.net/category/1/jeu", "method": "URL"}

        def recheck(entry, res=ok):
            outcome = {"checked": 0, "fixed": [], "removed": [], "rules": [], "verified": [], "still": [], "new": [], "unknown": []}
            pc.apply_recheck(entry, "Popular", 1, "Jeu", self.PAGE, o, res, lambda m: None, "2026-10-02 22:00", pc.time.time(), outcome)
            return entry, outcome
        base = dict(self.flagged(), url="https://www.kinguin.net/category/1/jeu")
        # (a) une offre qui n'avait pas pu être vérifiée, trouvée OK : vérifiée, ni « réparée » ni « faux positif levé »
        e, outcome = recheck(dict(base, verdict="NON VÉRIFIABLE"))
        self.assertEqual((e["fixed_kind"], len(outcome["verified"]), e["fixed_how"]), ("verified", 1, "verified OK at the re-check"))
        # (b) une entrée ancienne sans nom de filtre n'a pas « changé de région »
        e, _ = recheck(dict(base, region_filter=""))
        self.assertEqual(e["fixed_kind"], "rule")
        e, _ = recheck(dict(base, region="EUROPE", region_filter="STEAM EU"))
        self.assertEqual((e["fixed_kind"], e["fixed_how"]), ("repaired", "re-check OK, the offer changed (region)"))
        # (c) une offre réparée redevenue fausse perd son classement
        wrong = dict(ok, verdict="SUSPECT", reasons=["another product"])
        e, outcome = recheck(dict(base, verdict="OK", fixed_at="2026-10-02 18:00", fixed_kind="rule", still_wrong_at="2026-10-02 17:00"), wrong)
        self.assertEqual((e.get("fixed_kind"), e.get("still_wrong_at"), len(outcome["new"])), (None, None, 1))
        recap = pc.format_recheck("Price check top", "", {"checked": 2, "fixed": [e], "removed": [], "rules": [], "verified": [],
                                                          "still": [], "new": [e], "unknown": []})
        self.assertLess(recap.index("🆕 New errors"), recap.index("✅ Repaired"))  # le plus important d'abord

    def test_a_false_positive_decision_is_never_alerted(self):
        # audit des tests : une offre NON VÉRIFIABLE jugée « faux » partait quand même en À VÉRIFIER au premier prix
        state = pc.load_state("/nonexistent")
        state["checked"]["1001"] = self.flagged(verdict="NON VÉRIFIABLE", decision={"decision": "faux", "by": "romain"})
        sent = []
        self.cycle(aks_page([{"id": 1001, "price": 9.0}]), state, lambda p, o: self.fail("jamais recontrôlée"), sent=sent)
        self.assertEqual(sent, [])
        state["checked"]["1001"].pop("decision")
        self.cycle(aks_page([{"id": 1001, "price": 9.0}]), state, lambda p, o: self.fail("jamais recontrôlée"), sent=sent)
        self.assertEqual(len(sent), 1)  # sans la décision : la règle des offres non vérifiables s'applique

    def test_ignored_merchant_and_entries_without_page(self):
        state = pc.load_state("/nonexistent")
        state["checked"]["2001"] = self.flagged(merchant="Amazon")
        old = self.flagged()
        old.pop("page")  # entrée d'avant le 01/10 : sans sa page
        state["checked"]["2002"] = old
        calls = []
        checker = lambda p, o: calls.append(o["id"]) or {"verdict": "OK", "reasons": [], "notes": [], "url": "https://www.kinguin.net/category/1/jeu",
                                                         "method": "URL"}
        page = aks_page([{"id": 1, "price": 5.0}, {"id": 2, "price": 6.0}, {"id": 3, "price": 7.0}, {"id": 2001, "price": 8.0, "merchantName": "Amazon"},
                         {"id": 2002, "price": 9.0}])
        outcome = self.cycle(page, state, checker, recheck="flagged")
        self.assertNotIn("2001", state["checked"])  # marchand ignoré : ni recontrôle, ni report
        self.assertNotIn(2001, calls)
        self.assertEqual((state["checked"]["2002"]["page"], state["checked"]["2002"]["verdict"]), (self.PAGE, "OK"))
        self.assertIn(2002, calls)


@mock.patch.object(pc, "REQUEST_DELAY", 0)
@mock.patch.object(pc, "PAGE_DELAY", 0)
class TestMainLoopAudit20261002(unittest.TestCase):
    """La boucle principale (audit du 02/10/2026) : arrêt propre, reprise d'un passage demandé, demande pendant un passage."""
    TOP = [("Popular", 1, "Jeu", "https://www.allkeyshop.com/blog/buy-jeu-cd-key-compare-prices/")]

    def setUp(self):
        pc.STOP["asked"] = False
        self.addCleanup(pc.STOP.__setitem__, "asked", False)

    def run_main(self, d, mode, run_cycle):
        import sys
        with mock.patch.object(pc, "fetch_targets", return_value=self.TOP), mock.patch.object(pc, "run_cycle", side_effect=run_cycle), \
                mock.patch.object(pc.time, "sleep"), mock.patch.object(pc, "REPORTS_DIR", d), mock.patch.object(pc.signal, "signal"), \
                mock.patch.object(sys, "argv", ["price_check.py", "--mode", mode, "--dry-run", "--state", os.path.join(d, "state.json")]):
            pc.main()
        return pc.load_state(os.path.join(d, "state.json"))

    @staticmethod
    def outcome():
        return {"checked": 0, "fixed": [], "removed": [], "rules": [], "verified": [], "still": [], "new": [], "unknown": [], "first_checked": 0}

    def test_stop_mid_pass_then_resume_the_requested_pass(self):
        import tempfile
        calls = []
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "run-homepage.request"), "w") as f:
                f.write('{"by": "romain"}')

            def interrupted(targets, notify, state, checker=None, **kw):
                calls.append((kw["recheck"], kw.get("resume_after")))
                state["checked"]["1"] = {"verdict": "SUSPECT", "product": "Jeu", "last_recheck": "2026-10-02 19:05"}
                pc.STOP["asked"] = True  # SIGTERM pendant le passage
                raise pc.Stop()
            state = self.run_main(d, "homepage", interrupted)
            self.assertEqual(calls, [("all", None)])
            self.assertEqual(state["checked"]["1"]["verdict"], "SUSPECT")  # l'état est écrit à l'arrêt
            self.assertEqual(state["running"]["homepage"]["by"], "romain")  # le passage demandé est à reprendre
            started = state["running"]["homepage"]["started"]
            pc.STOP["asked"] = False

            def resumed(targets, notify, state, checker=None, **kw):
                calls.append((kw["recheck"], kw.get("resume_after")))
                pc.STOP["asked"] = True  # le passage suivant (automatique) arrête le test
                if len(calls) > 2:
                    raise pc.Stop()
                return self.outcome()
            state = self.run_main(d, "homepage", resumed)
            self.assertEqual(calls[1], ("all", started))  # reprise : recontrôle complet, sans refaire le déjà fait
            self.assertNotIn("homepage", state.get("running") or {})

    def test_a_request_during_a_pass_of_the_same_mode_runs_a_full_recheck_after_it(self):
        import tempfile
        calls = []
        with tempfile.TemporaryDirectory() as d:
            def run_cycle(targets, notify, state, checker=None, **kw):
                calls.append(kw["recheck"])
                if len(calls) == 1:  # passage automatique ; un clic dans l'admin arrive entre deux pages
                    with open(os.path.join(d, "run-homepage.request"), "w") as f:
                        f.write('{"by": "remi"}')
                    kw["between"]()
                    return self.outcome()
                pc.STOP["asked"] = True
                raise pc.Stop()
            self.run_main(d, "homepage", run_cycle)
        self.assertEqual(calls, ["flagged", "all"])

    def test_a_failed_requested_pass_is_retried(self):
        import tempfile
        calls = []
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "run-top-games.request"), "w") as f:
                f.write('{"by": "romain"}')

            def run_cycle(targets, notify, state, checker=None, **kw):
                calls.append(kw["recheck"])
                if len(calls) == 1:
                    raise RuntimeError("API des listes en panne")
                pc.STOP["asked"] = True
                raise pc.Stop()
            with mock.patch.object(pc.time, "monotonic", side_effect=itertools_count()):
                self.run_main(d, "top-games", run_cycle)
        self.assertEqual(calls, ["all", "all"])


def itertools_count(start=10_000.0, step=200.0):
    """Une horloge monotone qui avance de 200 s à chaque lecture : les intervalles des modes passent sans attendre."""
    value = [start]

    def tick():
        value[0] += step
        return value[0]
    while True:
        yield tick()


class TestTopOffersTrueErrors20261001(unittest.TestCase):
    """Les vraies erreurs du premier jour de Top Offers (docs/precedents.md, 01-02/10/2026) qui n'avaient pas de test
    (audit des tests du 02/10/2026 : casser la règle qui les attrape laissait la suite verte)."""

    def reasons(self, product, url, **kw):
        return pc.analyze(product, offer(**kw), pc.url_text(url), "URL")["reasons"]

    def test_crusader_kings_3_eneba_eu_key_shown_english_only(self):
        # « IN ENGLISH ONLY » (STEAM ENG ONLY) est une clé mondiale pour AllKeyShop ; Eneba vend une clé EUROPE
        self.assertEqual(self.reasons("Crusader Kings 3", "https://www.eneba.com/steam-crusader-kings-iii-starter-edition-pc-steam-key-europe",
                                      edition="Starter Edition", region="IN ENGLISH ONLY", region_filter="STEAM ENG ONLY"),
                         ["region: AllKeyShop IN ENGLISH ONLY, merchant EU"])

    def test_hunt_showdown_gameseal_emea_shown_row(self):
        self.assertEqual(self.reasons("Hunt Showdown", "https://gameseal.com/hunt-showdown-1896-pc-steam-key-emea",
                                      region="ROW", region_filter="STEAM ROW"), ["region: AllKeyShop ROW, merchant EMEA"])

    def test_black_ops_3_gamivo_eu_gift_shown_row(self):
        self.assertEqual(self.reasons("Call of Duty Black Ops 3", "https://www.gamivo.com/product/call-of-duty-black-ops-iii-zombies-chronicles-edition-eu",
                                      edition="Limited", region="ROW", region_filter="STEAM ROW"), ["region: AllKeyShop ROW, merchant EU"])

    def test_monster_hunter_wilds_g2a_row_shown_europe(self):
        self.assertEqual(self.reasons("Monster Hunter Wilds", "https://www.g2a.com/en/monster-hunter-wilds-deluxe-edition-pc-steam-key-row-i10000507334026",
                                      edition="Deluxe", region="EUROPE", region_filter="STEAM EU"), ["region: AllKeyShop EUROPE, merchant ROW"])

    def test_space_marine_2_anniversary_package_in_gold(self):
        # Steam /sub/997629 : le paquet « 1-Year Anniversary Edition » rangé en Gold, alors que la page a cette édition
        o = offer(edition="Gold", region="GLOBAL", region_filter="STEAM GLOBAL",
                  page_editions=["Standard", "Gold", "1 Year Anniversary Edition", "Ultra"])
        r = pc.analyze("Warhammer 40k Space Marine 2", o, "Warhammer 40,000: Space Marine 2 - 1-Year Anniversary Edition on Steam",
                       "page title")
        self.assertEqual(r["reasons"], ["edition: filed under Gold, the merchant sells anniversary (the page has the edition 1 Year Anniversary Edition)"])


@mock.patch.object(pc, "REQUEST_DELAY", 0)
@mock.patch.object(pc, "PAGE_DELAY", 0)
class TestDetectionAudit20261002(unittest.TestCase):
    """Audit de la détection du 02/10/2026 : rejeu des 3 614 URL marchand en mémoire contre les 495 pages suivies ;
    824 paires (page A, vraie URL du produit B) passaient le contrôle du nom, 84 après ces règles. La crainte de
    Romain : « Sonic 1 ou un vieux Mario sur la page du dernier Sonic »."""

    def match(self, product, url, edition="Standard"):
        return pc.analyze(product, offer(edition=edition), pc.url_text(url), "URL")["match"]

    def test_no_acronym_of_the_whole_name(self):
        # « ron » (Ready Or Not) était trouvé dans « hearts-of-iron », « ace » (Assetto Corsa EVO) dans « ace-combat »,
        # « eft » (Escape From Tarkov) dans « grand-theft-auto » : des URL réelles, toutes jugées OK
        self.assertIsNone(self.match("Ready Or Not", "https://www.eneba.com/steam-hearts-of-iron-iv-pc-steam-key-europe"))
        self.assertIsNone(self.match("Assetto Corsa EVO", "https://www.gamivo.com/product/ace-combat-8-wings-of-theve-pc-steam-global"))
        self.assertIsNone(self.match("Escape from Tarkov", "https://www.gamivo.com/product/grand-theft-auto-v-gta-5-rockstar-eu"))
        self.assertNotIn("mns", pc.name_variants("Minecraft Nintendo Switch"))  # jamais un sigle sur un suffixe de plateforme
        self.assertIn("aot 3", pc.name_variants("Attack on Titan 3"))  # un sigle suivi du reste du nom reste
        self.assertEqual(self.match("Attack on Titan 3", "https://shop.example/a-o-t-3-pc-steam"), "exact")

    def test_whole_words_and_never_a_sequel(self):
        self.assertIsNone(self.match("Rust", "https://shop.example/rusty-lake-hotel-pc-steam"))
        self.assertIsNone(self.match("Titanfall", "https://www.kinguin.net/category/25568/titanfall-2-deluxe-edition-ea-app-cd-key"))
        self.assertIsNone(self.match("GTA 5", "https://shop.example/grand-theft-auto-vi-ps5"))
        self.assertIsNone(self.match("Red Dead Redemption", "https://gameboost.com/red-dead-redemption-2-euus-00-1"))
        self.assertIsNone(self.match("Football Manager 2024", "https://shop.example/football-manager-2023-pc-steam"))
        self.assertIsNone(self.match("The Last of Us Part I", "https://www.driffle.com/the-last-of-us-part-ii-remastered-pc-steam-p1"))
        # ce qui n'est pas une suite : « 1-year », le « -1 » final de GAMIVO, l'année écrite à deux chiffres
        self.assertEqual(self.match("SnowRunner", "https://shop.example/snowrunner-1-year-anniversary-edition-pc-steam"), "exact")
        self.assertEqual(self.match("Stardew Valley", "https://www.gamivo.com/product/stardew-valley-1"), "exact")
        self.assertEqual(self.match("EA Sports WRC 2023", "https://kinguin.net/category/192006/ea-sports-wrc-23-steam-altergift"), "partial")
        self.assertEqual(self.match("Titanfall 2", "https://www.kinguin.net/category/25568/titanfall-2-deluxe-edition-ea-app-cd-key"), "exact")

    def test_never_an_old_game_for_the_new_one(self):
        # le dernier mot manquant, c'est le nouveau jeu ; un autre mot à la place d'un mot du nom, c'est un autre produit
        self.assertIsNone(self.match("Super Mario Party Jamboree Nintendo Switch", "https://www.eneba.com/nintendo-super-mario-party-nintendo-switch-europe"))
        self.assertIsNone(self.match("Star Wars Jedi Survivor", "https://shop.example/star-wars-jedi-fallen-order-pc-ea-app"))
        self.assertIsNone(self.match("Oblivion Remastered", "https://shop.example/oblivion-goty-pc-steam"))
        self.assertIsNone(self.match("Final Fantasy VII Remake", "https://shop.example/final-fantasy-vii-pc-steam"))
        self.assertIsNone(self.match("EA Sports UFC 5 PS5", "https://vidaplayer.com/product/playstation-4-5/ea-sports-fc-26-ps5"))
        # un autre mot à la place d'un mot du nom (« Liberty » pour « Vice ») : un autre jeu de la série
        self.assertIsNone(self.match("Grand Theft Auto Vice City", "https://shop.example/grand-theft-auto-liberty-city-stories-pc"))
        # la tolérance d'un mot absent reste quand rien ne prend sa place (« pokmon » : à une lettre près, déjà testé)
        self.assertEqual(self.match("Heroes of Might and Magic Olden Era", "https://shop.example/heroes-of-might-and-magic-era-pc"), "partial")

    def test_pokemon_scarlet_violet_dlc_page_covers_both(self):
        # Romain, 03/10/2026 : faux positif. Le DLC « The Hidden Treasure of Area Zero » existe pour Scarlet et pour Violet,
        # la page AllKeyShop « Pokemon Scarlet The Hidden Treasure of Area Zero » prend en compte les deux (GameBoost,
        # offre 138007132). « Spécifique à Pokémon, vraiment pas généraliser » : un alias pour ce produit seul
        pc._PRODUCT_ALIASES = None
        o = offer(platform="nintendo-eshop", region="EUROPE")
        url = "https://gameboost.com/pokemon-violet-the-hidden-treasure-of-area-zero-switch-eu-00-12005"
        r = pc.analyze("Pokemon Scarlet The Hidden Treasure of Area Zero Nintendo Switch", o, pc.url_text(url), "URL")
        self.assertEqual((r["match"], r["reasons"]), ("exact", []))
        # pas généralisé : le jeu Pokémon Violet n'est pas le jeu Pokémon Scarlet, ni Bouclier l'Épée
        r = pc.analyze("Pokemon Scarlet Nintendo Switch", o, pc.url_text("https://shop.example/pokemon-violet-nintendo-switch-eu"), "URL")
        self.assertTrue(r["reasons"] and r["reasons"][0].startswith("another product at the merchant: « Pokemon Violet"), r["reasons"])
        r = pc.analyze("Pokemon Sword Nintendo Switch", o, pc.url_text("https://shop.example/pokemon-shield-nintendo-switch-eu"), "URL")
        self.assertTrue(r["reasons"] and r["reasons"][0].startswith("another product"), r["reasons"])

    def test_short_page_title_only_the_end_of_the_name(self):
        o = offer(platform="playstation-store", region="PS5")
        self.assertIsNone(pc.analyze("God of War Ragnarok PS5", o, "God of War | Standard Edition", "page title")["match"])
        self.assertIsNone(pc.analyze("Call of Duty Black Ops 7", offer(), "Call of Duty: Black Ops | Steam", "page title")["match"])
        self.assertEqual(pc.analyze("Ace Combat 8 Wings of Theve", offer(), "Wings of Theve | Steam", "page title")["match"], "partial")

    def test_straight_apostrophe(self):
        # AllKeyShop écrit « Marvel's » : norm donnait « marvel-s », et le préfixe facultatif « marvels » ne s'appliquait pas
        self.assertEqual(pc.norm("Marvel's Spider-Man 2"), pc.norm("Marvel’s Spider-Man 2"))
        self.assertEqual(pc.analyze("Marvel's Spider-Man 2 PS5", offer(platform="playstation", region="EUROPE"),
                                    pc.url_text("https://shop.example/spider-man-2-ps5-psn-eu"), "URL")["reasons"], [])
        self.assertEqual(self.match("Sid Meier's Civilization VII", "https://shop.example/civilization-vii-pc-steam"), "exact")

    def test_slugs_decoded_and_extensions_dropped(self):
        # CJS CDKeys encode deux fois (« E%252dDay ») ; Mmoga finit par « .html » (« 2.html » donnait « 2html »)
        url = "https://www.cjs-cdkeys.com/products/Gears-of-War-E%252dDay-Premium-Edition-Digital-Download-Key-%28Xbox-%7B47%7D-Windows%29.html"
        self.assertEqual(self.match("Gears of War E-Day", url, edition="Premium"), "exact")
        self.assertEqual(self.match("Planet Zoo 2", "https://www.mmoga.com/Steam-Games/Planet-Zoo-2.html"), "exact")
        self.assertEqual(self.match("SnowRunner PS5", "https://www.cjs-cdkeys.com/products/SnowRunner-1%252dYear-Anniversary-Edition-PSN-Download-Key-%28Playstation%29-UNITED-STATES.html",
                                    edition="1 Year Anniversary Edition"), "exact")

    def test_xbox_pc_regions_have_a_zone_and_us_uk_keys_are_read(self):
        # le nom de filtre et le nom affiché étaient concaténés (« XBOX/PC XBOX/PC ») : les ancres ne trouvaient rien
        self.assertEqual(pc.aks_zone({"region": "XBOX/PC", "region_filter": "XBOX/PC"}), "GLOBAL")
        self.assertEqual(pc.aks_zone({"region": "XBOX X|S", "region_filter": "XBOX X|S"}), "GLOBAL")
        self.assertEqual(pc.aks_zone({"region": "XBOX/PC EU", "region_filter": "XBOX/PC EU"}), "EU")
        r = lambda url, **kw: pc.analyze("EA SPORTS FC 27 PS5", offer(platform="playstation", **kw), pc.url_text(url), "URL")["reasons"]
        cjs = "https://www.cjs-cdkeys.com/products/EA-SPORTS-FC-27-Standard-Edition-PSN-Download-Key-%28Playstation%29-UNITED-STATES.html"
        self.assertEqual(r(cjs, region="USA", region_filter="USA"), [])  # clé US affichée USA : rien
        # clé US affichée EUROPE sur console : admise depuis le 08/10/2026 (Romain) ; sur PC elle alerte toujours
        self.assertEqual(r(cjs, region="EUROPE", region_filter="PSN EU"), [])
        self.assertEqual(pc.analyze("EA SPORTS FC 27", offer(platform="steam", region="EUROPE", region_filter="STEAM EU"),
                                    pc.url_text("https://shop.example/ea-sports-fc-27-pc-steam-key-united-states"), "URL")["reasons"],
                         ["region: AllKeyShop EUROPE, merchant US"])
        self.assertEqual(r("https://shop.example/ea-sports-fc-27-ps5-united-kingdom", region="GLOBAL", region_filter="PSN GLOBAL"),
                         ["region: AllKeyShop GLOBAL, merchant UK"])
        self.assertEqual(pc.analyze("Among Us VR", offer(), pc.url_text("https://www.loaded.com/among-us-3d-vr-pc-steam"), "URL")["reasons"], [])

    def test_dlc_page_or_game_page_with_a_dlc_edition(self):
        # pages réelles du 02/10/2026 : Hearts of Iron 4 (55 offres Standard, 1 DLC) est la page d'un jeu ; Diablo 4 Lord of
        # Hatred (DLC 19, Deluxe 17, Ultimate 15, pas de Standard) et le DLC Pokémon Scarlet (DLC 27, Standard 1) sont des DLC
        def trans(**counts):
            names = {"standard": "Standard", "dlc": "DLC", "deluxe": "Deluxe", "ultimate": "Ultimate", "bundle": "Bundle"}
            prices = [{"edition": key, "price": 10.0, "dispo": 1} for key, n in counts.items() for _ in range(n)]
            return {"editions": {key: {"name": names[key]} for key in counts}, "prices": prices}
        self.assertEqual(pc.dlc_page_kind(trans(standard=55, dlc=1), "Hearts of Iron 4"), "edition")
        self.assertEqual(pc.dlc_page_kind(trans(dlc=19, deluxe=17, ultimate=15), "Diablo 4 Lord of Hatred"), "page")
        self.assertEqual(pc.dlc_page_kind(trans(dlc=27, standard=1, bundle=1), "Pokemon Scarlet The Hidden Treasure of Area Zero"), "page")
        self.assertEqual(pc.dlc_page_kind(trans(standard=3), "Farming Simulator 25 Year 1 Season Pass"), "page")  # son nom le dit
        self.assertIsNone(pc.dlc_page_kind(trans(standard=3, deluxe=2), "Hearts of Iron 4"))
        self.assertTrue(pc.offer_on_dlc_page("edition", {"edition": "DLC"}))
        self.assertFalse(pc.offer_on_dlc_page("edition", {"edition": "Standard"}))  # un DLC rangé en Standard : alerte
        url = "https://www.eneba.com/steam-hearts-of-iron-iv-no-step-back-dlc-pc-steam-key-global"
        o = offer(page_dlc=pc.offer_on_dlc_page("edition", {"edition": "Standard"}))
        self.assertEqual(pc.analyze("Hearts of Iron 4", o, pc.url_text(url), "URL")["reasons"], ["additional content: dlc"])

    def test_pass_and_add_on_are_extra_content(self):
        # le Booster Course Pass vendu sur la page du jeu de base, sans le mot « dlc »
        r = pc.analyze("Mario Kart 8 Deluxe Nintendo Switch", offer(platform="nintendo-eshop", region="EUROPE"),
                       pc.url_text("https://www.g2a.com/mario-kart-8-deluxe-booster-course-pass-nintendo-switch-nintendo-eshop-key-europe-i1"), "URL")
        self.assertEqual(r["reasons"], ["additional content: pass"])
        r = pc.analyze("Cities Skylines 2", offer(), pc.url_text("https://shop.example/cities-skylines-2-beach-properties-add-on-pc-steam"), "URL")
        self.assertEqual(r["reasons"], ["additional content: add-on"])
        # « season-pass » ne compte qu'une fois ; le season pass de l'édition « Year 1 » reste le jeu (arbitrage du 01/10)
        r = pc.analyze("Farming Simulator 25", offer(), pc.url_text("https://shop.example/farming-simulator-25-season-pass-pc-steam"), "URL")
        self.assertEqual(r["reasons"], ["additional content: season-pass"])

    def test_currency_quantity_that_looks_like_a_year_and_awin_links(self):
        r = lambda product, url: pc.analyze(product, offer(), pc.url_text(url), "URL")["reasons"]
        self.assertEqual(r("Apex Legends", "https://shop.example/apex-legends-2000-coins-pc"), ["in-game currency at the merchant: coins"])
        self.assertEqual(r("War Thunder", "https://shop.example/war-thunder-2500-golden-eagles-pc"), ["in-game currency at the merchant: golden-eagles"])
        self.assertEqual(r("Football Manager 2024", "https://shop.example/football-manager-2024-pc-steam"), [])  # l'année du nom
        awin = "https://www.awin1.com/cread.php?awinmid=1&awinaffid=2&ued=https%3A%2F%2Fwww.example-shop.com%2Fcrimson-desert-pc-steam"
        self.assertEqual(pc.unwrap_affiliate(awin), "https://www.example-shop.com/crimson-desert-pc-steam")

    def test_eneba_steam_prefix_is_not_a_platform(self):
        # merchants/eneba.toml : « steam- » devant une URL qui nomme une autre plateforme ne compte pas
        def check(url, **kw):
            o = offer(merchantName="Eneba", **kw)
            with mock.patch.object(pc, "http_get", side_effect=[(200, None, TestConfirmOnMerchantPage.page(TestConfirmOnMerchantPage(), url))]
                                   + [(200, None, "")] * 5), mock.patch.object(pc, "chromium_dom", return_value=None), \
                    mock.patch.object(pc, "page_title", return_value=None):
                return pc.check_offer("NBA 2K26", o)
        xbox = "https://www.eneba.com/steam-nba-2k26-superstar-edition-xbox-series-x-s-xbox-live-key-europe"
        res = check(xbox, edition="Superstar Edition", region="EUROPE", region_filter="STEAM EU", platform="steam")
        self.assertEqual((res["verdict"], res["reasons"]), ("SUSPECT", ["platform: AllKeyShop steam, merchant xbox"]))
        res = check(xbox, edition="Superstar Edition", region="EUROPE", region_filter="XBOX X|S EUROPE", platform="xbox")
        self.assertEqual(res["verdict"], "OK")  # la clé Xbox affichée Xbox
        res = check("https://www.eneba.com/steam-nba-2k26-pc-steam-key-europe", region="EUROPE", region_filter="STEAM EU", platform="steam")
        self.assertEqual(res["verdict"], "OK")  # une vraie clé Steam : le préfixe reste un mot comme un autre

    def test_aliases_of_the_audit(self):
        pc._PRODUCT_ALIASES = None
        self.assertEqual(self.match("Diablo 4 Lord of Hatred Xbox Series",
                                    "https://www.instant-gaming.com/en/21849-buy-diablo-iv-age-of-hatred-collection-xbox-one-xbox-series-x-s-microsoft-store/",
                                    edition="Hatred Edition"), "exact")
        self.assertEqual(self.match("Dynasty Warriors 3 Complete Edition Remastered",
                                    "https://www.gamingdragons.com/en/game/buy-dynasty-warriors-3-complete-edition-steam-key.html"), "exact")
        self.assertEqual(self.match("Resident Evil Requiem", "https://www.gamingdragons.com/en/game/buy-resident-evil-requiem-9-steam-key.html"), "exact")


@mock.patch.object(pc, "REQUEST_DELAY", 0)
@mock.patch.object(pc, "PAGE_DELAY", 0)
class TestReportModes20261003(unittest.TestCase):
    """Romain, 03/10/2026 : « dans l'admin, je veux que le report des problèmes sur les tops soit identifié des problèmes
    home page »."""

    def test_each_report_carries_its_mode(self):
        import tempfile
        top, home, gone = ("https://www.allkeyshop.com/blog/%s/" % x for x in ("top", "home", "gone"))
        # une page des tops est aussi dans la homepage (le TOP 50 Popular contient les 10 premiers)
        page_modes = pc.page_modes_of({"top-games": [("Popular", 1, "Top", top)],
                                       "homepage": [("TOP 50 · All Popular", 1, "Top", top), ("Home · RPG", 3, "Home", home)]})
        self.assertEqual(page_modes, {top: ["top-games", "homepage"], home: ["homepage"]})
        state = pc.load_state("/nonexistent")
        flagged = lambda **kw: dict({"verdict": "SUSPECT", "product": "Jeu", "merchant": "X", "reasons": ["x"], "at": "2026-10-03 10:00"}, **kw)
        state["checked"] = {
            "1": flagged(page=top, mode="homepage"),  # contrôlée par la homepage, sa page est dans les tops : un problème des tops
            "2": flagged(page=home, mode="homepage"),
            "3": flagged(page=gone, mode="top-games"),  # sortie des listes : le mode qui l'a contrôlée
            "4": flagged(page=gone, list="Coming soon PC"),  # entrée d'avant le 03/10 : le mode de sa liste
            "5": flagged(page=gone, list="TOP 50 · All Popular"),
        }
        with tempfile.TemporaryDirectory() as d:
            pc.export_reports(state, d, {}, page_modes)
            with open(os.path.join(d, "reports.json"), encoding="utf-8") as f:
                reports = {r["offer"]: r for r in json.load(f)["reports"]}
        self.assertEqual({k: (r["mode"], r["modes"], r["mode_label"]) for k, r in reports.items()}, {
            "1": ("top-games", ["top-games", "homepage"], "Price check top"),
            "2": ("homepage", ["homepage"], "Price check homepage"),
            "3": ("top-games", [], "Price check top"),
            "4": ("top-games", [], "Price check top"),
            "5": ("homepage", [], "Price check homepage"),
        })

    def test_first_price_problems_are_urgent(self):
        """Romain, 03/10/2026 : « quand c'est vraiment premier prix qui a un problème, c'est une grosse alerte, reportée sur
        ce webhook spécialement créé pour les urgences de problème premiers prix (premier prix = les 3 prix les moins
        chers par édition) »."""
        suspect = {"verdict": "SUSPECT", "reasons": ["x"], "notes": [], "url": None, "method": "URL"}
        alert = lambda o, res=suspect: pc.format_alert("Popular", 1, "Jeu", "https://www.allkeyshop.com/blog/jeu/", o, res)
        for rank in (1, 2, 3):
            self.assertTrue(alert(offer(edition_rank=rank)).startswith(pc.URGENT_PREFIX + "\n"), rank)
        # pas une urgence : le 4e prix, un compte, un doute (À VÉRIFIER), une offre sans rang connu
        for o, res in ((offer(edition_rank=4), suspect), (offer(edition_rank=1, account=True), suspect),
                       (offer(edition_rank=1), dict(suspect, verdict="À VÉRIFIER")), (offer(), suspect)):
            self.assertFalse(alert(o, res).startswith(pc.URGENT_PREFIX), (o.get("edition_rank"), o.get("account"), res["verdict"]))
        # l'urgence part sur le webhook des urgences, avec le mode qui l'a trouvée ; le reste, sur le salon du mode
        urgent_msg, normal_msg = alert(offer(edition_rank=1)), alert(offer(edition_rank=4))
        mode_sent, urgent_sent = [], []
        pc.route_alert(urgent_msg, "homepage", mode_sent.append, urgent_sent.append)
        pc.route_alert(normal_msg, "homepage", mode_sent.append, urgent_sent.append)
        self.assertEqual(len(urgent_sent), 1)
        self.assertTrue(urgent_sent[0].startswith(pc.URGENT_PREFIX + " · Price check homepage\n"), urgent_sent[0])
        self.assertEqual(mode_sent, [normal_msg])
        pc.route_alert(urgent_msg, "top-games", mode_sent.append, None)  # sans webhook d'urgence : le salon du mode
        self.assertEqual(mode_sent[-1], urgent_msg)
        with mock.patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": "https://hook/top", "DISCORD_WEBHOOK_URL_URGENT": "https://hook/urgent"}):
            self.assertEqual(pc.webhook_for("urgent"), "https://hook/urgent")
        with mock.patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": "https://hook/top"}, clear=True):
            self.assertEqual(pc.webhook_for("urgent"), "https://hook/top")
        # l'export le dit aussi, pour l'admin
        self.assertTrue(pc.is_first_price({"edition_rank": 2}))
        self.assertFalse(pc.is_first_price({"edition_rank": 2, "account": True}))
        self.assertFalse(pc.is_first_price({"edition_rank": 5}))

    def test_existing_reports_are_sent_once_to_their_right_channel(self):
        """Romain, 03/10/2026 : « si tu passes sur les offres qui ont déjà été reportées, il faudra les reporter ce coup-ci
        dans le bon chan discord au prochain passage »."""
        page = "https://www.allkeyshop.com/blog/jeu/"
        html = aks_page([{"id": n, "price": 5.0 + n} for n in (1001, 1002, 1003, 1004, 1005)])
        still = lambda product, o: {"verdict": "SUSPECT", "reasons": ["region: AllKeyShop GLOBAL, merchant EU"], "notes": [],
                                    "url": "https://shop.example/jeu-eu", "method": "URL"}
        old = lambda **kw: dict({"verdict": "SUSPECT", "product": "Jeu", "edition": "Standard", "merchant": "Kinguin", "page": page,
                                 "reasons": ["region: AllKeyShop GLOBAL, merchant EU"], "url": "https://shop.example/jeu-eu",
                                 "method": "URL", "at": "2026-09-30 15:54", "seen": pc.time.time()}, **kw)
        state = pc.load_state("/nonexistent")
        state["checked"] = {
            "1001": old(),  # 30/09 : partie sur le salon des top games ; un SUSPECT sur le 1er prix : les urgences
            "1002": old(at="2026-10-02 05:19", mode="homepage"),  # partie sur le salon homepage ; 2e prix : les urgences
            "1003": old(at="2026-10-02 05:19", mode="homepage", sent_to="urgent"),  # déjà au bon endroit
            "1005": old(mode="homepage"),  # 5e prix (recontrôlée, hors des 3 premiers) : le salon homepage, pas les urgences
        }
        targets = [("Home · RPG", 3, "Jeu", page)]

        def cycle(mode, sent, recheck="flagged"):
            with mock.patch.object(pc, "http_get", return_value=(200, None, html)), mock.patch.object(pc, "NOTIFY_OK", False):
                pc.run_cycle(targets, sent.append, state, still, per_edition=3, recheck=recheck, mode=mode)
        # un passage des tops voit la page (elle est aussi dans les tops) : les urgences partent, pas l'alerte homepage
        sent = []
        cycle("top-games", sent)
        urgent = [m for m in sent if m.startswith(pc.URGENT_PREFIX)]
        self.assertEqual(len(urgent), 2, sent)
        self.assertTrue(all("📌 **Reminder** · existing report (flagged on " in m and "sent again to the first price emergency channel" in m.splitlines()[1]
                            for m in urgent), urgent)
        self.assertEqual(len(sent), 2)  # l'offre 1005 attend un passage homepage
        self.assertEqual([state["checked"][k].get("sent_to") for k in ("1001", "1002", "1003")], ["urgent", "urgent", "urgent"])
        # le passage homepage : l'alerte homepage du 30/09 (partie sur le salon des top games) rejoint son salon
        sent = []
        cycle("homepage", sent)
        self.assertEqual(len(sent), 1, sent)
        self.assertTrue(sent[0].startswith("📌 **Reminder** · existing report (flagged on 2026-09-30 15:54), sent again to its mode's channel"), sent[0])
        self.assertEqual(state["checked"]["1005"]["sent_to"], "homepage")
        # une seule fois
        sent = []
        cycle("homepage", sent)
        cycle("top-games", sent)
        self.assertEqual(sent, [])
        # un envoi raté est retenté au passage suivant
        state["checked"]["1004"] = old(sent_to=None)
        failing = lambda msg: (_ for _ in ()).throw(OSError("Discord 503"))
        with mock.patch.object(pc, "http_get", return_value=(200, None, html)), mock.patch.object(pc, "NOTIFY_OK", False):
            pc.run_cycle(targets, failing, state, still, per_edition=3, recheck="flagged", mode="homepage")
        self.assertIsNone(state["checked"]["1004"].get("sent_to"))
        sent = []
        cycle("homepage", sent)
        self.assertEqual(len(sent), 1)
        self.assertEqual(state["checked"]["1004"]["sent_to"], "homepage")  # 4e prix : pas une urgence, le salon de son mode

    def test_a_new_alert_notes_its_channel(self):
        state = pc.load_state("/nonexistent")
        suspect = lambda product, o: {"verdict": "SUSPECT", "reasons": ["x"], "notes": [], "url": "https://shop.example/jeu", "method": "URL"}
        with mock.patch.object(pc, "http_get", return_value=(200, None, aks_page([{"id": 1, "price": 5.0}]))):
            pc.run_cycle([("Home · RPG", 1, "Jeu", "https://www.allkeyshop.com/blog/jeu/")], lambda m: None, state, suspect, mode="homepage")
        self.assertEqual(state["checked"]["1"]["sent_to"], "urgent")

    def test_feedback_threads_get_the_follow_ups(self):
        """Romain, 03/10/2026 : « envoyer le feedback sur un thread du report sur Discord … ou les 2 ? » — les deux : le bot
        ouvre un fil par alerte (bot/feedback.py, threads.json), le moniteur y poste les suites et l'admin en a le lien."""
        import tempfile
        state = pc.load_state("/nonexistent")
        fixed = {"verdict": "OK", "fixed_at": "2026-10-03 11:20", "fixed_how": "re-check OK, the offer changed (URL)"}
        rule = {"verdict": "OK", "fixed_at": "2026-10-03 11:21"}
        new = {"verdict": "SUSPECT", "at": "2026-10-03 11:22", "reasons": ["another product at the merchant: « Sonic »"]}
        lone = {"verdict": "OK", "fixed_at": "2026-10-03 11:23"}
        state["checked"] = {"1": fixed, "2": rule, "3": new, "4": lone}
        threads = {"1": {"thread": 901, "guild": 77, "mode": "urgent"}, "2": {"thread": 902, "guild": 77, "mode": "homepage"},
                   "3": {"thread": 903, "guild": 77, "mode": "top-games"}}
        outcome = {"fixed": [fixed, lone], "removed": [], "rules": [rule], "verified": [], "new": [new], "still": [], "unknown": []}
        sent = []
        self.assertEqual(pc.post_follow_ups(state, outcome, threads, lambda info, msg: sent.append((info["thread"], msg))), 3)
        self.assertEqual(sent, [
            (901, "✅ **Repaired** at the re-check of 2026-10-03 11:20: re-check OK, the offer changed (URL)"),
            (902, "🧹 **False positive cleared by a rule** at the re-check of 2026-10-03 11:21: nothing changed in the offer"),
            (903, "🆕 **Wrong again** at the re-check of 2026-10-03 11:22: another product at the merchant: « Sonic »"),
        ])  # l'offre 4 n'a pas de fil : rien
        # un envoi raté n'arrête rien
        self.assertEqual(pc.post_follow_ups(state, outcome, threads, lambda info, msg: (_ for _ in ()).throw(OSError("503"))), 0)
        # une décision prise dans l'admin est recopiée dans le fil ; celle prise sur Discord y est déjà
        state["checked"]["1"]["decision"] = {"decision": "faux", "note": "bonne édition", "by": "romain"}
        state["checked"]["2"]["decision"] = {"decision": "vrai", "note": "", "by": "Rémi (Discord)"}
        sent.clear()
        pc.post_admin_decisions(state, ["1", "2"], threads, lambda info, msg: sent.append((info["thread"], msg)))
        self.assertEqual(sent, [(901, "📝 Decision taken in the admin: **False positive** — « bonne édition » — by romain")])
        # le webhook du salon du fil, avec thread_id
        with mock.patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": "https://hook/top", "DISCORD_WEBHOOK_URL_URGENT": "https://hook/urgent"}), \
                mock.patch.object(pc, "send_discord") as send:
            pc.send_to_thread(threads["1"], "x")
        send.assert_called_once_with("https://hook/urgent?thread_id=901", "x")
        # l'export donne le lien du fil à l'admin
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "threads.json"), "w") as f:
                json.dump({"3": threads["3"]}, f)
            pc.export_reports(state, d)
            with open(os.path.join(d, "reports.json"), encoding="utf-8") as f:
                reports = {r["offer"]: r for r in json.load(f)["reports"]}
        self.assertEqual(reports["3"]["discord_thread"], "https://discord.com/channels/77/903")
        self.assertIsNone(reports["1"]["discord_thread"])

    def test_run_cycle_keeps_the_mode_that_checked_the_offer(self):
        state = pc.load_state("/nonexistent")
        ok = lambda product, o: {"verdict": "OK", "reasons": [], "notes": [], "url": "https://shop.example/jeu", "method": "URL"}
        with mock.patch.object(pc, "http_get", return_value=(200, None, aks_page([{"id": 1, "price": 5.0}]))), \
                mock.patch.object(pc, "NOTIFY_OK", False):
            pc.run_cycle([("Home · RPG", 1, "Jeu", "https://www.allkeyshop.com/blog/jeu/")], lambda m: None, state, ok, mode="homepage")
        self.assertEqual(state["checked"]["1"]["mode"], "homepage")


class TestLoopBanner20261003(unittest.TestCase):
    """Romain, 03/10/2026 : « il faut qu'on sache qu'une nouvelle boucle a commencé, et tu mets un petit message pour
    expliquer et un lien vers la doc … dans ce channel et les autres channels de check, à chaque boucle … très visible,
    qui fasse bien la séparation entre les boucles »."""

    def loop(self, mode="top-games", **kw):
        return dict(pc.new_loop(mode), start="03/10/2026 13:20", **kw)

    def test_the_banner_separates_explains_and_links_the_guide(self):
        banner = pc.loop_banner(self.loop(recheck="flagged"), "top-games")
        lines = banner.splitlines()
        self.assertEqual(lines[0], pc.BANNER_RULE)
        self.assertEqual(lines[1], "# 🔄 New loop · Price check top")  # un titre Discord : le plus visible
        self.assertEqual(lines[2], "-# 03/10/2026 13:20 · the tops: first 10 Popular, first 5 Coming soon PC · "
                                   "with the hourly re-check of the flagged offers")
        for legend in ("🔴 🟠 new report", "📌 reminder of an existing report", "🔁 re-check summary", "**true**"):
            self.assertIn(legend, banner)
        self.assertEqual(lines[-1], "📘 Team guide: <https://169.58.5.63.sslip.io/executor/price-check-guide>")
        self.assertLess(len(banner), pc.DISCORD_LIMIT)

    def test_each_channel_its_banner(self):
        urgent = pc.loop_banner(self.loop("homepage"), "urgent")
        self.assertEqual(urgent.splitlines()[1], "# 🚨 New loop · Price check homepage")
        self.assertIn("3 cheapest offers of an edition. Handle them first.", urgent)
        self.assertNotIn("🔁", urgent)  # les bilans du recontrôle partent sur le salon du mode
        requested = pc.loop_banner(self.loop("homepage", recheck="all", requested="romain"), "homepage")
        self.assertIn("the whole homepage: home widgets, TOP 50 of every platform · pass requested from "
                      "the admin by romain: every offer re-checked", requested)
        self.assertEqual(pc.loop_banner(self.loop("homepage"), "urgent", resumed=True),
                         pc.BANNER_RULE + "\n### ↪️ Loop continued · Price check homepage, started 03/10/2026 13:20")

    def test_the_bot_opens_no_feedback_thread_on_a_banner(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("feedback", os.path.join(os.path.dirname(__file__), "bot", "feedback.py"))
        fb = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fb)
        for channel in ("top-games", "homepage", "urgent"):
            for resumed in (False, True):
                self.assertIsNone(fb.alert_offer(pc.loop_banner(self.loop(channel if channel != "urgent" else "homepage"),
                                                                channel, resumed)))

    def test_one_banner_per_loop_in_each_channel(self):
        sent = []
        announce = pc.make_announcer(key_of=lambda channel: channel)
        to = lambda channel: (lambda m: sent.append((channel, m.splitlines()[1] if m.startswith(pc.BANNER_RULE) else m)))
        home, top = self.loop("homepage"), self.loop("top-games")
        announce(home, "urgent", to("urgent"), "🚨 alerte 1")
        announce(home, "urgent", to("urgent"), "🚨 alerte 2")  # la même boucle : pas de nouveau bandeau
        announce(top, "urgent", to("urgent"), "🚨 alerte 3")  # une boucle des tops entre deux pages de la homepage
        announce(home, "urgent", to("urgent"), "🚨 alerte 4")  # la homepage reprend : « suite »
        announce(home, "homepage", to("homepage"), "🔁 bilan")  # un autre salon : son propre bandeau
        pc.new_loop("top-games")  # une boucle qui ne poste rien : pas de bandeau
        self.assertEqual(sent, [
            ("urgent", "# 🚨 New loop · Price check homepage"), ("urgent", "🚨 alerte 1"), ("urgent", "🚨 alerte 2"),
            ("urgent", "# 🚨 New loop · Price check top"), ("urgent", "🚨 alerte 3"),
            ("urgent", "### ↪️ Loop continued · Price check homepage, started 03/10/2026 13:20"), ("urgent", "🚨 alerte 4"),
            ("homepage", "# 🔄 New loop · Price check homepage"), ("homepage", "🔁 bilan")])

    def test_a_shared_webhook_is_one_channel(self):
        """Sans webhook homepage, ses alertes partent sur celui des top games : un seul salon, une boucle chasse l'autre."""
        sent = []
        announce = pc.make_announcer(key_of=lambda channel: "top games")
        top, home = self.loop("top-games"), self.loop("homepage")
        announce(top, "top-games", sent.append, "a")
        announce(home, "homepage", sent.append, "b")
        announce(top, "top-games", sent.append, "c")
        titles = [m.splitlines()[1] for m in sent if m.startswith(pc.BANNER_RULE)]
        self.assertEqual(titles, ["# 🔄 New loop · Price check top", "# 🔄 New loop · Price check homepage",
                                  "### ↪️ Loop continued · Price check top, started 03/10/2026 13:20"])

    def test_a_banner_not_sent_goes_with_the_next_message(self):
        sent, fail = [], [True]

        def send(m):
            if fail[0]:
                fail[0] = False
                raise urllib.error.URLError("Discord injoignable")
            sent.append(m)
        announce = pc.make_announcer(key_of=lambda channel: channel)
        loop = self.loop()
        with self.assertRaises(urllib.error.URLError):
            announce(loop, "top-games", send, "🔴 alerte 1")  # l'appelant journalise, l'alerte repart au passage suivant
        announce(loop, "top-games", send, "🔴 alerte 1")
        self.assertEqual([m.splitlines()[1] if m.startswith(pc.BANNER_RULE) else m for m in sent],
                         ["# 🔄 New loop · Price check top", "🔴 alerte 1"])

    def test_the_main_loop_heads_each_loop_in_each_channel(self):
        """La boucle principale, de bout en bout (Discord simulé) : une boucle des tops, puis une de la homepage."""
        import sys
        import tempfile
        hooks = {"DISCORD_WEBHOOK_URL": "https://discord.invalid/api/webhooks/1/top",
                 "DISCORD_WEBHOOK_URL_HOMEPAGE": "https://discord.invalid/api/webhooks/2/home",
                 "DISCORD_WEBHOOK_URL_URGENT": "https://discord.invalid/api/webhooks/3/urgent"}
        channel = {url: name for name, url in (("top", hooks["DISCORD_WEBHOOK_URL"]), ("home", hooks["DISCORD_WEBHOOK_URL_HOMEPAGE"]),
                                                ("urgent", hooks["DISCORD_WEBHOOK_URL_URGENT"]))}
        sent = []

        def run_cycle(targets, notify, state, checker=None, **kw):
            if kw["mode"] == "top-games":
                notify("🟠 **TO CHECK** · **Jeu** (Popular #1) · Standard")
                notify(pc.URGENT_PREFIX + "\n🔴 **SUSPECT** · **Jeu** (Popular #1) · Standard")
            else:
                notify("🔴 **SUSPECT** · **Autre jeu** (Home · RPG #2) · Standard · 5e prix de l'édition")
            return TestMainLoopAudit20261002.outcome()
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, hooks), \
                mock.patch.object(pc, "fetch_targets", return_value=TestMainLoopAudit20261002.TOP), \
                mock.patch.object(pc, "run_cycle", side_effect=run_cycle), mock.patch.object(pc.time, "sleep"), \
                mock.patch.object(pc, "REPORTS_DIR", d), mock.patch.object(pc.signal, "signal"), \
                mock.patch.object(pc, "send_discord", side_effect=lambda url, msg: sent.append((channel[url], msg))), \
                mock.patch.object(sys, "argv", ["price_check.py", "--once", "--state", os.path.join(d, "state.json")]):
            pc.main()
        self.assertEqual([(c, m.splitlines()[1] if m.startswith(pc.BANNER_RULE) else m.splitlines()[0]) for c, m in sent], [
            ("top", "# 🔄 New loop · Price check top"), ("top", "🟠 **TO CHECK** · **Jeu** (Popular #1) · Standard"),
            ("urgent", "# 🚨 New loop · Price check top"), ("urgent", pc.URGENT_PREFIX + " · Price check top"),
            ("home", "# 🔄 New loop · Price check homepage"),
            ("home", "🔴 **SUSPECT** · **Autre jeu** (Home · RPG #2) · Standard · 5e prix de l'édition")])
        self.assertIn("with the hourly re-check of the flagged offers", sent[0][1])  # la première boucle d'un mode recontrôle

    def test_during_a_discord_pause_banner_and_alert_wait_together(self):
        state = {"queued": []}
        with mock.patch.object(pc, "MUTE_UNTIL", "2999-01-01 00:00"):
            notify = pc.make_notifier("https://discord.invalid/api/webhooks/1/x", state, "top-games")
            pc.make_announcer(key_of=lambda channel: channel)(self.loop(), "top-games", notify, "🔴 alerte")
        self.assertEqual([c for c, m in state["queued"]], ["top-games", "top-games"])
        self.assertEqual(state["queued"][0][1].splitlines()[1], "# 🔄 New loop · Price check top")
        self.assertEqual(state["queued"][1][1], "🔴 alerte")


class TestDecisions20261005(unittest.TestCase):
    """Les décisions de Rémy dans l'admin, relues avec Romain le 05/10/2026."""

    EU27 = ["at", "be", "bg", "cy", "cz", "de", "dk", "ee", "es", "fi", "fr", "gr", "hr", "hu", "ie", "it", "lt", "lu",
            "lv", "mt", "nl", "pl", "pt", "ro", "se", "si", "sk"]

    @staticmethod
    def reasons(product, url, merchant="", **kw):
        cfg = pc.merchant_config(url, merchant)
        o = offer(**kw)
        return pc.analyze(product, o, pc.shop_url_text(url, cfg), "URL", region=pc.region_text(url, cfg))["reasons"]

    # -- Règle générale de traitement des régions (Rémy : « si l'offre est activable au US et en EU on considère que
    # -- c'est du global » ; Romain : « c'est une décision du traitement des régions », pour tous les marchands)
    def test_a_key_for_europe_and_the_us_counts_as_global(self):
        for url in ("https://gameseal.com/star-wars-galactic-racer-pc-steam-key-eu-na",
                    "https://www.example-shop.com/star-wars-galactic-racer-steam-key-europe-usa"):
            with self.subTest(url):
                self.assertEqual(self.reasons("STAR WARS Galactic Racer", url, region="GLOBAL", region_filter="STEAM GLOBAL"), [])

    def test_narrower_zones_shown_global_still_alert(self):
        """Jamais au prix d'une erreur manquée : l'Europe seule ou ROW (Monster Hunter Wilds chez G2A, vrai positif)
        restent plus étroits que GLOBAL. Les États-Unis seuls affichés GLOBAL : admis depuis le 08/10/2026 (Romain),
        voir test_us_key_allowed_global_everywhere_europe_on_console."""
        for url, found in (("https://gameseal.com/star-wars-galactic-racer-pc-steam-key-eu", "EU"),
                           ("https://www.g2a.com/star-wars-galactic-racer-pc-steam-key-row-i10000", "ROW")):
            with self.subTest(found):
                self.assertEqual(self.reasons("STAR WARS Galactic Racer", url, region="GLOBAL", region_filter="STEAM GLOBAL"),
                                 ["region: AllKeyShop GLOBAL, merchant %s" % found])

    def test_the_region_of_a_list_of_activation_countries(self):
        everywhere_but_japan = set(self.EU27) | {"us", "gb", "br", "cn", "in"}
        self.assertEqual(pc.region_from_countries(everywhere_but_japan), "eu-us")
        self.assertEqual(pc.region_from_countries(set(self.EU27) | {"gb"}), "eu")
        self.assertEqual(pc.region_from_countries((set(self.EU27) - {"de"}) | {"us"}), "usa")  # l'Allemagne exclue
        self.assertEqual(pc.region_from_countries({"br", "in", "jp"}), "row")

    # -- GameBoost : « ROW » = partout sauf le Japon (Dying Light The Beast, offre 138082170, « à discuter » de Rémy)
    URL = "https://gameboost.com/dying-light-the-beast-pc-steam-key-row-00-42386"

    def dom(self, countries, other_first=True):
        """Le JSON de la fiche, comme la page le sert (attribut data-page, guillemets échappés), avec une autre variante
        (Europe) avant la clé du lien."""
        listing = lambda ids: ",".join('{"code":"%s","name":"%s"}' % (c, c.upper()) for c in sorted(ids))
        other = '{"id":36103,"region":{"id":2,"name":"Europe"},"supported_countries":[%s]}' % listing(self.EU27)
        link = ('"gameKey":{"id":42386,"region":{"id":41,"name":"ROW","slug":"ROW"},"restrictions_notice":"This is a '
                'restricted product and it CANNOT be activated and played in Japan.","supported_countries":[%s]}' % listing(countries))
        data = ('{"props":{"variants":[%s],%s}}' % (other, link)) if other_first else '{"props":{%s}}' % link
        return ('<html><head><title>Buy Cheap Dying Light: The Beast - Dying Light: The Beast Steam Key | GameBoost</title>'
                '</head><body><div id="app" data-page="%s"></div></body></html>' % data.replace('"', "&quot;"))

    def test_gameboost_reads_the_activation_countries_of_the_key_of_the_link(self):
        everywhere_but_japan = set(self.EU27) | {"us", "gb", "br", "cn", "in"}
        self.assertEqual(pc.supported_countries_region(self.dom(everywhere_but_japan), self.URL), "eu-us")
        self.assertEqual(pc.supported_countries_region(self.dom({"us", "br", "in"}), self.URL), "usa")
        self.assertIsNone(pc.supported_countries_region("<html><title>GameBoost</title></html>", self.URL))
        text = pc.page_text_from_dom(self.dom(everywhere_but_japan), self.URL, "supported-countries")
        self.assertTrue(text.endswith(" | REGION eu-us"), text)

    def check(self, countries):
        page = TestCheckOffer().interstitial(self.URL)
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, page), (403, None, "")]), \
                mock.patch.object(pc, "chromium_dom", return_value=self.dom(countries)), mock.patch.object(pc.time, "sleep"):
            return pc.check_offer("Dying Light The Beast", offer(merchantName="GameBoost", region="GLOBAL",
                                                                 region_filter="STEAM GLOBAL", platform="steam"))

    def test_gameboost_row_key_activable_in_europe_and_the_us_is_global(self):
        res = self.check(set(self.EU27) | {"us", "gb", "br", "cn", "in"})
        self.assertEqual((res["verdict"], res["reasons"]), ("OK", []))
        self.assertTrue(res["method"].startswith("URL and the page's activation countries"), res["method"])
        self.assertIn("region read on the page, from the key's activation countries: eu-us", res["notes"])

    def test_gameboost_key_without_europe_still_alerts(self):
        # ROW sans l'Europe ni les États-Unis : toujours une erreur. Une clé des seuls États-Unis affichée GLOBAL ne l'est
        # plus (Romain, 08/10/2026 : « It's allowed on AllKeyShop.com EU and US »)
        res = self.check({"br", "in", "cn"})
        self.assertEqual((res["verdict"], res["reasons"]), ("SUSPECT", ["region: AllKeyShop GLOBAL, merchant ROW"]))
        res = self.check({"us", "br", "in"})
        self.assertEqual((res["verdict"], res["reasons"]), ("OK", []))

    # -- Les vrais positifs de Rémy : le moniteur doit continuer d'alerter
    def test_the_true_positives_of_05_10_still_alert(self):
        cases = [
            ("EA SPORTS FC 27 Xbox Series", "https://www.hrkgame.com/en/product/ea-sports-fc-27-xbox-one-xbox-series-x-row", "HRK",
             dict(region="EU XBOX X|S", region_filter="XBOX X|S EUROPE", platform="xbox"),
             ["region: AllKeyShop EU XBOX X|S, merchant ROW"]),
            ("Call of Duty Black Ops 3", "https://www.gamivo.com/product/call-of-duty-black-ops-iii-zombies-chronicles-edition-eu", "GAMIVO",
             dict(region="ROW", region_filter="STEAM ROW", edition="Limited"), ["region: AllKeyShop ROW, merchant EU"]),
            ("Assassin’s Creed Black Flag Resynced",
             "https://royalcdkeys.com/products/assassins-creed-black-flag-resynced-deluxe-edition-eu-pc-steam-altergift", "Royal CD Keys",
             dict(region="EUROPE", region_filter="STEAM EU", edition="Deluxe"), ["gift at the merchant, shown as a key EUROPE"]),
            ("Crusader Kings 3", "https://www.eneba.com/steam-crusader-kings-iii-starter-edition-pc-steam-key-europe", "Eneba",
             dict(region="IN ENGLISH ONLY", region_filter="STEAM ENG ONLY", edition="Starter Edition"),
             ["region: AllKeyShop IN ENGLISH ONLY, merchant EU"]),
            ("TCG Card Shop Simulator Nintendo Switch 2",
             "https://www.nintendo.com/en-gb/Games/Nintendo-Switch-download-software/Horse-Spirit-Valley-2-3173803.html", "Nintendo eShop IT",
             dict(region="GLOBAL", region_filter="GLOBAL", platform="nintendo-eshop"),
             ["another product at the merchant: « Horse Spirit Valley 2 » instead of « TCG Card Shop Simulator Nintendo Switch 2 » (URL)"]),
        ]
        for product, url, merchant, kw, expected in cases:
            with self.subTest(merchant):
                self.assertEqual(self.reasons(product, url, merchant, **kw), expected)


@mock.patch.object(pc, "REQUEST_DELAY", 0)
@mock.patch.object(pc, "PAGE_DELAY", 0)
class TestReview20261005(unittest.TestCase):
    """La revue des cas en suspens avec Romain, le 05/10/2026, et ses suites."""
    PAGE = "https://www.allkeyshop.com/blog/buy-jeu-cd-key-compare-prices/"

    # -- « Le nom suivi de mots en plus » : « alerter tous ces cas », « une alerte par page et par mots »
    # Ace Combat 8 « Wings of Theve », l'exemple du 05/10 : depuis le 07/10, le titre complet est un alias (titre Steam,
    # Q5 de l'onglet Romain) ; le mécanisme des mots en plus se teste sans les alias
    @mock.patch.object(pc, "product_aliases", lambda: {})
    def test_the_words_after_the_name(self):
        tail = lambda product, url, **kw: pc.tail_words(product, offer(**kw), pc.norm(pc.url_text(url)))
        self.assertEqual(tail("Minecraft", "https://shop.example/minecraft-dungeons-2-pc-key"), ["dungeons"])
        self.assertEqual(tail("Elden Ring", "https://shop.example/elden-ring-shadow-of-the-erdtree-pc-steam-key-global"),
                         ["erdtree", "shadow"])
        self.assertEqual(tail("Ace Combat 8", "https://www.instant-gaming.com/en/9408-buy-ace-combat-8-wings-of-theve-pc-steam/"),
                         ["theve", "wings"])
        # service, plateforme, zone, édition de l'offre, langue, identifiant de fiche : rien à dire
        for url, product, edition in (
                ("https://www.instant-gaming.com/en/21656-buy-ea-sports-fc-27-pc-ea-app/", "EA SPORTS FC 27", "Standard"),
                ("https://www.g2a.com/ea-sports-fc-27-pc-steam-gift-global-i10000515240006", "EA SPORTS FC 27", "Standard"),
                ("https://www.driffle.com/crusader-kings-iii-eu-pc-steam-digital-code-p9881931", "Crusader Kings 3", "Standard"),
                ("https://www.eneba.com/steam-crusader-kings-iii-starter-edition-pc-steam-key-europe", "Crusader Kings 3", "Starter Edition"),
                ("https://www.gamivo.com/product/bodycam-pc-steam-global-en-de-fr-ru-zh-es", "Bodycam", "Standard")):
            with self.subTest(url):
                self.assertEqual(tail(product, url, edition=edition), [])

    @staticmethod
    def ok(url):
        return {"verdict": "OK", "reasons": [], "notes": [], "url": url, "method": "URL"}

    # Ace Combat 8 « Wings of Theve », l'exemple du 05/10 : depuis le 07/10, le titre complet est un alias (titre Steam,
    # Q5 de l'onglet Romain) ; le mécanisme des mots en plus se teste sans les alias
    @mock.patch.object(pc, "product_aliases", lambda: {})
    def test_one_alert_per_page_and_words_then_the_decision_teaches(self):
        state = pc.load_state("/nonexistent")
        url = "https://www.instant-gaming.com/en/9408-buy-ace-combat-8-wings-of-theve-pc-steam/"
        o = offer(merchantName="Instant Gaming")
        first = pc.apply_tail_words(self.ok(url), state, self.PAGE, "1001", "2026-10-05 17:00", "Ace Combat 8", o)
        self.assertEqual((first["verdict"], first["reasons"], first.get("quiet")),
                         ("À VÉRIFIER", ["in doubt: extra words after the name : « theve wings »"], None))
        self.assertEqual(first["unverifiable"], "report")  # envoyée quel que soit le rang de l'offre
        second = pc.apply_tail_words(self.ok(url), state, self.PAGE, "1002", "2026-10-05 17:00", "Ace Combat 8", o)
        self.assertEqual((second["verdict"], second.get("quiet")), ("À VÉRIFIER", True))
        self.assertIn("same doubt as offer 1001: one alert per page and per words", second["notes"])
        other_page = pc.apply_tail_words(self.ok(url), state, self.PAGE + "x", "1003", "2026-10-05 17:00", "Ace Combat 8", o)
        self.assertIsNone(other_page.get("quiet"))  # une autre page : sa propre alerte
        # la décision sur la première offre (lue dans decisions.jsonl) apprend les mots pour la page
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            state["checked"]["1001"] = {"verdict": "À VÉRIFIER", "product": "Ace Combat 8", "page": self.PAGE}
            with open(os.path.join(d, "decisions.jsonl"), "w") as f:
                f.write(json.dumps({"offer": "1001", "decision": "faux", "note": "c'est le sous-titre du jeu", "by": "romain",
                                    "at": "2026-10-05T17:10:00+02:00"}) + "\n")
            pc.apply_decisions(state, d)
        self.assertEqual(state["tail_words"][self.PAGE]["theve wings"]["decision"], "faux")
        learnt = pc.apply_tail_words(self.ok(url), state, self.PAGE, "1002", "2026-10-05 18:00", "Ace Combat 8", o)
        self.assertEqual((learnt["verdict"], learnt["reasons"]), ("OK", []))
        state["tail_words"][self.PAGE]["theve wings"]["decision"] = "vrai"
        wrong = pc.apply_tail_words(self.ok(url), state, self.PAGE, "1002", "2026-10-05 18:00", "Ace Combat 8", o)
        self.assertEqual((wrong["verdict"], wrong["reasons"]),
                         ("SUSPECT", ["extra words already judged an error on this page: « theve wings » (offer 1001)"]))
        # une offre jugée sur sa page (pas sur son URL) n'est pas concernée
        page_ok = dict(self.ok(url), method="page (Chromium)")
        self.assertIs(pc.apply_tail_words(page_ok, state, self.PAGE, "1004", "x", "Ace Combat 8", o), page_ok)

    # Ace Combat 8 « Wings of Theve », l'exemple du 05/10 : depuis le 07/10, le titre complet est un alias (titre Steam,
    # Q5 de l'onglet Romain) ; le mécanisme des mots en plus se teste sans les alias
    @mock.patch.object(pc, "product_aliases", lambda: {})
    def test_the_loop_sends_one_alert_per_page_and_words(self):
        html = aks_page([{"id": 2001, "price": 5.0}, {"id": 2002, "price": 6.0}], product="Ace Combat 8")
        url = "https://www.instant-gaming.com/en/9408-buy-ace-combat-8-wings-of-theve-pc-steam/"
        state, sent = pc.load_state("/nonexistent"), []
        with mock.patch.object(pc, "http_get", return_value=(200, None, html)), mock.patch.object(pc, "NOTIFY_OK", False):
            pc.run_cycle([("Popular", 1, "Ace Combat 8", self.PAGE)], sent.append, state, lambda product, o: self.ok(url),
                         per_edition=3, mode="top-games")
        self.assertEqual(len(sent), 1, sent)
        self.assertIn("in doubt: extra words after the name : « theve wings »", sent[0])
        self.assertEqual([state["checked"][k]["verdict"] for k in ("2001", "2002")], ["À VÉRIFIER", "À VÉRIFIER"])

    # -- « Même si un opérateur est passé et a traité l'offre, si, au prochain passage, l'offre est toujours en erreur,
    # -- on doit encore la reporter »
    def entry(self, decided_ago, decision="vrai"):
        at = datetime.datetime.fromtimestamp(time.time() - decided_ago).astimezone().isoformat(timespec="seconds")
        return {"verdict": "SUSPECT", "product": "Jeu", "edition": "Standard", "merchant": "Kinguin", "page": self.PAGE,
                "reasons": ["region: AllKeyShop GLOBAL, merchant EU"], "url": "https://shop.example/jeu-eu", "method": "URL",
                "at": "2026-10-05 09:00", "decision": {"decision": decision, "note": "", "by": "remy", "at": at}}

    def recheck(self, entry):
        sent, outcome = [], {"checked": 0, "fixed": [], "removed": [], "rules": [], "verified": [], "still": [], "new": [], "unknown": []}
        res = {"verdict": "SUSPECT", "reasons": ["region: AllKeyShop GLOBAL, merchant EU"], "notes": [],
               "url": "https://shop.example/jeu-eu", "method": "URL"}
        pc.apply_recheck(entry, "Popular", 1, "Jeu", self.PAGE, offer(id=3001, edition_rank=4), res, sent.append,
                         "2026-10-05 18:00", time.time(), outcome)
        return sent

    def test_a_handled_offer_still_wrong_is_reported_again_once(self):
        e = self.entry(decided_ago=3600)
        sent = self.recheck(e)
        self.assertEqual(len(sent), 1)
        self.assertTrue(sent[0].startswith("📌 **Reminder** · still wrong after being handled by remy (True on "), sent[0])
        self.assertEqual(self.recheck(e), [], "the reminder comes back at every re-check")
        e["decision"] = dict(e["decision"], at=datetime.datetime.now().astimezone().isoformat(timespec="seconds"), by="garance")
        self.assertEqual(self.recheck(e), [], "no time to fix the offer after the decision")

    def test_an_offer_to_discuss_is_not_reported_again(self):
        """Romain, 06/10/2026 : « pourquoi tu me renvoies le message alors que Rémy a répondu » (Monster Hunter Wilds chez
        G2A, offre 136209040 : « à discuter » de Rémy à 05:03, renvoyée à 05:57). Un « à discuter » attend la discussion,
        pas une correction ; l'offre reste dans l'admin et dans le rappel du matin."""
        e = self.entry(decided_ago=3600, decision="a_discuter")
        self.assertEqual(self.recheck(e), [])
        self.assertFalse(pc.rereport_due(e))
        reminder = pc.format_daily_reminder({"checked": {"3001": dict(e, edition_rank=1)}}, {})[0]
        self.assertIn("· To discuss (remy) ·", reminder)

    def test_a_handled_offer_gets_time_to_be_fixed(self):
        self.assertEqual(self.recheck(self.entry(decided_ago=300)), [])  # moins de 15 min après la décision

    # -- Kinguin : une redirection vers une fiche d'un autre identifiant est une rupture (même nom, région, édition)
    def test_kinguin_another_listing_id_is_out_of_stock(self):
        cfg = pc.merchant_config("https://www.kinguin.net/category/1/x", "Kinguin")
        link = "https://www.kinguin.net/category/111/stellaris-eu-pc-steam-cd-key"
        def flag(served):
            result = pc.analyze("Stellaris", offer(region="EUROPE", region_filter="STEAM EU"), pc.url_text(link), "URL")
            pc.flag_out_of_stock(result, "Stellaris", offer(region="EUROPE", region_filter="STEAM EU"), served, link, cfg)
            return result
        moved = flag("https://www.kinguin.net/category/222/stellaris-eu-pc-steam-cd-key")
        self.assertIn("stock", moved["kinds"])
        renamed = flag("https://www.kinguin.net/category/111/stellaris-eu-steam-cd-key")
        self.assertNotIn("stock", renamed["kinds"])
        self.assertIn("page renamed at the merchant: https://www.kinguin.net/category/111/stellaris-eu-steam-cd-key", renamed["notes"])

    # -- Le rappel du matin dans le salon des urgences
    def test_the_morning_reminder(self):
        now = time.mktime(time.strptime("2026-10-06 09:00", "%Y-%m-%d %H:%M"))
        base = {"edition": "Standard", "merchant": "G2A", "edition_rank": 1, "account": False}
        state = {"checked": {
            "1": dict(base, verdict="SUSPECT", product="Ancien", at="2026-10-02 08:00"),
            "2": dict(base, verdict="SUSPECT", product="Tranché", at="2026-10-05 20:00", edition_rank=2,
                      decision={"decision": "vrai", "by": "remy", "at": "2026-10-05T21:00:00+02:00"}),
            "3": dict(base, verdict="SUSPECT", product="Faux positif", at="2026-10-05 10:00",
                      decision={"decision": "faux", "by": "remy", "at": "2026-10-05T11:00:00+02:00"}),
            "4": dict(base, verdict="SUSPECT", product="Quatrième", at="2026-10-05 10:00", edition_rank=4),
            "5": dict(base, verdict="OK", product="Réparé", at="2026-10-04 10:00", fixed_at="2026-10-05 18:00", fixed_kind="repaired"),
        }}
        decisions = {"2": {"decision": "vrai", "by": "remy", "at": "2026-10-05T21:00:00+02:00"},
                     "3": {"decision": "faux", "by": "remy", "at": "2026-10-05T11:00:00+02:00"},
                     "9": {"decision": "vrai", "by": "romain", "at": "2026-10-01T11:00:00+02:00"}}
        with mock.patch.object(pc, "ADMIN_URL", "https://admin.example/price-check"):
            parts = pc.format_daily_reminder(state, decisions, now=now)
        self.assertEqual(len(parts), 1)
        lines = parts[0].splitlines()
        self.assertEqual(lines[0], "📋 **Morning reminder · first price emergencies** · 06/10/2026")
        self.assertEqual(lines[1], "**2 first prices wrong, not fixed yet** (oldest first):")
        self.assertEqual(lines[2], "• **Ancien** · Standard · G2A · 1st price of the edition · for 4 d · to handle · <https://admin.example/price-check#offer-1>")
        self.assertTrue(lines[3].startswith("• **Tranché** · Standard · G2A · 2nd price of the edition · for 13 h · True (remy) · "), lines[3])
        self.assertEqual(lines[4], "… and 1 other open report in the admin (emergencies aside).")
        # 3 nouveaux reports en 24 h, dont celui jugé faux ensuite : il a bien été signalé
        self.assertEqual(lines[5], "**Last 24 hours**: 3 new reports · 1 repaired · 0 false positive cleared by a rule · "
                                   "2 decisions (remy 2)")
        import importlib.util
        spec = importlib.util.spec_from_file_location("feedback", os.path.join(os.path.dirname(__file__), "bot", "feedback.py"))
        fb = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fb)
        self.assertIsNone(fb.alert_offer(parts[0]), "the bot opens a feedback thread on the reminder")

    def test_the_morning_reminder_is_due_once_a_day_from_its_hour(self):
        at = lambda hm: time.strptime("2026-10-06 " + hm, "%Y-%m-%d %H:%M")
        with mock.patch.object(pc, "DAILY_REMINDER_AT", "09:00"):
            self.assertFalse(pc.daily_reminder_due({"daily_reminder": "2026-10-05"}, at("08:59")))
            self.assertTrue(pc.daily_reminder_due({"daily_reminder": "2026-10-05"}, at("09:00")))
            self.assertFalse(pc.daily_reminder_due({"daily_reminder": "2026-10-06"}, at("15:00")))
        with mock.patch.object(pc, "DAILY_REMINDER_AT", ""):
            self.assertFalse(pc.daily_reminder_due({}, at("10:00")))

    def test_after_the_reminder_the_loop_shows_its_banner_again(self):
        """Le rappel du matin passe hors boucle : l'alerte suivante de la boucle en cours remet son bandeau (« suite »)."""
        sent = []
        announce = pc.make_announcer(key_of=lambda channel: channel)
        home = dict(pc.new_loop("homepage"), start="06/10/2026 08:40")
        to_urgent = lambda m: sent.append(m.splitlines()[1] if m.startswith(pc.BANNER_RULE) else m)
        announce(home, "urgent", to_urgent, "🚨 alerte 1")
        to_urgent("📋 rappel du matin")
        announce.forget("urgent")
        announce(home, "urgent", to_urgent, "🚨 alerte 2")
        self.assertEqual(sent, ["# 🚨 New loop · Price check homepage", "🚨 alerte 1", "📋 rappel du matin",
                                "### ↪️ Loop continued · Price check homepage, started 06/10/2026 08:40", "🚨 alerte 2"])


class TestReview20261006(unittest.TestCase):
    """Romain, 06/10/2026 : Monster Hunter Wilds chez G2A (offre 136209040), clé « ROW » affichée EUROPE. Rémy : « l'offre
    n'est pas activable aux États-Unis, mais fonctionne en Europe » ; faux positif. La page G2A est illisible depuis le
    serveur : une clé ROW de G2A affichée EUROPE part en À VÉRIFIER (« en doute »), pas en SUSPECT."""

    G2A = "https://www.g2a.com/en/monster-hunter-wilds-deluxe-edition-pc-steam-key-row-i10000507334026"

    def doubt(self, url, region, filter_name, merchant="G2A", product="Monster Hunter Wilds"):
        o = offer(merchantName=merchant, edition="Deluxe", region=region, region_filter=filter_name)
        result = pc.analyze(product, o, pc.url_text(url), "URL")
        return result, pc.unsure_zone(result, o, pc.merchant_config(url, merchant))

    def test_a_g2a_row_key_shown_europe_is_a_doubt(self):
        result, doubt = self.doubt(self.G2A, "EUROPE", "STEAM EU")
        self.assertEqual(result["reasons"], ["region: AllKeyShop EUROPE, merchant ROW"])
        self.assertEqual(doubt, ["in doubt: region: AllKeyShop EUROPE, merchant ROW; at G2A, a ROW key can activate in "
                                 "EUROPE: check the activation countries on the merchant's page"])

    def test_the_doubt_stays_narrow(self):
        # une clé ROW de G2A affichée GLOBAL reste une erreur : sans les États-Unis, ce n'est pas GLOBAL
        self.assertIsNone(self.doubt(self.G2A, "GLOBAL", "STEAM GLOBAL")[1])
        # chez HRK, une clé ROW affichée EUROPE reste une erreur (EA SPORTS FC 27 Xbox, Rémy et Romain, 05/10/2026)
        hrk = "https://www.hrkgame.com/en/games/product/ea-sports-fc-27-xbox-one-xbox-series-x-row"
        self.assertIsNone(self.doubt(hrk, "EUROPE", "XBOX X|S EUROPE", merchant="HRK", product="EA SPORTS FC 27")[1])
        # un autre problème que la région : l'alerte reste une erreur
        other = "https://www.g2a.com/en/monster-hunter-rise-deluxe-edition-pc-steam-key-row-i10000507334027"
        result, doubt = self.doubt(other, "EUROPE", "STEAM EU")
        self.assertIn("name", result["kinds"])
        self.assertIsNone(doubt)

    def test_check_offer_sends_it_as_a_doubt_whatever_its_rank(self):
        title = "<title>Buy Monster Hunter Wilds | Deluxe Edition (PC) - Steam Key - ROW - Cheap - G2A.COM!</title>"
        interstitial = TestConfirmOnMerchantPage.INTERSTITIAL.replace(TestRedirection.KINGUIN, self.G2A).replace(
            TestRedirection.KINGUIN.replace("/", "\\/"), self.G2A.replace("/", "\\/"))
        calls = []
        def fake_get(url, ua, follow=True, timeout=30):
            calls.append(url)
            return (200, None, interstitial if len(calls) == 1 else title)
        with mock.patch.object(pc, "http_get", side_effect=fake_get), mock.patch.object(pc, "page_title", return_value=None), \
             mock.patch.object(pc, "chromium_dom", return_value=None), mock.patch.object(pc, "REQUEST_DELAY", 0):
            res = pc.check_offer("Monster Hunter Wilds", offer(merchantName="G2A", edition="Deluxe", region="EUROPE",
                                                               region_filter="STEAM EU"))
        self.assertEqual((res["verdict"], res["unverifiable"]), ("À VÉRIFIER", "report"), res)
        self.assertTrue(res["reasons"][0].startswith("in doubt: region: AllKeyShop EUROPE, merchant ROW; at G2A"), res["reasons"])
        self.assertEqual(pc.unverifiable_verdict(offer(edition_rank=9), "TOP 50", "https://x", res["unverifiable"]), "À VÉRIFIER")

    def test_a_top_report_stays_in_the_tops_until_it_is_handled(self):
        """Romain, 06/10/2026 : The Witcher 3 sort du top 5 Popular à 12:03 avec deux premiers prix en erreur, qui
        disparaissaient du filtre top ; un report trouvé sur une page des tops y reste tant qu'il n'est pas traité."""
        import tempfile
        witcher, galactic = ("https://www.allkeyshop.com/blog/%s/" % x for x in ("witcher", "galactic"))
        flagged = lambda **kw: dict({"verdict": "SUSPECT", "product": "The Witcher 3", "merchant": "Loaded", "reasons": ["x"],
                                     "at": "2026-10-06 08:52", "page": witcher}, **kw)
        state = pc.load_state("/nonexistent")
        state["checked"] = {
            "1": flagged(mode="top-games"),  # trouvé par le top, pas encore traité
            "2": flagged(mode="top-games", decision={"decision": "a_discuter", "by": "remy"}),  # à discuter : pas traité
            "3": flagged(mode="top-games", decision={"decision": "vrai", "by": "remy"}),  # traité : sa page, maintenant
            "4": flagged(mode="homepage"),  # trouvé par la homepage, jamais vu dans les tops
            "5": flagged(mode="top-games", fixed_at="2026-10-06 12:10"),  # réparé
        }
        def export(page_modes):
            with tempfile.TemporaryDirectory() as d:
                pc.export_reports(state, d, {}, page_modes)
                with open(os.path.join(d, "reports.json"), encoding="utf-8") as f:
                    return {r["offer"]: (r["mode"], r["left_tops_at"]) for r in json.load(f)["reports"]}
        in_tops = pc.page_modes_of({"top-games": [("Popular", 5, "The Witcher 3", witcher)],
                                    "homepage": [("TOP 50 · All Popular", 5, "The Witcher 3", witcher)]})
        self.assertEqual({k: m for k, (m, _) in export(in_tops).items()}, dict.fromkeys("12345", "top-games"))
        out = pc.page_modes_of({"top-games": [("Popular", 1, "STAR WARS Galactic Racer", galactic)],
                                "homepage": [("TOP 50 · All Popular", 6, "The Witcher 3", witcher)]})
        with mock.patch.object(pc.time, "strftime", return_value="2026-10-06 12:03"):
            after = export(out)
        self.assertEqual(after["1"], ("top-games", "2026-10-06 12:03"))
        self.assertEqual(after["2"], ("top-games", "2026-10-06 12:03"))
        self.assertEqual((after["3"][0], after["5"][0]), ("homepage", "homepage"))
        self.assertEqual(after["4"], ("top-games", "2026-10-06 12:03"), "seen in the tops while reported: it stays too")
        self.assertEqual(export(in_tops)["1"], ("top-games", None), "back in the tops: no longer « sortie »")
        # un report jamais vu dans les tops reste dans la homepage ; des tops non lus ne font rien « sortir »
        state["checked"]["6"] = flagged(mode="homepage", page="https://www.allkeyshop.com/blog/home/")
        self.assertEqual(export(pc.page_modes_of({"homepage": [("Home", 1, "Home", "https://www.allkeyshop.com/blog/home/")]}))["6"],
                         ("homepage", None))
        state["checked"]["1"].pop("left_tops_at", None)
        self.assertEqual(export({})["1"], ("top-games", None))


class TestTailWordsReview20261006(unittest.TestCase):
    """Revue des commentaires du 06/10/2026 : les 15 doutes « mots en plus après le nom » jugés par Rémy et Romain étaient
    tous des faux positifs. Chaque cas devient une règle générale (jamais un alias par produit) ; les vrais doutes restent."""

    def tail(self, product, edition, url, page="", merchant="X"):
        o = offer(merchantName=merchant, edition=edition, url=url)
        res = {"verdict": "OK", "method": "URL", "url": url, "notes": [], "reasons": []}
        state = {"checked": {}}
        out = pc.apply_tail_words(res, state, page or "https://www.allkeyshop.com/blog/buy-x-cd-key-compare-prices/", "1",
                                  "2026-10-06 15:00", product, o)
        return out.get("tail") or []

    def test_the_fifteen_judged_doubts_are_gone(self):
        aks = "https://www.allkeyshop.com/blog/%s/"
        cases = [  # (produit, édition, URL du marchand, page AllKeyShop), le motif du faux positif en commentaire
            ("Forza Horizon 5 PS5", "Standard", "https://www.vidaplayer.com/en/product/game-playstation-5-spain/forza-horizon-5-standard-edition", ""),  # le nom plus loin que le « 5 »
            ("GTA 5", "Premium + Megalodon Card", "https://www.g2a.com/grand-theft-auto-v-premium-online-edition-megalodon-shark-card-bundle-rockstar-key-global-i10000171269001", ""),
            ("GTA 5", "Premium + Megalodon Card", "https://www.eneba.com/steam-grand-theft-auto-v-premium-online-edition-megalodon-shark-card-bundle-rockstar-social-club-key-global", ""),
            ("Civilization 7", "Settler Edition", "https://www.gamersgate.com/product/sid-meiers-civilization-vii-settlers-edition/", ""),  # pluriel
            ("Civilization 7", "Settler's Edition", "https://www.kinguin.net/en/category/383041/sid-meier-s-civilization-vii-settler-s-edition-eu-pc-steam-cd-key", ""),  # apostrophe
            ("Castlevania Belmont's Curse", "Standard", "https://www.kinguin.net/en/category/563743/castlevania-belmont-s-curse-pre-order-pc-steam-cd-key", ""),
            ("Castlevania Belmont's Curse", "Preorder bonus", "https://de.gamesplanet.com/game/castlevania-belmont-s-curse-steam-key--8223-1", ""),
            ("Resident Evil 4 Xbox Series", "Deluxe", "https://www.cjs-cdkeys.com/products/Resident-Evil-4-Remake-Deluxe-Edition-Key-%28Xbox-Series-X%7CS%29.html",
             aks % "buy-resident-evil-4-remake-xbox-series-compare-prices"),  # le mot est dans le nom de la page AllKeyShop
            ("Stellaris", "Starter Pack", "https://www.kinguin.net/category/84491/stellaris-starter-pack-steam-cd-key&roff=1", ""),  # paramètre collé
            ("Assetto Corsa EVO", "Bundle", "https://store.steampowered.com/bundle/61303/Assetto_Corsa_EVO__Rally_Bundle/", ""),
            ("GTA 5", "GTA 5 + Criminal", "https://www.gamebillet.com/grand-theft-auto-v-and-criminal-enterprise-starter-pack-bundle", ""),
            ("Black Myth Wu Kong Xbox Series", "Standard", "https://www.loaded.com/black-myth-wukong-xbox-series-x-s-eu", ""),  # mots collés
            ("Black Myth Wu Kong PS5", "Standard", "https://www.vidaplayer.com/product/game-playstation-5-spain/black-myth-wukong-standard-edition", ""),
            ("F1 25", "Iconic Edition", "https://muve.games/p/f1-25-iconic-edition-time-limited-pre-purchase-xbox-series-x-s-europe-2356297", ""),
            ("Euro Truck Simulator 2", "Bundle", "https://www.eneba.com/steam-euro-truck-simulator-2-scania-truck-driving-simulator-bundle-steam-pc-key-global", ""),
        ]
        for product, edition, url, page in cases:
            with self.subTest(product=product, url=url):
                self.assertEqual(self.tail(product, edition, url, page), [])

    def test_the_remaining_noise_too(self):
        for product, edition, url in [
            ("S.T.A.L.K.E.R. 2 Heart of Chornobyl", "Deluxe", "https://www.g2a.com/en/stalker-2-heart-of-chernobyl-deluxe-edition-pc-steam-key-global-i1000025"),  # une lettre
            ("EA SPORTS FC 26 Xbox Series", "The World's Game Edition", "https://www.eneba.com/xbox-ea-sports-fctm-26-the-worlds-game-edition-xbox-live-key-europe"),  # ™
            ("Subnautica 2 Xbox Series", "Standard", "https://gameboost.com/subnautica-2-xbox-seriesxbox-pc-global-00-72566"),  # mots connus collés
            ("GTA 5 Xbox Series", "Enhanced", "https://www.gamingdragons.com/en/game/buy-grand-theft-auto-v-xbox-digital-code-enh.html"),  # abréviation
            ("The Witcher 3 Wild Hunt", "GOTY", "https://muve.games/p/the-witcher-3-wild-hunt-game-of-the-year-edition-592ab2"),
            ("Dead by Daylight", "Stranger Edition", "https://www.eneba.com/steam-dead-by-daylight-stranger-things-edition-steam-key-global"),
            ("Football Manager 26", "Standard", "https://www.driffle.com/football-manager-26-europe-pc-mac-official-webiste-digital-key-p99"),  # faute de frappe
            ("Kingdom Come Deliverance 2", "Royal", "https://k4g.com/product/kingdom-come-deliverance-ii-steam-europe-instant-cd-key-royal-edition-cd-key-SCZXUHNQ"),
            ("Red Dead Redemption", "Standard + DLC", "https://kinguin.net/en/category/281410/red-dead-redemption-undead-nightmare-pc-epic-games-green-gift-redemption-code"),
            ("Dragon Shelter", "Bundle 2", "https://store.steampowered.com/bundle/86153/Dragon_Shelter_x_Amber_Isle/"),
        ]:
            with self.subTest(product=product):
                self.assertEqual(self.tail(product, edition, url), [])

    def test_real_doubts_stay(self):
        for product, edition, url, words in [
            ("Minecraft", "Standard", "https://www.example.com/minecraft-dungeons-2-pc-key", ["dungeons"]),
            ("Control", "Standard", "https://www.example.com/control-resonant-steam-key", ["resonant"]),
            ("Transport Fever 3", "Standard", "https://www.kinguin.net/category/942442/transport-fever-3-or-mystery-steam-cd-key-by-global", ["by", "mystery", "or"]),
            ("Elden Ring", "Standard", "https://gameseal.com/elden-sword-pc-steam-key-global", ["sword"]),
            ("F1 25", "Standard", "https://www.hrkgame.com/en/product/f1-25-iconic-edition-xbox-series-x-europe", ["iconic"]),
            ("Hearts of Iron 4", "Standard", "https://k4g.com/product/hearts-of-iron-iv-steam-ukraine-cd-key-standard-edition-cd-key-066ABC12", ["ukraine"]),
        ]:
            with self.subTest(product=product):
                self.assertEqual(self.tail(product, edition, url), words)

    def test_localized_stores_have_no_extra_words_doubt(self):
        url = "https://www.nintendo.com/fr-fr/Jeux/Jeux-Nintendo-Switch/Sesame-Street-Amis-et-Fun-3147246.html"
        self.assertEqual(self.tail("Sesame Street Friends & Fun Nintendo Switch", "Standard", url, merchant="Nintendo eShop FR"), [])

    # Ace Combat 8 « Wings of Theve », l'exemple du 05/10 : depuis le 07/10, le titre complet est un alias (titre Steam,
    # Q5 de l'onglet Romain) ; le mécanisme des mots en plus se teste sans les alias
    @mock.patch.object(pc, "product_aliases", lambda: {})
    def test_a_faux_teaches_the_words_for_every_platform_of_the_game(self):
        """Ace Combat 8 « Wings of Theve » : 45 offres sur les pages PC, Xbox et PS5 ; un « faux » suffit pour les trois."""
        pc_page, xbox_page = ("https://www.allkeyshop.com/blog/buy-ace-combat-8-%scompare-prices/" % x for x in ("cd-key-", "xbox-series-"))
        state = {"checked": {"1": {"product": "Ace Combat 8"}},
                 "tail_words": {pc_page: {"theve wings": {"offer": "1", "at": "2026-10-06 10:00", "decision": "faux"}}}}
        url = "https://www.eneba.com/xbox-ace-combat-8-wings-of-theve-deluxe-edition-xbox-series-x-s-xbox-live-key-europe"
        res = {"verdict": "OK", "method": "URL", "url": url, "notes": [], "reasons": []}
        out = pc.apply_tail_words(res, state, xbox_page, "2", "2026-10-06 15:00", "Ace Combat 8 Xbox Series", offer(edition="Deluxe", url=url))
        self.assertEqual(out["verdict"], "OK", out)
        self.assertIn("extra words accepted for this game: « theve wings » (offer 1 judged a false positive)", out["notes"])
        other = pc.apply_tail_words(dict(res), state, "https://www.allkeyshop.com/blog/buy-ace-combat-7-cd-key-compare-prices/", "3",
                                    "2026-10-06 15:00", "Ace Combat 7", offer(edition="Deluxe", url=url.replace("8", "7")))
        self.assertEqual(other["verdict"], "À VÉRIFIER", "another game learns nothing")


class TestOrphanPages20261006(unittest.TestCase):
    """Romain, 06/10/2026 : « les bugs qui ont été traités et réparés par Rémy sont re-reportés ». GTA 4 (Steam, rangée en
    Standard au lieu de Complete) et Warhammer 40k Space Marine 2 étaient réparés sur AllKeyShop, mais leurs pages étaient
    sorties du TOP 50 : plus jamais recontrôlées, elles restaient « à corriger ». TORO 2 n'avait même pas sa page."""

    GTA4 = "https://www.allkeyshop.com/blog/buy-gta-4-cd-key-compare-prices/"
    TORO = "https://www.allkeyshop.com/blog/buy-toro-2-nintendo-switch-compare-prices/"

    def trans(self, *prices):
        return {"editions": {"1": {"name": "Standard"}, "7": {"name": "Complete"}},
                "regions": {"1": {"region_name": "GLOBAL", "filter_name": "STEAM GLOBAL"}},
                "prices": [dict({"price": 5.99, "priceCard": 5.99, "dispo": 1, "region": "1", "merchant": 1, "merchantName": "Steam",
                                 "activationPlatform": "steam", "account": False}, **p) for p in prices]}

    def test_a_flagged_offer_on_a_page_no_longer_followed_is_rechecked_there(self):
        vrai = {"decision": "vrai", "by": "remy", "at": "2026-10-06T05:31:00+02:00"}
        flagged = lambda **kw: dict({"verdict": "SUSPECT", "reasons": ["x"], "at": "2026-09-30 15:54", "decision": vrai,
                                     "url": "https://store.steampowered.com/app/12210/", "merchant": "Steam", "edition": "Standard",
                                     "region": "GLOBAL", "region_filter": "STEAM GLOBAL", "platform": "steam"}, **kw)
        state = {"checked": {
            "80523": flagged(product="GTA 4", page=self.GTA4),  # réparée : rangée dans l'édition Complete
            "140421891": flagged(product="TORO 2 Nintendo Switch"),  # sans page : retrouvée par son nom, offre retirée
            "1": flagged(product="Jeu suivi", page="https://www.allkeyshop.com/blog/buy-suivi-cd-key-compare-prices/"),
            "2": flagged(product="GTA 4", page=self.GTA4, decision={"decision": "faux", "by": "remy"}),  # jugée faux : rien
        }}
        pages = {self.GTA4: (200, None, "<title>Buy GTA 4 CD Key Compare Prices</title>"),
                 self.TORO.replace("-compare-prices", "-cd-key-compare-prices"): (404, None, ""),
                 self.TORO: (200, None, "<title>Buy TORO 2 Nintendo Switch Compare Prices</title>")}
        fetched = []
        def get(url, ua, follow=True, timeout=30):
            fetched.append(url)
            self.assertEqual(ua, pc.AKS_UA)
            return pages[url]
        parsed = {"<title>Buy GTA 4 CD Key Compare Prices</title>": self.trans({"id": 80523, "edition": "7"}),
                  "<title>Buy TORO 2 Nintendo Switch Compare Prices</title>": self.trans({"id": 999, "edition": "1"})}
        sent = []
        ok = lambda product, o: {"verdict": "OK", "method": "URL", "url": "https://store.steampowered.com/app/12210/",
                                 "notes": [], "reasons": []}
        with mock.patch.object(pc, "http_get", side_effect=get), mock.patch.object(pc, "parse_game_page", side_effect=parsed.get), \
             mock.patch.object(pc, "PAGE_DELAY", 0):
            outcome = pc.recheck_orphans(state, {"https://www.allkeyshop.com/blog/buy-suivi-cd-key-compare-prices/"}, sent.append,
                                         checker=ok)
        gta, toro = state["checked"]["80523"], state["checked"]["140421891"]
        self.assertEqual((gta["verdict"], gta["fixed_kind"]), ("OK", "repaired"))
        self.assertIn("edition", gta["fixed_how"])
        self.assertEqual((toro["verdict"], toro["fixed_how"], toro["page"]), ("OK", pc.REMOVED_HOW, self.TORO))
        self.assertEqual(state["checked"]["1"]["verdict"], "SUSPECT", "a followed page is the passes' job")
        self.assertNotIn("fixed_at", state["checked"]["2"])
        self.assertEqual((outcome["pages"], outcome["offers"], len(outcome["fixed"]), len(outcome["removed"])), (2, 2, 1, 1))
        self.assertEqual(sent, [], "a repaired offer sent something")
        self.assertNotIn("https://www.allkeyshop.com/blog/buy-suivi-cd-key-compare-prices/", fetched)


class TestStillWrongAfterAFix20261006(unittest.TestCase):
    """Romain, 06/10/2026 : « nous avons les URL en cache sur AllKeyShop pendant 24 heures. Donc c'est bien de reporter
    quand on a encore le problème, car ça nous oblige à aller effacer ce cache » ; Warhammer 40k Space Marine 2, réparé par
    Rémy, re-reporté à tort : Steam renvoyait « /sub/997629 » vers sa vérification d'âge, lue comme le produit « Agecheck »."""

    def test_a_true_positive_still_wrong_is_reported_at_every_re_check_at_most_hourly(self):
        decided = datetime.datetime(2026, 10, 6, 9, 0).astimezone()
        entry = {"decision": {"decision": "vrai", "by": "remy", "at": decided.isoformat(timespec="seconds")}}
        t = decided.timestamp()
        self.assertFalse(pc.rereport_due(entry, now=t + 600), "before the grace time")
        self.assertTrue(pc.rereport_due(entry, now=t + 1000))
        entry["rereported_at"] = t + 1000
        self.assertFalse(pc.rereport_due(entry, now=t + 1000 + 1800), "twice within the hour")
        self.assertTrue(pc.rereport_due(entry, now=t + 1000 + 3600), "the next hourly re-check still sees it")
        self.assertIn("clear the cache of its URL on AllKeyShop (kept 24 h)", pc.rereport_note(entry["decision"]))
        entry["decision"]["decision"] = "a_discuter"
        self.assertFalse(pc.rereport_due(entry, now=t + 99999))

    def test_steam_gets_the_age_cookies_allkeyshop_never(self):
        seen = []
        class Opener:
            def open(self, req, timeout=30):
                seen.append((req.full_url, dict(req.header_items())))
                raise urllib.error.HTTPError(req.full_url, 404, "x", {}, None)
        with mock.patch.object(pc.urllib.request, "build_opener", return_value=Opener()):
            pc.http_get("https://store.steampowered.com/sub/997629/", pc.BROWSER_UA)
            pc.http_get("https://www.allkeyshop.com/blog/buy-x-cd-key-compare-prices/", pc.AKS_UA)
        steam, aks = seen[0][1], seen[1][1]
        self.assertIn("birthtime=", steam.get("Cookie", ""))
        self.assertNotIn("Cookie", aks)

    def test_an_age_gate_is_never_the_product(self):
        self.assertTrue(pc.interstitial("https://store.steampowered.com/agecheck/sub/997629"))
        self.assertFalse(pc.interstitial("https://store.steampowered.com/sub/997629/"))
        steam = "https://store.steampowered.com/sub/997629/?cc=fr"
        interstitial = TestConfirmOnMerchantPage.INTERSTITIAL.replace(TestRedirection.KINGUIN, steam).replace(
            TestRedirection.KINGUIN.replace("/", "\\/"), steam.replace("/", "\\/"))
        title = "<title>Warhammer 40,000: Space Marine 2 - Anniversary Edition on Steam</title>"
        def get(url, ua, follow=True, timeout=30):
            if "allkeyshop" in url:
                return 200, None, interstitial
            if not follow:
                return 302, "https://store.steampowered.com/agecheck/sub/997629", ""
            return 200, None, title
        with mock.patch.object(pc, "http_get", side_effect=get), mock.patch.object(pc, "page_title", return_value=None), \
             mock.patch.object(pc, "chromium_dom", return_value=None), mock.patch.object(pc, "REQUEST_DELAY", 0):
            res = pc.check_offer("Warhammer 40k Space Marine 2", offer(merchantName="Steam", edition="1 Year Anniversary Edition",
                                                                      region="GLOBAL", region_filter="STEAM GLOBAL"))
        self.assertFalse(any("Agecheck" in r for r in res["reasons"]), res)
        self.assertEqual(res["verdict"], "OK", res)  # le nom et l'édition lus sur la fiche Steam, pas sur la vérification d'âge


class TestPriceGap20261006(unittest.TestCase):
    """Romain, 06/10/2026 : « un premier prix de la page, vraiment pas cher par rapport au deuxième prix, ça peut être une
    alerte importante, une top alerte », sous 70 % du deuxième prix. Transport Fever 3 chez Kinguin : une clé « or
    mystery » à 2,96 € contre 33 €, jugée OK le 01/10 ; Elden Ring chez Gameseal : « Elden Sword » à 0,77 €."""

    def offers(self):
        return [offer(id=1, price=2.96, page_first=True, edition_rank=1), offer(id=2, price=33.03, edition_rank=2),
                offer(id=3, price=33.04, edition_rank=3), offer(id=4, price=1.00, account=True)]

    def test_a_first_price_far_below_the_second_is_an_urgent_alert(self):
        o = self.offers()
        ok = {"verdict": "OK", "reasons": [], "notes": [], "url": "https://www.kinguin.net/x", "method": "URL"}
        res = pc.apply_price_gap(ok, o[0], o)
        self.assertEqual(res["verdict"], "SUSPECT")
        self.assertEqual(res["reasons"], ["abnormally low first price: 2.96 €, 9 % of the page's second price (33.03 €, Kinguin, Standard)"])
        self.assertTrue(pc.is_urgent(o[0], res), "not sent to the emergencies")
        doubt = {"verdict": "À VÉRIFIER", "reasons": ["in doubt: extra words after the name : « by mystery or »"], "notes": [],
                 "quiet": True}
        both = pc.apply_price_gap(doubt, o[0], o)
        self.assertEqual((both["verdict"], len(both["reasons"]), both["quiet"]), ("SUSPECT", 2, False))

    def test_the_gap_stays_narrow(self):
        o = self.offers()
        ok = {"verdict": "OK", "reasons": [], "notes": []}
        self.assertIs(pc.apply_price_gap(ok, o[1], o), ok, "only the page's first price")
        close = [offer(id=1, price=24.0, page_first=True), offer(id=2, price=33.0)]
        self.assertIs(pc.apply_price_gap(ok, close[0], close), ok, "73 % of the second price is not a gap")
        alone = [offer(id=1, price=2.0, page_first=True)]
        self.assertIs(pc.apply_price_gap(ok, alone[0], alone), ok, "no second price")
        self.assertIsNotNone(pc.price_gap(offer(id=1, price=6.79, page_first=True), [offer(id=1, price=6.79), offer(id=2, price=15.51)]),
                             "Age of Wonders 4 : the DLC at 44 % of the game")


class TestPageEditions20261006(unittest.TestCase):
    """Romain, 06/10/2026 : « erreur SUSPECT » quand l'URL nomme une autre édition de la page ; mais GTA 5 « Premium Online
    Edition + Great White Shark Card » est bien rangée en « Premium + Great White Card », même si la page a aussi
    « Enhanced + Great White Shark Card » (3 fausses urgences au passage complet du 06/10)."""

    def reason(self, product, edition, url, editions, main=None):
        return pc.page_edition_reason({"edition": edition, "page_editions": editions, "page_main_edition": main},
                                      pc.norm(pc.url_text(url)), product)

    def test_another_edition_named_by_the_url(self):
        stellaris = ["Standard", "Deluxe", "Nova Edition", "Galaxy Edition", "Limited", "Explorer", "Bonus"]
        self.assertEqual(self.reason("Stellaris", "Deluxe", "https://www.gamingdragons.com/en/game/buy-stellaris-nova-steam-key.html", stellaris),
                         "edition: filed under Deluxe, the merchant sells nova (the page has the edition Nova Edition)")
        self.assertIsNotNone(self.reason("Stellaris", "Limited", "https://www.g2a.com/stellaris-galaxy-edition-steam-key-global-i1", stellaris))
        self.assertIsNotNone(self.reason("F1 25", "Standard", "https://www.hrkgame.com/en/product/f1-25-iconic-edition-xbox-series-x-europe",
                                         ["Standard", "Iconic Edition", "2026 Season Edition"]))
        self.assertIsNone(self.reason("Stellaris", "Nova Edition", "https://x.com/stellaris-nova-edition-steam-key", stellaris))

    def test_a_higher_edition_named_in_full_keeps_the_base_edition_in_the_product_name(self):
        # Minecraft, 06/10/2026 (Romain : « corriger et marquer faux ») : « Minecraft: Java & Bedrock Edition Deluxe
        # Collection » rangée en « Deluxe Collection Edition », trois urgences à tort (G2A 135537501, Eneba 134838435,
        # Driffle 135665249) : l'URL nomme en entier l'édition de l'offre, l'édition de base fait partie du nom du produit
        mc = ["Java & Bedrock Edition", "Windows 10 Edition", "Java Edition", "Bedrock Edition", "Standard", "Bundle",
              "Deluxe Collection Edition", "Deluxe", "Triple Bundle", "Triple Pack Edition"]
        for url in ("https://www.g2a.com/en/minecraft-java-bedrock-edition-deluxe-collection-pc-microsoft-store-key-europe-i10000326476010",
                    "https://www.eneba.com/other-minecraft-java-bedrock-edition-deluxe-collection-pc-windows-store-key-europe",
                    "https://www.driffle.com/minecraft-java-and-bedrock-edition-deluxe-collection-eu-pc-microsoft-store-digital-code-p9905694"):
            with self.subTest(url=url):
                self.assertIsNone(self.reason("Minecraft", "Deluxe Collection Edition", url, mc, main="Java & Bedrock Edition"))
        # sans l'édition principale de la page (ancienne offre en mémoire), l'alerte reste
        self.assertIsNotNone(self.reason("Minecraft", "Deluxe Collection Edition",
                                         "https://www.g2a.com/en/minecraft-java-bedrock-edition-deluxe-collection-pc-microsoft-store-key-europe-i1", mc))
        # les vraies erreurs de la même page restent signalées
        for edition, url in (("Bedrock Edition", "https://www.driffle.com/minecraft-windows-10-edition-pc-cd-key-p952490"),
                             ("Standard", "https://kinguin.net/category/121279/minecraft-windows-10-edition-eu-pc-cd-key"),
                             ("Bundle", "http://www.gamingdragons.com/en/game/buy-minecraft-java-n-bedrock-bundle-download.html")):
            with self.subTest(edition=edition):
                self.assertIsNotNone(self.reason("Minecraft", edition, url, mc, main="Java & Bedrock Edition"))
        # l'édition de l'offre pas nommée en entier (sans « collection ») : l'alerte reste
        self.assertIsNotNone(self.reason("Minecraft", "Deluxe Collection Edition", "https://x.com/minecraft-java-bedrock-edition-deluxe-key",
                                         mc, main="Java & Bedrock Edition"))
        # une édition à part, pas l'édition principale de la page, nommée avec l'édition de l'offre : l'alerte reste (inspiré
        # de The Blood of Dawnwalker chez Eneba, 140458058 : « Eclipse Edition (Deluxe) » seule en Deluxe, 77 offres en
        # Eclipse Edition, Standard 95 ; le vrai cas, une égalité, « eclipse » contre « deluxe », tranché par Romain le 07/10 : c'est la Deluxe, TestRomainTab20261007)
        self.assertIsNotNone(self.reason("Game", "Deluxe", "https://x.com/game-eclipse-moon-edition-deluxe-steam-key",
                                         ["Standard", "Eclipse Moon Edition", "Deluxe"], main="Standard"))
        # une autre édition supérieure nommée reste une erreur (cas inventé : « Nova Deluxe Edition » rangée en Deluxe)
        self.assertIsNotNone(self.reason("Stellaris", "Deluxe", "https://x.com/stellaris-nova-deluxe-edition-key",
                                         ["Standard", "Deluxe", "Nova Edition", "Nova Deluxe Edition"], main="Nova Edition"))

    def test_the_main_edition_is_the_one_with_the_most_offers_on_sale(self):
        trans = {"editions": {"2063": {"name": "Java & Bedrock Edition"}, "2497": {"name": "Deluxe Collection Edition"}},
                 "regions": {}, "prices": [
                     {"id": i, "price": 20 + i, "priceCard": 20 + i, "dispo": 1, "edition": "2063", "region": 1, "merchant": 1, "merchantName": "M"}
                     for i in range(3)] + [
                     {"id": 9, "price": 25, "priceCard": 25, "dispo": 1, "edition": "2497", "region": 1, "merchant": 1, "merchantName": "M"},
                     {"id": 10, "price": 0.02, "priceCard": 0.02, "dispo": 1, "edition": "2497", "region": 1, "merchant": 1, "merchantName": "M"}]}
        trans["prices"][-1]["price"] = pc.NO_PRICE
        offers = pc.page_offers(trans, 3)
        self.assertEqual({o["page_main_edition"] for o in offers}, {"Java & Bedrock Edition"})

    def test_the_url_naming_its_own_edition_better_is_not_misfiled(self):
        gta = ["Premium + Great White Card", "Enhanced + Great White Shark Card", "Standard + Great White Shark Card", "Premium"]
        for url in ("https://www.g2a.com/grand-theft-auto-v-premium-online-edition-great-white-shark-card-bundle-rockstar-key-global-i1",
                    "https://gameseal.com/grand-theft-auto-v-premium-online-edition-and-great-white-shark-card-bundle-pc-rockstar-games-launcher-key-global"):
            with self.subTest(url=url):
                self.assertIsNone(self.reason("GTA 5", "Premium + Great White Card", url, gta))


class TestSingleProductShop20261006(unittest.TestCase):
    """Romain, 06/10/2026, offre 135633063 (Escape from Tarkov, Unheard Edition, NON VÉRIFIABLE) : « cas spécial pour
    https://www.escapefromtarkov.com, ils vendent que ce jeu » (merchants/battlestategames.toml, « only_products »)."""

    TARKOV = "https://www.escapefromtarkov.com/preorder-page#preorder_unheard_edition"

    def check(self, product, url):
        page = TestCheckOffer.INTERSTITIAL.replace(TestRedirection.KINGUIN, url).replace(
            TestRedirection.KINGUIN.replace("/", "\\/"), url.replace("/", "\\/"))
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, page)]) as get, mock.patch.object(pc, "REQUEST_DELAY", 0):
            res = pc.check_offer(product, offer(merchantName="BattlestateGames", edition="Unheard Edition", region="GLOBAL",
                                                region_filter="PUBLISHER GLOBAL", platform="game-code"))
        return res, get.call_count

    def test_the_publisher_shop_sells_only_its_game(self):
        res, calls = self.check("Escape from Tarkov", self.TARKOV)
        self.assertEqual(res["verdict"], "OK", res)
        self.assertIn("shop that sells only Escape from Tarkov (merchant config)", res["notes"])
        self.assertEqual(calls, 1, "the merchant page is not needed")

    def test_only_for_that_product(self):
        self.assertTrue(pc.sells_only(pc.merchant_config(self.TARKOV, "BattlestateGames"), "Escape from Tarkov"))
        self.assertFalse(pc.sells_only(pc.merchant_config(self.TARKOV, "BattlestateGames"), "Escape from Tarkov Arena"))
        self.assertFalse(pc.sells_only(pc.merchant_config("https://www.kinguin.net/category/1/x", "Kinguin"), "Escape from Tarkov"))


class TestCompetitors20261006(unittest.TestCase):
    """Romain, 06/10/2026 : un widget par concurrent sur l'onglet Price check, pour les tops ; le meilleur prix affiché par
    le concurrent en vert si AllKeyShop est moins cher, en rouge sinon, le premier prix AKS à côté ; toutes les 30 min."""

    DL = ('<script type="application/ld+json">{"@context":"https://schema.org","@type":"Product","name":"STAR WARS: Galactic '
          'Racer","offers":{"@type":"AggregateOffer","offerCount":73,"lowPrice":"32.48","highPrice":"159.99",'
          '"priceCurrency":"EUR","seller":{"@type":"Organization","name":"GAMESEAL"}}}</script>')
    GO = ('<script type="application/ld+json">{"@type":"Product","name":"STAR WARS Galactic Racer\u2122","offers":{"@type":'
          '"AggregateOffer","priceCurrency":"EUR","lowPrice":"22.67","offers":[{"@type":"Offer","price":"33.69","seller":'
          '{"name":"Instant Gaming"}},{"@type":"Offer","price":"22.67","seller":{"name":"Difmark"}}]}}</script>')

    def test_the_best_displayed_price_of_a_competitor_page(self):
        self.assertEqual(pc.competitor_offer(self.DL), {"name": "STAR WARS: Galactic Racer", "price": 32.48, "seller": "GAMESEAL",
                                                        "offers": [{"price": 32.48, "seller": "GAMESEAL"}], "account": None})
        self.assertEqual(pc.competitor_offer(self.GO), {"name": "STAR WARS Galactic Racer™", "price": 22.67, "seller": "Difmark",
                                                        "offers": [{"price": 22.67, "seller": "Difmark"}, {"price": 33.69, "seller": "Instant Gaming"}],
                                                        "account": None})
        self.assertIsNone(pc.competitor_offer("<html>Access Denied</html>"))
        self.assertTrue(pc.same_product("STAR WARS Galactic Racer", "STAR WARS: Galactic Racer"))
        self.assertTrue(pc.same_product("STAR WARS Galactic Racer", "STAR WARS Galactic Racer™"))
        self.assertFalse(pc.same_product("STAR WARS Galactic Racer", "STAR WARS Zero Company"))
        self.assertEqual(pc.slug_core("star-wars-galactic-racer-steam-key"), "star-wars-galactic-racer")

    def test_keys_against_keys_accounts_against_accounts(self):
        # Romain, 06/10/2026 : « on compare clé avec clé et compte avec compte. On ne mélange pas. C'est une règle
        # importante. » gocdkeys liste ses offres : l'adresse du marchand dit si c'est un compte (relevé du 06/10)
        offers = [("https://www.driffle.com/star-wars-galactic-racer-global-pc-steam-account-p10002468?currency=EUR", "28.93", "Driffle"),
                  ("https://difmark.com/en/buy-console-account-star-wars-galactic-racer-steam-account-177035?referal=Gocdkeys", "31.07", "Difmark"),
                  ("https://www.instant-gaming.com/en/22527-/?igr=178567", "33.69", "Instant Gaming"),
                  ("https://impact.gameseal.com/c/3311119/2121715/25825?prodsku=SW152836&u=https%3A%2F%2Fgameseal.com%2Fstar-wars-galactic-racer-pc-steam-account-global", "27.10", "GAMESEAL"),
                  ("https://impact.gameseal.com/c/3311119/2121715/25825?prodsku=SW152835&u=https%3A%2F%2Fgameseal.com%2Fstar-wars-galactic-racer-pc-steam-gift-global", "36.51", "GAMESEAL")]
        page = ('<script type="application/ld+json">' + json.dumps({"@type": "Product", "name": "STAR WARS Galactic Racer", "offers": {
            "@type": "AggregateOffer", "priceCurrency": "EUR", "lowPrice": "27.10", "offers": [
                {"@type": "Offer", "url": u, "price": p, "seller": {"name": s}} for u, p, s in offers]}}) + "</script>")
        self.assertEqual(pc.competitor_offer(page), {
            "name": "STAR WARS Galactic Racer", "price": 33.69, "seller": "Instant Gaming",
            "offers": [{"price": 33.69, "seller": "Instant Gaming"}, {"price": 36.51, "seller": "GAMESEAL"}],
            "account": {"price": 27.1, "seller": "GAMESEAL", "offers": [{"price": 27.1, "seller": "GAMESEAL"},
                                                                       {"price": 28.93, "seller": "Driffle"}, {"price": 31.07, "seller": "Difmark"}]}})
        only_accounts = page.replace("https://www.instant-gaming.com/en/22527-/", "https://x.com/star-wars-galactic-racer-steam-account").replace(
            "pc-steam-gift-global", "pc-steam-account-global")
        self.assertIsNone(pc.competitor_offer(only_accounts)["price"], "an account became the key price")

    def test_the_cheapest_offer_of_each_seller_for_the_fee_to_move_to_the_next(self):
        # Romain, 06/10/2026 : « pourquoi Instant Gaming reste premier prix alors que j'y ai rajouté 20 € ? » : le fee
        # s'applique au marchand ; l'offre suivante du concurrent (Kinguin à 36,40 €) doit être connue
        offers = [{"price": "37.31", "seller": {"name": "K4G"}}, {"price": "33.69", "seller": {"name": "Instant Gaming"}},
                  {"price": "37.02", "seller": {"name": "K4G"}}, {"price": "36.40", "seller": {"name": "Kinguin"}}]
        self.assertEqual(pc.best_offers(offers), {"price": 33.69, "seller": "Instant Gaming", "offers": [
            {"price": 33.69, "seller": "Instant Gaming"}, {"price": 36.4, "seller": "Kinguin"}, {"price": 37.02, "seller": "K4G"}]})
        self.assertIsNone(pc.best_offers([]))

    def test_dlcompare_search_picks_the_product_page_named_like_the_game(self):
        search = ('<a href="https://www.dlcompare.fr/actualites-gaming/star-wars-galactic-racer-mise-tout-sur-la-vitesse-85034">'
                  '<a href="https://www.dlcompare.fr/jeux/100035197/acheter-star-wars-zero-company-steam-key">'
                  '<a href="https://www.dlcompare.fr/jeux/100037294/acheter-star-wars-galactic-racer-steam-key">')
        pages = {"https://www.dlcompare.fr/search?q=STAR%20WARS%20Galactic%20Racer": search,
                 "https://www.dlcompare.fr/jeux/100037294/acheter-star-wars-galactic-racer-steam-key": self.DL}
        with mock.patch.object(pc, "competitor_get", side_effect=pages.get):
            found = pc.find_competitor(pc.COMPETITORS[1], "STAR WARS Galactic Racer")
        self.assertEqual((found["url"], found["price"]), ("https://www.dlcompare.fr/jeux/100037294/acheter-star-wars-galactic-racer-steam-key", 32.48))

    def test_allkeyshop_side_is_the_best_displayed_price_too(self):
        trans = {"editions": {"1": {"name": "Standard"}}, "prices": [
            {"id": 1, "price": 0.02, "priceCard": 0.02, "dispo": 1, "edition": "1", "merchantName": "Loaded"},  # sans prix
            {"id": 2, "price": 35.59, "priceCard": 39.95, "dispo": 1, "edition": "1", "merchantName": "Kinguin"},
            {"id": 3, "price": 30.87, "priceCard": 34.70, "dispo": 1, "edition": "1", "merchantName": "Kinguin", "account": True},
            {"id": 4, "price": 20.00, "priceCard": 20.00, "dispo": 0, "edition": "1", "merchantName": "Épuisé"}]}
        # clé contre clé, compte contre compte (Romain, 06/10/2026) ; frais de carte compris, comme la page (07/10/2026)
        self.assertEqual(pc.aks_best_price(trans), {"price": 39.95, "merchant": "Kinguin", "account": False, "edition": "Standard"})
        self.assertEqual(pc.aks_best_price(trans, account=True), {"price": 34.7, "merchant": "Kinguin", "account": True, "edition": "Standard"})
        # le premier prix de la page avec les frais de carte n'est pas forcément le moins cher sans frais (STAR WARS, 07/10)
        driffle = dict(trans, prices=[{"id": 5, "price": 35.59, "priceCard": 39.95, "dispo": 1, "edition": "1", "merchantName": "Kinguin"},
                                      {"id": 6, "price": 36.99, "priceCard": 39.70, "dispo": 1, "edition": "1", "merchantName": "Driffle"}])
        self.assertEqual(pc.aks_best_price(driffle)["merchant"], "Driffle")
        self.assertEqual(pc.compare_prices({"price": 30.87}, {"price": 32.48}), "aks")
        # Romain, 06/10/2026 : « couleur orange quand on est au même prix que le concurrent » (au centime près)
        self.assertEqual(pc.compare_prices({"price": 32.48}, {"price": 32.48}), "same")
        self.assertEqual(pc.compare_prices({"price": 32.48}, {"price": 32.480000001}), "same")
        self.assertEqual(pc.compare_prices({"price": 32.47}, {"price": 32.48}), "aks", "one cent cheaper is cheaper")
        self.assertEqual(pc.compare_prices({"price": 32.49}, {"price": 32.48}), "competitor")
        self.assertEqual(pc.compare_prices({"price": 30.87}, {"price": 22.67}), "competitor")
        self.assertIsNone(pc.compare_prices({"price": 30.87}, None))

    def test_the_check_covers_the_tops_and_says_gg_deals_is_blocked(self):
        targets = [("Popular", 1, "STAR WARS Galactic Racer", "https://www.allkeyshop.com/blog/buy-star-wars-galactic-racer-cd-key-compare-prices/")]
        trans = {"editions": {"1": {"name": "Standard"}}, "prices": [
            {"id": 2, "price": 30.87, "dispo": 1, "edition": "1", "merchantName": "Kinguin"},
            {"id": 3, "price": 25.00, "dispo": 1, "edition": "1", "merchantName": "Kinguin", "account": True}]}
        found = {"dlcompare": {"url": "https://dl/x", "name": "STAR WARS: Galactic Racer", "price": 32.48, "seller": "GAMESEAL", "account": None},
                 "gocdkeys": {"url": "https://go/x", "name": "STAR WARS Galactic Racer™", "price": 22.67, "seller": "Difmark",
                              "account": {"price": 20.0, "seller": "Driffle"}}}
        state = {"checked": {}}
        with mock.patch.object(pc, "http_get", return_value=(200, None, "<html>")), mock.patch.object(pc, "parse_game_page", return_value=trans), \
             mock.patch.object(pc, "find_competitor", side_effect=lambda site, product, cached=None: found.get(site["id"])), \
             mock.patch.object(pc, "PAGE_DELAY", 0), mock.patch.dict(os.environ, {"GGDEALS_API_KEY": ""}):
            payload = pc.check_competitors(targets, state, now=1791300000)
        sites = {x["id"]: x for x in payload["sites"]}
        self.assertEqual(sites["gg-deals"]["status"], "blocked")
        self.assertIn("GGDEALS_API_KEY", sites["gg-deals"]["message"], "without its key, gg.deals says why")
        dl, go = sites["dlcompare"]["rows"][0], sites["gocdkeys"]["rows"][0]
        self.assertEqual((dl["cheaper"], dl["gap"]), ("aks", 1.61))
        self.assertEqual((go["cheaper"], go["gap"]), ("competitor", -8.2))
        # les comptes à part : le compte AllKeyShop face au compte du concurrent, jamais face à une clé
        self.assertEqual(sites["dlcompare"]["accounts"], [])
        account = sites["gocdkeys"]["accounts"][0]
        self.assertEqual((account["aks"]["price"], account["competitor"]["price"], account["cheaper"], account["gap"]),
                         (25.0, 20.0, "competitor", -5.0))
        self.assertEqual(state["competitors"][targets[0][3]]["dlcompare"]["url"], "https://dl/x", "the page found is not kept")
        self.assertEqual(payload["every"], 1800)

    def test_a_console_page_is_not_compared_with_the_pc_price(self):
        # 06/10/2026 : « EA SPORTS FC 27 PS5 » tombait sur « acheter-ea-sports-fc-27-pc-cd-key » (gocdkeys, 23,93 €, le prix
        # de la page PC) et sur la fiche toutes plateformes de dlcompare : un prix PS5 face à un prix PC
        targets = [("Popular", 3, "EA SPORTS FC 27 PS5", "https://www.allkeyshop.com/blog/buy-ea-sports-fc-27-ps5-key-compare-prices/"),
                   ("Popular", 4, "EA SPORTS FC 27", "https://www.allkeyshop.com/blog/buy-ea-sports-fc-27-key-compare-prices/")]
        trans = {"editions": {"1": {"name": "Standard"}}, "prices": [
            {"id": 3, "price": 36.71, "dispo": 1, "edition": "1", "merchantName": "BuyGames"}]}
        asked = []
        def find(site, product, cached=None):
            asked.append(product)
            return {"url": "https://go/fc27-pc", "name": "EA Sports FC 27", "price": 23.93, "seller": "Gamivo"}
        with mock.patch.object(pc, "http_get", return_value=(200, None, "<html>")), mock.patch.object(pc, "parse_game_page", return_value=trans), \
             mock.patch.object(pc, "find_competitor", side_effect=find), mock.patch.object(pc, "PAGE_DELAY", 0):
            payload = pc.check_competitors(targets, {"checked": {}}, now=1791300000)
        for site in payload["sites"]:
            if site["status"] != "ok":
                continue
            ps5, pc_page = site["rows"]
            self.assertEqual((ps5["skipped"], ps5["competitor"], ps5["cheaper"]), ("console", None, None), site["id"])
            self.assertEqual(ps5["aks"]["price"], 36.71, "AllKeyShop's first price is still shown")
            self.assertEqual(pc_page["competitor"]["price"], 23.93)
        self.assertNotIn("EA SPORTS FC 27 PS5", asked, "a console page was looked up at a competitor")


class TestGgDealsApi20261006(unittest.TestCase):
    """Romain, 06/10/2026 : la clé de l'API gg.deals (gg.deals bloque le serveur, 403 Cloudflare). L'API se consulte par
    identifiant Steam (by-steam-app-id), région France ; le meilleur prix affiché : boutiques officielles ou keyshops."""

    OK = json.dumps({"success": True, "data": {
        "2001": {"title": "STAR WARS Galactic Racer", "url": "https://gg.deals/game/star-wars-galactic-racer/",
                 "prices": {"currentRetail": "39.99", "currentKeyshops": "29.10", "currency": "EUR"}},
        "2002": {"title": "WARDOGS", "url": "javascript:alert(1)", "prices": {"currentRetail": "12.49", "currentKeyshops": None, "currency": "EUR"}},
        "2003": {"title": "Dollars", "url": "https://gg.deals/game/x/", "prices": {"currentRetail": "9.99", "currency": "USD"}},
        "2004": None}})

    def test_the_best_displayed_price_official_stores_or_keyshops(self):
        with mock.patch.object(pc, "http_get", return_value=(200, None, self.OK.encode())) as get, mock.patch.object(pc, "REQUEST_DELAY", 0):
            prices = pc.ggdeals_prices(["2001", "2002", "2003", "2004"], "secret-key")
        self.assertEqual(prices["2001"], {"url": "https://gg.deals/game/star-wars-galactic-racer/", "name": "STAR WARS Galactic Racer",
                                          "price": 29.1, "seller": "keyshops", "offers": [
                                              {"price": 29.1, "seller": "keyshops"}, {"price": 39.99, "seller": "official stores"}]})
        self.assertEqual((prices["2002"]["price"], prices["2002"]["seller"], prices["2002"]["url"]), (12.49, "official stores", "https://gg.deals/"))
        self.assertNotIn("2003", prices, "a price in dollars is not compared")
        self.assertNotIn("2004", prices)
        url = get.call_args[0][0]
        self.assertTrue(url.startswith("https://api.gg.deals/v1/prices/by-steam-app-id/?ids=2001%2C2002%2C2003%2C2004&region=fr&key="), url)

    def test_a_refusal_says_why_and_never_shows_the_key(self):
        refused = json.dumps({"success": False, "data": {"name": "Bad Request", "message": "You need to confirm your email address.",
                                                          "code": 400, "status": 400}}).encode()
        with mock.patch.object(pc, "http_get", return_value=(400, None, refused)), mock.patch.object(pc, "REQUEST_DELAY", 0):
            with self.assertRaises(pc.CompetitorBlocked) as e:
                pc.ggdeals_prices(["2001"], "secret-key")
        self.assertEqual(str(e.exception), "gg.deals API key not active yet: confirm the email address of the gg.deals account")
        with mock.patch.object(pc, "http_get", side_effect=OSError("timed out https://api.gg.deals/?key=secret-key")), \
             mock.patch.object(pc, "REQUEST_DELAY", 0):
            with self.assertRaises(pc.CompetitorBlocked) as e:
                pc.ggdeals_prices(["2001"], "secret-key")
        self.assertNotIn("secret-key", str(e.exception))

    def test_the_rows_one_api_call_steam_ids_kept_console_pages_skipped(self):
        rows = [{"product": "STAR WARS Galactic Racer", "page_url": "https://aks/sw", "list": "Popular", "rank": 1, "aks": {"price": 30.87}},
                {"product": "EA SPORTS FC 27 PS5", "page_url": "https://aks/fc-ps5", "list": "Popular", "rank": 2, "aks": {"price": 36.71}},
                {"product": "Gears of War E-Day", "page_url": "https://aks/gears", "list": "Popular", "rank": 3, "aks": {"price": 46.78}}]
        steam = {"STAR WARS Galactic Racer": "2001", "Gears of War E-Day": None}
        memo = {}
        with mock.patch.dict(os.environ, {"GGDEALS_API_KEY": "secret-key"}), \
             mock.patch.object(pc, "steam_app_id", side_effect=lambda product: steam[product]) as search, \
             mock.patch.object(pc, "http_get", return_value=(200, None, self.OK.encode())) as get, mock.patch.object(pc, "REQUEST_DELAY", 0):
            out = pc.ggdeals_rows(rows, memo, 1791300000)
            again = pc.ggdeals_rows(rows, memo, 1791300000 + 1800)
        self.assertEqual(get.call_count, 2, "one API call per check, for every page")
        self.assertEqual(search.call_count, 2, "the Steam ids are kept: a game not found is searched again a day later")
        sw, fc, gears = out
        self.assertEqual((sw["competitor"]["price"], sw["cheaper"], sw["gap"]), (29.1, "competitor", -1.77))
        self.assertEqual((fc["skipped"], fc["competitor"]), ("console", None))
        self.assertIsNone(gears["competitor"])
        self.assertEqual(again[0]["competitor"]["price"], 29.1)

    def test_the_steam_id_is_the_game_with_the_same_name(self):
        found = json.dumps({"total": 2, "items": [{"type": "app", "name": "STAR WARS Outlaws", "id": 1},
                                                  {"type": "app", "name": "STAR WARS™ Galactic Racer", "id": 2001}]}).encode()
        with mock.patch.object(pc, "competitor_get", return_value=found):
            self.assertEqual(pc.steam_app_id("STAR WARS Galactic Racer"), "2001")
        with mock.patch.object(pc, "competitor_get", return_value=None):
            self.assertIsNone(pc.steam_app_id("STAR WARS Galactic Racer"))


class TestEnglish20261007(unittest.TestCase):
    """Romain, 07/10/2026 : « tout l'outil en anglais ». The codes stay (verdicts À VÉRIFIER, NON VÉRIFIABLE; decisions vrai,
    faux, a_discuter); what the monitor writes is English, and what it re-reads on an older entry is known in French too."""

    def test_the_alert_shows_english_labels_over_the_codes(self):
        res = {"verdict": "À VÉRIFIER", "reasons": ["in doubt: extra words after the name: « remastered »"], "notes": [],
               "method": "URL", "url": "https://x/witcher-3-remastered"}
        msg = pc.format_alert("Popular", 2, "The Witcher 3 Wild Hunt", "https://aks/x", offer(edition_rank=1), res)
        self.assertIn("🟠 **TO CHECK** · **The Witcher 3 Wild Hunt** (Popular #2) · Standard · 1st price of the edition", msg)
        self.assertIn(" · offer 1 · check: URL", msg)
        self.assertNotIn("offre", msg)
        self.assertEqual(pc.ordinal(2), "2nd")
        self.assertEqual(pc.ordinal(11), "11th")
        self.assertEqual(pc.ordinal(23), "23rd")

    def test_an_older_french_stock_reason_is_still_recognized(self):
        # an entry written before 07/10/2026 keeps its French reason: the out-of-stock follow-up must still see it
        old = "offre en rupture chez le marchand : le lien redirige vers une autre fiche (https://k/1), mais le prix reste dans le feed"
        self.assertTrue(old.startswith(pc.STOCK_REASON_STARTS))
        self.assertEqual(pc.seen_changes({"reasons": [old]}, {"reasons": []}), ["page served"])


class TestRomainTab20261007(unittest.TestCase):
    """Les questions de l'onglet Romain (récolte du 06/10/2026), réglées avec Romain le 07/10/2026. Sa consigne du jour :
    « il ne faut pas hésiter à faire des reports, même si c'est des faux positifs ; je veux pas qu'on fasse des règles
    bizarres qui cassent tout » : un seul changement de règle (Q7, déjà validé), deux alias (Q5), le reste reporté."""

    def reason(self, product, edition, url, editions, main=None):
        return pc.page_edition_reason({"edition": edition, "page_editions": editions, "page_main_edition": main},
                                      pc.norm(pc.url_text(url)), product)

    def check(self, product, url, page="", **kw):
        interstitial = TestCheckOffer.INTERSTITIAL.replace(TestRedirection.KINGUIN, url).replace(
            TestRedirection.KINGUIN.replace("/", "\\/"), url.replace("/", "\\/"))
        def get(u, ua, follow=True, timeout=30):
            return (200, None, interstitial) if "allkeyshop" in u else (403, None, page)
        with mock.patch.object(pc, "http_get", side_effect=get), mock.patch.object(pc, "page_title", return_value=None), \
             mock.patch.object(pc, "chromium_dom", return_value=None), mock.patch.object(pc, "REQUEST_DELAY", 0):
            return pc.check_offer(product, offer(**kw))

    def test_q7_digital_deluxe_is_the_deluxe_edition(self):
        # Romain : « Digital deluxe edition = Deluxe edition » (Eneba 137662983, Driffle 140438327), « a good rule » :
        # les synonymes d'édition valent aussi pour « une autre édition de la page nommée par l'URL »
        wukong = ["Standard", "Deluxe", "Digital Deluxe Edition"]
        for url in ("https://www.eneba.com/xbox-black-myth-wukong-digital-deluxe-edition-xbox-series-x-s-xbox-live-key-europe",
                    "https://www.driffle.com/black-myth-wukong-digital-deluxe-edition-europe-xbox-series-xs-xbox-live-digital-key-p10001019"):
            with self.subTest(url=url):
                self.assertIsNone(self.reason("Black Myth Wu Kong Xbox Series", "Deluxe", url, wukong))
                # la Digital Deluxe rangée en Standard reste une erreur
                self.assertEqual(self.reason("Black Myth Wu Kong Xbox Series", "Standard", url, wukong),
                                 "edition: filed under Standard, the merchant sells digital (the page has the edition Digital Deluxe Edition)")
        self.assertTrue(pc.same_edition("Digital Deluxe Edition", "Deluxe"))
        self.assertTrue(pc.same_edition("Game of the Year Edition", "GOTY"))
        self.assertFalse(pc.same_edition("Eclipse Edition", "Deluxe"))
        # les autres éditions nommées par l'URL restent des erreurs (les vrais positifs du 07/10)
        stellaris = ["Standard", "Deluxe", "Nova Edition", "Galaxy Edition", "Limited", "Explorer", "Bonus"]
        self.assertIsNotNone(self.reason("Stellaris", "Deluxe", "https://www.gamingdragons.com/en/game/buy-stellaris-nova-steam-key.html", stellaris))
        self.assertIsNotNone(self.reason("Gran Turismo 7 PS5", "Deluxe", "https://store.playstation.com/fr-fr/product/x-gran-turismo-7-25th-anniversary-edition",
                                         ["Standard", "Deluxe", "25th Anniversary Edition"]))

    def test_q9_dawnwalker_eclipse_edition_deluxe_is_the_deluxe(self):
        # Romain : « eclipse-edition-deluxe = deluxe », « C est bien une edition deluxe » (Eneba 140458058) : à égalité
        # (« eclipse » contre « deluxe »), pas d'alerte depuis la correction GTA 5 du 06/10 ; éditions réelles de la page
        dawnwalker = ["Standard", "Standard + Bonus", "Eclipse Edition", "Bonus", "Deluxe", "Eclipse Edition + Bonus"]
        self.assertIsNone(self.reason("The Blood Of Dawnwalker", "Deluxe",
                                      "https://www.eneba.com/steam-the-blood-of-dawnwalker-eclipse-edition-deluxe-steam-key-pc-europe",
                                      dawnwalker, main="Standard"))

    def test_q5_the_steam_names_of_two_top_games(self):
        # Romain : « Oui, les 2 alias » : gg.deals cherche le jeu par son nom Steam (identifiant Steam)
        self.assertTrue(pc.same_product("The Witcher 3 Wild Hunt", "The Witcher 3: Wild Hunt — Remastered"))
        self.assertTrue(pc.same_product("Ace Combat 8", "ACE COMBAT 8: WINGS OF THEVE"))
        self.assertFalse(pc.same_product("The Witcher 3 Wild Hunt", "The Witcher 3: Wild Hunt — Remastered Soundtrack"))
        self.assertFalse(pc.same_product("Ace Combat 8", "ACE COMBAT 8 - Playable Aircraft: F-14A"))

    def test_q1_wow_forever_heroic_pack_at_driffle(self):
        # Romain : « Heroic pack est une edition pour ce DLC, battlenet Gift est correct » (Driffle 140501637)
        res = self.check("World of Warcraft: Forever",
                         "https://www.driffle.com/warcraft-forever-skyborne-heroic-pack-dlc-global-pc-mac-battlenet-gift-p10001673",
                         merchantName="Driffle", edition="Heroic Pack", region="BATTLENET GIFT", region_filter="BATTLENET GIFT",
                         platform="battle-net")
        self.assertEqual(res["verdict"], "OK", res)

    def test_q2_a_doubt_is_still_reported(self):
        # Romain : « still report if we have a doubt » (PS Store 135588553 : un code produit sans le nom du jeu, page
        # illisible ; jugée faux par Rémy) : jamais d'OK, et une alerte quand c'est le premier prix d'une page des tops
        res = self.check("Cyberpunk 2077 PS5", "https://store.playstation.com/fr-fr/product/EP4497-PPSA04029_00-EXPANSION1B00000",
                         merchantName="PS Store FR", edition="Ultimate", region="PS5", region_filter="PS5", platform="playstation-store")
        self.assertIn(res["verdict"], ("NON VÉRIFIABLE", "À VÉRIFIER"), res)
        self.assertEqual(pc.unverifiable_verdict(offer(page_first=True), "Popular",
                                                 "https://www.allkeyshop.com/blog/buy-cyberpunk-2077-ps5-compare-prices/",
                                                 res.get("unverifiable") or "first-price"), "À VÉRIFIER")

    def test_q4_gamivo_row_key_shown_europe_stays_an_error(self):
        # Romain : « Rien, reste SUSPECT » (GAMIVO 136411763, jugée faux par Rémy) : la page GAMIVO exclut 209 pays, dont
        # l'Estonie, la Lettonie et la Lituanie ; pas de doute comme chez G2A
        res = self.check("The Last of Us Part II Remastered",
                         "https://www.gamivo.com/product/the-last-of-us-part-ii-remastered-pc-steam-row-standard",
                         merchantName="GAMIVO", edition="Standard", region="EUROPE", region_filter="STEAM EU")
        self.assertEqual(res["verdict"], "SUSPECT", res)
        self.assertIn("region: AllKeyShop EUROPE, merchant ROW", res["reasons"])

    def test_q6_the_70_percent_rule_stays(self):
        # Romain : « We keep the 70% rules, we will forever need to double check manually » (Lootbar 140375811, 64 %)
        o = [offer(id=1, price=11.85, page_first=True, edition_rank=1, merchantName="Lootbar"),
             offer(id=2, price=18.39, edition_rank=2, merchantName="Instant Gaming")]
        res = pc.apply_price_gap({"verdict": "OK", "reasons": [], "notes": []}, o[0], o)
        self.assertEqual(res["verdict"], "SUSPECT")
        self.assertIn("64 %", res["reasons"][0])


class TestCurrencyQuantities20261008(unittest.TestCase):
    """Romain, 08/10/2026 : « si on prend l'exemple des Vbucks, tu verras que ce n'est pas le même nombre de Vbucks. Donc,
    qu'on compare les prix, il faut qu'on compare les prix avec le même nombre de points ou de Vbucks. »"""

    def test_a_quantity_of_currency_is_compared_with_the_same_quantity(self):
        # Fortnite V-Bucks PS5 (140251759) : les 800 V-Bucks d'Eneba à 15,61 €, seuls de leur quantité, alertés à 41 % des
        # 2400 V-Bucks (37,89 €) ; au V-Buck, c'est même la carte la plus chère (1,95 c contre 1,58 c)
        page = [offer(id=1, price=15.61, edition="800 V-Bucks", page_first=True, merchantName="Eneba"),
                offer(id=2, price=37.89, edition="2400 V-Bucks", merchantName="Eneba"),
                offer(id=3, price=44.60, edition="2800 V-Bucks", merchantName="CJS CDKeys")]
        self.assertIsNone(pc.price_gap(page[0], page))
        # la même quantité bien plus chère ailleurs : l'alerte reste
        page.append(offer(id=4, price=25.00, edition="800 V-Bucks", merchantName="Kinguin"))
        ratio, nxt = pc.price_gap(page[0], page)
        self.assertEqual((round(100 * ratio), nxt["id"]), (62, 4))

    def test_what_is_a_quantity_of_currency(self):
        for edition in ("800 V-Bucks", "1050 FC Points", "15000 VC", "1000 Coins", "2000 Coins + 200", "5000 COD Points"):
            with self.subTest(edition=edition):
                self.assertTrue(pc.currency_quantity(edition))
        for edition in ("Standard", "Gold", "2026 Season Edition", "1 Year Anniversary Edition", "Standard + Great White Shark Card",
                        "DLC", "Bundle 3", "25th Anniversary Edition"):
            with self.subTest(edition=edition):
                self.assertFalse(pc.currency_quantity(edition))

    def test_dlc_is_expected_on_a_quantity_of_currency(self):
        # Romain, 08/10/2026 (« règle simple ») : 13 offres FC 27 Points de Vidaplayer, dont 140522002 jugée faux par
        # Romain (« PSN wallet DE = points playstation Germany »), et Overwatch 2 Coins chez Kinguin (136290300)
        def reasons(product, edition, url, **kw):
            o = dict({"edition": edition, "region": "WALLET DE", "region_filter": "PSN WALLET DE", "platform": "playstation-store",
                      "merchantName": "Vidaplayer", "account": False}, **kw)
            return pc.analyze(product, o, pc.url_text(url), "URL")["reasons"]
        self.assertEqual(reasons("EA Sports FC 27 Points PS5", "1050 FC Points",
                                 "https://www.vidaplayer.com/en/product/dlc-playstation-4-5-germany/ea-sports-fc-27-1050-fc-points"), [])
        self.assertEqual(reasons("Overwatch 2 Coins Xbox Series", "1000 Coins",
                                 "https://www.kinguin.net/en/category/293896/overwatch-2-1000-coins-dlc-eu-xbox-one-xbox-series-x-s-cd-key",
                                 region="EU IN ENGLISH ONLY", region_filter="EU IN ENGLISH ONLY", platform="xbox", merchantName="Kinguin"), [])
        # un season pass vendu sur une quantité de monnaie reste signalé, et le DLC sur la page d'un jeu aussi
        self.assertIn("additional content: season-pass",
                      reasons("EA Sports FC 27 Points PS5", "1050 FC Points", "https://x.com/ea-sports-fc-27-1050-fc-points-season-pass-dlc"))
        self.assertTrue(any(r.startswith("additional content") for r in reasons(
            "Hearts of Iron 4", "Standard", "https://x.com/hearts-of-iron-4-arms-against-tyranny-dlc-steam-key",
            region="GLOBAL", region_filter="STEAM GLOBAL", platform="steam")))

    def test_on_a_points_page_the_game_and_its_currency_name_the_product(self):
        # Romain, 08/10/2026 : « si on est bien sur la page AllKeyShop correspondant aux points c'est ok, par contre si les
        # points sont sur la page du jeu c'est une erreur » (NBA 2K25 VC, 11 offres « another product »)
        product = "NBA 2K25 Virtual Currency Pack Xbox Series"
        def reasons(edition, url, product=product):
            o = {"edition": edition, "region": "XBOX X|S", "region_filter": "XBOX X|S GLOBAL", "platform": "xbox",
                 "merchantName": "x", "account": False}
            return pc.analyze(product, o, pc.url_text(url), "URL")["reasons"]
        for edition, url in (
                ("15000 VC", "https://www.instant-gaming.com/en/16386-buy-nba-2k25-15-000-virtual-currency-pack-15-000-xbox-series-x-s-xbox-one-game-microsoft-store/"),
                ("15000 VC", "https://kinguin.net/category/270390/nba-2k25-15-000-vc-pack-xbox-one-xbox-series-x-s-cd-key"),
                ("15000 VC", "https://k4g.com/product/nba-2k25-vc-xbox-one-series-x-s-xbox-global-15000-cd-key-BCD2023D"),
                ("35000 VC", "https://www.eneba.com/xbox-nba-2k25-35-000-vc-xbox-one-xbox-series-x-s-key-global"),
                ("450000 VC", "https://wyrel.com/en/buy-cheap-nba-2k25-450000-vc-xbox-series-x-149121")):
            with self.subTest(url=url):
                self.assertEqual(reasons(edition, url), [])
        # un autre jeu de la série, ou le jeu sans sa monnaie, sur la page des points : l'alerte reste
        self.assertTrue(any(r.startswith("another product") for r in reasons("15000 VC", "https://x.com/nba-2k26-15-000-vc-xbox-series-x-s-key")))
        self.assertTrue(any(r.startswith(("another product", "product name not found"))
                            for r in reasons("15000 VC", "https://x.com/nba-2k25-xbox-series-x-s-key-global")))
        # les points sur la page du jeu : une erreur (principe 12)
        self.assertTrue(any(r.startswith("in-game currency") for r in reasons(
            "Standard", "https://x.com/call-of-duty-black-ops-6-5000-cod-points-xbox-key", product="Call of Duty Black Ops 6 Xbox Series")))
        # Overwatch 2 Coins : la monnaie s'appelle « Overwatch Coins » (alias, Romain, 08/10/2026)
        ow = "Overwatch 2 Coins Xbox Series"
        self.assertEqual(reasons("2000 Coins + 200", "https://k4g.com/product/overwatch-coins-xbox-live-xbox-global-instant-cd-key-2000-cd-key-885C4C67", ow), [])
        self.assertEqual(reasons("10000 Coins", "https://www.instant-gaming.com/en/12995-buy-overwatch-10000-overwatch-coins-xbox-series-x-s-xbox-one-microsoft-store/", ow), [])

    def test_a_wallet_card_is_for_one_country(self):
        # Romain, 08/10/2026 : « ajoute le contrôle du pays pour les wallets » (une carte PSN est liée au pays du compte)
        def reasons(region, url, edition="1050 FC Points", product="EA Sports FC 27 Points PS5"):
            o = {"edition": edition, "region": "WALLET " + region, "region_filter": "PSN WALLET " + region,
                 "platform": "playstation-store", "merchantName": "Vidaplayer", "account": False}
            return pc.analyze(product, o, pc.url_text(url), "URL")["reasons"]
        vida = "https://www.vidaplayer.com/en/product/dlc-playstation-4-5-%s/ea-sports-fc-27-1050-fc-points"
        self.assertEqual(reasons("DE", vida % "germany"), [])
        self.assertEqual(reasons("SP", vida % "spain"), [])
        self.assertEqual(reasons("IT", vida % "italy"), [])
        self.assertEqual(reasons("DE", "https://x.com/ea-sports-fc-27-1050-fc-points-ps5"), [], "no country named: nothing to compare")
        self.assertEqual(reasons("DE", vida % "spain"), ["region: AllKeyShop WALLET DE, merchant spain"])
        self.assertEqual(reasons("IT", vida % "germany"), ["region: AllKeyShop WALLET IT, merchant germany"])
        # « united-kingdom » est déjà un mot de zone : une seule raison de région
        uk = [r for r in reasons("US", vida % "united-kingdom") if r.startswith("region")]
        self.assertEqual(len(uk), 1, uk)
        # hors recharge, un nom de pays n'est pas une zone (« Vive la France! », DLC d'Euro Truck Simulator 2)
        o = {"edition": "DLC", "region": "GLOBAL", "region_filter": "STEAM GLOBAL", "platform": "steam", "merchantName": "x", "account": False}
        self.assertFalse(any("merchant france" in r for r in pc.analyze(
            "Euro Truck Simulator 2", o, pc.url_text("https://x.com/euro-truck-simulator-2-vive-la-france-dlc-steam-key"), "URL")["reasons"]))

    def test_the_other_pages_keep_the_page_s_second_price(self):
        # Resident Evil 4 PS5 chez GAMESEAL (vrai positif du 07/10) : le DLC à 7,79 €, 49 % de la Gold du PS Store UK
        page = [offer(id=1, price=7.79, edition="DLC", page_first=True, merchantName="GAMESEAL"),
                offer(id=2, price=15.91, edition="Gold", merchantName="PS Store UK")]
        ratio, nxt = pc.price_gap(page[0], page)
        self.assertEqual((round(100 * ratio), nxt["edition"]), (49, "Gold"))


class TestSecurityAudit20261002(unittest.TestCase):
    """Audit sécurité du 02/10/2026 : le moniteur tourne en root."""

    def test_targets_refused(self):
        # l'URL marchand vient de la redirection AllKeyShop et des redirections du marchand, sans contrôle
        for url in ("file:///etc/hostname", "http://127.0.0.1:8650/api/price-check/run", "http://localhost/x",
                    "http://10.0.0.5/", "http://169.254.169.254/latest/meta-data/", "http://[::1]/", "ftp://example.com/x", ""):
            self.assertFalse(pc.safe_target(url), url)
        for url in ("https://www.kinguin.net/category/1/x", "https://www.allkeyshop.com/blog/x/", "http://93.184.216.34/"):
            self.assertTrue(pc.safe_target(url), url)
        with self.assertRaises(OSError):
            pc.http_get("file:///etc/hostname", pc.BROWSER_UA)
        with self.assertRaises(OSError):  # AKS/Staff ne part jamais chez un marchand
            pc.http_get("https://www.kinguin.net/category/1/x", pc.AKS_UA)

    def test_redirects_never_carry_the_aks_user_agent_off_allkeyshop(self):
        handler = pc.GuardedRedirect()
        aks = urllib.request.Request("https://www.allkeyshop.com/redirection/offer/eur/1?merchant=2", headers={"User-Agent": pc.AKS_UA})
        self.assertIsNone(handler.redirect_request(aks, None, 302, "Found", {}, "https://www.kinguin.net/category/1/x"))
        self.assertIsNotNone(handler.redirect_request(aks, None, 301, "Moved", {}, "https://www.allkeyshop.com/blog/x/"))
        shop = urllib.request.Request("https://www.kinguin.net/category/1/x", headers={"User-Agent": pc.BROWSER_UA})
        self.assertIsNotNone(handler.redirect_request(shop, None, 301, "Moved", {}, "https://www.kinguin.net/category/1/y"))
        self.assertIsNone(handler.redirect_request(shop, None, 302, "Found", {}, "http://127.0.0.1:8650/"))
        # si le lien de redirection répond un jour 302 tout droit chez le marchand : l'URL est lue dans l'en-tête,
        # et la suite du contrôle part avec l'UA navigateur
        calls = []

        def get(url, ua, follow=True, timeout=30):
            calls.append((url, ua))
            if "allkeyshop.com/redirection" in url:
                return 302, "https://www.instant-gaming.com/en/21656-buy-ea-sports-fc-27-pc-ea-app/", ""
            return 200, None, ""
        with mock.patch.object(pc, "http_get", side_effect=get):
            res = pc.check_offer("EA SPORTS FC 27", offer(merchantName="Instant Gaming", platform="ea-app",
                                                          region="GLOBAL", region_filter="EA GLOBAL"))
        self.assertEqual((res["verdict"], res["url"]), ("OK", "https://www.instant-gaming.com/en/21656-buy-ea-sports-fc-27-pc-ea-app/"))
        self.assertTrue(all(ua == pc.BROWSER_UA for url, ua in calls if "allkeyshop" not in url))
        with mock.patch.object(pc, "http_get", return_value=(200, None, '<meta http-equiv="refresh" content="0; URL=file:///etc/passwd">')):
            with self.assertRaises(pc.CheckError):
                pc.check_offer("EA SPORTS FC 27", offer())

    def test_chromium_never_runs_as_root(self):
        # rendu de pages tierces : « --no-sandbox » en root, sur un Chromium en retard de versions
        nobody = pc.pwd.struct_passwd(("nobody", "x", 65534, 65534, "", "/nonexistent", "/usr/sbin/nologin"))
        with mock.patch.object(pc.os, "geteuid", return_value=0), mock.patch.object(pc.pwd, "getpwnam", return_value=nobody), \
             mock.patch.object(pc.os, "chown") as chown, mock.patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": "https://hook/secret"}):
            cmd, env = pc.chromium_command("https://www.kinguin.net/category/1/x", "/tmp/profile")
        self.assertEqual(cmd[:4], ["setpriv", "--reuid=65534", "--regid=65534", "--clear-groups"])
        self.assertNotIn("--no-sandbox", cmd)
        chown.assert_called_once_with("/tmp/profile", 65534, 65534)
        self.assertNotIn("DISCORD_WEBHOOK_URL", env)
        self.assertEqual(cmd[-1], "https://www.kinguin.net/category/1/x")
        with mock.patch.object(pc.shutil, "which", return_value="/usr/bin/chromium"), mock.patch.object(pc.subprocess, "run") as run:
            self.assertIsNone(pc.chromium_dom("file:///etc/passwd"))
        run.assert_not_called()

    def test_shared_directory_never_written_or_read_through_a_symlink(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            victim = os.path.join(d, "victime.txt")
            with open(victim, "w") as f:
                f.write("secret")
            os.symlink(victim, os.path.join(d, "status.json.tmp"))
            os.symlink(victim, os.path.join(d, "status.json"))
            pc.write_status(d, {"modes": {}})
            with open(victim) as f:
                self.assertEqual(f.read(), "secret")
            self.assertFalse(os.path.islink(os.path.join(d, "status.json")))
            with open(os.path.join(d, "status.json"), encoding="utf-8") as f:
                self.assertIn("modes", json.load(f))
            self.assertEqual(oct(os.stat(os.path.join(d, "status.json")).st_mode & 0o777), "0o644")
            with open(victim, "w") as f:
                f.write(json.dumps({"offer": "1", "decision": "faux"}) + "\n")
            os.symlink(victim, os.path.join(d, "decisions.jsonl"))
            self.assertEqual(pc.read_decisions(d), {})  # un lien symbolique n'est pas lu

    def test_state_backup_once_an_hour(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            pc.save_state(path, {"checked": {"1": {}}, "merchants": {}})
            with open(path + ".bak", encoding="utf-8") as f:
                self.assertEqual(json.load(f)["checked"], {"1": {}})
            pc.save_state(path, {"checked": {}, "merchants": {}})  # moins d'une heure après : la copie reste
            with open(path + ".bak", encoding="utf-8") as f:
                self.assertEqual(json.load(f)["checked"], {"1": {}})


@mock.patch.object(pc, "REQUEST_DELAY", 0)
@mock.patch.object(pc, "PAGE_DELAY", 0)
class TestCycle(unittest.TestCase):
    PAGE = sample("prod_popular1_ea-fc-27.html")
    TARGETS = [("Popular", 1, "EA SPORTS FC 27", "https://www.allkeyshop.com/blog/buy-ea-sports-fc-27-key-compare-prices/")]

    def setUp(self):
        pc.FAILURES.clear()
        patcher = mock.patch.object(pc, "http_get", return_value=(200, None, self.PAGE))
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def ok(product, o):
        return {"verdict": "OK", "reasons": [], "notes": [], "url": "https://shop.example/x", "method": "URL"}

    def test_each_first_price_checked_once(self):
        sent, state, checks = [], pc.load_state("/nonexistent"), []

        def checker(product, o):
            checks.append(o["id"])
            return self.ok(product, o)

        pc.run_cycle(self.TARGETS, sent.append, state, checker)
        pc.run_cycle(self.TARGETS, sent.append, state, checker)
        self.assertEqual(len(checks), 4)  # 4 éditions, contrôlées une seule fois
        self.assertEqual(len(sent), 4)
        self.assertTrue(sent[0].startswith("🟢 **OK** · **EA SPORTS FC 27** (Popular #1) · Standard"))
        self.assertIn("Mmoga · IN ENGLISH ONLY (EA ENG/POL/RUS ONLY) · ea-app · **54.99 €**", sent[0])
        self.assertEqual(state["merchants"]["Mmoga"]["methods"], {"URL": 1})
        self.assertIn("| Mmoga | URL (1) | ", pc.coverage_table(state))
        self.assertEqual({v["verdict"] for v in state["checked"].values()}, {"OK"})

    def test_ok_not_sent_when_disabled(self):
        sent, state = [], pc.load_state("/nonexistent")
        with mock.patch.object(pc, "NOTIFY_OK", False):
            pc.run_cycle(self.TARGETS, sent.append, state, self.ok)
        self.assertEqual(sent, [])
        self.assertEqual(len(state["checked"]), 4)

    def test_suspect_alert(self):
        def suspect(product, o):
            return {"verdict": "SUSPECT", "reasons": ["nom du produit absent (URL)"], "notes": [],
                    "url": "https://shop.example/sonic", "method": "URL"}

        sent, state = [], pc.load_state("/nonexistent")
        with mock.patch.object(pc, "NOTIFY_OK", False):
            pc.run_cycle(self.TARGETS, sent.append, state, suspect)
        self.assertEqual(len(sent), 4)
        self.assertIn("🔴 **SUSPECT**", sent[0])
        self.assertIn("Reason: nom du produit absent (URL)", sent[0])
        self.assertIn("Merchant: <https://shop.example/sonic>", sent[0])

    def test_check_failures_then_manual(self):
        def failing(product, o):
            raise pc.CheckError("redirection AllKeyShop HTTP 503")

        sent, state = [], pc.load_state("/nonexistent")
        for _ in range(pc.MAX_CHECK_FAILURES - 1):
            pc.run_cycle(self.TARGETS, sent.append, state, failing)
        self.assertEqual((sent, state["checked"]), ([], {}))
        pc.run_cycle(self.TARGETS, sent.append, state, failing)
        # seul le premier prix de la page (page d'un top « Popular ») part en À VÉRIFIER ; les autres éditions sont notées
        self.assertEqual(len(sent), 1)
        self.assertIn("🟠 **TO CHECK**", sent[0])
        self.assertIn("check impossible: redirection AllKeyShop HTTP 503", sent[0])
        self.assertEqual(sorted(v["verdict"] for v in state["checked"].values()), ["NON VÉRIFIABLE"] * 3 + ["À VÉRIFIER"])
        self.assertIn("| EA SPORTS FC 27 |", pc.unverified_table(state))

    def test_unverifiable_policy(self):
        page = "https://www.allkeyshop.com/blog/x/"
        with mock.patch.dict(pc.PAGE_LISTS, {page: {"Home · FPS", "TOP 50 · PC Popular"}}, clear=True):
            self.assertEqual(pc.unverifiable_verdict({"page_first": True}, "Home · FPS", page), "À VÉRIFIER")
            self.assertEqual(pc.unverifiable_verdict({"page_first": False}, "Home · FPS", page), "NON VÉRIFIABLE")
            self.assertEqual(pc.unverifiable_verdict({"page_first": True}, "Home · FPS", page, "note"), "NON VÉRIFIABLE")
        with mock.patch.dict(pc.PAGE_LISTS, {page: {"Home · RPG"}}, clear=True):
            self.assertEqual(pc.unverifiable_verdict({"page_first": True}, "Home · RPG", page), "NON VÉRIFIABLE")
        for label in ("Coming soon PC", "Home · Most anticipated", "TOP 50 · Xbox Coming soon", "Popular"):
            self.assertTrue(pc.in_top_or_soon("https://www.allkeyshop.com/blog/other/", label), label)

    def test_page_lists_memberships(self):
        data = json.loads(sample("api_topclick_home.json"))
        pc.PAGE_LISTS.clear()
        targets = pc.parse_lists(data, pc.HOMEPAGE_LISTS)
        multi = [u for u, labels in pc.PAGE_LISTS.items() if len(labels) > 1]
        self.assertTrue(multi)  # des pages sont dans plusieurs listes : un widget de la home et un TOP 50
        self.assertEqual(len(targets), len(pc.PAGE_LISTS))

    def test_amazon_is_never_reported_when_unverifiable(self):
        self.assertEqual(pc.merchant_config("https://www.amazon.fr/dp/B0/", "Amazon.fr").get("unverifiable"), "note")

    def test_discord_failure_retries_next_cycle(self):
        def flaky(msg):
            raise OSError("discord down")

        sent, state = [], pc.load_state("/nonexistent")
        pc.run_cycle(self.TARGETS, flaky, state, self.ok)
        self.assertEqual(state["checked"], {})
        pc.run_cycle(self.TARGETS, sent.append, state, self.ok)
        self.assertEqual(len(sent), 4)

    def test_periodic_save_during_a_pass(self):
        saves = []
        targets = [("Popular", i, "EA SPORTS FC 27", "https://www.allkeyshop.com/blog/p%d/" % i) for i in range(1, 2 * pc.SAVE_EVERY + 2)]
        with mock.patch.object(pc, "NOTIFY_OK", False), mock.patch.object(pc, "PAGE_DELAY", 0):
            pc.run_cycle(targets, lambda m: None, pc.load_state("/nonexistent"), self.ok, save=lambda: saves.append(1))
        self.assertEqual(len(saves), 2)
        # audit du 02/10/2026 : une page qui a envoyé une alerte est sauvegardée aussitôt (un redémarrage ne la renvoie pas)
        suspect = lambda product, o: {"verdict": "SUSPECT", "reasons": ["x"], "notes": [], "url": "https://x/y", "method": "URL"}
        saves.clear()
        pc.run_cycle(targets[:1], lambda m: None, pc.load_state("/nonexistent"), suspect, save=lambda: saves.append(1))
        self.assertEqual(len(saves), 1)

    def test_discord_pause_queues_alerts_then_flushes(self):
        state = pc.load_state("/nonexistent")
        with mock.patch.object(pc, "MUTE_UNTIL", "2999-01-01 00:00"), mock.patch.object(pc, "send_discord") as send:
            pc.make_notifier("https://hook", state)("alerte 1")
            pc.make_notifier("https://hook", state)("alerte 2")
        send.assert_not_called()
        self.assertEqual(state["queued"], ["alerte 1", "alerte 2"])
        sent = []
        with mock.patch.object(pc.time, "sleep"):
            pc.flush_queue(state, lambda msg, channel: sent.append((channel, msg)))
        self.assertEqual((sent, state["queued"]), ([("", "alerte 1"), ("", "alerte 2")], []))
        # une alerte en attente garde le salon de son mode
        with mock.patch.object(pc, "MUTE_UNTIL", "2999-01-01 00:00"):
            pc.make_notifier("https://hook-home", state, "homepage")("alerte home")
        self.assertEqual(state["queued"], [["homepage", "alerte home"]])
        with mock.patch.object(pc.time, "sleep"):
            pc.flush_queue(state, lambda msg, channel: sent.append((channel, msg)))
        self.assertEqual(sent[-1], ("homepage", "alerte home"))
        with mock.patch.object(pc, "MUTE_UNTIL", "2000-01-01 00:00"), mock.patch.object(pc, "send_discord") as send:
            pc.make_notifier("https://hook", state)("alerte 3")
        send.assert_called_once_with("https://hook", "alerte 3")
        self.assertEqual(state["queued"], [])

    def test_state_keeps_page_list_and_rank(self):
        state = pc.load_state("/nonexistent")
        pc.run_cycle(self.TARGETS, lambda m: None, state, self.ok)
        e = next(iter(state["checked"].values()))
        self.assertEqual((e["page"], e["list"], e["rank"]), (self.TARGETS[0][3], "Popular", 1))

    def test_reports_export_and_decisions(self):
        import tempfile
        state = pc.load_state("/nonexistent")
        state["checked"] = {
            "1": {"verdict": "SUSPECT", "product": "TORO 2 Nintendo Switch", "edition": "Standard", "merchant": "Nintendo eShop FR",
                  "price": 5.99, "reasons": ["another product at the merchant: « Metal Garden » instead of « TORO 2 Nintendo Switch » (URL)"],
                  "url": "https://www.nintendo.com/fr-fr/Metal-Garden-3177422.html", "at": "2026-09-30 16:15"},
            "2": {"verdict": "OK", "product": "Valheim", "edition": "Standard", "at": "2026-09-30 14:00"},
            "3": {"verdict": "NON VÉRIFIABLE", "product": "Mario Kart 8 Deluxe Nintendo Switch", "at": "2026-09-30 16:13"},
        }
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "decisions.jsonl"), "w") as f:
                f.write('{"offer": "1", "decision": "vrai", "note": "Metal Garden", "by": "romain", "at": "2026-10-01T10:00"}\n')
                f.write("pas du json\n")
                f.write('{"offer": "2", "decision": "inconnue"}\n')
            self.assertEqual(pc.apply_decisions(state, d), ["1"])
            self.assertEqual(pc.apply_decisions(state, d), [])  # déjà appliquée
            pages = {"TORO 2 Nintendo Switch": ("TOP 50 · Nintendo Popular", 56, "https://www.allkeyshop.com/blog/buy-toro-2-nintendo-switch-compare-prices/")}
            self.assertEqual(pc.export_reports(state, d, pages), 2)  # SUSPECT et NON VÉRIFIABLE, pas l'OK sans décision
            data = json.load(open(os.path.join(d, "reports.json")))
        first = data["reports"][0]
        self.assertEqual((first["offer"], first["page_url"], first["list"], first["decision"]["decision"]),
                         ("1", "https://www.allkeyshop.com/blog/buy-toro-2-nintendo-switch-compare-prices/", "TOP 50 · Nintendo Popular", "vrai"))
        self.assertIn("faux", data["decisions"])

    def test_state_roundtrip_and_prune(self):
        state = pc.load_state("/nonexistent")
        pc.run_cycle(self.TARGETS, lambda m: None, state, self.ok)
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            pc.save_state(path, state)
            loaded = pc.load_state(path)
        self.assertEqual(loaded["checked"].keys(), state["checked"].keys())
        pc.prune_state(loaded, pc.time.time() + (pc.STATE_TTL_DAYS + 1) * 86400)
        self.assertEqual(loaded["checked"], {})


if __name__ == "__main__":
    unittest.main()
