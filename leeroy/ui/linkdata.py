"""Hauptfenster: LinkDataMixin."""
from __future__ import annotations

import time

import tkinter as tk

from ..constants import APP_VERSION
from ..i18n import tr
from ..config import log, save_config
from ..linkdata import LINK_SCHEMA, _norm_name, error_rows, notes_lines, write_link_data
from ..questdb import ensure_reward_data, write_quest_data


class LinkDataMixin:
    """Teil des Hauptfensters (wird in App eingemischt)."""

    # --- Leeroylink: Daten fürs Spiel (Data.lua) ------------------------------------ #
    def link_news(self) -> list:
        return self.cfg.setdefault("link_news", {}).setdefault(self.flavor, [])

    def record_link_news(self, results):
        """Nach Updates den Changelog merken – Leeroylink zeigt ihn beim nächsten Login (neueste 20)."""
        news = self.link_news()
        for repo, info, asset, folders in results:
            nid = f"{repo}@{info.get('tag', '')}"[:80]
            news[:] = [n for n in news if n.get("id") != nid]
            news.append({"id": nid, "addon": self.source_name(repo)[:80], "version": str(info.get("tag", ""))[:40],
                         "lines": notes_lines(info.get("notes", ""))})
        del news[:-20]

    def build_link_payload(self) -> dict:
        updates, issues = [], []
        for sid, src in self.sources().items():
            new = self.updates.get(sid)
            if self.is_held(sid):
                issues.append({"addon": self.source_name(sid),
                               "text": tr("In Leeroyzan festgehalten – keine Updates, bis du es freigibst.")})
            if not new or new == src.get("tag"):
                continue
            note = tr("festgehalten") if self.is_held(sid) else (
                tr("Download nur im Browser") if src.get("allow_dl") is False else None)
            updates.append({"name": self.source_name(sid), "installed": str(src.get("tag", "")),
                            "available": str(new), "note": note})
        managed = sorted({f for src in self.sources().values() for f in src.get("folders", [])}, key=str.lower)
        names = {_norm_name(a.name) for a in self.addons} | {_norm_name(a.title) for a in self.addons}
        installed = [w["name"] for w in self.link_state["wishlist"] if _norm_name(w["name"]) in names]
        profiles = [{"name": name, "enable": list(folders)} for name, folders in sorted(self.sets().items())]
        return {"schema": LINK_SCHEMA, "generated": int(time.time()), "managerVersion": APP_VERSION,
                "updates": updates, "managed": managed, "installed": installed, "profiles": profiles,
                "whatsnew": list(self.link_news()), "knownIssues": issues,
                "questGuide": {"on": self.quest_guide_on(), "at": int(self.cfg.get("quest_guide", {}).get("at", 0))}}

    def schedule_link_write(self):
        if self._link_write_job:
            try:
                self.after_cancel(self._link_write_job)
            except tk.TclError:
                pass
        self._link_write_job = self.after(1500, self.write_link_now)

    def write_link_now(self):
        self._link_write_job = None
        if not self.root_path or not self.flavor:
            return
        try:
            res = write_link_data(self.root_path, self.flavor, self.build_link_payload())
        except OSError as e:
            log.warning("Data.lua für Leeroylink: %s", e)
            return
        if res == "written":
            log.info("Data.lua für Leeroylink geschrieben (%s)", self.flavor)
        if not self._quest_write_running:
            self._quest_write_running = True
            root, flavor = self.root_path, self.flavor
            zn, fn = dict(self.link_state.get("zoneNames", {})), dict(self.link_state.get("factionNames", {}))
            names, on, token = dict(self.link_state.get("items", {})), self.quest_guide_on(), self.cfg.get("github_token", "")

            def work():
                cm = ensure_reward_data(flavor, token) if on else None
                return write_quest_data(root, flavor, zn, fn, cm, names, on)

            def done(res, err):
                self._quest_write_running = False
                if err:
                    log.warning("Quests.lua für Leeroylink: %s", err)
                elif res in ("written", "off"):
                    log.info("Quests.lua für Leeroylink: %s (%s)", res, flavor)
            self.run_quiet(work, done)

    def _link_state_changed(self):
        """Neue Daten aus dem Spiel: Seiten auffrischen, Hinweise zeigen, Data.lua anpassen."""
        if self.current_page == "diagnose":
            self.refresh_diagnose()
        elif self.current_page == "characters":
            self.refresh_characters()
        self._hint_reports()
        self._hint_errors()
        self._quest_guide_button()
        self.schedule_link_write()

    def _addon_by_name(self, name: str):
        return next((a for a in self.addons if a.name.lower() == name.lower()), None)

    def _source_of(self, addon_name: str) -> str:
        return self.folder_source(addon_name)[0] or ""

    def _hint_reports(self):
        """/lz problem im Spiel: Rückgängig anbieten (einmal pro Meldung)."""
        seen = self.cfg.setdefault("reports_seen", {}).setdefault(self.flavor, {})
        for addon, r in self.link_state["reports"].items():
            if seen.get(addon, 0) >= r["time"]:
                continue
            seen[addon] = r["time"]
            sid = self._source_of(addon)
            acts = []
            if sid and self.rollback_info(sid):
                acts.append((tr("↶ Zurücksetzen"), lambda sid=sid: self.rollback(sid)))
            acts.append((tr("Diagnose"), lambda: self.show_page("diagnose")))
            self.notify(tr("Im Spiel gemeldet: {0} (Version {1}) wurde mit /lz problem als fehlerhaft gemeldet und abgeschaltet.",
                           addon, r["version"] or "?"), "warn", acts, key=f"report:{addon}")
        save_config(self.cfg)

    def _hint_errors(self):
        """„Addon X: 14 Fehler seit dem Update“ – nur wenn sich die Vorversion wiederherstellen lässt."""
        seen = self.cfg.setdefault("error_hints", {}).setdefault(self.flavor, {})
        versions = {a.name: a.version for a in self.addons}
        for row in error_rows(self.link_state, versions):
            sid = self._source_of(row["addon"])
            if row["current"] < 3 or not sid or not self.rollback_info(sid) or seen.get(row["addon"]) == row["version"]:
                continue
            seen[row["addon"]] = row["version"]
            self.notify(tr("{0}: {1} Lua-Fehler seit dem Update auf {2}. Vorherige Version wiederherstellen?",
                           row["addon"], row["current"], row["version"] or "?"), "warn",
                        [(tr("↶ Zurücksetzen"), lambda sid=sid: self.rollback(sid)),
                         (tr("Details"), lambda: self.show_page("diagnose"))], key=f"errors:{row['addon']}")
        save_config(self.cfg)
