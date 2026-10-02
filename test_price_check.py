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
        self.assertEqual(r, ["autre produit chez le marchand : « Sonic The Hedgehog » au lieu de « Sonic Racing CrossWorlds » (URL)"])

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
        # l'inverse est sans danger : une clé GLOBAL affichée EUROPE marche en Europe
        self.assertEqual(self.reasons("EA SPORTS FC 27", "https://shop.example/ea-sports-fc-27-steam-key-global", region="EUROPE"), [])
        self.assertEqual(self.reasons("EA SPORTS FC 27", "https://shop.example/ea-sports-fc-27-steam-key-europe", region="GIFT EU"), [])

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

                         ["région : AllKeyShop GLOBAL, marchand EU", "gift chez le marchand, affiché en clé GLOBAL"])


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
        # édition supérieure vendue sous Standard alors que la page a une édition Deluxe : erreur (arbitrage du 01/10/2026)
        self.assertEqual(self.reasons("EA SPORTS FC 27", "https://shop.example/ea-sports-fc-27-deluxe-edition-pc-steam", edition="Standard",
                                      page_editions=["Standard", "Deluxe"]),
                         ["édition : rangée en Standard, le marchand vend deluxe (la page a une édition Deluxe)"])
        # pas d'édition Deluxe sur la page : l'acheteur a plus que ce qui est affiché, pas d'alerte
        self.assertEqual(self.reasons("EA SPORTS FC 27", "https://shop.example/ea-sports-fc-27-deluxe-edition-pc-steam", edition="Standard"), [])
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
        self.assertEqual(self.reasons("The Witcher 3 Wild Hunt", url, edition="Standard", platform="gog",
                                      page_editions=["Complete", "GOTY", "Standard", "Bundle"]),
                         ["édition : rangée en Standard, le marchand vend goty (la page a une édition GOTY)"])

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
        self.assertEqual(self.reasons("WARDOGS", "https://shop.example/wardogs-deluxe-edition-pc-steam", edition="Standard",
                                      page_editions=["Standard", "Deluxe"]),
                         ["édition : rangée en Standard, le marchand vend deluxe (la page a une édition Deluxe)"])
        # une édition supérieure affichée, l'édition de base vendue : alerte aussi
        self.assertEqual(self.reasons("WARDOGS", "https://shop.example/wardogs-standard-edition-pc-steam", edition="Deluxe"),
                         ["édition : AllKeyShop Deluxe, marchand standard"])

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
                         ["autre produit chez le marchand : « Call Of Duty Modern Warfare 3 » au lieu de « Call of Duty Modern Warfare 4 » (URL)"])
        self.assertEqual(self.reasons("Red Dead Redemption 2", "https://shop.example/red-dead-redemption-pc-rockstar-key",
                                      platform="rockstar"), ["autre produit chez le marchand : « Red Dead Redemption » au lieu de « Red Dead Redemption 2 » (URL)"])

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
                                      edition="Ultimate", platform="ea-app"), ["plateforme : AllKeyShop ea-app, marchand steam"])

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
        res = pc.analyze("EA Sports UFC 5 PS5", o, "UFC® 5 | Access Denied", "titre de la page")
        self.assertEqual((res["match"], res["reasons"]), ("exact", []))  # « UFC 5 » sans « EA Sports », depuis le 01/10/2026
        res = pc.analyze("Ace Combat 8 Wings of Theve", o, "Wings of Theve | Bandai Namco", "titre de la page")
        self.assertEqual((res["match"], res["reasons"]), ("partial", []))  # titre court contenu dans le nom
        res = pc.analyze("Pokemon Sword Nintendo Switch", o, "Pokémon Shield | Nintendo", "titre de la page")
        self.assertIsNone(res["match"])
        res = pc.analyze("Sonic Racing CrossWorlds", o, "Sonic | SEGA", "titre de la page")
        self.assertIsNone(res["match"])  # un seul mot, pas assez

    def test_dlc_page(self):
        # Driffle, 30/09/2026 (formation) : la page AllKeyShop de Diablo 4 Lord of Hatred est un DLC
        url = "https://www.driffle.com/diablo-iv-lord-of-hatred-ultimate-edition-dlc-global-xbox-one-xbox-series-xs-xbox-live-digital-key-p9990076"
        o = offer(edition="Ultimate", region="XBOX X|S", platform="xbox", page_dlc=True)
        self.assertEqual(pc.analyze("Diablo 4 Lord of Hatred Xbox Series", o, pc.url_text(url), "URL")["reasons"], [])
        o["page_dlc"] = False
        self.assertEqual(pc.analyze("Diablo 4 Lord of Hatred Xbox Series", o, pc.url_text(url), "URL")["reasons"], ["contenu additionnel : dlc"])
        trans = {"editions": {"1": {"name": "Standard"}, "16": {"name": "DLC"}, "21": {"name": "Ultimate"}}}
        self.assertTrue(pc.is_dlc_page(trans, "Diablo 4 Lord of Hatred Xbox Series"))
        self.assertFalse(pc.is_dlc_page({"editions": {"1": {"name": "Standard"}}}, "Diablo 4"))
        self.assertTrue(pc.is_dlc_page({"editions": {}}, "Farming Simulator 25 Year 1 Season Pass"))

    def test_wrong_product_dredge_doom(self):
        # Greenmangaming, 30/09/2026 : DOOM The Dark Ages en premier prix « Premium » de la page DREDGE
        self.assertEqual(self.reasons("DREDGE", "https://www.greenmangaming.com/games/doom-the-dark-ages-premium-edition-pc/",
                                      edition="Premium"), ["autre produit chez le marchand : « Doom The Dark Ages Premium Edition » au lieu de « DREDGE » (URL)"])

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
        self.assertEqual((res["match"], res["notes"]), ("partial", ["nom partiel"]))
        self.assertEqual(res["reasons"], ["édition : rangée en Standard, le marchand vend goty (la page a une édition GOTY)"])


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
        self.assertEqual(res["reasons"], ["région : AllKeyShop GLOBAL, marchand EU"])
        url2 = "https://wyrel.com/en/buy-cheap-ea-sports-fc-27-pc-196673?referal=allkeyshop&marketplace_id=2&edition_id=780&region=4"
        res = pc.analyze("EA SPORTS FC 27", offer(merchantName="Wyrel", region="GIFT EU"), pc.url_text(url2), "URL",
                         region=pc.region_text(url2, cfg))
        self.assertEqual(res["reasons"], [])
        self.assertEqual(pc.region_text(url.replace("region=1", "region=9"), cfg), "")  # inconnue : pas de contrôle
        # region=5 = ROW : normal pour une offre affichée ROW, suspect pour une offre affichée GLOBAL
        url5 = "https://wyrel.com/en/buy-cheap-kingdom-come-deliverance-ii-pc-146507?referal=allkeyshop&region=5"
        for region, expected in (("ROW", []), ("GLOBAL", ["région : AllKeyShop GLOBAL, marchand ROW"])):
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
            self.assertEqual((res["verdict"], res["method"]), (expected, "URL de la version en-GB"), product)
            if expected == "SUSPECT":  # le message dit ce que vend le marchand (formation du 01/10/2026)
                self.assertEqual(res["reasons"], ["autre produit chez le marchand : « Metal Garden » au lieu de « TORO 2 Nintendo Switch » (URL de la version en-GB)"])

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
                         ["région : AllKeyShop GLOBAL, marchand EU"])
        self.assertEqual(self.reasons("The Blood Of Dawnwalker",
                                      "https://www.eneba.com/steam-the-blood-of-dawnwalker-eclipse-edition-deluxe-steam-key-pc-europe",
                                      edition="Deluxe", region="GLOBAL", region_filter="STEAM GLOBAL"),
                         ["région : AllKeyShop GLOBAL, marchand EU"])

    def test_wrong_platform(self):
        self.assertEqual(self.reasons("F1 25", "https://www.gamivo.com/product/f1-25-xbox-xbox-series-eu-2026-season",
                                      edition="2026 Season Edition", region="EU ENGLISH ONLY", region_filter="STEAM EU EN ONLY"),
                         ["plateforme : AllKeyShop steam, marchand xbox"])
        self.assertEqual(self.reasons("EA SPORTS FC 26", "https://www.driffle.com/ea-sports-fc-26-icons-edition-global-pc-steam-digital-key-p9990128",
                                      edition="ICONS Edition", region="GLOBAL", region_filter="EA GLOBAL", platform="ea-app"),
                         ["plateforme : AllKeyShop ea-app, marchand steam"])

    def test_platform_from_region_filter(self):
        # activationPlatform vide, mais la région dit EA GLOBAL
        self.assertEqual(self.reasons("EA SPORTS FC 26", "https://shop.example/ea-sports-fc-26-pc-steam-key",
                                      region="GLOBAL", region_filter="EA GLOBAL", platform=""),
                         ["plateforme : AllKeyShop ea, marchand steam".replace("ea,", "ea-app,")])

    def test_console_of_the_page(self):
        # Amazon.fr, Elden Ring Xbox Series : l'URL parle de PlayStation
        url = "https://www.amazon.fr/Bandai-Namco-Entertainment-3391892017632-PlayStation/dp/B0977LKSQ6/"
        res = pc.analyze("Elden Ring Xbox Series", offer(edition="Launch Edition", region="BOX", region_filter="BOX", platform="physical-medium"),
                         pc.url_text(url), "URL")
        self.assertIn("plateforme : page AllKeyShop Xbox, marchand PlayStation", res["reasons"])
        # la bonne console, ou une URL qui n'en parle pas : rien
        self.assertEqual(self.reasons("GTA The Trilogy The Definitive Edition Xbox Series", "https://www.amazon.fr/GTA-Trilogy-Definition-Xbox-X/dp/B09KGZ37M1/",
                                      region="BOX", platform="physical-medium"), [])

    def test_year_one_season_pass_is_the_year_one_edition(self):
        # Loaded, « Year 1 Season Pass » rangé dans « Year 1 Edition » : signalé le 30/09, faux positif selon
        # l'arbitrage du 01/10/2026 (« le jeu est bien inclus, l'offre est bien rentrée »)
        fs25 = ["Standard", "Highlands Fishing Edition", "Year 1 Bundle", "Year 1 Edition"]
        url = "https://www.loaded.com/farming-simulator-25-year-1-season-pass-pc-steam"
        self.assertEqual(self.reasons("Farming Simulator 25", url, edition="Year 1 Edition", page_editions=fs25), [])
        # le même pass rangé ailleurs, ou le pass d'une autre année, reste du contenu additionnel
        self.assertEqual(self.reasons("Farming Simulator 25", url, edition="Standard", page_editions=fs25),
                         ["contenu additionnel : season-pass"])
        self.assertEqual(self.reasons("Farming Simulator 25", "https://www.loaded.com/farming-simulator-25-year-2-season-pass-pc-steam",
                                      edition="Year 1 Edition", page_editions=fs25),
                         ["contenu additionnel : season-pass"])

    def test_edition_misfiled_while_the_page_has_it(self):
        # arbitrage du 01/10/2026 : mauvaise édition = erreur, même si l'acheteur reçoit plus (annule la formation
        # du matin, qui tenait GTA 4 pour un faux positif)
        gta4 = ["Complete", "Standard", "Collection", "Complete Bundle", "Complete Pack", "Bundle"]
        res = pc.analyze("GTA 4", offer(edition="Standard", page_editions=gta4), "Grand Theft Auto IV: The Complete Edition on Steam", "titre de la page")
        self.assertEqual(res["reasons"], ["édition : rangée en Standard, le marchand vend complete (la page a une édition Complete)"])
        zero = ["Standard", "Deluxe", "Deluxe + Bonus", "Bonus", "Standard + DLC"]
        self.assertEqual(self.reasons("STAR WARS Zero Company Xbox Series",
                                      "https://www.gamivo.com/product/star-wars-zero-company-xbox-xbox-series-global-deluxe-pre-order-bonus",
                                      edition="Standard + DLC", region="XBOX X|S", region_filter="XBOX X|S GLOBAL", platform="xbox", page_editions=zero),
                         ["édition : rangée en Standard + DLC, le marchand vend deluxe (la page a une édition Deluxe)"])
        # la page n'a pas l'édition vendue : rien de mieux où ranger l'offre, pas d'alerte
        alone = pc.analyze("GTA 4", offer(edition="Standard", page_editions=["Standard"]),
                           "Grand Theft Auto IV: The Complete Edition on Steam", "titre de la page")
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
            res = pc.analyze(product, o, title, "titre de la page")
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
                         ["autre produit chez le marchand : « Mortal Kombat 2 » au lieu de « Portal 2 » (URL)"])
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
                         ["plateforme : AllKeyShop steam, marchand nintendo"])

    def test_full_replay_new_catches(self):
        # et trois vraies erreurs que les anciennes règles ne voyaient pas
        self.assertEqual(self.reasons("Call of Duty Black Ops 6", "https://www.eneba.com/steam-call-of-duty-r-black-ops-6-pc-steam-key-europe",
                                      region="EUROPE", region_filter="WINDOWS EU", platform="microsoft-windows"),
                         ["plateforme : AllKeyShop microsoft-windows, marchand steam"])
        self.assertEqual(self.reasons("The Witcher 3 Wild Hunt Xbox Series", "https://www.lootbar.com/game-key/the-witcher-3-wild-hunt-xbox",
                                      region="ROW", region_filter="STEAM ROW", platform="steam"),
                         ["plateforme : AllKeyShop steam, marchand xbox"])
        # Splatoon Raiders : offre saisie Xbox (plateforme et région) sur la page Nintendo Switch 2
        self.assertEqual(self.reasons("Splatoon Raiders Nintendo Switch 2", "https://www.gamingdragons.com/en/game/buy-splatoon-raiders-switch-2-code.html",
                                      region="EU XBOX X|S", region_filter="XBOX X|S EUROPE", platform="xbox"),
                         ["plateforme : AllKeyShop xbox, marchand nintendo"])

    def test_distinctive_word_never_missing(self):
        # LDShop : page du jeu de base pour l'offre de l'upgrade (cas à trancher, doit rester signalé)
        res = pc.analyze("Forza Horizon 6 Premium Upgrade Bundle Xbox Series", offer(edition="Upgrade", region="XBOX/PC", platform="xbox-play-anywhere"),
                         "Forza Horizon 6 CD-Key for Xbox & PC – Safe & Fast | Forza Horizon 6 Global Key (Xbox/PC)", "titre de la page")
        self.assertEqual(res["reasons"], ["autre produit chez le marchand : « Forza Horizon 6 CD-Key for Xbox & PC » au lieu de « Forza Horizon 6 Premium Upgrade Bundle Xbox Series » (titre de la page)"])

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
            self.assertEqual(pc.analyze(product, o, title, "titre de la page")["reasons"], [], product)
        # mais Épée n'est pas Bouclier
        self.assertEqual(pc.analyze("Pokemon Sword Nintendo Switch", o, "Pokémon Bouclier (Nintendo Switch)", "titre de la page")["reasons"],
                         ["autre produit chez le marchand : « Pokémon Bouclier (Nintendo Switch) » au lieu de « Pokemon Sword Nintendo Switch » (titre de la page)"])

    def test_names_merchants_shorten_20261001(self):
        # premier jour de Top Offers (étude) : « UFC 5 » sans le préfixe d'éditeur, chez Eneba (URL « ufc-r-5 », ® écrit r)
        # et GAMIVO ; « Onimusha: WotS », sigle des derniers mots, au PS Store (2 éditions, boutiques UK et FR)
        ufc = offer(edition="Standard", region="EU XBOX X|S", region_filter="XBOX X|S EUROPE", platform="xbox")
        for text, source in (("xbox-ufc-r-5-xbox-series-x-s-xbox-live-key-europe", "URL"),
                             ("Buy UFC® 5 Xbox key! Cheap price | Eneba", "titre de la page"),
                             ("Buy UFC 5 Xbox Series Key Europe", "titre de la page"),
                             (pc.url_text("https://www.gamivo.com/product/ufc-5-xbox-xboxseries-eu-en-standard"), "URL")):
            with self.subTest(text=text):
                res = pc.analyze("EA Sports UFC 5 Xbox Series", ufc, text, source)
                self.assertNotIn("name", res["kinds"], res["reasons"])
        oni = offer(edition="Deluxe", region="PS5", platform="playstation-store")
        self.assertEqual(pc.analyze("Onimusha Way of the Sword PS5", oni, "Onimusha: WotS | Deluxe Edition", "titre de la page")["reasons"], [])
        # un autre jeu de la série, ou un autre UFC, reste un autre produit
        self.assertEqual(pc.analyze("Onimusha Way of the Sword PS5", oni, "Onimusha 2: Samurai's Destiny", "titre de la page")["kinds"], ["name"])
        self.assertEqual(pc.analyze("EA Sports UFC 5 Xbox Series", ufc, "Buy UFC 4 Xbox key! Cheap price", "titre de la page")["kinds"], ["name"])
        self.assertIn("ufc 5 xbox series", pc.name_variants("EA Sports UFC 5 Xbox Series"))
        self.assertIn("onimusha wots", pc.name_variants("Onimusha Way of the Sword PS5"))

    def test_booster_courses_pack_alias(self):
        # K4G, 01/10/2026 (étude, 3e prix de l'édition DLC) : « Booster Courses Pack » = le Booster Course Pass
        pc._PRODUCT_ALIASES = None  # relit aliases.toml
        o = offer(edition="DLC", region="EUROPE", platform="nintendo-eshop", page_dlc=True)
        url = "https://k4g.com/product/mario-kart-8-deluxe-booster-courses-pack-nintendo-switch-europe-cd-key-D492FAEB"
        self.assertEqual(pc.analyze("Mario Kart 8 Deluxe Booster Course Pass Nintendo Switch", o, pc.url_text(url), "URL")["reasons"], [])
        self.assertEqual(pc.analyze("Mario Kart 8 Deluxe Booster Course Pass Nintendo Switch", o,
                                    "Buy Mario Kart 8 Deluxe - Booster Courses Pack - cheap | K4G.com", "titre de la page")["reasons"], [])
        # le jeu de base vendu sur la page du DLC reste un autre produit
        self.assertEqual(pc.analyze("Mario Kart 8 Deluxe Booster Course Pass Nintendo Switch", o,
                                    "Buy Mario Kart 8 Deluxe Nintendo Switch Europe - cheap | K4G.com", "titre de la page")["kinds"], ["name"])

    def test_multi_product_page_selected_option(self):
        # LDShop, 01/10/2026 (formation « à discuter ») : la page Forza Horizon 6 a l'option « Premium Upgrade » cochée
        dom = sample("ldshop_forza-horizon-6_sku16560.html")
        self.assertEqual(pc.selected_option_text(dom), "Forza Horizon 6 Premium Upgrade (Global)")
        # une option de prix cochée avant le produit (page réelle) ne doit pas être prise pour le produit
        priced = dom.replace("<section>", '<section><li role="radio" aria-checked="true">€ 40,97 € 49,99 Direct purchase</li>')
        self.assertEqual(pc.selected_option_text(priced), "Forza Horizon 6 Premium Upgrade (Global)")
        text = pc.page_text_from_dom(dom, "https://www.ldshop.gg/card/forza-horizon-6.html?skuId=16560", "selected-option")
        res = pc.analyze("Forza Horizon 6 Premium Upgrade Bundle Xbox Series", offer(edition="Upgrade", region="XBOX/PC", platform="xbox-play-anywhere"),
                         text, "titre de la page")
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
        self.assertTrue(any(n.startswith("URL contredite par la page") for n in res["notes"]))

    def test_url_confirmed_by_the_page(self):
        res = self.run_check("F1 25", offer(edition="2026 Season Edition", region="EU ENGLISH ONLY", region_filter="STEAM EU EN ONLY"),
                             "https://www.gamivo.com/product/f1-25-xbox-xbox-series-eu-2026-season",
                             "<title>Buy F1 25 2026 Season Edition Xbox Series Key Europe | GAMIVO</title>")
        self.assertEqual((res["verdict"], res["reasons"]), ("SUSPECT", ["plateforme : AllKeyShop steam, marchand xbox"]))
        self.assertTrue(any(n.startswith("confirmé par la page") for n in res["notes"]))
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
        self.assertTrue(any(n.startswith("URL contredite par la page") for n in res["notes"]))
        # le DLC seul, dans la même édition : l'alerte reste, confirmée par la page
        res = self.run_check("GTA 5", o, url, "<title>GTA V: Criminal Enterprise Starter Pack DLC | Keycense</title>")
        self.assertEqual(res["reasons"], ["contenu additionnel : dlc"])
        self.assertTrue(any(n.startswith("confirmé par la page") for n in res["notes"]))
        # un « + » qui ne suit pas le jeu (le pack plus une carte) ne vaut pas le jeu
        res = self.run_check("GTA 5", o, url, "<title>Criminal Enterprise Starter Pack + Great White Shark Card DLC</title>")
        self.assertEqual(res["reasons"], ["contenu additionnel : dlc"])

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
        self.assertEqual((res["verdict"], res["method"]), ("OK", "URL de la version en-GB"))

    def test_unverifiable_reason_says_what_the_url_names(self):
        # 02/10/2026, World of Warcraft: Forever (Heroic Pack) chez Driffle : la page bloque le moniteur ; l'ancien
        # message « URL sans nom du produit » était faux, l'URL nomme « warcraft forever skyborne heroic pack »
        o = offer(edition="Heroic Pack", region="BATTLENET GIFT", region_filter="BATTLENET GIFT", platform="battle-net")
        url = "https://www.driffle.com/warcraft-forever-skyborne-heroic-pack-dlc-global-pc-mac-battlenet-gift-p10001673"
        self.assertEqual(pc.unverified_reason(o, url),
                         "édition Heroic Pack : nom non contrôlé dans l'URL (elle nomme « Warcraft Forever Skyborne Heroic Pack Dlc Mac »), "
                         "page marchand illisible")
        self.assertEqual(pc.unverified_reason(offer(), "https://www.hrkgame.com/en/product/12345/"),
                         "nom du produit introuvable dans l'URL, page marchand illisible")
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
        self.assertTrue(res["reasons"][0].startswith("autre produit chez le marchand : « Titanfall Deluxe Edition"), res["reasons"])
        # audit du 02/10/2026 : un slug périmé que le marchand redirige (301) vers la fiche du bon produit n'alerte pas
        # (Instant Gaming garde l'id et change le slug : « /en/4860-buy-key-…-breath-of-the-wild-2/ »)
        zelda = "The Legend of Zelda Tears of the Kingdom Nintendo Switch"
        stale = "https://www.instant-gaming.com/en/4860-buy-key-some-old-name/"
        good = "https://www.instant-gaming.com/en/4860-buy-the-legend-of-zelda-tears-of-the-kingdom-switch-game-nintendo-eshop-europe/"
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, self.page(stale)), (301, good, "")]):
            res = pc.check_offer(zelda, offer(merchantName="Instant Gaming", region="EUROPE", platform="nintendo-eshop"))
        self.assertEqual((res["verdict"], res["method"]), ("OK", "URL après 301 marchand"))
        # et un lien sans nom dont le 301 mène à une fiche d'un autre produit alerte, sans lire la page
        other = "https://www.instant-gaming.com/en/4860-buy-the-legend-of-zelda-breath-of-the-wild-switch/"
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, self.page("https://www.instant-gaming.com/en/4860-/")), (301, other, "")]):
            res = pc.check_offer(zelda, offer(merchantName="Instant Gaming", region="EUROPE", platform="nintendo-eshop"))
        self.assertEqual((res["verdict"], res["method"]), ("SUSPECT", "URL après 301 marchand"))
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
        self.assertTrue(res["reasons"][1].startswith("offre en rupture chez le marchand"))  # et la redirection est signalée
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
        self.assertEqual(res["reasons"], ["offre en rupture chez le marchand : le lien redirige vers une autre fiche "
                                          "(https://www.kinguin.net/category/25568/titanfall-2-pc-ea-app-key), mais le prix reste dans le feed"])
        # une fiche seulement RENOMMÉE (même nom, région, plateforme, édition : 24 des 25 redirections en mémoire le 02/10)
        # n'est pas une rupture : une note, pas d'alerte
        dayz = "https://www.kinguin.net/category/55338/dayz-eu-steam-altergift/"
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, self.page(dayz)), (301, "/category/55338/dayz-eu-pc-steam-altergift", "")]):
            res = pc.check_offer("Dayz", offer(merchantName="Kinguin", region="GIFT EU", region_filter="STEAM GIFT EU"))
        self.assertEqual((res["verdict"], res["reasons"]), ("OK", []))
        self.assertTrue(any(n.startswith("fiche renommée chez le marchand") for n in res["notes"]), res["notes"])
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
        self.assertTrue(res["reasons"][0].startswith("offre en rupture chez le marchand"), res)
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
        self.assertTrue(res["reasons"][0].startswith("offre en rupture chez le marchand : le lien redirige vers une autre fiche"), res)

    def test_kinguin_probe_keeps_what_it_saw_and_never_passes_silently(self):
        # 02/10/2026 : la fiche servie à la place du lien est gardée pour le recontrôle (evidence) ; une sonde en erreur
        # n'est pas une fiche en stock : contrôle raté, retenté au passage suivant (À VÉRIFIER au 3e échec)
        rust = "https://www.kinguin.net/category/55259/rust-eu-steam-altergift/"
        o = offer(merchantName="Kinguin", region="GIFT EU", region_filter="STEAM GIFT EU")
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, self.page(rust)), (301, "/category/55259/rust-de-pc-steam-altergift", "")]):
            res = pc.check_offer("Rust", o)
        self.assertEqual(res["evidence"], {"served": pc.norm(pc.url_text("https://www.kinguin.net/category/55259/rust-de-pc-steam-altergift")),
                                           "page": ""})
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, self.page(rust)), (200, None, "")]):
            res = pc.check_offer("Rust", o)
        self.assertEqual((res["verdict"], res["evidence"]), ("OK", {"served": "", "page": ""}))
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, self.page(rust)), OSError("timed out")]):
            with self.assertRaises(pc.CheckError):
                pc.check_offer("Rust", o)

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
        self.assertEqual(res["reasons"], ["offre en rupture chez le marchand : le lien redirige vers une autre fiche "
                                          "(https://www.kinguin.net/category/172478/stellaris-starter-pack-bundle-2023-pc-steam-cd-key), "
                                          "mais le prix reste dans le feed"])
        self.assertTrue(any("fiche servie" in n for n in res["notes"]))
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
        self.assertEqual(res["reasons"], ["région : AllKeyShop GLOBAL, marchand EU"])
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
        self.assertTrue(any(n.startswith("URL contredite par la page") for n in res["notes"]))

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
        self.assertEqual((res["verdict"], res["reasons"]), ("SUSPECT", ["plateforme : AllKeyShop ea-app, marchand steam"]))

    def test_localized_title_gives_manual_check(self):
        url = "https://www.amazon.fr/gp/product/B09XXXXXXX/"
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, self.page(url)), (200, None, ""),
                                                            (200, None, "<title>Amazon.fr : Le Jeu Inconnu (Nintendo Switch)</title>")]):
            res = pc.check_offer("Some Game Nintendo Switch", offer(merchantName="Amazon.fr", region="BOX", platform="physical-medium"))
        self.assertEqual(res["verdict"], "À VÉRIFIER")
        self.assertTrue(res["reasons"][0].startswith("titre du marchand dans une autre langue"))

    def test_silent_page_is_not_a_confirmation(self):
        res = self.run_check("Stellaris", offer(edition="Bundle 1", region="GLOBAL", region_filter="STEAM GLOBAL"),
                             "https://kinguin.net/category/172478/stellaris-starter-pack-eu-steam-cd-key",
                             "<title>Stellaris: Starter Pack Bundle 2023 PC Steam CD Key | Buy cheap on Kinguin.net</title>")
        self.assertEqual(res["verdict"], "SUSPECT")
        self.assertTrue(any(n.startswith("la page ne dit rien sur ce point") for n in res["notes"]))

    def test_name_unverifiable_but_wrong_console(self):
        # Elden Ring Xbox Series chez Amazon : nom illisible, mais l'URL dit PlayStation
        url = "https://www.amazon.fr/Bandai-Namco-Entertainment-3391892017632-PlayStation/dp/B0977LKSQ6/"
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, self.page(url)), (200, None, ""), (200, None, "<title>Amazon.fr</title>")]), \
             mock.patch.object(pc, "page_title") as title:
            res = pc.check_offer("Elden Ring Xbox Series", offer(merchantName="Amazon.fr", edition="Launch Edition", region="BOX",
                                                                region_filter="BOX", platform="physical-medium"))
        title.assert_not_called()
        self.assertEqual((res["verdict"], res["reasons"]), ("SUSPECT", ["plateforme : page AllKeyShop Xbox, marchand PlayStation"]))


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
        self.assertEqual((res["verdict"], res["method"], res["url"]), ("OK", "URL après 301 marchand", full))

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
                         ("SUSPECT", ["autre produit chez le marchand : « Sonic the Hedgehog » au lieu de « Sonic Racing CrossWorlds » (titre de la page)"]))

    def test_unreadable_page(self):
        page = self.interstitial("https://store.epicgames.com/p/abc-123")
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, page), (403, None, ""), (403, None, "")]), \
             mock.patch.object(pc, "page_title", return_value=None):
            res = pc.check_offer("EA SPORTS FC 27", offer())
        self.assertEqual((res["verdict"], res["method"]), ("À VÉRIFIER", "aucune"))

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
                         ["monnaie de jeu chez le marchand : cod-points"])
        self.assertEqual(self.reasons(cod, "https://shop.example/black-ops-6-2400-points-pc-battle-net", platform="battle-net"),
                         ["monnaie de jeu chez le marchand : points"])  # mot seul + quantité
        self.assertEqual(self.reasons("Fortnite", "https://shop.example/fortnite-1000-v-bucks-pc", platform="epic-store"),
                         ["monnaie de jeu chez le marchand : v-bucks"])
        self.assertEqual(self.reasons("EA SPORTS FC 26", "https://shop.example/ea-sports-fc-26-12000-fc-points-pc-ea-app", platform="ea-app"),
                         ["monnaie de jeu chez le marchand : fc-points"])
        self.assertEqual(self.reasons("Elden Ring", "https://shop.example/elden-ring-steam-wallet-code-50-eur"),
                         ["monnaie de jeu chez le marchand : wallet"])

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
        self.assertIn("· Standard · 2e prix de l'édition", msg.splitlines()[0])
        self.assertEqual(pc.rank_label({"edition_rank": 1, "account": True}), "1er prix de l'édition (compte)")
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
                                     "reasons": ["région : AllKeyShop GLOBAL, marchand EU"], "page": page, "at": "2026-10-01 10:00",
                                     "seen": pc.time.time()}, **kw)
        def fresh_state():
            st = pc.load_state("/nonexistent")
            st["checked"] = {fixed_id: flagged(), still_id: flagged(), "999999": flagged(merchant="Retiré"),
                             faux_id: flagged(decision={"decision": "faux", "by": "romain"}),
                             nv_id: flagged(verdict="NON VÉRIFIABLE", reasons=["nom du produit introuvable dans l'URL, page marchand illisible"]),
                             ok_id: flagged(verdict="OK", reasons=[])}
            return st
        suspect = {"verdict": "SUSPECT", "reasons": ["région : AllKeyShop GLOBAL, marchand EU"], "notes": [], "url": "https://x/eu", "method": "URL"}
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
        self.assertEqual(state["checked"]["999999"]["fixed_how"], "offre retirée de la page")
        self.assertEqual((len(outcome["still"]), state["checked"][nv_id]["verdict"], outcome["new"]), (2, "SUSPECT", []))
        self.assertTrue(state["checked"][still_id]["still_wrong_at"])
        # alertes : la NON VÉRIFIABLE devenue SUSPECT, plus les 6 offres de la page jamais vues (SUSPECT dans ce test)
        self.assertEqual(len(sent), 1 + 6)
        recap = pc.format_recheck("Price check top", "", outcome)
        self.assertIn("Recontrôle des offres signalées** · Price check top · 3 offre(s)", recap)
        self.assertIn("✅ Réparées (1)", recap)
        self.assertIn("🧹 Anciens faux positifs levés par les règles, rien n'a changé (1)", recap)
        self.assertIn("🔴 Toujours en erreur (2)", recap)
        self.assertIn("Retiré — offre retirée de la page", recap)
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
        self.assertIn("Recontrôle de toutes les offres** · Price check top (demandé depuis l'admin par romain) · 4 offre(s)", recap)
        self.assertIn("🆕 Nouvelles erreurs (1)", recap)
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
        self.assertTrue(e["fixed_how"].startswith("recontrôle OK, l'offre a changé (URL"), e["fixed_how"])
        # un recontrôle qui ne conclut pas (page illisible) ne défait pas un OK vérifié (G2A Witcher, 02/10/2026)
        state, sent = fresh_state(), []
        unverifiable = {"verdict": "À VÉRIFIER", "reasons": ["édition Bundle : nom non contrôlé dans l'URL, page marchand illisible"],
                        "notes": [], "url": None, "method": "aucune"}
        with mock.patch.object(pc, "http_get", return_value=(200, None, self.PAGE)), mock.patch.object(pc, "NOTIFY_OK", False):
            outcome = pc.run_cycle(self.TARGETS, sent.append, state, lambda p, o: dict(unverifiable), per_edition=3, recheck="all")
        self.assertEqual(state["checked"][ok_id]["verdict"], "OK")
        self.assertEqual(state["checked"][still_id]["verdict"], "SUSPECT")
        self.assertEqual((outcome["new"], outcome["fixed"], outcome["still"]), ([], [], []))
        self.assertEqual(len(outcome["unknown"]), 4)  # fixed, still, ok, et la NON VÉRIFIABLE, qui le reste
        self.assertEqual(state["checked"][nv_id]["verdict"], "NON VÉRIFIABLE")
        recap = pc.format_recheck("Price check top", "romain", outcome, full=True)
        self.assertIn("⚪ Recontrôle sans conclusion, verdict inchangé (4)", recap)
        self.assertNotIn("Nouvelles erreurs", recap)
        # 3. sans recontrôle : rien
        with mock.patch.object(pc, "http_get", return_value=(200, None, self.PAGE)), mock.patch.object(pc, "NOTIFY_OK", False):
            outcome = pc.run_cycle(self.TARGETS, lambda m: None, pc.load_state("/nonexistent"), self.ok)
        self.assertEqual(outcome, {"checked": 0, "fixed": [], "removed": [], "rules": [], "still": [], "new": [], "unknown": []})
        self.assertIn("Rien à signaler", pc.format_recheck("Price check top", "", outcome))

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
        self.assertEqual((e["fixed_kind"], e["fixed_how"]), ("repaired", "recontrôle OK, l'offre a changé (fiche servie)"))
        self.assertEqual(e["evidence"], nothing)  # désormais gardé
        # 2. la fiche servie est gardée : elle était une autre, le lien sert de nouveau sa fiche
        served = pc.evidence_of("https://www.kinguin.net/category/55259/rust-de-pc-steam-altergift", None)
        self.assertEqual(recheck(base(evidence=served), ok(nothing))["fixed_kind"], "repaired")
        # 3. même lien, autre titre de page (le marchand a corrigé sa fiche) : réparée
        title = lambda t: pc.evidence_of(None, t)
        e = recheck(base(reasons=["autre produit chez le marchand : « Nocturne » au lieu de « Rust » (titre de la page)"],
                         evidence=title("Nocturne PC Steam CD Key | Kinguin")), ok(title("Rust EU Steam Altergift | Kinguin")))
        self.assertEqual((e["fixed_kind"], e["fixed_how"]), ("repaired", "recontrôle OK, l'offre a changé (page marchand)"))
        # 4. rien n'a changé, ni le lien ni ce qui a été vu : une règle a levé un faux positif
        e = recheck(base(reasons=["région : AllKeyShop GIFT EU, marchand GLOBAL"], evidence=title("Rust | Kinguin")),
                    ok(title("Rust | Kinguin")))
        self.assertEqual(e["fixed_kind"], "rule")
        e = recheck(base(reasons=["région : AllKeyShop GIFT EU, marchand GLOBAL"]), ok(nothing))  # d'avant le 02/10, sans rupture
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
                                              "reasons": ["nom du produit introuvable dans l'URL, page marchand illisible"], "notes": [],
                                              "url": "https://shop.example/dp/B0", "method": "aucune"}
        with mock.patch.object(pc, "http_get", return_value=(200, None, self.PAGE)), mock.patch.object(pc, "NOTIFY_OK", False), \
                mock.patch.dict(pc.PAGE_LISTS, {page: {"Popular"}}, clear=True):
            pc.run_cycle(self.TARGETS, sent.append, state, self.ok, per_edition=3)
            self.assertEqual(state["checked"][str(first["id"])]["verdict"], "À VÉRIFIER")
            self.assertEqual(len(sent), 1)
            self.assertIn("🟠 **À VÉRIFIER**", sent[0])
            self.assertIn("devenue le premier prix de la page", sent[0])
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
        # seul le premier prix de la page (page d'un top « Popular ») part en À VÉRIFIER ; les autres éditions sont notées
        self.assertEqual(len(sent), 1)
        self.assertIn("🟠 **À VÉRIFIER**", sent[0])
        self.assertIn("contrôle impossible : redirection AllKeyShop HTTP 503", sent[0])
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
        pc.run_cycle(targets, lambda m: None, pc.load_state("/nonexistent"), self.ok, save=lambda: saves.append(1))
        self.assertEqual(len(saves), 2)

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
                  "price": 5.99, "reasons": ["autre produit chez le marchand : « Metal Garden » au lieu de « TORO 2 Nintendo Switch » (URL)"],
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
