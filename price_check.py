"""Moniteur du premier prix des pages produit des top clics AllKeyShop, avec alertes Discord.

Boucle sans fin, deux modes (--mode) :
- top-games : top 5 All Popular + top 4 Coming soon PC, un passage toutes les 2 min 30 ;
- homepage : tous les jeux des top clics de la home (10 widgets + TOP 50 par plateforme,
  ~415 pages), un passage toutes les 15 min.
Pour chaque passage :
- relit les listes toutes les 30 min ;
- lit les offres de chaque page produit (UA AKS/Staff) ;
- pour toute nouvelle offre en premier prix d'une édition, suit son lien de
  redirection AllKeyShop (UA AKS/Staff) pour obtenir l'URL marchand, puis vérifie
  que cette URL (à défaut : l'URL après le 301 du marchand, puis le titre de sa
  page ouverte avec Chromium) correspond au produit, à la région, à la
  plateforme et à l'édition affichées ;
- envoie le verdict (OK, SUSPECT, À VÉRIFIER) sur Discord.

Voir docs/detection.md et docs/marchands.md.
"""

import argparse
import html
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
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
# Modes de surveillance : listes suivies et intervalle entre deux passages (s)
MODES = {
    "top-games": {"lists": TOP_GAMES_LISTS, "interval": 150},  # 9 pages ; le cache des pages est de 120 s
    "homepage": {"lists": HOMEPAGE_LISTS, "interval": 900},  # ~415 pages, un passage dure plusieurs minutes
}
REDIRECTION_URL = "https://www.allkeyshop.com/redirection/offer/eur/%s?locale=en&merchant=%s"

AKS_UA = "AKS/Staff"  # pages AllKeyShop seulement, jamais chez le marchand
BROWSER_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/150.0.0.0 Safari/537.36")  # chez le marchand
CHROMIUM = os.environ.get("CHROMIUM_BIN", "chromium")  # dernier repli : ouvrir la page marchand
NOTIFY_OK = os.environ.get("NOTIFY_OK", "1") != "0"  # envoyer aussi les verdicts OK sur Discord

NO_PRICE = 0.02  # sentinelle « pas de prix »
LISTS_REFRESH = 1800  # max-age de l'API getLists
PAGE_DELAY = 1  # pause entre deux pages produit AllKeyShop
REQUEST_DELAY = 2  # pause entre deux requêtes d'un contrôle (redirection, marchand)
MAX_CHECK_FAILURES = 3  # échecs de contrôle avant de conclure « À VÉRIFIER »
STATE_TTL_DAYS = 30  # oubli des offres plus vues en premier prix depuis ce délai

# Mots d'URL ou de titre marchand, après normalisation (minuscules, tout ce qui
# n'est pas lettre ou chiffre devient « - »). Un mot n'est reconnu qu'entier.
REGION_FAMILIES = {
    "GLOBAL": ("global", "worldwide", "ww", "row"),
    "EU": ("eu", "europe", "european"),
}
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
BUNDLE_WORDS = ("bundle", "pack", "collection", "trilogy")  # éditions dont le nom diffère par nature
EXTRA_CONTENT_WORDS = ("dlc", "bundle", "pack", "collection", "bonus", "season", "expansion")  # éditions qui annoncent du contenu en plus
# Suffixes plateforme des noms AllKeyShop (« GTA 6 PS5 »), que les marchands omettent souvent
PLATFORM_SUFFIXES = ("ps5", "ps4", "playstation 5", "playstation 4", "xbox series x", "xbox series", "xbox one",
                     "xbox", "nintendo switch", "switch", "pc")
# Mots qui ne comptent pas pour reconnaître le nom du produit dans une URL
SOFT_WORDS = {"the", "of", "a", "an", "and", "edition", "remastered", "remaster", "remake", "hd"} | set(EDITION_WORDS)
# Abréviations : un mot du nom AllKeyShop et son équivalent chez les marchands, valables dans les deux sens
NAME_ALIASES = (
    ("gta", "grand theft auto"),
    ("cod", "call of duty"),
)
ROMAN = {1: "i", 2: "ii", 3: "iii", 4: "iv", 5: "v", 6: "vi", 7: "vii", 8: "viii", 9: "ix", 10: "x",
         11: "xi", 12: "xii", 13: "xiii", 14: "xiv", 15: "xv", 16: "xvi", 17: "xvii", 18: "xviii", 19: "xix", 20: "xx"}
ARABIC = {v: str(k) for k, v in ROMAN.items()}

log = logging.getLogger("price-check")


# ---- HTTP --------------------------------------------------------------------

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def http_get(url, ua, follow=True, timeout=30):
    """Renvoie (statut HTTP, en-tête Location, corps). Un statut d'erreur ne lève pas."""
    req = urllib.request.Request(url, headers={
        "User-Agent": ua, "Accept": "text/html,*/*;q=0.8", "Accept-Language": "en-GB,en;q=0.9"})
    opener = urllib.request.build_opener() if follow else urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.headers.get("Location"), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Location"), ""


# ---- AllKeyShop : listes, page produit, redirection ---------------------------

def parse_lists(data, lists):
    """Renvoie [(liste, rang, nom, url)] pour chaque liste de `lists`, une seule fois par page.

    `data` : réponse de l'API getLists, {widget: {liste: {items: [...]}}}.
    """
    targets, seen = [], set()
    for list_id, label, top in lists:
        widget, name = list_id.split(".", 1)
        items = ((data.get(widget) or {}).get(name) or {}).get("items")
        if items is None:
            log.warning("liste %s absente de la réponse de l'API", list_id)
            continue
        games = sorted((i for i in items if i.get("productType") == "game"), key=lambda i: i["index"])
        for rank, item in enumerate(games[:top], 1):
            url = item.get("urls", {}).get(SITE_KEY)
            if url and url not in seen:
                seen.add(url)
                targets.append((label, rank, item["name"], url))
    return targets


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


def first_prices(trans):
    """Offre de clé la moins chère de chaque édition (priceCard), hors offres compte et « sans prix »."""
    editions = trans.get("editions") or {}
    regions = trans.get("regions") or {}
    best = {}
    for p in trans.get("prices") or []:
        if p.get("price") == NO_PRICE or p.get("account") or not p.get("dispo"):
            continue
        edition = str(p["edition"])
        if edition not in best or p["priceCard"] < best[edition]["priceCard"]:
            best[edition] = p
    offers = []
    for edition, p in sorted(best.items(), key=lambda kv: kv[1]["priceCard"]):
        offers.append({
            "id": p["id"], "merchant": p["merchant"], "merchantName": p["merchantName"],
            "edition": editions.get(edition, {}).get("name", edition),
            "region": regions.get(str(p["region"]), {}).get("region_name", str(p["region"])),
            "platform": p.get("activationPlatform") or "",
            "price": p["priceCard"], "account": bool(p.get("account")),
        })
    return offers


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


# ---- Analyse d'une URL ou d'un titre marchand --------------------------------

def norm(text):
    """« EA SPORTS FC 27 » -> « ea-sports-fc-27 »."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower().replace("&", " and ")).strip("-")


def compact(text):
    return norm(text).replace("-", "")


LOCALE_SEGMENT_RE = re.compile(r"^[a-z]{2}([-_][a-zA-Z]{2})?$")


def url_text(url):
    """Le chemin de l'URL, sans les segments de langue (/en/, /en-us/)."""
    segments = [s for s in urllib.parse.urlparse(url).path.split("/") if s and not LOCALE_SEGMENT_RE.match(s)]
    return " ".join(urllib.parse.unquote(s) for s in segments)


def name_variants(product):
    """Le nom AllKeyShop et ses variantes : abréviations (« GTA 6 PS5 » / « Grand Theft Auto 6 PS5 »)
    et chiffres <-> chiffres romains (« Dungeons 2 » / « Dungeons II »), combinées."""
    names = [product]
    for suffix in PLATFORM_SUFFIXES:
        if norm(product).endswith("-" + norm(suffix)) and norm(product) != norm(suffix):
            names.append(norm(product)[:-len(norm(suffix)) - 1].replace("-", " "))
            break
    for short, long in NAME_ALIASES:
        for name in list(names):
            spaced = " %s " % norm(name).replace("-", " ")
            for a, b in ((short, long), (long, short)):
                if " %s " % a in spaced:
                    names.append(spaced.replace(" %s " % a, " %s " % b).strip())
    for name in list(names):
        words = norm(name).split("-")
        roman = " ".join(ROMAN[int(w)] if w.isdigit() and int(w) in ROMAN else w for w in words)
        arabic = " ".join(ARABIC.get(w, w) for w in words)
        for variant in (roman, arabic):
            if norm(variant) != norm(name) and variant not in names:
                names.append(variant)
    return tuple(dict.fromkeys(names))


def name_match(names, normed):
    """« exact » si un des noms est dans le texte, « partial » si tous ses mots significatifs y sont."""
    compact_text = normed.replace("-", "")
    for name in names:
        c = compact(name)
        if c and c in compact_text:
            return "exact"
    tokens = set(normed.split("-"))
    for name in names:
        significant = [w for w in norm(name).split("-") if w and w not in SOFT_WORDS]
        if significant and all(w in tokens for w in significant):
            return "partial"
    return None


def region_family(region_name):
    n = region_name.upper()
    if re.search(r"\bEU\b|EUROPE", n):
        return "EU"
    if "GLOBAL" in n or "WORLDWIDE" in n or n in ("GIFT", "XBOX/PC"):
        return "GLOBAL"
    return None


def platform_family(platform):
    if platform in PLATFORM_FAMILIES:
        return platform
    for key in sorted(PLATFORM_FAMILIES, key=len, reverse=True):
        if platform.startswith(key):
            return key
    return None


def canonical_edition(text):
    """« GOTY » et « Game of the Year » -> « gameoftheyear »."""
    c = compact(text)
    for word, full in EDITION_SYNONYMS.items():
        c = c.replace(compact(word), compact(full))
    return c


def edition_matches(edition_name, url_editions):
    """Faux seulement si l'édition AllKeyShop est connue (standard, deluxe, GOTY...) et que le
    marchand en nomme une autre. « Preorder bonus », « Early Access »... ne se comparent pas."""
    aks = canonical_edition(edition_name)
    if not any(canonical_edition(w) in aks for w in EDITION_WORDS):
        return True
    return any(canonical_edition(w) in aks for w in url_editions)


def announces_extra_content(edition_name):
    words = set(norm(edition_name).split("-"))
    return any(w in words for w in EXTRA_CONTENT_WORDS)


def is_bundle(edition_name):
    words = set(norm(edition_name).split("-"))
    return any(w in words for w in BUNDLE_WORDS)


def analyze(product, offer, text, source):
    """Confronte un texte marchand (chemin d'URL ou titre de page) à l'offre AllKeyShop.

    Renvoie {"match": "exact" | "partial" | None, "reasons": [...], "notes": [...]}.
    Chaque raison est un motif de SUSPECT.
    """
    names = name_variants(product)
    normed = norm(text)
    match = name_match(names, normed)
    # Le reste s'analyse sans les mots du nom du produit (« Complete Edition Remastered »...)
    product_words = {w for name in names for w in norm(name).split("-")}
    words = "-".join(w for w in normed.split("-") if w and w not in product_words)

    def has(word):
        return re.search(r"(^|-)%s(-|$)" % re.escape(word), words) is not None

    reasons, notes = [], []
    if match is None and is_bundle(offer["edition"]):
        notes.append("édition %s : nom non contrôlé" % offer["edition"])  # un bundle porte un autre nom
    elif match is None:
        reasons.append("nom du produit absent (%s)" % source)
    elif match == "partial":
        notes.append("nom partiel")
    if not offer["account"] and any(has(w) for w in ACCOUNT_WORDS):
        reasons.append("compte chez le marchand, saisi en clé")
    forbidden = [w for w in FORBIDDEN_REGION_WORDS if has(w)]
    if forbidden:
        reasons.append("région interdite : " + ", ".join(forbidden))
    url_regions = {f for f, ws in REGION_FAMILIES.items() if any(has(w) for w in ws)}
    aks_region = region_family(offer["region"])
    if aks_region and url_regions and aks_region not in url_regions:
        reasons.append("région : AllKeyShop %s, marchand %s" % (offer["region"], "/".join(sorted(url_regions))))
    if any(has(w) for w in GIFT_WORDS) and "GIFT" not in offer["region"].upper():
        reasons.append("gift chez le marchand, affiché en clé %s" % offer["region"])
    aks_platform = platform_family(offer["platform"])
    url_platforms = {f for f, ws in PLATFORM_FAMILIES.items() if any(has(w) for w in ws)}
    if aks_platform and url_platforms and aks_platform not in url_platforms:
        reasons.append("plateforme : AllKeyShop %s, marchand %s" % (offer["platform"], "/".join(sorted(url_platforms))))
    url_editions = [w for w in EDITION_WORDS if has(w)]
    if url_editions and not edition_matches(offer["edition"], url_editions):
        reasons.append("édition : AllKeyShop %s, marchand %s" % (offer["edition"], ", ".join(url_editions)))
    dlc = [w for w in DLC_WORDS if has(w)]
    # « pre-order-bonus-dlc » est le bonus vendu avec le jeu ; « Standard + DLC Bundle » l'annonce
    if dlc and not has("bonus") and not announces_extra_content(offer["edition"]):
        reasons.append("contenu additionnel : " + ", ".join(dlc))
    return {"match": match, "reasons": reasons, "notes": notes}


# ---- Page marchand (dernier repli) -------------------------------------------

TITLE_RES = (
    re.compile(r"<title[^>]*>(.*?)</title>", re.DOTALL | re.IGNORECASE),
    re.compile(r'property="og:title"\s+content="([^"]*)"', re.IGNORECASE),
    re.compile(r'content="([^"]*)"\s+property="og:title"', re.IGNORECASE),
    re.compile(r"<h1[^>]*>(.*?)</h1>", re.DOTALL | re.IGNORECASE),
)


def page_title_from_html(dom):
    parts = []
    for rx in TITLE_RES:
        m = rx.search(dom)
        if m:
            parts.append(re.sub(r"<[^>]+>", " ", html.unescape(m.group(1))))
    text = re.sub(r"\s+", " ", " ".join(parts)).strip()
    return text or None


def page_title(url):
    """Titre, og:title et h1 de la page marchand, via Chromium sans écran. None si impossible."""
    if not shutil.which(CHROMIUM):
        return None
    cmd = [CHROMIUM, "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
           "--user-agent=" + BROWSER_UA, "--virtual-time-budget=20000", "--dump-dom", url]
    try:
        dom = subprocess.run(cmd, capture_output=True, timeout=90).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.TimeoutExpired):
        return None
    return page_title_from_html(dom)


# ---- Contrôle d'une offre ----------------------------------------------------

class CheckError(Exception):
    """Contrôle impossible pour l'instant (réseau, redirection AllKeyShop en erreur) : à réessayer."""


def check_offer(product, offer):
    """Suit la redirection AllKeyShop de l'offre et confronte l'URL marchand au produit.

    Renvoie {"verdict", "reasons", "notes", "url", "method"}.
    """
    try:
        status, _, body = http_get(REDIRECTION_URL % (offer["id"], offer["merchant"]), AKS_UA)
    except OSError as e:
        raise CheckError("redirection AllKeyShop : %s" % e)
    time.sleep(REQUEST_DELAY)
    if status != 200:
        raise CheckError("redirection AllKeyShop HTTP %s" % status)
    url = merchant_url(body)
    if not url:
        raise CheckError("URL marchand introuvable dans la page de redirection")
    url = unwrap_affiliate(url)
    result, method = analyze(product, offer, url_text(url), "URL"), "URL"

    if result["match"] is None:
        # 1er repli : le marchand redirige peut-être vers l'URL complète (Instant Gaming, Fanatical)
        try:
            _, location, _ = http_get(url, BROWSER_UA, follow=False)
        except OSError:
            location = None
        time.sleep(REQUEST_DELAY)
        if location:
            url2 = unwrap_affiliate(urllib.parse.urljoin(url, location))
            result2 = analyze(product, offer, url_text(url2), "URL après redirection du marchand")
            if result2["match"]:
                result, method, url = result2, "URL après 301 marchand", url2

    if result["match"] is None:
        # 2e repli : ouvrir la page marchand
        title = page_title(url)
        if title is None:
            return {"verdict": "À VÉRIFIER", "url": url, "method": "aucune", "notes": [],
                    "reasons": ["URL sans nom du produit et page marchand illisible"]}
        result, method = analyze(product, offer, title, "titre de la page"), "page (Chromium)"

    return {"verdict": "SUSPECT" if result["reasons"] else "OK", "url": url, "method": method,
            "reasons": result["reasons"], "notes": result["notes"]}


# ---- Alertes, état, boucle ---------------------------------------------------

ICONS = {"OK": "🟢", "SUSPECT": "🔴", "À VÉRIFIER": "🟠"}


def format_alert(label, rank, product, page_url, offer, res):
    lines = [
        f"{ICONS[res['verdict']]} **{res['verdict']}** · **{product}** ({label} #{rank}) · {offer['edition']}",
        f"{offer['merchantName']} · {offer['region']} · {offer['platform'] or 'plateforme ?'} · "
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


def load_state(path):
    try:
        with open(path) as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    state.setdefault("checked", {})  # id d'offre -> verdict rendu
    state.setdefault("merchants", {})  # marchand -> méthodes de contrôle qui ont marché
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


def run_cycle(targets, notify, state, checker=check_offer):
    """Lit chaque page suivie et contrôle toute offre en premier prix pas encore contrôlée."""
    now = time.time()
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
    for label, rank, product, page_url in targets:
        try:
            _, _, page_html = http_get(page_url, AKS_UA)
            trans = parse_game_page(page_html)
        except Exception as e:
            log.warning("%s : %s", product, e)
            continue
        finally:
            time.sleep(PAGE_DELAY)
        for offer in first_prices(trans):
            key = str(offer["id"])
            if key in state["checked"]:
                state["checked"][key]["seen"] = now
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
            msg = format_alert(label, rank, product, page_url, offer, res)
            log.info("%s", msg.replace("\n", " | "))
            if res["verdict"] != "OK" or NOTIFY_OK:
                try:
                    notify(msg)
                except Exception as e:
                    log.error("Envoi Discord impossible, nouvel essai au prochain passage : %s", e)
                    continue
            state["checked"][key] = {
                "verdict": res["verdict"], "reasons": res["reasons"], "product": product,
                "edition": offer["edition"], "merchant": offer["merchantName"], "price": offer["price"],
                "url": res["url"], "method": res["method"], "at": stamp, "seen": now,
            }
            if res["method"] != "aucune":
                m = state["merchants"].setdefault(offer["merchantName"], {"methods": {}})
                m["methods"][res["method"]] = m["methods"].get(res["method"], 0) + 1
                m.update(url=res["url"], at=stamp)
    prune_state(state, now)


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
    ap.add_argument("--dry-run", action="store_true", help="affiche les alertes sans les envoyer")
    ap.add_argument("--once", action="store_true", help="un seul passage puis arrêt")
    ap.add_argument("--state", default="state.json", help="fichier des offres déjà contrôlées")
    ap.add_argument("--coverage", action="store_true", help="affiche la table de couverture des marchands et sort")
    ap.add_argument("--check", nargs=2, metavar=("PRODUIT", "URL"), help="analyse une URL marchand et sort")
    ap.add_argument("--edition", default="Standard", help="avec --check : édition affichée")
    ap.add_argument("--region", default="GLOBAL", help="avec --check : région affichée")
    ap.add_argument("--platform", default="", help="avec --check : plateforme d'activation affichée")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.coverage:
        print(coverage_table(load_state(args.state)))
        return
    if args.check:
        product, url = args.check
        offer = {"account": False, "edition": args.edition, "region": args.region, "platform": args.platform}
        res = analyze(product, offer, url_text(unwrap_affiliate(url)), "URL")
        print("nom :", res["match"] or "absent", "| verdict :", "SUSPECT" if res["reasons"] else "OK")
        for r in res["reasons"]:
            print("raison :", r)
        return

    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if args.dry_run:
        notify = lambda msg: None  # le journal affiche déjà chaque verdict sur une ligne
    elif webhook:
        notify = lambda msg: send_discord(webhook, msg)
    else:
        sys.exit("DISCORD_WEBHOOK_URL manquant (ou utiliser --dry-run)")

    state = load_state(args.state)
    modes = [m for m in MODES if args.mode in (m, "both")]
    targets = {m: [] for m in modes}
    lists_at = {m: 0.0 for m in modes}
    due = {m: 0.0 for m in modes}  # prochain passage de chaque mode (time.monotonic)
    while True:
        for mode in modes:
            now = time.monotonic()
            if now < due[mode]:
                continue
            due[mode] = now + MODES[mode]["interval"]
            try:
                if not targets[mode] or now - lists_at[mode] >= LISTS_REFRESH:
                    targets[mode] = fetch_targets(MODES[mode]["lists"])
                    lists_at[mode] = now
                    names = ", ".join(t[2] for t in targets[mode]) if len(targets[mode]) <= 20 else ""
                    log.info("%s : %d pages suivies %s", mode, len(targets[mode]), names)
                run_cycle(targets[mode], notify, state)
                save_state(args.state, state)
            except Exception:
                log.exception("%s : passage en échec, nouvel essai au prochain cycle", mode)
        if args.once:
            return
        time.sleep(max(1, min(due.values()) - time.monotonic()))


if __name__ == "__main__":
    main()
