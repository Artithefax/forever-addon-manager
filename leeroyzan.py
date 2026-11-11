#!/usr/bin/env python3
"""
Leeroyzan
Ein schlanker Addon-Manager mit GUI für World of Warcraft (Forever, Classic, Retail).

Startskript – der Code liegt im Paket leeroy/ (Logik) und leeroy/ui/ (Oberfläche):

  leeroy/constants.py   Name, Version, feste Adressen
  leeroy/i18n.py        tr() und Sprachen, englische Texte in i18n_en.py
  leeroy/config.py      Datenordner (portabel), Einstellungen, Logdatei
  leeroy/wow.py         WoW-Installation, .toc-Dateien, Addons finden
  leeroy/files.py       Installieren, Aktivieren, WTF-Backups, Rollback
  leeroy/github.py, curseforge.py, sharing.py   Quellen, Updates, Teilen
  leeroy/selfupdate.py  Selbst-Update des Managers
  leeroy/lua.py, sessions.py, reports.py, linkdata.py, linkaddon.py   Leeroylink
  leeroy/wowhead.py, questdb.py, auction.py    Nachschlagen, Quests, Auktionshaus
  leeroy/ui/app.py      Hauptfenster aus den Seiten in leeroy/ui/*.py

Läuft mit der Python-Standardbibliothek; tkinterdnd2 ist optional (Drag & Drop).
Arbeitet ausschließlich mit Dateien im Addon-/WTF-Ordner – kein Eingriff in den Spielclient.
"""
from leeroy.main import main

if __name__ == "__main__":
    main()
