"""Tests for the session import in Leeroyzan (python3 tests/test_sessions.py).

Runs the addon through tests/scenario.lua (needs `pip install lupa`), puts the
saved variables into a fake WoW folder and checks import, archive and reports.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="lz-test-"))
os.environ["APPDATA"] = str(TMP / "appdata")          # Leeroyzan-Daten in einen Testordner
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import leeroy.api as lz  # noqa: E402

try:
    import run_addon_test
except ImportError:                                    # lupa fehlt
    run_addon_test = None


def make_wow(sv_text: str, account: str = "123#1") -> Path:
    wow = TMP / "World of Warcraft"
    sv = wow / "_forever_" / "WTF" / "Account" / account / "SavedVariables"
    sv.mkdir(parents=True, exist_ok=True)
    (sv / "Leeroylink.lua").write_text(sv_text, encoding="utf-8")
    (wow / "_forever_" / "Interface" / "AddOns").mkdir(parents=True, exist_ok=True)
    return wow


class LuaParserTests(unittest.TestCase):
    def test_wow_format(self):
        text = '''
LeeroylinkDB = {
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
        db = out["LeeroylinkDB"]
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
    """The saved variables written by Leeroylink 0.1 in the real game."""

    def test_legacy_session(self):
        text = '''
LeeroylinkDB = {
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
        db = lz.parse_lua_savedvariables(text)["LeeroylinkDB"]
        ss = lz.sessions_in_db(db, "_forever_", "acc")
        self.assertEqual(len(ss), 1)
        s = ss[0]
        self.assertTrue(s["legacy"])
        self.assertEqual(s["played"], 127)
        self.assertEqual(s["xp"]["total"], 1550)
        text = lz.report_plain_text(lz.session_report(s))
        self.assertIn("Leeroylink 0.1", text)


@unittest.skipIf(run_addon_test is None, "lupa not installed")
class ImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sv = TMP / "sv.lua"
        f1, text, now = run_addon_test.run(ROOT / "Leeroylink", 1, None)
        sv.write_text(text, encoding="utf-8")
        f2, text, _ = run_addon_test.run(ROOT / "Leeroylink", 2, sv, start_now=now + 15)
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
        self.assertEqual(s["kills"]["n"], 11)
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
        src = "\n".join(p.read_text(encoding="utf-8") for p in [ROOT / "leeroyzan.py", *sorted((ROOT / "leeroy").rglob("*.py"))])
        start = src.index("# Leeroylink: Spielsitzungen")
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
               "knownIssues": [{"addon": "Bagnon", "text": "In Leeroyzan festgehalten"}],
               "questGuide": {"on": False, "at": 1794300000}}

    def test_lua_text_parses_back(self):
        text = lz.link_data_text(self.PAYLOAD)
        back = lz.parse_lua_savedvariables(text)["LeeroylinkImport"]
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
        g.ADDON_DIR, g.TESTS_DIR, g.DATA_FILE = str(ROOT / "Leeroylink"), str(ROOT / "tests"), str(data)
        self.assertEqual(lua.execute(f'return dofile("{(ROOT / "tests" / "import_check.lua").as_posix()}")'), 0)

    def test_write_only_when_changed(self):
        wow = make_wow("LeeroylinkDB = {}\n", "w#1")
        addon = wow / "_forever_" / "Interface" / "AddOns" / "Leeroylink"
        addon.mkdir(parents=True, exist_ok=True)
        (addon / "Leeroylink.toc").write_text("## Version: 0.3.0\n", encoding="utf-8")
        self.assertEqual(lz.write_link_data(str(wow), "_forever_", self.PAYLOAD), "written")
        later = dict(self.PAYLOAD, generated=1794399999)
        self.assertEqual(lz.write_link_data(str(wow), "_forever_", later), "unchanged")
        changed = dict(self.PAYLOAD, managed=["Questie"])
        self.assertEqual(lz.write_link_data(str(wow), "_forever_", changed), "written")


class RenameTests(unittest.TestCase):
    """Leeroyzan Link (bis 0.3.2, Ordner LeeroyzanLink) → Leeroylink: Daten bleiben erhalten."""

    def test_migration(self):
        wow = TMP / "rename" / "World of Warcraft"
        acc = wow / "_forever_" / "WTF" / "Account"
        for name, text in (("1#1", '\nLeeroyzanLinkDB = {\n["global"] = {\n["sessionLog"] = {\n},\n["chars"] = {\n["Rosenbert"] = {\n["level"] = 23,\n},\n},\n},\n}\n'),
                           ("2#1", 'LeeroyzanLinkDB = {\n["global"] = {\n},\n}\n')):
            (acc / name / "SavedVariables").mkdir(parents=True)
            (acc / name / "SavedVariables" / "LeeroyzanLink.lua").write_bytes(text.encode("utf-8"))
        # account 2 already uses the new addon: its new file must stay as it is
        (acc / "2#1" / "SavedVariables" / "Leeroylink.lua").write_text("LeeroylinkDB = {}\n", encoding="utf-8")
        old = wow / "_forever_" / "Interface" / "AddOns" / "LeeroyzanLink"
        old.mkdir(parents=True)
        (old / "LeeroyzanLink.toc").write_text("## Version: 0.3.2\n", encoding="utf-8")
        root = str(wow)
        # before switching, Leeroyzan still reads the old file
        files = lz.link_savedvariables(root, "_forever_")
        self.assertEqual([f.name for f in files], ["LeeroyzanLink.lua", "Leeroylink.lua"])
        self.assertEqual(lz.read_link_db(files[0])["global"]["chars"]["Rosenbert"]["level"], 23)
        self.assertEqual(lz.old_link_dir(root, "_forever_"), old)

        res = lz.migrate_link_addon(root, "_forever_")
        self.assertEqual((res["copied"], res["skipped"]), (["1#1"], ["2#1"]))
        new = acc / "1#1" / "SavedVariables" / "Leeroylink.lua"
        db = lz.parse_lua_savedvariables(new.read_text(encoding="utf-8"))
        self.assertEqual(db["LeeroylinkDB"]["global"]["chars"]["Rosenbert"]["level"], 23)
        self.assertTrue((acc / "1#1" / "SavedVariables" / "LeeroyzanLink.lua").is_file())   # old file untouched
        self.assertEqual((acc / "2#1" / "SavedVariables" / "Leeroylink.lua").read_text(encoding="utf-8"), "LeeroylinkDB = {}\n")
        self.assertFalse(old.exists())
        inst = lz.installed_link_dir(root, "_forever_")
        self.assertEqual(inst.name, "Leeroylink")
        self.assertEqual(res["version"], lz.link_version(lz.bundled_link_dir()))
        self.assertIsNone(lz.old_link_dir(root, "_forever_"))
        self.assertEqual([f.name for f in lz.link_savedvariables(root, "_forever_")], ["Leeroylink.lua", "Leeroylink.lua"])
        again = lz.migrate_link_savedvariables(root, "_forever_")      # second run changes nothing
        self.assertEqual(again["copied"], [])


def cbor_encode(v) -> bytes:
    """Gegenstück zu lz.cbor_decode für die Testdaten."""
    def head(mt, n):
        if n < 24:
            return bytes([mt << 5 | n])
        for ai, size in ((24, 1), (25, 2), (26, 4), (27, 8)):
            if n < 1 << (8 * size):
                return bytes([mt << 5 | ai]) + n.to_bytes(size, "big")
    if v is None:
        return b"\xf6"
    if isinstance(v, bool):
        return b"\xf5" if v else b"\xf4"
    if isinstance(v, int):
        return head(0, v) if v >= 0 else head(1, -1 - v)
    if isinstance(v, float):
        import struct
        return b"\xfb" + struct.pack(">d", v)
    if isinstance(v, str):
        raw = v.encode("utf-8")
        return head(2, len(raw)) + raw                    # QuestieDB speichert Texte als Bytes
    if isinstance(v, list):
        return head(4, len(v)) + b"".join(cbor_encode(x) for x in v)
    if isinstance(v, dict):
        return head(5, len(v)) + b"".join(cbor_encode(k) + cbor_encode(x) for k, x in v.items())
    raise TypeError(v)


def baked_toc(quests: dict, npcs: dict, de_names: dict) -> str:
    """Eine kleine QuestieDB_Forever.toc im gebackenen Format (Base64 → zlib → CBOR)."""
    import base64
    import zlib
    b64 = lambda raw: base64.b64encode(raw).decode()
    lines = ["## Interface: 16001", "## Title: QuestieDB", "## Version: 9.9.9", "## X-Mode: baked", "## X-Flavor: Forever"]

    def entity(prefix, rows, table_fields):
        ids = sorted(rows)
        lines.append(f"## X-{prefix}-IDS: " + b64(zlib.compress(cbor_encode(ids))))
        for eid in ids:
            scal, presence = {}, 0
            for fi, val in rows[eid].items():
                if fi in table_fields:
                    presence |= 1 << (fi - 1)
                    lines.append(f"## X-{prefix}-{eid}-{fi}: " + b64(cbor_encode(val)))
                else:
                    scal[fi] = val
            scal["p"] = presence
            lines.append(f"## X-{prefix}-{eid}-S: " + b64(cbor_encode(scal)))
        return ids
    qids = entity("Quest", quests, {2, 3, 8, 10, 12, 13, 14, 16, 26})
    entity("Npc", npcs, {7, 10, 11})
    block = b64(zlib.compress(cbor_encode([[de_names.get(i, [None, None])[0] for i in qids],
                                           [de_names.get(i, [None, None])[1] for i in qids]])))
    half = len(block) // 2                                 # lange Werte stehen in Teilen: ~2~
    lines += ["## X-l10n-deDE-Quest: ~2~", "## X-l10n-deDE-Quest-1: " + block[:half], "## X-l10n-deDE-Quest-2: " + block[half:]]
    return "\n".join(lines) + "\n"


ALLIANCE = 4294967296 + 77
QUESTS = {
    783: {1: "A Threat Within", 2: [[823]], 3: [[197]], 4: 1, 5: 1, 6: ALLIANCE, 8: ["Speak with Marshal McBride."], 17: 9, 22: 7, 26: [[72, 50]]},
    7: {1: "Kobold Camp Cleanup", 2: [[197]], 3: [[197]], 4: 1, 5: 2, 6: ALLIANCE, 8: ["Kill 10 Kobold Vermin."],
        10: [[[6, None, 0]]], 13: [783], 17: 9, 22: 15, 26: [[72, 100]]},
    15: {1: "Investigate Echo Ridge", 2: [[197]], 3: [[197]], 4: 1, 5: 3, 6: ALLIANCE, 13: [7], 17: 9},
    16: {1: "Horde Only", 2: [[3143]], 3: [[3143]], 4: 1, 5: 3, 6: 4294967296 + 178, 13: [7], 17: 14},
    176: {1: 'WANTED: "Hogger"', 2: [None, [68]], 3: [[240]], 4: 5, 5: 11, 6: ALLIANCE, 10: {3: [[1931, None, 0]]}, 17: 12},
}
NPCS = {823: {1: "Deputy Willem", 9: 12, 7: {12: [[48.17, 42.94]]}}, 197: {1: "Marshal McBride", 9: 12, 7: {12: [[48.92, 41.61]]}},
        240: {1: "Marshal Dughan", 9: 12, 7: {12: [[42.1, 65.9]]}}, 3143: {1: "Gornek", 9: 14}}
DE = {783: ["Die Bedrohung von innen", ["Sprecht mit Marshal McBride."]], 7: ["Säuberung im Koboldlager", None]}


class QuestDBTests(unittest.TestCase):
    """QuestieDB lesen (gebackenes TOC-Format), suchen, Questreihen und Quests.lua fürs Addon."""

    @classmethod
    def setUpClass(cls):
        cls.wow = TMP / "qdb" / "World of Warcraft"
        qdb = cls.wow / "_forever_" / "Interface" / "AddOns" / "QuestieDB"
        (qdb / "support" / "Forever" / "QuestXP").mkdir(parents=True)
        (qdb / "src" / "corrections" / "enum").mkdir(parents=True)
        (qdb / "QuestieDB_Forever.toc").write_text(baked_toc(QUESTS, NPCS, DE), encoding="utf-8")
        (qdb / "QuestieDB_Camelot.toc").write_text("## X-Mode: baked\n## X-Flavor: Camelot\n", encoding="utf-8")
        (qdb / "support" / "Forever" / "QuestXP" / "xpDB-classic.lua").write_text(
            "QuestXP.db = {\n  [783] = {1, 40}, -- x\n  [7] = {2, 170},\n  [15] = {3, 250},\n}\n", encoding="utf-8")
        (qdb / "src" / "corrections" / "enum" / "zones.lua").write_text(
            "constants.zoneIDs = {\n  DUN_MOROGH = 1,\n  ELWYNN_FOREST = 12,\n  DUROTAR = 14,\n}\n", encoding="utf-8")
        (cls.wow / "_forever_" / "WTF").mkdir(parents=True)
        (cls.wow / "_forever_" / "WTF" / "Config.wtf").write_text('SET textLocale "deDE"\n', encoding="utf-8")
        cls.db = lz.load_questdb(str(cls.wow), "_forever_")

    def test_read(self):
        db = self.db
        self.assertEqual(db.version, "9.9.9")
        self.assertEqual(set(db.quests), {783, 7, 15, 16, 176})
        self.assertEqual(db.quest_name(783, "deDE"), "Die Bedrohung von innen")
        self.assertEqual(db.quest_name(15, "deDE"), "Investigate Echo Ridge")          # keine Übersetzung: Englisch
        self.assertEqual(db.quest_text(783, "deDE"), ["Sprecht mit Marshal McBride."])
        self.assertEqual(db.quest_text(7, "deDE"), ["Kill 10 Kobold Vermin."])
        q = db.quests[176]
        self.assertEqual((q["side"], q["lvl"], q["req"], q["start"]["obj"], q["items"]), ("A", 11, 5, [68], [1931]))
        self.assertEqual(db.quests[16]["side"], "H")
        self.assertEqual(db.quests[7]["kill"], [6])
        self.assertEqual(db.quests[783]["rep"], [(72, 50)])
        self.assertEqual(db.xp[7], (2, 170))
        self.assertEqual(db.npc(197, "deDE")["pos"], (48.92, 41.61))
        self.assertEqual(db.zone_name(12), "Elwynn Forest")
        self.assertEqual(db.zone_name(12, {12: "Wald von Elwynn"}), "Wald von Elwynn")

    def test_chain_and_search(self):
        db = self.db
        self.assertEqual(db.chain(7), [783, 7, 15])
        self.assertEqual(db.chain(15), [783, 7, 15])
        self.assertIn(16, db.next_of[7])
        self.assertEqual(db.search("kobold", "deDE"), [7])
        self.assertEqual(db.search("säuberung", "deDE"), [7])
        self.assertEqual(db.search("176", "deDE"), [176])
        self.assertEqual(sorted(db.search("mcbride", "deDE")), [7, 15, 783])         # nach Questgeber
        self.assertNotIn(16, db.search("", "deDE", side="A", lmin=1, lmax=60))
        self.assertEqual(db.search("", "deDE", lmin=10, lmax=12), [176])

    def test_quest_data_for_the_addon(self):
        text = lz.quest_data_text(self.db, "deDE", {12: "Wald von Elwynn"})
        self.assertIn('[783]={"Die Bedrohung von innen",1,1,9,"A",7,false,{"Deputy Willem",12,48.2,42.9}', text)
        self.assertIn('[12]="Wald von Elwynn"', text)
        if run_addon_test is not None:
            from lupa import lua51
            lua = lua51.LuaRuntime(unpack_returned_tuples=True)
            lua.execute(text)
            q = lua.globals().LeeroylinkQuests
            self.assertEqual(q.q[7][1], "Säuberung im Koboldlager")
            self.assertEqual(q.q[7][7][1], 783)
            self.assertEqual(q.q[176][8][5], "o")                            # Quest startet an einem Objekt
            self.assertEqual(q.zones[12], "Wald von Elwynn")
        # written once into the load-on-demand addon next to Leeroylink, then left alone until something changes
        addon = self.wow / "_forever_" / "Interface" / "AddOns" / "Leeroylink"
        quests = addon.parent / "Leeroylink_Quests"
        addon.mkdir(parents=True, exist_ok=True)
        (addon / "Leeroylink.toc").write_text("## Interface: 16001\n## Version: 0.7.0\n\nCore\\Core.lua\n", encoding="utf-8")
        self.assertEqual(lz.write_quest_data(str(self.wow), "_forever_"), "written")
        toc = (quests / "Leeroylink_Quests.toc").read_text(encoding="utf-8")
        self.assertIn("## LoadOnDemand: 1", toc)
        self.assertIn("## Interface: 16001", toc)
        self.assertIn("## Dependencies: Leeroylink", toc)
        self.assertFalse((addon / "Quests.lua").exists())
        self.assertEqual(lz.write_quest_data(str(self.wow), "_forever_"), "unchanged")
        self.assertEqual(lz.write_quest_data(str(self.wow), "_forever_", {12: "Wald von Elwynn"}), "written")
        # an addon update leaves the quest data alone
        lz.install_link_addon(str(self.wow), "_forever_")
        self.assertIn("Wald von Elwynn", (quests / "Quests.lua").read_text(encoding="utf-8"))
        self.assertEqual(lz.write_quest_data(str(self.wow), "_forever_", {12: "Wald von Elwynn"}), "unchanged")

    def test_quest_data_for_old_leeroylink(self):
        """Leeroylink up to 0.6 loads Quests.lua itself – then it stays in its folder."""
        wow = TMP / "qdb-old" / "World of Warcraft"
        shutil.copytree(self.wow / "_forever_" / "Interface" / "AddOns" / "QuestieDB",
                        wow / "_forever_" / "Interface" / "AddOns" / "QuestieDB", dirs_exist_ok=True)
        addon = wow / "_forever_" / "Interface" / "AddOns" / "Leeroylink"
        addon.mkdir(parents=True, exist_ok=True)
        (addon / "Leeroylink.toc").write_text("## Version: 0.6.0\n\nData.lua\nQuests.lua\n", encoding="utf-8")
        self.assertEqual(lz.write_quest_data(str(wow), "_forever_"), "written")
        self.assertIn("LeeroylinkQuests = {", (addon / "Quests.lua").read_text(encoding="utf-8"))
        self.assertFalse((addon.parent / "Leeroylink_Quests").exists())

    @unittest.skipIf(run_addon_test is None, "lupa not installed")
    def test_quest_companion_in_game(self):
        """Leeroylink with the quest data Leeroyzan wrote: nothing loaded at login, the companion loads it
        on first use and finds quests, chains and texts."""
        from lupa import lua51
        wow = TMP / "qdb-game" / "World of Warcraft"
        addons = wow / "_forever_" / "Interface" / "AddOns"
        shutil.copytree(self.wow / "_forever_" / "Interface" / "AddOns" / "QuestieDB", addons / "QuestieDB", dirs_exist_ok=True)
        shutil.copytree(ROOT / "Leeroylink", addons / "Leeroylink", dirs_exist_ok=True)
        (wow / "_forever_" / "WTF").mkdir(parents=True, exist_ok=True)
        (wow / "_forever_" / "WTF" / "Config.wtf").write_text('SET textLocale "deDE"\n', encoding="utf-8")
        self.assertEqual(lz.write_quest_data(str(wow), "_forever_"), "written")
        lua = lua51.LuaRuntime(unpack_returned_tuples=True)
        g = lua.globals()
        g.TESTS_DIR = str(ROOT / "tests")
        lua.execute(f'dofile("{(ROOT / "tests" / "wow_stub.lua").as_posix()}")')
        g.STUB.addonsRoot = str(addons.as_posix())
        lua.execute(f'STUB.loadAddon("{(addons / "Leeroylink").as_posix()}")')
        lua.execute('STUB.fire("ADDON_LOADED", "Leeroylink"); STUB.fire("PLAYER_LOGIN"); '
                    'STUB.fire("PLAYER_ENTERING_WORLD", true, false); STUB.advance(2)')
        lua.execute('STUB.testErrors = {}; seterrorhandler(function(e) table.insert(STUB.testErrors, tostring(e)) end)')
        self.assertIsNone(g.LeeroylinkQuests, "quest data must not be loaded at login")
        mod = g.Leeroylink.modules.QuestGuide
        self.assertTrue(mod.enabled)
        self.assertEqual(lua.eval('Leeroylink.modules.QuestGuide:Find("Kobold")'), 7)
        self.assertIsNotNone(g.LeeroylinkQuests, "loaded on first use")
        chain = lua.eval('(function() local l = Leeroylink.modules.QuestGuide:Chain(7); return table.concat(l, ",") end)()')
        self.assertEqual(chain, "783,7,15")
        text = lua.eval('Leeroylink.modules.QuestGuide:Text(7)')
        self.assertIn("Säuberung im Koboldlager", text)
        lua.execute('Leeroylink.modules.QuestGuide:Show(7)')
        self.assertIn("Quests", lua.eval('Leeroylink.modules.QuestGuide:Report()'))
        lua.execute('Leeroylink.coreCommands.quest("aus")')
        self.assertFalse(g.Leeroylink.modules.QuestGuide.enabled)
        self.assertEqual(list(lua.eval('STUB.testErrors').values()), [])

    def test_missing(self):
        with self.assertRaises(lz.QuestDBError):
            lz.load_questdb(str(TMP / "nowhere"), "_forever_")

    def test_rewards(self):
        """Belohnungen: cMaNGOS-Abzug lesen, Quellen kombinieren, in Quests.lua schreiben, ausschalten."""
        import gzip
        cols = ["entry", "Title", "RewChoiceItemId1", "RewChoiceItemId2", "RewChoiceItemCount1", "RewChoiceItemCount2",
                "RewItemId1", "RewItemCount1", "RewOrReqMoney", "RewMoneyMaxLevel", "RewSpell"]
        dump = "\n".join([
            "CREATE TABLE `quest_template` (", *[f"  `{c}` int NOT NULL DEFAULT '0'," for c in cols], ") ENGINE=MyISAM;",
            "INSERT INTO `quest_template` VALUES (176,'WANTED: \\'Hogger\\'',6085,6084,1,1,0,0,0,525,0),"
            "(7,'Kobold (Camp), Cleanup',0,0,0,0,2589,3,25,102,0),(54,'Report',0,0,0,0,0,0,-10,0,0);",
            "CREATE TABLE `item_template` (", "  `entry` int,", "  `class` int,", "  `name` varchar(1),", "  `Quality` int,", ") ENGINE=MyISAM;",
            "INSERT INTO `item_template` VALUES (6085,4,'Footman Tunic',2),(6084,4,'Stormwind Guard Leggings',2),(2589,7,'Linen Cloth',1),(9,1,'x',0);",
        ])
        path = TMP / "dump.sql.gz"
        with gzip.open(path, "wt", encoding="utf-8") as f:
            f.write(dump + "\n")
        cm = lz.parse_quest_rewards_dump(path)
        self.assertEqual(cm["q"]["176"], {"c": [[6085, 1], [6084, 1]], "mx": 525})
        self.assertEqual(cm["q"]["7"], {"i": [[2589, 3]], "m": 25, "mx": 102})
        self.assertNotIn("54", cm["q"])                                    # Gold verlangt, nicht gegeben
        self.assertEqual(cm["items"]["6085"], ["Footman Tunic", 2])
        self.assertNotIn("9", cm["items"])
        db = self.db
        r = lz.quest_rewards(db, 176, cm, None, "deDE")
        self.assertEqual((r["src"], [x["name"] for x in r["choice"]], r["choice"][0]["q"]), ("cmangos", ["Footman Tunic", "Stormwind Guard Leggings"], 2))
        seen = {"t": 1, "money": 50, "xp": 0, "lvl": 5, "items": [{"id": 1, "n": 1, "q": 3, "name": "Gesehen"}], "choice": [], "spell": ""}
        r = lz.quest_rewards(db, 176, cm, seen, "deDE")
        self.assertEqual((r["src"], r["money"], r["items"][0]["name"]), ("game", 50, "Gesehen"))
        text = lz.quest_data_text(db, "deDE", cm=cm)
        self.assertIn("[7]={", text)
        line7 = next(l for l in text.splitlines() if l.startswith("[7]="))
        self.assertTrue(line7.endswith(",{2589,3},false,25},"), line7)
        self.assertIn('[6085]={"Footman Tunic",2}', text)
        # switched off: an empty data file, no memory in the game
        addon = self.wow / "_forever_" / "Interface" / "AddOns" / "Leeroylink"
        addon.mkdir(parents=True, exist_ok=True)
        (addon / "Leeroylink.toc").write_text("## Version: 0.5.0\n", encoding="utf-8")
        self.assertEqual(lz.write_quest_data(str(self.wow), "_forever_", enabled=False), "off")
        self.assertIn("LeeroylinkQuests = nil", (addon.parent / "Leeroylink_Quests" / "Quests.lua").read_text(encoding="utf-8"))
        self.assertEqual(lz.write_quest_data(str(self.wow), "_forever_", cm=cm), "written")

        # download from GitHub (faked): only once, and again only when the dump changed
        import io
        calls = {"api": 0, "dl": 0}
        listing = [{"name": "README.md", "sha": "a"}, {"name": "ClassicDB_1_12_1_z2815.sql.gz", "sha": "s1",
                    "download_url": "https://raw.githubusercontent.com/cmangos/classic-db/master/Full_DB/ClassicDB_1_12_1_z2815.sql.gz"}]
        qmod = lz.module_of("fetch_reward_data")
        orig_get, orig_open = qmod.http_get, qmod.urllib.request.urlopen

        def fake_get(url, token=""):
            calls["api"] += 1
            self.assertIn("api.github.com/repos/cmangos/classic-db/contents/Full_DB", url)
            return json.dumps(listing).encode()

        def fake_open(req, timeout=0):
            calls["dl"] += 1
            return io.BytesIO(path.read_bytes())
        qmod.http_get, qmod.urllib.request.urlopen = fake_get, fake_open
        try:
            lz.rewards_file("_forever_").unlink(missing_ok=True)
            self.assertTrue(lz.rewards_due("_forever_"))
            data = lz.fetch_reward_data("_forever_")
            self.assertEqual((data["sha"], data["q"]["7"]["m"], calls["dl"]), ("s1", 25, 1))
            self.assertFalse(lz.rewards_due("_forever_"))
            self.assertEqual(lz.load_reward_data("_forever_")["source"], "ClassicDB_1_12_1_z2815.sql.gz")
            lz.fetch_reward_data("_forever_")                                  # same sha: no second download
            self.assertEqual(calls["dl"], 1)
            self.assertIsNone(lz.rewards_file("_retail_"))
        finally:
            qmod.http_get, qmod.urllib.request.urlopen = orig_get, orig_open


def lua_quote_bytes(raw: bytes) -> bytes:
    """Wie WoW Binärdaten in SavedVariables schreibt: \\ddd für Steuerzeichen, \\" und \\\\."""
    out = bytearray(b'"')
    for c in raw:
        if c == 0x22:
            out += b'\\"'
        elif c == 0x5C:
            out += b"\\\\"
        elif c == 10:
            out += b"\\n"
        elif c == 13:
            out += b"\\r"
        elif c < 32 or c == 127:
            out += b"\\%03d" % c
        else:
            out.append(c)
    return bytes(out + b'"')


class SelfUpdateTests(unittest.TestCase):
    """Sichtbares Manager-Update: Download mit Fortschritt, Abbrechen, Datei ersetzen, Warten aufs Ende."""

    def test_download_progress_and_cancel(self):
        import io

        class Resp(io.BytesIO):
            headers = {"Content-Length": str(300_000)}
        mod = lz.module_of("download_with_progress")
        orig = mod.open_url
        mod.open_url = lambda req, timeout=0: Resp(b"x" * 300_000)
        try:
            prog = {}
            self.assertEqual(len(lz.download_with_progress("https://x", progress=prog)), 300_000)
            self.assertEqual((prog["done"], prog["total"]), (300_000, 300_000))
            with self.assertRaises(lz.UpdateCancelled):
                lz.download_with_progress("https://x", progress={"cancel": True})
        finally:
            mod.open_url = orig

    def test_replace_and_wait(self):
        import subprocess as sp
        src, dst = TMP / "new.exe", TMP / "Leeroyzan.exe"
        src.write_bytes(b"MZ new")
        dst.write_bytes(b"MZ old")
        lz.replace_exe(src, dst)
        self.assertEqual(dst.read_bytes(), b"MZ new")
        self.assertFalse(dst.with_name("Leeroyzan.exe.tmp").exists())
        p = sp.Popen([sys.executable, "-c", "import time; time.sleep(0.3)"])
        import threading
        threading.Thread(target=p.wait, daemon=True).start()
        self.assertTrue(lz.wait_for_exit(p.pid, 5))
        self.assertTrue(lz.wait_for_exit(999_999_999 % 4_000_000 + 3_000_000, 0.1))      # gibt es nicht


class AuctionatorTests(unittest.TestCase):
    """Auktionshaus: Auctionator-Preisdatenbank (Version 8, CBOR im Lua-String) lesen und auswerten."""

    def test_read_and_rows(self):
        realm = {"version": 2,
                 "2589": {"a": {"2505": 40, "2506": 12}, "l": {"2506": 34}, "h": {"2505": 60, "2506": 50}, "m": 34},
                 "4306": {"a": {"2506": 3}, "l": [], "h": {"2506": 1000}, "m": 1000},
                 "g:6085:1": {"a": {"2501": 1}, "l": [], "h": {"2501": 25092}, "m": 25092}}
        blob = cbor_encode(realm) + bytes([0, 10, 13, 34, 92, 255])         # Sonderbytes am Ende stören nicht
        text = (b"\r\nAUCTIONATOR_CONFIG = {\r\n[\"x\"] = true,\r\n}\r\nAUCTIONATOR_PRICE_DATABASE = {\r\n"
                b"[\"__dbversion\"] = 8,\r\n[\"Mein \\\"Realm\\\"\"] = " + lua_quote_bytes(blob) + b",\r\n"
                b"[\"Leer\"] = " + lua_quote_bytes(cbor_encode({"version": 2})) + b",\r\n}\r\n"
                b"AUCTIONATOR_VENDOR_PRICE_CACHE = {\r\n[\"2589\"] = 10,\r\n[\"4306\"] = 1200.5,\r\n}\r\n")
        wow = TMP / "auc" / "World of Warcraft"
        sv = wow / "_forever_" / "WTF" / "Account" / "9#1" / "SavedVariables"
        sv.mkdir(parents=True)
        (sv / "Auctionator.lua").write_bytes(text)
        data = lz.load_auctionator(str(wow), "_forever_")
        self.assertEqual(list(data["realms"]), ['Mein "Realm"'])                  # Realm ohne Preise fehlt
        entries = data["realms"]['Mein "Realm"']
        self.assertEqual(set(entries), {"2589", "4306", "g:6085:1"})
        self.assertEqual(data["vendor"], {"2589": 10.0, "4306": 1200.5})
        r = lz.auction_row("2589", entries["2589"], data["vendor"])
        self.assertEqual((r["id"], r["price"], r["last"], r["avail"], r["days"], r["vendor"]), (2589, 34, 2506, 12, 2, 10))
        self.assertEqual(r["hist"], [(2505, 60, 60, 40), (2506, 34, 50, 12)])
        self.assertAlmostEqual(r["change"], 34 / 60 - 1)
        self.assertEqual(lz.auc_day_date(2506).date().isoformat(), "2026-11-11")
        g = lz.auction_row("g:6085:1", entries["g:6085:1"], {})
        self.assertEqual((g["id"], g["change"]), (6085, None))
        cheap = lz.auction_row("4306", entries["4306"], data["vendor"])
        self.assertTrue(cheap["price"] < cheap["vendor"])                             # unter Händlerpreis
        state = {"items": {"4306": {"name": "Seidenstoff", "q": 1}},
                 "chars": {"a": {"bags": {"4306": 5}, "bank": {"4306": 20, "2589": 3}}}}
        self.assertEqual(lz.owned_counts(state), {4306: 25, 2589: 3})
        self.assertEqual(lz.item_details(4306, None, None, state, "deDE")["name"], "Seidenstoff")
        cm = {"all_items": {"2589": ["Linen Cloth", 1, 7, 0, 5, 0, 13]}}
        info = lz.item_details(2589, None, cm, state, "deDE")
        self.assertEqual((info["name"], info["cls"], info["sell"]), ("Linen Cloth", 7, 13))


@unittest.skipIf(run_addon_test is None, "lupa not installed")
class GameDataTests(unittest.TestCase):
    """Spiel -> Leeroyzan: Fehler, Charaktere, Rezepte, Einkaufsliste, Auswertung."""

    @classmethod
    def setUpClass(cls):
        sv = TMP / "sv_state.lua"
        f1, text, now = run_addon_test.run(ROOT / "Leeroylink", 1, None)
        sv.write_text(text, encoding="utf-8")
        f2, text, _ = run_addon_test.run(ROOT / "Leeroylink", 2, sv, start_now=now + 15)
        assert f1 + f2 == 0
        db = lz.parse_lua_savedvariables(text)["LeeroylinkDB"]
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
        self.assertEqual(s["explore"]["zones"]["Rotkammgebirge"]["kills"], 11)  # the stub never changes the zone
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
