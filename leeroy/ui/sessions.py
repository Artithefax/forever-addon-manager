"""Hauptfenster: SessionsPage."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from tkinter import ttk
import tkinter as tk
import tkinter.font as tkfont

from ..i18n import tr
from ..config import log, save_config
from ..wow import running_wow
from ..files import disabled_dir, open_in_explorer
from ..selfupdate import parse_version
from ..lua import LINK_ADDON, OLD_LINK_ADDON
from ..sessions import (
    delete_session,
    import_link_sessions,
    link_savedvariables,
    link_signature,
    load_sessions,
    session_net_money,
    sessions_dir,
)
from ..linkaddon import (
    bundled_link_dir,
    install_link_addon,
    installed_link_dir,
    link_version,
    migrate_link_addon,
    migrate_link_savedvariables,
    old_link_dir,
)
from ..reports import (
    DISCORD_WEBHOOK_RE,
    analysis_report,
    chart_series,
    discord_payload,
    discord_send,
    fmt_clock,
    fmt_day,
    fmt_dur,
    fmt_int,
    fmt_money,
    overview_report,
    plural,
    report_plain_text,
    session_headline,
    session_report,
)
from ..linkdata import empty_link_state
from .theme import THEME


class SessionsPage:
    """Teil des Hauptfensters (wird in App eingemischt)."""

    # --- Sitzungen -------------------------------------------------------------- #
    SESSION_PERIODS = [("all", "Alle Zeiträume"), ("today", "Heute"), ("week", "Letzte 7 Tage"),
                       ("month", "Letzte 30 Tage")]

    def _page_sessions(self):
        t = THEME
        page, head = self._page("sessions", tr("Leeroylink"), tr("Spielsitzungen"))
        right = ttk.Frame(head)
        right.pack(side="right", anchor="s")
        ttk.Button(right, text=tr("Jetzt einlesen"), command=lambda: self.import_sessions(manual=True)).pack(side="right")
        pcb = ttk.Combobox(right, textvariable=self.sess_period_var, state="readonly", width=16,
                           values=[tr(label) for _, label in self.SESSION_PERIODS])
        pcb.pack(side="right", padx=(0, 8))
        pcb.bind("<<ComboboxSelected>>", lambda e: self._sess_filter_changed())
        self.sess_period_var.set(tr(dict(self.SESSION_PERIODS)[self.sess_period]))
        self.sess_char_cb = ttk.Combobox(right, textvariable=self.sess_char_var, state="readonly", width=22)
        self.sess_char_cb.pack(side="right", padx=(0, 8))
        self.sess_char_cb.bind("<<ComboboxSelected>>", lambda e: self.refresh_sessions_page())

        link = self._card_box(page, pady=(12, 0))
        row = ttk.Frame(link, style="Panel.TFrame")
        row.pack(fill="x")
        # wraplength < 300: _fit_wraps lässt das Feld in Ruhe, die Breite rechnet _wrap_link_label (Knopf daneben)
        self.link_label = ttk.Label(row, textvariable=self.link_state_var, style="Panel.TLabel", wraplength=280,
                                    justify="left")
        self.link_label.pack(side="left", fill="x", expand=True)
        self.link_btn = ttk.Button(row, style="Primary.TButton", command=self.install_link)
        row.bind("<Configure>", lambda e: self._wrap_link_label(e.width))

        body = ttk.Frame(page)
        body.pack(fill="both", expand=True, pady=(12, 0))
        left = ttk.Frame(body, width=496)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        frame, self.sess_tree = self._list(left, (("when", tr("Datum")), ("char", tr("Charakter")), ("dur", tr("Dauer")),
                                                  ("xp", tr("XP")), ("gold", tr("Gold"))),
                                           (134, 104, 78, 66, 92), stretch=("char",))
        frame.pack(fill="both", expand=True)
        self.sess_tree.tag_configure("total", foreground=t["gold"], font=self.fonts["bold"])
        self.sess_tree.bind("<<TreeviewSelect>>", lambda e: self._sess_show())
        brow = ttk.Frame(left)
        brow.pack(fill="x", pady=(8, 0))
        ttk.Button(brow, text=tr("Text kopieren"), command=self.copy_session_text).pack(side="left")
        ttk.Button(brow, text=tr("Löschen"), style="Danger.TButton", command=self.delete_selected_session).pack(
            side="left", padx=(6, 0))
        ttk.Button(brow, text=tr("Ordner öffnen"), command=self.open_sessions_folder).pack(side="right")

        box = self._card_box(body, pady=(0, 0), side="left", fill="both", expand=True, padx=(14, 0))
        seg = ttk.Frame(box, style="Panel.TFrame")
        seg.pack(fill="x", pady=(0, 10))
        self.sess_view_btns = {}
        for key, label in self.SESS_VIEWS:
            b = ttk.Button(seg, text=tr(label), width=11, command=lambda k=key: self._sess_select_view(k))
            b.pack(side="left", padx=(0, 4))
            self.sess_view_btns[key] = b
        ttk.Button(seg, text="Discord ↗", command=self.send_session_discord).pack(side="right")
        tf, txt = self._text(box)
        tf.pack(fill="both", expand=True)
        self._tag_text(txt)
        self.sess_text = txt
        self.sess_text_frame = tf
        self.sess_canvas = tk.Canvas(box, bg=t["panel"], highlightthickness=0)
        self.sess_canvas.bind("<Configure>", lambda e: self.after_idle(self.draw_session_charts))
        self.after_idle(lambda: self._sess_select_view(self.sess_view))

    def _wrap_link_label(self, width: int | None = None):
        try:
            width = width or self.link_label.master.winfo_width()
            btn = self.link_btn.winfo_reqwidth() + 16 if self.link_btn.winfo_manager() else 0
            self.link_label.configure(wraplength=max(240, width - btn - 8))
        except tk.TclError:
            pass

    def _char_label(self, char: str) -> str:
        """Name, bei gleichen Namen auf mehreren Realms mit Realm."""
        name, _, realm = char.partition("-")
        same = [c for c in {s["char"] for s in self.sessions_all} if c.partition("-")[0] == name]
        return f"{name} ({realm})" if len(same) > 1 and realm else name

    def _sess_filter_changed(self):
        label = self.sess_period_var.get()
        self.sess_period = next((k for k, l in self.SESSION_PERIODS if tr(l) == label), "all")
        self.refresh_sessions_page()

    def refresh_sessions_page(self, select: str = ""):
        """Liste neu aufbauen. select: Sitzungs-ID oder "total"."""
        if getattr(self, "sess_tree", None) is None or not self.sess_tree.winfo_exists():
            return
        self.sessions_all = load_sessions(self.flavor) if self.flavor else []
        chars = sorted({s["char"] for s in self.sessions_all}, key=str.lower)
        labels = {c: self._char_label(c) for c in chars}
        all_label = tr("Alle Charaktere")
        self.sess_char_cb["values"] = [all_label] + [labels[c] for c in chars]
        if self.sess_char_var.get() not in self.sess_char_cb["values"]:
            self.sess_char_var.set(all_label)
        want = next((c for c in chars if labels[c] == self.sess_char_var.get()), "")
        now = datetime.now()
        cutoff = {"today": now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp(),
                  "week": now.timestamp() - 7 * 86400, "month": now.timestamp() - 30 * 86400}.get(self.sess_period, 0)
        shown = [s for s in self.sessions_all if (not want or s["char"] == want) and s["start"] >= cutoff]
        self.sessions_shown = shown

        tree = self.sess_tree
        if not select:
            cur = tree.selection()
            if cur:
                select = "total" if cur[0] == "total" else next(
                    (s["id"] for i, s in enumerate(self._sess_rows) if f"s{i}" == cur[0]), "")
        self._sess_rows = shown
        tree.delete(*tree.get_children())
        tree.insert("", "end", iid="total", tags=("total",), values=(
            tr("Σ Gesamt"), plural(len(shown), "{0} Sitzung", "{0} Sitzungen"),
            fmt_dur(sum(s["played"] for s in shown)), fmt_int(sum(s["xp"]["total"] for s in shown)),
            fmt_money(sum(session_net_money(s) for s in shown), sign=True)))
        target = "total"
        for i, s in enumerate(shown):
            tags = ["odd"] if i % 2 else []
            if s.get("legacy"):
                tags.append("dim")
            tree.insert("", "end", iid=f"s{i}", tags=tuple(tags), values=(
                datetime.fromtimestamp(s["start"]).strftime(tr("%d.%m.%Y  %H:%M")), labels.get(s["char"], s["char"]),
                fmt_dur(s["played"]), fmt_int(s["xp"]["total"]), fmt_money(session_net_money(s), sign=True)))
            if select == s["id"]:
                target = f"s{i}"
        if not select and shown:
            target = "s0"                      # ohne Auswahl: die neueste Sitzung
        tree.selection_set(target)
        tree.see(target)
        self._sess_show()

    def _sess_selected(self) -> dict | None:
        sel = self.sess_tree.selection()
        if not sel or sel[0] == "total":
            return None
        try:
            return self._sess_rows[int(sel[0][1:])]
        except (ValueError, IndexError):
            return None

    def _sess_show(self):
        if not self.sessions_all:
            parts = [(tr("Noch keine Sitzungen") + "\n", "title"),
                     ("\n" + tr("So funktioniert es") + "\n", "head"),
                     (tr("Leeroylink zeichnet im Spiel jede Sitzung auf: Spielzeit, Erfahrung, Gold, Quests, Beute, "
                         "Kampf (Schaden, Heilung, Hiebe, Zauber, Buffs, Tode), Strecke und Schritte, Berufe, entdeckte "
                         "Gebiete, Dungeons und Schlachtzüge sowie besiegte Gegner, Elite und Bosse.") + "\n\n", ""),
                     (tr("Sobald du dich ausloggst, schreibt WoW die Daten. Leeroyzan übernimmt sie dann automatisch, "
                         "speichert sie dauerhaft und fasst jede Sitzung hier zusammen.") + "\n\n", ""),
                     (tr("Ein /reload oder kurzes Ausloggen (unter 15 Minuten) mit demselben Charakter zählt zur selben "
                         "Sitzung. Im Spiel zeigt /lz sitzung die laufende Sitzung.") + "\n", "dim")]
        else:
            s = self._sess_selected()
            who = self.sess_char_var.get() or tr("Alle Charaktere")
            period = tr(dict(self.SESSION_PERIODS)[self.sess_period])
            if self.sess_view == "charts":
                self.draw_session_charts()
                parts = overview_report(self.sessions_shown, f"{who}, {period}")
            elif self.sess_view == "analysis":
                parts = analysis_report(self.sessions_shown, f"{who}, {period}")
            elif s is None:
                parts = overview_report(self.sessions_shown, f"{who}, {period}")
            else:
                parts = session_report(s)
        self._sess_parts = parts
        self._set_text(self.sess_text, parts=parts)
        self.sess_text.yview_moveto(0)

    def copy_session_text(self):
        if self._sess_parts:
            self._clip(report_plain_text(self._sess_parts), tr("Zusammenfassung kopiert."))

    def delete_selected_session(self):
        s = self._sess_selected()
        if s is None:
            self.set_status(tr("Bitte eine einzelne Sitzung auswählen."))
            return
        if not self.ask(tr("Sitzung vom {0} ({1}) aus Leeroyzan löschen? Sie wird danach auch nicht mehr aus dem "
                           "Spiel übernommen.", fmt_day(s["start"]) + " " + fmt_clock(s["start"]), s["name"] or s["char"]),
                        yes=tr("Löschen"), no=tr("Abbrechen")):
            return
        try:
            delete_session(self.flavor, s)
        except OSError as e:
            self.error(tr("Konnte nicht gelöscht werden:\n{0}", e))
            return
        self.refresh_sessions_page(select="total")
        self.set_status(tr("Sitzung gelöscht."))

    def open_sessions_folder(self):
        if not self.flavor:
            return
        d = sessions_dir(self.flavor)
        d.mkdir(parents=True, exist_ok=True)
        open_in_explorer(d)

    def show_session(self, sid: str):
        self.sess_char_var.set("")
        self.sess_period = "all"
        self.sess_period_var.set(tr(dict(self.SESSION_PERIODS)["all"]))
        self.show_page("sessions")
        self.refresh_sessions_page(select=sid)

    # Übernahme aus dem Spiel ----------------------------------------------------- #
    def import_sessions(self, manual: bool = False):
        """Liest die SavedVariables des Addons (im Hintergrund) und übernimmt neue Sitzungen."""
        if not self.root_path or not self.flavor:
            if manual:
                self.ready(modifies=False)
            return
        if self._sess_importing:
            return
        self._sess_importing = True
        root, flavor = self.root_path, self.flavor
        sig = link_signature(root, flavor)
        pending_before = bool(self._sess_pending)

        def work():
            res = import_link_sessions(root, flavor)
            res["running"] = bool(running_wow(root, flavor)) if (res["new"] or res["updated"] or pending_before) else False
            return res

        def done(res, err):
            self._sess_importing = False
            if err:
                log.error("Sitzungen einlesen: %s", err)
                if manual:
                    self.error(tr("Sitzungen konnten nicht eingelesen werden:\n{0}", err))
                return
            if flavor != self.flavor:
                return
            self._sess_sig = sig
            self.link_state = res.get("state") or empty_link_state()
            self._link_state_changed()
            changed = res["new"] + res["updated"]
            for s in changed:
                self._sess_pending[s["id"]] = s
            if res["errors"]:
                log.warning("Leeroylink: %s", "; ".join(res["errors"]))
            if changed:
                log.info("Sitzungen übernommen: %d neu, %d aktualisiert", len(res["new"]), len(res["updated"]))
                if self.current_page == "sessions":
                    self.refresh_sessions_page()
            if self._sess_pending and not res["running"]:
                self._announce_sessions()
            elif manual:
                if res["errors"] and not changed:
                    self.warn(tr("Die Daten von Leeroylink konnten nicht gelesen werden:\n{0}", "\n".join(res["errors"])))
                elif res["files"] == 0:
                    self.info(tr("Noch keine Daten von Leeroylink gefunden. WoW schreibt sie beim Ausloggen und bei /reload."))
                elif not changed:
                    self.info(tr("Keine neuen Sitzungen."))
                else:
                    self.info(tr("{0} übernommen. WoW läuft noch – die laufende Sitzung wird nach dem Ausloggen ergänzt.",
                                 plural(len(changed), "{0} Sitzung", "{0} Sitzungen")))

        self.run_quiet(work, done)

    def _watch_sessions(self):
        """Alle 10 s: Hat WoW die Datei neu geschrieben? Ist das Spiel inzwischen beendet?"""
        try:
            if self.root_path and self.flavor and not self._sess_importing:
                if link_signature(self.root_path, self.flavor) != self._sess_sig:
                    self.import_sessions()
                elif self._sess_pending:
                    root, flavor = self.root_path, self.flavor

                    def check(running, err):
                        if not err and not running and flavor == self.flavor:
                            self._announce_sessions()
                    self.run_quiet(lambda: running_wow(root, flavor), check)
        except (OSError, tk.TclError) as e:
            log.warning("Sitzungen beobachten: %s", e)
        finally:
            self.after(10000, self._watch_sessions)

    def _announce_sessions(self):
        """Hinweis nach Spielende: Die Sitzung ist gespeichert und zusammengefasst."""
        pending = sorted(self._sess_pending.values(), key=lambda s: s["start"])
        self._sess_pending.clear()
        if not pending:
            return
        last = pending[-1]
        if len(pending) == 1:
            text = tr("Sitzung gespeichert: {0}", session_headline(last))
        else:
            text = tr("{0} Sitzungen gespeichert. Zuletzt: {1}", len(pending), session_headline(last))
        self.notify(text, "ok", [(tr("Zusammenfassung ansehen"), lambda sid=last["id"]: self.show_session(sid))],
                    timeout=180000)
        self.set_status(text)
        if self.cfg.get("discord_auto") and self.cfg.get("discord_webhook") and not last.get("legacy"):
            self.send_session_discord(last, quiet=True)

    # Addon installieren ------------------------------------------------------------ #
    def update_link_state(self) -> bool:
        """Zeigt, ob Leeroylink installiert und aktuell ist. True = installiert, aber veraltet."""
        if getattr(self, "link_btn", None) is None or not self.link_btn.winfo_exists():
            return False
        bundled = link_version(bundled_link_dir())
        btn = ""
        outdated = False
        if not self.root_path or not self.flavor:
            text = tr("Wähle zuerst den WoW-Ordner – dann zeigt Leeroyzan hier deine Spielsitzungen.")
        else:
            inst = installed_link_dir(self.root_path, self.flavor)
            have = link_version(inst)
            newer = bool(bundled) and (not have or parse_version(bundled) > parse_version(have))
            old = old_link_dir(self.root_path, self.flavor)
            if old and bundled:
                outdated = True
                text = tr("Leeroyzan Link heißt jetzt Leeroylink. Beim Umstellen übernimmt Leeroyzan deine Daten aus "
                          "dem Spiel (Charaktere, Taschen und Bank, Rezepte, Fehlerlog, Sitzungen) und ersetzt das alte "
                          "Addon. WoW muss dafür geschlossen sein.")
                btn = tr("Auf Leeroylink {0} umstellen", bundled)
                inst = old
            elif not inst:
                text = tr("Leeroylink ist in dieser Spielversion nicht installiert. Das Addon zeichnet deine "
                          "Sitzungen im Spiel auf – ohne es bleibt diese Seite leer.")
                btn = tr("Leeroylink {0} installieren", bundled) if bundled else ""
            elif newer:
                outdated = True
                text = tr("Leeroylink {0} ist installiert. Version {1} liegt Leeroyzan bei und zeichnet die ganze "
                          "Sitzung auf (Quests, Beute, Kampf, Strecke, Berufe, Instanzen …).", have or "?", bundled)
                btn = tr("Auf {0} aktualisieren", bundled)
            else:
                text = tr("Leeroylink {0} ist installiert. Nach dem Ausloggen übernimmt Leeroyzan jede Sitzung "
                          "automatisch und fasst sie hier zusammen.", have or "?")
                times = []
                for f in link_savedvariables(self.root_path, self.flavor):
                    try:
                        times.append(f.stat().st_mtime)
                    except OSError:
                        pass
                if times:
                    text += " " + tr("Daten zuletzt geschrieben: {0}.",
                                     datetime.fromtimestamp(max(times)).strftime(tr("%d.%m.%Y  %H:%M")))
            if inst is not None and inst.parent == disabled_dir(self.root_path, self.flavor):
                text += " " + tr("Das Addon ist zurzeit deaktiviert – aktiviere es unter „Meine Addons“.")
        self.link_state_var.set(text)
        if btn:
            self.link_btn.configure(text=btn)
            self.link_btn.pack(side="right", padx=(12, 0), before=self.link_label)
        else:
            self.link_btn.pack_forget()
        self.after_idle(self._wrap_link_label)
        return outdated

    def install_link(self):
        migrate = bool(self.root_path and self.flavor and old_link_dir(self.root_path, self.flavor))
        if not self.ready(modifies=not migrate):
            return
        root, flavor = self.root_path, self.flavor
        if migrate:
            if running_wow(root, flavor):
                self.warn(tr("WoW läuft noch. Beende zuerst das Spiel – sonst gehen die Daten der laufenden Sitzung verloren."))
                return

            def migrated(res):
                log.info("Leeroyzan Link → Leeroylink %s (%s): Daten übernommen %s, übersprungen %s",
                         res["version"], flavor, res["copied"], res["skipped"])
                self.dismiss("link")
                self.refresh()
                self.update_link_state()
                self.import_sessions()
                self.notify(tr("Leeroyzan Link ist jetzt Leeroylink {0}. Deine Daten sind übernommen – beim nächsten "
                               "Start von WoW lädt das neue Addon.", res["version"]), "ok", timeout=30000)

            self.run_bg(tr("Stelle auf Leeroylink um"), lambda: migrate_link_addon(root, flavor), migrated)
            return

        def done(version):
            log.info("Leeroylink %s installiert (%s)", version, flavor)
            self.dismiss("link")
            self.refresh()
            self.update_link_state()
            self.notify(tr("Leeroylink {0} ist installiert. Starte WoW neu (oder logge dich neu ein), damit das "
                           "Addon geladen wird.", version), "ok", timeout=30000)

        self.run_bg(tr("Installiere Leeroylink"), lambda: install_link_addon(root, flavor), done)

    def _carry_over_old_link_data(self):
        """Leeroylink von Hand installiert und den alten Ordner gelöscht: die Daten von „Leeroyzan Link“
        trotzdem übernehmen – nur wenn es noch keine Leeroylink-Datei gibt und WoW geschlossen ist."""
        root, flavor = self.root_path, self.flavor
        if not root or not flavor or old_link_dir(root, flavor) or not installed_link_dir(root, flavor):
            return
        base = Path(root) / flavor / "WTF" / "Account"
        if not base.is_dir() or not any(not p.with_name(f"{LINK_ADDON}.lua").exists()
                                        for p in base.glob(f"*/SavedVariables/{OLD_LINK_ADDON}.lua")):
            return

        def work():
            return {"copied": [], "skipped": []} if running_wow(root, flavor) else migrate_link_savedvariables(root, flavor)

        def done(res, err):
            if err:
                log.warning("Daten von Leeroyzan Link: %s", err)
            elif res and res["copied"]:
                log.info("Daten von Leeroyzan Link für Leeroylink übernommen: %s", res["copied"])
                self.notify(tr("Die Daten von „Leeroyzan Link“ sind für Leeroylink übernommen (Charaktere, Rezepte, "
                               "Fehlerlog, Sitzungen)."), "ok", timeout=20000)
        self.run_quiet(work, done)

    def _sessions_flavor_changed(self):
        self._carry_over_old_link_data()
        self._sess_sig = None
        self._sess_pending.clear()
        self.link_state = empty_link_state()
        outdated = self.update_link_state()
        if outdated and not self._link_hint_shown and old_link_dir(self.root_path, self.flavor):
            self._link_hint_shown = True
            self.notify(tr("Leeroyzan Link heißt jetzt Leeroylink. Ein Klick stellt um und übernimmt deine Daten "
                           "(WoW muss dafür geschlossen sein)."),
                        "update", [(tr("Umstellen"), self.install_link),
                                   (tr("Leeroylink"), lambda: self.show_page("sessions"))], key="link")
        elif outdated and not self._link_hint_shown:
            self._link_hint_shown = True
            inst = installed_link_dir(self.root_path, self.flavor)
            self.notify(tr("Leeroylink {0} ist verfügbar (installiert: {1}). Die neue Version zeichnet ganze "
                           "Spielsitzungen für Leeroyzan auf.", link_version(bundled_link_dir()), link_version(inst) or "?"),
                        "update", [(tr("Aktualisieren"), self.install_link),
                                   (tr("Leeroylink"), lambda: self.show_page("sessions"))], key="link")
        if self.current_page == "sessions":
            self.refresh_sessions_page()
        self.import_sessions()

    # --- Sitzungen: Diagramme, Auswertung, Discord ---------------------------------------------- #
    SESS_VIEWS = [("report", "Bericht"), ("charts", "Diagramme"), ("analysis", "Auswertung")]

    def _sess_select_view(self, key: str):
        self.sess_view = key
        self.cfg["sess_view"] = key
        for k, b in self.sess_view_btns.items():
            b.configure(style="Primary.TButton" if k == key else "TButton")
        if key == "charts":
            self.sess_text_frame.pack_forget()
            self.sess_canvas.pack(fill="both", expand=True)
            self.after_idle(self.draw_session_charts)
        else:
            self.sess_canvas.pack_forget()
            self.sess_text_frame.pack(fill="both", expand=True)
            self._sess_show()

    def draw_session_charts(self):
        cv = getattr(self, "sess_canvas", None)
        if cv is None or not cv.winfo_exists() or not cv.winfo_ismapped():
            return
        t = THEME
        cv.delete("all")
        w, h = max(cv.winfo_width(), 300), max(cv.winfo_height(), 240)
        data = chart_series(self.sessions_shown)
        if not self.sessions_shown:
            cv.create_text(w // 2, h // 2, text=tr("Noch keine Sitzungen."), fill=t["text_dim"], font=self.fonts["base"])
            return
        pad = 10
        cw, ch = (w - 3 * pad) // 2, (h - 3 * pad) // 2
        boxes = [(pad, pad), (2 * pad + cw, pad), (pad, 2 * pad + ch), (2 * pad + cw, 2 * pad + ch)]
        self._bar_chart(cv, *boxes[0], cw, ch, tr("Erfahrung pro Stunde"), data["xp_h"], data["labels"],
                        lambda v: fmt_int(v))
        self._bar_chart(cv, *boxes[1], cw, ch, tr("Gold pro Sitzung"), data["gold"], data["labels"],
                        lambda v: fmt_money(v * 10000, sign=True))
        self._line_chart(cv, *boxes[2], cw, ch, tr("Stufe"), data["levels"])
        self._bar_chart(cv, *boxes[3], cw, ch, tr("Spielzeit pro Tag"), data["day_hours"], data["day_labels"],
                        lambda v: fmt_dur(v * 3600))

    def _chart_frame(self, cv, x, y, w, h, title, axis_labels=()):
        t = THEME
        cv.create_rectangle(x, y, x + w, y + h, outline=t["border_soft"], fill=t["row_a"])
        cv.create_text(x + 12, y + 14, text=title.upper(), anchor="w", fill=t["gold"], font=self.fonts["label"])
        f = tkfont.Font(font=self.fonts["small"])
        left = max([f.measure(a) for a in axis_labels] + [16]) + 18
        return x + left, y + 34, x + w - 14, y + h - 26     # Zeichenfläche

    def _x_labels(self, cv, x0, x1, y1, labels, slot):
        f = tkfont.Font(font=self.fonts["small"])
        widest = max((f.measure(l) for l in labels), default=30) + 10
        step = max(1, -(-len(labels) * widest // max(1, int(x1 - x0))))
        for i in range(0, len(labels), step):
            cv.create_text(x0 + slot * i + slot / 2, y1 + 12, text=labels[i], fill=THEME["text_faint"], font=self.fonts["small"])

    def _bar_chart(self, cv, x, y, w, h, title, values, labels, fmt):
        t = THEME
        hi0, lo0 = max(max(values, default=0), 0), min(min(values, default=0), 0)
        x0, y0, x1, y1 = self._chart_frame(cv, x, y, w, h, title, (fmt(hi0), fmt(lo0)) if lo0 < 0 else (fmt(hi0),))
        if not values or not any(values):
            cv.create_text((x0 + x1) // 2, (y0 + y1) // 2, text=tr("keine Daten"), fill=t["text_faint"], font=self.fonts["small"])
            return
        hi, lo = max(max(values), 0), min(min(values), 0)
        span = (hi - lo) or 1
        zero = y1 - (0 - lo) / span * (y1 - y0)
        n = len(values)
        slot = (x1 - x0) / n
        bw = max(2, min(26, slot * 0.7))
        cv.create_line(x0, zero, x1, zero, fill=t["line"])
        for v_lab, v in ((hi, hi), (lo, lo)) if lo < 0 else ((hi, hi),):
            yy = y1 - (v - lo) / span * (y1 - y0)
            cv.create_text(x0 - 6, yy, text=fmt(v_lab), anchor="e", fill=t["text_faint"], font=self.fonts["small"])
        for i, v in enumerate(values):
            cx = x0 + slot * i + slot / 2
            top = y1 - (v - lo) / span * (y1 - y0)
            color = t["warn"] if v < 0 else t["gold_deep"]
            cv.create_rectangle(cx - bw / 2, min(top, zero), cx + bw / 2, max(top, zero), fill=color, outline="")
        self._x_labels(cv, x0, x1, y1, labels, slot)
        avg = sum(values) / n
        cv.create_text(x + w - 12, y + 14, text=tr("Ø {0}", fmt(avg)), anchor="e", fill=t["text_dim"], font=self.fonts["small"])

    def _line_chart(self, cv, x, y, w, h, title, series: dict):
        t = THEME
        x0, y0, x1, y1 = self._chart_frame(cv, x, y, w, h, title, ("88",))
        pts = [p for s in series.values() for p in s]
        if not pts:
            cv.create_text((x0 + x1) // 2, (y0 + y1) // 2, text=tr("keine Daten"), fill=t["text_faint"], font=self.fonts["small"])
            return
        tmin, tmax = min(p[0] for p in pts), max(p[0] for p in pts)
        lmin, lmax = min(p[1] for p in pts), max(p[1] for p in pts)
        if lmax == lmin:
            lmin, lmax = lmin - 1, lmax + 1
        tspan = (tmax - tmin) or 1
        colors = [t["gold"], t["ok"], "#6fb7ff", "#d98cff", t["warn"], "#9fe0d0"]
        for v in (lmin, lmax):
            yy = y1 - (v - lmin) / (lmax - lmin) * (y1 - y0)
            cv.create_text(x0 - 6, yy, text=str(v), anchor="e", fill=t["text_faint"], font=self.fonts["small"])
            cv.create_line(x0, yy, x1, yy, fill=t["line"], dash=(2, 3))
        for i, (name, s) in enumerate(series.items()):
            color = colors[i % len(colors)]
            coords = []
            for ts, lvl in s:
                coords += [x0 + (ts - tmin) / tspan * (x1 - x0), y1 - (lvl - lmin) / (lmax - lmin) * (y1 - y0)]
            if len(coords) >= 4:
                cv.create_line(*coords, fill=color, width=2)
            for j in range(0, len(coords), 2):
                cv.create_oval(coords[j] - 2, coords[j + 1] - 2, coords[j] + 2, coords[j + 1] + 2, fill=color, outline="")
            cv.create_text(x0 + 4 + (i % 3) * 110, y1 + 12 + (i // 3) * 0, text=name, anchor="w", fill=color, font=self.fonts["small"])
        cv.create_text(x1, y1 + 12, text=datetime.fromtimestamp(tmax).strftime("%d.%m"), anchor="e",
                       fill=t["text_faint"], font=self.fonts["small"])

    def send_session_discord(self, s: dict | None = None, quiet: bool = False):
        url = (self.cfg.get("discord_webhook") or "").strip()
        s = s or self._sess_selected()
        if s is None:
            if not quiet:
                self.set_status(tr("Bitte eine einzelne Sitzung auswählen."))
            return
        if not url:
            if not quiet:
                self.notify(tr("Trage zuerst eine Discord-Webhook-Adresse in den Einstellungen ein."), "info",
                            [(tr("Einstellungen"), lambda: self.show_page("settings"))])
            return
        payload = discord_payload(s)

        def done(res, err):
            if err:
                log.warning("Discord: %s", err)
                if not quiet:
                    self.error(tr("Discord: {0}", err))
                else:
                    self.set_status(tr("Discord: {0}", err))
                return
            log.info("Sitzung an Discord gesendet: %s", s["id"])
            self.set_status(tr("Sitzung an Discord gesendet."))

        self.run_quiet(lambda: discord_send(url, payload), done)

    def _w_discord(self, parent):
        box = ttk.Frame(parent, style="Panel.TFrame")
        ttk.Label(box, text=tr("Webhook-Adresse eines Discord-Kanals (Kanal bearbeiten → Integrationen → Webhooks)."),
                  style="Panel.TLabel", wraplength=280, justify="left").pack(anchor="w")
        e = ttk.Entry(box, textvariable=self.discord_var)
        e.pack(fill="x", pady=(6, 0))
        e.bind("<FocusOut>", lambda ev: self._save_discord())
        e.bind("<Return>", lambda ev: self._save_discord())
        ttk.Checkbutton(box, text=tr("Nach jeder Sitzung automatisch senden"), variable=self.discord_auto_var,
                        style="Wrap.TCheckbutton", command=self._save_discord).pack(anchor="w", pady=(6, 0))
        ttk.Button(box, text=tr("Letzte Sitzung senden (Test)"), command=self._discord_test).pack(anchor="w", pady=(6, 0))
        return box

    def _save_discord(self):
        url = self.discord_var.get().strip()
        if url and not DISCORD_WEBHOOK_RE.match(url):
            self.set_status(tr("Das ist keine Discord-Webhook-Adresse."))
            return
        self.cfg["discord_webhook"] = url
        self.cfg["discord_auto"] = bool(self.discord_auto_var.get())
        save_config(self.cfg)

    def _discord_test(self):
        self._save_discord()
        latest = load_sessions(self.flavor)[:1] if self.flavor else []
        if not latest:
            self.info(tr("Noch keine Sitzung zum Senden."))
            return
        self.send_session_discord(latest[0])
