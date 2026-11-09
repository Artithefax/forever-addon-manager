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
        texts |= {label for _, label in lz.App.SESSION_PERIODS}
        for d in ("QUALITY_NAMES", "MONEY_SOURCES", "GATHER_KINDS", "INSTANCE_KINDS", "ENV_KINDS", "CLASS_NAMES"):
            v = getattr(lz, d)
            texts |= set(v.values() if isinstance(v, dict) else v)
        texts = {t.encode().decode("unicode_escape").encode("latin-1").decode("utf-8") if "\\" in t else t for t in texts}
        missing = sorted(t for t in texts if t not in lz.TRANSLATIONS["en"] and t not in ("Arena", "Paladin"))
        self.assertEqual(missing, [], "missing English texts")


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
