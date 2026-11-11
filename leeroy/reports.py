"""Sitzungen formatieren, zusammenfassen, auswerten und an Discord senden."""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime

from .constants import APP_VERSION, USER_AGENT
from .i18n import cur_lang, tr
from .lua import YARD_M
from .sessions import _int, _num, session_net_money


# ----- Formatieren --------------------------------------------------------- #
def fmt_int(n) -> str:
    v = _int(n)
    out = f"{abs(v):,}".replace(",", "." if cur_lang() == "de" else ",")
    return ("-" if v < 0 else "") + out


def fmt_money(copper, sign: bool = False) -> str:
    c = _int(copper)
    neg = c < 0
    c = abs(c)
    g, rest = divmod(c, 10000)
    sv, k = divmod(rest, 100)
    unit_c = "k" if cur_lang() == "de" else "c"
    parts = [f"{fmt_int(g)}g" if g else "", f"{sv}s" if sv else "", f"{k}{unit_c}" if k else ""]
    out = " ".join(p for p in parts if p) or f"0{unit_c}"
    if neg:
        return "−" + out
    return ("+" + out) if sign and c else out


def fmt_dur(sec) -> str:
    sec = max(0, _int(sec))
    h, rest = divmod(sec, 3600)
    m = rest // 60
    if h:
        return tr("{0} h {1:02d} min", h, m)
    if m:
        return tr("{0} min", m)
    return tr("{0} s", sec)


def fmt_dist(yards) -> str:
    m = _num(yards) * YARD_M
    if m < 1000:
        return f"{fmt_int(m)} m"
    v = f"{m / 1000:.1f}"
    return (v.replace(".", ",") if cur_lang() == "de" else v) + " km"


def plural(n, one: str, many: str) -> str:
    """tr(one) für genau 1, sonst tr(many, n) – one/many sind deutsche Texte mit {0}."""
    return tr(one, fmt_int(n)) if _int(n) == 1 else tr(many, fmt_int(n))


def fmt_pct(part, whole) -> str:
    whole = _num(whole)
    return f"{round(100 * _num(part) / whole)} %" if whole > 0 else "–"


WEEKDAYS = {"de": ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"],
            "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]}


def fmt_day(ts) -> str:
    d = datetime.fromtimestamp(_int(ts))
    return f"{WEEKDAYS.get(cur_lang(), WEEKDAYS['en'])[d.weekday()]}, {d.strftime(tr('%d.%m.%Y'))}"


def fmt_clock(ts) -> str:
    return datetime.fromtimestamp(_int(ts)).strftime("%H:%M")


QUALITY_NAMES = ["Schlecht", "Gewöhnlich", "Ungewöhnlich", "Selten", "Episch", "Legendär", "Artefakt"]
QUALITY_COLORS = ["#9d9d9d", "#f3f0e8", "#1eff00", "#3d9bff", "#c27cff", "#ff8000", "#e6cc80"]
MONEY_SOURCES = {"loot": "Beute", "quest": "Quests", "vendor": "Händler", "mail": "Post", "auction": "Auktionshaus",
                 "trainer": "Lehrer", "taxi": "Flugmeister", "trade": "Handel", "other": "Sonstiges"}
GATHER_KINDS = {"herb": "Kräuterkunde", "mining": "Bergbau", "skinning": "Kürschnerei", "fishing": "Angeln"}
INSTANCE_KINDS = {"party": "Dungeon", "raid": "Schlachtzug", "pvp": "Schlachtfeld", "arena": "Arena",
                  "scenario": "Szenario"}
ENV_KINDS = {"Falling": "Sturz", "Drowning": "Ertrinken", "Fatigue": "Erschöpfung", "Fire": "Feuer",
             "Lava": "Lava", "Slime": "Schleim"}
CLASS_NAMES = {"WARRIOR": "Krieger", "PALADIN": "Paladin", "HUNTER": "Jäger", "ROGUE": "Schurke", "PRIEST": "Priester",
               "SHAMAN": "Schamane", "MAGE": "Magier", "WARLOCK": "Hexenmeister", "DRUID": "Druide",
               "DEATHKNIGHT": "Todesritter", "MONK": "Mönch", "DEMONHUNTER": "Dämonenjäger", "EVOKER": "Rufer"}


def skill_group(header: str) -> str:
    """prof | weapon | other – aus der (deutschen oder englischen) Überschrift im Fertigkeitenfenster."""
    h = header.lower()
    if h == "prof" or "beruf" in h or "profession" in h or "sekund" in h or "secondary" in h or "trade" in h:
        return "prof"
    if "waffe" in h or "weapon" in h:
        return "weapon"
    return "other"


PROFESSION_NAMES = {
    "alchemie", "alchemy", "schmiedekunst", "blacksmithing", "verzauberkunst", "enchanting", "ingenieurskunst",
    "engineering", "kräuterkunde", "herbalism", "lederverarbeitung", "leatherworking", "bergbau", "mining",
    "kürschnerei", "skinning", "schneiderei", "tailoring", "kochkunst", "cooking", "erste hilfe", "first aid",
    "angeln", "fishing", "juwelierskunst", "jewelcrafting", "inschriftenkunde", "inscription"}


def skill_group_by_name(name: str) -> str:
    return "prof" if name.strip().lower() in PROFESSION_NAMES else "other"


def _top(d: dict, n: int, key=lambda v: v):
    return sorted(d.items(), key=lambda kv: key(kv[1]), reverse=True)[:n]


def session_title(s: dict) -> str:
    return tr("{0} · {1} – {2}", fmt_day(s["start"]), fmt_clock(s["start"]), fmt_clock(s["stop"]))


def session_headline(s: dict) -> str:
    """Kurzfassung für Hinweise: Charakter · Dauer · XP · Gold · Kills."""
    parts = [s.get("name") or s["char"], fmt_dur(s["played"])]
    if s["xp"]["total"]:
        parts.append(tr("{0} XP", fmt_int(s["xp"]["total"])))
    parts.append(fmt_money(session_net_money(s), sign=True))
    if s["kills"]["n"]:
        parts.append(plural(s["kills"]["n"], "{0} Kill", "{0} Kills"))
    if s["quests"]["done"]:
        parts.append(plural(len(s["quests"]["done"]), "{0} Quest", "{0} Quests"))
    return " · ".join(parts)


def _summary_sentence(s: dict) -> str:
    """Die Sitzung in zwei, drei Sätzen."""
    x, k, mv = s["xp"], s["kills"], s["move"]
    progress = []
    if x["total"]:
        progress.append(tr("{0} Erfahrung gesammelt", fmt_int(x["total"])))
    net = session_net_money(s)
    if net > 0:
        progress.append(tr("{0} verdient", fmt_money(net)))
    elif net < 0:
        progress.append(tr("{0} ausgegeben", fmt_money(-net)))
    if s["quests"]["done"]:
        n = len(s["quests"]["done"])
        progress.append(tr("1 Quest abgeschlossen") if n == 1 else tr("{0} Quests abgeschlossen", n))
    if progress:
        out = [tr("In {0} Spielzeit hast du {1}.", fmt_dur(s["played"]), _join_and(progress))]
    else:
        out = [tr("Du warst {0} im Spiel.", fmt_dur(s["played"]))]

    action = []
    if k["n"]:
        special = []
        if k["elite"] + k["rareelite"]:
            special.append(tr("{0} Elite", k["elite"] + k["rareelite"]))
        if k["rare"] + k["rareelite"]:
            special.append(tr("{0} seltene", k["rare"] + k["rareelite"]))
        bosses = max(k["boss"], len(s["bosses"]))
        if bosses:
            special.append(tr("1 Boss") if bosses == 1 else tr("{0} Bosse", bosses))
        action.append(tr("{0} Gegner besiegt", fmt_int(k["n"])) +
                      (tr(" (darunter {0})", _join_and(special)) if special else ""))
    walked = mv["foot"] + mv["ghost"]
    if walked * YARD_M >= 50:
        action.append(tr("{0} zu Fuß zurückgelegt (≈ {1} Schritte)", fmt_dist(walked), fmt_int(mv["steps"])))
    if s["explore"]["found"]:
        n = len(s["explore"]["found"])
        action.append(tr("1 Gebiet entdeckt") if n == 1 else tr("{0} Gebiete entdeckt", n))
    if s["runs"]:
        n = len(s["runs"])
        action.append(tr("1 Instanz besucht") if n == 1 else tr("{0} Instanzen besucht", n))
    if action:
        out.append(tr("Du hast {0}.", _join_and(action)))

    lv = s["level"]
    if lv["to"] > lv["from"] > 0:
        out.append(tr("Du bist von Stufe {0} auf {1} aufgestiegen.", lv["from"], lv["to"]))
    if s["deathN"] == 1:
        out.append(tr("Du bist einmal gestorben."))
    elif s["deathN"] > 1:
        out.append(tr("Du bist {0}-mal gestorben.", s["deathN"]))
    return " ".join(out)


def _join_and(parts: list[str]) -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + tr(" und ") + parts[-1]


def session_report(s: dict) -> list[tuple[str, str]]:
    """Ausführliche Zusammenfassung einer Sitzung als (Text, Tag)-Teile für ein Text-Feld."""
    P: list[tuple[str, str]] = []
    add = lambda text, tag="": P.append((text, tag))   # noqa: E731

    def head(text):
        add("\n" + text + "\n", "head")

    def line(text, tag=""):
        add(text + "\n", tag)

    def pairs(items, tag=""):
        items = [i for i in items if i]
        if items:
            line(" · ".join(items), tag)

    cls = tr(CLASS_NAMES.get(s["class"], s["class"].title())) if s["class"] else ""
    add(session_title(s) + "\n", "title")
    who = s["name"] or s["char"]
    lv = s["level"]
    level = (tr("Stufe {0}", lv["to"]) if lv["from"] in (0, lv["to"]) else tr("Stufe {0} → {1}", lv["from"], lv["to"]))
    pairs([who, cls, level, s["realm"]])
    if s["legacy"]:
        line(tr("Aufgezeichnet mit Leeroylink 0.1 – nur Dauer, Erfahrung, Gold und Kills."), "dim")
    if s["resumes"]:
        line(tr("Fortgesetzt nach {0}× /reload oder kurzem Ausloggen.", s["resumes"]), "dim")

    head(tr("Zusammenfassung"))
    line(_summary_sentence(s))
    hl = _highlights(s)
    if hl:
        for h in hl:
            add("★ ", "hl")
            line(h)

    head(tr("Zeit"))
    t = s["time"]
    pairs([tr("Spielzeit {0}", fmt_dur(s["played"])), tr("im Kampf {0}", fmt_dur(t["combat"])) if t["combat"] else "",
           tr("AFK {0}", fmt_dur(t["afk"])) if t["afk"] else "", tr("ausgeruht {0}", fmt_dur(t["rest"])) if t["rest"] else "",
           tr("in Instanzen {0}", fmt_dur(t["instance"])) if t["instance"] else "",
           tr("Flugroute {0}", fmt_dur(t["taxi"])) if t["taxi"] else "", tr("tot {0}", fmt_dur(t["dead"])) if t["dead"] else ""])
    if s["playedTotal"]:
        line(tr("Gesamtspielzeit des Charakters: {0}", fmt_dur(s["playedTotal"])), "dim")

    x = s["xp"]
    if x["total"] or lv["ups"]:
        head(tr("Erfahrung"))
        per_h = x["total"] * 3600 / s["played"] if s["played"] >= 300 else 0
        pairs([tr("{0} XP", fmt_int(x["total"])), tr("{0} pro Stunde", fmt_int(per_h)) if per_h else ""])
        pairs([tr("Kills {0} ({1})", fmt_int(x["kills"]), fmt_pct(x["kills"], x["total"])) if x["kills"] else "",
               tr("Quests {0} ({1})", fmt_int(x["quests"]), fmt_pct(x["quests"], x["total"])) if x["quests"] else "",
               tr("Entdecken {0} ({1})", fmt_int(x["explore"]), fmt_pct(x["explore"], x["total"])) if x["explore"] else "",
               tr("Sonstiges {0}", fmt_int(x["other"])) if x["other"] else ""], "dim")
        if lv["ups"]:
            line(tr("Stufenaufstiege: {0}", ", ".join(tr("{0} um {1}", u["l"], fmt_clock(u["t"])) for u in lv["ups"])))

    m = s["money"]
    net = session_net_money(s)
    head(tr("Gold"))
    if s["legacy"]:
        line(tr("netto {0}", fmt_money(net, sign=True)))
    else:
        pairs([tr("{0} → {1}", fmt_money(m["start"]), fmt_money(m["stop"])), tr("netto {0}", fmt_money(net, sign=True))])
        if m["gain"]:
            line(tr("Einnahmen {0}: {1}", fmt_money(m["income"]), ", ".join(
                f"{tr(MONEY_SOURCES.get(k, k))} {fmt_money(v)}" for k, v in _top(m["gain"], 9))), "dim")
        if m["spend"]:
            line(tr("Ausgaben {0}: {1}", fmt_money(m["spent"]), ", ".join(
                f"{tr(MONEY_SOURCES.get(k, k))} {fmt_money(v)}" for k, v in _top(m["spend"], 9))), "dim")

    q = s["quests"]
    if q["done"] or q["accepted"] or q["abandoned"]:
        head(tr("Quests"))
        pairs([tr("{0} abgeschlossen", len(q["done"])), tr("{0} angenommen", len(q["accepted"])),
               tr("{0} abgebrochen", len(q["abandoned"])) if q["abandoned"] else ""])
        for e in q["done"]:
            add("✓ ", "ok")
            reward = [tr("{0} XP", fmt_int(e["xp"])) if e["xp"] else "", fmt_money(e["money"]) if e["money"] else ""]
            reward = [r for r in reward if r]
            line((e["title"] or tr("Quest {0}", e["id"])) + (f"  ({', '.join(reward)})" if reward else ""))
        open_q = [e for e in q["accepted"] if e["id"] not in {d["id"] for d in q["done"]}]
        if open_q:
            line(tr("Neu angenommen: {0}", ", ".join(e["title"] or tr("Quest {0}", e["id"]) for e in open_q)), "dim")
        if q["abandoned"]:
            line(tr("Abgebrochen: {0}", ", ".join(e["title"] for e in q["abandoned"])), "dim")

    lo = s["loot"]
    if lo["n"]:
        head(tr("Beute"))
        pairs([plural(lo["n"], "{0} Gegenstand", "{0} Gegenstände"), tr("Händlerwert {0}", fmt_money(lo["value"])) if lo["value"] else ""])
        by_q = []
        for qi in range(len(QUALITY_NAMES)):
            n = lo["q"].get(str(qi))
            if n:
                by_q.append(f"{fmt_int(n)} {tr(QUALITY_NAMES[qi]).lower()}")
        if by_q:
            line(", ".join(by_q), "dim")
        if lo["notable"]:
            add(tr("Besondere Beute: "))
            for i, e in enumerate(lo["notable"][-12:]):
                if i:
                    add(", ")
                add(f"[{e['name']}]", f"q{min(max(e['q'], 0), 6)}")
                if e["n"] > 1:
                    add(f" ×{e['n']}")
            add("\n")
        common = _top(lo["items"], 8, key=lambda e: e["n"])
        if common:
            add(tr("Am häufigsten: "), "dim")
            for i, (_, e) in enumerate(common):
                if i:
                    add(", ", "dim")
                add(e["name"], f"q{min(max(e['q'], 0), 6)}")
                add(f" ×{fmt_int(e['n'])}", "dim")
            add("\n")

    cb = s["combat"]
    k = s["kills"]
    if k["n"] or cb["dmg"] or cb["taken"] or s["deathN"]:
        head(tr("Gegner"))
        bosses = max(k["boss"], len(s["bosses"]))
        pairs([tr("{0} besiegt", fmt_int(k["n"])), tr("{0} normal", fmt_int(k["normal"])),
               tr("{0} Elite", fmt_int(k["elite"] + k["rareelite"])) if k["elite"] + k["rareelite"] else "",
               tr("{0} selten", fmt_int(k["rare"] + k["rareelite"])) if k["rare"] + k["rareelite"] else "",
               plural(bosses, "{0} Boss", "{0} Bosse") if bosses else "",
               tr("{0} Spieler", k["players"]) if k["players"] else "",
               tr("{0} Todesstöße", fmt_int(k["blows"])) if k["blows"] else ""])
        if s["bosses"]:
            line(tr("Bosse: {0}", ", ".join(b["name"] for b in s["bosses"])))
        mobs = _top(k["mobs"], 10, key=lambda e: e["n"])
        if mobs:
            labels = {"elite": tr("Elite"), "rare": tr("selten"), "rareelite": tr("seltene Elite"), "boss": tr("Boss")}
            line(tr("Am häufigsten: {0}", ", ".join(
                f"{e['name']} ×{e['n']}" + (f" ({labels[e['c']]})" if e["c"] in labels else "") for _, e in mobs)), "dim")

        if not cb["err"] and (cb["dmg"] or cb["taken"] or cb["heal"]):
            head(tr("Kampf"))
            dps = cb["dmg"] / s["time"]["combat"] if s["time"]["combat"] >= 10 else 0
            pairs([tr("Schaden {0}", fmt_int(cb["dmg"])) + (tr(" (davon Begleiter {0})", fmt_int(cb["dmgPet"])) if cb["dmgPet"] else ""),
                   tr("{0} DPS im Kampf", fmt_int(dps)) if dps else "",
                   tr("Krit-Rate {0}", fmt_pct(cb["crits"], cb["hits"])) if cb["hits"] else ""])
            if cb["maxHit"]:
                line(tr("Größter Treffer: {0} mit {1}{2}", fmt_int(cb["maxHit"]), cb["maxHitSpell"] or "?",
                        tr(" (kritisch)") if cb["maxHitCrit"] else ""), "dim")
            if cb["heal"] or cb["over"]:
                pairs([tr("Heilung {0}", fmt_int(cb["heal"])), tr("Überheilung {0}", fmt_int(cb["over"])) if cb["over"] else "",
                       tr("größte Heilung {0} ({1})", fmt_int(cb["maxHeal"]), cb["maxHealSpell"]) if cb["maxHeal"] else ""])
            pairs([tr("erlittener Schaden {0}", fmt_int(cb["taken"])), tr("erhaltene Heilung {0}", fmt_int(cb["healTaken"])) if cb["healTaken"] else "",
                   tr("härtester Treffer {0} von {1}", fmt_int(cb["maxTaken"]), cb["maxTakenBy"] or tr(ENV_KINDS.get(cb["maxTakenSpell"], cb["maxTakenSpell"]) or "?"))
                   if cb["maxTaken"] else ""])
            if cb["env"]:
                line(tr("Umgebung: {0}", ", ".join(f"{tr(ENV_KINDS.get(kk, kk))} {fmt_int(v)}" for kk, v in _top(cb["env"], 6))), "dim")
            sw, st = cb["swings"], cb["swingsTaken"]
            if sw["n"]:
                pairs([tr("{0} Hiebe", fmt_int(sw["n"])), tr("{0} Treffer", fmt_int(sw["hit"])), tr("{0} kritisch", fmt_int(sw["crit"])),
                       tr("{0} verfehlt", fmt_int(sw["miss"])) if sw["miss"] else "", tr("{0} ausgewichen", fmt_int(sw["dodge"])) if sw["dodge"] else "",
                       tr("{0} pariert", fmt_int(sw["parry"])) if sw["parry"] else "", tr("{0} geblockt", fmt_int(sw["block"])) if sw["block"] else "",
                       tr("{0} gestreift", fmt_int(sw["glance"])) if sw["glance"] else ""])
            if st["n"]:
                avoided = st["miss"] + st["dodge"] + st["parry"]
                pairs([tr("{0} Hiebe eingesteckt", fmt_int(st["n"])), tr("{0} abgewehrt ({1})", fmt_int(avoided), fmt_pct(avoided, st["n"])) if avoided else "",
                       tr("{0} kritisch", fmt_int(st["crit"])) if st["crit"] else ""], )
            top_dmg = _top(cb["spells"], 6, key=lambda v: v[0])
            if top_dmg:
                line(tr("Schaden nach Fähigkeit: {0}", ", ".join(
                    f"{name} {fmt_int(v[0])} ({fmt_pct(v[0], cb['dmg'])})" for name, v in top_dmg)), "dim")
            if cb["castN"]:
                line(plural(cb["castN"], "1 Zauber gewirkt", "{0} Zauber gewirkt") + ": " + ", ".join(
                    f"{name} ×{fmt_int(n)}" for name, n in _top(cb["casts"], 8)), "dim")
            if cb["buffN"]:
                line(plural(cb["buffN"], "{0} Buff erhalten", "{0} Buffs erhalten") + ": " + ", ".join(
                    f"{name} ×{fmt_int(n)}" for name, n in _top(cb["buffs"], 8)), "dim")
            if cb["debuffN"]:
                line(plural(cb["debuffN"], "{0} Schwächung erlitten", "{0} Schwächungen erlitten") + ": " + ", ".join(
                    f"{name} ×{fmt_int(n)}" for name, n in _top(cb["debuffs"], 6)), "dim")
            pairs([tr("{0} Buffs auf andere", fmt_int(cb["buffsGiven"])) if cb["buffsGiven"] else "",
                   tr("{0} Unterbrechungen", cb["interrupts"]) if cb["interrupts"] else "",
                   tr("{0} Bannungen", cb["dispels"]) if cb["dispels"] else ""], )
        elif cb["err"]:
            line(tr("Kampfdaten waren im Spiel nicht verfügbar ({0}).", cb["err"]), "dim")
        if s["deathN"]:
            line(tr("Tode: {0}", s["deathN"]), "warn")
            for d in s["deaths"]:
                where = d["inst"] or d["zone"] or "?"
                if d["sub"] and not d["inst"]:
                    where += f" ({d['sub']})"
                cause = d["by"] or tr(ENV_KINDS.get(d["spell"], d["spell"] or "")) or tr("unbekannt")
                if d["by"] and d["spell"]:
                    cause += f" – {d['spell']}"
                line(tr("✝ {0}  {1}, durch {2}", fmt_clock(d["t"]), where, cause), "dim")

    mv = s["move"]
    if any(mv[kk] for kk in ("foot", "mount", "swim", "taxi", "ghost", "jumps")):
        head(tr("Unterwegs"))
        walked = mv["foot"] + mv["ghost"]
        pairs([tr("{0} zu Fuß", fmt_dist(walked)) + (tr(" (≈ {0} Schritte)", fmt_int(mv["steps"])) if mv["steps"] else "") if walked else "",
               tr("{0} beritten", fmt_dist(mv["mount"])) if mv["mount"] else "",
               tr("{0} geschwommen", fmt_dist(mv["swim"])) if mv["swim"] else "",
               tr("{0} Flugroute", fmt_dist(mv["taxi"])) if mv["taxi"] else "",
               tr("{0} Sprünge", fmt_int(mv["jumps"])) if mv["jumps"] else ""])
        if mv["ghost"]:
            line(tr("Davon als Geist: {0}", fmt_dist(mv["ghost"])), "dim")

    pr = s["prof"]
    profs = [(n, e) for n, e in pr["skills"].items()
             if skill_group(e["h"]) == "prof" or skill_group_by_name(n) == "prof"]
    raised = [(n, e) for n, e in pr["skills"].items() if e["b"] > e["a"]]
    if profs or raised or pr["crafted"] or pr["gathered"] or pr["learned"]:
        head(tr("Berufe und Fertigkeiten"))
        for n, e in sorted(raised, key=lambda kv: kv[1]["b"] - kv[1]["a"], reverse=True):
            add(f"{n} {e['a']} → {e['b']}", "")
            add(f"  (+{e['b'] - e['a']}{'/' + str(e['m']) if e['m'] else ''})\n", "ok")
        rest = [f"{n} {e['b']}/{e['m']}" for n, e in sorted(profs) if e["b"] <= e["a"]]
        if rest:
            line(tr("Unverändert: {0}", ", ".join(rest)), "dim")
        if pr["crafted"]:
            line(tr("Hergestellt: {0}", ", ".join(f"{e['name']} ×{fmt_int(e['n'])}" for _, e in _top(pr["crafted"], 10, key=lambda e: e["n"]))))
        for kind, items in pr["gathered"].items():
            line(tr("{0}: {1}", tr(GATHER_KINDS.get(kind, kind)),
                    ", ".join(f"{e['name']} ×{fmt_int(e['n'])}" for _, e in _top(items, 10, key=lambda e: e["n"]))))
        if pr["learned"]:
            line(tr("Neue Rezepte: {0}", ", ".join(pr["learned"])), "dim")
    if s["spellsLearned"]:
        line(tr("Neue Zauber und Fähigkeiten: {0}", ", ".join(s["spellsLearned"])), "dim")

    ex = s["explore"]
    if ex["found"] or ex["zones"]:
        head(tr("Entdeckt und bereist"))
        if ex["found"]:
            xp_found = sum(e["xp"] for e in ex["found"])
            line(plural(len(ex["found"]), "{0} neues Gebiet", "{0} neue Gebiete") + (tr(" (+{0} XP)", fmt_int(xp_found)) if xp_found else "") + ": "
                 + ", ".join(e["name"] for e in ex["found"]))
        zones = _top(ex["zones"], 10, key=lambda z: z["sec"])
        if zones:
            line(tr("Zonen: {0}", ", ".join(f"{name} {fmt_dur(z['sec'])}" for name, z in zones if z["sec"] >= 30) or "–"), "dim")

    if s["runs"]:
        head(tr("Dungeons und Schlachtzüge"))
        for r in s["runs"]:
            kind = tr(INSTANCE_KINDS.get(r["kind"], r["kind"] or "Instanz"))
            dur = fmt_dur((r["stop"] or s["stop"]) - r["start"]) if r["start"] else ""
            details = [kind + (f", {r['diff']}" if r["diff"] else "") + (tr(", {0} Spieler", r["group"]) if r["group"] > 1 else ""),
                       f"{fmt_clock(r['start'])}–{fmt_clock(r['stop'] or s['stop'])}" if r["start"] else "", dur]
            add(r["name"] or "?", "hl")
            line("  " + " · ".join(d for d in details if d), "dim")
            pairs([plural(r["kills"], "{0} Kill", "{0} Kills"), tr("Bosse: {0}", ", ".join(r["bosses"])) if r["bosses"] else "",
                   plural(r["wipes"], "{0} Wipe", "{0} Wipes") if r["wipes"] else "",
                   plural(r["deaths"], "{0} Tod", "{0} Tode") if r["deaths"] else "",
                   tr("{0} XP", fmt_int(r["xp"])) if r["xp"] else "",
                   plural(r["loot"], "{0} Gegenstand", "{0} Gegenstände") if r["loot"] else ""])

    if s["rep"]:
        head(tr("Ruf"))
        line(", ".join(f"{name} {'+' if v > 0 else ''}{fmt_int(v)}" for name, v in _top(s["rep"], 12, key=abs)))
    return P


def _highlights(s: dict) -> list[str]:
    out = []
    cb, k, lo = s["combat"], s["kills"], s["loot"]
    if lo["notable"]:
        best = max(lo["notable"], key=lambda e: (e["q"], e["t"]))
        if best["q"] >= 3:
            out.append(tr("Beste Beute: [{0}] ({1})", best["name"], tr(QUALITY_NAMES[min(best["q"], 6)])))
    if s["bosses"]:
        out.append(tr("Boss besiegt: {0}", ", ".join(dict.fromkeys(b["name"] for b in s["bosses"]))))
    if s["level"]["ups"]:
        out.append(tr("Stufe {0} erreicht", s["level"]["ups"][-1]["l"]))
    if cb["maxHit"] >= 100:
        out.append(tr("Größter Treffer {0} ({1})", fmt_int(cb["maxHit"]), cb["maxHitSpell"] or "?"))
    if k["mobs"]:
        name, e = max(k["mobs"].items(), key=lambda kv: kv[1]["n"])
        if e["n"] >= 5:
            out.append(tr("Lieblingsgegner: {0} ×{1}", e["name"], e["n"]))
    return out[:4]


# ----- Mehrere Sitzungen zusammenfassen ----------------------------------- #
def aggregate_sessions(sessions: list[dict]) -> dict:
    tot = {"n": len(sessions), "played": 0, "xp": 0, "net": 0, "income": 0, "spent": 0, "quests": 0, "kills": 0,
           "elite": 0, "rare": 0, "boss": 0, "blows": 0, "deaths": 0, "dmg": 0, "heal": 0, "taken": 0, "loot": 0,
           "value": 0, "walked": 0, "mount": 0, "swim": 0, "taxi": 0, "steps": 0, "jumps": 0, "found": 0, "runs": 0,
           "levels": 0, "skillups": 0, "combat": 0, "afk": 0, "first": 0, "last": 0,
           "chars": {}, "mobs": {}, "zones": {}, "items": {}, "bosses": {}, "days": {}}
    for s in sessions:
        k, cb, mv, lo = s["kills"], s["combat"], s["move"], s["loot"]
        tot["played"] += s["played"]
        tot["xp"] += s["xp"]["total"]
        net = session_net_money(s)
        tot["net"] += net
        tot["income"] += s["money"]["income"]
        tot["spent"] += s["money"]["spent"]
        tot["quests"] += len(s["quests"]["done"])
        tot["kills"] += k["n"]
        tot["elite"] += k["elite"] + k["rareelite"]
        tot["rare"] += k["rare"] + k["rareelite"]
        tot["boss"] += max(k["boss"], len(s["bosses"]))
        tot["blows"] += k["blows"]
        tot["deaths"] += s["deathN"]
        tot["dmg"] += cb["dmg"]
        tot["heal"] += cb["heal"]
        tot["taken"] += cb["taken"]
        tot["loot"] += lo["n"]
        tot["value"] += lo["value"]
        tot["walked"] += mv["foot"] + mv["ghost"]
        tot["mount"] += mv["mount"]
        tot["swim"] += mv["swim"]
        tot["taxi"] += mv["taxi"]
        tot["steps"] += mv["steps"]
        tot["jumps"] += mv["jumps"]
        tot["found"] += len(s["explore"]["found"])
        tot["runs"] += len(s["runs"])
        tot["levels"] += max(0, s["level"]["to"] - s["level"]["from"]) if s["level"]["from"] else 0
        tot["skillups"] += sum(max(0, e["b"] - e["a"]) for e in s["prof"]["skills"].values())
        tot["combat"] += s["time"]["combat"]
        tot["afk"] += s["time"]["afk"]
        tot["first"] = min(tot["first"] or s["start"], s["start"])
        tot["last"] = max(tot["last"], s["stop"])
        c = tot["chars"].setdefault(s["char"], {"n": 0, "played": 0, "xp": 0, "net": 0, "kills": 0, "level": 0,
                                                "class": s["class"], "name": s["name"] or s["char"]})
        c["n"] += 1
        c["played"] += s["played"]
        c["xp"] += s["xp"]["total"]
        c["net"] += net
        c["kills"] += k["n"]
        c["level"] = max(c["level"], s["level"]["to"])
        for key, e in k["mobs"].items():
            m = tot["mobs"].setdefault(e["name"], 0)
            tot["mobs"][e["name"]] = m + e["n"]
        for name, z in s["explore"]["zones"].items():
            tot["zones"][name] = tot["zones"].get(name, 0) + z["sec"]
        for key, e in lo["items"].items():
            it = tot["items"].setdefault(key, {"name": e["name"], "q": e["q"], "n": 0})
            it["n"] += e["n"]
        for b in s["bosses"]:
            tot["bosses"][b["name"]] = tot["bosses"].get(b["name"], 0) + 1
        day = datetime.fromtimestamp(s["start"]).strftime("%Y-%m-%d")
        tot["days"][day] = tot["days"].get(day, 0) + s["played"]
    return tot


def overview_report(sessions: list[dict], label: str) -> list[tuple[str, str]]:
    P: list[tuple[str, str]] = []
    add = lambda text, tag="": P.append((text, tag))   # noqa: E731

    def head(text):
        add("\n" + text + "\n", "head")

    def line(text, tag=""):
        add(text + "\n", tag)

    def pairs(items, tag=""):
        items = [i for i in items if i]
        if items:
            line(" · ".join(items), tag)

    a = aggregate_sessions(sessions)
    add(tr("Gesamt – {0}", label) + "\n", "title")
    if not sessions:
        line(tr("Noch keine Sitzungen."), "dim")
        return P
    pairs([plural(a["n"], "{0} Sitzung", "{0} Sitzungen"), tr("{0} bis {1}", datetime.fromtimestamp(a["first"]).strftime(tr("%d.%m.%Y")),
                                              datetime.fromtimestamp(a["last"]).strftime(tr("%d.%m.%Y"))),
           plural(len(a["days"]), "{0} Spieltag", "{0} Spieltage")], "dim")

    head(tr("Zeit"))
    avg = a["played"] / a["n"]
    pairs([tr("Spielzeit {0}", fmt_dur(a["played"])), tr("Ø {0} pro Sitzung", fmt_dur(avg)),
           tr("im Kampf {0}", fmt_dur(a["combat"])) if a["combat"] else "", tr("AFK {0}", fmt_dur(a["afk"])) if a["afk"] else ""])
    if a["days"]:
        best_day, sec = max(a["days"].items(), key=lambda kv: kv[1])
        line(tr("Längster Spieltag: {0} mit {1}", fmt_day(datetime.strptime(best_day, "%Y-%m-%d").timestamp()), fmt_dur(sec)), "dim")

    head(tr("Fortschritt"))
    hours = a["played"] / 3600 if a["played"] >= 600 else 0
    pairs([tr("{0} XP", fmt_int(a["xp"])) + (tr(" ({0}/h)", fmt_int(a["xp"] / hours)) if hours else ""),
           plural(a["levels"], "{0} Stufe", "{0} Stufen") if a["levels"] else "",
           plural(a["quests"], "{0} Quest", "{0} Quests"),
           tr("{0} Fertigkeitspunkte", fmt_int(a["skillups"])) if a["skillups"] else "",
           plural(a["found"], "{0} neues Gebiet", "{0} neue Gebiete") if a["found"] else ""])
    pairs([tr("Gold netto {0}", fmt_money(a["net"], sign=True)) + (tr(" ({0}/h)", fmt_money(a["net"] / hours)) if hours and a["net"] > 0 else ""),
           tr("eingenommen {0}", fmt_money(a["income"])) if a["income"] else "", tr("ausgegeben {0}", fmt_money(a["spent"])) if a["spent"] else ""])

    head(tr("Kampf"))
    pairs([tr("{0} besiegt", fmt_int(a["kills"])), tr("{0} Elite", fmt_int(a["elite"])) if a["elite"] else "",
           tr("{0} selten", a["rare"]) if a["rare"] else "",
           plural(a["boss"], "{0} Boss", "{0} Bosse") if a["boss"] else "",
           plural(a["deaths"], "{0} Tod", "{0} Tode")])
    pairs([tr("Schaden {0}", fmt_int(a["dmg"])) if a["dmg"] else "", tr("Heilung {0}", fmt_int(a["heal"])) if a["heal"] else "",
           tr("erlitten {0}", fmt_int(a["taken"])) if a["taken"] else "",
           plural(a["runs"], "{0} Instanz", "{0} Instanzen") if a["runs"] else ""], "dim")
    if a["mobs"]:
        line(tr("Am häufigsten: {0}", ", ".join(f"{n} ×{fmt_int(c)}" for n, c in _top(a["mobs"], 8))), "dim")
    if a["bosses"]:
        line(tr("Bosse: {0}", ", ".join(f"{n} ×{c}" if c > 1 else n for n, c in _top(a["bosses"], 12))), "dim")

    head(tr("Beute und Wege"))
    pairs([tr("{0} Gegenstände", fmt_int(a["loot"])), tr("Händlerwert {0}", fmt_money(a["value"])) if a["value"] else ""])
    pairs([tr("{0} zu Fuß (≈ {1} Schritte)", fmt_dist(a["walked"]), fmt_int(a["steps"])) if a["walked"] else "",
           tr("{0} beritten", fmt_dist(a["mount"])) if a["mount"] else "", tr("{0} geschwommen", fmt_dist(a["swim"])) if a["swim"] else "",
           tr("{0} Flugroute", fmt_dist(a["taxi"])) if a["taxi"] else "", tr("{0} Sprünge", fmt_int(a["jumps"])) if a["jumps"] else ""])
    if a["zones"]:
        line(tr("Zonen: {0}", ", ".join(f"{n} {fmt_dur(sec)}" for n, sec in _top(a["zones"], 8))), "dim")

    if len(a["chars"]) > 1 or sessions:
        head(tr("Charaktere"))
        for char, c in sorted(a["chars"].items(), key=lambda kv: kv[1]["played"], reverse=True):
            cls = tr(CLASS_NAMES.get(c["class"], c["class"].title())) if c["class"] else ""
            add(c["name"], "hl")
            line("  " + " · ".join(x for x in (cls, tr("Stufe {0}", c["level"]) if c["level"] else "",
                                               plural(c["n"], "{0} Sitzung", "{0} Sitzungen"), fmt_dur(c["played"]),
                                               tr("{0} XP", fmt_int(c["xp"])), fmt_money(c["net"], sign=True),
                                               tr("{0} Kills", fmt_int(c["kills"]))) if x), "dim")
    return P


def report_plain_text(parts: list[tuple[str, str]]) -> str:
    return "".join(t for t, _ in parts).strip() + "\n"


# ----- Sitzungen auswerten: Prognose, Rekorde, Tode, Farmgebiete, Dungeons ------------- #
# Erfahrung pro Stufe (Classic 1–59). Was die Sitzungen selbst gemessen haben, geht vor.
CLASSIC_XP = [400, 900, 1400, 2100, 2800, 3600, 4500, 5400, 6500, 7600, 8800, 10100, 11400, 12900, 14400, 16000,
              17700, 19400, 21300, 23200, 25200, 27300, 29400, 31700, 34000, 36400, 38900, 41400, 44300, 47400,
              50800, 54500, 58600, 62800, 67100, 71600, 76100, 80800, 85700, 90700, 95800, 101000, 106300, 111800,
              117500, 123200, 129100, 135100, 141200, 147500, 153900, 160400, 167100, 173900, 180800, 187900,
              195000, 202300, 209800]
MAX_LEVEL = len(CLASSIC_XP) + 1


def xp_for_level(level: int, measured: dict[int, int]) -> int:
    if level in measured:
        return measured[level]
    return CLASSIC_XP[level - 1] if 1 <= level <= len(CLASSIC_XP) else 0


def level_forecast(sessions: list[dict]) -> list[dict]:
    """Pro Charakter: Tempo der letzten Sitzungen und geschätzte Spielzeit bis zu den nächsten Stufen."""
    by_char: dict[str, list[dict]] = {}
    for s in sessions:
        by_char.setdefault(s["char"], []).append(s)
    measured: dict[int, int] = {}
    for s in sessions:
        if s.get("xpMax") and s["level"]["to"]:
            measured[s["level"]["to"]] = _int(s["xpMax"])
    out = []
    for char, lst in by_char.items():
        lst = sorted(lst, key=lambda s: s["stop"])
        last = lst[-1]
        level = last["level"]["to"]
        if not level or level >= MAX_LEVEL:
            continue
        recent = [s for s in lst if s["played"] >= 900 and s["xp"]["total"] > 0][-10:]
        played = sum(s["played"] for s in recent)
        xp = sum(s["xp"]["total"] for s in recent)
        if played < 1800 or xp <= 0:
            continue
        rate = xp * 3600 / played
        need_lvl = xp_for_level(level, measured)
        now = _int(last.get("xpNow")) if last.get("xpMax") else 0
        need_next = max(0, (_int(last.get("xpMax")) or need_lvl) - now)
        targets = []
        for target in sorted({level + 1, ((level // 10) + 1) * 10, MAX_LEVEL}):
            if target <= level or target > MAX_LEVEL:
                continue
            need = need_next + sum(xp_for_level(l, measured) for l in range(level + 1, target))
            targets.append({"level": target, "xp": need, "hours": need / rate})
        out.append({"char": char, "name": last["name"] or char, "level": level, "progress": now / last["xpMax"]
                    if last.get("xpMax") else None, "rate": rate, "sessions": len(recent), "targets": targets})
    out.sort(key=lambda f: f["name"].lower())
    return out


def session_records(sessions: list[dict]) -> list[tuple[str, str, dict]]:
    """(Rekord, Wert, Sitzung) – nur sinnvolle Sitzungen (Raten ab 20 Minuten)."""
    recs = []

    def best(label, key, fmt, cond=lambda s: True):
        cands = [s for s in sessions if cond(s)]
        if not cands:
            return
        s = max(cands, key=key)
        if key(s) > 0:
            recs.append((label, fmt(s), s))

    long_enough = lambda s: s["played"] >= 1200      # noqa: E731
    best("Längste Sitzung", lambda s: s["played"], lambda s: fmt_dur(s["played"]))
    best("Meiste Erfahrung", lambda s: s["xp"]["total"], lambda s: tr("{0} XP", fmt_int(s["xp"]["total"])))
    best("Beste XP pro Stunde", lambda s: s["xp"]["total"] / s["played"], lambda s: tr("{0}/h", fmt_int(
        s["xp"]["total"] * 3600 / s["played"])), long_enough)
    best("Bestes Gold pro Stunde", lambda s: session_net_money(s) / s["played"], lambda s: tr("{0}/h", fmt_money(
        session_net_money(s) * 3600 / s["played"])), long_enough)
    best("Meiste Kills", lambda s: s["kills"]["n"], lambda s: fmt_int(s["kills"]["n"]))
    best("Meiste Quests", lambda s: len(s["quests"]["done"]), lambda s: fmt_int(len(s["quests"]["done"])))
    best("Größter Treffer", lambda s: s["combat"]["maxHit"], lambda s: f"{fmt_int(s['combat']['maxHit'])} "
         f"({s['combat']['maxHitSpell'] or '?'})")
    best("Größte Heilung", lambda s: s["combat"]["maxHeal"], lambda s: f"{fmt_int(s['combat']['maxHeal'])} "
         f"({s['combat']['maxHealSpell'] or '?'})")
    best("Weiteste Strecke zu Fuß", lambda s: s["move"]["foot"] + s["move"]["ghost"],
         lambda s: fmt_dist(s["move"]["foot"] + s["move"]["ghost"]))
    best("Wertvollste Beute", lambda s: s["loot"]["value"], lambda s: fmt_money(s["loot"]["value"]))
    best("Meiste Tode", lambda s: s["deathN"], lambda s: fmt_int(s["deathN"]))
    return recs


def death_stats(sessions: list[dict]) -> dict:
    by_killer, by_zone, total, played = {}, {}, 0, 0
    for s in sessions:
        total += s["deathN"]
        played += s["played"]
        for d in s["deaths"]:
            killer = d["by"] or tr(ENV_KINDS.get(d["spell"], d["spell"] or "unbekannt"))
            by_killer[killer] = by_killer.get(killer, 0) + 1
            where = d["inst"] or d["zone"] or "?"
            by_zone[where] = by_zone.get(where, 0) + 1
    return {"total": total, "per_hour": total * 3600 / played if played >= 3600 else 0,
            "killers": _top(by_killer, 8), "zones": _top(by_zone, 8)}


def zone_ranking(sessions: list[dict], min_sec: int = 600) -> list[dict]:
    zones: dict[str, dict] = {}
    for s in sessions:
        for name, z in s["explore"]["zones"].items():
            e = zones.setdefault(name, {"zone": name, "sec": 0, "xp": 0, "money": 0, "kills": 0})
            e["sec"] += _int(z.get("sec"))
            e["xp"] += _int(z.get("xp"))
            e["money"] += _int(z.get("money"))
            e["kills"] += _int(z.get("kills"))
    out = []
    for e in zones.values():
        if e["sec"] >= min_sec:
            h = e["sec"] / 3600
            out.append(dict(e, xp_h=e["xp"] / h, money_h=e["money"] / h, kills_h=e["kills"] / h))
    return out


def dungeon_history(sessions: list[dict]) -> list[dict]:
    inst: dict[str, dict] = {}
    for s in sessions:
        for r in s["runs"]:
            if not r["name"] or not r["start"]:
                continue
            stop = r["stop"] or s["stop"]
            e = inst.setdefault(r["name"], {"name": r["name"], "kind": r["kind"], "runs": 0, "times": [], "bosses": {},
                                            "wipes": 0, "deaths": 0, "kills": 0, "xp": 0, "last": 0, "drops": []})
            e["runs"] += 1
            e["times"].append(max(0, stop - r["start"]))
            for b in r["bosses"]:
                e["bosses"][b] = e["bosses"].get(b, 0) + 1
            e["wipes"] += r["wipes"]
            e["deaths"] += r["deaths"]
            e["kills"] += r["kills"]
            e["xp"] += r["xp"]
            e["last"] = max(e["last"], r["start"])
            for d in r.get("drops", []):
                e["drops"].append(d)
    out = []
    for e in inst.values():
        cleared = [t for t in e["times"] if t > 0]
        e["best"] = min(cleared) if cleared else 0
        e["avg"] = sum(cleared) / len(cleared) if cleared else 0
        out.append(e)
    out.sort(key=lambda e: e["last"], reverse=True)
    return out


def analysis_report(sessions: list[dict], label: str) -> list[tuple[str, str]]:
    P: list[tuple[str, str]] = []
    add = lambda text, tag="": P.append((text, tag))   # noqa: E731

    def head(text):
        add("\n" + text + "\n", "head")

    def line(text, tag=""):
        add(text + "\n", tag)

    add(tr("Auswertung – {0}", label) + "\n", "title")
    if not sessions:
        line(tr("Noch keine Sitzungen."), "dim")
        return P

    head(tr("Prognose"))
    fc = level_forecast(sessions)
    if not fc:
        line(tr("Für eine Prognose braucht es mindestens 30 Minuten Spielzeit mit Erfahrung."), "dim")
    for f in fc:
        add(f["name"], "hl")
        prog = f" ({round(100 * f['progress'])} %)" if f["progress"] is not None else ""
        line("  " + tr("Stufe {0}{1} · Ø {2} XP/h aus {3}", f["level"], prog, fmt_int(f["rate"]),
                       plural(f["sessions"], "{0} Sitzung", "{0} Sitzungen")), "dim")
        line("  " + " · ".join(tr("Stufe {0} in ≈ {1}", t["level"], fmt_dur(t["hours"] * 3600)) for t in f["targets"]))
    if fc:
        line(tr("Spielzeit, geschätzt aus deinem bisherigen Tempo. Wo noch nicht gemessen, gilt die Erfahrungstabelle von Classic."), "dim")

    head(tr("Rekorde"))
    for label_r, value, s in session_records(sessions):
        add(tr(label_r) + ": ", "")
        add(value, "hl")
        line(f"  {s['name'] or s['char']}, {datetime.fromtimestamp(s['start']).strftime(tr('%d.%m.%Y'))}", "dim")

    head(tr("Tode"))
    ds = death_stats(sessions)
    if not ds["total"]:
        line(tr("Keine Tode – sehr vorsichtig!"), "dim")
    else:
        line(plural(ds["total"], "{0} Tod", "{0} Tode") +
             (tr(" · {0} pro Stunde", f"{ds['per_hour']:.2f}".replace(".", "," if cur_lang() == "de" else ".")) if ds["per_hour"] else ""))
        if ds["killers"]:
            line(tr("Gefährlichste Gegner: {0}", ", ".join(f"{k} ×{n}" for k, n in ds["killers"])), "dim")
        if ds["zones"]:
            line(tr("Gefährlichste Orte: {0}", ", ".join(f"{k} ×{n}" for k, n in ds["zones"])), "dim")

    head(tr("Farmgebiete"))
    zr = zone_ranking(sessions)
    if not zr:
        line(tr("Noch zu wenig Daten – eine Zone zählt ab 10 Minuten Spielzeit (aufgezeichnet ab Leeroylink 0.3)."), "dim")
    else:
        by_xp = sorted((z for z in zr if z["xp"]), key=lambda z: z["xp_h"], reverse=True)[:6]
        by_gold = sorted((z for z in zr if z["money"]), key=lambda z: z["money_h"], reverse=True)[:6]
        if by_xp:
            line(tr("Erfahrung pro Stunde: {0}", ", ".join(f"{z['zone']} {fmt_int(z['xp_h'])}" for z in by_xp)))
        if by_gold:
            line(tr("Gold pro Stunde: {0}", ", ".join(f"{z['zone']} {fmt_money(z['money_h'])}" for z in by_gold)))
        by_kills = sorted((z for z in zr if z["kills"]), key=lambda z: z["kills_h"], reverse=True)[:6]
        if by_kills:
            line(tr("Kills pro Stunde: {0}", ", ".join(f"{z['zone']} {fmt_int(z['kills_h'])}" for z in by_kills)), "dim")
        line(tr("Gold zählt Beute, Questbelohnungen und Händlerverkäufe in der Zone."), "dim")

    head(tr("Dungeons und Schlachtzüge"))
    dh = dungeon_history(sessions)
    if not dh:
        line(tr("Noch keine Instanz besucht."), "dim")
    for e in dh:
        add(e["name"], "hl")
        line("  " + " · ".join(x for x in (
            plural(e["runs"], "{0} Besuch", "{0} Besuche"),
            tr("Bestzeit {0}", fmt_dur(e["best"])) if e["best"] else "",
            tr("Ø {0}", fmt_dur(e["avg"])) if e["avg"] and e["runs"] > 1 else "",
            plural(e["wipes"], "{0} Wipe", "{0} Wipes") if e["wipes"] else "",
            plural(e["deaths"], "{0} Tod", "{0} Tode") if e["deaths"] else "",
            tr("zuletzt {0}", datetime.fromtimestamp(e["last"]).strftime(tr("%d.%m.%Y")))) if x), "dim")
        if e["bosses"]:
            line("  " + tr("Bosse: {0}", ", ".join(f"{b} ×{n}" if n > 1 else b for b, n in _top(e["bosses"], 12))))
        by_boss: dict[str, list] = {}
        for d in e["drops"]:
            by_boss.setdefault(d.get("boss") or tr("unterwegs"), []).append(d)
        for boss, drops in by_boss.items():
            add("  " + boss + ": ", "dim")
            for i, d in enumerate(drops[-8:]):
                if i:
                    add(", ", "dim")
                add(f"[{d['name']}]", f"q{min(max(_int(d.get('q'), 2), 0), 6)}")
            add("\n")
    return P


def chart_series(sessions: list[dict]) -> dict:
    """Werte für die Diagramme (chronologisch, höchstens 40 Sitzungen)."""
    lst = sorted(sessions, key=lambda s: s["start"])[-40:]
    labels = [datetime.fromtimestamp(s["start"]).strftime("%d.%m") for s in lst]
    xp_h = [s["xp"]["total"] * 3600 / s["played"] if s["played"] >= 600 else 0 for s in lst]
    gold = [session_net_money(s) / 10000 for s in lst]
    days: dict[str, int] = {}
    today = datetime.now().date()
    for i in range(29, -1, -1):
        days[(today.fromordinal(today.toordinal() - i)).strftime("%Y-%m-%d")] = 0
    for s in sessions:
        d = datetime.fromtimestamp(s["start"]).strftime("%Y-%m-%d")
        if d in days:
            days[d] += s["played"]
    levels: dict[str, list[tuple[int, int]]] = {}
    for s in sorted(sessions, key=lambda s: s["stop"]):
        if s["level"]["to"]:
            levels.setdefault(s["name"] or s["char"], []).append((s["stop"], s["level"]["to"]))
    return {"labels": labels, "xp_h": xp_h, "gold": gold,
            "day_labels": [datetime.strptime(d, "%Y-%m-%d").strftime("%d.%m") for d in days],
            "day_hours": [v / 3600 for v in days.values()], "levels": levels}


# ----- Discord ---------------------------------------------------------------------- #
DISCORD_WEBHOOK_RE = re.compile(r"^https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/api/webhooks/\d+/[\w-]+/?$")


def discord_payload(s: dict) -> dict:
    """Eine Sitzung als Discord-Nachricht (Embed)."""
    k, x = s["kills"], s["xp"]
    lv = s["level"]
    fields = [(tr("Spielzeit"), fmt_dur(s["played"])),
              (tr("Stufe"), f"{lv['from']} → {lv['to']}" if lv["to"] > lv["from"] > 0 else str(lv["to"] or "–")),
              (tr("Erfahrung"), fmt_int(x["total"])),
              (tr("Gold"), fmt_money(session_net_money(s), sign=True)),
              (tr("Gegner"), fmt_int(k["n"]) + (f" ({tr('{0} Elite', k['elite'] + k['rareelite'])})" if k["elite"] + k["rareelite"] else "")),
              (tr("Quests"), fmt_int(len(s["quests"]["done"]))),
              (tr("Tode"), fmt_int(s["deathN"])),
              (tr("Strecke"), fmt_dist(s["move"]["foot"] + s["move"]["ghost"] + s["move"]["mount"]))]
    if s["bosses"]:
        fields.append((tr("Bosse"), ", ".join(dict.fromkeys(b["name"] for b in s["bosses"]))[:1000]))
    best = max(s["loot"]["notable"], key=lambda e: (e["q"], e["t"]), default=None)
    if best and best["q"] >= 3:
        fields.append((tr("Beste Beute"), f"[{best['name']}]"))
    cls = tr(CLASS_NAMES.get(s["class"], s["class"].title())) if s["class"] else ""
    return {"username": "Leeroyzan", "embeds": [{
        "title": f"{s['name'] or s['char']}{' · ' + cls if cls else ''} – {session_title(s)}"[:256],
        "description": _summary_sentence(s)[:3500],
        "color": 0xE6BB70,
        "fields": [{"name": n, "value": v or "–", "inline": True} for n, v in fields][:25],
        "footer": {"text": f"Leeroyzan {APP_VERSION} · Leeroylink"},
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(s["stop"])),
    }]}


def discord_send(url: str, payload: dict) -> None:
    if not DISCORD_WEBHOOK_RE.match(url.strip()):
        raise ValueError(tr("Das ist keine Discord-Webhook-Adresse."))
    req = urllib.request.Request(url.strip(), data=json.dumps(payload).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(tr("Discord hat abgelehnt (HTTP {0}).", e.code)) from None
