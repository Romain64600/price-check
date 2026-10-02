"""Moniteur des premiers prix des pages produit des top clics AllKeyShop, avec alertes Discord.

Boucle sans fin. Deux modes de pages (--mode) :
- top-games : top 5 All Popular + top 4 Coming soon PC, un passage toutes les 2 min 30, qui passe
  aussi au milieu d'un long passage homepage ;
- homepage : tous les jeux des top clics de la home (10 widgets + TOP 50 par plateforme,
  ~430 pages), un passage toutes les 15 min.
Deux modes d'offres (--offers) :
- top-offers (défaut) : les 3 premiers prix de chaque édition ;
- full-page : toutes les offres en vente de la page, comptes compris.
Pour chaque passage :
- relit les listes toutes les 30 min ;
- lit les offres de chaque page produit (UA AKS/Staff) ;
- pour toute nouvelle offre retenue, suit son lien de redirection AllKeyShop (UA AKS/Staff)
  pour obtenir l'URL marchand, puis vérifie que cette URL (à défaut : l'URL après le 301
  du marchand, puis sa page) correspond au produit, à la région, à la plateforme et à
  l'édition affichées ;
- envoie les alertes (SUSPECT, À VÉRIFIER) sur Discord, un webhook par mode de pages.

Voir docs/detection.md et docs/marchands.md.
"""

import argparse
import glob
import html
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

# ---- Réglages ----------------------------------------------------------------

LISTS_API = "https://api.allkeyshop.com/videogame/api/topClick/getLists/eur/allkeyshop.com"
LISTS_PER_CALL = 6  # l'API répond 503 à tout l'appel si un id de liste est inconnu : petits lots
SITE_KEY = "allkeyshop.com.eur"

# Listes top clics : (id de liste <widget>.<liste>, libellé, nombre de jeux suivis, None = toute la liste)
TOP_GAMES_LISTS = (
    ("sidebar.all.popular", "Popular", 5),
    ("sidebar.pc.soon", "Coming soon PC", 4),
)
HOMEPAGE_LISTS = tuple(
    [(f"{widget}.default", f"Home · {label}", None) for widget, label in (
        ("mostAnticipated", "Most anticipated"), ("recentlyReleased", "Recently released"),
        ("fps", "FPS"), ("rpg", "RPG"), ("strategy", "Strategy"), ("action", "Action"),
        ("adventure", "Adventure"), ("management", "Management"), ("racing", "Racing"), ("vr", "VR"))]
    + [(f"sidebar.{platform}.{tab}", f"TOP 50 · {platform_label} {tab_label}", None)
       for platform, platform_label in (("all", "All"), ("pc", "PC"), ("xbox", "Xbox"),
                                        ("playstation", "PlayStation"), ("nintendo", "Nintendo"))
       for tab, tab_label in (("popular", "Popular"), ("soon", "Coming soon"))]
)
# Modes de pages : listes suivies, intervalle entre deux passages (s), variable du webhook Discord.
# « urgent » : le mode passe aussi entre deux pages d'un passage plus long (les top games pendant la homepage).
MODES = {
    "top-games": {"lists": TOP_GAMES_LISTS, "interval": 150, "urgent": True,  # 9 pages ; cache des pages : 120 s
                  "webhook": "DISCORD_WEBHOOK_URL", "label": "Price check top"},
    "homepage": {"lists": HOMEPAGE_LISTS, "interval": 900,  # ~430 pages, un passage dure plusieurs minutes
                 "webhook": "DISCORD_WEBHOOK_URL_HOMEPAGE", "label": "Price check homepage"},  # son salon ; à défaut, celui des top games
}
# Modes d'offres : prix contrôlés par édition (None = toutes les offres en vente de la page, comptes compris)
OFFER_MODES = {"top-offers": 3, "full-page": None}
REDIRECTION_URL = "https://www.allkeyshop.com/redirection/offer/eur/%s?locale=en&merchant=%s"

AKS_UA = "AKS/Staff"  # pages AllKeyShop seulement, jamais chez le marchand
BROWSER_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/150.0.0.0 Safari/537.36")  # chez le marchand
CHROMIUM = os.environ.get("CHROMIUM_BIN", "chromium")  # dernier repli : ouvrir la page marchand
MERCHANTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "merchants")  # une exception par marchand
NOTIFY_OK = os.environ.get("NOTIFY_OK", "1") != "0"  # envoyer aussi les verdicts OK sur Discord
# Pause Discord : jusqu'à cette date locale « AAAA-MM-JJ HH:MM », les alertes sont mises en attente
# dans l'état (et journalisées), puis envoyées automatiquement à la fin de la pause.
MUTE_UNTIL = os.environ.get("DISCORD_MUTE_UNTIL", "")

NO_PRICE = 0.02  # sentinelle « pas de prix »
LISTS_REFRESH = 1800  # max-age de l'API getLists
PAGE_DELAY = 1  # pause entre deux pages produit AllKeyShop
REQUEST_DELAY = 2  # pause entre deux requêtes d'un contrôle (redirection, marchand)
MAX_CHECK_FAILURES = 3  # échecs de contrôle avant de conclure « À VÉRIFIER »
STATE_TTL_DAYS = 30  # oubli des offres plus vues en premier prix depuis ce délai
SAVE_EVERY = 25  # pages entre deux sauvegardes de l'état pendant un passage

# Mots d'URL ou de titre marchand, après normalisation (minuscules, tout ce qui
# n'est pas lettre ou chiffre devient « - »). Un mot n'est reconnu qu'entier.
# Zones : l'ensemble des pays que couvre chaque zone. Une offre est suspecte quand le marchand vend
# pour une zone qui ne couvre pas toute la zone affichée par AllKeyShop (clé EU affichée GLOBAL,
# Stellaris, formation du 30/09/2026) ; l'inverse (clé GLOBAL affichée EUROPE) est sans danger.
EU_COUNTRIES = frozenset({"de", "fr", "it", "es", "pt", "be", "nl", "pl", "at", "ie", "eu-other"})
ZONE_COVERAGE = {
    "GLOBAL": EU_COUNTRIES | {"uk", "us", "row", "me"},
    "EMEA": EU_COUNTRIES | {"uk", "me"},
    "EUUS": EU_COUNTRIES | {"us"},
    "EU": EU_COUNTRIES,
    "ROW": frozenset({"row"}),  # « rest of world » : ni l'Europe, ni GLOBAL
    "US": frozenset({"us"}), "UK": frozenset({"uk"}),
    "DE": frozenset({"de"}), "FR": frozenset({"fr"}), "IT": frozenset({"it"}), "ES": frozenset({"es"}),
    "PT": frozenset({"pt"}), "BE": frozenset({"be"}), "PL": frozenset({"pl"}),
}
# Mots marchands -> zone. Pas de noms de pays : ce sont aussi des noms de DLC (« Vive la France! »,
# « Italia » pour Euro Truck Simulator 2, étude du 30/09/2026) ; pas de codes ambigus (de, it, us...).
MERCHANT_ZONE_WORDS = {
    "GLOBAL": ("global", "worldwide", "ww", "region-free"),
    "EU": ("eu", "europe", "european"),
    "EUUS": ("eu-na", "eu-us", "na-eu", "us-eu"),  # GAMESEAL « …-steam-key-eu-na » = région EU/US
    "ROW": ("row", "rest-of-world", "rest-of-the-world"),
    "EMEA": ("emea",),
}
REGION_FAMILIES = {z: MERCHANT_ZONE_WORDS[z] for z in ("GLOBAL", "EU", "ROW")}  # compatibilité
FORBIDDEN_REGION_WORDS = ("ru", "russia", "russian", "cis", "asia", "sea", "latam", "latin-america",
                          "india", "tr", "turkey", "cn", "china", "ar", "argentina", "br", "brazil",
                          "jp", "japan", "kr", "korea", "mena", "africa", "za")
GIFT_WORDS = ("gift", "altergift")
ACCOUNT_WORDS = ("account", "accounts", "offline-account", "shared-account")
DLC_WORDS = ("dlc", "season-pass", "expansion", "soundtrack", "upgrade")
PLATFORM_FAMILIES = {  # clé = activationPlatform AllKeyShop, ou son début
    "steam": ("steam",),
    "ea-app": ("ea-app", "eaapp", "ea-play", "origin"),
    "epic": ("epic", "epic-games"),
    "gog": ("gog",),
    "ubisoft": ("ubisoft", "ubisoft-connect", "uplay"),
    "battle-net": ("battle-net", "battlenet", "blizzard"),
    "rockstar": ("rockstar",),
    "microsoft-store": ("microsoft-store", "windows-store", "microsoft"),
    "xbox-play-anywhere": ("xbox-play-anywhere", "play-anywhere", "xbox", "windows"),
    "xbox": ("xbox", "xbox-live"),
    "playstation": ("playstation", "psn", "ps5", "ps4"),
    "nintendo": ("nintendo", "switch"),
}
EDITION_WORDS = ("standard", "deluxe", "digital-deluxe", "ultimate", "gold", "premium", "complete",
                 "definitive", "goty", "game-of-the-year", "collector", "collectors", "legendary",
                 "enhanced", "anniversary", "directors-cut", "silver", "platinum")
EDITION_SYNONYMS = {"goty": "game of the year", "collectors": "collector",
                    "digital-deluxe": "deluxe", "directors-cut": "director s cut"}
# Éditions AllKeyShop génériques (« Bundle », « Bundle 2 », « Bonus », « DLC Bundle »...) : elles ne
# désignent pas une édition précise, on ne les compare pas à celle du marchand (étude du 30/09/2026 :
# Euro Truck Simulator 2 a 22 éditions qui se recoupent).
GENERIC_EDITION_WORDS = {"bundle", "bonus", "pack", "collection", "dlc", "dlcs", "upgrade", "set", "edition", "and", "plus"}
# Plateformes compatibles entre elles : un code Xbox, Xbox Play Anywhere ou Microsoft Store
PLATFORM_GROUPS = {"xbox-play-anywhere": "xbox", "microsoft-store": "xbox"}
CONSOLE_GROUPS = ("xbox", "playstation", "nintendo")
CONSOLE_LABELS = {"xbox": "Xbox", "playstation": "PlayStation", "nintendo": "Nintendo"}
# Plateforme d'après le nom de filtre de la région AllKeyShop (« EA GLOBAL », « STEAM GIFT EU »...)
REGION_PLATFORMS = ((r"^STEAM\b", "steam"), (r"^EA\b", "ea-app"), (r"^ROCKSTAR\b", "rockstar"), (r"^GOG\b", "gog"),
                    (r"^EPIC\b", "epic"), (r"^BATTLENET\b", "battle-net"), (r"^(UBISOFT|UPLAY)\b", "ubisoft"),
                    (r"^WINDOWS\b", "microsoft-store"), (r"XBOX|X\|S", "xbox"), (r"\bPSN\b|PLAYSTATION", "playstation"),
                    (r"NINTENDO", "nintendo"))
# pas « forbidden » ni « blocked » seuls : « Horizon Forbidden West » est un vrai titre
BLOCK_PAGE_MARKERS = ("just a moment", "access denied", "attention required", "blocked -", "- blocked", "captcha",
                      "are you a robot", "are you human", "tut uns leid", "403 forbidden", "robot check", "security check",
                      "pardon our interruption")
BUNDLE_WORDS = ("bundle", "pack", "collection", "trilogy")  # éditions dont le nom diffère par nature
EXTRA_CONTENT_WORDS = ("dlc", "bundle", "pack", "collection", "bonus", "season", "expansion", "soundtrack", "ost")  # éditions qui annoncent du contenu en plus
NOT_A_LANGUAGE = {"pc", "eu", "us", "uk", "na", "ww", "vr", "hd", "ps", "cd", "dl", "xs"}  # codes de 2 lettres qui ne sont pas des langues
# Suffixes plateforme des noms AllKeyShop (« GTA 6 PS5 »), que les marchands omettent souvent
PLATFORM_SUFFIXES = ("ps5", "ps4", "playstation 5", "playstation 4", "xbox series x s", "xbox series x", "xbox series",
                     "xbox one", "xbox", "nintendo switch 2", "switch 2", "nintendo switch", "switch", "pc", "vr")
# Mots qui ne comptent pas pour reconnaître le nom du produit dans une URL
SOFT_WORDS = {"the", "of", "a", "an", "and", "edition", "remastered", "remaster", "remake", "hd", "official", "game",
              "bundle"} | set(EDITION_WORDS)  # « Fable Premium Upgrade Bundle » vendu « Fable Premium Upgrade »
# Abréviations : un mot du nom AllKeyShop et son équivalent chez les marchands, valables dans les deux sens
NAME_ALIASES = (
    ("gta", "grand theft auto"),
    ("cod", "call of duty"),
)
# préfixes d'éditeur que les marchands omettent (« UFC 5 » chez Eneba et GAMIVO pour « EA Sports UFC 5 », 01/10/2026)
# Liste élargie le 02/10/2026 (Romain : un alias par produit ne tient pas avec beaucoup de marchands) : préfixes
# de franchise ou d'éditeur, pas de nom distinctif. Le nom complet reste testé ; la variante sans le préfixe garde
# au moins deux mots et le numéro de l'épisode.
OPTIONAL_PREFIXES = ("ea sports", "call of duty", "the legend of", "warhammer 40k", "warhammer 40 000", "warhammer 40000",
                     "world of", "marvels", "tom clancys", "sid meiers")
# Mots qui distinguent un produit d'un autre : jamais tolérés comme « le mot manquant » d'un nom long
# (le titre du jeu de base ne passe pas pour « Forza Horizon 6 Premium Upgrade Bundle », étude du 30/09/2026)
NEVER_MISSING = {"upgrade", "dlc", "expansion", "season", "pass", "soundtrack", "ost", "demo", "vr", "remake", "remastered"}
# Monnaie de jeu vendue comme le jeu (Romain, 02/10/2026) : l'URL « call-of-duty-black-ops-6-5000-cod-points » contient le
# nom du jeu. Une expression compte toujours ; un mot seul (« coins ») compte avec une quantité (500, 5000…), pas pour
# un bonus (« 2 gold coins » de G2A). Pas de comparaison quand l'édition AllKeyShop est elle-même la monnaie
# (« Standard + Great White Shark Card »). Sur une offre WALLET (prix PS Store via une recharge), les mots de recharge
# sont attendus.
CURRENCY_PHRASES = ("cod-points", "v-bucks", "vbucks", "apex-coins", "fut-points", "fc-points", "fifa-points", "shark-card",
                    "cash-card", "riot-points", "robux", "minecoins", "gift-card", "prepaid-card", "psn-card", "eshop-card",
                    "playstation-network-card", "wallet", "top-up", "topup")
CURRENCY_WORDS = ("points", "coins", "credits", "gems", "tokens", "crystals", "shards")
WALLET_PHRASES = ("wallet", "top-up", "topup", "gift-card", "prepaid-card", "psn-card", "playstation-network-card", "eshop-card")
# Éditions AllKeyShop qui sont elles-mêmes de la monnaie : « Standard + Great White Shark Card », « GTA 5 + Criminal + Megalodon »
# (les Shark Cards de GTA Online sont nommées par leur requin : Megalodon, Whale, Great White, Bull, Tiger, Red)
CURRENCY_EDITION_PHRASES = ("card", "points", "coins", "credits", "bucks", "shark", "cash", "currency", "gems", "tokens", "wallet",
                            "megalodon", "whale", "great-white", "bull-shark", "tiger-shark", "red-shark")
# Autres noms d'un produit (titre européen, titre localisé...), appris au fil de la formation : aliases.toml
ALIASES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aliases.toml")
ROMAN = {1: "i", 2: "ii", 3: "iii", 4: "iv", 5: "v", 6: "vi", 7: "vii", 8: "viii", 9: "ix", 10: "x",
         11: "xi", 12: "xii", 13: "xiii", 14: "xiv", 15: "xv", 16: "xvi", 17: "xvii", 18: "xviii", 19: "xix", 20: "xx"}
ARABIC = {v: str(k) for k, v in ROMAN.items()}

log = logging.getLogger("price-check")


# ---- HTTP --------------------------------------------------------------------

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def request_headers(ua):
    """En-têtes d'une requête. Chez les marchands (UA navigateur), le jeu complet qu'envoie Chrome : Akamai (Kinguin)
    refuse une requête à l'Accept minimal (403) et répond 200, ou 301 vers la fiche servie, au jeu complet
    (Romain, 02/10/2026 : « on a juste besoin de suivre les redirections »). Sur AllKeyShop : UA AKS/Staff, en-têtes simples."""
    headers = {"User-Agent": ua, "Accept": "text/html,*/*;q=0.8", "Accept-Language": "en-GB,en;q=0.9"}
    if ua == BROWSER_UA:
        major = (re.search(r"Chrome/(\d+)", ua) or [None, "150"])[1]
        headers.update({
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Encoding": "identity", "Upgrade-Insecure-Requests": "1", "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "none", "Sec-Fetch-User": "?1",
            "sec-ch-ua": '"Chromium";v="%s", "Not=A?Brand";v="8"' % major, "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Linux"'})
    return headers


def http_get(url, ua, follow=True, timeout=30):
    """Renvoie (statut HTTP, en-tête Location, corps). Un statut d'erreur ne lève pas."""
    req = urllib.request.Request(url, headers=request_headers(ua))
    opener = urllib.request.build_opener() if follow else urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.headers.get("Location"), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Location"), ""


# ---- AllKeyShop : listes, page produit, redirection ---------------------------

PAGE_LISTS = {}  # URL de page -> libellés de toutes les listes où elle figure (une page peut être dans plusieurs)
# Listes où une offre invérifiable mérite quand même une alerte À VÉRIFIER, si elle est le premier prix de la page
TOP_LIST_MARKERS = ("popular", "coming soon", "most anticipated")


def parse_lists(data, lists):
    """Renvoie [(liste, rang, nom, url)] pour chaque liste de `lists`, une seule fois par page.

    `data` : réponse de l'API getLists, {widget: {liste: {items: [...]}}}.
    """
    targets, seen, memberships = [], set(), {}
    for list_id, label, top in lists:
        widget, name = list_id.split(".", 1)
        items = ((data.get(widget) or {}).get(name) or {}).get("items")
        if items is None:
            log.warning("liste %s absente de la réponse de l'API", list_id)
            continue
        games = sorted((i for i in items if i.get("productType") == "game"), key=lambda i: i["index"])
        for rank, item in enumerate(games[:top], 1):
            url = item.get("urls", {}).get(SITE_KEY)
            if url:
                memberships.setdefault(url, set()).add(label)
            if url and url not in seen:
                seen.add(url)
                targets.append((label, rank, item["name"], url))
    PAGE_LISTS.update(memberships)
    return targets


def in_top_or_soon(page_url, label):
    """La page figure-t-elle dans un top (Popular) ou un coming soon (Coming soon, Most anticipated) ?"""
    labels = PAGE_LISTS.get(page_url) or {label}
    return any(marker in l.lower() for l in labels for marker in TOP_LIST_MARKERS)


def unverifiable_verdict(offer, label, page_url, policy="first-price"):
    """Offre qu'on ne peut pas vérifier (ni l'URL ni la page ne donnent le nom) : on prend note, sans alerte
    (formation du 30/09/2026), sauf si c'est vraiment le premier prix de la page, dans un top ou un coming
    soon. `policy` vient de la config marchand : « note » (Amazon : jamais d'alerte), « first-price », « report »."""
    if policy == "report" or (policy == "first-price" and offer.get("page_first") and in_top_or_soon(page_url, label)):
        return "À VÉRIFIER"
    return "NON VÉRIFIABLE"


def fetch_lists(list_ids):
    """Réponse de l'API getLists pour ces listes, par lots : un lot en erreur ne bloque pas les autres."""
    data = {}
    for i in range(0, len(list_ids), LISTS_PER_CALL):
        batch = list_ids[i:i + LISTS_PER_CALL]
        status, _, body = http_get(LISTS_API + "?" + "&".join("lists[]=" + lid for lid in batch), AKS_UA)
        if status == 200:
            for widget, lists in json.loads(body).items():
                data.setdefault(widget, {}).update(lists or {})
        else:
            log.warning("API des listes HTTP %s pour %s", status, ", ".join(batch))
        time.sleep(PAGE_DELAY)
    return data


def fetch_targets(lists):
    return parse_lists(fetch_lists([lid for lid, _, _ in lists]), lists)


GAME_PAGE_RE = re.compile(r"var gamePageTrans = (\{.*?\});\n", re.DOTALL)


def parse_game_page(page_html):
    m = GAME_PAGE_RE.search(page_html)
    if not m:
        raise ValueError("gamePageTrans introuvable")
    return json.loads(m.group(1))


def is_dlc_page(trans, product):
    """La page AllKeyShop est celle d'un DLC : une de ses éditions s'appelle « DLC » (Diablo 4 Lord of
    Hatred Xbox Series, formation du 30/09/2026), ou son nom le dit. Les listes et CatalogV2 typent
    pourtant ces pages « game » sur console."""
    editions = {norm(e.get("name", "")) for e in (trans.get("editions") or {}).values()}
    words = set(norm(product).split("-"))
    return "dlc" in editions or bool(words & {"dlc", "expansion"}) or "season-pass" in norm(product)


def page_offers(trans, per_edition=1):
    """Offres à contrôler sur une page AllKeyShop.

    `per_edition` = n : les n offres de clé les moins chères de chaque édition (priceCard), hors offres
    compte et « sans prix » (mode Top Offers : n = 3) ; None : toutes les offres en vente, comptes compris
    (mode Full Page). Chaque offre porte son rang dans son édition (« edition_rank », compté à part pour
    les comptes) et « page_first » : est-ce le premier prix (clé) de toute la page ?"""
    editions = trans.get("editions") or {}
    regions = trans.get("regions") or {}
    groups = {}  # (édition, compte ?) -> offres, de la moins chère à la plus chère
    for p in trans.get("prices") or []:
        if p.get("price") == NO_PRICE or not p.get("dispo"):
            continue
        if p.get("account") and per_edition is not None:
            continue
        groups.setdefault((str(p["edition"]), bool(p.get("account"))), []).append(p)
    for group in groups.values():
        group.sort(key=lambda p: (p["priceCard"], str(p["id"])))
    cheapest = min((g[0] for (_, account), g in groups.items() if not account),
                   key=lambda p: (p["priceCard"], str(p["id"])), default=None)
    page_editions = [e.get("name", "") for e in editions.values()]
    offers = []
    # les éditions dans l'ordre de leur premier prix, les clés avant les comptes
    for edition, account in sorted(groups, key=lambda k: (k[1], groups[k][0]["priceCard"], k[0])):
        for rank, p in enumerate(groups[(edition, account)][:per_edition], 1):
            region = regions.get(str(p["region"]), {})
            offers.append({
                "id": p["id"], "merchant": p["merchant"], "merchantName": p["merchantName"],
                "edition": editions.get(edition, {}).get("name", edition),
                "region": region.get("region_name", str(p["region"])),
                # le vrai sens de la région : « GERMANY » peut être STEAM GIFT GERMANY (étude du 30/09/2026)
                "region_filter": region.get("filter_name") or "",
                "region_desc": region.get("region_short_description") or "",
                "platform": p.get("activationPlatform") or "",
                "price": p["priceCard"], "account": account,
                "page_editions": page_editions,
                "edition_rank": rank, "page_first": p is cheapest,
            })
    return offers


def first_prices(trans):
    """Offre de clé la moins chère de chaque édition : le contrôle d'origine (Top Offers à un prix)."""
    return page_offers(trans, 1)


APP_DATA_RE = re.compile(r'<script[^>]*id="appData"[^>]*>(.*?)</script>', re.DOTALL)
META_REFRESH_RE = re.compile(r'http-equiv="refresh"\s+content="\d+;\s*URL=([^"]+)"', re.IGNORECASE)


def merchant_url(interstitial):
    """URL marchand de la page « Redirecting... » d'AllKeyShop (JSON appData, sinon meta refresh)."""
    m = APP_DATA_RE.search(interstitial)
    if m:
        try:
            url = json.loads(m.group(1)).get("redirectionUrl")
        except ValueError:
            url = None
        if url:
            return url
    m = META_REFRESH_RE.search(interstitial)
    # pas html.unescape : « &currency=EUR » y deviendrait « ¤cy=EUR » (entité &curren)
    return m.group(1).replace("&amp;", "&") if m else None


def unwrap_affiliate(url):
    """Lien affilié (go.loaded.com/...?u=https://www.loaded.com/...) : la cible est en paramètre."""
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    for key in ("u", "url", "dest", "destination", "redirect", "target", "link", "r"):
        for value in query.get(key, ()):
            if value.startswith("http"):
                return unwrap_affiliate(value)
    return url


# ---- Configs marchands (merchants/*.toml) ------------------------------------

def load_merchant_configs(directory=MERCHANTS_DIR):
    """Un fichier TOML par marchand qui demande une exception. Clés : voir docs/marchands.md."""
    configs = []
    for path in sorted(glob.glob(os.path.join(directory, "*.toml"))):
        with open(path, "rb") as f:
            cfg = tomllib.load(f)
        cfg.setdefault("name", os.path.splitext(os.path.basename(path))[0])
        cfg.setdefault("hosts", [])
        configs.append(cfg)
    return configs


MERCHANT_CONFIGS = load_merchant_configs()


def load_product_aliases(path=ALIASES_PATH):
    """aliases.toml, section [products] : « nom AllKeyShop » = [« autre nom », ...]."""
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except OSError:
        return {}
    return {norm(k): tuple(v) for k, v in (data.get("products") or {}).items()}


_PRODUCT_ALIASES = None


def product_aliases():
    global _PRODUCT_ALIASES
    if _PRODUCT_ALIASES is None:
        _PRODUCT_ALIASES = load_product_aliases()
    return _PRODUCT_ALIASES


def merchant_config(url, merchant_name):
    """La config du marchand (par hôte de l'URL, par nom AllKeyShop ou par début de nom : « name_prefixes »),
    ou {} sans exception."""
    host = urllib.parse.urlparse(url or "").netloc.lower()
    name = norm(merchant_name or "")
    for cfg in MERCHANT_CONFIGS:
        if (any(h in host for h in cfg["hosts"]) or (name and norm(cfg["name"]) == name)
                or (name and any(name.startswith(norm(prefix)) for prefix in cfg.get("name_prefixes", [])))):
            return cfg
    return {}


def out_of_stock_reason(served):
    """Groupe Kinguin : la fiche du lien est en rupture, le marchand sert une autre offre, le prix reste dans le feed."""
    return "offre en rupture chez le marchand : le lien redirige vers une autre fiche (%s), mais le prix reste dans le feed" % served


def redirect_untrusted(cfg):
    """Groupe Kinguin (Romain, 02/10/2026) : chez ce marchand, une redirection mène à UNE AUTRE OFFRE, parce que la
    fiche du lien est en rupture. Elle ne dit rien de l'offre AllKeyShop : on ne s'y fie ni pour l'accuser, ni pour la
    blanchir. L'autre groupe (défaut : Instant Gaming, Fanatical…) redirige vers la fiche actuelle de la même offre."""
    return (cfg.get("redirect") or {}).get("means") == "out-of-stock"


def region_text(url, cfg):
    """Texte où lire la région selon la config : None = le chemin de l'URL (défaut),
    « » = région inconnue (pas de contrôle), sinon le texte à analyser à la place."""
    rc = cfg.get("region") or {}
    if rc.get("from") == "query":
        value = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get(rc.get("param", "region"), [""])[0]
        return rc.get("map", {}).get(value, "")
    if rc.get("from") == "none":
        return ""
    return None


# ---- Analyse d'une URL ou d'un titre marchand --------------------------------

def norm(text):
    """« EA SPORTS FC 27 » -> « ea-sports-fc-27 » ; « S.T.A.L.K.E.R. 2 » -> « stalker-2 »."""
    text = re.sub(r"[\u2122\u00ae\u00a9\u2120]", " ", text)  # ™ ® © ℠ (NFKD ferait de ™ les lettres « TM »)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"(?<=\d)\.(?=\d)", " ", text)  # « HD 1.5+2.5 » -> « 1-5-2-5 », comme les URL (02/10/2026)
    text = re.sub(r"\b(\w)\.", r"\1", text)  # sigle pointé : S.T.A.L.K.E.R. -> STALKER, A.O.T. -> AOT
    return re.sub(r"[^a-z0-9]+", "-", text.lower().replace("&", " and ")).strip("-")


def compact(text):
    return norm(text).replace("-", "")


LOCALE_SEGMENT_RE = re.compile(r"^[a-z]{2}([-_][a-zA-Z]{2})?$")


def url_text(url):
    """Le chemin de l'URL, sans les segments de langue (/en/, /en-us/), avec deux réparations des slugs
    (rejeu du 02/10/2026) : le ™ collé au mot (Eneba « pokemontm ») est retiré, un sigle écrit lettre par
    lettre (GAMIVO « s-t-a-l-k-e-r-2 ») est recollé."""
    segments = [s for s in urllib.parse.urlparse(url).path.split("/") if s and not LOCALE_SEGMENT_RE.match(s)]
    return " ".join(repair_slug(urllib.parse.unquote(s)) for s in segments)


def repair_slug(segment):
    tokens = [t[:-2] if t.lower().endswith("tm") and len(t) >= 6 and t[:-2].isalpha() else t for t in segment.split("-")]
    out, run = [], []
    for t in tokens + [""]:
        if len(t) == 1 and t.isalpha():
            run.append(t)
            continue
        out.extend(["".join(run)] if len(run) >= 3 else run)
        run = []
        if t:
            out.append(t)
    return "-".join(out)


def name_variants(product):
    """Le nom AllKeyShop et ses variantes : abréviations (« GTA 6 PS5 » / « Grand Theft Auto 6 PS5 »)
    et chiffres <-> chiffres romains (« Dungeons 2 » / « Dungeons II »), combinées."""
    names = [product]
    if re.search(r"\d\.\d", product):  # « HD 1.5+2.5 ReMIX » écrit « 15-25 » par certains marchands
        names.append(re.sub(r"(?<=\d)\.(?=\d)", "", product))
    base = norm(product)
    for suffix in PLATFORM_SUFFIXES:  # « GTA 6 PS5 » -> « GTA 6 », « Ragnarock VR » -> « Ragnarock »
        if base.endswith("-" + norm(suffix)) and base != norm(suffix):
            base = base[:-len(norm(suffix)) - 1]
            names.append(base.replace("-", " "))
            break
    m = re.fullmatch(r"(.+)-(20\d\d)", base)  # année de désambiguïsation AllKeyShop : « Screamer 2026 » -> « Screamer »
    if m:
        names.append(m.group(1).replace("-", " "))
    for name in list(names):  # « EA Sports UFC 5 Xbox Series » -> « UFC 5 Xbox Series »
        n = norm(name)
        for prefix in map(norm, OPTIONAL_PREFIXES):
            rest = n[len(prefix) + 1:] if n.startswith(prefix + "-") else ""
            core = rest  # deux mots au moins HORS suffixe de plateforme : pas « Avengers PS5 » pour « Marvel's Avengers PS5 »
            for suffix in PLATFORM_SUFFIXES:
                if core.endswith("-" + norm(suffix)):
                    core = core[:-len(norm(suffix)) - 1]
                    break
            if len([w for w in core.split("-") if w]) >= 2:
                names.append(rest.replace("-", " "))
    for short, long in NAME_ALIASES:
        for name in list(names):
            spaced = " %s " % norm(name).replace("-", " ")
            for a, b in ((short, long), (long, short)):
                if " %s " % a in spaced:
                    names.append(spaced.replace(" %s " % a, " %s " % b).strip())
    for name in list(names):
        names.extend(product_aliases().get(norm(name), ()))
    for name in list(names):
        words = norm(name).split("-")
        roman = " ".join(ROMAN[int(w)] if w.isdigit() and int(w) in ROMAN else w for w in words)
        arabic = " ".join(ARABIC.get(w, w) for w in words)
        for variant in (roman, arabic):
            if norm(variant) != norm(name) and variant not in names:
                names.append(variant)
    for name in list(names):
        # sigle des premiers mots : « Attack on Titan 3 » -> « AOT 3 » (PS Store : « A.O.T. 3 »)
        words = norm(name).split("-")
        run = 0
        while run < len(words) and words[run].isalpha() and words[run] not in ARABIC:  # pas de chiffre romain
            run += 1
        for k in range(3, run + 1):
            names.append(" ".join(["".join(w[0] for w in words[:k])] + words[k:]))
        # sigle de tous les mots après le premier : « Onimusha Way of the Sword » -> « Onimusha WotS » (PS Store,
        # 01/10/2026). Deux mots au plus, donc jamais de mot toléré absent ; jamais sur un nom à suffixe de
        # plateforme, ni sur un mot distinctif (« Premium Upgrade Bundle » ne devient pas « pub »)
        tail = words[1:]
        if (len(tail) >= 3 and all(w.isalpha() and w not in ARABIC for w in tail) and not has_platform_suffix(name)
                and not set(tail) & (NEVER_MISSING | set(EDITION_WORDS) | set(BUNDLE_WORDS) | GENERIC_EDITION_WORDS)):
            names.append(" ".join([words[0], "".join(w[0] for w in tail)]))
    return tuple(dict.fromkeys(names))


def near(a, b):
    """Deux mots à une lettre près (« pokmon » pour « pokemon » : l'accent mangé par Dreamgame)."""
    if abs(len(a) - len(b)) > 1 or a == b:
        return a == b
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    short, long = (a, b) if len(a) < len(b) else (b, a)
    return any(long[:i] + long[i + 1:] == short for i in range(len(long)))


def has_word(word, tokens):
    return word in tokens or (len(word) >= 6 and not word.isdigit() and any(len(t) >= 5 and near(word, t) for t in tokens))


def name_match(names, normed):
    """« exact » si un des noms est dans le texte, « partial » si ses mots significatifs y sont.

    Sur un nom long (4 mots significatifs ou plus), un seul mot peut manquer, sauf un nombre :
    Amazon tronque (« Zelda-Kingdom-Collector »), mais « Modern Warfare 3 » n'est pas « Modern Warfare 4 ».
    """
    compact_text = normed.replace("-", "")
    for name in names:
        c = compact(name)
        if c and c in compact_text:
            return "exact"
    tokens = set(normed.split("-"))
    for name in names:
        significant = [w for w in norm(name).split("-") if w and w not in SOFT_WORDS]
        missing = [w for w in significant if w not in tokens]
        if len(significant) >= 3 and len(missing) == 1 and has_word(missing[0], tokens):
            missing = []  # un seul mot à une lettre près, sur un nom d'au moins 3 mots (« pokmon ») ; pas « Portal 2 » / « mortal »
        if significant and not missing:
            return "partial"
        if (len(significant) >= 4 and len(missing) == 1 and not (missing[0].isdigit() or missing[0] in ARABIC)
                and missing[0] not in NEVER_MISSING and not has_platform_suffix(name)):
            # la tolérance vaut pour le nom sans « Nintendo Switch » : sinon « Pokémon Bouclier »
            # passerait pour « Pokemon Sword Nintendo Switch » (étude du 30/09/2026)
            return "partial"
    return None


# Mots de service des URL et des titres marchands, sans valeur pour dire quel produit est vendu
LABEL_NOISE = {"buy", "cheap", "acheter", "kaufen", "comprar", "key", "keys", "cd", "cdkey", "code", "codes", "digital",
               "steam", "pc", "global", "europe", "eu", "row", "ww", "gift", "altergift", "xbox", "live", "series", "one",
               "ps5", "ps4", "psn", "playstation", "nintendo", "switch", "eshop", "epic", "gog", "origin", "app", "ea",
               "games", "game", "jeux", "jeu", "spiele", "giochi", "juegos", "download", "software", "telecharger", "a",
               "sur", "product", "products", "category", "p", "html", "htm", "store", "instant", "en", "fr", "de", "it",
               "es", "gb", "us", "uk", "card", "dp", "gp", "ref", "the-game", "windows", "microsoft", "account", "s",
               "rockstar", "ubisoft", "uplay", "connect", "battle", "net", "battlenet", "social", "club",
               "bundle", "bundles", "sub", "preorder", "page", "pages"}  # « bundle/27059 », « preorder-page »


def merchant_label(text, source):
    """Le produit que le texte marchand nomme, pour le dire dans l'alerte (« Metal Garden »), ou None.

    URL : le dernier segment qui contient des mots, sans identifiants ni mots de service.
    Titre : sa première partie (« Sonic the Hedgehog - Epic Games Store » -> « Sonic the Hedgehog »)."""
    if not text:
        return None
    if source.startswith("titre"):
        first = re.split(r"\s+\|\s+|\s+[-\u2013\u2014]\s+", text.strip())[0].strip()
        return first[:80] or None
    for segment in reversed(text.split(" ")):
        if "=" in segment or segment.lower().startswith("ref"):
            continue  # segment de suivi (Amazon : « ref=as_li_tl »)
        segment = re.sub(r"\.(html?|php|aspx?)$", "", segment)
        words = [w for w in re.split(r"[-_]+", segment) if w]
        kept = [w for w in words if w.lower() not in LABEL_NOISE
                and not (any(c.isdigit() for c in w) and (len(w) >= 5 or any(c.isalpha() for c in w) and len(w) >= 4))]
        if sum(len(w) for w in kept if w.isalpha()) >= 4:
            label = " ".join(w if any(c.isupper() for c in w) else w.capitalize() for w in kept)
            return label[:80]
    return None


def has_platform_suffix(name):
    n = norm(name)
    return any(n.endswith("-" + norm(sfx)) for sfx in PLATFORM_SUFFIXES)


def title_match(names, text):
    """Un titre de page court, entièrement contenu dans le nom AllKeyShop (« UFC 5 » pour
    « EA Sports UFC 5 PS5 »), avec au moins deux mots significatifs : « partial »."""
    product_tokens = {w for name in names for w in norm(name).split("-")}
    for segment in re.split(r"\s[|\-\u2013\u2014]\s|\|", text):
        tokens = [w for w in norm(segment).split("-") if w and w not in SOFT_WORDS]
        if len(tokens) >= 2 and all(w in product_tokens for w in tokens):
            return "partial"
    return None


def alternate_url(page_html, hreflang):
    """Le lien <link rel="alternate" hreflang="..."> d'une page (Nintendo : version anglaise)."""
    for tag in re.findall(r"<link[^>]+>", page_html):
        if re.search(r'hreflang="%s"' % re.escape(hreflang), tag, re.IGNORECASE):
            m = re.search(r'href="([^"]+)"', tag)
            if m:
                return html.unescape(m.group(1))
    return None


def drop_language_lists(tokens):
    """Retire les listes de langues des URL (GAMIVO : « en-de-fr-ru-zh-es ») : 3 codes de 2 lettres ou plus à la suite."""
    out, run = [], []
    for t in tokens + [None]:
        if t is not None and len(t) == 2 and t.isalpha() and t not in NOT_A_LANGUAGE:
            run.append(t)
            continue
        if len(run) < 3:
            out.extend(run)
        run = []
        if t is not None:
            out.append(t)
    return out


def region_text_of(offer):
    return " ".join(x for x in (offer.get("region_filter"), offer.get("region")) if x).upper()


def is_gift_region(offer):
    return "GIFT" in region_text_of(offer)


def aks_zone(offer):
    """Zone de la région AllKeyShop, d'après son nom de filtre (« STEAM EU », « XBOX GERMANY CODE »...).
    Un gift n'a pas de zone comparée (formation du 30/09/2026, K4G Screamer 2026). None = inconnue."""
    t = region_text_of(offer)
    if not t or "GIFT" in t:
        return None
    if re.search(r"\bROW\b|REST OF", t):
        return "ROW"
    if "EMEA" in t:
        return "EMEA"
    if "EU/US" in t:
        return "EUUS"
    if re.search(r"\bEU\b|EUROPE", t):
        return "EU"
    for pattern, zone in ((r"GERMAN|\bWALLET DE\b", "DE"), (r"FRANCE|\bWALLET FR\b", "FR"), (r"ITALY|\bWALLET IT\b", "IT"),
                          (r"SPAIN|SPANISH|\bWALLET SP\b", "ES"), (r"PORTUGAL|\bWALLET PT\b", "PT"), (r"BELGIUM", "BE"),
                          (r"POLAND", "PL"), (r"\bUSA\b|\bWALLET US\b", "US"), (r"\bUK\b|UNITED KINGDOM", "UK")):
        if re.search(pattern, t):
            return zone
    # « IN ENGLISH ONLY », « EN/FR » : restriction de langue, clé mondiale (formation du 30/09/2026)
    if re.search(r"GLOBAL|WORLDWIDE|REGION FREE|ENGLISH|ENG ONLY|EN ONLY|EN/FR|^XBOX/PC$|^XBOX X\|S$", t):
        return "GLOBAL"
    return None


def merchant_zones(words):
    return {z for z, ws in MERCHANT_ZONE_WORDS.items() if any(re.search(r"(^|-)%s(-|$)" % re.escape(w), words) for w in ws)}


def zone_coverage(zones):
    return frozenset().union(*(ZONE_COVERAGE[z] for z in zones)) if zones else frozenset()


def region_family(region_name):  # compatibilité (anciens appels)
    return aks_zone({"region": region_name})


def platform_family(platform):
    if platform in PLATFORM_FAMILIES:
        return platform
    for key in sorted(PLATFORM_FAMILIES, key=len, reverse=True):
        if platform.startswith(key):
            return key
    return None


def platform_group(family):
    return PLATFORM_GROUPS.get(family, family)


def aks_platform_groups(offer):
    """Plateformes de l'offre AllKeyShop : activationPlatform et nom de filtre de la région."""
    groups = set()
    fam = platform_family(offer.get("platform") or "")
    if fam:
        groups.add(platform_group(fam))
    t = " ".join(x for x in (offer.get("region_filter"), offer.get("region_desc")) if x).upper()
    for pattern, fam in REGION_PLATFORMS:
        if re.search(pattern, t):
            groups.add(platform_group(fam))
            break
    return groups


def text_platform_groups(normed):
    return {platform_group(f) for f, ws in PLATFORM_FAMILIES.items()
            if any(re.search(r"(^|-)%s(-|$)" % re.escape(w), normed) for w in ws)}


def page_console(product):
    """Console de la page AllKeyShop, d'après le suffixe de son nom (« Elden Ring Xbox Series »)."""
    n = norm(product)
    if re.search(r"-(ps5|ps4|playstation-[45])$", n):
        return "playstation"
    if re.search(r"-xbox(-series(-x(-s)?)?|-one)?$", n):
        return "xbox"
    if re.search(r"-(nintendo-)?switch(-2)?$", n):
        return "nintendo"
    return None


def canonical_edition(text):
    """« GOTY » et « Game of the Year » -> « gameoftheyear »."""
    c = compact(text)
    for word, full in EDITION_SYNONYMS.items():
        c = c.replace(compact(word), compact(full))
    return c


def edition_matches(edition_name, url_editions):
    """Vrai si une édition nommée par le marchand est celle d'AllKeyShop (« deluxe » dans « Deluxe + Bonus »)."""
    aks = canonical_edition(edition_name)
    return any(canonical_edition(w) in aks for w in url_editions)


def is_generic_edition(edition_name):
    tokens = [t for t in norm(edition_name).split("-") if t]
    return bool(tokens) and all(t in GENERIC_EDITION_WORDS or t.isdigit() for t in tokens)


def is_base_edition(edition_name):
    """Édition de base : sans mot d'édition supérieure (« Standard », « Preorder bonus », « Early Access »)."""
    aks = canonical_edition(edition_name)
    return not any(canonical_edition(w) in aks for w in EDITION_WORDS if w != "standard")


def edition_reason(offer, merchant_editions, words):
    """Raison de SUSPECT sur l'édition, ou None. L'écart compte quand l'offre aurait pu être rangée dans une
    autre édition de la page, même si l'acheteur reçoit plus (arbitrage du 01/10/2026 : GTA 4, Complete
    Edition rangée en Standard, et STAR WARS Zero Company, Deluxe rangée en « Standard + DLC », sont de
    vraies erreurs ; la règle « édition supérieure sous édition de base : pas d'alerte » du matin est
    annulée), ou quand l'édition affichée est supérieure à celle vendue : l'acheteur reçoit moins."""
    aks = offer["edition"]
    if not merchant_editions or edition_matches(aks, merchant_editions):
        return None
    # un mot propre à l'édition AllKeyShop présent chez le marchand : « 2024 Edition », « Mediterranean Bundle »
    own = [t for t in norm(aks).split("-") if t and t not in GENERIC_EDITION_WORDS and t not in EDITION_WORDS
           and (len(t) >= 3 or (t.isdigit() and len(t) == 4))]
    if any(re.search(r"(^|-)%s(-|$)" % re.escape(t), words) for t in own):
        return None
    if is_generic_edition(aks):
        return None
    if is_base_edition(aks) and set(merchant_editions) <= {"standard"}:
        return None  # « Preorder bonus » vendu « standard pre-purchase »
    # l'offre aurait dû être rangée dans une autre édition de la page (GTA 4 : la Complete Edition de Steam
    # rangée en Standard alors que la page a une édition Complete)
    others = [e for e in offer.get("page_editions") or [] if e != aks and edition_matches(e, merchant_editions)]
    if others:
        return "édition : rangée en %s, le marchand vend %s (la page a une édition %s)" % (
            aks, ", ".join(merchant_editions), others[0])
    if not is_base_edition(aks):
        return "édition : AllKeyShop %s, marchand %s" % (aks, ", ".join(merchant_editions))
    return None  # édition de base affichée, la page n'a pas l'édition vendue : rien de mieux où la ranger


def year_pass_edition(edition_name, words):
    """« Year 1 Edition » : le jeu + le season pass de l'année 1. Chez le marchand, « year-1-season-pass » est
    alors cette édition, pas un DLC seul (arbitrage du 01/10/2026, Farming Simulator 25 chez Loaded : « le
    jeu est bien inclus »)."""
    m = re.search(r"(?:^|-)year-(\d+)(?:-|$)", norm(edition_name))
    return bool(m) and re.search(r"(?:^|-)year-%s-season-pass(?:-|$)" % m.group(1), words) is not None


def currency_reason(offer, words, normed):
    """Raison de SUSPECT « monnaie de jeu », ou None (voir CURRENCY_PHRASES). Les expressions se cherchent dans tout le
    texte (`normed` : « cod-points » contient « cod », un alias du nom du jeu), les mots seuls hors des mots du nom
    (`words` : « Tarot Tokens » est un jeu)."""
    edition = norm(offer["edition"])
    if any(re.search(r"(^|-)%s(-|$)" % p, edition) for p in CURRENCY_EDITION_PHRASES):
        return None
    wallet = "WALLET" in ("%s %s" % (offer.get("region_filter") or "", offer.get("region") or "")).upper()
    found = [p for p in CURRENCY_PHRASES if re.search(r"(^|-)%s(-|$)" % re.escape(p), normed) and not (wallet and p in WALLET_PHRASES)]
    numbers = [int(w) for w in words.split("-") if w.isdigit() and len(w) >= 3]
    if any(n >= 100 and not 1980 <= n <= 2035 for n in numbers):  # une quantité, pas une année
        found += [w for w in CURRENCY_WORDS if re.search(r"(^|-)%s(-|$)" % w, words) and not any(w in p for p in found)]
    return "monnaie de jeu chez le marchand : " + ", ".join(found) if found else None


def announces_extra_content(edition_name):
    words = set(norm(edition_name).split("-"))
    return any(w in words for w in EXTRA_CONTENT_WORDS)


def is_bundle(edition_name):
    words = set(norm(edition_name).split("-"))
    return any(w in words for w in BUNDLE_WORDS)


def analyze(product, offer, text, source, region=None):
    """Confronte un texte marchand (chemin d'URL ou titre de page) à l'offre AllKeyShop.

    `region` : texte où lire la région à la place de `text` (config marchand), « » = inconnue.
    Renvoie {"match": "exact" | "partial" | None, "reasons": [...], "kinds": [...], "notes": [...]}.
    Chaque raison est un motif de SUSPECT ; « kinds » donne sa nature (name, zone, platform...).
    """
    names = name_variants(product)
    normed = norm(text)
    match = name_match(names, normed)
    # Le reste s'analyse sans les mots du nom du produit (« Complete Edition Remastered »...)
    product_words = {w for name in names for w in norm(name).split("-")}
    words = "-".join(drop_language_lists([w for w in normed.split("-") if w and w not in product_words]))
    region_words = words if region is None else "-".join(drop_language_lists(norm(region).split("-")))

    def has(word, where=None):
        return re.search(r"(^|-)%s(-|$)" % re.escape(word), words if where is None else where) is not None

    reasons, kinds, notes = [], [], []
    result_label = None

    def reason(kind, message):
        reasons.append(message)
        kinds.append(kind)

    if match is None and source == "titre de la page":
        match = title_match(names, text)
        if match:
            notes.append("titre court contenu dans le nom")
    if match is None and is_bundle(offer["edition"]):
        notes.append("édition %s : nom non contrôlé" % offer["edition"])  # un bundle porte un autre nom
    elif match is None:
        label = merchant_label(text, source)
        result_label = label
        if label:  # le marchand nomme un produit, mais pas celui-là (TORO 2 -> « Metal Garden »)
            reason("name", "autre produit chez le marchand : « %s » au lieu de « %s » (%s)" % (label, product, source))
        else:
            reason("name", "nom du produit introuvable (%s)" % source)
    elif match == "partial":
        notes.append("nom partiel")
    if not offer["account"] and any(has(w) for w in ACCOUNT_WORDS):
        reason("account", "compte chez le marchand, saisi en clé")
    forbidden = [w for w in FORBIDDEN_REGION_WORDS if has(w, region_words)]
    if forbidden:
        reason("zone", "région interdite : " + ", ".join(forbidden))
    zone = aks_zone(offer)
    found = merchant_zones(region_words)
    if zone and found and not ZONE_COVERAGE[zone] <= zone_coverage(found):
        reason("zone", "région : AllKeyShop %s, marchand %s" % (offer["region"], "/".join(sorted(found))))
    if any(has(w) for w in GIFT_WORDS) and not is_gift_region(offer):
        reason("gift", "gift chez le marchand, affiché en clé %s" % offer["region"])
    # plateforme : sur tous les mots, car « Xbox Series » fait partie du nom AllKeyShop et de l'URL
    aks_groups = aks_platform_groups(offer)
    url_groups = text_platform_groups(normed)
    if aks_groups and url_groups and not aks_groups & url_groups:
        reason("platform", "plateforme : AllKeyShop %s, marchand %s" % (
            offer["platform"] or "/".join(sorted(aks_groups)), "/".join(sorted(url_groups))))
    else:
        console = page_console(product)
        url_consoles = url_groups & set(CONSOLE_GROUPS)
        if console and url_consoles and console not in url_consoles:
            reason("console", "plateforme : page AllKeyShop %s, marchand %s" % (
                CONSOLE_LABELS[console], "/".join(CONSOLE_LABELS[c] for c in sorted(url_consoles))))
    merchant_editions = [w for w in EDITION_WORDS if has(w)]
    er = edition_reason(offer, merchant_editions, words)
    if er:
        reason("edition", er)
    dlc = [w for w in DLC_WORDS if has(w)]
    # « pre-order-bonus-dlc » est le bonus vendu avec le jeu ; « Standard + DLC Bundle » l'annonce ;
    # sur la page d'un DLC (édition « DLC » présente), le mot est attendu
    if (dlc and not has("bonus") and not announces_extra_content(offer["edition"]) and not offer.get("page_dlc")
            and not year_pass_edition(offer["edition"], words)):
        reason("dlc", "contenu additionnel : " + ", ".join(dlc))
    cr = currency_reason(offer, words, normed)
    if cr:
        reason("currency", cr)
    return {"match": match, "reasons": reasons, "kinds": kinds, "notes": notes, "label": result_label,
            "zones": sorted(found), "platforms": sorted(url_groups), "editions": merchant_editions}


def confirmed(kind, product, offer, page_text):
    """La page marchand dit-elle elle aussi ce que disait l'URL (autre plateforme, zone plus étroite) ?"""
    normed = norm(page_text)
    if kind == "platform":
        return bool(text_platform_groups(normed) - aks_platform_groups(offer))
    if kind == "console":
        return bool((text_platform_groups(normed) & set(CONSOLE_GROUPS)) - {page_console(product)})
    if kind == "zone":
        zone = aks_zone(offer)
        found = merchant_zones(normed)
        return bool(zone and found) and not ZONE_COVERAGE[zone] <= zone_coverage(found)
    if kind == "dlc":
        return any(re.search(r"(^|-)%s(-|$)" % w, normed) for w in DLC_WORDS) and not game_plus_content(product, page_text)
    return False


def game_plus_content(product, page_text):
    """La page vend-elle le jeu PLUS un contenu (« Grand Theft Auto V + Criminal Enterprise Starter Pack DLC »,
    Keycense, étude du 01/10/2026) ? Le nom du produit avant un « + » d'une partie du titre."""
    names = name_variants(product)
    return any(name_match(names, norm(part.split("+", 1)[0])) for part in (page_text or "").split(" | ") if "+" in part)


def contradicted(kind, product, offer, page_text):
    """La page marchand contredit-elle l'URL sur ce point ? Il faut qu'elle dise explicitement ce
    qu'affiche AllKeyShop (Gamingdragons : URL « steam-key », page « PC - EA App ») ; une page muette
    ne contredit rien."""
    normed = norm(page_text)
    if kind == "platform":
        page_groups = text_platform_groups(normed)
        return bool(page_groups) and page_groups <= aks_platform_groups(offer)
    if kind == "console":
        page_consoles = text_platform_groups(normed) & set(CONSOLE_GROUPS)
        return page_consoles == {page_console(product)}
    if kind == "zone":
        zone = aks_zone(offer)
        found = merchant_zones(normed)
        # une seule zone, sans ambiguïté : un menu « Global / Europe / ROW » ne contredit rien
        return bool(zone) and len(found) == 1 and ZONE_COVERAGE[zone] <= zone_coverage(found)
    if kind == "dlc":
        return game_plus_content(product, page_text)
    return False


# ---- Page marchand (dernier repli) -------------------------------------------

TITLE_RES = (
    re.compile(r"<title[^>]*>(.*?)</title>", re.DOTALL | re.IGNORECASE),
    re.compile(r'property="og:title"\s+content="([^"]*)"', re.IGNORECASE),
    re.compile(r'content="([^"]*)"\s+property="og:title"', re.IGNORECASE),
    re.compile(r"<h1[^>]*>(.*?)</h1>", re.DOTALL | re.IGNORECASE),
)


# Champs « Région / Plateforme » du corps de la page marchand (K4G : « PLATFORM Steam REGION Global »,
# alors que son slug dit « playstation-5-europe », étude du 30/09/2026) : en majuscules, ou suivis de « : »
PAGE_FACT_RE = re.compile(r"(?:\b(REGION|PLATFORM)\b|\b(Region|Platform)\s*:)\s*(?:Loading\.\.\.\s*)?([^|]{1,24})")


def page_facts(dom):
    """« REGION Global | PLATFORM Steam » : les champs région et plateforme lus dans le corps de la page."""
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", dom, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text)))
    facts = []
    for upper, colon, value in PAGE_FACT_RE.findall(text):
        fact = "%s %s" % ((upper or colon).upper(), value.strip())
        if fact not in facts:
            facts.append(fact)
    return facts[:4]


def page_title_from_html(dom):
    """« <title> | og:title | h1 » de la page, chaque partie séparée par « | », puis ses champs région et plateforme."""
    parts = []
    for rx in TITLE_RES:
        m = rx.search(dom)
        if m:
            part = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(m.group(1)))).strip()
            if part and part not in parts:
                parts.append(part)
    if parts:
        parts += page_facts(dom)
    return " | ".join(parts) or None


def is_block_page(text):
    """Page anti-robot (Cloudflare, Akamai, captcha Amazon) : le titre ne dit rien du produit."""
    first = (text or "").split(" | ")[0].strip().lower()
    return not first or len(norm(first)) < 4 or any(m in first for m in BLOCK_PAGE_MARKERS) or first in ("amazon.fr", "amazon.de", "amazon.it", "amazon.es")


def playstation_text(page_html, url):
    """PS Store : nom du produit et libellé d'édition, dans le JSON de la page
    (« Crimson Desert Enhanced » + « Standard Edition »)."""
    product_id = urllib.parse.urlparse(url).path.rstrip("/").split("/")[-1]
    m = re.search(r'"Product:%s":\{.*?"edition":\{"__typename":"ProductEdition","name":"([^"]*)"\},"name":"([^"]*)"' % re.escape(product_id),
                  page_html, re.DOTALL)
    if not m:
        return None
    edition, name = (json.loads('"%s"' % x) for x in m.groups())
    return "%s | %s" % (name, edition) if edition else name


SELECTED_OPTION_RE = re.compile(r'<(button|div|li|a|label)\b[^>]*\baria-(?:checked|selected)="true"[^>]*>(.*?)</\1>', re.DOTALL)


def selected_option_text(dom):
    """Page multi-produits (LDShop : Standard, Deluxe, Premium Upgrade sur une même page) : le libellé de
    l'option choisie par le lien (aria-checked / aria-selected), ajouté au titre. Parmi les options cochées
    (produit, mode d'achat, devise…), celle qui reprend les mots du titre : le produit, pas un prix."""
    title = page_title_from_html(dom) or ""
    title_words = {w for w in norm(title).split("-") if len(w) >= 4 and not w.isdigit()}
    best, best_score = None, 0
    for _, inner in SELECTED_OPTION_RE.findall(dom):
        label = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", inner))).strip()
        label = re.split(r"\s+(?:From|Dès|Ab|Da)\s", label)[0].strip()
        score = len({w for w in norm(label).split("-") if len(w) >= 4} & title_words)
        if score > best_score:
            best, best_score = label[:100], score
    return best


def page_text_from_dom(dom, url, parser):
    if parser == "playstation":
        return playstation_text(dom, url) or page_title_from_html(dom)
    text = page_title_from_html(dom)
    if parser == "selected-option" and text:
        option = selected_option_text(dom)
        if option:
            text = "%s | option choisie : %s" % (text, option)
    return text


def merchant_page_text(url, cfg):
    """Titre de la page marchand : HTTP simple d'abord (UA navigateur), puis Chromium si la config le
    permet. La config peut réécrire l'URL (PS Store : version en-gb, pour un titre en anglais).
    Renvoie (texte, méthode), ou (None, None) si la page est illisible."""
    page_cfg = cfg.get("page") or {}
    if page_cfg.get("locale_from"):
        url = re.sub(page_cfg["locale_from"], page_cfg.get("locale_to", ""), url)
    try:
        status, _, body = http_get(url, BROWSER_UA)
    except OSError:
        status, body = None, ""
    time.sleep(REQUEST_DELAY)
    if status == 200 and body:
        text = page_text_from_dom(body, url, page_cfg.get("parser"))
        # LDShop (option cochée) : le HTML servi en HTTP n'a pas les options, rendues en JavaScript : Chromium
        if text and not is_block_page(text) and not (page_cfg.get("parser") == "selected-option" and "option choisie" not in text):
            return text, "page (HTTP)"
    if cfg.get("browser", True):
        text = page_title(url, page_cfg.get("parser"))
        if text and not is_block_page(text):
            return text, "page (Chromium)"
    return None, None


def chromium_dom(url):
    """DOM de la page marchand rendu par Chromium sans écran, ou None si impossible."""
    if not shutil.which(CHROMIUM):
        return None
    cmd = [CHROMIUM, "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
           "--user-agent=" + BROWSER_UA, "--virtual-time-budget=20000", "--dump-dom", url]
    try:
        return subprocess.run(cmd, capture_output=True, timeout=90).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.TimeoutExpired):
        return None


def page_title(url, parser=None):
    """Titre, og:title et h1 de la page marchand, via Chromium sans écran. None si impossible."""
    dom = chromium_dom(url)
    return None if dom is None else page_text_from_dom(dom, url, parser)


CANONICAL_RE = re.compile(r'<link\b(?=[^>]*\brel=["\']canonical["\'])[^>]*\bhref=["\']([^"\']+)["\']', re.IGNORECASE)


def canonical_url(dom):
    m = CANONICAL_RE.search(dom or "")
    return html.unescape(m.group(1)) if m else None


def page_canonical(url, cfg):
    """URL canonique de la page marchand (HTTP simple, puis Chromium si la config le permet), ou None."""
    try:
        status, _, body = http_get(url, BROWSER_UA)
    except OSError:
        status, body = None, ""
    time.sleep(REQUEST_DELAY)
    if status == 200 and body and not is_block_page(page_title_from_html(body) or ""):
        return canonical_url(body)  # page lue : sa fiche canonique, ou aucune
    return canonical_url(chromium_dom(url)) if cfg.get("browser", True) else None


def moved(url, served):
    """La fiche servie (redirection ou URL canonique) est-elle une autre que celle du lien ? Même chemin aux segments
    de langue près : Kinguin répond 301 de « /en/category/360568/… » vers « /category/360568/… », c'est la même fiche."""
    if not served:
        return False
    return norm(url_text(url)) != norm(url_text(urllib.parse.urljoin(url, served)))


def offer_signature(result):
    """Ce qu'une URL dit de l'offre : nom reconnu, zones, plateformes, éditions (hors « standard ») et écarts relevés.
    Deux URL de même signature désignent la même offre, au nom près."""
    return (result["match"] is not None, tuple(result.get("zones", ())), tuple(result.get("platforms", ())),
            tuple(e for e in result.get("editions", ()) if e != "standard"), frozenset(result["kinds"]) - {"name", "stock"})


def flag_out_of_stock(result, product, offer, served):
    """Groupe Kinguin : le lien mène à une autre fiche. Si elle dit la même chose de l'offre (nom, région, plateforme,
    édition), Kinguin a seulement renommé sa fiche (« dayz-eu-steam-altergift » -> « dayz-eu-pc-steam-altergift » :
    24 des 25 redirections en mémoire le 02/10/2026) : une note. Sinon, c'est une autre offre servie à la place d'une
    fiche en rupture : alerte « en rupture, le prix reste dans le feed » (Romain, 02/10/2026 ; Stellaris : la clé EU
    du lien remplacée par la globale, Rust : « eu » -> « de »). La région n'est plus reprochée quand la fiche servie,
    ce que l'acheteur obtient, correspond à l'affichage (Stellaris, 01/10/2026)."""
    again = analyze(product, offer, url_text(served), "URL de la fiche servie")
    if offer_signature(again) == offer_signature(result):
        result["notes"].append("fiche renommée chez le marchand : %s" % served)
        return
    if "zone" in result["kinds"]:
        if again["match"] and "zone" not in again["kinds"]:
            kept = [(r, k) for r, k in zip(result["reasons"], result["kinds"]) if k != "zone"]
            result["reasons"], result["kinds"] = [r for r, _ in kept], [k for _, k in kept]
            result["notes"].append("région lue sur la fiche servie, qui correspond à l'affichage")
    result["reasons"].append(out_of_stock_reason(served))
    result["kinds"].append("stock")


# ---- Contrôle d'une offre ----------------------------------------------------

def unverified_reason(offer, url):
    """Pourquoi l'offre n'a pas pu être vérifiée, en disant ce que nomme l'URL : jamais « URL sans nom »
    quand elle nomme quelque chose (formation du 01/10/2026 sur TORO 2, revu le 02/10 sur World of Warcraft:
    Forever chez Driffle, dont l'URL « warcraft-forever-… » n'était pas contrôlée, édition « Heroic Pack »)."""
    label = merchant_label(url_text(url), "URL")
    if is_bundle(offer["edition"]):
        why = "édition %s : nom non contrôlé dans l'URL" % offer["edition"]
    else:
        why = "nom du produit introuvable dans l'URL"
    return why + (" (elle nomme « %s »)" % label if label else "") + ", page marchand illisible"


class CheckError(Exception):
    """Contrôle impossible pour l'instant (réseau, redirection AllKeyShop en erreur) : à réessayer."""


def check_offer(product, offer):
    """Suit la redirection AllKeyShop de l'offre et confronte l'URL marchand au produit.

    Renvoie {"verdict", "reasons", "notes", "url", "method"}.
    """
    for attempt in (1, 2):
        try:
            status, _, body = http_get(REDIRECTION_URL % (offer["id"], offer["merchant"]), AKS_UA)
        except OSError as e:
            raise CheckError("redirection AllKeyShop : %s" % e)
        if status < 500 or attempt == 2:
            break
        time.sleep(REQUEST_DELAY * 3)  # 503 passager de la redirection AllKeyShop : un second essai
    time.sleep(REQUEST_DELAY)
    if status != 200:
        raise CheckError("redirection AllKeyShop HTTP %s" % status)
    url = merchant_url(body)
    if not url:
        raise CheckError("URL marchand introuvable dans la page de redirection")
    url = unwrap_affiliate(url)
    cfg = merchant_config(url, offer["merchantName"])
    result, method = analyze(product, offer, url_text(url), "URL", region=region_text(url, cfg)), "URL"
    if (cfg.get("region") or {}).get("from") == "query":
        result["notes"].append("région lue dans le paramètre %s de l'URL" % (cfg["region"].get("param", "region")))

    hreflang = (cfg.get("product_name") or {}).get("hreflang")
    if result["match"] is None and hreflang:
        # boutique localisée (Nintendo eShop FR/IT/DE) : le nom se contrôle sur la version anglaise de la page
        try:
            _, _, page = http_get(url, BROWSER_UA)
        except OSError:
            page = ""
        time.sleep(REQUEST_DELAY)
        alt = alternate_url(page, hreflang)
        if alt:
            result = analyze(product, offer, url_text(alt), "URL de la version %s" % hreflang, region=region_text(alt, cfg))
            result["notes"].append("nom contrôlé sur %s" % alt)
            method = "URL de la version %s" % hreflang
            return {"verdict": "SUSPECT" if result["reasons"] else "OK", "url": url, "method": method,
                    "reasons": result["reasons"], "notes": result["notes"]}

    location = None
    if redirect_untrusted(cfg) or result["match"] is None:
        # Une requête chez le marchand sans suivre la redirection. Groupe Kinguin : à CHAQUE offre, une redirection vers
        # une autre fiche = la fiche du lien est en rupture, le prix reste dans le feed (Romain, 02/10/2026 : « on a juste
        # besoin de suivre les redirections… si on voit une redirection vers une URL différente, on lance l'alerte »).
        # Autre groupe : seulement quand l'URL ne nomme pas le produit (Instant Gaming « /en/4860-/ », Fanatical), ou en
        # nomme un autre (slug périmé) : c'est l'URL finale qui compte.
        try:
            _, location, _ = http_get(url, BROWSER_UA, follow=False)
        except OSError:
            location = None
        time.sleep(REQUEST_DELAY)
    if location:
        url2 = unwrap_affiliate(urllib.parse.urljoin(url, location))
        if redirect_untrusted(cfg):
            if moved(url, url2):
                flag_out_of_stock(result, product, offer, url2)
        elif result["match"] is None:
            result2 = analyze(product, offer, url_text(url2), "URL après redirection du marchand", region=region_text(url2, cfg))
            if result2["match"] or result2.get("label"):  # la fiche finale nomme le produit, ou un autre
                result, method, url = result2, "URL après 301 marchand", url2

    if (result["match"] is None and result.get("label") and not (cfg.get("page") or {}).get("parser")
            and not cfg.get("localized") and not (cfg.get("product_name") or {}).get("hreflang")):
        # l'URL (finale) nomme un autre produit (Titanfall chez Kinguin, TORO 2 -> « Metal Garden ») : l'alerte part
        # sans lire la page (Romain, 02/10/2026 : « je vois pas pourquoi tu veux vérifier la page quand on a déjà un
        # problème détecté à la base »). Sauf marchand dont la page décide (LDShop : option cochée ; PS Store), aux
        # titres traduits (« localized ») ou contrôlé sur sa version anglaise (Nintendo : un slug traduit n'est pas
        # un autre produit quand la version en-GB n'a pas pu être lue)
        return {"verdict": "SUSPECT", "url": url, "method": method, "reasons": result["reasons"], "notes": result["notes"]}

    page_text = None
    if result["match"] is None:
        # 2e repli : lire la page marchand (HTTP simple, puis Chromium si la config le permet)
        page_text, page_method = merchant_page_text(url, cfg)
        if page_text is None:
            others = [r for r, k in zip(result["reasons"], result["kinds"]) if k != "name"]
            if others:  # le nom ne se vérifie pas, mais l'URL montre déjà un autre problème (Elden Ring : « PlayStation »)
                return {"verdict": "SUSPECT", "url": url, "method": method, "reasons": others,
                        "notes": result["notes"] + ["nom du produit non vérifiable (page marchand illisible)"]}
            return {"verdict": "À VÉRIFIER", "url": url, "method": "aucune", "notes": [],
                    "reasons": [unverified_reason(offer, url)],
                    "unverifiable": cfg.get("unverifiable", "first-price")}
        result, method = analyze(product, offer, page_text, "titre de la page"), page_method
        if cfg.get("localized") and result["kinds"] == ["name"]:
            # boutique au titre traduit (Amazon.fr : « Kirby et le monde oublié ») : un nom introuvable
            # n'est pas une preuve, un humain vérifie ; la réponse enrichit aliases.toml
            return {"verdict": "À VÉRIFIER", "url": url, "method": method, "notes": result["notes"],
                    "reasons": ["titre du marchand dans une autre langue, nom non reconnu : %s" % page_text[:120]],
                    "unverifiable": cfg.get("unverifiable", "first-price")}

    # le DLC aussi, sur une édition « X + Y » : le « + » du titre (le jeu plus le contenu) disparaît dans l'URL
    confirmable = [k for k in result["kinds"] if k in ("platform", "console", "zone")
                   or (k == "dlc" and "+" in offer["edition"])]
    if confirmable and method.startswith("URL") and not (cfg.get("region") or {}).get("from") == "query":
        # l'URL contredit AllKeyShop : avant d'alerter, on regarde la page (URL trompeuse chez Gamingdragons)
        page_text, _ = merchant_page_text(url, cfg)
        if page_text:
            kept = [(r, k) for r, k in zip(result["reasons"], result["kinds"])
                    if not (k in confirmable and contradicted(k, product, offer, page_text))]
            if len(kept) < len(result["reasons"]):
                result["notes"].append("URL contredite par la page : %s" % page_text[:120])
            elif any(k in confirmable and confirmed(k, product, offer, page_text) for _, k in kept):
                result["notes"].append("confirmé par la page : %s" % page_text[:120])
            elif kept:
                result["notes"].append("la page ne dit rien sur ce point : %s" % page_text[:120])
            result["reasons"] = [r for r, _ in kept]
            result["kinds"] = [k for _, k in kept]
        if "zone" in result["kinds"] and redirect_untrusted(cfg) and "stock" not in result["kinds"]:
            # repli du groupe Kinguin quand la sonde n'a pas vu de redirection : la fiche servie a-t-elle une autre URL
            # canonique que le lien ?
            canonical = page_canonical(url, cfg)
            if moved(url, canonical):
                flag_out_of_stock(result, product, offer, urllib.parse.urljoin(url, canonical))

    return {"verdict": "SUSPECT" if result["reasons"] else "OK", "url": url, "method": method,
            "reasons": result["reasons"], "notes": result["notes"]}


# ---- Alertes, état, boucle ---------------------------------------------------

ICONS = {"OK": "🟢", "SUSPECT": "🔴", "À VÉRIFIER": "🟠", "NON VÉRIFIABLE": "⚪"}
NOT_SENT = ("OK", "NON VÉRIFIABLE")  # verdicts gardés dans le journal et l'état, sans alerte (OK : sauf NOTIFY_OK)


def rank_label(offer):
    """« 1er prix de l'édition », « 2e prix de l'édition (compte) », ou « » sans rang connu."""
    r = offer.get("edition_rank")
    if not r:
        return ""
    return "%s prix de l'édition%s" % ("1er" if r == 1 else "%de" % r, " (compte)" if offer.get("account") else "")


def format_alert(label, rank, product, page_url, offer, res):
    where = rank_label(offer)
    lines = [
        f"{ICONS[res['verdict']]} **{res['verdict']}** · **{product}** ({label} #{rank}) · {offer['edition']}"
        + (f" · {where}" if where else ""),
        f"{offer['merchantName']} · {offer['region']}"
        + (f" ({offer['region_filter']})" if offer.get("region_filter") and offer["region_filter"] != offer["region"] else "")
        + f" · {offer['platform'] or 'plateforme ?'} · "
        f"**{offer['price']:.2f} €** · offre {offer['id']} · contrôle : {res['method']}",
    ]
    lines += ["Raison : " + r for r in res["reasons"]]
    if res["notes"]:
        lines.append("Note : " + ", ".join(res["notes"]))
    if res.get("url"):
        lines.append(f"Marchand : <{res['url']}>")
    lines.append(f"Page : <{page_url}>")
    return "\n".join(lines)


def send_discord(webhook, content):
    body = json.dumps({"content": content, "allowed_mentions": {"parse": []}}).encode()
    req = urllib.request.Request(
        webhook, data=body, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "price-check (Discord webhook)"},
    )
    urllib.request.urlopen(req, timeout=30).close()


def muted():
    return bool(MUTE_UNTIL) and time.strftime("%Y-%m-%d %H:%M") < MUTE_UNTIL


def webhook_for(mode):
    """Webhook Discord d'un mode de pages (variable MODES[mode]["webhook"]), à défaut DISCORD_WEBHOOK_URL."""
    return os.environ.get(MODES.get(mode, {}).get("webhook", ""), "") or os.environ.get("DISCORD_WEBHOOK_URL", "")


def make_notifier(webhook, state, channel=""):
    """Envoie sur Discord, ou met en attente pendant une pause (`DISCORD_MUTE_UNTIL`). `channel` : le mode de
    pages dont le webhook enverra l'alerte mise en attente."""
    def notify(msg):
        if muted():
            state["queued"].append([channel, msg] if channel else msg)
            log.info("Discord en pause jusqu'au %s : alerte mise en attente (%d)", MUTE_UNTIL, len(state["queued"]))
        else:
            send_discord(webhook, msg)
    return notify


def flush_queue(state, send):
    """Envoie les alertes mises en attente pendant la pause, dans l'ordre : `send(msg, channel)`."""
    while state["queued"]:
        item = state["queued"][0]
        channel, msg = item if isinstance(item, list) else ("", item)
        send(msg, channel)
        state["queued"].pop(0)
        time.sleep(1)


def load_state(path):
    try:
        with open(path) as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    state.setdefault("checked", {})  # id d'offre -> verdict rendu
    state.setdefault("merchants", {})  # marchand -> méthodes de contrôle qui ont marché
    state.setdefault("queued", [])  # alertes en attente pendant une pause Discord
    for m in state["merchants"].values():  # ancien format : une seule méthode
        if "methods" not in m:
            m["methods"] = {m.pop("method", "URL"): 1}
    return state


def save_state(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def prune_state(state, now):
    limit = now - STATE_TTL_DAYS * 86400
    for key in [k for k, v in state["checked"].items() if v.get("seen", now) < limit]:
        del state["checked"][key]


FAILURES = {}  # id d'offre -> contrôles ratés d'affilée


def promote_unverifiable(entry, label, rank, product, page_url, offer, notify):
    """Une offre notée NON VÉRIFIABLE devient le premier prix de la page d'un top ou d'un coming soon : elle
    passe À VÉRIFIER et part sur Discord (la règle des offres non vérifiables vaut au moment où l'offre est
    vraiment le premier prix, pas seulement au premier contrôle)."""
    res = {"verdict": "À VÉRIFIER", "reasons": entry.get("reasons") or [], "url": entry.get("url"),
           "method": entry.get("method") or "aucune",
           "notes": (entry.get("notes") or []) + ["devenue le premier prix de la page"]}
    msg = format_alert(label, rank, product, page_url, offer, res)
    log.info("%s", msg.replace("\n", " | "))
    try:
        notify(msg)
    except Exception as e:
        log.error("Envoi Discord impossible, nouvel essai au prochain passage : %s", e)
        return
    entry.update(verdict="À VÉRIFIER", notes=res["notes"], page_first=True, edition_rank=offer.get("edition_rank"))


RECHECK_EVERY = 3600  # s : recontrôle des offres signalées encore sur leurs pages (et à chaque passage demandé depuis l'admin)


def flagged_entries(state, page_url):
    """Les offres signalées de cette page à recontrôler : SUSPECT, À VÉRIFIER, NON VÉRIFIABLE, pas encore réparées,
    hors « faux positif » décidé dans l'admin."""
    return {k: e for k, e in state["checked"].items()
            if e.get("page") == page_url and e.get("verdict") in REPORTED and not e.get("fixed_at")
            and (e.get("decision") or {}).get("decision") != "faux"}


def apply_recheck(entry, label, rank, product, page_url, offer, res, notify, stamp, now, outcome):
    """Une offre déjà vue, recontrôlée : son nouveau verdict comparé à l'ancien. Réparée (était signalée, maintenant
    OK), toujours en erreur, ou nouvelle erreur (était OK) : celle-ci part sur Discord comme une alerte, ainsi qu'une
    offre notée NON VÉRIFIABLE devenue SUSPECT. Un recontrôle qui ne conclut pas (page illisible : À VÉRIFIER, NON
    VÉRIFIABLE) ne change rien au verdict : un OK vérifié reste OK (G2A, The Witcher Trilogy Pack, 02/10/2026 :
    « Access Denied » au recontrôle, alors que la page avait été lue le 30/09)."""
    was = entry.get("verdict")
    before = offer_facts(entry.get("url"), entry.get("region"), entry.get("region_filter"), entry.get("platform"), entry.get("edition"))
    after = offer_facts(res.get("url") or entry.get("url"), offer.get("region"), offer.get("region_filter"), offer.get("platform"),
                        offer.get("edition"))
    if res["verdict"] in ("À VÉRIFIER", "NON VÉRIFIABLE"):
        entry["last_recheck"] = stamp
        entry["seen"] = now
        outcome["unknown"].append((entry, (res.get("reasons") or ["recontrôle sans conclusion"])[0]))
        return
    entry.update(reasons=res["reasons"], notes=res["notes"], method=res["method"], url=res["url"] or entry.get("url"),
                 edition_rank=offer.get("edition_rank"), page_first=offer.get("page_first", False), seen=now,
                 last_recheck=stamp, region=offer["region"], region_filter=offer.get("region_filter", ""),
                 platform=offer["platform"], price=offer["price"])
    outcome["checked"] += 1
    if res["verdict"] == "OK":
        if was in REPORTED:
            changed = [name for name, a, b in zip(("URL", "région", "plateforme", "édition"), before, after) if a and b and a != b]
            if changed:  # l'offre a changé chez AllKeyShop ou chez le marchand : une vraie réparation
                entry.update(fixed_at=stamp, fixed_kind="repaired", fixed_how="recontrôle OK, l'offre a changé (%s)" % ", ".join(changed),
                             fixed_from=was, verdict="OK")
                outcome["fixed"].append(entry)
            else:  # rien n'a changé : c'était un faux positif, levé par une règle ajoutée depuis
                entry.update(fixed_at=stamp, fixed_kind="rule", fixed_how="ancien faux positif : rien n'a changé, levé par une règle",
                             fixed_from=was, verdict="OK")
                outcome["rules"].append(entry)
        else:
            entry["verdict"] = "OK"
        return
    entry["verdict"] = res["verdict"]
    if was in REPORTED:
        entry["still_wrong_at"] = stamp
        outcome["still"].append(entry)
        alert = res["verdict"] == "SUSPECT" and was != "SUSPECT"  # une offre notée devient une erreur avérée
    else:  # était OK (ou réparée) : nouvelle erreur
        entry.update(at=stamp, fixed_at=None, fixed_how=None, fixed_from=None)
        outcome["new"].append(entry)
        alert = res["verdict"] not in NOT_SENT
    if alert:
        msg = format_alert(label, rank, product, page_url, offer, res)
        log.info("%s", msg.replace("\n", " | "))
        try:
            notify(msg)
        except Exception as e:
            log.error("Envoi Discord impossible : %s", e)


def offer_facts(url, region, region_filter, platform, edition):
    """Ce qui identifie une offre pour dire si elle a changé entre deux contrôles : chemin de l'URL marchand (sans les
    paramètres de suivi), région, plateforme, édition."""
    path = norm(url_text(unwrap_affiliate(url))) if url else ""
    return (path, norm(region_filter or region or ""), norm(platform or ""), norm(edition or ""))


def recheck_flagged(label, rank, product, page_url, trans, state, notify, checker, stamp, now, outcome, skip=()):
    """Romain, 02/10/2026 : « il faut qu'il contrôle les offres déjà vues, comme ça on saura si elles sont réparées ou
    pas ». Les offres signalées de la page qui ne sont plus parmi les offres retenues (`skip` : déjà recontrôlées) :
    disparues de la page = retirées ; encore là (plus bas dans l'édition) = recontrôlées."""
    flagged = {k: e for k, e in flagged_entries(state, page_url).items() if k not in skip}
    if not flagged:
        return
    on_page = {str(o["id"]): o for o in page_offers(trans, None)}
    page_dlc = is_dlc_page(trans, product)
    for key, entry in flagged.items():
        offer = on_page.get(key)
        if offer is None:
            entry.update(fixed_at=stamp, fixed_kind="repaired", fixed_how="offre retirée de la page", fixed_from=entry["verdict"],
                         verdict="OK", seen=now, last_recheck=stamp)
            outcome["removed"].append(entry)
            continue
        offer["page_dlc"] = page_dlc
        try:
            res = checker(product, offer)
        except CheckError as e:
            outcome["unknown"].append((entry, str(e)))
            continue
        if res["verdict"] == "À VÉRIFIER":
            res = dict(res, verdict=unverifiable_verdict(offer, label, page_url, res.get("unverifiable", "first-price")))
        apply_recheck(entry, label, rank, product, page_url, offer, res, notify, stamp, now, outcome)


def format_recheck(label, by, outcome, full=False):
    """Récapitulatif Discord d'un recontrôle : complet (toutes les offres retenues, passage demandé depuis l'admin) ou
    des seules offres signalées (toutes les heures)."""
    what = "Recontrôle de toutes les offres" if full else "Recontrôle des offres signalées"
    lines = ["🔁 **%s** · %s%s · %d offre(s) recontrôlée(s)" % (
        what, label, " (demandé depuis l'admin par %s)" % by if by else "", outcome["checked"])]
    item = lambda e: "%s · %s · %s" % (e.get("product"), e.get("edition"), e.get("merchant"))
    fixed = ["%s — %s" % (item(e), e.get("fixed_how")) for e in outcome["removed"] + outcome["fixed"]]
    if fixed:
        lines.append("✅ Réparées (%d) : %s%s" % (len(fixed), " ; ".join(fixed[:15]), " ; …" if len(fixed) > 15 else ""))
    rules = [item(e) for e in outcome.get("rules", [])]
    if rules:
        lines.append("🧹 Anciens faux positifs levés par les règles, rien n'a changé (%d) : %s%s" % (
            len(rules), " ; ".join(rules[:10]), " ; …" if len(rules) > 10 else ""))
    new = ["%s (%s)" % (item(e), (e.get("reasons") or ["?"])[0][:90]) for e in outcome["new"]]
    if new:
        lines.append("🆕 Nouvelles erreurs (%d) : %s%s" % (len(new), " ; ".join(new[:15]), " ; …" if len(new) > 15 else ""))
    still = ["%s (%s)" % (item(e), (e.get("reasons") or ["?"])[0][:90]) for e in outcome["still"]]
    if still:
        lines.append("🔴 Toujours en erreur (%d) : %s%s" % (len(still), " ; ".join(still[:15]), " ; …" if len(still) > 15 else ""))
    if outcome["unknown"]:
        unknown = ["%s (%s)" % (item(e), why[:70]) for e, why in outcome["unknown"]]
        lines.append("⚪ Recontrôle sans conclusion, verdict inchangé (%d) : %s%s" % (
            len(unknown), " ; ".join(unknown[:5]), " ; …" if len(unknown) > 5 else ""))
    if not (fixed or rules or new or still or outcome["unknown"]):
        lines.append("Rien à signaler : aucune offre réparée ni en erreur.")
    return "\n".join(lines)[:1900]


def run_cycle(targets, notify, state, checker=None, save=None, per_edition=1, between=None, progress=None, recheck=False):
    """Lit chaque page suivie et contrôle toute offre retenue (`per_edition` : voir page_offers) pas encore
    contrôlée. `recheck` : "all" recontrôle aussi toutes les offres retenues déjà vues (passage demandé depuis
    l'admin : Romain, 02/10/2026, « toutes les offres concernées par le top check, pareil pour l'autre check ») ;
    "flagged" recontrôle les seules offres signalées. Renvoie le bilan du recontrôle.

    `save()` est appelé toutes les SAVE_EVERY pages : un long passage interrompu ne repart pas de zéro.
    `between()` est appelé entre deux pages : un mode urgent (les top games) y passe pendant un long passage.
    `progress(count, total)` est appelé à chaque page : l'avancement publié pour l'admin.
    """
    checker = checker or check_offer
    now = time.time()
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
    outcome = {"checked": 0, "fixed": [], "removed": [], "rules": [], "still": [], "new": [], "unknown": []}
    for count, (label, rank, product, page_url) in enumerate(targets, 1):
        if between and count > 1:
            between()
        if progress:
            progress(count, len(targets))
        if save and count % SAVE_EVERY == 0:
            save()
        try:
            _, _, page_html = http_get(page_url, AKS_UA)
            trans = parse_game_page(page_html)
        except Exception as e:
            log.warning("%s : %s", product, e)
            continue
        finally:
            time.sleep(PAGE_DELAY)
        page_dlc = is_dlc_page(trans, product)
        handled = set()
        for offer in page_offers(trans, per_edition):
            offer["page_dlc"] = page_dlc
            key = str(offer["id"])
            if merchant_config("", offer["merchantName"]).get("skip"):
                state["checked"].pop(key, None)  # marchand ignoré par sa config (Amazon) : ni contrôle, ni report
                continue
            entry = state["checked"].get(key)
            handled.add(key)  # une offre retenue est traitée ici, une fois ; recheck_flagged ne voit que les autres
            # une offre déjà vue est recontrôlée sur un passage complet ("all"), ou si elle est signalée ("flagged") ;
            # jamais si l'admin l'a jugée faux positif
            again = entry is not None and (entry.get("decision") or {}).get("decision") != "faux" and (
                recheck == "all" or (recheck == "flagged" and entry.get("verdict") in REPORTED and not entry.get("fixed_at")))
            if entry is not None and not again:
                entry["seen"] = now
                policy = entry.get("unverifiable") or merchant_config("", entry.get("merchant")).get("unverifiable", "first-price")
                if (entry.get("verdict") == "NON VÉRIFIABLE" and policy == "first-price"
                        and unverifiable_verdict(offer, label, page_url) == "À VÉRIFIER"):
                    promote_unverifiable(entry, label, rank, product, page_url, offer, notify)
                continue
            try:
                res = checker(product, offer)
            except CheckError as e:
                FAILURES[key] = FAILURES.get(key, 0) + 1
                log.warning("%s / %s : %s (%s) : %s", product, offer["edition"], offer["merchantName"], key, e)
                if FAILURES[key] < MAX_CHECK_FAILURES:
                    continue
                res = {"verdict": "À VÉRIFIER", "url": None, "method": "aucune", "notes": [],
                       "reasons": ["contrôle impossible : %s" % e]}
            FAILURES.pop(key, None)
            if res["verdict"] == "À VÉRIFIER":
                res = dict(res, verdict=unverifiable_verdict(offer, label, page_url, res.get("unverifiable", "first-price")))
            if entry is not None:  # recontrôle d'une offre déjà vue
                apply_recheck(entry, label, rank, product, page_url, offer, res, notify, stamp, now, outcome)
                continue
            msg = format_alert(label, rank, product, page_url, offer, res)
            log.info("%s", msg.replace("\n", " | "))
            if res["verdict"] not in NOT_SENT or (res["verdict"] == "OK" and NOTIFY_OK):
                try:
                    notify(msg)
                except Exception as e:
                    log.error("Envoi Discord impossible, nouvel essai au prochain passage : %s", e)
                    continue
            state["checked"][key] = {
                "verdict": res["verdict"], "reasons": res["reasons"], "notes": res["notes"], "product": product,
                "edition": offer["edition"], "region": offer["region"], "region_filter": offer.get("region_filter", ""),
                "platform": offer["platform"], "merchant": offer["merchantName"], "price": offer["price"],
                "url": res["url"], "method": res["method"], "at": stamp, "seen": now,
                "page": page_url, "list": label, "rank": rank,
                "edition_rank": offer.get("edition_rank"), "account": offer.get("account", False),
                "page_first": offer.get("page_first", False), "unverifiable": res.get("unverifiable"),
            }
            if res["method"] != "aucune":
                m = state["merchants"].setdefault(offer["merchantName"], {"methods": {}})
                m["methods"][res["method"]] = m["methods"].get(res["method"], 0) + 1
                m.update(url=res["url"], at=stamp)
        if recheck:
            recheck_flagged(label, rank, product, page_url, trans, state, notify, checker, stamp, now, outcome, skip=handled)
    prune_state(state, now)
    return outcome


# ---- Reports pour l'admin (fichiers partagés) ---------------------------------

# Dossier partagé avec l'admin : le moniteur y écrit reports.json, l'admin y écrit decisions.jsonl.
REPORTS_DIR = os.environ.get("PRICE_CHECK_REPORTS_DIR", "")
REPORTED = ("SUSPECT", "À VÉRIFIER", "NON VÉRIFIABLE")
REQUEST_FILE = "run-%s.request"  # déposé par l'admin : un passage demandé pour un mode (run-top-games.request)
STATUS_FILE = "status.json"  # écrit par le moniteur pour l'admin : l'état de chaque mode
REQUEST_POLL = 5  # s entre deux lectures des demandes de l'admin pendant l'attente


def take_requests(directory, modes):
    """Les passages demandés depuis l'admin (Romain, 02/10/2026 : deux boutons, « Price check top » et « Price check
    homepage ») : un fichier run-<mode>.request ({"by", "at"}) par mode, lu puis supprimé. Renvoie [(mode, by)]."""
    found = []
    if not directory:
        return found
    for mode in modes:
        path = os.path.join(directory, REQUEST_FILE % mode)
        if not os.path.exists(path):
            continue
        by = ""
        try:
            with open(path, encoding="utf-8") as f:
                by = str(json.load(f).get("by") or "")
        except (OSError, ValueError):
            pass
        try:
            os.remove(path)
        except OSError:
            continue
        found.append((mode, by or "admin"))
    return found


def write_status(directory, status):
    """status.json pour l'admin : l'état de chaque mode (en cours, avancement, dernier et prochain passage)."""
    if not directory:
        return
    path = os.path.join(directory, STATUS_FILE)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(dict(status, updated_at=time.strftime("%Y-%m-%dT%H:%M:%S%z")), f, ensure_ascii=False, indent=1)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except OSError as e:
        log.warning("status.json non écrit : %s", e)
DECISIONS = {"vrai": "Vrai positif : alerter", "faux": "Faux positif : ne pas alerter", "a_discuter": "À discuter"}


def read_decisions(directory):
    """Décisions de l'admin (decisions.jsonl : une ligne JSON par décision), la dernière par offre l'emporte.
    Une ligne illisible ou inconnue est ignorée."""
    decisions = {}
    try:
        with open(os.path.join(directory, "decisions.jsonl"), encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if isinstance(d, dict) and str(d.get("offer", "")).isdigit() and d.get("decision") in DECISIONS:
                    decisions[str(d["offer"])] = {k: d.get(k) for k in ("decision", "note", "by", "at")}
    except OSError:
        pass
    return decisions


def apply_decisions(state, directory):
    """Reporte les décisions de l'admin dans l'état ; renvoie les offres dont la décision est nouvelle."""
    changed = []
    for key, decision in read_decisions(directory).items():
        entry = state["checked"].get(key)
        if entry is not None and entry.get("decision") != decision:
            entry["decision"] = decision
            changed.append(key)
    return changed


def export_reports(state, directory, pages=None):
    """Écrit reports.json : chaque report (SUSPECT, À VÉRIFIER, NON VÉRIFIABLE, ou déjà décidé) avec son
    URL AllKeyShop, son URL marchand, sa raison, sa preuve et sa décision. `pages` (produit -> (liste, rang,
    URL)) complète les reports anciens, enregistrés avant que l'état garde la page."""
    pages = pages or {}
    reports = []
    for key, e in state["checked"].items():
        if e.get("verdict") not in REPORTED and not e.get("decision") and not e.get("fixed_at"):
            continue
        if merchant_config("", e.get("merchant")).get("skip"):
            continue  # marchand ignoré par sa config (Amazon depuis le 01/10/2026)
        label, rank, page = pages.get(e.get("product"), (None, None, None))
        reports.append({
            "offer": key, "verdict": e.get("verdict"), "product": e.get("product"), "edition": e.get("edition"),
            "merchant": e.get("merchant"), "price": e.get("price"), "region": e.get("region"),
            "region_filter": e.get("region_filter") or "", "platform": e.get("platform"),
            "reasons": e.get("reasons") or [], "notes": e.get("notes") or [], "method": e.get("method"),
            "merchant_url": e.get("url"), "page_url": e.get("page") or page, "list": e.get("list") or label,
            "rank": e.get("rank") or rank, "at": e.get("at"), "decision": e.get("decision"),
            "edition_rank": e.get("edition_rank"), "account": bool(e.get("account")),
            "page_first": e.get("page_first"), "seen": e.get("seen"),
            # recontrôle des offres signalées (02/10/2026) : réparée (retirée de la page, ou recontrôle OK) ou toujours en erreur
            "fixed_at": e.get("fixed_at"), "fixed_how": e.get("fixed_how"), "fixed_from": e.get("fixed_from"),
            "fixed_kind": e.get("fixed_kind"),
            "last_recheck": e.get("last_recheck"), "still_wrong_at": e.get("still_wrong_at"),
        })
    reports.sort(key=lambda r: r.get("at") or "", reverse=True)
    payload = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "decisions": DECISIONS, "reports": reports}
    path = os.path.join(directory, "reports.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
    return len(reports)


def unverified_table(state):
    """Table Markdown des offres en tête qu'on n'a pas pu vérifier (NON VÉRIFIABLE), pour la doc."""
    rows = ["| Jeu | Édition | Marchand | Prix | Pourquoi | URL marchand | Vu le |", "|---|---|---|---|---|---|---|"]
    for key, e in sorted(state["checked"].items(), key=lambda kv: (kv[1].get("merchant", ""), kv[1].get("product", ""))):
        if e.get("verdict") == "NON VÉRIFIABLE":
            rows.append("| %s | %s | %s | %.2f € | %s | `%s` | %s |" % (
                e.get("product"), e.get("edition"), e.get("merchant"), e.get("price") or 0, "; ".join(e.get("reasons") or []),
                re.sub(r"[?#].*", "", e.get("url") or ""), e.get("at")))
    return "\n".join(rows)


def coverage_table(state):
    """Table Markdown des marchands rencontrés et de la méthode qui a marché, pour docs/marchands.md."""
    rows = ["| Marchand | Méthodes (nombre de contrôles) | Dernier contrôle | Exemple d'URL |", "|---|---|---|---|"]
    for name, m in sorted(state["merchants"].items(), key=lambda kv: kv[0].lower()):
        methods = ", ".join(f"{k} ({v})" for k, v in sorted(m["methods"].items(), key=lambda kv: -kv[1]))
        rows.append(f"| {name} | {methods} | {m['at']} | `{re.sub(r'[?#].*', '', m['url'])}` |")
    return "\n".join(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=("top-games", "homepage", "both"), default=os.environ.get("PRICE_CHECK_MODE", "both"),
                    help="top-games : top 5 Popular + top 4 Coming soon PC ; homepage : tous les jeux des top clics "
                         "de la home ; both (défaut, ou variable PRICE_CHECK_MODE)")
    ap.add_argument("--offers", choices=tuple(OFFER_MODES), default=os.environ.get("PRICE_CHECK_OFFERS", "top-offers"),
                    help="top-offers (défaut, ou variable PRICE_CHECK_OFFERS) : les 3 premiers prix de chaque édition ; "
                         "full-page : toutes les offres en vente de la page, comptes compris")
    ap.add_argument("--dry-run", action="store_true", help="affiche les alertes sans les envoyer")
    ap.add_argument("--once", action="store_true", help="un seul passage puis arrêt")
    ap.add_argument("--state", default="state.json", help="fichier des offres déjà contrôlées")
    ap.add_argument("--coverage", action="store_true", help="affiche la table de couverture des marchands et sort")
    ap.add_argument("--unverified", action="store_true", help="affiche la table des offres NON VÉRIFIABLE et sort")
    ap.add_argument("--export-reports", metavar="DOSSIER", help="écrit reports.json (et lit decisions.jsonl) dans DOSSIER et sort")
    ap.add_argument("--check", nargs=2, metavar=("PRODUIT", "URL"), help="analyse une URL marchand et sort")
    ap.add_argument("--edition", default="Standard", help="avec --check : édition affichée")
    ap.add_argument("--region", default="GLOBAL", help="avec --check : région affichée")
    ap.add_argument("--platform", default="", help="avec --check : plateforme d'activation affichée")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.coverage:
        print(coverage_table(load_state(args.state)))
        return
    if args.unverified:
        print(unverified_table(load_state(args.state)))
        return
    if args.export_reports:
        state = load_state(args.state)
        apply_decisions(state, args.export_reports)
        pages = {}
        for mode in MODES:
            for label, rank, product, url in fetch_targets(MODES[mode]["lists"]):
                pages.setdefault(product, (label, rank, url))
        print(export_reports(state, args.export_reports, pages), "reports écrits dans", args.export_reports)
        save_state(args.state, state)
        return
    if args.check:
        product, url = args.check
        offer = {"account": False, "edition": args.edition, "region": args.region, "platform": args.platform}
        res = analyze(product, offer, url_text(unwrap_affiliate(url)), "URL")
        print("nom :", res["match"] or "absent", "| verdict :", "SUSPECT" if res["reasons"] else "OK")
        for r in res["reasons"]:
            print("raison :", r)
        return

    state = load_state(args.state)
    modes = [m for m in MODES if args.mode in (m, "both")]
    per_edition = OFFER_MODES[args.offers]
    if args.dry_run:
        notifiers = {m: (lambda msg: None) for m in modes}  # le journal affiche déjà chaque verdict sur une ligne
    else:
        missing = [MODES[m]["webhook"] for m in modes if not webhook_for(m)]
        if missing:
            sys.exit("%s manquant (ou utiliser --dry-run)" % ", ".join(missing))
        for m in modes:
            if webhook_for(m) == os.environ.get("DISCORD_WEBHOOK_URL") and MODES[m]["webhook"] != "DISCORD_WEBHOOK_URL":
                log.info("%s : pas de %s, alertes envoyées sur le webhook des top games", m, MODES[m]["webhook"])
        notifiers = {m: make_notifier(webhook_for(m), state, m) for m in modes}
    log.info("modes de pages : %s ; offres : %s", ", ".join(modes), args.offers)

    targets = {m: [] for m in modes}
    lists_at = {m: 0.0 for m in modes}
    due = {m: 0.0 for m in modes}  # prochain passage de chaque mode (time.monotonic)
    stamp_iso = lambda: time.strftime("%Y-%m-%dT%H:%M:%S%z")
    status = {"offers": args.offers, "modes": {m: {
        "label": MODES[m]["label"], "interval": MODES[m]["interval"], "running": False, "pages": 0, "progress": None,
        "last_start": None, "last_end": None, "last_checked": 0, "last_alerts": 0, "requested_by": None,
        "last_requested_by": None} for m in modes}}

    def publish_status():
        for m in modes:
            status["modes"][m]["next_at"] = time.strftime(
                "%Y-%m-%dT%H:%M:%S%z", time.localtime(time.time() + max(0.0, due[m] - time.monotonic())))
        write_status(REPORTS_DIR, status)

    def honor_requests():
        """Les passages demandés depuis l'admin : le mode est dû tout de suite. Renvoie True si une demande a été lue."""
        taken = take_requests(REPORTS_DIR, modes)
        for mode, by in taken:
            log.info("%s : passage demandé depuis l'admin par %s", mode, by)
            due[mode] = 0.0
            status["modes"][mode]["requested_by"] = by
        return bool(taken)

    last_recheck = {m: 0.0 for m in modes}

    def run_mode(mode, between=None):
        now = time.monotonic()
        due[mode] = now + MODES[mode]["interval"]
        st, alerts, before, last_pub = status["modes"][mode], [0], len(state["checked"]), [time.monotonic()]
        requested = st.get("requested_by")
        # passage demandé depuis l'admin : toutes les offres retenues sont recontrôlées ; sinon, les offres signalées
        # une fois par heure
        recheck = "all" if requested else ("flagged" if now - last_recheck[mode] >= RECHECK_EVERY else False)

        def notify(msg, _send=notifiers[mode]):
            _send(msg)
            alerts[0] += 1

        def progress(count, total):
            st["progress"] = [count, total]
            if time.monotonic() - last_pub[0] >= REQUEST_POLL or count == total:
                last_pub[0] = time.monotonic()
                publish_status()

        st.update(running=True, last_start=stamp_iso(), progress=None, last_alerts=0, last_requested_by=st.get("requested_by"))
        try:
            if not targets[mode] or now - lists_at[mode] >= LISTS_REFRESH:
                targets[mode] = fetch_targets(MODES[mode]["lists"])
                lists_at[mode] = now
                names = ", ".join(t[2] for t in targets[mode]) if len(targets[mode]) <= 20 else ""
                log.info("%s : %d pages suivies %s", mode, len(targets[mode]), names)
            st["pages"] = len(targets[mode])
            publish_status()
            if REPORTS_DIR:
                for key in apply_decisions(state, REPORTS_DIR):
                    log.info("décision de l'admin pour l'offre %s : %s", key, state["checked"][key]["decision"])
            outcome = run_cycle(targets[mode], notify, state, save=lambda: save_state(args.state, state),
                                per_edition=per_edition, between=between, progress=progress, recheck=recheck)
            save_state(args.state, state)
            if recheck:
                last_recheck[mode] = time.monotonic()
                st["last_recheck"] = {"at": stamp_iso(), "kind": recheck, "checked": outcome["checked"],
                                      "fixed": len(outcome["fixed"]) + len(outcome["removed"]), "rules": len(outcome["rules"]),
                                      "new": len(outcome["new"]),
                                      "still": len(outcome["still"]), "unknown": len(outcome["unknown"])}
                recap = format_recheck(MODES[mode]["label"], requested, outcome, full=recheck == "all")
                log.info("%s", recap.replace("\n", " | "))
                if requested or outcome["fixed"] or outcome["removed"] or outcome["new"]:  # automatique : s'il y a du nouveau
                    try:
                        notifiers[mode](recap)
                    except Exception as e:
                        log.error("Envoi Discord du récapitulatif impossible : %s", e)
            if REPORTS_DIR:
                pages = {t[2]: (t[0], t[1], t[3]) for m in modes for t in targets[m]}
                export_reports(state, REPORTS_DIR, pages)
        except Exception:
            log.exception("%s : passage en échec, nouvel essai au prochain cycle", mode)
        finally:
            st.update(running=False, last_end=stamp_iso(), last_checked=len(state["checked"]) - before,
                      last_alerts=alerts[0], progress=None, requested_by=None)
            publish_status()

    def run_urgent():
        """Entre deux pages d'un long passage : les demandes de l'admin, puis les modes urgents (top games) dont
        l'heure est venue."""
        honor_requests()
        for m in modes:
            if MODES[m].get("urgent") and time.monotonic() >= due[m]:
                run_mode(m)

    while True:
        if state["queued"] and not muted() and not args.dry_run:
            try:
                flush_queue(state, lambda msg, channel: send_discord(webhook_for(channel), msg))
                log.info("fin de la pause Discord : alertes en attente envoyées")
            except Exception as e:
                log.error("Envoi des alertes en attente impossible, nouvel essai plus tard : %s", e)
            save_state(args.state, state)
        honor_requests()
        for mode in modes:
            if time.monotonic() >= due[mode]:
                run_mode(mode, between=None if MODES[mode].get("urgent") else run_urgent)
        if args.once:
            return
        wait_until = min(due.values())  # attente par tranches : une demande de l'admin est vue en quelques secondes
        while time.monotonic() < wait_until:
            publish_status()
            time.sleep(max(1, min(REQUEST_POLL, wait_until - time.monotonic())))
            if honor_requests():
                break


if __name__ == "__main__":
    main()
