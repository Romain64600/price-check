"""Un doute de Claude sur un report, envoyé dans l'onglet Romain de l'admin (Romain, 08/10/2026 : « quand tu as un doute
sur les reports, tu peux les renvoyer sur l'onglet Romain, ça sera plus facile pour moi à traiter de là-bas »).

    python3 tools/ask_romain.py OFFRE "La question, en anglais (l'onglet Romain est en anglais)"

Dépose une demande « ask » pour le service de la console (price-check-console), qui ouvre la question Qn avec les liens
du report : page AllKeyShop, offre chez le marchand, fil Discord (lus dans state.json et threads.json). Romain la règle
depuis son onglet ; sa réponse revient à Claude avec le message suivant de la console, et dans questions.json."""
import json
import os
import secrets
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SHARED = os.environ.get("PRICE_CHECK_REPORTS_DIR", "/var/lib/price-check")


def links_of(offer):
    """Les liens du report : la page AllKeyShop et l'offre chez le marchand (state.json), le fil Discord (threads.json)."""
    links = {}
    try:
        with open(os.path.join(ROOT, "state.json"), encoding="utf-8") as f:
            entry = (json.load(f).get("checked") or {}).get(offer) or {}
    except (OSError, ValueError):
        entry = {}
    if entry.get("page"):
        links["page"] = entry["page"]
    if entry.get("url"):
        links["merchant"] = entry["url"].split("?")[0]
    try:
        with open(os.path.join(SHARED, "threads.json"), encoding="utf-8") as f:
            info = json.load(f).get(offer) or {}
        if info.get("guild") and info.get("thread"):
            links["thread"] = "https://discord.com/channels/%s/%s" % (info["guild"], info["thread"])
    except (OSError, ValueError):
        pass
    return entry, links


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2
    offer, text = argv[1].strip(), argv[2].strip()
    entry, links = links_of(offer)
    head = " · ".join(x for x in (entry.get("product"), entry.get("edition"), entry.get("merchant")) if x)
    request = {"kind": "ask", "user": "claude", "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "offer": offer,
               "text": ("%s (offer %s): %s" % (head, offer, text)) if head else text, "links": links}
    path = os.path.join(SHARED, "console-%d-%s.request" % (time.time_ns() // 1_000_000, secrets.token_hex(3)))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o664)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(request, f, ensure_ascii=False)
    print("question déposée pour l'onglet Romain :", request["text"][:120], "| liens :", ", ".join(sorted(links)) or "aucun")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
