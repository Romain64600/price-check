"""Tests hors ligne, sur les fichiers de samples/ : python3 -m unittest -v"""

import copy
import json
import os
import re
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
        self.assertEqual((res["match"], res["reasons"], res["notes"]),
                         (None, [], ["édition Bundle : nom non contrôlé en entier (un mot du nom présent)"]))
        res = pc.analyze("The Witcher 3 Wild Hunt", offer(edition="Bundle", region="GLOBAL"), pc.url_text(url), "URL")
        self.assertEqual(res["reasons"], ["gift chez le marchand, affiché en clé GLOBAL"])
        # audit du 02/10/2026 : sans un mot du nom, un lot n'est pas blanchi (Sonic en « Starter Pack » sur The Witcher 3)
        sonic = "https://www.g2a.com/sonic-the-hedgehog-4-episode-1-steam-key-global-i10000000001"
        for edition in ("Starter Pack", "Bundle", "Complete Collection"):
            res = pc.analyze("The Witcher 3 Wild Hunt", offer(edition=edition), pc.url_text(sonic), "URL")
            self.assertTrue(res["reasons"] and res["reasons"][0].startswith("autre produit chez le marchand"), (edition, res["reasons"]))
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
        self.assertEqual(pc.name_variants("Ace Combat 8"), ("Ace Combat 8", "ace combat viii"))

    def test_platform_suffix_of_the_product_name(self):
        # PS Store US, 30/09/2026 : « GTA 6 PS5 », URL EP1004-PPSA01547_00-GTAVIULTIMATE001, titre sans « PS5 »
        url = "https://store.playstation.com/en-us/product/EP1004-PPSA01547_00-GTAVIULTIMATE001?partner=allkeyshopcom"
        # depuis l'audit du 02/10/2026, le nom n'est plus cherché au milieu d'un mot : « gtavi » dans « gtaviultimate001 »
        # ne prouve rien (« ron » était trouvé dans « iron ») ; au PS Store, c'est le JSON de la page qui décide
        self.assertEqual(self.reasons("GTA 6 PS5", url, edition="Ultimate", region="PS5", platform="playstation-store"),
                         ["nom du produit introuvable (URL)"])
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
        self.assertEqual(pc.analyze("Minecraft Dungeons", o, text, "titre de la page")["reasons"], [])
        argentina = pc.page_text_from_dom(dom, base + "701", "url-variation")
        self.assertEqual(pc.analyze("Minecraft Dungeons", o, argentina, "titre de la page")["reasons"], ["région interdite : ar, argentina"])

        # de bout en bout : l'URL nomme le produit mais pas la région ; avec « variation= », la page est lue pour la variante
        def check(variation):
            with mock.patch.object(pc, "http_get", side_effect=[(200, None, self.page(base + variation)), (403, None, "")] + [(403, None, "")] * 4), \
                 mock.patch.object(pc, "chromium_dom", return_value=dom):
                return pc.check_offer("Minecraft", offer(merchantName="CJS CDKeys", region="EUROPE", region_filter="WINDOWS EU",
                                                         platform="microsoft-windows"))
        res = check("699")
        self.assertEqual(res["verdict"], "OK", res)
        self.assertIn("région lue sur la variante choisie par le lien : Europe", res["notes"])
        res = check("701")  # la clé argentine affichée EUROPE : une vraie erreur, que l'URL seule laissait passer
        self.assertEqual((res["verdict"], res["reasons"]), ("SUSPECT", ["région interdite : ar, argentina"]))

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
        self.assertIn("· Standard · 2e prix de l'édition", msg.splitlines()[1])
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
                                     "seen": pc.time.time(), "sent_to": "urgent"}, **kw)  # déjà dans le bon salon
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
        self.assertEqual({k: v for k, v in outcome.items() if k != "first_checked"},
                         {"checked": 0, "fixed": [], "removed": [], "rules": [], "verified": [], "still": [], "new": [], "unknown": []})
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
                     "reasons": ["autre produit chez le marchand : « Autre » au lieu de « Jeu » (URL)"], "url": "https://www.kinguin.net/category/1/autre",
                     "region": "GLOBAL", "region_filter": "STEAM GLOBAL", "platform": "steam", "at": "2026-10-01 10:00", "seen": pc.time.time()}, **kw)

    def cycle(self, page_html, state, checker, recheck=False, sent=None):
        with mock.patch.object(pc, "http_get", return_value=(200, None, page_html)), mock.patch.object(pc, "NOTIFY_OK", False):
            return pc.run_cycle(self.TARGETS, (sent if sent is not None else []).append, state, checker, per_edition=3, recheck=recheck)

    def test_an_offer_without_price_is_not_repaired_and_a_removed_one_is_rechecked_when_back(self):
        # constat 1 : une offre signalée passée « sans prix » (0.02) devenait « réparée », puis n'était plus contrôlée
        state = pc.load_state("/nonexistent")
        state["checked"]["1001"] = self.flagged()
        calls = []
        wrong = lambda product, o: calls.append(o["id"]) or {"verdict": "SUSPECT", "reasons": ["autre produit"], "notes": [],
                                                              "url": "https://www.kinguin.net/category/1/autre", "method": "URL"}
        other = {"id": 1002, "price": 12.0}
        outcome = self.cycle(aks_page([{"id": 1001, "price": 0.02}, other]), state, wrong, recheck="flagged")
        e = state["checked"]["1001"]
        self.assertEqual((e["verdict"], e.get("fixed_at"), outcome["removed"]), ("SUSPECT", None, []))
        self.assertEqual(outcome["unknown"][0][1], "offre momentanément sans prix sur la page")
        # page servie sans offres : rien n'est « retiré »
        outcome = self.cycle(aks_page([]), state, wrong, recheck="flagged")
        self.assertEqual((state["checked"]["1001"]["verdict"], outcome["removed"]), ("SUSPECT", []))
        # vraiment retirée de la page : réparée, et marquée pour être recontrôlée si elle revient
        outcome = self.cycle(aks_page([other]), state, wrong, recheck="flagged")
        self.assertEqual((e["verdict"], e["fixed_how"], bool(e.get("removed_at"))), ("OK", "offre retirée de la page", True))
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
        self.assertEqual((pc.FAILURES.get("1001"), outcome["unknown"][0][1][:13]), (1, "contrôle raté"))
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
        self.assertEqual((e["fixed_kind"], len(outcome["verified"]), e["fixed_how"]), ("verified", 1, "vérifiée OK au recontrôle"))
        # (b) une entrée ancienne sans nom de filtre n'a pas « changé de région »
        e, _ = recheck(dict(base, region_filter=""))
        self.assertEqual(e["fixed_kind"], "rule")
        e, _ = recheck(dict(base, region="EUROPE", region_filter="STEAM EU"))
        self.assertEqual((e["fixed_kind"], e["fixed_how"]), ("repaired", "recontrôle OK, l'offre a changé (région)"))
        # (c) une offre réparée redevenue fausse perd son classement
        wrong = dict(ok, verdict="SUSPECT", reasons=["autre produit"])
        e, outcome = recheck(dict(base, verdict="OK", fixed_at="2026-10-02 18:00", fixed_kind="rule", still_wrong_at="2026-10-02 17:00"), wrong)
        self.assertEqual((e.get("fixed_kind"), e.get("still_wrong_at"), len(outcome["new"])), (None, None, 1))
        recap = pc.format_recheck("Price check top", "", {"checked": 2, "fixed": [e], "removed": [], "rules": [], "verified": [],
                                                          "still": [], "new": [e], "unknown": []})
        self.assertLess(recap.index("🆕 Nouvelles erreurs"), recap.index("✅ Réparées"))  # le plus important d'abord

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
                         ["région : AllKeyShop IN ENGLISH ONLY, marchand EU"])

    def test_hunt_showdown_gameseal_emea_shown_row(self):
        self.assertEqual(self.reasons("Hunt Showdown", "https://gameseal.com/hunt-showdown-1896-pc-steam-key-emea",
                                      region="ROW", region_filter="STEAM ROW"), ["région : AllKeyShop ROW, marchand EMEA"])

    def test_black_ops_3_gamivo_eu_gift_shown_row(self):
        self.assertEqual(self.reasons("Call of Duty Black Ops 3", "https://www.gamivo.com/product/call-of-duty-black-ops-iii-zombies-chronicles-edition-eu",
                                      edition="Limited", region="ROW", region_filter="STEAM ROW"), ["région : AllKeyShop ROW, marchand EU"])

    def test_monster_hunter_wilds_g2a_row_shown_europe(self):
        self.assertEqual(self.reasons("Monster Hunter Wilds", "https://www.g2a.com/en/monster-hunter-wilds-deluxe-edition-pc-steam-key-row-i10000507334026",
                                      edition="Deluxe", region="EUROPE", region_filter="STEAM EU"), ["région : AllKeyShop EUROPE, marchand ROW"])

    def test_space_marine_2_anniversary_package_in_gold(self):
        # Steam /sub/997629 : le paquet « 1-Year Anniversary Edition » rangé en Gold, alors que la page a cette édition
        o = offer(edition="Gold", region="GLOBAL", region_filter="STEAM GLOBAL",
                  page_editions=["Standard", "Gold", "1 Year Anniversary Edition", "Ultra"])
        r = pc.analyze("Warhammer 40k Space Marine 2", o, "Warhammer 40,000: Space Marine 2 - 1-Year Anniversary Edition on Steam",
                       "titre de la page")
        self.assertEqual(r["reasons"], ["édition : rangée en Gold, le marchand vend anniversary (la page a une édition 1 Year Anniversary Edition)"])


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
        self.assertTrue(r["reasons"] and r["reasons"][0].startswith("autre produit chez le marchand : « Pokemon Violet"), r["reasons"])
        r = pc.analyze("Pokemon Sword Nintendo Switch", o, pc.url_text("https://shop.example/pokemon-shield-nintendo-switch-eu"), "URL")
        self.assertTrue(r["reasons"] and r["reasons"][0].startswith("autre produit"), r["reasons"])

    def test_short_page_title_only_the_end_of_the_name(self):
        o = offer(platform="playstation-store", region="PS5")
        self.assertIsNone(pc.analyze("God of War Ragnarok PS5", o, "God of War | Standard Edition", "titre de la page")["match"])
        self.assertIsNone(pc.analyze("Call of Duty Black Ops 7", offer(), "Call of Duty: Black Ops | Steam", "titre de la page")["match"])
        self.assertEqual(pc.analyze("Ace Combat 8 Wings of Theve", offer(), "Wings of Theve | Steam", "titre de la page")["match"], "partial")

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
        self.assertEqual(r(cjs, region="EUROPE", region_filter="PSN EU"), ["région : AllKeyShop EUROPE, marchand US"])
        self.assertEqual(r("https://shop.example/ea-sports-fc-27-ps5-united-kingdom", region="GLOBAL", region_filter="PSN GLOBAL"),
                         ["région : AllKeyShop GLOBAL, marchand UK"])
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
        self.assertEqual(pc.analyze("Hearts of Iron 4", o, pc.url_text(url), "URL")["reasons"], ["contenu additionnel : dlc"])

    def test_pass_and_add_on_are_extra_content(self):
        # le Booster Course Pass vendu sur la page du jeu de base, sans le mot « dlc »
        r = pc.analyze("Mario Kart 8 Deluxe Nintendo Switch", offer(platform="nintendo-eshop", region="EUROPE"),
                       pc.url_text("https://www.g2a.com/mario-kart-8-deluxe-booster-course-pass-nintendo-switch-nintendo-eshop-key-europe-i1"), "URL")
        self.assertEqual(r["reasons"], ["contenu additionnel : pass"])
        r = pc.analyze("Cities Skylines 2", offer(), pc.url_text("https://shop.example/cities-skylines-2-beach-properties-add-on-pc-steam"), "URL")
        self.assertEqual(r["reasons"], ["contenu additionnel : add-on"])
        # « season-pass » ne compte qu'une fois ; le season pass de l'édition « Year 1 » reste le jeu (arbitrage du 01/10)
        r = pc.analyze("Farming Simulator 25", offer(), pc.url_text("https://shop.example/farming-simulator-25-season-pass-pc-steam"), "URL")
        self.assertEqual(r["reasons"], ["contenu additionnel : season-pass"])

    def test_currency_quantity_that_looks_like_a_year_and_awin_links(self):
        r = lambda product, url: pc.analyze(product, offer(), pc.url_text(url), "URL")["reasons"]
        self.assertEqual(r("Apex Legends", "https://shop.example/apex-legends-2000-coins-pc"), ["monnaie de jeu chez le marchand : coins"])
        self.assertEqual(r("War Thunder", "https://shop.example/war-thunder-2500-golden-eagles-pc"), ["monnaie de jeu chez le marchand : golden-eagles"])
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
        self.assertEqual((res["verdict"], res["reasons"]), ("SUSPECT", ["plateforme : AllKeyShop steam, marchand xbox"]))
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
        # une page des tops est aussi dans la homepage (le TOP 50 Popular contient les 5 premiers)
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
        still = lambda product, o: {"verdict": "SUSPECT", "reasons": ["région : AllKeyShop GLOBAL, marchand EU"], "notes": [],
                                    "url": "https://shop.example/jeu-eu", "method": "URL"}
        old = lambda **kw: dict({"verdict": "SUSPECT", "product": "Jeu", "edition": "Standard", "merchant": "Kinguin", "page": page,
                                 "reasons": ["région : AllKeyShop GLOBAL, marchand EU"], "url": "https://shop.example/jeu-eu",
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
        self.assertTrue(all("📌 **Rappel** · report existant (signalé le " in m and "renvoyé dans le salon des urgences premiers prix" in m.splitlines()[1]
                            for m in urgent), urgent)
        self.assertEqual(len(sent), 2)  # l'offre 1005 attend un passage homepage
        self.assertEqual([state["checked"][k].get("sent_to") for k in ("1001", "1002", "1003")], ["urgent", "urgent", "urgent"])
        # le passage homepage : l'alerte homepage du 30/09 (partie sur le salon des top games) rejoint son salon
        sent = []
        cycle("homepage", sent)
        self.assertEqual(len(sent), 1, sent)
        self.assertTrue(sent[0].startswith("📌 **Rappel** · report existant (signalé le 2026-09-30 15:54), renvoyé dans le salon de son mode"), sent[0])
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
        fixed = {"verdict": "OK", "fixed_at": "2026-10-03 11:20", "fixed_how": "recontrôle OK, l'offre a changé (URL)"}
        rule = {"verdict": "OK", "fixed_at": "2026-10-03 11:21"}
        new = {"verdict": "SUSPECT", "at": "2026-10-03 11:22", "reasons": ["autre produit chez le marchand : « Sonic »"]}
        lone = {"verdict": "OK", "fixed_at": "2026-10-03 11:23"}
        state["checked"] = {"1": fixed, "2": rule, "3": new, "4": lone}
        threads = {"1": {"thread": 901, "guild": 77, "mode": "urgent"}, "2": {"thread": 902, "guild": 77, "mode": "homepage"},
                   "3": {"thread": 903, "guild": 77, "mode": "top-games"}}
        outcome = {"fixed": [fixed, lone], "removed": [], "rules": [rule], "verified": [], "new": [new], "still": [], "unknown": []}
        sent = []
        self.assertEqual(pc.post_follow_ups(state, outcome, threads, lambda info, msg: sent.append((info["thread"], msg))), 3)
        self.assertEqual(sent, [
            (901, "✅ **Réparée** au recontrôle du 2026-10-03 11:20 : recontrôle OK, l'offre a changé (URL)"),
            (902, "🧹 **Faux positif levé par une règle** au recontrôle du 2026-10-03 11:21 : rien n'a changé dans l'offre"),
            (903, "🆕 **De nouveau en erreur** au recontrôle du 2026-10-03 11:22 : autre produit chez le marchand : « Sonic »"),
        ])  # l'offre 4 n'a pas de fil : rien
        # un envoi raté n'arrête rien
        self.assertEqual(pc.post_follow_ups(state, outcome, threads, lambda info, msg: (_ for _ in ()).throw(OSError("503"))), 0)
        # une décision prise dans l'admin est recopiée dans le fil ; celle prise sur Discord y est déjà
        state["checked"]["1"]["decision"] = {"decision": "faux", "note": "bonne édition", "by": "romain"}
        state["checked"]["2"]["decision"] = {"decision": "vrai", "note": "", "by": "Rémi (Discord)"}
        sent.clear()
        pc.post_admin_decisions(state, ["1", "2"], threads, lambda info, msg: sent.append((info["thread"], msg)))
        self.assertEqual(sent, [(901, "📝 Décision prise dans l'admin : **Faux positif** — « bonne édition » — par romain")])
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
        self.assertEqual(lines[1], "# 🔄 Nouvelle boucle · Price check top")  # un titre Discord : le plus visible
        self.assertEqual(lines[2], "-# 03/10/2026 13:20 · les tops : 5 premiers Popular, 4 premiers Coming soon PC · "
                                   "avec le recontrôle horaire des offres signalées")
        for legend in ("🔴 🟠 nouveau report", "📌 rappel d'un report existant", "🔁 bilan du recontrôle", "**vrai**"):
            self.assertIn(legend, banner)
        self.assertEqual(lines[-1], "📘 Guide de l'équipe : <https://169.58.5.63.sslip.io/executor/price-check-guide>")
        self.assertLess(len(banner), pc.DISCORD_LIMIT)

    def test_each_channel_its_banner(self):
        urgent = pc.loop_banner(self.loop("homepage"), "urgent")
        self.assertEqual(urgent.splitlines()[1], "# 🚨 Nouvelle boucle · Price check homepage")
        self.assertIn("3 offres les moins chères d'une édition. À traiter en premier.", urgent)
        self.assertNotIn("🔁", urgent)  # les bilans du recontrôle partent sur le salon du mode
        requested = pc.loop_banner(self.loop("homepage", recheck="all", requested="romain"), "homepage")
        self.assertIn("toute la homepage : widgets de la home, TOP 50 de chaque plateforme · passage demandé depuis "
                      "l'admin par romain : toutes les offres recontrôlées", requested)
        self.assertEqual(pc.loop_banner(self.loop("homepage"), "urgent", resumed=True),
                         pc.BANNER_RULE + "\n### ↪️ Suite de la boucle · Price check homepage, commencée le 03/10/2026 13:20")

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
            ("urgent", "# 🚨 Nouvelle boucle · Price check homepage"), ("urgent", "🚨 alerte 1"), ("urgent", "🚨 alerte 2"),
            ("urgent", "# 🚨 Nouvelle boucle · Price check top"), ("urgent", "🚨 alerte 3"),
            ("urgent", "### ↪️ Suite de la boucle · Price check homepage, commencée le 03/10/2026 13:20"), ("urgent", "🚨 alerte 4"),
            ("homepage", "# 🔄 Nouvelle boucle · Price check homepage"), ("homepage", "🔁 bilan")])

    def test_a_shared_webhook_is_one_channel(self):
        """Sans webhook homepage, ses alertes partent sur celui des top games : un seul salon, une boucle chasse l'autre."""
        sent = []
        announce = pc.make_announcer(key_of=lambda channel: "top games")
        top, home = self.loop("top-games"), self.loop("homepage")
        announce(top, "top-games", sent.append, "a")
        announce(home, "homepage", sent.append, "b")
        announce(top, "top-games", sent.append, "c")
        titles = [m.splitlines()[1] for m in sent if m.startswith(pc.BANNER_RULE)]
        self.assertEqual(titles, ["# 🔄 Nouvelle boucle · Price check top", "# 🔄 Nouvelle boucle · Price check homepage",
                                  "### ↪️ Suite de la boucle · Price check top, commencée le 03/10/2026 13:20"])

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
                         ["# 🔄 Nouvelle boucle · Price check top", "🔴 alerte 1"])

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
                notify("🟠 **À VÉRIFIER** · **Jeu** (Popular #1) · Standard")
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
            ("top", "# 🔄 Nouvelle boucle · Price check top"), ("top", "🟠 **À VÉRIFIER** · **Jeu** (Popular #1) · Standard"),
            ("urgent", "# 🚨 Nouvelle boucle · Price check top"), ("urgent", pc.URGENT_PREFIX + " · Price check top"),
            ("home", "# 🔄 Nouvelle boucle · Price check homepage"),
            ("home", "🔴 **SUSPECT** · **Autre jeu** (Home · RPG #2) · Standard · 5e prix de l'édition")])
        self.assertIn("avec le recontrôle horaire des offres signalées", sent[0][1])  # la première boucle d'un mode recontrôle

    def test_during_a_discord_pause_banner_and_alert_wait_together(self):
        state = {"queued": []}
        with mock.patch.object(pc, "MUTE_UNTIL", "2999-01-01 00:00"):
            notify = pc.make_notifier("https://discord.invalid/api/webhooks/1/x", state, "top-games")
            pc.make_announcer(key_of=lambda channel: channel)(self.loop(), "top-games", notify, "🔴 alerte")
        self.assertEqual([c for c, m in state["queued"]], ["top-games", "top-games"])
        self.assertEqual(state["queued"][0][1].splitlines()[1], "# 🔄 Nouvelle boucle · Price check top")
        self.assertEqual(state["queued"][1][1], "🔴 alerte")


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
