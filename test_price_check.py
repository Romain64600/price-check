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
        self.assertEqual(self.reasons("Red Dead Redemption 2", "https://shop.example/red-dead-redemption-pc-rockstar-key",
                                      platform="rockstar"), ["nom du produit absent (URL)"])

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
        self.assertEqual((res["match"], res["reasons"]), ("partial", []))
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
        self.assertEqual(pc.merchant_config("https://www.kinguin.net/x", "Kinguin"), {})

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

    def test_season_pass_as_game_edition(self):
        # Loaded : le season pass seul, rangé dans « Year 1 Edition » (jeu + pass, 40,45 € chez GAMIVO)
        self.assertEqual(self.reasons("Farming Simulator 25", "https://www.loaded.com/farming-simulator-25-year-1-season-pass-pc-steam",
                                      edition="Year 1 Edition", page_editions=["Standard", "Highlands Fishing Edition", "Year 1 Bundle", "Year 1 Edition"]),
                         ["contenu additionnel : season-pass"])

    def test_edition_misfiled_while_the_page_has_it(self):
        gta4 = ["Complete", "Standard", "Collection", "Complete Bundle", "Complete Pack", "Bundle"]
        res = pc.analyze("GTA 4", offer(edition="Standard", page_editions=gta4), "Grand Theft Auto IV: The Complete Edition on Steam", "titre de la page")
        self.assertEqual(res["reasons"], ["édition : rangée en Standard, le marchand vend complete (la page a une édition Complete)"])
        zero = ["Standard", "Deluxe", "Deluxe + Bonus", "Bonus", "Standard + DLC"]
        self.assertEqual(self.reasons("STAR WARS Zero Company Xbox Series",
                                      "https://www.gamivo.com/product/star-wars-zero-company-xbox-xbox-series-global-deluxe-pre-order-bonus",
                                      edition="Standard + DLC", region="XBOX X|S", region_filter="XBOX X|S GLOBAL", platform="xbox", page_editions=zero),
                         ["édition : rangée en Standard + DLC, le marchand vend deluxe (la page a une édition Deluxe)"])

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
        self.assertEqual(self.reasons("Portal 2", "https://shop.example/mortal-kombat-2-pc-steam"), ["nom du produit absent (URL)"])
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
                         ["nom du produit absent (titre de la page)"])

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
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, self.page(merchant)), (200, None, merchant_html)]), \
             mock.patch.object(pc, "page_title", return_value=None):
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
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, page), (200, None, ""), (200, None, "")]), \
             mock.patch.object(pc, "page_title", return_value="EA SPORTS FC 27 | Download and Buy Today - Epic Games Store"):
            res = pc.check_offer("EA SPORTS FC 27", offer(platform="epic-store"))
        self.assertEqual((res["verdict"], res["method"]), ("OK", "page (Chromium)"))

    def test_page_fallback_wrong_product(self):
        page = self.interstitial("https://store.epicgames.com/p/abc-123")
        with mock.patch.object(pc, "http_get", side_effect=[(200, None, page), (200, None, ""), (200, None, "")]), \
             mock.patch.object(pc, "page_title", return_value="Sonic the Hedgehog - Epic Games Store"):
            res = pc.check_offer("Sonic Racing CrossWorlds", offer(platform="epic-store"))
        self.assertEqual((res["verdict"], res["reasons"]), ("SUSPECT", ["nom du produit absent (titre de la page)"]))

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
            pc.flush_queue(state, sent.append)
        self.assertEqual((sent, state["queued"]), (["alerte 1", "alerte 2"], []))
        with mock.patch.object(pc, "MUTE_UNTIL", "2000-01-01 00:00"), mock.patch.object(pc, "send_discord") as send:
            pc.make_notifier("https://hook", state)("alerte 3")
        send.assert_called_once_with("https://hook", "alerte 3")
        self.assertEqual(state["queued"], [])

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
