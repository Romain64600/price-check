"""La page « Guide de l'équipe » de l'admin Price check (executor : src/admin/static/pricecheck-guide.html), générée
depuis docs/guide-equipe.md (FR) et docs/team-guide.md (EN).

Romain, 03/10/2026 : « je préférerais que tu l'intègres à l'admin. Quelqu'un qui a accès à l'admin a accès à ce
guide. » Le guide s'écrit en Markdown dans ce dépôt ; ce script en fait la page de l'admin, sans rien en ligne (la
politique de sécurité de l'admin, default-src 'self', refuse script et style en ligne) : le texte est échappé, les
couleurs du schéma viennent de pricecheck-guide.css, la bascule FR / EN de pricecheck-guide.js.

    python3 tools/guide_html.py | runuser -u debian -- tee /home/debian/executor/src/admin/static/pricecheck-guide.html >/dev/null

puis, dans l'executor, sa suite complète et le commit (CLAUDE.md du projet). Un convertisseur réduit aux formes du
guide : titres, paragraphes, listes (une sous-liste à 4 espaces), listes numérotées, tableaux, blocs de code, gras,
code, liens. Le bloc ```mermaid devient le schéma SVG du cycle d'un report.
"""

import html
import os
import re
import sys
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES = (("fr", "docs/guide-equipe.md"), ("en", "docs/team-guide.md"))
ADMIN_PAGE = "https://169.58.5.63.sslip.io/executor/price-check"  # dans l'admin : un lien relatif
LIST_RE = re.compile(r"^(\d+\.|-) ")

TABS = """    <nav class="tabs">
      <a href="." class="tab">Validation &amp; Submit</a>
      <a href="tri" class="tab">Tri des listes</a>
      <a href="auto" class="tab">Data Entry Auto</a>
      <a href="games" class="tab">Saisie par jeux</a>
      <a href="sql" class="tab">Tri SQL</a>
      <a href="overview" class="tab">Vue d'ensemble</a>
      <a href="price-check" class="tab active" aria-current="page">Price check</a>
    </nav>"""

CYCLE = {  # le schéma du cycle d'un report, par langue : (titre, boîtes, libellés des flèches)
    "fr": ("Une alerte est suivie jusqu’à sa réparation",
           [("Détection", "tops : 2 min 30", "homepage : 15 min"), ("Alerte", "urgences, tops", "ou homepage"),
            ("Fil de feedback", "ouvert par le bot", "sous l’alerte"), ("Décision", "vrai, faux ou", "à discuter + note"),
            ("Recontrôle", "toutes les heures", "tant qu’en erreur"), ("Réparée", "l’offre a changé", "ou quitté la page"),
            ("Faux positif", "plus recontrôlée,", "la règle corrigée")],
           ("offre signalée", "OK", "écrite dans le fil", "faux")),
    "en": ("An alert is followed until it is fixed",
           [("Detection", "tops: every 2 min 30", "homepage: 15 min"), ("Alert", "emergencies, tops", "or homepage"),
            ("Feedback thread", "opened by the bot", "under the alert"), ("Decision", "vrai, faux or", "à discuter + note"),
            ("Re-check", "every hour", "while still wrong"), ("Repaired", "the offer changed", "or left the page"),
            ("False positive", "no more re-checks,", "the rule is fixed")],
           ("flagged offer", "OK", "written in the thread", "faux")),
}


def cycle_svg(lang):
    """Le cycle : détection, alerte, fil, décision ; l'alerte part au recontrôle horaire, une offre réparée l'est dans
    le fil, un « faux » arrête le suivi. Mêmes positions que le schéma de la doc partagée."""
    title, boxes, labels = CYCLE[lang]
    e = html.escape
    w, h = 160, 72
    centers = [(104, 110), (288, 110), (472, 110), (656, 110), (288, 270), (472, 270), (656, 270)]
    marker = "cyc-arrow-%s" % lang
    out = ['<svg class="cycle" viewBox="0 0 760 332" role="img" aria-label="%s">' % e(title),
           '<defs><marker id="%s" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" '
           'orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" class="cyc-arrow"/></marker></defs>' % marker,
           '<text class="cyc-title" x="24" y="34">%s</text>' % e(title)]
    for d in ("M184 110H208", "M368 110H392", "M552 110H576", "M288 146V234", "M368 270H392", "M472 234V146", "M656 146V234"):
        out.append('<path class="cyc-edge" d="%s" marker-end="url(#%s)"/>' % (d, marker))
    for i, ((cx, cy), (name, line1, line2)) in enumerate(zip(centers, boxes)):
        main = i == 5  # la réparation : le but
        out.append('<rect class="%s" x="%d" y="%d" width="%d" height="%d" rx="8"/>' % (
            "cyc-box cyc-main" if main else "cyc-box", cx - w // 2, cy - h // 2, w, h))
        out.append('<text class="cyc-name" x="%d" y="%d" text-anchor="middle">%s</text>' % (cx, cy - 12, e(name)))
        for dy, line in ((4, line1), (20, line2)):
            out.append('<text class="%s" x="%d" y="%d" text-anchor="middle">%s</text>' % (
                "cyc-line cyc-strong" if main else "cyc-line", cx, cy + dy, e(line)))
    flagged, ok, thread, false = labels
    out += ['<text class="cyc-label" x="296" y="194">%s</text>' % e(flagged),
            '<text class="cyc-label" x="380" y="262" text-anchor="middle">%s</text>' % e(ok),
            '<text class="cyc-label" x="480" y="194">%s</text>' % e(thread),
            '<text class="cyc-label" x="664" y="194">%s</text>' % e(false), "</svg>"]
    return "".join(out)


def link(text, url):
    if url == ADMIN_PAGE:
        return '<a href="price-check">%s</a>' % text
    if url.startswith(("https://", "http://")):
        return '<a href="%s" target="_blank" rel="noopener noreferrer">%s</a>' % (html.escape(url, quote=True), text)
    return text  # un lien vers un fichier du dépôt (precedents.md) : le texte seul dans l'admin


def inline(text):
    """Le texte d'une ligne : échappé, puis `code`, **gras**, [lien](url), <url>."""
    out = []
    for i, part in enumerate(re.split(r"(`[^`]+`)", text)):
        if i % 2:
            out.append("<code>%s</code>" % html.escape(part[1:-1]))
            continue
        part = html.escape(part, quote=False)
        part = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", part)
        part = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", lambda m: link(m.group(1), html.unescape(m.group(2))), part)
        part = re.sub(r"&lt;(https?://[^\s&]+)&gt;", lambda m: link(html.escape(m.group(1)), m.group(1)), part)
        out.append(part)
    return "".join(out)


def slug(text):
    """« Cas déjà jugés » -> « cas-deja-juges » (les accents retirés, pas les lettres)."""
    plain = "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", plain).strip("-")


def render_list(items):
    ordered = items[0][0].isdigit()
    out, nested = ["<ol>" if ordered else "<ul>"], []

    def flush():
        if nested:
            out[-1] += "<ul>%s</ul>" % "".join("<li>%s</li>" % inline(n) for n in nested)
            nested.clear()
    for line in items:
        if line.startswith("    "):
            nested.append(LIST_RE.sub("", line.strip(), count=1))
            continue
        flush()
        if out[-1].startswith("<li>"):
            out[-1] += "</li>"
        out.append("<li>%s" % inline(LIST_RE.sub("", line, count=1)))
    flush()
    out[-1] += "</li>"
    out.append("</ol>" if ordered else "</ul>")
    return "".join(out)


def render_table(rows):
    cells = [[c.strip().replace("\x00", "|") for c in r.replace("\\|", "\x00").strip().strip("|").split("|")] for r in rows]
    head, body = cells[0], cells[2:]
    return ('<div class="table-wrap"><table><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>' % (
        "".join("<th>%s</th>" % inline(c) for c in head),
        "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % inline(c) for c in row) for row in body)))


def render(md, lang):
    """(titre, sections) : le corps de l'article d'une langue, et la table des matières."""
    lines, i, out, toc, title = md.splitlines(), 0, [], [], ""
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
        elif line.startswith("```"):
            kind, body = line[3:].strip(), []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1
            out.append(cycle_svg(lang) if kind == "mermaid" else "<pre><code>%s</code></pre>" % html.escape("\n".join(body)))
        elif line.startswith("# "):
            title = line[2:].strip()
            i += 1
        elif line.startswith("## "):
            text = line[3:].strip()
            anchor = "%s-%s" % (lang, slug(text))
            toc.append((anchor, text))
            out.append('<h2 id="%s">%s</h2>' % (anchor, inline(text)))
            i += 1
        elif line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append(lines[i])
                i += 1
            out.append(render_table(rows))
        elif LIST_RE.match(line):
            items = []
            while i < len(lines) and (LIST_RE.match(lines[i]) or lines[i].startswith("    ")):
                items.append(lines[i])
                i += 1
            out.append(render_list(items))
        else:
            para = []
            while i < len(lines) and lines[i].strip() and not re.match(r"^(```|#|\|)", lines[i]) and not LIST_RE.match(lines[i]):
                para.append(lines[i].strip())
                i += 1
            text = " ".join(para)
            if text.startswith(("État au", "As of")):  # l'en-tête du fichier : la date seulement (l'admin est la page)
                text = re.split(r"(?<=\d{4})\. ", text)[0] + "."
            out.append('<p class="dim">%s</p>' % inline(text) if text.startswith(("État au", "As of")) else "<p>%s</p>" % inline(text))
    return title, toc, "".join(out)


def page():
    articles = []
    for lang, path in SOURCES:
        with open(os.path.join(ROOT, path), encoding="utf-8") as f:
            title, toc, body = render(f.read(), lang)
        nav = '<nav class="guide-toc" aria-label="%s"><ol>%s</ol></nav>' % (
            "Sommaire" if lang == "fr" else "Contents", "".join('<li><a href="#%s">%s</a></li>' % (a, inline(t)) for a, t in toc))
        articles.append('<article id="guide-%s" lang="%s" class="guide%s"><h2 class="guide-title">%s</h2>%s%s</article>' % (
            lang, lang, "" if lang == "fr" else " hidden", inline(title), nav, body))
    return """<!DOCTYPE html>
<!-- Généré par price-check/tools/guide_html.py depuis docs/guide-equipe.md et docs/team-guide.md : ne pas modifier à la main. -->
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AKS Executor — Price check, guide de l'équipe</title>
  <link rel="stylesheet" href="auto.css">
  <link rel="stylesheet" href="pricecheck.css">
  <link rel="stylesheet" href="pricecheck-guide.css">
</head>
<body>
  <header class="topbar">
    <div class="brand">
      <span class="dot"></span>
      <h1>Price check <span class="sub">— guide de l'équipe · team guide</span></h1>
    </div>
%s
    <div class="topbar-right">
      <a class="topbar-link" href="price-check" title="Les reports à trancher">← Reports</a>
      <button id="lang-fr" type="button" aria-pressed="true" title="Français">FR</button>
      <button id="lang-en" type="button" aria-pressed="false" title="English">EN</button>
      <button id="theme" type="button" title="Basculer le thème">◐</button>
    </div>
  </header>
  <main class="guide-main">
%s
  </main>
  <script src="pricecheck-guide.js"></script>
</body>
</html>
""" % (TABS, "\n".join(articles))


if __name__ == "__main__":
    sys.stdout.write(page())
