"""Moniteur des pages produit du top AllKeyShop, avec alertes Discord.

Boucle sans fin : récupère le top 5 All Popular et le top 4 PC Coming soon, lit les
offres de chaque page produit et alerte sur Discord quand une offre est au moins
30 % moins chère que la suivante dans la même édition.
"""

import argparse
import json
import logging
import os
import re
import sys
import time
import urllib.request

# (id de liste sidebar.<id>, libellé, nombre de jeux suivis)
LISTS = (
    ("all.popular", "Popular", 5),
    ("pc.soon", "Coming soon PC", 4),
)
LISTS_URL = (
    "https://api.allkeyshop.com/videogame/api/topClick/getLists/eur/allkeyshop.com?"
    + "&".join(f"lists[]=sidebar.{key}" for key, _, _ in LISTS)
)
USER_AGENT = "AKS/Staff"
SITE_KEY = "allkeyshop.com.eur"

NO_PRICE = 0.02  # sentinelle « pas de prix »
THRESHOLD = 0.30

LISTS_REFRESH = 1800  # max-age de l'API getLists
CYCLE_INTERVAL = 150  # le cache des pages produit est de 120 s
REQUEST_DELAY = 2  # pause entre deux GET

GAME_PAGE_RE = re.compile(r"var gamePageTrans = (\{.*?\});\n", re.DOTALL)

log = logging.getLogger("price-check")


def http_get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def parse_lists(data, lists=LISTS):
    """Renvoie [(liste, rang, nom, url)] pour le haut de chaque liste de `lists`."""
    sidebar = data["sidebar"]
    targets = []
    for key, label, top in lists:
        games = [i for i in sidebar[key]["items"] if i.get("productType") == "game"]
        games.sort(key=lambda i: i["index"])
        for rank, item in enumerate(games[:top], 1):
            url = item.get("urls", {}).get(SITE_KEY)
            if url:
                targets.append((label, rank, item["name"], url))
    return targets


def parse_game_page(html):
    m = GAME_PAGE_RE.search(html)
    if not m:
        raise ValueError("gamePageTrans introuvable")
    return json.loads(m.group(1))


def find_anomalies(trans, threshold=THRESHOLD):
    """Offres de clés au moins `threshold` moins chères que la suivante de la même édition.

    Même périmètre que le tableau affiché : priceCard, offres de clés seulement.
    """
    editions = trans.get("editions") or {}
    regions = trans.get("regions") or {}
    groups = {}
    for p in trans.get("prices") or []:
        if p.get("price") == NO_PRICE or p.get("account") or not p.get("dispo"):
            continue
        groups.setdefault(str(p["edition"]), []).append(p)

    anomalies = []
    for edition_id, offers in groups.items():
        if len(offers) < 2:
            continue
        offers.sort(key=lambda p: p["priceCard"])
        cheapest, runner_up = offers[0], offers[1]
        if runner_up["priceCard"] <= 0:
            continue
        gap = 1 - cheapest["priceCard"] / runner_up["priceCard"]
        if gap >= threshold:
            anomalies.append({
                "offer_id": cheapest["id"],
                "edition": editions.get(edition_id, {}).get("name", edition_id),
                "merchant": cheapest["merchantName"],
                "region": regions.get(str(cheapest["region"]), {}).get("region_name", cheapest["region"]),
                "platform": cheapest.get("activationPlatform"),
                "price": cheapest["priceCard"],
                "next_price": runner_up["priceCard"],
                "next_merchant": runner_up["merchantName"],
                "gap": gap,
            })
    return anomalies


def format_alert(label, rank, name, url, a):
    return (
        f"**{name}** ({label} #{rank}) - {a['edition']}\n"
        f"{a['merchant']} ({a['region']}, {a['platform']}) : **{a['price']:.2f} €**, "
        f"soit -{a['gap']:.0%} face à {a['next_merchant']} à {a['next_price']:.2f} €\n"
        f"Offre {a['offer_id']} - <{url}>"
    )


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
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, path)


def run_cycle(targets, notify, state):
    """Vérifie chaque page produit. `state` retient les offres déjà alertées (id -> prix)."""
    seen = set()
    for label, rank, name, url in targets:
        try:
            anomalies = find_anomalies(parse_game_page(http_get(url)))
        except Exception as e:
            log.warning("%s : %s", name, e)
            continue
        finally:
            time.sleep(REQUEST_DELAY)
        for a in anomalies:
            key = str(a["offer_id"])
            seen.add(key)
            if state.get(key) == a["price"]:
                continue
            msg = format_alert(label, rank, name, url, a)
            log.info("ALERTE %s", msg.replace("\n", " | "))
            try:
                notify(msg)
                state[key] = a["price"]
            except Exception as e:
                log.error("Envoi Discord impossible : %s", e)
    # Une offre qui n'est plus anormale pourra de nouveau alerter plus tard.
    for key in list(state):
        if key not in seen:
            del state[key]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="affiche les alertes sans les envoyer")
    ap.add_argument("--once", action="store_true", help="un seul passage puis arrêt")
    ap.add_argument("--state", default="alerted.json", help="fichier des offres déjà alertées")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if args.dry_run:
        notify = print
    elif webhook:
        notify = lambda msg: send_discord(webhook, msg)
    else:
        sys.exit("DISCORD_WEBHOOK_URL manquant (ou utiliser --dry-run)")

    state = load_state(args.state)
    targets, lists_at = [], 0
    while True:
        start = time.monotonic()
        try:
            if not targets or start - lists_at >= LISTS_REFRESH:
                targets = parse_lists(json.loads(http_get(LISTS_URL)))
                lists_at = start
                log.info("%d pages suivies : %s", len(targets), ", ".join(t[2] for t in targets))
            run_cycle(targets, notify, state)
            save_state(args.state, state)
        except Exception:
            log.exception("Passage en échec, nouvel essai au prochain cycle")
        if args.once:
            return
        time.sleep(max(0, CYCLE_INTERVAL - (time.monotonic() - start)))


if __name__ == "__main__":
    main()
