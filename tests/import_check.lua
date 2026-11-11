-- import_check.lua: loads the addon with a Data.lua written by Leeroyzan and checks
-- that every module accepts it. Globals from the runner: ADDON_DIR, TESTS_DIR, DATA_FILE.
dofile(TESTS_DIR .. "/wow_stub.lua")
local failures = 0
local function check(cond, msg) if not cond then failures = failures + 1; print("FAIL: " .. msg) end end

local NS = {}
for line in io.lines(ADDON_DIR .. "/Leeroylink.toc") do
	line = line:gsub("\r", "")
	if line:match("%S") and not line:match("^#") then
		local path = (line == "Data.lua") and DATA_FILE or (ADDON_DIR .. "/" .. line:gsub("\\", "/"))
		assert(loadfile(path))("Leeroylink", NS)
	end
end
local LZ = _G.Leeroylink
LeeroylinkDB = { global = { wishlist = { ["details! damage meter"] = { name = "Details! Damage Meter", added = 1 },
	["weakauras"] = { name = "WeakAuras", added = 2 } } } }
STUB.fire("ADDON_LOADED", "Leeroylink")
STUB.fire("PLAYER_LOGIN")
STUB.fire("PLAYER_ENTERING_WORLD", true, false)
STUB.advance(2)
local imp = LZ.import
check(imp ~= nil, "import accepted: " .. tostring(LZ.importProblem))
if imp then
	check(#imp.updates == 1 and imp.updates[1].name == "Questie" and imp.updates[1].available == "9.3.0", "updates")
	check(#imp.whatsnew == 1 and imp.whatsnew[1].lines[1] == "Neue Quests für Forever", "whatsnew")
	check(#imp.profiles == 1 and imp.profiles[1].name == "Raid" and #imp.profiles[1].enable == 2, "profiles")
	check(#imp.knownIssues == 1, "known issues")
	check(#imp.managed == 2, "managed folders")
	check(imp.questGuide and imp.questGuide.on == false, "quest companion switch from Leeroyzan")
end
-- switched off in Leeroyzan after the last change in the game: the module stays off
check(not LZ.modules.QuestGuide.enabled and LeeroylinkDB.settings.modules.QuestGuide == false, "quest companion switched off by Leeroyzan")
local names = LZ.modules.Profiles:Names()
check(#names == 1 and names[1].source == "leeroyzan", "profile from Leeroyzan listed")
check(LeeroylinkDB.global.wishlist["details! damage meter"] == nil, "installed wishlist entry resolved")
check(LeeroylinkDB.global.wishlist["weakauras"] ~= nil, "open wishlist entry kept")
local ok, rep = pcall(LZ.modules.Updates.Report, LZ.modules.Updates)
check(ok and rep:find("Questie", 1, true) and rep:find("Neue Quests", 1, true), "updates page shows it: " .. tostring(rep))
for _, line in ipairs(STUB.chat) do if line:find("ERROR") then print(line); failures = failures + 1 end end
print("RESULT[import] failures " .. failures)
return failures
