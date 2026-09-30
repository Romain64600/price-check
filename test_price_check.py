"""Tests hors ligne, sur les fichiers de samples/ : python3 -m unittest -v"""

import copy
import json
import os
import re
import unittest
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
        self.assertEqual(popular, ["EA SPORTS FC 27", "The Witcher 3 Wild Hunt", "CONTROL Resonant",
                                   "WARDOGS", "Valheim"])
        self.assertEqual(soon, ["Dynasty Warriors 3 Complete Edition Remastered", "Ace Combat 8",
                                "AION 2", "STAR WARS Galactic Racer"])

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
        self.assertEqual(len(targets), 9)

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

    def test_epic_url_has_no_name(self):
        row = next(r for r in self.rows if r["merchant"] == "Epic Games")
        res = self.analyze(row)
        self.assertIsNone(res["match"])
        self.assertEqual(res["reasons"], ["nom du produit absent (URL)"])

    def test_ea_com_url_is_partial(self):
        row = next(r for r in self.rows if r["merchant"] == "EA.com")
        res = self.analyze(row)
        self.assertEqual((res["match"], res["reasons"], res["notes"]), ("partial", [], ["nom partiel"]))


class TestAnalyzeSuspects(unittest.TestCase):
    def reasons(self, product, url, **kw):
        return pc.analyze(product, offer(**kw), pc.url_text(url), "URL")["reasons"]

    def test_wrong_product(self):
        r = self.reasons("Sonic Racing CrossWorlds", "https://www.kinguin.net/category/1/sonic-the-hedgehog-pc-steam")
        self.assertEqual(r, ["nom du produit absent (URL)"])

    def test_account_sold_as_key(self):
        r = self.reasons("EA SPORTS FC 27", "https://shop.example/ea-sports-fc-27-pc-steam-account-global")
        self.assertIn("compte chez le marchand, saisi en clé", r)
        self.assertEqual(self.reasons("EA SPORTS FC 27", "https://shop.example/ea-sports-fc-27-account", account=True), [])

    def test_forbidden_region(self):
        r = self.reasons("EA SPORTS FC 27", "https://shop.example/ea-sports-fc-27-pc-steam-key-ru-cis")
        self.assertEqual(r, ["région interdite : ru, cis"])

    def test_region_family_mismatch(self):
        self.assertEqual(self.reasons("EA SPORTS FC 27", "https://shop.example/ea-sports-fc-27-steam-key-europe", region="GLOBAL"),
                         ["région : AllKeyShop GLOBAL, marchand EU"])
        self.assertEqual(self.reasons("EA SPORTS FC 27", "https://shop.example/ea-sports-fc-27-steam-key-global", region="EUROPE"),
                         ["région : AllKeyShop EUROPE, marchand GLOBAL"])
        self.assertEqual(self.reasons("EA SPORTS FC 27", "https://shop.example/ea-sports-fc-27-steam-key-europe", region="GIFT EU"), [])

    def test_gift_sold_as_key(self):
        self.assertEqual(self.reasons("EA SPORTS FC 27", "https://shop.example/ea-sports-fc-27-steam-altergift", region="GLOBAL"),
                         ["gift chez le marchand, affiché en clé GLOBAL"])
        self.assertEqual(self.reasons("EA SPORTS FC 27", "https://shop.example/ea-sports-fc-27-steam-gift-global", region="GIFT"), [])

    def test_platform_mismatch(self):
        self.assertEqual(self.reasons("EA SPORTS FC 27", "https://shop.example/ea-sports-fc-27-pc-steam-key", platform="ea-app"),
                         ["plateforme : AllKeyShop ea-app, marchand steam"])
        self.assertEqual(self.reasons("EA SPORTS FC 27", "https://shop.example/ea-sports-fc-27-origin-key", platform="ea-app"), [])
        self.assertEqual(self.reasons("EA SPORTS FC 27", "https://shop.example/ea-sports-fc-27-xbox-pc-key", platform="xbox-play-anywhere"), [])
        self.assertEqual(self.reasons("EA SPORTS FC 27", "https://shop.example/ea-sports-fc-27-pc-key", platform="mystery-platform"), [])

    def test_edition_mismatch(self):
        self.assertEqual(self.reasons("EA SPORTS FC 27", "https://shop.example/ea-sports-fc-27-deluxe-edition-pc-steam", edition="Standard"),
                         ["édition : AllKeyShop Standard, marchand deluxe"])
        self.assertEqual(self.reasons("EA SPORTS FC 27", "https://shop.example/ea-sports-fc-27-standard-edition", edition="Ultimate"),
                         ["édition : AllKeyShop Ultimate, marchand standard"])
        self.assertEqual(self.reasons("EA SPORTS FC 27", "https://shop.example/ea-sports-fc-27-standard-edition", edition="Standard + Bonus"), [])
        self.assertEqual(self.reasons("EA SPORTS FC 27", "https://shop.example/ea-sports-fc-27-goty", edition="Game of the Year"), [])
        self.assertEqual(self.reasons("EA SPORTS FC 27", "https://shop.example/ea-sports-fc-27-digital-deluxe", edition="Deluxe"), [])

    def test_edition_words_of_the_product_name_are_ignored(self):
        product = "Dynasty Warriors 3 Complete Edition Remastered"
        self.assertEqual(self.reasons(product, "https://shop.example/dynasty-warriors-3-complete-edition-remastered-steam-key"), [])

    def test_dlc(self):
        self.assertEqual(self.reasons("Valheim", "https://shop.example/valheim-season-pass-dlc"),
                         ["contenu additionnel : dlc, season-pass"])

    def test_preorder_bonus_dlc_sold_with_the_game(self):
        # Driffle, 30/09/2026, édition AllKeyShop « Standard + Bonus »
        url = "https://www.driffle.com/ea-sports-fc-27-pre-order-bonus-dlc-global-pc-ea-play-digital-key-p10001977"
        self.assertEqual(self.reasons("EA SPORTS FC 27", url, edition="Standard + Bonus", platform="ea-app"), [])

    def test_goty_both_ways(self):
        # Instant Gaming, 30/09/2026, édition AllKeyShop « GOTY »
        url = "https://www.instant-gaming.com/en/1497-buy-key-gogcom-the-witcher-3-wild-hunt-goty/"
        self.assertEqual(self.reasons("The Witcher 3 Wild Hunt", url, edition="GOTY", platform="gog"), [])
        self.assertEqual(self.reasons("The Witcher 3 Wild Hunt", url, edition="Standard", platform="gog"),
                         ["édition : AllKeyShop Standard, marchand goty"])

    def test_bundle_edition_has_another_name(self):
        # G2A, 30/09/2026, édition AllKeyShop « Bundle » : The Witcher Trilogy Pack
        url = "https://www.g2a.com/en/the-witcher-trilogy-pack-steam-gift-global-i10000000746004"
        res = pc.analyze("The Witcher 3 Wild Hunt", offer(edition="Bundle", region="GIFT"), pc.url_text(url), "URL")
        self.assertEqual((res["match"], res["reasons"], res["notes"]), (None, [], ["édition Bundle : nom non contrôlé"]))
        res = pc.analyze("The Witcher 3 Wild Hunt", offer(edition="Bundle", region="GLOBAL"), pc.url_text(url), "URL")
        self.assertEqual(res["reasons"], ["gift chez le marchand, affiché en clé GLOBAL"])

    def test_language_restriction_is_fine(self):
        self.assertEqual(self.reasons("EA SPORTS FC 27", "https://www.mmoga.com/EA-Games/EA-SPORTS-FC-27-EA-App-English-Only.html",
                                      region="IN ENGLISH ONLY", platform="ea-app"), [])

    def test_roman_numerals_both_ways(self):
        # GameBoost et YUPLAY, 30/09/2026 : « Minecraft Dungeons 2 » écrit « II »
        for url in ("https://gameboost.com/minecraft-dungeons-ii-xbox-series-x-pc-xbox-live-key-global-00-71537?cmpid=206",
                    "https://www.yuplay.com/product/minecraft-dungeons-ii-xbox-series-xs-and-xbox-on-pc/?partner=2334725"):
            self.assertEqual(self.reasons("Minecraft Dungeons 2", url, region="XBOX/PC", platform="xbox-play-anywhere"), [])
        self.assertEqual(self.reasons("Final Fantasy VII Rebirth", "https://shop.example/final-fantasy-7-rebirth-pc-steam"), [])
        self.assertEqual(pc.name_variants("Ace Combat 8"), ("Ace Combat 8", "ace combat viii"))

    def test_platform_suffix_of_the_product_name(self):
        # PS Store US, 30/09/2026 : « GTA 6 PS5 », URL EP1004-PPSA01547_00-GTAVIULTIMATE001, titre sans « PS5 »
        url = "https://store.playstation.com/en-us/product/EP1004-PPSA01547_00-GTAVIULTIMATE001?partner=allkeyshopcom"
        self.assertEqual(self.reasons("GTA 6 PS5", url, edition="Ultimate", region="PS5", platform="playstation-store"), [])
        res = pc.analyze("GTA 6 PS5", offer(edition="Ultimate", region="PS5", platform="playstation-store"),
                         "Grand Theft Auto VI Ultimate Edition | PlayStation Store", "titre de la page")
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
        self.assertEqual(self.reasons("WARDOGS", "https://shop.example/wardogs-deluxe-edition-pc-steam", edition="Standard"),
                         ["édition : AllKeyShop Standard, marchand deluxe"])

    def test_edition_announcing_dlc(self):
        # Kinguin, 30/09/2026 : édition AllKeyShop « Standard + DLC Bundle »
        url = "https://www.kinguin.net/en/category/553797/hunt-showdown-1896-10-dlc-bundle-pc-steam-cd-key?r=3445"
        self.assertEqual(self.reasons("Hunt Showdown", url, edition="Standard + DLC Bundle"), [])
        self.assertEqual(self.reasons("Hunt Showdown", url, edition="Standard"), ["contenu additionnel : dlc"])

    def test_language_lists_are_not_regions(self):
        # GAMIVO, 30/09/2026 : les langues de la clé dans l'URL, dont « ru » et « tr »
        url = "https://www.gamivo.com/product/rimworld-stareter-pack-pc-steam-global-en-de-fr-it-pl-cs-nl-ja-ko-no-pt-ru-zh-es-sv-tr-zh-hu-da-ro-fi-uk-standard"
        self.assertEqual(self.reasons("RimWorld", url, edition="Starter Pack"), [])
        url = "https://www.gamivo.com/product/age-of-wonders-4-pc-steam-global-en-de-fr-pl-ja-ko-ru-zh-es-standard"
        self.assertEqual(self.reasons("Age of Wonders 4", url), [])
        self.assertEqual(self.reasons("Age of Wonders 4", "https://shop.example/age-of-wonders-4-steam-key-ru-cis"),
                         ["région interdite : ru, cis"])
        self.assertEqual(self.reasons("Age of Wonders 4", "https://shop.example/age-of-wonders-4-pc-eu-key", region="GLOBAL"),
                         ["région : AllKeyShop GLOBAL, marchand EU"])

    def test_soundtrack_edition(self):
        # Steam, 30/09/2026 : Stray « Soundtrack Edition »
        res = pc.analyze("Stray", offer(edition="Soundtrack Edition"), "Stray + Soundtrack Bundle on Steam", "titre de la page")
        self.assertEqual(res["reasons"], [])
        self.assertEqual(self.reasons("Stray", "https://shop.example/stray-soundtrack-dlc", edition="Standard"),
                         ["contenu additionnel : dlc, soundtrack"])

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
                         ["nom du produit absent (URL)"])
        self.assertEqual(self.reasons("Red Dead Redemption 2", "https://shop.example/red-dead-redemption-pc-rockstar-key"),
                         ["nom du produit absent (URL)"])

    def test_wrong_product_dredge_doom(self):
        # Greenmangaming, 30/09/2026 : DOOM The Dark Ages en premier prix « Premium » de la page DREDGE
        self.assertEqual(self.reasons("DREDGE", "https://www.greenmangaming.com/games/doom-the-dark-ages-premium-edition-pc/",
                                      edition="Premium"), ["nom du produit absent (URL)"])

    def test_alias(self):
        self.assertEqual(self.reasons("GTA 6", "https://shop.example/grand-theft-auto-vi-ps5", platform="playstation"), [])
        # Wyrel, 30/09/2026 : « GTA 6 PS5 » écrit « grand-theft-auto-vi-ps5 »
        url = "https://wyrel.com/en/buy-cheap-grand-theft-auto-vi-ps5-193995?referal=allkeyshop&marketplace_id=5"
        self.assertEqual(self.reasons("GTA 6 PS5", url, region="EUROPE", platform="playstation-store"), [])
        self.assertEqual(self.reasons("Grand Theft Auto V", "https://shop.example/gta-5-pc-rockstar-key", platform="rockstar"), [])
        self.assertEqual(self.reasons("Call of Duty Black Ops 7", "https://shop.example/cod-black-ops-7-pc-steam"), [])
        self.assertIn("grand theft auto vi ps5", pc.name_variants("GTA 6 PS5"))

    def test_partial_name(self):
        res = pc.analyze("The Witcher 3 Wild Hunt", offer(), "witcher-3-wild-hunt-goty-steam-key", "URL")
        self.assertEqual((res["match"], res["notes"]), ("partial", ["nom partiel"]))
        self.assertEqual(res["reasons"], ["édition : AllKeyShop Standard, marchand goty"])


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
        get.assert_called_once_with(pc.REDIRECTION_URL % (1, 47), pc.AKS_UA)

    def test_merchant_redirect_fallback(self):
        page = self.interstitial("https://www.instant-gaming.com/en/21656-/?igr=289098")
        full = "https://www.instant-gaming.com/en/21656-buy-ea-sports-fc-27-pc-ea-app/?igr=289098"

        def fake_get(url, ua, follow=True, timeout=30):
            if "allkeyshop.com" in url:
                return 200, None, page
            self.assertEqual((ua, follow), (pc.BROWSER_UA, False))
            return 301, full, ""

        with mock.patch.object(pc, "http_get", side_effect=fake_get):
            res = pc.check_offer("EA SPORTS FC 27", offer(platform="ea-app"))
        self.assertEqual((res["verdict"], res["method"], res["url"]), ("OK", "URL après 301 marchand", full))

    def test_page_fallback(self):
        page = self.interstitial("https://store.epicgames.com/p/fc-27-e149fb")
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, page), (200, None, "")]), \
             mock.patch.object(pc, "page_title", return_value="EA SPORTS FC 27 | Download and Buy Today - Epic Games Store"):
            res = pc.check_offer("EA SPORTS FC 27", offer(platform="epic-store"))
        self.assertEqual((res["verdict"], res["method"]), ("OK", "page (Chromium)"))

    def test_page_fallback_wrong_product(self):
        page = self.interstitial("https://store.epicgames.com/p/abc-123")
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, page), (200, None, "")]), \
             mock.patch.object(pc, "page_title", return_value="Sonic the Hedgehog - Epic Games Store"):
            res = pc.check_offer("Sonic Racing CrossWorlds", offer(platform="epic-store"))
        self.assertEqual((res["verdict"], res["reasons"]), ("SUSPECT", ["nom du produit absent (titre de la page)"]))

    def test_unreadable_page(self):
        page = self.interstitial("https://store.epicgames.com/p/abc-123")
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, page), (403, None, "")]), \
             mock.patch.object(pc, "page_title", return_value=None):
            res = pc.check_offer("EA SPORTS FC 27", offer())
        self.assertEqual((res["verdict"], res["method"]), ("À VÉRIFIER", "aucune"))

    def test_redirection_failure_is_retryable(self):
        with mock.patch.object(pc, "http_get", return_value=(503, None, "")) as get:
            with self.assertRaises(pc.CheckError):
                pc.check_offer("EA SPORTS FC 27", offer())
        self.assertEqual(get.call_count, 2)  # un second essai sur un 5xx

    def test_transient_503_then_ok(self):
        with mock.patch.object(pc, "http_get", side_effect=[(503, None, ""), (200, None, self.INTERSTITIAL)]):
            res = pc.check_offer("EA SPORTS FC 27", offer(region="GIFT"))
        self.assertEqual(res["verdict"], "OK")
        with mock.patch.object(pc, "http_get", return_value=(200, None, "<html>rien</html>")):
            with self.assertRaises(pc.CheckError):
                pc.check_offer("EA SPORTS FC 27", offer())


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
        self.assertIn("Mmoga · IN ENGLISH ONLY · ea-app · **54.99 €**", sent[0])
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
        self.assertIn("Raison : nom du produit absent (URL)", sent[0])
        self.assertIn("Marchand : <https://shop.example/sonic>", sent[0])

    def test_check_failures_then_manual(self):
        def failing(product, o):
            raise pc.CheckError("redirection AllKeyShop HTTP 503")

        sent, state = [], pc.load_state("/nonexistent")
        for _ in range(pc.MAX_CHECK_FAILURES - 1):
            pc.run_cycle(self.TARGETS, sent.append, state, failing)
        self.assertEqual((sent, state["checked"]), ([], {}))
        pc.run_cycle(self.TARGETS, sent.append, state, failing)
        self.assertEqual(len(sent), 4)
        self.assertIn("🟠 **À VÉRIFIER**", sent[0])
        self.assertIn("contrôle impossible : redirection AllKeyShop HTTP 503", sent[0])

    def test_discord_failure_retries_next_cycle(self):
        def flaky(msg):
            raise OSError("discord down")

        sent, state = [], pc.load_state("/nonexistent")
        pc.run_cycle(self.TARGETS, flaky, state, self.ok)
        self.assertEqual(state["checked"], {})
        pc.run_cycle(self.TARGETS, sent.append, state, self.ok)
        self.assertEqual(len(sent), 4)

    def test_state_roundtrip_and_prune(self):
        state = pc.load_state("/nonexistent")
        pc.run_cycle(self.TARGETS, lambda m: None, state, self.ok)
        path = os.path.join(SAMPLES, "_state_test.json")
        try:
            pc.save_state(path, state)
            loaded = pc.load_state(path)
        finally:
            os.remove(path)
        self.assertEqual(loaded["checked"].keys(), state["checked"].keys())
        pc.prune_state(loaded, pc.time.time() + (pc.STATE_TTL_DAYS + 1) * 86400)
        self.assertEqual(loaded["checked"], {})


if __name__ == "__main__":
    unittest.main()
