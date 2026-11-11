"""Name, Version und feste Adressen von Leeroyzan."""
from __future__ import annotations



APP_NAME = "Leeroyzan"
APP_VERSION = "1.9.0"
USER_AGENT = f"Leeroyzan/{APP_VERSION}"
# Selbst-Update über GitHub-Releases dieses Repos
UPDATE_REPO = "Artithefax/leeroyzan"
EXE_ASSET = "Leeroyzan.exe"
SCRIPT_ASSET = "leeroyzan.py"                     # Quelldatei im Repo (für die Python-Variante)
# Link-Buttons in der Kopfzeile (Text, Adresse) – hier einfach anpassbar
HEADER_LINKS = [
    ("GitHub", "https://github.com/Artithefax/leeroyzan"),
    ("CurseForge", "https://www.curseforge.com/wow"),
    ("Wowhead", "wowhead"),                   # Startseite der gewählten Wowhead-Ausgabe (siehe wowhead_base)
]
