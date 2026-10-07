"""Le DOM d'une page marchand ouverte avec Camoufox (un Firefox modifié), pour les marchands que Chromium ne lit pas
(Romain, 07/10/2026, Q12 de l'onglet Romain : « Prends Camoufox » ; d'abord pour les pages marchands bloquées).

Lancé par price_check.py (`camoufox_dom`) avec le Python de son venv, à part du moniteur :
    /opt/camoufox/bin/python tools/camoufox_dom.py URL
écrit le DOM de la page sur la sortie standard. Code de sortie 2 : URL refusée (http et https seulement)."""
import sys

WAIT_MS = 4000  # le temps du JavaScript de la fiche, après le chargement du document
TIMEOUT_MS = 45000


def main(url):
    if not url.startswith(("https://", "http://")):
        return 2
    from camoufox.sync_api import Camoufox
    with Camoufox(headless=True, locale="en-US") as browser:
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
        page.wait_for_timeout(WAIT_MS)
        sys.stdout.write(page.content())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else ""))
