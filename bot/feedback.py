"""Le feedback des reports sur Discord (Romain, 03/10/2026 : « envoyer le feedback sur un thread du report sur
Discord », et l'admin aussi : « les 2 »).

Chaque alerte du moniteur (un message de ses webhooks dans un salon d'alertes) reçoit un fil « Feedback · <jeu> ·
offre <id> ». Une personne autorisée sur le bot (le propriétaire, ou ajoutée par !allow : Romain, 03/10/2026) y répond
« vrai », « faux » ou « à discuter », suivi si besoin d'une note : la décision s'ajoute à decisions.jsonl, le même fichier que
l'admin, que le moniteur relit avant chaque passage. Rien ici ne passe par Claude : des mots-clés seulement, le texte
d'un fil (une alerte cite des pages marchands) ne devient jamais une consigne.

Fonctions pures, sans discord.py : testées par test_feedback.py.
"""

import json
import os
import re
import stat
import time

DECISIONS = {"vrai": "True", "faux": "False positive", "a_discuter": "To discuss"}  # mêmes clés que l'admin ; « true », pas « true positive » (Romain, 07/10/2026)
NOTE_MAX = 1000
THREAD_PREFIX = "Feedback · "
THREADS_FILE = "threads.json"  # offre -> dernier fil ouvert (pour les suites que poste le moniteur), dossier partagé
DECISIONS_FILE = "decisions.jsonl"

# une alerte du moniteur : l'en-tête d'urgence, un verdict, ou un report existant renvoyé ; jamais un récapitulatif (🔁)
ALERT_STARTS = ("🚨", "🔴", "🟠", "⚪", "🟢", "📌")
# 07/10/2026 : the alerts and the threads are in English (« offer », « TO CHECK »), the older ones in French
OFFER_RE = re.compile(r"· (?:offer|offre) (\d{3,12}) ·")
PRODUCT_RE = re.compile(r"\*\*(?:SUSPECT|TO CHECK|UNVERIFIABLE|À VÉRIFIER|NON VÉRIFIABLE|OK)\*\* · \*\*(.+?)\*\*")
THREAD_OFFER_RE = re.compile(r"· (?:offer|offre) (\d{3,12})$")

# une réponse qui tranche : le mot-clé en tête, la note ensuite. En anglais depuis le 07/10/2026 (« true », « false »,
# « discuss »), et les mots français restent compris (« vrai », « faux », « à discuter ») : personne n'est bloqué
DECISION_RES = (
    ("vrai", re.compile(r"^(?:true(?:\s+positive)?|tp|vrai(?:\s+positif)?|vp|✅)(?=$|[\s:,.;!—–-])", re.IGNORECASE)),
    ("faux", re.compile(r"^(?:false(?:\s+positive)?|fp|faux(?:\s+positif)?|❌)(?=$|[\s:,.;!—–-])", re.IGNORECASE)),
    ("a_discuter", re.compile(r"^(?:to\s+discuss|discuss|[àa]\s+discuter|discuter|💬)(?=$|[\s:,.;!—–-])", re.IGNORECASE)),
)


def alert_offer(content):
    """L'id d'offre d'un message d'alerte du moniteur, ou None (récapitulatif, autre message)."""
    content = (content or "").lstrip()
    if not content.startswith(ALERT_STARTS):
        return None
    m = OFFER_RE.search(content)
    return m.group(1) if m else None


def alert_product(content):
    m = PRODUCT_RE.search(content or "")
    return m.group(1) if m else ""


def thread_name(offer, product):
    """« Feedback · Minecraft Dungeons · offer 140513764 » : le nom se coupe dans le jeu, jamais dans l'offre (100
    caractères au plus pour Discord)."""
    tail = " · offer %s" % offer
    room = 100 - len(THREAD_PREFIX) - len(tail)
    product = (product or "report").strip()
    if len(product) > room:
        product = product[:room - 1].rstrip() + "…"
    return THREAD_PREFIX + product + tail


def thread_offer(name):
    """L'offre d'un fil de feedback, d'après son nom, ou None (un autre fil)."""
    if not (name or "").startswith(THREAD_PREFIX):
        return None
    m = THREAD_OFFER_RE.search(name)
    return m.group(1) if m else None


def parse_decision(text):
    """(clé, note) si le message tranche (« faux : bonne édition », « ✅ », « à discuter, la page dit Global »), sinon
    None (« vraiment ? » ne tranche pas : le mot-clé doit être entier)."""
    text = (text or "").strip()
    for key, rx in DECISION_RES:
        m = rx.match(text)
        if m:
            note = re.sub(r"^[\s:,.;!—–-]+", "", text[m.end():]).strip()
            note = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", note)[:NOTE_MAX]
            return key, note
    return None


# La note est facultative (Romain, 05/10/2026 : « si on est d'accord avec l'erreur décrite sur le report, il n'y a pas de raison de commenter »).
INSTRUCTIONS = ("Feedback on this report: answer **true** (the error is real), **false** (false positive) or **discuss**. "
                "Agree with the error described: **true** is enough, no note. Otherwise, add a note after the word "
                "(« false: the AllKeyShop page is a DLC »). The French words (vrai, faux, à discuter) work too. Only the "
                "people allowed on the bot can decide. The decision is saved in the Price check admin; the monitor takes "
                "it into account at its next pass, and posts the follow-ups here (repaired, still wrong…).")


def confirmation(key, note, by):
    return "✅ Decision saved: **%s**%s — by %s. Visible in the admin, taken into account at the next pass." % (
        DECISIONS[key], (" — « %s »" % note) if note else "", by)


# ---- Fichiers du dossier partagé avec le moniteur et l'admin (/var/lib/price-check) ----------------------------
# Dossier inscriptible par le groupe debian : jamais d'écriture ni de lecture à travers un lien symbolique.

def append_decision(directory, offer, key, note, by, at=None):
    """Ajoute une décision à decisions.jsonl (une ligne JSON, le format de l'admin). Créé ici, le fichier reste
    inscriptible par le groupe (l'admin, sous debian, y écrit aussi)."""
    if not str(offer).isdigit() or key not in DECISIONS:
        raise ValueError("invalid decision")
    line = json.dumps({"offer": str(offer), "decision": key, "note": (note or "")[:NOTE_MAX], "by": by,
                       "at": at or time.strftime("%Y-%m-%dT%H:%M:%S%z")}, ensure_ascii=False) + "\n"
    path = os.path.join(directory, DECISIONS_FILE)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o664)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("%s : pas un fichier ordinaire" % path)
        if os.fstat(fd).st_size == 0:
            os.fchmod(fd, 0o664)
        os.write(fd, line.encode("utf-8"))  # une seule écriture en mode ajout : la ligne ne se mêle pas à une autre
    finally:
        os.close(fd)
    return line


def load_threads(directory):
    try:
        fd = os.open(os.path.join(directory, THREADS_FILE), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return {}
    with os.fdopen(fd, encoding="utf-8", errors="replace") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            return {}
        try:
            data = json.load(f)
        except ValueError:
            return {}
    return data if isinstance(data, dict) else {}


def save_threads(directory, threads):
    path = os.path.join(directory, THREADS_FILE)
    tmp = path + ".tmp"
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        os.fchmod(f.fileno(), 0o644)
        json.dump(threads, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def webhook_id(url):
    """L'id d'un webhook Discord d'après son URL (…/api/webhooks/<id>/<jeton>), ou None."""
    m = re.search(r"/api/webhooks/(\d+)/", url or "")
    return int(m.group(1)) if m else None
