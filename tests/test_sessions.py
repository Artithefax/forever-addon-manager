"""Tests for the session import in Leeroyzan (python3 tests/test_sessions.py).

Runs the addon through tests/scenario.lua (needs `pip install lupa`), puts the
saved variables into a fake WoW folder and checks import, archive and reports.
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="lz-test-"))
os.environ["APPDATA"] = str(TMP / "appdata")          # Leeroyzan-Daten in einen Testordner
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import leeroyzan as lz  # noqa: E402

try:
    import run_addon_test
except ImportError:                                    # lupa fehlt
    run_addon_test = None


def make_wow(sv_text: str, account: str = "123#1") -> Path:
    wow = TMP / "World of Warcraft"
    sv = wow / "_forever_" / "WTF" / "Account" / account / "SavedVariables"
    sv.mkdir(parents=True, exist_ok=True)
    (sv / "LeeroyzanLink.lua").write_text(sv_text, encoding="utf-8")
    (wow / "_forever_" / "Interface" / "AddOns").mkdir(parents=True, exist_ok=True)
    return wow


class LuaParserTests(unittest.TestCase):
    def test_wow_format(self):
        text = '''
LeeroyzanLinkDB = {
["a"] = "x\\"y\\\\z\\nnew",
["n"] = -12,
["f"] = 1.5e3,
["t"] = true,
["list"] = {
"eins", -- [1]
{
["k"] = 1,
}, -- [2]
},
["sparse"] = {
[0] = 5,
[2] = 7,
},
["empty"] = {
},
["umlaut"] = "Kräuterkunde \\195\\164",
}
Other = nil
'''
        out = lz.parse_lua_savedvariables(text)
        db = out["LeeroyzanLinkDB"]
        self.assertEqual(db["a"], 'x"y\\z\nnew')
        self.assertEqual(db["n"], -12)
        self.assertEqual(db["f"], 1500.0)
        self.assertIs(db["t"], True)
        self.assertEqual(db["list"], ["eins", {"k": 1}])
        self.assertEqual(db["sparse"], {0: 5, 2: 7})
        self.assertEqual(db["empty"], {})
        self.assertEqual(db["umlaut"], "Kräuterkunde ä")
        self.assertIsNone(out["Other"])

    def test_broken_file(self):
        with self.assertRaises(lz.LuaParseError):
            lz.parse_lua_savedvariables('X = { ["a"] = 1, ')


class RealDataTests(unittest.TestCase):
    """The saved variables written by Leeroyzan Link 0.1 in the real game."""

    def test_legacy_session(self):
        text = '''
LeeroyzanLinkDB = {
["global"] = {
["sessions"] = {
{
["char"] = "Rosenbert-CAMWOWEU5Realm2PvP",
["kills"] = 0,
["level"] = 21,
["start"] = 1794265109,
["duration"] = 127,
["xp"] = 1550,
["money"] = 0,
},
},
},
}
'''
        db = lz.parse_lua_savedvariables(text)["LeeroyzanLinkDB"]
        ss = lz.sessions_in_db(db, "_forever_", "acc")
        self.assertEqual(len(ss), 1)
        s = ss[0]
        self.assertTrue(s["legacy"])
        self.assertEqual(s["played"], 127)
        self.assertEqual(s["xp"]["total"], 1550)
        text = lz.report_plain_text(lz.session_report(s))
        self.assertIn("Leeroyzan Link 0.1", text)


@unittest.skipIf(run_addon_test is None, "lupa not installed")
class ImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sv = TMP / "sv.lua"
        f1, text, now = run_addon_test.run(ROOT / "LeeroyzanLink", 1, None)
        sv.write_text(text, encoding="utf-8")
        f2, text, _ = run_addon_test.run(ROOT / "LeeroyzanLink", 2, sv, start_now=now + 15)
        assert f1 + f2 == 0, "addon scenario failed"
        cls.sv_text = text
        cls.wow = make_wow(text)

    def setUp(self):
        lz.set_language("de")
        shutil.rmtree(lz.sessions_dir("_forever_"), ignore_errors=True)

    def test_import_and_update(self):
        root = str(self.wow)
        r = lz.import_link_sessions(root, "_forever_")
        self.assertEqual(len(r["new"]), 1)
        self.assertEqual(r["errors"], [])
        again = lz.import_link_sessions(root, "_forever_")
        self.assertEqual(again["new"] + again["updated"], [])
        stored = lz.load_sessions("_forever_")
        self.assertEqual(len(stored), 1)
        s = stored[0]
        self.assertEqual(s["kills"]["n"], 8)
        self.assertEqual(s["kills"]["boss"], 1)
        self.assertEqual(s["quests"]["done"][0]["title"], "Die Bedrohung der Gnolle")
        self.assertEqual(s["prof"]["skills"]["Kräuterkunde"]["b"], 46)
        self.assertEqual(lz.session_net_money(s), 2625)
        # the same session saved again later (continued after a /reload) replaces the old one
        newer = self.sv_text.replace(f'["saved"] = {s["saved"]},', f'["saved"] = {s["saved"] + 100},')
        self.assertNotEqual(newer, self.sv_text)
        make_wow(newer)
        r = lz.import_link_sessions(root, "_forever_")
        self.assertEqual((len(r["new"]), len(r["updated"])), (0, 1))
        self.assertEqual(len(lz.load_sessions("_forever_")), 1)
        make_wow(self.sv_text)

    def test_report_texts(self):
        lz.import_link_sessions(str(self.wow), "_forever_")
        s = lz.load_sessions("_forever_")[0]
        for lang in ("de", "en"):
            lz.set_language(lang)
            text = lz.report_plain_text(lz.session_report(s))
            for word in ({"de": ["Zusammenfassung", "Edwin VanCleef", "Kräuterkunde 45 → 46", "Die Todesminen",
                                 "Heldenhafter Stoß", "Steinwacht", "Sturmwind +250", "Hiebe"],
                          "en": ["Summary", "Edwin VanCleef", "Kräuterkunde 45 → 46", "swings", "Reputation"]}[lang]):
                self.assertIn(word, text, f"{lang}: {word}")
            head = lz.session_headline(s)
            self.assertIn("Rosenbert", head)
            over = lz.report_plain_text(lz.overview_report([s, s], "Test"))
            self.assertIn("Rosenbert", over)
        lz.set_language("de")

    def test_untranslated(self):
        """Every German UI text of the session feature has an English translation."""
        import re
        src = (ROOT / "leeroyzan.py").read_text(encoding="utf-8")
        start = src.index("# Leeroyzan Link: Spielsitzungen")
        end = src.index("# Theme (Braun/Gold")
        block = src[start:end]
        ui_start = src.index("# --- Sitzungen ---")
        ui_end = src.index("# --- Einstellungen ---")
        block += src[ui_start:ui_end]
        texts = set()
        for m in re.finditer(r'tr\(\s*((?:"(?:[^"\\]|\\.)*"\s*)+)', block):     # auch "a" "b" (verkettet)
            texts.add("".join(re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1))))
        for m in re.finditer(r'plural\([^,]+, "((?:[^"\\]|\\.)*)", "((?:[^"\\]|\\.)*)"\)', block):
            texts |= {m.group(1), m.group(2)}
        texts |= {label for _, label in lz.App.SESSION_PERIODS + lz.App.DIAG_VIEWS + lz.App.SESS_VIEWS}
        texts |= set(re.findall(r'best\("([^"]+)"', block))           # Rekorde
        texts |= set(lz.LOAD_REASONS.values()) | {"Charaktere", "Diagnose"}
        texts.discard("Discord")
        for d in ("QUALITY_NAMES", "MONEY_SOURCES", "GATHER_KINDS", "INSTANCE_KINDS", "ENV_KINDS", "CLASS_NAMES"):
            v = getattr(lz, d)
            texts |= set(v.values() if isinstance(v, dict) else v)
        texts = {t.encode().decode("unicode_escape").encode("latin-1").decode("utf-8") if "\\" in t else t for t in texts}
        missing = sorted(t for t in texts if t not in lz.TRANSLATIONS["en"] and t not in ("Arena", "Paladin"))
        self.assertEqual(missing, [], "missing English texts")


class LinkDataTests(unittest.TestCase):
    """Leeroyzan -> Spiel: Data.lua, die das Addon annehmen muss."""

    PAYLOAD = {"schema": 1, "generated": 1794300000, "managerVersion": "1.4.0",
               "updates": [{"name": "Questie", "installed": "9.2.0", "available": "9.3.0", "note": None}],
               "managed": ["Questie", "Bagnon"], "installed": ["Details! Damage Meter"],
               "profiles": [{"name": "Raid", "enable": ["Details", "Bagnon"]}],
               "whatsnew": [{"id": "gh:x@9.3.0", "addon": "Questie", "version": "9.3.0",
                             "lines": lz.notes_lines("## Changes\n- **Neue** Quests für Forever\n\n* \"Fix\" für \\Pfade")}],
               "knownIssues": [{"addon": "Bagnon", "text": "In Leeroyzan festgehalten"}]}

    def test_lua_text_parses_back(self):
        text = lz.link_data_text(self.PAYLOAD)
        back = lz.parse_lua_savedvariables(text)["LeeroyzanLinkImport"]
        self.assertEqual(back["updates"][0]["available"], "9.3.0")
        self.assertEqual(back["whatsnew"][0]["lines"], ["Neue Quests für Forever", '"Fix" für \\Pfade'])
        self.assertEqual(back["profiles"][0]["enable"], ["Details", "Bagnon"])

    @unittest.skipIf(run_addon_test is None, "lupa not installed")
    def test_addon_accepts_data(self):
        from lupa import lua51
        data = TMP / "Data.lua"
        data.write_text(lz.link_data_text(self.PAYLOAD), encoding="utf-8")
        lua = lua51.LuaRuntime(unpack_returned_tuples=True)
        g = lua.globals()
        g.ADDON_DIR, g.TESTS_DIR, g.DATA_FILE = str(ROOT / "LeeroyzanLink"), str(ROOT / "tests"), str(data)
        self.assertEqual(lua.execute(f'return dofile("{ROOT / "tests" / "import_check.lua"}")'), 0)

    def test_write_only_when_changed(self):
        wow = make_wow("LeeroyzanLinkDB = {}\n", "w#1")
        addon = wow / "_forever_" / "Interface" / "AddOns" / "LeeroyzanLink"
        addon.mkdir(parents=True, exist_ok=True)
        (addon / "LeeroyzanLink.toc").write_text("## Version: 0.3.0\n", encoding="utf-8")
        self.assertEqual(lz.write_link_data(str(wow), "_forever_", self.PAYLOAD), "written")
        later = dict(self.PAYLOAD, generated=1794399999)
        self.assertEqual(lz.write_link_data(str(wow), "_forever_", later), "unchanged")
        changed = dict(self.PAYLOAD, managed=["Questie"])
        self.assertEqual(lz.write_link_data(str(wow), "_forever_", changed), "written")


@unittest.skipIf(run_addon_test is None, "lupa not installed")
class GameDataTests(unittest.TestCase):
    """Spiel -> Leeroyzan: Fehler, Charaktere, Rezepte, Einkaufsliste, Auswertung."""

    @classmethod
    def setUpClass(cls):
        sv = TMP / "sv_state.lua"
        f1, text, now = run_addon_test.run(ROOT / "LeeroyzanLink", 1, None)
        sv.write_text(text, encoding="utf-8")
        f2, text, _ = run_addon_test.run(ROOT / "LeeroyzanLink", 2, sv, start_now=now + 15)
        assert f1 + f2 == 0
        db = lz.parse_lua_savedvariables(text)["LeeroyzanLinkDB"]
        cls.state = lz.link_state_from_dbs([("acc#1", db)])
        cls.sessions = lz.sessions_in_db(db, "_forever_", "acc#1")

    def test_state(self):
        st = self.state
        self.assertIn("FooBar", st["errorStats"])
        rows = lz.error_rows(st, {"FooBar": "0.2.0"})        # the stub reports 0.2.0 for every addon
        self.assertEqual((rows[0]["addon"], rows[0]["current"]), ("FooBar", 1))
        self.assertEqual(st["wishlist"][0]["name"], "Details! Damage Meter")
        self.assertEqual(st["reports"]["FooBar"]["version"], "2.0")
        ch = st["chars"]["Rosenbert-CAMWOWEU5Realm2PvP"]
        self.assertEqual(ch["level"], 22)
        self.assertEqual(ch["bags"]["2589"], 12)
        self.assertIn("Geringer Heiltrank", ch["recipes"]["Alchemie"]["list"])
        self.assertEqual(lz.item_name(st, 3371), "Leere Phiole")
        found = lz.find_items(st, "seiden")
        self.assertEqual((found[0]["name"], found[0]["n"]), ("Seidenstoff", 3))

    def test_shopping_list(self):
        import copy
        st = copy.deepcopy(self.state)
        alt = copy.deepcopy(st["chars"]["Rosenbert-CAMWOWEU5Realm2PvP"])
        alt.update(key="Twink-Realm", name="Twink", recipes={}, bags={"3371": 7}, bank={"118": 4})
        st["chars"]["Twink-Realm"] = alt
        res = lz.shopping_list(st, "Rosenbert-CAMWOWEU5Realm2PvP", "Alchemie", "Geringer Heiltrank", 5)
        rows = {r["name"]: r for r in res["rows"]}
        self.assertEqual(res["crafts"], 5)
        self.assertEqual(rows["Schwacher Heiltrank"]["need"], 10)
        self.assertEqual(rows["Schwacher Heiltrank"]["others"][0]["char"], "Twink")
        self.assertEqual(rows["Schwacher Heiltrank"]["missing"], 6)
        self.assertEqual(rows["Leere Phiole"]["missing"], 0)
        self.assertEqual(rows["Leere Phiole"]["transfer"], 5)

    def test_analysis(self):
        s = self.sessions[0]
        self.assertEqual(s["explore"]["zones"]["Rotkammgebirge"]["kills"], 8)  # the stub never changes the zone
        self.assertEqual(s["runs"][0]["drops"][0]["boss"], "Edwin VanCleef")
        # a few longer sessions so forecast and rates have something to work with
        many = []
        for i in range(4):
            x = json.loads(json.dumps(s))
            x.update(id=f"x{i}", start=s["start"] + i * 86400, stop=s["start"] + i * 86400 + 3600, played=3600)
            x["explore"]["zones"]["Rotkammgebirge"]["sec"] = 3000
            many.append(x)
        for lang in ("de", "en"):
            lz.set_language(lang)
            text = lz.report_plain_text(lz.analysis_report(many, "Test"))
            self.assertIn("Edwin VanCleef", text)
            self.assertIn("Rotkammgebirge", text)
            self.assertIn("Rosenbert", text)
        lz.set_language("de")
        fc = lz.level_forecast(many)
        self.assertEqual(fc[0]["level"], 22)
        self.assertGreater(fc[0]["targets"][0]["hours"], 0)
        series = lz.chart_series(many)
        self.assertEqual(len(series["xp_h"]), 4)
        self.assertEqual(len(series["day_hours"]), 30)
        payload = lz.discord_payload(s)
        self.assertEqual(payload["embeds"][0]["fields"][0]["value"], lz.fmt_dur(s["played"]))
        with self.assertRaises(ValueError):
            lz.discord_send("https://example.com/hook", payload)


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
