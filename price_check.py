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
import collections
import datetime
import glob
import html
import http.client
import ipaddress
import itertools
import json
import logging
import os
import pwd
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
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
# Urgences premiers prix (Romain, 03/10/2026 : « quand c'est vraiment premier prix qui a un problème, c'est une grosse
# alerte, reportée sur ce webhook spécialement créé pour les urgences de problème premiers prix (premier prix = les 3
# prix les moins chers par édition) ») : un SUSPECT sur l'une des 3 offres de clé les moins chères de son édition part
# sur ce webhook, et seulement là ; le reste (À VÉRIFIER, offres plus bas dans l'édition, comptes, récapitulatifs)
# reste sur le salon de son mode. Sans ce webhook, l'alerte part sur le salon du mode, avec son en-tête.
FIRST_PRICES = 3
URGENT_WEBHOOK = "DISCORD_WEBHOOK_URL_URGENT"
URGENT_PREFIX = "🚨 **URGENCE PREMIER PRIX**"
# Le bandeau de boucle (Romain, 03/10/2026 : « il faut qu'on sache qu'une nouvelle boucle a commencé, et tu mets un
# petit message pour expliquer et un lien vers la doc … très visible, qui fasse bien la séparation entre les
# boucles », dans chaque salon de check) : avant le premier message d'une boucle dans un salon (loop_banner).
GUIDE_URL = os.environ.get("PRICE_CHECK_GUIDE_URL", "https://169.58.5.63.sslip.io/executor/price-check-guide")
ADMIN_URL = os.environ.get("PRICE_CHECK_ADMIN_URL", "https://169.58.5.63.sslip.io/executor/price-check")
# Le rappel du matin (Romain, 05/10/2026) : chaque jour à cette heure locale, dans le salon des urgences. Vide : aucun.
DAILY_REMINDER_AT = os.environ.get("DAILY_REMINDER_AT", "09:00")
REDIRECTION_URL = "https://www.allkeyshop.com/redirection/offer/eur/%s?locale=en&merchant=%s"

AKS_UA = "AKS/Staff"  # pages AllKeyShop seulement, jamais chez le marchand
BROWSER_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/150.0.0.0 Safari/537.36")  # chez le marchand
CHROMIUM = os.environ.get("CHROMIUM_BIN", "chromium")  # dernier repli : ouvrir la page marchand
CHROMIUM_USER = os.environ.get("CHROMIUM_USER", "nobody")  # le compte du rendu : jamais root (voir chromium_command)
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
    # audit du 02/10/2026 : une clé US ou UK affichée EUROPE ou GLOBAL passait. Pas « us » ni « uk » seuls (« Among Us »,
    # « uk » = ukrainien dans les listes de langues de GAMIVO) ; mesure : « united-states » n'apparaît en mémoire que
    # sur des offres affichées USA (CJS CDKeys)
    "US": ("united-states", "usa", "north-america"),
    "UK": ("united-kingdom",),
}
REGION_FAMILIES = {z: MERCHANT_ZONE_WORDS[z] for z in ("GLOBAL", "EU", "ROW")}  # compatibilité
FORBIDDEN_REGION_WORDS = ("ru", "russia", "russian", "cis", "asia", "sea", "latam", "latin-america",
                          "india", "tr", "turkey", "cn", "china", "ar", "argentina", "br", "brazil",
                          "jp", "japan", "kr", "korea", "mena", "africa", "za")
GIFT_WORDS = ("gift", "altergift")
ACCOUNT_WORDS = ("account", "accounts", "offline-account", "shared-account")
DLC_WORDS = ("dlc", "season-pass", "expansion", "soundtrack", "upgrade", "add-on", "pass")  # « pass », « add-on » : audit du 02/10/2026
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
                    "playstation-network-card", "wallet", "top-up", "topup", "golden-eagles")  # golden eagles : War Thunder
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

def is_aks_host(url):
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host == "allkeyshop.com" or host.endswith(".allkeyshop.com")


def safe_target(url):
    """Une URL que le moniteur (root) peut ouvrir : http(s) seulement, jamais la machine elle-même ni le réseau privé
    (audit du 02/10/2026 : l'URL marchand vient de la redirection AllKeyShop et des redirections du marchand, sans
    contrôle ; « file:///… » ou « http://127.0.0.1:8650/… » étaient ouverts)."""
    parts = urllib.parse.urlsplit(url or "")
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme not in ("http", "https") or not host or host == "localhost" or host.endswith(".localhost"):
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return True  # un nom DNS


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class GuardedRedirect(urllib.request.HTTPRedirectHandler):
    """Suit une redirection, sauf vers une cible refusée (safe_target), et sauf, avec l'UA AKS/Staff, hors d'AllKeyShop :
    urllib renvoie le même User-Agent à la cible, et AKS/Staff ne part jamais chez un marchand (règle de Romain ; audit
    du 02/10/2026). La redirection non suivie revient à l'appelant : statut 30x et en-tête Location."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not safe_target(newurl) or (req.get_header("User-agent") == AKS_UA and not is_aks_host(newurl)):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


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
    """Renvoie (statut HTTP, en-tête Location, corps). Un statut d'erreur ne lève pas ; une cible refusée (safe_target),
    ou l'UA AKS/Staff hors d'AllKeyShop, lève OSError."""
    if not safe_target(url):
        raise OSError("cible refusée : %s" % (url or "")[:120])
    if ua == AKS_UA and not is_aks_host(url):
        raise OSError("UA AKS/Staff hors d'AllKeyShop refusé : %s" % url[:120])
    opener = urllib.request.build_opener(GuardedRedirect if follow else NoRedirect)
    try:
        req = urllib.request.Request(quote_url(url), headers=request_headers(ua))
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.headers.get("Location"), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Location"), ""
    except (http.client.HTTPException, ValueError) as e:
        # IncompleteRead, BadStatusLine, LineTooLong, InvalidURL, UnicodeEncodeError : une erreur réseau comme une
        # autre (audit du 02/10/2026 : elles arrêtaient le passage entier, à chaque passage, au même endroit)
        raise OSError("%s : %s" % (type(e).__name__, e)) from e


def quote_url(url):
    """L'URL avec ses espaces et ses caractères non ASCII encodés (« %xx » déjà présents gardés tels quels) : un lien de
    feed « …/p/12345 6 » ou accentué ne fait plus échouer la requête."""
    parts = urllib.parse.urlsplit(url)
    safe = "/:@!$&'()*+,;=%~-._"
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, urllib.parse.quote(parts.path, safe=safe),
                                    urllib.parse.quote(parts.query, safe=safe + "?/"), urllib.parse.quote(parts.fragment, safe=safe + "?/")))


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


def dlc_page_kind(trans, product):
    """« page » : la page AllKeyShop est celle d'un DLC, toutes ses éditions sont du DLC (Diablo 4 Lord of Hatred,
    formation du 30/09/2026 : éditions DLC, Deluxe et Ultimate, pas de Standard) ; son nom le dit, ou son édition
    « DLC » a au moins autant d'offres que ses éditions de base. « edition » : la page d'un jeu qui a aussi une petite
    édition « DLC » (Hearts of Iron 4 : 1 offre DLC pour 55 en Standard ; Age of Wonders 4, Resident Evil 4 PS5) : seule
    cette édition attend du DLC (audit du 02/10/2026 : toute la page en était exemptée). None : page d'un jeu.
    Les listes et CatalogV2 typent pourtant ces pages « game » sur console."""
    words = set(norm(product).split("-"))
    if words & {"dlc", "expansion"} or "season-pass" in norm(product):
        return "page"
    editions = {str(k): e.get("name", "") for k, e in (trans.get("editions") or {}).items()}
    dlc_ids = {k for k, name in editions.items() if norm(name) == "dlc"}
    if not dlc_ids:
        return None
    on_sale = [p for p in trans.get("prices") or [] if p.get("price") != NO_PRICE and p.get("dispo", 1)]
    if not on_sale:
        return "page"  # sans offres à compter : comme avant, l'édition « DLC » fait la page
    dlc_n = sum(1 for p in on_sale if str(p.get("edition")) in dlc_ids)
    base_n = sum(1 for p in on_sale if str(p.get("edition")) not in dlc_ids
                 and is_base_edition(editions.get(str(p.get("edition")), "")) and not is_bundle(editions.get(str(p.get("edition")), "")))
    return "page" if dlc_n >= base_n else "edition"


def is_dlc_page(trans, product):
    return dlc_page_kind(trans, product) == "page"


def offer_on_dlc_page(kind, offer):
    """Le DLC est attendu pour cette offre : page d'un DLC, ou offre rangée dans l'édition « DLC » d'une page de jeu."""
    return kind == "page" or (kind == "edition" and norm(offer.get("edition") or "") == "dlc")


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
    for key in ("u", "url", "dest", "destination", "redirect", "target", "link", "r", "ued"):  # ued : Awin
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


STOCK_REASON_START = "offre en rupture chez le marchand"


def out_of_stock_reason(served):
    """Groupe Kinguin : la fiche du lien est en rupture, le marchand sert une autre offre, le prix reste dans le feed."""
    return STOCK_REASON_START + " : le lien redirige vers une autre fiche (%s), mais le prix reste dans le feed" % served


def unsure_zone(result, offer, cfg):
    """Romain, 06/10/2026 (Monster Hunter Wilds chez G2A, offre 136209040 : clé « ROW » affichée EUROPE, jugée vraie
    erreur le 01/10 ; Rémy : « l'offre n'est pas activable aux États-Unis, mais fonctionne en Europe ») : chez certains
    marchands, le mot de région de l'URL ne dit pas quels pays la clé couvre, et leur page est illisible depuis le
    serveur. `[region.unsure]` de leur config : zone du marchand -> zones AllKeyShop pour lesquelles la contradiction
    n'est qu'un doute. Quand elle est le seul problème de l'offre, l'offre part en À VÉRIFIER (« en doute »), quel que
    soit son rang : l'équipe lit les pays d'activation sur la page. Renvoie les raisons du doute, ou None."""
    unsure = (cfg.get("region") or {}).get("unsure") or {}
    found = result.get("zones") or []
    if (not unsure or not found or set(result["kinds"]) != {"zone"}
            or not all(r.startswith("région : AllKeyShop ") for r in result["reasons"])):
        return None
    zone = aks_zone(offer)
    if not all(zone in unsure.get(z, ()) for z in found):
        return None
    return ["en doute : %s ; chez %s, une clé %s peut s'activer en %s : vérifier les pays d'activation sur la page du marchand"
            % (r, cfg.get("name", "ce marchand"), "/".join(found), offer["region"]) for r in result["reasons"]]


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
    return None  # « url », et « page-variation » tant que la page n'est pas lue (voir check_offer)


# ---- Analyse d'une URL ou d'un titre marchand --------------------------------

def norm(text):
    """« EA SPORTS FC 27 » -> « ea-sports-fc-27 » ; « S.T.A.L.K.E.R. 2 » -> « stalker-2 »."""
    text = re.sub(r"[\u2122\u00ae\u00a9\u2120]", " ", text)  # ™ ® © ℠ (NFKD ferait de ™ les lettres « TM »)
    text = text.replace("'", "")  # « Marvel's » -> « marvels », comme « Marvel’s » et les URL (audit du 02/10/2026)
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
    return " ".join(repair_slug(PAGE_EXTENSION_RE.sub("", unquote_all(s))) for s in segments)


PAGE_EXTENSION_RE = re.compile(r"\.(?:html?|php|aspx?)$", re.IGNORECASE)  # « Planet-Zoo-2.html » : pas de « 2html »


def unquote_all(segment):
    """Décode jusqu'à stabilité : CJS CDKeys encode deux fois (« E%252dDay » -> « E%2dDay » -> « E-Day ») ; audit du
    02/10/2026, le nom n'était reconnu que grâce à la tolérance d'un mot manquant."""
    for _ in range(3):
        decoded = urllib.parse.unquote(segment)
        if decoded == segment:
            break
        segment = decoded
    return segment


def shop_url_text(url, cfg):
    """url_text, moins le préfixe que le marchand met devant toutes ses URL quand l'URL nomme une autre plateforme
    (`[url] noise_prefix` : Eneba « steam-…-xbox-live-key »)."""
    text = url_text(url)
    noise = (cfg.get("url") or {}).get("noise_prefix") or ()
    for segment in text.split(" "):
        words = norm(segment).split("-")
        if words and words[0] in noise and text_platform_groups("-".join(words[1:])) - text_platform_groups(words[0]):
            return text.replace(segment, "-".join(segment.split("-")[1:]), 1)
    return text


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
        # sigle des premiers mots : « Attack on Titan 3 » -> « AOT 3 » (PS Store : « A.O.T. 3 »). Toujours suivi du
        # reste du nom : un sigle du nom entier (« ron », « ace », « eft ») se trouvait dans n'importe quelle URL
        # (« hearts-of-iron » sur la page Ready or Not), et jamais sur un nom à suffixe de plateforme (« mns »,
        # « vxs ») : audit de la détection du 02/10/2026
        words = norm(name).split("-")
        if has_platform_suffix(name) or norm(name).endswith("-switch-ii"):
            continue
        run = 0
        while run < len(words) and words[run].isalpha() and words[run] not in ARABIC:  # pas de chiffre romain
            run += 1
        for k in range(3, run + 1):
            if words[k:]:
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


# Audit de la détection du 02/10/2026 (rejeu des 3 614 URL en mémoire contre les 495 pages suivies : 824 paires
# « page A, vraie URL du produit B » passaient le contrôle du nom ; 80 après ces règles) :
REMASTER_WORDS = {"remastered", "remaster", "remake", "hd"}  # du nom, ils comptent : l'original n'est pas le remaster
PLATFORM_HEADS = {"switch", "playstation", "ps", "windows", "win", "xbox"}  # « switch-2 » n'est pas un 2e épisode
NOT_EPISODE_AFTER = {"year", "years", "month", "months", "pack", "packs", "player", "players", "hours", "hour", "day", "days"}
# mots qui peuvent remplir la place d'un mot absent du nom sans nommer un autre produit (service, plateforme, édition, zone)
FILLER_WORDS = {"pre", "order", "purchase", "incl", "early", "access", "only", "language", "english", "standard", "version",
                "full", "instant", "delivery", "activation", "region", "free", "new", "official", "the", "of", "a", "an",
                "and", "for", "with", "playstation", "nintendo", "switch", "xbox", "series", "one", "pc", "mac", "windows",
                "win", "steam", "key", "cd", "eshop", "psn", "download", "digital", "code", "global", "europe", "eu", "uk",
                "us", "na", "row", "ww", "emea", "html", "htm", "php", "aspx", "product", "products", "category", "game",
                "games", "buy", "cheap", "item", "p"}


def is_episode_marker(tokens, i, product_years):
    """Un numéro d'épisode (2 à 30, ii à xx) ou une année autre que celle du nom, à la position i du texte : ce qui
    suit le nom et en fait un autre jeu (« titanfall-2 », « red-dead-redemption-2 », « football-manager-2023 »)."""
    token = tokens[i]
    if i + 1 < len(tokens) and tokens[i + 1] in NOT_EPISODE_AFTER:  # « 1-year-anniversary », « 4-pack »
        return False
    if token.isdigit():
        value = int(token)
        if 1980 <= value <= 2035:
            return bool(product_years) and token not in product_years
        if value == 1 and i == len(tokens) - 1:
            return False  # suffixe de dédoublonnage (GAMIVO « stardew-valley-1 »)
        return 1 <= value <= 30
    return token in ARABIC and token not in ("i", "x")


def name_core_words(names):
    """Les mots du nom, sans son suffixe de plateforme (« xbox », « series » ne sont pas le nom)."""
    out = set()
    for name in names:
        words = norm(name).split("-")
        for suffix in ("nintendo switch ii", "switch ii") + PLATFORM_SUFFIXES:
            s = norm(suffix).split("-")
            if len(words) > len(s) and words[-len(s):] == s:
                words = words[:-len(s)]
                break
        out |= set(words)
    return out


def exact_in(c, tokens, core, product_years):
    """Le nom compacté `c` dans le texte, aligné sur des mots (début et fin), et pas suivi d'un numéro d'épisode ou d'une
    autre année : « rust » n'est pas dans « rusty-lake », « titanfall » n'est pas « titanfall-2 »."""
    starts, ends, pos = set(), {}, 0
    for i, t in enumerate(tokens):
        starts.add(pos)
        pos += len(t)
        ends[pos] = i
    text = "".join(tokens)
    j = text.find(c)
    while j != -1:
        if j in starts and j + len(c) in ends:
            nxt = ends[j + len(c)] + 1
            if not (nxt < len(tokens) and is_episode_marker(tokens, nxt, product_years) and tokens[nxt] not in core):
                return True
        j = text.find(c, j + 1)
    return False


def bundle_names_product(names, normed):
    """Un lot (édition Bundle, Pack, Collection…) porte un autre nom que le jeu, mais il en reprend au moins un mot
    distinctif : 4 lettres ou plus, hors mots d'édition et de service, entier ou par ses 5 premières lettres
    (« dredging » pour DREDGE)."""
    tokens = [t for t in normed.split("-") if t]
    distinctive = {w for w in name_core_words(names) if len(w) >= 4 and not w.isdigit() and w not in SOFT_WORDS
                   and w not in FILLER_WORDS and w not in LABEL_NOISE and w not in GENERIC_EDITION_WORDS}
    return any(t == w or (len(w) >= 5 and len(t) >= 5 and t[:5] == w[:5]) for w in distinctive for t in tokens)


def name_match(names, normed, extra_ok=()):
    """« exact » si un des noms est dans le texte (aligné sur des mots), « partial » si ses mots significatifs y sont.

    Sur un nom long (4 mots significatifs ou plus), un seul mot peut manquer, sauf un nombre, sauf le dernier (c'est
    lui qui distingue le nouveau jeu : « Super Mario Party Jamboree », « Jedi Survivor »), et sauf si un autre mot
    occupe sa place (« Liberty » pour « Vice » dans « Grand Theft Auto Vice City ») : un vieux Mario sur la page du dernier Mario, c'est
    l'erreur à ne jamais laisser passer. `extra_ok` : les mots de l'édition de l'offre, qui peuvent occuper la place.
    """
    tokens_list = [t for t in normed.split("-") if t]
    product_tokens = {w for n in names for w in norm(n).split("-")}
    product_years = {t for t in product_tokens if t.isdigit() and 1980 <= int(t) <= 2035}
    core = name_core_words(names)
    for name in names:
        c = compact(name)
        if c and exact_in(c, tokens_list, core, product_years):
            return "exact"
    # un numéro d'épisode ou une année qui suit un mot du nom, et que le nom n'a pas : un autre jeu de la série
    allowed = core | {y[2:] for y in product_years}
    if any(t not in allowed and i > 0 and tokens_list[i - 1] in core and is_episode_marker(tokens_list, i, product_years)
           for i, t in enumerate(tokens_list)):
        return None
    tokens, after_platform = set(), False  # les chiffres d'une plateforme (« switch-2 », « playstation-4-5 ») ne comptent pas
    for t in tokens_list:
        if not (t.isdigit() and after_platform):
            tokens.add(t)
        after_platform = t in PLATFORM_HEADS or (after_platform and t.isdigit())
    for name in names:
        significant = [w for w in norm(name).split("-") if w and (w not in SOFT_WORDS or w in REMASTER_WORDS)]
        missing = [w for w in significant if w not in tokens]
        if len(significant) >= 3 and len(missing) == 1 and has_word(missing[0], tokens):
            missing = []  # un seul mot à une lettre près, sur un nom d'au moins 3 mots (« pokmon ») ; pas « Portal 2 » / « mortal »
        if significant and not missing:
            return "partial"
        if (len(significant) >= 4 and len(missing) == 1 and not (missing[0].isdigit() or missing[0] in ARABIC)
                and missing[0] not in NEVER_MISSING and not has_platform_suffix(name) and missing[0] != significant[-1]):
            # la tolérance vaut pour le nom sans « Nintendo Switch » : sinon « Pokémon Bouclier »
            # passerait pour « Pokemon Sword Nintendo Switch » (étude du 30/09/2026)
            gone = missing[0]
            others = [t for t in tokens_list if t not in product_tokens and t not in FILLER_WORDS and t not in LABEL_NOISE
                      and t not in extra_ok and len(t) >= 3 and not any(ch.isdigit() for ch in t) and gone not in t
                      and t not in EDITION_WORDS and t not in GENERIC_EDITION_WORDS]
            if not others:  # le mot est absent, pas remplacé par un autre
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
# Mots qu'une URL marchand ajoute après le nom du produit sans nommer un autre produit : service, plateforme, zone,
# édition, langue (relevé sur les 4 212 URL OK en mémoire, 05/10/2026). Les identifiants de fiche (« p10001977 »,
# « i10000515240006 ») contiennent des chiffres : ils ne comptent pas non plus.
TAIL_SERVICE_WORDS = {"xs", "xboxseries", "xboxoneseries", "xboxone", "xboxwindows", "onexbox", "pcxbox", "steamgift",
                      "steamkey", "com", "net", "checkout", "launcher", "linux", "mac", "macos", "os", "online", "ms", "java",
                      "bedrock", "gogcom", "drm", "eur", "usd", "gbp", "cdkey", "pcsteam", "alter", "play", "anywhere",
                      "website", "multi", "multilanguage", "languages", "lang", "vr", "ps",
                      "limited", "time",  # « iconic-edition-time-limited-pre-purchase » (Muve, F1 25, 06/10/2026)
                      # revue des doutes du 06/10/2026 : zones collées (« euus », « euna »), mentions de clé, lanceur GIANTS
                      # (Farming Simulator), « green gift » (Rockstar), Xbox One (« xone »), codes de langue
                      "euus", "euna", "useu", "naeu", "restricted", "uncut", "advanced", "access", "early", "giants",
                      "software", "green", "xone", "eng", "mx",
                      # éditeurs (« mojang », « ubi »), cartes de GTA Online, pièces G2A, délai de livraison (« up to 12
                      # hours »), versions coupées (« cut »), « european union », abréviations (« pcw », « sx »), « numérique »
                      "mojang", "ubi", "shark", "cash", "coins", "hours", "up", "to", "delivery", "instant", "cut", "union",
                      "pcw", "sx", "numerique"}
KNOWN_TAIL_WORDS = (LABEL_NOISE | FILLER_WORDS | set(EDITION_WORDS) | GENERIC_EDITION_WORDS | set(GIFT_WORDS)
                    | set(ACCOUNT_WORDS) | {w for d in DLC_WORDS for w in d.split("-")} | set(FORBIDDEN_REGION_WORDS)
                    | TAIL_SERVICE_WORDS | {w for ws in MERCHANT_ZONE_WORDS.values() for x in ws for w in x.split("-")}
                    | {w for ws in PLATFORM_FAMILIES.values() for x in ws for w in x.split("-")} | set(ARABIC))
DOUBT_PREFIX = "en doute : mots en plus après le nom"


def name_words(names):
    """Les mots d'un nom, tels que les marchands les écrivent : avec et sans l'apostrophe (« Belmont's » : « belmonts » ou
    « belmont-s »), et deux mots voisins collés (« Wu Kong » : « wukong ») (revue des doutes du 06/10/2026)."""
    out = set()
    for name in names:
        for variant in (name, re.sub(r"['’]", " ", name)):
            words = [w for w in norm(variant).split("-") if w]
            out |= set(words) | {a + b for a, b in zip(words, words[1:])}
    return out


def singular(word):
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def one_letter_apart(a, b):
    """Deux orthographes d'un même mot, une lettre de différence (« chernobyl » / « chornobyl », « resynched » / « resynced »)
    ou deux lettres voisines inversées (« webiste » / « website »)."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diff = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
        return len(diff) == 1 or (len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def familiar(word, known, names):
    """Un mot inconnu qui n'en est pas un (revue des doutes du 06/10/2026) : « ™ » collé (« fctm » : « fc »), deux mots
    connus collés (« seriesxbox »), une lettre de différence avec un mot du nom ou de l'édition (`names`), abréviation
    d'un mot de l'édition (« enh » : enhanced)."""
    if word.endswith("tm") and word[:-2] in known:
        return True
    if any(word[:i] in known and word[i:] in known and max(i, len(word) - i) >= 4 for i in range(2, len(word) - 1)):
        return True  # « seriesxbox », « xboxpc »
    if len(word) >= 6 and any(len(w) >= 6 and one_letter_apart(word, w) for w in names):
        return True
    if len(word) >= 5 and any(len(w) >= 5 and one_letter_apart(word, w) for w in KNOWN_TAIL_WORDS):
        return True  # fautes de frappe du marchand : « webiste », « digtal », « steamm », « globa »
    return len(word) >= 3 and any(len(w) > len(word) and w.startswith(word) for w in names if w.isalpha())


def tail_words(product, offer, normed, page_words=()):
    """Les mots qui suivent le nom du produit dans l'URL et ne disent rien de connu (service, plateforme, zone, édition,
    langue, identifiant) : ils nomment souvent un autre jeu ou un DLC (« minecraft-dungeons-2 » pour Minecraft,
    « control-resonant » pour Control, « elden-ring-shadow-of-the-erdtree » pour Elden Ring). Romain, 05/10/2026 :
    « alerter tous ces cas », une alerte par page et par mots (apply_tail_words). Triés, sans doublons : la clé.

    Revue des doutes du 06/10/2026 (les 15 jugés étaient des faux positifs) : le nom est la plus longue suite de mots du
    produit (pas le « 5 » de « game-playstation-5-spain ») ; les mots du nom et de l'édition comptent avec et sans
    apostrophe, au pluriel comme au singulier, collés (name_words) ; les mots du nom de la page AllKeyShop sont connus
    (`page_words` : « resident-evil-4-remake ») ; un mot entre deux mots de l'édition en fait partie (« megalodon shark
    card ») ; pour un bundle ou une édition « X + Y », les mots avant le mot bundle/pack sont le nom du bundle (« rally
    bundle », « criminal enterprise starter pack »)."""
    tokens = [t for t in normed.split("-") if t]
    product_words = name_words(name_variants(product))
    end, best, i = None, 0, 0
    while i < len(tokens):
        j = i
        while j < len(tokens) and tokens[j] in product_words:
            j += 1
        if j - i > best:
            best, end = j - i, j
        i = max(j, i + 1)
    if end is None:
        return []
    tail = drop_language_lists(tokens[end:])
    edition = offer.get("edition") or ""
    if is_bundle(edition):
        return []  # un bundle contient d'autres jeux (« le bundle inclus bien le jeu », Minecraft Dungeons, Rémy, 05/10/2026)
    edition_words = name_words([edition])
    if "goty" in edition_words:  # « GOTY » s'écrit « game of the year »
        edition_words |= {"game", "of", "the", "year"}
    known = KNOWN_TAIL_WORDS | product_words | edition_words | set(page_words)
    known_singular = {singular(w) for w in known}
    unknown = {k for k, t in enumerate(tail) if len(t) > 1 and not any(c.isdigit() for c in t)
               and t not in known and singular(t) not in known_singular
               and not familiar(t, known, product_words | (edition_words - GENERIC_EDITION_WORDS))}
    def side(k, step):  # le premier mot connu de ce côté-là
        k += step
        while 0 <= k < len(tail) and k in unknown:
            k += step
        return tail[k] if 0 <= k < len(tail) else None
    proper = edition_words - GENERIC_EDITION_WORDS - set(EDITION_WORDS)  # « megalodon », « card », pas « edition »
    added = edition.split("+", 1)[1] if "+" in edition else ""
    if added and set(norm(added).split("-")) <= GENERIC_EDITION_WORDS | {"content", "contents"}:
        return []  # « Standard + DLC » : AllKeyShop ne nomme pas le contenu, le marchand le nomme (« undead nightmare »)
    bundle_like = is_bundle(edition) or "+" in edition
    if bundle_like and any(t in BUNDLE_WORDS for t in tokens[:end]):
        return []  # une page de bundle (Steam : « bundle/86153/Dragon_Shelter_x_Amber_Isle ») : les autres jeux du bundle
    bundle_at = max((k for k, t in enumerate(tail) if t in BUNDLE_WORDS), default=-1) if bundle_like else -1
    # « megalodon shark card » : entre deux mots de l'édition ; « stranger things edition » : d'un mot de l'édition au mot
    # « edition »
    return sorted({tail[k] for k in unknown
                   if not (side(k, -1) in proper and (side(k, 1) in proper or side(k, 1) == "edition")) and not k < bundle_at})


def product_family(product):
    """Le jeu sans sa plateforme : « Ace Combat 8 Xbox Series », « Ace Combat 8 PS5 » -> « ace-combat-8 »."""
    base = norm(product or "")
    for suffix in PLATFORM_SUFFIXES:
        if base.endswith("-" + norm(suffix)) and base != norm(suffix):
            return base[:-len(norm(suffix)) - 1]
    return base


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
    """Un titre de page court qui est la FIN du nom AllKeyShop, sans son suffixe de plateforme (« UFC 5 » pour « EA Sports
    UFC 5 PS5 », « Wings of Theve »), avec au moins deux mots significatifs : « partial ». Jamais un début du nom : « God
    of War » n'est pas « God of War Ragnarok », « Black Ops » n'est pas « Black Ops 7 » (audit du 02/10/2026)."""
    for segment in re.split(r"\s[|\-\u2013\u2014]\s|\|", text):
        seg = [w for w in norm(segment).split("-") if w and w not in SOFT_WORDS]
        if len(seg) < 2:
            continue
        for name in names:
            words = [w for w in norm(name).split("-") if w and w not in SOFT_WORDS]
            for suffix in PLATFORM_SUFFIXES:
                s = norm(suffix).split("-")
                if len(words) > len(s) and words[-len(s):] == s:
                    words = words[:-len(s)]
                    break
            if len(seg) < len(words) and words[-len(seg):] == seg:
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
    if re.search(r"GLOBAL|WORLDWIDE|REGION FREE|ENGLISH|ENG ONLY|EN ONLY|EN/FR", t):
        return "GLOBAL"
    # « XBOX/PC », « XBOX X|S » sans pays : mondiales. Chaque champ à part : le nom de filtre et le nom affiché sont
    # concaténés plus haut (« XBOX/PC XBOX/PC »), et les ancres ne trouvaient jamais rien (audit du 02/10/2026 : 263
    # offres sans zone comparée)
    if any(re.fullmatch(r"XBOX/PC|XBOX X\|S", (x or "").strip().upper()) for x in (offer.get("region_filter"), offer.get("region"))):
        return "GLOBAL"
    return None


def merchant_zones(words):
    return {z for z, ws in MERCHANT_ZONE_WORDS.items() if any(re.search(r"(^|-)%s(-|$)" % re.escape(w), words) for w in ws)}


def zone_coverage(zones):
    """Les pays que couvre l'offre du marchand. Règle générale de traitement des régions (Rémy, validée par Romain le
    05/10/2026, pour tous les marchands : « si l'offre est activable au US et en EU on considère que c'est du global ») :
    une offre qui couvre l'Europe et les États-Unis compte comme GLOBAL."""
    cov = frozenset().union(*(ZONE_COVERAGE[z] for z in zones)) if zones else frozenset()
    return ZONE_COVERAGE["GLOBAL"] if ZONE_COVERAGE["EUUS"] <= cov else cov


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
    # une quantité, pas une année ; un nombre suivi d'un mot de monnaie est toujours une quantité (« apex-legends-2000-coins »,
    # audit du 02/10/2026 : 2000 passait pour une année)
    counted = any(re.search(r"(^|-)\d{3,}-%s(-|$)" % w, words) for w in CURRENCY_WORDS)
    if counted or any(n >= 100 and not 1980 <= n <= 2035 for n in numbers):
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
    match = name_match(names, normed, extra_ok=set(norm(offer.get("edition") or "").split("-")))
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
    if match is None and is_bundle(offer["edition"]) and bundle_names_product(names, normed):
        # un bundle porte un autre nom (« The Witcher Trilogy Pack ») ; mais un mot distinctif du nom y est
        # (audit du 02/10/2026 : sans ce mot, une URL Sonic passait sur la page de The Witcher 3 en « Starter Pack »)
        notes.append("édition %s : nom non contrôlé en entier (un mot du nom présent)" % offer["edition"])
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
    if "season-pass" in dlc and "pass" in dlc:
        dlc.remove("pass")  # le même mot
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


VARIATION_LABEL_RE = r'<label\b[^>]*>\s*<input\b[^>]*\bvalue="%s"[^>]*>(.*?)</label>'


def url_variation_label(dom, url, param="variation"):
    """Page à variantes (CJS CDKeys : AR (Argentina), Europe, USA… sur une même fiche) : le libellé de la variante que
    choisit le lien (`?variation=699` -> « Europe »), lu sur le bouton dont la valeur est celle du paramètre, prix
    retirés. None si le lien n'a pas le paramètre ou si la page ne l'a pas."""
    value = (urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get(param) or [""])[0]
    if not re.fullmatch(r"[\w-]{1,40}", value or ""):
        return None
    m = re.search(VARIATION_LABEL_RE % re.escape(value), dom, re.DOTALL)
    if not m:
        return None
    name = re.search(r'class="[^"]*variation-name[^"]*"[^>]*>(.*?)<', m.group(1), re.DOTALL)
    label = html.unescape(re.sub(r"<[^>]+>", " ", name.group(1) if name else m.group(1)))
    label = re.sub(r"[£€$]\s?\d[\d.,]*|\d[\d.,]*\s?[£€$]", " ", label)
    return re.sub(r"\s+", " ", label).strip() or None


# Les 27 pays de l'Union européenne (codes ISO) : une clé « couvre l'Europe » quand elle s'active dans chacun.
EU27 = ("at", "be", "bg", "cy", "cz", "de", "dk", "ee", "es", "fi", "fr", "gr", "hr", "hu", "ie", "it", "lt", "lu",
        "lv", "mt", "nl", "pl", "pt", "ro", "se", "si", "sk")
SUPPORTED_COUNTRIES_RE = re.compile(r'"supported_countries"\s*:\s*\[')
URL_TRAILING_ID_RE = re.compile(r"-(\d+)/?$")


def region_from_countries(codes):
    """La région d'une clé d'après la liste des pays où elle s'active (règle générale, pour tout marchand dont la page
    donne cette liste) : l'Europe (les 27 pays de l'UE) et les États-Unis = « eu-us », comptée GLOBAL (zone_coverage) ;
    l'Europe seule = « eu » ; les États-Unis sans l'Europe = « usa » ; ni l'un ni l'autre = « row »."""
    europe = all(c in codes for c in EU27)
    return "eu-us" if europe and "us" in codes else "eu" if europe else "usa" if "us" in codes else "row"


def supported_countries_region(dom, url):
    """Lecteur de page « supported-countries » (format GameBoost : liste « supported_countries » de la fiche, JSON de la
    page) : la région de la clé d'après ses pays d'activation, pas d'après le mot de l'URL. Chez GameBoost, « ROW » veut
    dire « partout sauf le Japon » : 249 pays, Europe et États-Unis compris (Dying Light The Beast, 05/10/2026).
    None : liste introuvable."""
    text = html.unescape(dom or "")
    start = 0
    m_id = URL_TRAILING_ID_RE.search(urllib.parse.urlparse(url or "").path)
    if m_id:  # la fiche de la clé du lien (la page liste aussi les autres variantes : Global, Europe…)
        anchor = re.search(r'"gameKey"\s*:\s*\{\s*"id"\s*:\s*%s\b' % m_id.group(1), text)
        if anchor:
            start = anchor.start()
    m = SUPPORTED_COUNTRIES_RE.search(text, start)
    if not m:
        return None
    end = text.find("]", m.end())
    codes = set(re.findall(r'"code"\s*:\s*"([a-z]{2})"', text[m.end():end if end > 0 else None]))
    return region_from_countries(codes) if codes else None


def page_text_from_dom(dom, url, parser, variation_param="variation"):
    if parser == "playstation":
        return playstation_text(dom, url) or page_title_from_html(dom)
    text = page_title_from_html(dom)
    if parser == "selected-option" and text:
        option = selected_option_text(dom)
        if option:
            text = "%s | option choisie : %s" % (text, option)
    if parser == "supported-countries" and text:
        # la région se lit dans la liste des pays d'activation (GameBoost), elle remplace le mot de la page (« ROW »)
        region = supported_countries_region(dom, url)
        if region:
            parts = [p for p in text.split(" | ") if not p.upper().startswith("REGION ")]
            text = " | ".join(parts + ["REGION %s" % region])
    if parser == "url-variation" and text:
        # le champ « Region » de la page montre la variante mise en avant, pas celle du lien : il est remplacé
        parts = [p for p in text.split(" | ") if not p.upper().startswith("REGION ")]
        label = url_variation_label(dom, url, variation_param)
        text = " | ".join(parts + (["REGION %s" % label] if label else []))
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
        text = page_text_from_dom(body, url, page_cfg.get("parser"), page_cfg.get("variation_param", "variation"))
        # LDShop (option cochée) : le HTML servi en HTTP n'a pas les options, rendues en JavaScript : Chromium
        if text and not is_block_page(text) and not (page_cfg.get("parser") == "selected-option" and "option choisie" not in text):
            return text, "page (HTTP)"
    if cfg.get("browser", True):
        text = page_title(url, page_cfg.get("parser"), page_cfg.get("variation_param", "variation"))
        if text and not is_block_page(text):
            return text, "page (Chromium)"
    return None, None


def chromium_command(url, profile):
    """La commande Chromium : jamais en root. Le moniteur tourne en root ; le rendu d'une page tierce passe sous
    CHROMIUM_USER (nobody), avec le bac à sable de Chromium (audit du 02/10/2026 : « --no-sandbox » en root), un profil
    jetable et un environnement vide (ni webhooks ni jeton)."""
    cmd = [CHROMIUM, "--headless=new", "--disable-gpu", "--disable-dev-shm-usage", "--user-data-dir=" + os.path.join(profile, "p"),
           "--user-agent=" + BROWSER_UA, "--virtual-time-budget=20000", "--dump-dom", url]
    if os.geteuid() == 0:
        user = pwd.getpwnam(CHROMIUM_USER)
        os.chown(profile, user.pw_uid, user.pw_gid)
        cmd = ["setpriv", "--reuid=%d" % user.pw_uid, "--regid=%d" % user.pw_gid, "--clear-groups"] + cmd
    return cmd, {"HOME": profile, "PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"}


def chromium_dom(url):
    """DOM de la page marchand rendu par Chromium sans écran, ou None si impossible."""
    if not shutil.which(CHROMIUM) or not safe_target(url):
        return None
    profile = tempfile.mkdtemp(prefix="price-check-chromium.")
    try:
        cmd, env = chromium_command(url, profile)
        return subprocess.run(cmd, capture_output=True, timeout=90, env=env, cwd="/").stdout.decode("utf-8", "replace")
    except (OSError, KeyError, subprocess.TimeoutExpired):
        return None
    finally:
        shutil.rmtree(profile, ignore_errors=True)


def page_title(url, parser=None, variation_param="variation"):
    """Titre, og:title et h1 de la page marchand, via Chromium sans écran. None si impossible."""
    dom = chromium_dom(url)
    return None if dom is None else page_text_from_dom(dom, url, parser, variation_param)


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


def other_listing(url, served, cfg):
    """Le lien et la fiche servie sont-ils deux fiches différentes chez ce marchand, d'après l'identifiant de fiche de sa
    config (Kinguin : « /category/<id>/ ») ? Romain, 05/10/2026 : une redirection vers une fiche d'un autre identifiant,
    même nom, même région, même plateforme, même édition, est une rupture (le prix AllKeyShop peut être périmé)."""
    pattern = ((cfg or {}).get("redirect") or {}).get("listing_id")
    if not pattern or not url:
        return False
    a = re.search(pattern, urllib.parse.urlparse(url).path)
    b = re.search(pattern, urllib.parse.urlparse(urllib.parse.urljoin(url, served)).path)
    return bool(a and b and a.group(1) != b.group(1))


def flag_out_of_stock(result, product, offer, served, url=None, cfg=None):
    """Groupe Kinguin : le lien mène à une autre fiche. Si elle dit la même chose de l'offre (nom, région, plateforme,
    édition), Kinguin a seulement renommé sa fiche (« dayz-eu-steam-altergift » -> « dayz-eu-pc-steam-altergift » :
    24 des 25 redirections en mémoire le 02/10/2026) : une note. Sinon, c'est une autre offre servie à la place d'une
    fiche en rupture : alerte « en rupture, le prix reste dans le feed » (Romain, 02/10/2026 ; Stellaris : la clé EU
    du lien remplacée par la globale, Rust : « eu » -> « de »). La région n'est plus reprochée quand la fiche servie,
    ce que l'acheteur obtient, correspond à l'affichage (Stellaris, 01/10/2026)."""
    again = analyze(product, offer, url_text(served), "URL de la fiche servie")
    if offer_signature(again) == offer_signature(result) and not other_listing(url, served, cfg):
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


def evidence_of(served, page_text, page_via=None):
    """Ce que le contrôle a vu au-delà du lien : la fiche servie à sa place (groupe Kinguin : rupture ; Nintendo : la
    version anglaise) et le titre de la page lue, avec la façon de le lire (HTTP ou Chromium : deux lectures d'une même
    page peuvent différer). Le recontrôle s'en sert pour dire si l'offre a changé chez le marchand alors que le lien
    est le même (fiche Kinguin de nouveau en stock : réparée, pas un faux positif)."""
    return {"served": norm(url_text(served)) if served else "", "page": norm(page_text)[:300] if page_text else "",
            "via": page_via or ""}


def check_offer(product, offer):
    """Suit la redirection AllKeyShop de l'offre et confronte l'URL marchand au produit.

    Renvoie {"verdict", "reasons", "notes", "url", "method", "evidence"}.
    """
    served = page_text = page_via = None  # fiche servie à la place du lien, page lue et comment : voir evidence_of

    def done(verdict, method, reasons, notes, **extra):
        return dict({"verdict": verdict, "url": url, "method": method, "reasons": reasons, "notes": notes,
                     "evidence": evidence_of(served, page_text, page_via)}, **extra)

    for attempt in (1, 2):
        try:
            status, location, body = http_get(REDIRECTION_URL % (offer["id"], offer["merchant"]), AKS_UA)
        except OSError as e:
            raise CheckError("redirection AllKeyShop : %s" % e)
        if status < 500 or attempt == 2:
            break
        time.sleep(REQUEST_DELAY * 3)  # 503 passager de la redirection AllKeyShop : un second essai
    time.sleep(REQUEST_DELAY)
    if status in (301, 302, 303, 307, 308) and location:
        # le lien redirige tout droit chez le marchand (aujourd'hui : page « Redirecting… » en 200) : l'URL est lue
        # dans l'en-tête, la redirection n'est pas suivie avec l'UA AKS/Staff
        url = urllib.parse.urljoin(REDIRECTION_URL % (offer["id"], offer["merchant"]), location)
    elif status != 200:
        raise CheckError("redirection AllKeyShop HTTP %s" % status)
    else:
        url = merchant_url(body)
    if not url:
        raise CheckError("URL marchand introuvable dans la page de redirection")
    url = unwrap_affiliate(url)
    if not safe_target(url):
        raise CheckError("URL marchand refusée : %s" % url[:120])
    cfg = merchant_config(url, offer["merchantName"])
    result, method = analyze(product, offer, shop_url_text(url, cfg), "URL", region=region_text(url, cfg)), "URL"
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
            served = alt
            return done("SUSPECT" if result["reasons"] else "OK", "URL de la version %s" % hreflang, result["reasons"], result["notes"])

    location = None
    if redirect_untrusted(cfg) or result["match"] is None:
        # Une requête chez le marchand sans suivre la redirection. Groupe Kinguin : à CHAQUE offre, une redirection vers
        # une autre fiche = la fiche du lien est en rupture, le prix reste dans le feed (Romain, 02/10/2026 : « on a juste
        # besoin de suivre les redirections… si on voit une redirection vers une URL différente, on lance l'alerte »).
        # Autre groupe : seulement quand l'URL ne nomme pas le produit (Instant Gaming « /en/4860-/ », Fanatical), ou en
        # nomme un autre (slug périmé) : c'est l'URL finale qui compte.
        try:
            _, location, _ = http_get(url, BROWSER_UA, follow=False)
        except OSError as e:
            if redirect_untrusted(cfg):  # sans la sonde, une rupture passerait pour une offre OK : contrôle raté, retenté
                raise CheckError("sonde de la fiche marchand : %s" % e)
            location = None
        time.sleep(REQUEST_DELAY)
    if location:
        url2 = unwrap_affiliate(urllib.parse.urljoin(url, location))
        if redirect_untrusted(cfg):
            if moved(url, url2):
                flag_out_of_stock(result, product, offer, url2, url, cfg)
                served = url2
        elif result["match"] is None:
            result2 = analyze(product, offer, shop_url_text(url2, cfg), "URL après redirection du marchand", region=region_text(url2, cfg))
            if result2["match"] or result2.get("label"):  # la fiche finale nomme le produit, ou un autre
                result, method, url = result2, "URL après 301 marchand", url2

    if (result["match"] is None and result.get("label") and not (cfg.get("page") or {}).get("parser")
            and not cfg.get("localized") and not (cfg.get("product_name") or {}).get("hreflang")):
        # l'URL (finale) nomme un autre produit (Titanfall chez Kinguin, TORO 2 -> « Metal Garden ») : l'alerte part
        # sans lire la page (Romain, 02/10/2026 : « je vois pas pourquoi tu veux vérifier la page quand on a déjà un
        # problème détecté à la base »). Sauf marchand dont la page décide (LDShop : option cochée ; PS Store), aux
        # titres traduits (« localized ») ou contrôlé sur sa version anglaise (Nintendo : un slug traduit n'est pas
        # un autre produit quand la version en-GB n'a pas pu être lue)
        return done("SUSPECT", method, result["reasons"], result["notes"])

    variation_param = (cfg.get("page") or {}).get("variation_param", "variation")
    if ((cfg.get("region") or {}).get("from") == "page-variation" and result["match"] is not None
            and urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get(variation_param)):
        # la région est celle de la variante que choisit le lien (CJS CDKeys : ?variation=699 = Europe), pas dans l'URL :
        # la page est lue (sinon une clé AR (Argentina) affichée EUROPE passait sur la seule URL)
        variation_text, variation_via = merchant_page_text(url, cfg)
        chosen = [p[len("REGION "):] for p in (variation_text or "").split(" | ") if p.startswith("REGION ")]
        if chosen:
            page_text, page_via = variation_text, variation_via
            result = analyze(product, offer, shop_url_text(url, cfg), "URL", region=chosen[0])
            result["notes"].append("région lue sur la variante choisie par le lien : %s" % chosen[0])
            method = "URL et variante de la page (%s)" % (variation_via or "page")
        else:
            result["notes"].append("variante choisie par le lien non lue (page illisible) : région de l'URL seule")

    region_from_page = False
    if (cfg.get("region") or {}).get("from") == "page" and result["match"] is not None and "zone" in result["kinds"]:
        # le mot de région de l'URL ne fait pas foi chez ce marchand (GameBoost : « ROW » = partout sauf le Japon) : la
        # page dit où la clé s'active, et c'est cette région qui est comparée à celle d'AllKeyShop (règle générale :
        # Europe et États-Unis = GLOBAL, voir zone_coverage)
        countries_text, countries_via = merchant_page_text(url, cfg)
        chosen = [p[len("REGION "):] for p in (countries_text or "").split(" | ") if p.startswith("REGION ")]
        if chosen:
            page_text, page_via, region_from_page = countries_text, countries_via, True
            result = analyze(product, offer, shop_url_text(url, cfg), "URL", region=chosen[0])
            result["notes"].append("région lue sur la page, d'après les pays d'activation de la clé : %s" % chosen[0])
            method = "URL et pays d'activation de la page (%s)" % (countries_via or "page")
        else:
            result["notes"].append("pays d'activation non lus (page illisible) : région de l'URL seule")

    if result["match"] is None:
        # 2e repli : lire la page marchand (HTTP simple, puis Chromium si la config le permet)
        page_text, page_method = merchant_page_text(url, cfg)
        page_via = page_method
        if page_text is None:
            others = [r for r, k in zip(result["reasons"], result["kinds"]) if k != "name"]
            if others:  # le nom ne se vérifie pas, mais l'URL montre déjà un autre problème (Elden Ring : « PlayStation »)
                return done("SUSPECT", method, others, result["notes"] + ["nom du produit non vérifiable (page marchand illisible)"])
            return done("À VÉRIFIER", "aucune", [unverified_reason(offer, url)], [],
                        unverifiable=cfg.get("unverifiable", "first-price"))
        result, method = analyze(product, offer, page_text, "titre de la page"), page_method
        if cfg.get("localized") and result["kinds"] == ["name"]:
            # boutique au titre traduit (Amazon.fr : « Kirby et le monde oublié ») : un nom introuvable
            # n'est pas une preuve, un humain vérifie ; la réponse enrichit aliases.toml
            return done("À VÉRIFIER", method, ["titre du marchand dans une autre langue, nom non reconnu : %s" % page_text[:120]],
                        result["notes"], unverifiable=cfg.get("unverifiable", "first-price"))

    # le DLC aussi, sur une édition « X + Y » : le « + » du titre (le jeu plus le contenu) disparaît dans l'URL
    confirmable = [k for k in result["kinds"] if k in ("platform", "console", "zone")
                   or (k == "dlc" and "+" in offer["edition"])]
    if confirmable and method.startswith("URL") and not (cfg.get("region") or {}).get("from") == "query" and not region_from_page:
        # l'URL contredit AllKeyShop : avant d'alerter, on regarde la page (URL trompeuse chez Gamingdragons)
        page_text, page_via = merchant_page_text(url, cfg)
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
                served = urllib.parse.urljoin(url, canonical)
                flag_out_of_stock(result, product, offer, served, url, cfg)

    doubt = unsure_zone(result, offer, cfg)
    if doubt:  # G2A : une clé ROW affichée EUROPE est un doute, pas une erreur avérée (Romain, 06/10/2026)
        return done("À VÉRIFIER", method, doubt, result["notes"], unverifiable="report")
    return done("SUSPECT" if result["reasons"] else "OK", method, result["reasons"], result["notes"])


# ---- Alertes, état, boucle ---------------------------------------------------

ICONS = {"OK": "🟢", "SUSPECT": "🔴", "À VÉRIFIER": "🟠", "NON VÉRIFIABLE": "⚪"}
NOT_SENT = ("OK", "NON VÉRIFIABLE")  # verdicts gardés dans le journal et l'état, sans alerte (OK : sauf NOTIFY_OK)


def rank_label(offer):
    """« 1er prix de l'édition », « 2e prix de l'édition (compte) », ou « » sans rang connu."""
    r = offer.get("edition_rank")
    if not r:
        return ""
    return "%s prix de l'édition%s" % ("1er" if r == 1 else "%de" % r, " (compte)" if offer.get("account") else "")


def is_first_price(offer):
    """L'offre est un premier prix : l'une des 3 offres de clé les moins chères de son édition (pas un compte, rangé à
    part et masqué par défaut sur AllKeyShop)."""
    rank = offer.get("edition_rank")
    return isinstance(rank, int) and 1 <= rank <= FIRST_PRICES and not offer.get("account")


def is_urgent(offer, res):
    """Une urgence premier prix : un problème avéré (SUSPECT) sur un premier prix."""
    return res.get("verdict") == "SUSPECT" and is_first_price(offer)




def format_alert(label, rank, product, page_url, offer, res):
    where = rank_label(offer)
    lines = [
        (URGENT_PREFIX + "\n" if is_urgent(offer, res) else "")
        + f"{ICONS[res['verdict']]} **{res['verdict']}** · **{product}** ({label} #{rank}) · {offer['edition']}"
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


DISCORD_LIMIT = 2000  # caractères d'un message Discord : au-delà, le webhook répond 400


def send_discord(webhook, content):
    """Envoie un message sur le webhook. Un message trop long est coupé (sinon 400, et l'alerte ne partirait jamais) ;
    un 429 (trop de messages) est réessayé une fois après le délai demandé par Discord."""
    if len(content) > DISCORD_LIMIT:
        content = content[:DISCORD_LIMIT - 2] + " …"
    body = json.dumps({"content": content, "allowed_mentions": {"parse": []}}).encode()
    for attempt in (1, 2):
        req = urllib.request.Request(
            webhook, data=body, method="POST",
            headers={"Content-Type": "application/json", "User-Agent": "price-check (Discord webhook)"},
        )
        try:
            urllib.request.urlopen(req, timeout=30).close()
            return
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 2:
                raise
            try:
                wait = float(json.loads(e.read() or b"{}").get("retry_after") or e.headers.get("Retry-After") or 2)
            except (ValueError, AttributeError):
                wait = 2.0
            time.sleep(min(max(wait, 0.5), 30))


def muted():
    return bool(MUTE_UNTIL) and time.strftime("%Y-%m-%d %H:%M") < MUTE_UNTIL


def webhook_for(mode):
    """Webhook Discord d'un mode de pages (variable MODES[mode]["webhook"]), à défaut DISCORD_WEBHOOK_URL ; « urgent » :
    celui des urgences premiers prix (DISCORD_WEBHOOK_URL_URGENT), à défaut celui des top games."""
    if mode == "urgent":
        return os.environ.get(URGENT_WEBHOOK, "") or os.environ.get("DISCORD_WEBHOOK_URL", "")
    return os.environ.get(MODES.get(mode, {}).get("webhook", ""), "") or os.environ.get("DISCORD_WEBHOOK_URL", "")


def route_alert(msg, mode, send_mode, send_urgent=None):
    """Une alerte d'urgence premier prix (en-tête URGENT_PREFIX) part sur le webhook des urgences, avec le mode qui l'a
    trouvée (« Price check top », « Price check homepage ») ; toute autre alerte, sur le salon du mode. Sans webhook
    d'urgence (`send_urgent` None), tout part sur le salon du mode."""
    if send_urgent is not None and msg.startswith(URGENT_PREFIX):
        send_urgent(msg.replace(URGENT_PREFIX, "%s · %s" % (URGENT_PREFIX, MODES.get(mode, {}).get("label", mode)), 1))
    else:
        send_mode(msg)


BANNER_RULE = "━" * 28
LOOP_WHAT = {"top-games": "les tops : 5 premiers Popular, 4 premiers Coming soon PC",
             "homepage": "toute la homepage : widgets de la home, TOP 50 de chaque plateforme"}
LOOP_IDS = itertools.count(1)


def new_loop(mode, recheck=False, requested=None):
    """Une boucle : un passage d'un mode (run_mode), et ce que son bandeau en dit."""
    return {"id": next(LOOP_IDS), "mode": mode, "start": time.strftime("%d/%m/%Y %H:%M"), "recheck": recheck,
            "requested": requested, "channels": set()}


def loop_banner(loop, channel, resumed=False):
    """Le bandeau d'une boucle dans un salon : une règle et un titre (la séparation), ce que la boucle contrôle, la
    légende des messages qui suivent, comment trancher, le lien vers le guide de l'équipe. Jamais pris pour une alerte
    par le bot (il ne commence pas par un verdict). `resumed` : la boucle avait déjà posté dans ce salon et une autre y
    a posté entre-temps (une boucle des tops tourne entre deux pages de la homepage) : un bandeau « suite », court."""
    label = MODES.get(loop["mode"], {}).get("label", loop["mode"])
    if resumed:
        return "%s\n### ↪️ Suite de la boucle · %s, commencée le %s" % (BANNER_RULE, label, loop["start"])
    if loop.get("requested"):
        kind = " · passage demandé depuis l'admin par %s : toutes les offres recontrôlées" % loop["requested"]
    elif loop.get("recheck") == "flagged":
        kind = " · avec le recontrôle horaire des offres signalées"
    else:
        kind = ""
    lines = [BANNER_RULE, "# %s Nouvelle boucle · %s" % ("🚨" if channel == "urgent" else "🔄", label),
             "-# %s · %s%s" % (loop["start"], LOOP_WHAT.get(loop["mode"], label), kind)]
    if channel == "urgent":
        lines += ["Urgences premiers prix : un problème avéré (SUSPECT) sur l'une des 3 offres les moins chères d'une "
                  "édition. À traiter en premier.",
                  "Ce qui suit vient de cette boucle : 🚨 nouveau report · 📌 rappel d'un report existant."]
    else:
        lines.append("Ce qui suit vient de cette boucle : 🔴 🟠 nouveau report · 📌 rappel d'un report existant · "
                     "🔁 bilan du recontrôle.")
    # la note est facultative (Romain, 05/10/2026 : « si on est d'accord avec l'erreur décrite sur le report, il n'y a pas de raison de commenter »)
    lines += ["Chaque alerte a son fil « Feedback » : réponds **vrai**, **faux** ou **à discuter** ; une note seulement si tu "
              "n'es pas d'accord avec l'erreur décrite, ou pour préciser.",
              "📘 Guide de l'équipe : <%s>" % GUIDE_URL]
    return "\n".join(lines)


def make_announcer(key_of=webhook_for):
    """`announce(loop, channel, send, msg)` : le bandeau de la boucle avant son premier message dans un salon, et de
    nouveau (« suite ») quand une autre boucle y a posté entre-temps. Une boucle qui ne poste rien n'y met pas de
    bandeau (une boucle des tops toutes les 2 min 30). Un salon est reconnu à son webhook (un mode sans webhook propre
    partage celui des top games). Bandeau et message passent par le même `send` : pendant une pause Discord, ils
    attendent ensemble, dans l'ordre ; un bandeau qui n'a pas pu partir repart avec le message suivant."""
    last = {}  # salon (son webhook) -> la dernière boucle qui y a posté

    def announce(loop, channel, send, msg):
        key = key_of(channel)
        if last.get(key) != loop["id"]:
            resumed = key in loop["channels"]
            send(loop_banner(loop, channel, resumed))
            loop["channels"].add(key)
            last[key] = loop["id"]
            log.info("%s : bandeau « %s » dans le salon %s", loop["mode"], "suite de la boucle" if resumed else "nouvelle boucle",
                     channel)
        send(msg)

    def forget(channel):
        """Un message hors boucle (le rappel du matin) vient de passer dans ce salon : le message suivant de la boucle en
        cours y remet son bandeau (« suite »), la séparation reste visible."""
        last.pop(key_of(channel), None)
    announce.forget = forget
    return announce


def make_notifier(webhook, state, channel=""):
    """Envoie sur Discord, ou met en attente pendant une pause (`DISCORD_MUTE_UNTIL`), ou quand Discord ne répond pas
    (audit du 02/10/2026 : une nouvelle erreur trouvée au recontrôle dont l'envoi échouait n'était jamais renvoyée) :
    la file `queued` de state.json, envoyée dans l'ordre dès que possible. `channel` : le mode de pages dont le
    webhook enverra l'alerte mise en attente."""
    def notify(msg):
        if muted():
            state["queued"].append([channel, msg] if channel else msg)
            log.info("Discord en pause jusqu'au %s : alerte mise en attente (%d)", MUTE_UNTIL, len(state["queued"]))
            return
        if state["queued"]:  # des alertes attendent déjà : celle-ci passe après elles, l'ordre est gardé
            state["queued"].append([channel, msg] if channel else msg)
            return
        try:
            send_discord(webhook, msg)
        except Exception as e:
            state["queued"].append([channel, msg] if channel else msg)
            log.error("Envoi Discord impossible (%s) : alerte mise en file, renvoyée dès que Discord répond (%d en file)",
                      e, len(state["queued"]))
    return notify


def flush_queue(state, send):
    """Envoie les alertes mises en attente (pause, ou Discord injoignable), dans l'ordre : `send(msg, channel)`. Une
    alerte refusée par Discord (4xx autre que 429 : elle ne passera jamais) est retirée de la file, pas bloquante."""
    while state["queued"]:
        item = state["queued"][0]
        channel, msg = item if isinstance(item, list) else ("", item)
        try:
            send(msg, channel)
        except urllib.error.HTTPError as e:
            if not 400 <= e.code < 500 or e.code == 429:
                raise
            log.error("Alerte refusée par Discord (HTTP %s), retirée de la file : %s", e.code, msg[:200])
        state["queued"].pop(0)
        time.sleep(1)


def load_state(path):
    """L'état. Absent : vide (c'est ainsi qu'on recontrôle tout). Illisible : mis de côté (`.corrupt-<date>`) et repris
    de la copie horaire `state.json.bak` (audit du 02/10/2026 : un état illisible repartait de zéro sans un mot)."""
    state = {}
    try:
        with open(path) as f:
            state = json.load(f)
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as e:
        aside = "%s.corrupt-%s" % (path, time.strftime("%Y%m%d-%H%M%S"))
        log.error("%s illisible (%s) : mis de côté en %s, reprise de %s.bak", path, e, aside, path)
        try:
            os.replace(path, aside)
            with open(path + ".bak") as f:
                state = json.load(f)
        except (OSError, ValueError) as e2:
            log.error("pas de copie lisible (%s) : état vide, toutes les offres seront recontrôlées", e2)
            state = {}
    if not isinstance(state, dict):
        state = {}
    state.setdefault("checked", {})  # id d'offre -> verdict rendu
    state.setdefault("merchants", {})  # marchand -> méthodes de contrôle qui ont marché
    state.setdefault("queued", [])  # alertes en attente pendant une pause Discord
    for m in state["merchants"].values():  # ancien format : une seule méthode
        if "methods" not in m:
            m["methods"] = {m.pop("method", "URL"): 1}
    return state


BACKUP_EVERY = 3600  # copie de state.json, une fois par heure (audit du 02/10/2026 : aucune sauvegarde)


def save_state(path, state):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), prefix=os.path.basename(path) + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:  # un fichier temporaire à soi : deux écritures simultanées ne se mélangent pas
            json.dump(state, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    backup = path + ".bak"
    try:
        if not os.path.exists(backup) or time.time() - os.path.getmtime(backup) >= BACKUP_EVERY:
            shutil.copy2(path, backup + ".tmp")
            os.replace(backup + ".tmp", backup)  # un state.json perdu ou abîmé se reprend là (au plus une heure de retard)
            os.utime(backup)
    except OSError as e:
        log.warning("copie de state.json impossible : %s", e)


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


# Le bon salon d'une offre signalée (Romain, 03/10/2026 : « si tu passes sur les offres qui ont déjà été reportées, il
# faudra les reporter ce coup-ci dans le bon chan discord au prochain passage »). Le salon où part chaque alerte est
# noté depuis le 03/10 (`sent_to`) ; avant, il se déduit : un seul webhook, celui des top games, jusqu'au déploiement
# des salons par mode (01/10/2026 14:55, commit d34ebe9), puis le salon du mode.
MODE_CHANNELS_SINCE = "2026-10-01 14:55"


def mode_of_list(label):
    """Le mode d'une liste : les tops (« Popular », « Coming soon PC ») ou la homepage (tout le reste)."""
    return "top-games" if (label or "") in TOP_GAMES_LABELS else "homepage"


def mode_channel(entry, mode=None):
    """Le salon du mode d'une offre : le mode qui l'a contrôlée, à défaut celui du passage, à défaut celui de sa liste."""
    return entry.get("mode") or mode or mode_of_list(entry.get("list"))


def channel_of(entry, offer, mode=None):
    """Le bon salon d'une offre signalée : celui des urgences pour un SUSPECT sur un premier prix (au rang qu'elle a
    maintenant), sinon celui de son mode."""
    if entry.get("verdict") == "SUSPECT" and is_first_price(offer):
        return "urgent"
    return mode_channel(entry, mode)


def sent_channel(entry):
    """Le salon où l'alerte de l'offre est partie (voir MODE_CHANNELS_SINCE)."""
    if entry.get("sent_to"):
        return entry["sent_to"]
    if (entry.get("at") or "") < MODE_CHANNELS_SINCE:
        return "top-games"
    return mode_channel(entry)


def reroute_existing(entry, label, rank, product, page_url, offer, notify, mode):
    """Une offre déjà signalée (SUSPECT, À VÉRIFIER), encore en erreur, dont l'alerte n'est pas dans le bon salon : elle
    y est signalée une fois, au passage qui la voit, marquée « report existant » (ce n'est pas une nouvelle détection).
    Une urgence premier prix part du premier passage qui la voit ; une autre alerte attend un passage de son mode (une
    page des tops est aussi dans la homepage : elle ne part pas deux fois)."""
    if (entry.get("verdict") not in ("SUSPECT", "À VÉRIFIER") or entry.get("fixed_at")
            or (entry.get("decision") or {}).get("decision") == "faux"):
        return
    target = channel_of(entry, offer, mode)
    if sent_channel(entry) == target:
        entry.setdefault("sent_to", target)
        return
    if target != "urgent" and mode and target != mode:
        return
    res = {"verdict": entry["verdict"], "reasons": entry.get("reasons") or [], "notes": entry.get("notes") or [],
           "url": entry.get("url"), "method": entry.get("method") or "?"}
    msg = format_alert(label, rank, product, page_url, offer, res)
    note = "📌 **Rappel** · report existant (signalé le %s), %s" % (
        entry.get("at") or "?", "renvoyé dans le salon des urgences premiers prix" if target == "urgent" else "renvoyé dans le salon de son mode")
    head, _, rest = msg.partition("\n")
    msg = "%s\n%s\n%s" % (head, note, rest) if msg.startswith(URGENT_PREFIX) else "%s\n%s" % (note, msg)
    log.info("%s", msg.replace("\n", " | "))
    try:
        notify(msg)
    except Exception as e:
        log.error("Envoi Discord impossible, nouvel essai au prochain passage : %s", e)
        return
    entry["sent_to"] = target


def page_slug_words(page_url):
    """Les mots du nom de la page AllKeyShop (« buy-resident-evil-4-remake-xbox-series-compare-prices »)."""
    slug = urllib.parse.urlparse(page_url or "").path.rstrip("/").rsplit("/", 1)[-1]
    return {w for w in norm(slug).split("-") if w} - {"buy", "compare", "prices", "cd", "key", "keys"}


def apply_tail_words(res, state, page_url, key, stamp, product, offer):
    """Les mots en plus après le nom (Romain, 05/10/2026 : « alerter tous ces cas », « une alerte par page et par
    mots ») : une offre jugée OK sur son URL, mais dont l'URL ajoute après le nom des mots inconnus (tail_words), part en
    À VÉRIFIER, une fois par page et par mots. Les offres suivantes de la page aux mêmes mots restent À VÉRIFIER sans
    nouvelle alerte ; un « faux » sur la première apprend ces mots pour la page (les offres passent OK), un « vrai » en
    fait une erreur avérée pour toutes les offres de la page (SUSPECT). Registre : state["tail_words"][page][mots]."""
    if res.get("verdict") != "OK" or not str(res.get("method") or "").startswith("URL") or not res.get("url"):
        return res
    cfg = merchant_config(res["url"], offer.get("merchantName"))
    if (cfg.get("product_name") or {}).get("hreflang"):  # boutique localisée : l'URL traduit le nom (Nintendo eShop FR, IT)
        return res
    url = re.sub(r"&[\w-]+=[^/&?#]*", "", res["url"])  # « …-cd-key&roff=1 » (Kinguin) : un paramètre, pas un mot
    url = re.sub(r"-[A-Z0-9]{6,}(?=$|[/?#])", "", url)  # « …-cd-key-SCZXUHNQ » (K4G) : l'identifiant de la fiche
    tail = tail_words(product, offer, norm(shop_url_text(url, cfg)), page_slug_words(page_url))
    if not tail:
        return res
    words = " ".join(tail)
    reg = state.setdefault("tail_words", {}).setdefault(page_url, {})
    seen = reg.get(words)
    family = product_family(product)
    if seen is None:
        reg[words] = seen = {"offer": key, "at": stamp, "decision": None, "family": family}
    decision = seen.get("decision") if seen.get("offer") != key else None
    if decision is None:  # un « faux » sur ces mots vaut pour toutes les pages du jeu (Ace Combat 8 PC, Xbox, PS5 : 06/10/2026)
        for other_page, other in (state.get("tail_words") or {}).items():
            info = other.get(words)
            if other_page != page_url and info and info.get("decision") == "faux" and (info.get("family") or product_family(
                    (state["checked"].get(info.get("offer")) or {}).get("product"))) == family:
                decision, seen = "faux", info
                break
    if decision == "faux":
        return dict(res, notes=res["notes"] + ["mots en plus acceptés pour ce jeu : « %s » (offre %s jugée faux positif)" % (
            words, seen["offer"])])
    if decision == "vrai":
        return dict(res, verdict="SUSPECT", reasons=["mots en plus déjà jugés comme une erreur sur cette page : « %s » (offre %s)" % (
            words, seen["offer"])], tail=tail)
    out = dict(res, verdict="À VÉRIFIER", reasons=["%s : « %s »" % (DOUBT_PREFIX, words)], unverifiable="report", tail=tail)
    if seen.get("offer") != key:  # le même doute qu'une offre déjà signalée de la page : pas de nouvelle alerte
        out.update(quiet=True, notes=res["notes"] + ["même doute que l'offre %s : une seule alerte par page et par mots" % seen["offer"]])
    return out


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
    entry.pop("removed_at", None)  # revenue sur sa page : ce recontrôle-ci la juge
    before = offer_facts(entry.get("url"), entry.get("region"), entry.get("region_filter"), entry.get("platform"), entry.get("edition"))
    after = offer_facts(res.get("url") or entry.get("url"), offer.get("region"), offer.get("region_filter"), offer.get("platform"),
                        offer.get("edition"))
    if res["verdict"] in ("À VÉRIFIER", "NON VÉRIFIABLE"):
        entry["last_recheck"] = stamp
        entry["seen"] = now
        outcome["unknown"].append((entry, (res.get("reasons") or ["recontrôle sans conclusion"])[0]))
        return
    names = ("URL", "région", "région", "plateforme", "édition")  # région : son nom, et son nom de filtre, chacun au sien
    changed = list(dict.fromkeys(name for name, a, b in zip(names, before, after) if a and b and a != b))
    changed += seen_changes(entry, res)
    entry.update(reasons=res["reasons"], notes=res["notes"], method=res["method"], url=res["url"] or entry.get("url"),
                 edition_rank=offer.get("edition_rank"), page_first=offer.get("page_first", False), seen=now,
                 last_recheck=stamp, region=offer["region"], region_filter=offer.get("region_filter", ""),
                 platform=offer["platform"], price=offer["price"], evidence=res.get("evidence"))
    outcome["checked"] += 1
    if res["verdict"] == "OK":
        if was in ("À VÉRIFIER", "NON VÉRIFIABLE"):  # elle n'avait pas pu être vérifiée : vérifiée OK, ni réparée ni levée
            entry.update(fixed_at=stamp, fixed_kind="verified", fixed_from=was, verdict="OK",
                         fixed_how="vérifiée OK au recontrôle" + (" (l'offre a changé : %s)" % ", ".join(changed) if changed else ""))
            outcome["verified"].append(entry)
        elif was in REPORTED:
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
    entry["tail"] = res.get("tail")
    rereport = False
    if was in REPORTED:
        entry["still_wrong_at"] = stamp
        outcome["still"].append(entry)
        alert = res["verdict"] == "SUSPECT" and was != "SUSPECT"  # une offre notée devient une erreur avérée
        rereport = not alert and res["verdict"] == "SUSPECT" and rereport_due(entry)
    else:  # était OK (ou réparée) : nouvelle erreur
        entry.update(at=stamp, fixed_at=None, fixed_how=None, fixed_from=None, fixed_kind=None, still_wrong_at=None)
        outcome["new"].append(entry)
        alert = res["verdict"] not in NOT_SENT and not res.get("quiet")
    if alert or rereport:
        msg = format_alert(label, rank, product, page_url, offer, res)
        if rereport:
            msg = with_note(msg, rereport_note(entry["decision"]))
        log.info("%s", msg.replace("\n", " | "))
        try:
            notify(msg)
        except Exception as e:
            log.error("Envoi Discord impossible : %s", e)
            return
        if rereport:
            entry["rereported_for"] = entry["decision"].get("at")


REREPORT_GRACE = 900  # s : le temps de corriger l'offre après l'avoir tranchée, avant le rappel


def rereport_due(entry, now=None):
    """Romain, 05/10/2026 : « même si un opérateur est passé et a traité l'offre, si, au prochain passage, l'offre est
    toujours en erreur, on doit encore la reporter ». Une offre tranchée « vrai », toujours en erreur au recontrôle, est
    reportée de nouveau : une fois par décision, au moins REREPORT_GRACE après elle. Un « à discuter » attend la
    discussion, pas une correction : il n'est pas reporté (Romain, 06/10/2026, Monster Hunter Wilds chez G2A, « à
    discuter » de Rémy renvoyé une heure plus tard : « pourquoi tu me renvoies le message alors que Rémy a répondu ») ;
    l'admin et le rappel du matin le montrent. Un « faux » arrête le suivi (l'offre n'est plus recontrôlée)."""
    d = entry.get("decision") or {}
    if d.get("decision") != "vrai" or entry.get("rereported_for") == d.get("at"):
        return False
    try:
        decided = datetime.datetime.fromisoformat(d.get("at") or "").timestamp()
    except ValueError:
        return False
    return (time.time() if now is None else now) - decided >= REREPORT_GRACE


def rereport_note(decision):
    at = decision.get("at") or ""
    when = "%s/%s %s" % (at[8:10], at[5:7], at[11:16]) if len(at) >= 16 else at
    return "📌 **Rappel** · toujours en erreur après traitement par %s (%s le %s)" % (
        decision.get("by") or "?", DECISION_LABELS.get(decision.get("decision"), decision.get("decision")), when)


def with_note(msg, note):
    """Une ligne en tête de l'alerte, sous l'en-tête d'urgence s'il y en a un (le bot lit l'urgence en tête)."""
    head, _, rest = msg.partition("\n")
    return "%s\n%s\n%s" % (head, note, rest) if msg.startswith(URGENT_PREFIX) else "%s\n%s" % (note, msg)


def seen_changes(entry, res):
    """Ce qui a changé chez le marchand derrière le même lien : la fiche servie (Kinguin de nouveau en stock), le titre
    de la page lue. Une offre contrôlée avant le 02/10/2026 n'a pas gardé ce qu'elle avait vu : seule sa raison
    « en rupture » le dit (la fiche servie était une autre)."""
    old, new = entry.get("evidence"), res.get("evidence") or {}
    if old is None:
        was_stock = any(r.startswith(STOCK_REASON_START) for r in entry.get("reasons") or [])
        now_stock = any(r.startswith(STOCK_REASON_START) for r in res.get("reasons") or [])
        return ["fiche servie"] if was_stock and not now_stock else []
    changes = []
    if "served" in new and (old.get("served") or "") != (new.get("served") or ""):
        changes.append("fiche servie")
    if old.get("page") and new.get("page") and old["page"] != new["page"] and old.get("via") == new.get("via"):
        changes.append("page marchand")
    return changes


def offer_facts(url, region, region_filter, platform, edition):
    """Ce qui identifie une offre pour dire si elle a changé entre deux contrôles : chemin de l'URL marchand (sans les
    paramètres de suivi), région (son nom et son nom de filtre, comparés chacun au sien : une entrée ancienne n'a pas
    de nom de filtre), plateforme, édition."""
    path = norm(url_text(unwrap_affiliate(url))) if url else ""
    return (path, norm(region or ""), norm(region_filter or ""), norm(platform or ""), norm(edition or ""))


def recheck_flagged(label, rank, product, page_url, trans, state, notify, checker, stamp, now, outcome, skip=(), mode=None,
                    sends=None, reroute=True):
    """Romain, 02/10/2026 : « il faut qu'il contrôle les offres déjà vues, comme ça on saura si elles sont réparées ou
    pas ». Les offres signalées de la page qui ne sont plus parmi les offres retenues (`skip` : déjà recontrôlées) :
    disparues de la page = retirées ; encore là (plus bas dans l'édition) = recontrôlées."""
    flagged = {k: e for k, e in flagged_entries(state, page_url).items() if k not in skip}
    if not flagged:
        return
    on_page = {str(o["id"]): o for o in page_offers(trans, None)}
    listed = {str(p.get("id")) for p in trans.get("prices") or []}
    page_dlc = dlc_page_kind(trans, product)
    for key, entry in flagged.items():
        check_stop()
        if merchant_config("", entry.get("merchant")).get("skip"):  # marchand ignoré par sa config (Amazon)
            state["checked"].pop(key, None)
            continue
        offer = on_page.get(key)
        if offer is None:
            if key in listed or not listed:
                # encore sur la page, mais « sans prix » (0.02) ou hors vente, ou page servie sans offres : pas une
                # réparation (audit du 02/10/2026) ; le verdict reste, l'offre sera recontrôlée en revenant en vente
                outcome["unknown"].append((entry, "offre momentanément sans prix sur la page" if listed else "page servie sans offres"))
                continue
            # retirée de la page : réparée, et recontrôlée tout de suite si elle revient (removed_at)
            entry.update(fixed_at=stamp, fixed_kind="repaired", fixed_how=REMOVED_HOW, fixed_from=entry["verdict"],
                         verdict="OK", seen=now, last_recheck=stamp, removed_at=stamp)
            outcome["removed"].append(entry)
            continue
        offer["page_dlc"] = offer_on_dlc_page(page_dlc, offer)
        try:
            res = apply_tail_words(checker(product, offer), state, page_url, key, stamp, product, offer)
        except Exception as e:
            if not isinstance(e, CheckError):
                log.exception("%s / %s : %s (%s) : erreur imprévue", product, offer["edition"], offer["merchantName"], key)
            outcome["unknown"].append((entry, "contrôle raté : %s" % e))
            continue
        if res["verdict"] == "À VÉRIFIER":
            res = dict(res, verdict=unverifiable_verdict(offer, label, page_url, res.get("unverifiable", "first-price")))
        before = sends[0] if sends else 0
        apply_recheck(entry, label, rank, product, page_url, offer, res, notify, stamp, now, outcome)
        if sends and sends[0] > before:
            entry["sent_to"] = channel_of(entry, offer, mode)
        if reroute:  # une page sortie des listes (recheck_orphans) : jamais de « report existant » renvoyé
            reroute_existing(entry, label, rank, product, page_url, offer, notify, mode)


ORPHAN_EVERY = 3600  # s : les pages sorties des listes, relues pour leurs offres signalées
ORPHAN_LIMIT = 30    # pages au plus par tour


def page_guesses(product):
    """Les URL possibles de la page AllKeyShop d'un produit, pour une offre signalée enregistrée sans sa page (avant le
    01/10/2026 : TORO 2, EA SPORTS FC 26)."""
    slug = norm(product or "")
    return ["https://www.allkeyshop.com/blog/buy-%s-cd-key-compare-prices/" % slug,
            "https://www.allkeyshop.com/blog/buy-%s-compare-prices/" % slug] if slug else []


def recheck_orphans(state, followed, notify, checker=None, now=None):
    """Romain, 06/10/2026 : « les bugs qui ont été traités et réparés par Rémy sont re-reportés ». Une offre signalée dont
    la page n'est plus dans les listes suivies (GTA 4, Warhammer 40k Space Marine 2 : sortis du TOP 50) n'était plus jamais
    recontrôlée : réparée par l'équipe (rangée dans la bonne édition, retirée de la page), elle restait SUSPECT, « à
    corriger » dans l'admin et dans le rappel du matin. Sa page est relue directement et recheck_flagged y fait le même
    travail que sur une page suivie : retirée = réparée, encore là = recontrôlée. Une entrée sans sa page la retrouve par le
    nom du produit (page_guesses), si le titre de la page le confirme. Renvoie le bilan."""
    checker = checker or check_offer
    now = time.time() if now is None else now
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
    outcome = {"checked": 0, "fixed": [], "removed": [], "rules": [], "verified": [], "still": [], "new": [], "unknown": [],
               "pages": 0, "offers": 0}
    orphans = {}
    for key, e in state["checked"].items():
        if (e.get("verdict") in REPORTED and not e.get("fixed_at") and (e.get("decision") or {}).get("decision") != "faux"
                and e.get("page") not in followed):
            orphans.setdefault(e.get("page") or "", []).append(e)
    for page_url, entries in list(orphans.items())[:ORPHAN_LIMIT]:
        check_stop()
        product = entries[0].get("product")
        found = None
        for url in [page_url] if page_url else page_guesses(product):
            try:
                status, _, page_html = http_get(url, AKS_UA)
            except OSError as e:
                log.warning("page sortie des listes %s : %s", url, e)
                continue
            finally:
                time.sleep(PAGE_DELAY)
            if status != 200 or (not page_url and norm(product or "") not in norm(page_title_from_html(page_html) or "")):
                continue  # une page devinée doit être celle du produit
            found = (url, page_html)
            break
        if not found:
            outcome["unknown"] += [(e, "page AllKeyShop introuvable") for e in entries]
            continue
        url, page_html = found
        try:
            trans = parse_game_page(page_html)
        except Exception as e:
            outcome["unknown"] += [(entry, "page illisible : %s" % e) for entry in entries]
            continue
        for e in entries:
            e["page"] = url
        outcome["pages"] += 1
        outcome["offers"] += len(entries)
        first = entries[0]
        recheck_flagged(first.get("list") or "", first.get("rank") or 0, product, url, trans, state, notify, checker, stamp, now,
                        outcome, reroute=False)
    return outcome


def format_recheck(label, by, outcome, full=False):
    """Récapitulatif Discord d'un recontrôle : complet (toutes les offres retenues, passage demandé depuis l'admin) ou
    des seules offres signalées (toutes les heures)."""
    what = "Recontrôle de toutes les offres" if full else "Recontrôle des offres signalées"
    lines = ["🔁 **%s** · %s%s · %d offre(s) recontrôlée(s)" % (
        what, label, " (demandé depuis l'admin par %s)" % by if by else "", outcome["checked"])]
    item = lambda e: "%s · %s · %s" % (e.get("product"), e.get("edition"), e.get("merchant"))
    # le plus important d'abord : le message est coupé à 1 900 caractères (limite Discord)
    new = ["%s (%s)" % (item(e), (e.get("reasons") or ["?"])[0][:90]) for e in outcome["new"]]
    if new:
        lines.append("🆕 Nouvelles erreurs (%d) : %s%s" % (len(new), " ; ".join(new[:15]), " ; …" if len(new) > 15 else ""))
    still = ["%s (%s)" % (item(e), (e.get("reasons") or ["?"])[0][:90]) for e in outcome["still"]]
    if still:
        lines.append("🔴 Toujours en erreur (%d) : %s%s" % (len(still), " ; ".join(still[:15]), " ; …" if len(still) > 15 else ""))
    fixed = ["%s — %s" % (item(e), e.get("fixed_how")) for e in outcome["removed"] + outcome["fixed"]]
    if fixed:
        lines.append("✅ Réparées (%d) : %s%s" % (len(fixed), " ; ".join(fixed[:15]), " ; …" if len(fixed) > 15 else ""))
    verified = [item(e) for e in outcome.get("verified", [])]
    if verified:
        lines.append("🔎 Vérifiées OK, elles n'avaient pas pu être vérifiées (%d) : %s%s" % (
            len(verified), " ; ".join(verified[:10]), " ; …" if len(verified) > 10 else ""))
    rules = [item(e) for e in outcome.get("rules", [])]
    if rules:
        lines.append("🧹 Anciens faux positifs levés par les règles, rien n'a changé (%d) : %s%s" % (
            len(rules), " ; ".join(rules[:10]), " ; …" if len(rules) > 10 else ""))
    if outcome["unknown"]:
        unknown = ["%s (%s)" % (item(e), why[:70]) for e, why in outcome["unknown"]]
        lines.append("⚪ Recontrôle sans conclusion, verdict inchangé (%d) : %s%s" % (
            len(unknown), " ; ".join(unknown[:5]), " ; …" if len(unknown) > 5 else ""))
    if not (fixed or verified or rules or new or still or outcome["unknown"]):
        lines.append("Rien à signaler : aucune offre réparée ni en erreur.")
    return "\n".join(lines)[:1900]


def recap_due(requested, outcome):
    """Le récapitulatif du recontrôle part toujours après un passage demandé ; après un passage automatique,
    seulement s'il y a du nouveau : une offre réparée, retirée, levée par une règle, ou une nouvelle erreur."""
    return bool(requested or outcome["fixed"] or outcome["removed"] or outcome["rules"] or outcome.get("verified")
                or outcome["new"])


def entry_time(at):
    """« 2026-10-05 15:13 » (heure locale du moniteur) -> heure Unix, ou None."""
    try:
        return time.mktime(time.strptime((at or "")[:16], "%Y-%m-%d %H:%M"))
    except ValueError:
        return None


def iso_time(at):
    try:
        return datetime.datetime.fromisoformat(at or "").timestamp()
    except ValueError:
        return None


def age_label(seconds):
    hours = int(seconds // 3600)
    return "moins d'une heure" if hours < 1 else "%d h" % hours if hours < 48 else "%d j" % (hours // 24)


def daily_reminder_due(state, t=None):
    """Le rappel du matin est-il dû : une fois par jour, à partir de DAILY_REMINDER_AT (heure locale) ?"""
    if not DAILY_REMINDER_AT:
        return False
    t = t or time.localtime()
    return state.get("daily_reminder") != time.strftime("%Y-%m-%d", t) and time.strftime("%H:%M", t) >= DAILY_REMINDER_AT


def format_daily_reminder(state, decisions, now=None, limit=1900):
    """Le rappel du matin (Romain, 05/10/2026 : « un message chaque matin dans #aks_price_emergencies les listerait avec
    leur ancienneté, avec un bilan du jour : nouveaux, réparés, traités par chacun »). Les premiers prix en erreur pas
    encore corrigés, les plus anciens d'abord (un « faux » n'en est plus un), puis le bilan des dernières 24 h.
    Renvoie les messages, chacun sous la limite de Discord."""
    now = time.time() if now is None else now
    since = now - 86400
    checked = state.get("checked") or {}
    first = lambda e: isinstance(e.get("edition_rank"), int) and 1 <= e["edition_rank"] <= FIRST_PRICES and not e.get("account")
    live = lambda e: not e.get("fixed_at") and (e.get("decision") or {}).get("decision") != "faux"
    urgent = sorted(((k, e) for k, e in checked.items() if e.get("verdict") == "SUSPECT" and first(e) and live(e)),
                    key=lambda ke: ke[1].get("at") or "")
    others = sum(1 for e in checked.values() if e.get("verdict") in ("SUSPECT", "À VÉRIFIER") and live(e)
                 and not (e.get("verdict") == "SUSPECT" and first(e)))
    lines = ["📋 **Rappel du matin · urgences premiers prix** · %s" % time.strftime("%d/%m/%Y", time.localtime(now))]
    if urgent:
        lines.append("**%d premier%s prix en erreur, pas encore corrigé%s** (les plus anciens d'abord) :" % (
            len(urgent), "s" if len(urgent) > 1 else "", "s" if len(urgent) > 1 else ""))
        for k, e in urgent:
            d = e.get("decision") or {}
            status = "%s (%s)" % (DECISION_LABELS.get(d.get("decision"), d.get("decision")), d.get("by") or "?") if d.get("decision") else "à traiter"
            seen = entry_time(e.get("at"))
            lines.append("• **%s** · %s · %s · %s · %s · %s · <%s#offer-%s>" % (
                e.get("product"), e.get("edition"), e.get("merchant"), rank_label(e) or "premier prix",
                "depuis " + age_label(now - seen) if seen else "depuis ?", status, ADMIN_URL, k))
    else:
        lines.append("✅ **Aucun premier prix en erreur ce matin.**")
    if others:
        lines.append("… et %d autre%s report%s ouvert%s dans l'admin (hors urgences)." % ((others,) + ("s",) * 3 if others > 1 else (others, "", "", "")))
    new = sum(1 for e in checked.values() if e.get("verdict") in REPORTED and (entry_time(e.get("at")) or 0) >= since)
    fixed = lambda kind: sum(1 for e in checked.values() if e.get("fixed_kind") == kind and (entry_time(e.get("fixed_at")) or 0) >= since)
    by = collections.Counter(d.get("by") or "?" for d in (decisions or {}).values() if (iso_time(d.get("at")) or 0) >= since)
    lines.append("**Bilan des dernières 24 h** : %d nouveau%s report%s · %d réparé%s · %d faux positif%s levé%s par une règle · "
                 "%d décision%s%s" % (new, "x" if new > 1 else "", "s" if new > 1 else "", fixed("repaired"), "s" if fixed("repaired") > 1 else "",
                                     fixed("rule"), "s" if fixed("rule") > 1 else "", "s" if fixed("rule") > 1 else "",
                                     sum(by.values()), "s" if sum(by.values()) > 1 else "",
                                     " (%s)" % ", ".join("%s %d" % (n, c) for n, c in by.most_common()) if by else ""))
    lines.append("📘 Admin : <%s> · guide : <%s>" % (ADMIN_URL, GUIDE_URL))
    messages, current = [], ""
    for line in lines:
        if current and len(current) + 1 + len(line) > limit:
            messages.append(current)
            current = line
        else:
            current = current + "\n" + line if current else line
    return messages + [current]


class Stop(Exception):
    """Arrêt demandé (SIGTERM) : le passage s'interrompt entre deux offres, l'état est sauvegardé."""


STOP = {"asked": False}  # posé par le gestionnaire de SIGTERM (main)


def check_stop():
    if STOP["asked"]:
        raise Stop()


REMOVED_HOW = "offre retirée de la page"


def run_cycle(targets, notify, state, checker=None, save=None, per_edition=1, between=None, progress=None, recheck=False,
              resume_after=None, mode=None):
    """Lit chaque page suivie et contrôle toute offre retenue (`per_edition` : voir page_offers) pas encore
    contrôlée. `recheck` : "all" recontrôle aussi toutes les offres retenues déjà vues (passage demandé depuis
    l'admin : Romain, 02/10/2026, « toutes les offres concernées par le top check, pareil pour l'autre check ») ;
    "flagged" recontrôle les seules offres signalées. Renvoie le bilan du recontrôle.

    `save()` est appelé toutes les SAVE_EVERY pages : un long passage interrompu ne repart pas de zéro.
    `between()` est appelé entre deux pages : un mode urgent (les top games) y passe pendant un long passage.
    `progress(count, total)` est appelé à chaque page : l'avancement publié pour l'admin.
    `resume_after` : reprise d'un recontrôle complet interrompu (redémarrage) : les offres déjà recontrôlées depuis
    cette date ne le sont pas une seconde fois. `mode` : le mode de pages du passage, gardé avec chaque offre contrôlée
    (l'admin distingue les reports des tops de ceux de la homepage).
    """
    checker = checker or check_offer
    outcome = {"checked": 0, "fixed": [], "removed": [], "rules": [], "verified": [], "still": [], "new": [], "unknown": [],
               "first_checked": 0}
    # entrées d'avant le 01/10/2026 sans leur page (TORO 2, F1 25, Dawnwalker…) : la page de leur produit, sinon le
    # recontrôle des offres signalées ne les voit jamais (audit du 02/10/2026)
    by_product = {}
    for label, rank, product, page_url in targets:
        by_product.setdefault(product, (label, rank, page_url))
    for entry in state["checked"].values():
        if not entry.get("page") and entry.get("product") in by_product:
            label, rank, page_url = by_product[entry["product"]]
            entry.update(page=page_url, list=entry.get("list") or label, rank=entry.get("rank") or rank)
    now = time.time()
    for count, (label, rank, product, page_url) in enumerate(targets, 1):
        check_stop()
        if between and count > 1:
            between()
        if progress:
            progress(count, len(targets))
        if save and count % SAVE_EVERY == 0:
            save()
        now = time.time()  # l'heure de la page : un long passage ne date pas tout de son début
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
        try:
            _, _, page_html = http_get(page_url, AKS_UA)
            trans = parse_game_page(page_html)
            offers = page_offers(trans, per_edition)
            page_dlc = dlc_page_kind(trans, product)
        except Exception as e:
            log.warning("%s : %s", product, e)
            continue
        finally:
            time.sleep(PAGE_DELAY)
        handled = set()
        alerted = [False]
        sends = [0]

        def page_notify(msg):
            notify(msg)
            alerted[0] = True
            sends[0] += 1

        def mark_sent(entry, offer, before):
            # une alerte vient de partir pour cette offre (recontrôle, promotion) : le salon où elle est partie
            if sends[0] > before:
                entry["sent_to"] = channel_of(entry, offer, mode)
        for offer in offers:
            check_stop()
            offer["page_dlc"] = offer_on_dlc_page(page_dlc, offer)
            key = str(offer["id"])
            if merchant_config("", offer["merchantName"]).get("skip"):
                state["checked"].pop(key, None)  # marchand ignoré par sa config (Amazon) : ni contrôle, ni report
                continue
            entry = state["checked"].get(key)
            handled.add(key)  # une offre retenue est traitée ici, une fois ; recheck_flagged ne voit que les autres
            if entry is not None and not entry.get("page"):  # entrée ancienne sans sa page : jamais recontrôlée sinon
                entry.update(page=page_url, list=label, rank=rank)
            if entry is not None and mode and not entry.get("mode"):
                entry["mode"] = mode
            # une offre déjà vue est recontrôlée sur un passage complet ("all"), si elle est signalée ("flagged"), ou si
            # elle revient sur sa page après en avoir été retirée ; jamais si l'admin l'a jugée faux positif
            again = entry is not None and (entry.get("decision") or {}).get("decision") != "faux" and (
                entry.get("removed_at")
                or (recheck == "all" and not (resume_after and (entry.get("last_recheck") or "") >= resume_after))
                or (recheck == "flagged" and entry.get("verdict") in REPORTED and not entry.get("fixed_at")))
            if entry is not None and not again:
                entry["seen"] = now
                policy = entry.get("unverifiable") or merchant_config("", entry.get("merchant")).get("unverifiable", "first-price")
                if (entry.get("verdict") == "NON VÉRIFIABLE" and policy == "first-price"
                        and (entry.get("decision") or {}).get("decision") != "faux"  # jugée faux positif : jamais alertée
                        and unverifiable_verdict(offer, label, page_url) == "À VÉRIFIER"):
                    before = sends[0]
                    promote_unverifiable(entry, label, rank, product, page_url, offer, page_notify)
                    mark_sent(entry, offer, before)
                reroute_existing(entry, label, rank, product, page_url, offer, page_notify, mode)
                continue
            try:
                res = apply_tail_words(checker(product, offer), state, page_url, key, stamp, product, offer)
            except Exception as e:  # CheckError, ou toute erreur imprévue : l'offre est retentée, le passage continue
                if not isinstance(e, CheckError):
                    log.exception("%s / %s : %s (%s) : erreur imprévue", product, offer["edition"], offer["merchantName"], key)
                FAILURES[key] = FAILURES.get(key, 0) + 1
                log.warning("%s / %s : %s (%s) : %s", product, offer["edition"], offer["merchantName"], key, e)
                if FAILURES[key] < MAX_CHECK_FAILURES:
                    if entry is not None:
                        outcome["unknown"].append((entry, "contrôle raté : %s" % e))
                    continue
                res = {"verdict": "À VÉRIFIER", "url": None, "method": "aucune", "notes": [],
                       "reasons": ["contrôle impossible : %s" % e]}
            FAILURES.pop(key, None)
            if res["verdict"] == "À VÉRIFIER":
                res = dict(res, verdict=unverifiable_verdict(offer, label, page_url, res.get("unverifiable", "first-price")))
            if entry is not None:  # recontrôle d'une offre déjà vue
                before = sends[0]
                apply_recheck(entry, label, rank, product, page_url, offer, res, page_notify, stamp, now, outcome)
                mark_sent(entry, offer, before)
                reroute_existing(entry, label, rank, product, page_url, offer, page_notify, mode)
                continue
            msg = format_alert(label, rank, product, page_url, offer, res)
            log.info("%s", msg.replace("\n", " | "))
            if (res["verdict"] not in NOT_SENT or (res["verdict"] == "OK" and NOTIFY_OK)) and not res.get("quiet"):
                try:
                    page_notify(msg)
                except Exception as e:
                    log.error("Envoi Discord impossible, nouvel essai au prochain passage : %s", e)
                    continue
            outcome["first_checked"] += 1
            state["checked"][key] = {
                "verdict": res["verdict"], "reasons": res["reasons"], "notes": res["notes"], "product": product,
                "edition": offer["edition"], "region": offer["region"], "region_filter": offer.get("region_filter", ""),
                "platform": offer["platform"], "merchant": offer["merchantName"], "price": offer["price"],
                "url": res["url"], "method": res["method"], "at": stamp, "seen": now,
                "page": page_url, "list": label, "rank": rank,
                "edition_rank": offer.get("edition_rank"), "account": offer.get("account", False),
                "page_first": offer.get("page_first", False), "unverifiable": res.get("unverifiable"),
                "evidence": res.get("evidence"), "mode": mode, "tail": res.get("tail"),
                "sent_to": ("urgent" if is_urgent(offer, res) else mode_channel({}, mode)) if res["verdict"] not in NOT_SENT else None,
            }
            if res["method"] != "aucune":
                m = state["merchants"].setdefault(offer["merchantName"], {"methods": {}})
                m["methods"][res["method"]] = m["methods"].get(res["method"], 0) + 1
                m.update(url=res["url"], at=stamp)
        if recheck:
            recheck_flagged(label, rank, product, page_url, trans, state, page_notify, checker, stamp, now, outcome, skip=handled,
                            mode=mode, sends=sends)
        if alerted[0] and save:  # une alerte partie : l'état est écrit tout de suite (un redémarrage ne la renverra pas)
            save()
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
            with open_shared(path) as f:
                by = str(json.load(f).get("by") or "")
        except (OSError, ValueError, AttributeError):
            pass
        try:
            os.remove(path)
        except OSError:
            continue
        found.append((mode, by or "admin"))
    return found


def write_shared(path, payload):
    """Écrit un fichier JSON du dossier partagé avec l'admin. Ce dossier est inscriptible par le groupe debian : le
    moniteur (root) n'y écrit jamais à travers un lien symbolique posé là (audit du 02/10/2026) ; le .tmp est retiré
    s'il existe (unlink ne suit pas le lien), recréé neuf (O_EXCL | O_NOFOLLOW), puis renommé sur le fichier."""
    tmp = path + ".tmp"
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        os.fchmod(f.fileno(), 0o644)
        json.dump(payload, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def open_shared(path):
    """Lit un fichier du dossier partagé : jamais à travers un lien symbolique, jamais un tube nommé ou un périphérique
    (il bloquerait la boucle), un octet non UTF-8 remplacé plutôt que fatal (OSError sinon)."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise OSError("%s : pas un fichier ordinaire" % path)
    return os.fdopen(fd, encoding="utf-8", errors="replace")


def write_status(directory, status):
    """status.json pour l'admin : l'état de chaque mode (en cours, avancement, dernier et prochain passage)."""
    if not directory:
        return
    try:
        write_shared(os.path.join(directory, STATUS_FILE), dict(status, updated_at=time.strftime("%Y-%m-%dT%H:%M:%S%z")))
    except OSError as e:
        log.warning("status.json non écrit : %s", e)
DECISIONS = {"vrai": "Vrai positif : alerter", "faux": "Faux positif : ne pas alerter", "a_discuter": "À discuter"}


def read_decisions(directory):
    """Décisions de l'admin (decisions.jsonl : une ligne JSON par décision), la dernière par offre l'emporte.
    Une ligne illisible ou inconnue est ignorée."""
    decisions = {}
    try:
        with open_shared(os.path.join(directory, "decisions.jsonl")) as f:
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


# Fils de feedback Discord (Romain, 03/10/2026 : « envoyer le feedback sur un thread du report sur Discord », et l'admin
# aussi) : le bot ouvre un fil sur chaque alerte et note dans threads.json, par offre, le dernier fil ouvert (salon,
# mode du webhook, serveur). Le moniteur y poste les suites de l'offre et en donne le lien à l'admin.
THREADS_FILE = "threads.json"
DECISION_LABELS = {"vrai": "Vrai positif", "faux": "Faux positif", "a_discuter": "À discuter"}


def read_threads(directory):
    if not directory:
        return {}
    try:
        with open_shared(os.path.join(directory, THREADS_FILE)) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def thread_url(info):
    if isinstance(info, dict) and str(info.get("guild", "")).isdigit() and str(info.get("thread", "")).isdigit():
        return "https://discord.com/channels/%s/%s" % (info["guild"], info["thread"])
    return None


def follow_up_message(kind, entry, stamp):
    """Le message posté dans le fil de feedback d'une offre quand son recontrôle conclut."""
    at = stamp or entry.get("fixed_at") or entry.get("last_recheck") or ""
    if kind == "fixed":
        return "✅ **Réparée** au recontrôle du %s : %s" % (at, entry.get("fixed_how") or "recontrôle OK")
    if kind == "removed":
        return "✅ **Réparée** au recontrôle du %s : offre retirée de la page" % at
    if kind == "rules":
        return "🧹 **Faux positif levé par une règle** au recontrôle du %s : rien n'a changé dans l'offre" % at
    if kind == "verified":
        return "🔎 **Vérifiée OK** au recontrôle du %s (elle n'avait pas pu être vérifiée)" % at
    if kind == "new":
        return "🆕 **De nouveau en erreur** au recontrôle du %s : %s" % (at, (entry.get("reasons") or ["?"])[0])
    raise ValueError(kind)


def post_follow_ups(state, outcome, threads, send):
    """Poste dans le fil de feedback de chaque offre ce que son recontrôle a conclu (réparée, retirée, faux positif levé,
    vérifiée, de nouveau en erreur ; jamais « toujours en erreur », qui reviendrait toutes les heures). `send(info, msg)`.
    Renvoie le nombre de messages postés."""
    if not threads:
        return 0
    keys = {id(e): k for k, e in state["checked"].items()}
    posted = 0
    for kind in ("fixed", "removed", "rules", "verified", "new"):
        for entry in outcome.get(kind) or []:
            info = threads.get(keys.get(id(entry)))
            if not thread_url(info):
                continue
            try:
                send(info, follow_up_message(kind, entry, entry.get("fixed_at") if kind != "new" else entry.get("at")))
                posted += 1
            except Exception as e:  # une suite manquée n'est pas une alerte : journalisée, pas mise en file
                log.warning("fil de l'offre %s : suite non postée (%s)", keys.get(id(entry)), e)
    return posted


def post_admin_decisions(state, keys, threads, send):
    """Une décision prise dans l'admin est recopiée dans le fil de feedback de l'offre (celles prises sur Discord y sont
    déjà : le bot les a confirmées)."""
    for key in keys:
        decision = (state["checked"].get(key) or {}).get("decision") or {}
        info = threads.get(key)
        if not thread_url(info) or str(decision.get("by") or "").endswith("(Discord)"):
            continue
        note = decision.get("note")
        try:
            send(info, "📝 Décision prise dans l'admin : **%s**%s — par %s" % (
                DECISION_LABELS.get(decision.get("decision"), decision.get("decision")), (" — « %s »" % note) if note else "",
                decision.get("by") or "?"))
        except Exception as e:
            log.warning("fil de l'offre %s : décision non recopiée (%s)", key, e)


def send_to_thread(info, msg):
    """Poste dans un fil, par le webhook de son salon (un webhook ne poste que dans les fils de son salon)."""
    hook = webhook_for(info.get("mode") or "top-games")
    if not hook:
        raise OSError("pas de webhook pour le mode %s" % info.get("mode"))
    send_discord("%s?thread_id=%s" % (hook, info["thread"]), msg)


def apply_decisions(state, directory):
    """Reporte les décisions de l'admin dans l'état ; renvoie les offres dont la décision est nouvelle."""
    changed = []
    for key, decision in read_decisions(directory).items():
        entry = state["checked"].get(key)
        if entry is not None and entry.get("decision") != decision:
            entry["decision"] = decision
            changed.append(key)
            for reg in (state.get("tail_words") or {}).values():  # le doute « mots en plus » dont elle est la première offre
                for info in reg.values():
                    if info.get("offer") == key:
                        info["decision"] = (decision or {}).get("decision")
    return changed


TOP_GAMES_LABELS = {label for _, label, _ in TOP_GAMES_LISTS}


def page_modes_of(targets_by_mode):
    """URL de page -> modes de pages dont les listes la suivent en ce moment (une page des tops est aussi dans la
    homepage : le TOP 50 Popular contient les 5 premiers)."""
    out = {}
    for mode, targets in targets_by_mode.items():
        for _, _, _, url in targets:
            if mode not in out.setdefault(url, []):
                out[url].append(mode)
    return out


def report_mode(entry, page_url, page_modes):
    """Le mode d'un report (Romain, 03/10/2026 : « que le report des problèmes sur les tops soit identifié des problèmes
    home page ») : « top-games » si sa page est dans les tops en ce moment (5 premiers Popular, 4 premiers Coming soon
    PC), sinon « homepage » si elle est dans les listes de la home ; une page sortie des listes garde le mode qui l'a
    contrôlée, ou, pour une entrée d'avant le 03/10, celui de sa liste (« Popular », « Coming soon PC » : les tops).
    Romain, 06/10/2026 (The Witcher 3, sortie du top 5 Popular à 12:03 avec deux premiers prix en erreur, qui
    disparaissaient du filtre top) : un report trouvé sur une page des tops y reste tant qu'il n'est pas traité (sans
    décision, ou à discuter), même quand sa page sort des tops."""
    current = page_modes.get(page_url) or []
    if "top-games" in current:
        return "top-games", current
    if (entry.get("in_tops") or entry.get("mode") == "top-games") and report_open(entry):
        return "top-games", current
    if current:
        return current[0], current
    if entry.get("mode") in MODES:
        return entry["mode"], current
    label = entry.get("list") or ""
    return ("top-games" if label in TOP_GAMES_LABELS else "homepage" if label else None), current


def report_open(entry):
    """Un report pas encore traité : ni réparé, ni tranché vrai ou faux (un « à discuter » attend sa décision)."""
    return not entry.get("fixed_at") and (entry.get("decision") or {}).get("decision") in (None, "", "a_discuter")


def export_reports(state, directory, pages=None, page_modes=None):
    """Écrit reports.json : chaque report (SUSPECT, À VÉRIFIER, NON VÉRIFIABLE, ou déjà décidé) avec son
    URL AllKeyShop, son URL marchand, sa raison, sa preuve et sa décision. `pages` (produit -> (liste, rang,
    URL)) complète les reports anciens, enregistrés avant que l'état garde la page ; `page_modes` (URL de page ->
    modes qui la suivent, page_modes_of) donne le mode de chaque report."""
    pages = pages or {}
    page_modes = page_modes or {}
    threads = read_threads(directory)
    tops_known = any("top-games" in m for m in page_modes.values())  # les tops lus : sinon, rien ne « sort » des tops
    reports = []
    for key, e in state["checked"].items():
        if e.get("verdict") not in REPORTED and not e.get("decision") and not e.get("fixed_at"):
            continue
        if merchant_config("", e.get("merchant")).get("skip"):
            continue  # marchand ignoré par sa config (Amazon depuis le 01/10/2026)
        label, rank, page = pages.get(e.get("product"), (None, None, None))
        mode, modes = report_mode(e, e.get("page") or page, page_modes)
        if "top-games" in modes:  # vu dans les tops : le report y restera jusqu'à sa décision (06/10/2026)
            e["in_tops"] = True
            e.pop("left_tops_at", None)
        elif mode == "top-games" and tops_known and not e.get("left_tops_at"):
            e["left_tops_at"] = time.strftime("%Y-%m-%d %H:%M")
        reports.append({
            "left_tops_at": e.get("left_tops_at") if mode == "top-games" and "top-games" not in modes else None,
            "mode": mode, "modes": modes, "mode_label": MODES[mode]["label"] if mode in MODES else None,
            "first_price": is_first_price(e),  # l'un des 3 prix de clé les moins chers de son édition (03/10/2026)
            "discord_thread": thread_url(threads.get(key)),  # le fil de feedback Discord de l'offre (03/10/2026)
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
    write_shared(path, payload)
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
        pages, by_mode = {}, {}
        for mode in MODES:
            by_mode[mode] = fetch_targets(MODES[mode]["lists"])
            for label, rank, product, url in by_mode[mode]:
                pages.setdefault(product, (label, rank, url))
        print(export_reports(state, args.export_reports, pages, page_modes_of(by_mode)), "reports écrits dans", args.export_reports)
        return  # state.json n'est pas réécrit : le service, s'il tourne, en est le seul auteur
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
    urgent_notifier = None
    if os.environ.get(URGENT_WEBHOOK):
        urgent_notifier = (lambda msg: None) if args.dry_run else make_notifier(webhook_for("urgent"), state, "urgent")
        log.info("urgences premiers prix (SUSPECT sur l'un des %d premiers prix d'une édition) : webhook %s", FIRST_PRICES, URGENT_WEBHOOK)
    else:
        log.info("pas de %s : les urgences premiers prix partent sur le salon de leur mode", URGENT_WEBHOOK)
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

    # Demandes de l'admin en attente, par mode : gardées à part, une demande lue pendant un passage du même mode
    # relance un recontrôle complet à la fin de celui-ci (audit du 02/10/2026 : elle était effacée).
    pending = {m: None for m in modes}
    # Un passage demandé en cours est noté dans state.json (« running ») : interrompu par un redémarrage, il reprend
    # au démarrage, sans recontrôler les offres déjà recontrôlées depuis son début (audit : la demande était perdue).
    resume = {}
    for mode, info in list((state.get("running") or {}).items()):
        if mode in modes and isinstance(info, dict):
            pending[mode] = "%s (reprise après redémarrage)" % (info.get("by") or "admin")
            resume[mode] = info.get("started")
            due[mode] = 0.0
            log.info("%s : reprise du passage demandé par %s, interrompu (commencé le %s)", mode, info.get("by"), info.get("started"))

    def honor_requests():
        """Les passages demandés depuis l'admin : le mode est dû tout de suite. Renvoie True si une demande a été lue."""
        try:
            taken = take_requests(REPORTS_DIR, modes)
        except Exception:  # un fichier inattendu dans le dossier partagé ne doit jamais arrêter la surveillance
            log.exception("lecture des demandes de l'admin impossible")
            return False
        for mode, by in taken:
            log.info("%s : passage demandé depuis l'admin par %s", mode, by)
            due[mode] = 0.0
            pending[mode] = by
            status["modes"][mode]["requested_by"] = by
        return bool(taken)

    last_recheck = {m: 0.0 for m in modes}
    last_flush = [0.0]
    announce = make_announcer()  # le bandeau de chaque boucle, dans chaque salon où elle poste
    if DAILY_REMINDER_AT and "daily_reminder" not in state and time.strftime("%H:%M") >= DAILY_REMINDER_AT:
        state["daily_reminder"] = time.strftime("%Y-%m-%d")  # premier démarrage après l'heure : le premier rappel est demain

    def daily_reminder():
        """Le rappel du matin, une fois par jour à partir de DAILY_REMINDER_AT, dans le salon des urgences (à défaut, celui
        des top games). Il passe aussi entre deux pages d'un long passage homepage."""
        if args.dry_run or not daily_reminder_due(state):
            return
        state["daily_reminder"] = time.strftime("%Y-%m-%d")
        try:
            decisions = read_decisions(REPORTS_DIR) if REPORTS_DIR else {}
        except Exception:
            decisions = {}
        send = urgent_notifier or notifiers[modes[0]]
        for part in format_daily_reminder(state, decisions):
            send(part)
        announce.forget("urgent" if urgent_notifier else modes[0])
        log.info("rappel du matin envoyé")
        save_state(args.state, state)

    def flush_alerts(every=0):
        """Les alertes en file (pause terminée, Discord de nouveau joignable), au plus une tentative par `every` s."""
        if not state["queued"] or muted() or args.dry_run or time.monotonic() - last_flush[0] < every:
            return
        last_flush[0] = time.monotonic()
        try:
            flush_queue(state, lambda msg, channel: send_discord(webhook_for(channel), msg))
            log.info("alertes en file envoyées")
        except Exception as e:
            log.error("Envoi des alertes en file impossible, nouvel essai plus tard (%d en file) : %s", len(state["queued"]), e)
        save_state(args.state, state)

    def run_mode(mode, between=None):
        now = time.monotonic()
        due[mode] = now + MODES[mode]["interval"]
        st, alerts, last_pub = status["modes"][mode], [0], [time.monotonic()]
        requested, pending[mode] = pending[mode], None
        resume_after = resume.pop(mode, None)
        st["requested_by"] = requested
        # passage demandé depuis l'admin : toutes les offres retenues sont recontrôlées ; sinon, les offres signalées
        # une fois par heure
        recheck = "all" if requested else ("flagged" if now - last_recheck[mode] >= RECHECK_EVERY else False)
        if requested:
            info = (state.get("running") or {}).get(mode) or {}
            by = info.get("by") or requested
            started = resume_after or time.strftime("%Y-%m-%d %H:%M")
            state.setdefault("running", {})[mode] = {"by": by, "started": started}
            save_state(args.state, state)
        outcome = None
        loop = new_loop(mode, recheck, requested)
        to_urgent = None if urgent_notifier is None else (lambda m: announce(loop, "urgent", urgent_notifier, m))

        def notify(msg, _send=notifiers[mode]):
            route_alert(msg, mode, lambda m: announce(loop, mode, _send, m), to_urgent)
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
                try:
                    changed = apply_decisions(state, REPORTS_DIR)
                    for key in changed:
                        log.info("décision de l'admin pour l'offre %s : %s", key, state["checked"][key]["decision"])
                    if changed and not args.dry_run:
                        post_admin_decisions(state, changed, read_threads(REPORTS_DIR), send_to_thread)
                except Exception:  # decisions.jsonl illisible : la surveillance continue, les décisions attendront
                    log.exception("lecture des décisions de l'admin impossible")
            outcome = run_cycle(targets[mode], notify, state, save=lambda: save_state(args.state, state),
                                per_edition=per_edition, between=between, progress=progress, recheck=recheck,
                                resume_after=resume_after, mode=mode)
            (state.get("running") or {}).pop(mode, None)  # passage demandé terminé
            save_state(args.state, state)
            if REPORTS_DIR and not args.dry_run:
                posted = post_follow_ups(state, outcome, read_threads(REPORTS_DIR), send_to_thread)
                if posted:
                    log.info("%s : %d suite(s) postée(s) dans les fils de feedback", mode, posted)
            if recheck:
                last_recheck[mode] = time.monotonic()
                st["last_recheck"] = {"at": stamp_iso(), "kind": recheck, "checked": outcome["checked"],
                                      "fixed": len(outcome["fixed"]) + len(outcome["removed"]), "rules": len(outcome["rules"]),
                                      "verified": len(outcome["verified"]), "new": len(outcome["new"]),
                                      "still": len(outcome["still"]), "unknown": len(outcome["unknown"])}
                recap = format_recheck(MODES[mode]["label"], requested, outcome, full=recheck == "all")
                log.info("%s", recap.replace("\n", " | "))
                if recap_due(requested, outcome):
                    try:
                        announce(loop, mode, notifiers[mode], recap)
                    except Exception as e:
                        log.error("Envoi Discord du récapitulatif impossible : %s", e)
            if REPORTS_DIR:
                pages = {t[2]: (t[0], t[1], t[3]) for m in modes for t in targets[m]}
                export_reports(state, REPORTS_DIR, pages, page_modes_of({m: targets[m] for m in modes}))
        except Stop:
            # arrêt du service (SIGTERM) : l'état est écrit, le passage demandé reprendra au démarrage
            save_state(args.state, state)
            if REPORTS_DIR:
                pages = {t[2]: (t[0], t[1], t[3]) for m in modes for t in targets[m]}
                export_reports(state, REPORTS_DIR, pages, page_modes_of({m: targets[m] for m in modes}))
            raise
        except Exception:
            log.exception("%s : passage en échec, nouvel essai au prochain cycle", mode)
            if requested:  # la demande de l'admin n'est pas perdue : le prochain passage du mode la reprend
                pending[mode] = requested
                resume[mode] = ((state.get("running") or {}).get(mode) or {}).get("started")
        finally:
            st.update(running=False, last_end=stamp_iso(), last_checked=outcome["first_checked"] if outcome else 0,
                      last_alerts=alerts[0], progress=None, requested_by=None)
            publish_status()

    orphans_due = [time.monotonic() + 120]

    def orphans():
        """Les offres signalées dont la page n'est plus suivie, recontrôlées sur leur page toutes les heures (06/10/2026)."""
        if args.dry_run or time.monotonic() < orphans_due[0] or not all(targets[m] for m in modes):
            return  # listes pas encore toutes lues : on ne sait pas quelles pages en sont sorties
        orphans_due[0] = time.monotonic() + ORPHAN_EVERY
        mode = "homepage" if "homepage" in modes else modes[0]
        loop = new_loop(mode)
        to_urgent = None if urgent_notifier is None else (lambda m: announce(loop, "urgent", urgent_notifier, m))

        def notify(msg, _send=notifiers[mode]):
            route_alert(msg, mode, lambda m: announce(loop, mode, _send, m), to_urgent)
        try:
            outcome = recheck_orphans(state, {t[3] for m in modes for t in targets[m]}, notify)
        except Stop:
            raise
        except Exception:
            log.exception("recontrôle des pages sorties des listes en échec")
            return
        if not outcome["offers"] and not outcome["unknown"]:
            return
        log.info("pages sorties des listes : %d page(s), %d offre(s) signalée(s) recontrôlée(s), %d réparée(s), %d retirée(s), "
                 "%d toujours en erreur, %d sans conclusion", outcome["pages"], outcome["offers"], len(outcome["fixed"]),
                 len(outcome["removed"]), len(outcome["still"]), len(outcome["unknown"]))
        if REPORTS_DIR:
            post_follow_ups(state, outcome, read_threads(REPORTS_DIR), send_to_thread)
            pages = {t[2]: (t[0], t[1], t[3]) for m in modes for t in targets[m]}
            export_reports(state, REPORTS_DIR, pages, page_modes_of({m: targets[m] for m in modes}))
        save_state(args.state, state)

    def run_urgent():
        """Entre deux pages d'un long passage : les demandes de l'admin, les alertes en file, puis les modes urgents
        (top games) dont l'heure est venue."""
        check_stop()
        honor_requests()
        flush_alerts(every=60)
        daily_reminder()
        orphans()
        for m in modes:
            if MODES[m].get("urgent") and time.monotonic() >= due[m]:
                run_mode(m)

    def ask_stop(signum, frame):
        if not STOP["asked"]:
            log.info("arrêt demandé (signal %d) : fin de l'offre en cours, état écrit, puis arrêt", signum)
        STOP["asked"] = True

    signal.signal(signal.SIGTERM, ask_stop)
    try:
        while True:
            check_stop()
            flush_alerts()
            daily_reminder()
            orphans()
            honor_requests()
            for mode in modes:
                if time.monotonic() >= due[mode]:
                    run_mode(mode, between=None if MODES[mode].get("urgent") else run_urgent)
            if args.once:
                return
            wait_until = min(due.values())  # attente par tranches : une demande de l'admin est vue en quelques secondes
            while time.monotonic() < wait_until:
                check_stop()
                publish_status()
                time.sleep(max(1, min(REQUEST_POLL, wait_until - time.monotonic())))
                if honor_requests():
                    break
    except Stop:
        save_state(args.state, state)
        log.info("arrêt : état écrit%s", " ; passage demandé à reprendre : %s" % ", ".join(state["running"]) if state.get("running") else "")


if __name__ == "__main__":
    main()
