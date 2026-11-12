"""Leeroylink: Jede Lua-Datei benutzt nur bekannte globale Namen (WoW-API, Lua, eigene Daten).

Ein vergessenes „local“ oder ein Tippfehler wird in Lua still zu einer globalen Variable – das fällt erst im
Spiel auf. luac listet alle globalen Zugriffe; neue Namen müssen hier bewusst ergänzt werden.
"""
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LUAC = shutil.which("luac5.1") or shutil.which("luac")

LUA = {"_G", "bit", "date", "debugstack", "geterrorhandler", "ipairs", "math", "next", "pairs", "pcall", "print",
       "select", "seterrorhandler", "setmetatable", "string", "strsplit", "table", "time", "tonumber", "tostring",
       "type", "unpack", "wipe", "xpcall", "hooksecurefunc", "issecretvalue"}
WOW = set("""AcceptQuest C_DateAndTime C_Item C_Map C_QuestLog C_Spell C_Timer CanMerchantRepair
CombatLogGetCurrentEventInfo CompleteQuest CreateFrame DoEmote Enum GameTooltip GetAbandonQuestName
GetAuctionItemInfo GetBuildInfo GetCursorPosition GetFactionInfo GetFactionInfoByID GetGuildInfo GetInstanceInfo
GetItemInfo GetLocale GetMoney GetNormalizedRealmName GetNumAuctionItems GetNumFactions GetNumGroupMembers
GetNumPartyMembers GetNumQuestChoices GetNumQuestLogEntries GetNumRaidMembers GetNumSavedInstances
GetNumSkillLines GetProfessionInfo GetProfessions GetQuestID GetQuestLogTitle GetQuestResetTime GetQuestReward
GetRealZoneText GetRealmName GetRepairAllCost GetSavedInstanceInfo GetSkillLineInfo GetSpecialization
GetSpecializationRole GetSubZoneText GetTime GetTitleText GetUnitSpeed GetZoneText InCombatLockdown IsFishingLoot
IsFlying IsInInstance IsMounted IsQuestCompletable IsResting IsShiftKeyDown IsSwimming Minimap RAID_CLASS_COLORS
ReloadUI RepairAllItems RequestRaidInfo Screenshot SlashCmdList TooltipDataProcessor UIErrorsFrame UIParent
UISpecialFrames UnitAffectingCombat UnitCanAttack UnitClass UnitClassification UnitExists UnitFactionGroup
UnitGUID UnitIsAFK UnitIsDead UnitIsDeadOrGhost UnitIsPlayer UnitIsTapDenied UnitLevel UnitName UnitOnTaxi
UnitRace UnitXP UnitXPMax""".split())
OWN = {"LeeroylinkImport", "LeeroylinkQuests"}         # Data.lua (Leeroyzan) und die Questdaten
SETTERS = {"Data.lua": {"LeeroylinkImport"}}            # nur diese Dateien dürfen globale Variablen anlegen


@unittest.skipUnless(LUAC, "luac not installed")
class LuaGlobalsTests(unittest.TestCase):
    def test_only_known_globals(self):
        allowed = LUA | WOW | OWN
        bad = []
        for path in sorted((ROOT / "Leeroylink").rglob("*.lua")):
            out = subprocess.run([LUAC, "-p", "-l", str(path)], capture_output=True, text=True, check=True).stdout
            for kind, name in re.findall(r"([GS])ETGLOBAL.*; (\S+)$", out, re.M):
                rel = path.relative_to(ROOT / "Leeroylink").as_posix()
                if kind == "S" and name not in SETTERS.get(rel, set()):
                    bad.append(f"{rel}: setzt {name}")
                elif kind == "G" and name not in allowed:
                    bad.append(f"{rel}: liest {name}")
        self.assertEqual(sorted(set(bad)), [])


if __name__ == "__main__":
    unittest.main()
