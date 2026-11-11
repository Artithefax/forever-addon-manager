-- scenario.lua: a play session driven through the stub. Part 1 runs until a
-- /reload, part 2 continues after it and logs out. Globals from the runner:
-- ADDON_DIR, TESTS_DIR, SV_IN (file with saved variables or nil), PART.
dofile(TESTS_DIR .. "/wow_stub.lua")
if SV_IN then dofile(SV_IN) end
if START_NOW then STUB.now = START_NOW end
if PART == 2 then -- the game state after the reload is what part 1 left behind
	STUB.level, STUB.xp, STUB.xpMax, STUB.money = 22, 200, 11000, 52625
	STUB.skills[2][4], STUB.skills[3][4] = 46, 31
	STUB.bag = { { id = 2589, n = 12 }, { id = 4306, n = 3 } }
end

local failures, checks = 0, 0
local function check(cond, msg)
	checks = checks + 1
	if not cond then failures = failures + 1; print("FAIL: " .. msg) end
end

STUB.loadAddon(ADDON_DIR)
local LZ = _G.Leeroylink
STUB.fire("ADDON_LOADED", "Leeroylink")
STUB.fire("PLAYER_LOGIN")
STUB.fire("PLAYER_ENTERING_WORLD", PART == 1, PART ~= 1)
STUB.advance(2)

local mod = LZ.modules.Session
check(mod.enabled, "session module enabled")
local s = mod:Current()
check(s ~= nil, "session exists")

local ME = STUB.playerGUID
local MINE_PLAYER = 0x511     -- mine | friendly | player
local HOSTILE_NPC = 0xa48     -- npc | hostile | outsider
local PET = 0x1111            -- mine | friendly | pet
local function mob(id, n) return string.format("Creature-0-5096-0-1-%d-00000%04d", id, n) end

local function kill(id, name, n, class, opts)
	opts = opts or {}
	local g = mob(id, n)
	if class then
		STUB.units.target = { name = name, guid = g, class = class, level = opts.level or 22 }
		STUB.fire("PLAYER_TARGET_CHANGED")
	end
	STUB.combat = true
	STUB.cleu("SPELL_CAST_SUCCESS", ME, "Rosenbert", MINE_PLAYER, g, name, HOSTILE_NPC, 78, "Heldenhafter Stoß", 1)
	STUB.cleu("SWING_DAMAGE", ME, "Rosenbert", MINE_PLAYER, g, name, HOSTILE_NPC, 60, -1, 1, nil, nil, nil, false, false, false)
	STUB.cleu("SWING_DAMAGE", ME, "Rosenbert", MINE_PLAYER, g, name, HOSTILE_NPC, 95, -1, 1, nil, nil, nil, true, false, false)
	STUB.cleu("SWING_MISSED", ME, "Rosenbert", MINE_PLAYER, g, name, HOSTILE_NPC, "DODGE", false)
	STUB.cleu("SPELL_DAMAGE", ME, "Rosenbert", MINE_PLAYER, g, name, HOSTILE_NPC, 78, "Heldenhafter Stoß", 1, opts.big or 140, -1, 1, nil, nil, nil, opts.crit or false, false, false)
	STUB.cleu("SWING_DAMAGE", g, name, HOSTILE_NPC, ME, "Rosenbert", MINE_PLAYER, 35, -1, 1, nil, nil, nil, false, false, false)
	STUB.cleu("SWING_MISSED", g, name, HOSTILE_NPC, ME, "Rosenbert", MINE_PLAYER, "PARRY", false)
	if not opts.noBlow then
		STUB.cleu("PARTY_KILL", ME, "Rosenbert", MINE_PLAYER, g, name, HOSTILE_NPC)
	end
	STUB.cleu("UNIT_DIED", nil, nil, 0, g, name, HOSTILE_NPC)
	STUB.combat = false
	if opts.xp then
		STUB.xp = STUB.xp + opts.xp
		STUB.fire("CHAT_MSG_COMBAT_XP_GAIN", string.format("%s dies, you gain %d experience. (+%d exp Rested bonus)", name, opts.xp, math.floor(opts.xp / 2)))
		STUB.fire("PLAYER_XP_UPDATE", "player")
	end
	STUB.advance(1)
end

local function loot(id, n, kind)
	local link = STUB.link(id)
	local msg
	if kind == "pushed" then
		msg = n > 1 and string.format(LOOT_ITEM_PUSHED_SELF_MULTIPLE, link, n) or string.format(LOOT_ITEM_PUSHED_SELF, link)
	elseif kind == "created" then
		msg = n > 1 and string.format(LOOT_ITEM_CREATED_SELF_MULTIPLE, link, n) or string.format(LOOT_ITEM_CREATED_SELF, link)
	else
		msg = n > 1 and string.format(LOOT_ITEM_SELF_MULTIPLE, link, n) or string.format(LOOT_ITEM_SELF, link)
	end
	STUB.fire("CHAT_MSG_LOOT", msg)
end

if PART == 1 then
	check(s.start == STUB.now - 2, "session start time")
	check(s.level.from == 21, "start level")

	-- walking around, then a mount ride
	STUB.speed = 7
	STUB.advance(60)                -- 420 yards on foot
	STUB.mounted, STUB.speed = true, 14
	STUB.advance(30)                -- 420 yards mounted
	STUB.mounted, STUB.speed = false, 0
	STUB.fire("ZONE_CHANGED")
	_G.JumpOrAscendStart(); _G.JumpOrAscendStart(); _G.JumpOrAscendStart()

	-- quest accepted (Classic args: log index, quest ID)
	STUB.questLog = { { title = "Die Bedrohung der Gnolle", level = 20, id = 125 } }
	STUB.fire("QUEST_ACCEPTED", 1, 125)
	STUB.questLog[2] = { title = "Unnützes Zeug", level = 19, id = 300 }
	STUB.fire("QUEST_ACCEPTED", 2, 300)

	-- kills: normal mobs, an elite, a rare; a pet kill and a kill by the group
	kill(434, "Rotkammgnoll", 1, "normal", { xp = 120 })
	kill(434, "Rotkammgnoll", 2, "normal", { xp = 120, big = 412, crit = true })
	kill(434, "Rotkammgnoll", 3, nil, { xp = 120 })
	kill(445, "Rotkammgnoll-Alpha", 4, "elite", { xp = 300 })
	kill(947, "Rohh der Schweigsame", 5, "rare", { xp = 400, noBlow = true })
	-- grey mob killed by the pet only (no XP message)
	local g = mob(1922, 6)
	STUB.cleu("SWING_DAMAGE", "Pet-0-1-2-3-4-5", "Wolf", PET, g, "Graue Hyäne", HOSTILE_NPC, 40, -1, 1, nil, nil, nil, false, false, false)
	STUB.cleu("PARTY_KILL", "Pet-0-1-2-3-4-5", "Wolf", PET, g, "Graue Hyäne", HOSTILE_NPC)
	STUB.cleu("UNIT_DIED", nil, nil, 0, g, "Graue Hyäne", HOSTILE_NPC)
	-- a mob nobody of us touched dies: must not count
	STUB.cleu("UNIT_DIED", nil, nil, 0, mob(500, 7), "Fremder Gnoll", HOSTILE_NPC)

	-- heal, buff, environmental damage
	STUB.cleu("SPELL_HEAL", ME, "Rosenbert", MINE_PLAYER, ME, "Rosenbert", MINE_PLAYER, 2061, "Erste Hilfe", 1, 300, 50, 0, false)
	STUB.cleu("SPELL_AURA_APPLIED", "Player-1", "Priester", 0x514, ME, "Rosenbert", MINE_PLAYER, 1243, "Machtwort: Seelenstärke", 2, "BUFF")
	STUB.cleu("SPELL_AURA_APPLIED", mob(434, 1), "Rotkammgnoll", HOSTILE_NPC, ME, "Rosenbert", MINE_PLAYER, 100, "Schwächung", 1, "DEBUFF")
	STUB.cleu("ENVIRONMENTAL_DAMAGE", nil, nil, 0, ME, "Rosenbert", MINE_PLAYER, "Falling", 80)

	-- loot from a mob, with money
	STUB.units.target = { name = "Rotkammgnoll", guid = mob(434, 3), dead = true }
	STUB.fire("LOOT_OPENED")
	STUB.fire("CHAT_MSG_MONEY", "You loot 25 Copper")
	STUB.money = STUB.money + 25; STUB.fire("PLAYER_MONEY")
	loot(2589, 3)
	loot(1210, 1)
	STUB.fire("LOOT_CLOSED")
	STUB.units.target = nil
	STUB.advance(3)

	-- herb gathering
	STUB.fire("UNIT_SPELLCAST_SUCCEEDED", "player", "Cast-1", 2366)
	STUB.fire("LOOT_OPENED")
	loot(765, 2)
	STUB.fire("LOOT_CLOSED")
	STUB.fire("CHAT_MSG_SKILL", "Your skill in Kräuterkunde has increased to 46.")
	STUB.skills[2][4] = 46
	STUB.advance(3)

	-- crafting
	loot(118, 5, "created")
	STUB.fire("CHAT_MSG_SKILL", "Your skill in Alchemie has increased to 31.")
	STUB.skills[3][4] = 31
	STUB.fire("CHAT_MSG_SYSTEM", "You have learned how to create a new item: Schwacher Heiltrank.")
	STUB.fire("CHAT_MSG_SYSTEM", "You have learned a new spell: Spalten.")

	-- profession window: two recipes, one header
	STUB.trade = { line = { "Alchemie", 31, 75 }, recipes = {
		{ name = "Tränke", type = "header" },
		{ name = "Schwacher Heiltrank", item = 118, reagents = { { id = 765, n = 1 }, { id = 2449, n = 1 } } },
		{ name = "Geringer Heiltrank", item = 858, made = 1, reagents = { { id = 118, n = 2 }, { id = 3371, n = 1 } } },
	} }
	STUB.fire("TRADE_SKILL_SHOW")
	STUB.advance(1)
	local book = LZ.char.recipes["Alchemie"]
	check(book and book.list["Geringer Heiltrank"] and #book.list["Geringer Heiltrank"].r == 2, "recipes scanned")
	check(book and book.list["Tränke"] == nil, "headers skipped")
	check(book and book.list["Schwacher Heiltrank"].item == 118 and book.rank == 31, "recipe product and rank")
	check(LZ.gdb.items[3371] and LZ.gdb.items[3371].name == "Leere Phiole", "reagent name remembered")
	STUB.trade = nil

	-- bags: names for Leeroyzan
	STUB.bag = { { id = 2589, n = 12 }, { id = 4306, n = 3 } }
	LZ.modules.Alts:ScanBags()
	check(LZ.char.bags[2589] == 12 and LZ.gdb.items[4306].name == "Seidenstoff", "bags with item names")

	-- data for Leeroyzan's diagnostics: an error of another addon, a wishlist entry, a report
	STUB.errhandler("Interface\\AddOns\\FooBar\\Core.lua:10: attempt to index a nil value")
	LZ:HandleSlash("merken Details! Damage Meter")
	LZ.gdb.reports.FooBar = { time = STUB.now, version = "2.0" }
	check(LZ.gdb.errorStats.FooBar and LZ.gdb.errorStats.FooBar.total == 1, "foreign error recorded")

	-- discovering a subzone, reputation
	STUB.xp = STUB.xp + 140
	STUB.fire("CHAT_MSG_SYSTEM", "Discovered Steinwacht: 140 experience gained")
	STUB.fire("UI_INFO_MESSAGE", 0, "Discovered Steinwacht: 140 experience gained")
	STUB.fire("PLAYER_XP_UPDATE", "player")
	STUB.factions[2][3] = 3250
	STUB.fire("UPDATE_FACTION")
	STUB.advance(1.5)

	-- newer clients: protected values in combat must neither break nor spam anything
	local secretSkill = "Your skill in Schwerter has increased to 101."
	STUB.secrets[secretSkill] = true
	STUB.secrets[99999] = true
	STUB.combat = true
	local killsBefore = s.kills.n
	STUB.fire("CHAT_MSG_SKILL", secretSkill)
	STUB.fire("CHAT_MSG_COMBAT_XP_GAIN", secretSkill)
	STUB.fire("CHAT_MSG_LOOT", secretSkill)
	STUB.fire("UNIT_SPELLCAST_SUCCEEDED", "player", "Cast-2", 99999)
	-- the real error from Forever: the speed is a secret number in combat
	local footBefore = s.move.foot
	STUB.speed = 4242
	STUB.secrets[4242] = true
	STUB.advance(3)
	STUB.speed = 0
	check(s.move.foot == footBefore and s.time.combat >= 2.5, "secret speed skipped, combat time still counted")
	STUB.skills[5][4] = 101               -- the skill-up still arrives via SKILL_LINES_CHANGED
	STUB.fire("SKILL_LINES_CHANGED")
	STUB.combat = false
	STUB.advance(1.5)
	check(s.prof.skills["Schwerter"].b == 101, "weapon skill-up without the chat line")
	check(s.kills.n == killsBefore + 1, "protected XP line in combat counted as a kill")

	-- WoW Forever has no combat log: kills come from dead units we fought and from the XP lines
	local K = s.kills
	local n0, elite0 = K.n, K.elite
	-- the kill behind the protected XP line above shows up as a dead unit: gets its name, no 2nd kill
	STUB.units.target = { name = "Murloc-Späher", guid = mob(44, 25), level = 21 }
	STUB.combat = true
	STUB.fire("PLAYER_TARGET_CHANGED")
	STUB.advance(1)
	STUB.units.target.dead = true
	STUB.fire("UNIT_HEALTH", "target")
	check(K.n == n0 and K.mobs["Murloc-Späher"] and K.mobs["Murloc-Späher"].n == 1, "protected kill named by its unit")
	STUB.units.target = { name = "Kobold-Tunnelgräber", guid = mob(40, 21), class = "elite", level = 22 }
	STUB.combat = true
	STUB.fire("PLAYER_TARGET_CHANGED")
	STUB.advance(1)                                   -- the ticker sees it fighting us
	STUB.fire("CHAT_MSG_COMBAT_XP_GAIN", "Kobold-Tunnelgräber dies, you gain 50 experience.")
	STUB.units.target.dead = true
	STUB.fire("UNIT_HEALTH", "target")                -- same kill: must not count twice
	STUB.units.nameplate3 = { name = "Graue Ratte", guid = mob(41, 22) }   -- grey, no XP
	STUB.advance(1)
	STUB.units.nameplate3.dead = true
	STUB.fire("NAME_PLATE_UNIT_REMOVED", "nameplate3")
	STUB.units.nameplate3 = nil
	STUB.units.mouseover = { name = "Fremder Kobold", guid = mob(43, 24) }  -- someone else's mob
	STUB.tapDenied = true
	STUB.advance(1)
	STUB.units.mouseover.dead = true
	STUB.advance(1)
	STUB.units.mouseover, STUB.tapDenied = nil, nil
	STUB.combat = false
	STUB.units.target = { name = "Alter Kadaver", guid = mob(42, 23), dead = true }  -- old corpse
	STUB.fire("PLAYER_TARGET_CHANGED")
	STUB.advance(1)
	STUB.units.target = nil
	check(K.n == n0 + 2, "kills without combat log: " .. (K.n - n0))
	check(K.elite == elite0 + 1 and K.mobs["Kobold-Tunnelgräber"].n == 1 and K.mobs["Graue Ratte"].n == 1, "kills without combat log classified")
	local own = 0
	for _, e in pairs(LZ.gdb.errors) do if e.addon == "Leeroylink" then own = own + 1 end end
	check(own == 0, "no errors from protected values")

	-- a handler that fails is logged once, silently, and the others keep working
	local realSub = GetSubZoneText
	GetSubZoneText = function() error("boom") end
	STUB.fire("ZONE_CHANGED")
	STUB.fire("ZONE_CHANGED")
	GetSubZoneText = realSub
	local logged = 0
	for sig, e in pairs(LZ.gdb.errors) do
		if e.addon == "Leeroylink" and e.msg:find("ZONE_CHANGED", 1, true) then
			logged = logged + e.count
			LZ.gdb.errors[sig] = nil          -- planted: do not count as a real error below
		end
	end
	LZ.gdb.errorStats.Leeroylink = nil
	check(logged == 1 and s.recErrors.ZONE_CHANGED == 2, "failing handler logged once: " .. logged)
	for i = #STUB.chat, 1, -1 do if STUB.chat[i]:find("ERROR", 1, true) and STUB.chat[i]:find("boom", 1, true) then check(false, "popup for a session error") end end

	-- quest turn-in at a vendor: XP, money, reward item
	STUB.questID, STUB.questTitle = 125, "Die Bedrohung der Gnolle"
	STUB.fire("QUEST_COMPLETE")
	STUB.xp = STUB.xp + 1250
	STUB.fire("QUEST_TURNED_IN", 125, 1250, 3500)
	STUB.fire("PLAYER_XP_UPDATE", "player")
	STUB.money = STUB.money + 3500; STUB.fire("PLAYER_MONEY")
	loot(10000, 1, "pushed")
	table.remove(STUB.questLog, 1)
	STUB.advance(5)

	-- abandon a quest
	STUB.abandon = "Unnützes Zeug"
	SetAbandonQuest()
	AbandonQuest()

	-- merchant: sell and repair
	STUB.fire("MERCHANT_SHOW")
	STUB.money = STUB.money + 300; STUB.fire("PLAYER_MONEY")
	STUB.money = STUB.money - 1200; STUB.fire("PLAYER_MONEY")
	STUB.fire("MERCHANT_CLOSED")
	STUB.advance(5)

	-- level up: rest of the bar + start of the next one
	STUB.xp = 9900
	STUB.fire("PLAYER_XP_UPDATE", "player")
	STUB.level, STUB.xp, STUB.xpMax = 22, 200, 11000
	STUB.fire("PLAYER_LEVEL_UP", 22)
	STUB.fire("PLAYER_XP_UPDATE", "player")

	-- AFK and resting for a while
	STUB.afk, STUB.resting = true, true
	STUB.advance(120)
	STUB.afk, STUB.resting = false, false

	local z = s.explore.zones["Rotkammgebirge"]
	check(z.kills == 9 and z.xp > 3000 and z.money == 3825, "zone stats: " .. tostring(z.kills) .. "/" .. tostring(z.xp) .. "/" .. tostring(z.money))
	check(s.kills.n == 9, "kills counted (incl. pet kill, excl. foreign): " .. s.kills.n)
	check(s.kills.elite == 2 and s.kills.rare == 1, "elite/rare classified")
	check(s.kills.blows == 5, "killing blows: " .. s.kills.blows)
	check(s.xp.kills == 1110, "xp from kills incl. rested messages: " .. s.xp.kills)
	check(s.xp.quests == 1250, "xp from quests")
	check(s.xp.explore == 140, "xp from exploring (deduplicated): " .. s.xp.explore)
	check(#s.explore.found == 1, "discovery deduplicated")
	check(#s.quests.done == 1 and s.quests.done[1].title == "Die Bedrohung der Gnolle", "quest done with title")
	check(#s.quests.accepted == 2, "quests accepted")
	check(#s.quests.abandoned == 1 and s.quests.abandoned[1].title == "Unnützes Zeug", "quest abandoned")
	check(s.money.gain.loot == 25 and s.money.gain.quest == 3500 and s.money.gain.vendor == 300, "money sources")
	check(s.money.spend.vendor == 1200, "money spent at vendor")
	check(s.prof.gathered.herb and s.prof.gathered.herb[765].n == 2, "herbs gathered")
	check(s.prof.crafted[118].n == 5, "potions crafted")
	check(s.prof.skills["Kräuterkunde"].b == 46, "skill up herbalism")
	check(s.loot.items[2589].n == 3 and s.loot.items[1210].q == 2, "loot items")
	check(s.loot.items[10000].p == 1, "quest reward marked as pushed")
	check(#s.loot.notable == 2, "notable loot (green+): " .. #s.loot.notable)
	check(s.combat.swings.n == 15 and s.combat.swings.dodge == 5 and s.combat.swings.crit == 5, "swings")
	check(s.combat.maxHit == 412 and s.combat.maxHitCrit, "biggest hit")
	check(s.combat.heal == 250 and s.combat.over == 50, "healing")
	check(s.combat.buffs["Machtwort: Seelenstärke"] == 1, "buffs received")
	check(s.combat.env.Falling == 80, "falling damage")
	check(s.level.to == 22 and #s.level.ups == 1, "level up")
	check(s.rep["Sturmwind"] == 250, "reputation")
	check(math.abs(s.move.foot - 420) < 8, "distance on foot: " .. s.move.foot)
	check(math.abs(s.move.mount - 420) < 15, "distance mounted: " .. s.move.mount)
	check(s.move.jumps == 3, "jumps")
	check(s.time.afk >= 118, "afk time: " .. s.time.afk)
	check(#s.spellsLearned == 1 and #s.prof.learned == 1, "learned spell and recipe")
	-- quest companion: own window, chain from Quests.lua, rewards remembered for all characters
	local QG = LZ.modules.QuestGuide
	check(QG and QG.enabled, "quest companion enabled")
	STUB.questID, STUB.reward = 783, { xp = 40, items = { { name = "Rekrutenhemd", n = 1, q = 1, id = 6125 } } }
	STUB.fire("QUEST_DETAIL")                       -- no quest data yet: remember, but no window
	check(LZ.gdb.questRewards[783] and LZ.gdb.questRewards[783].items[1].name == "Rekrutenhemd", "reward remembered without quest data")
	check(not (_G.LeeroylinkQuestGuide and _G.LeeroylinkQuestGuide.shown), "no window without quest data")
	LeeroylinkQuests = { v = 1, src = "QuestieDB test", locale = "deDE", zones = { [12] = "Elwynn Forest" }, factions = { [72] = "Stormwind" },
		q = {
			[783] = { "Die Bedrohung von innen", 1, 1, 12, "A", 7, false, { "Deputy Willem", 12, 48.2, 42.9 }, { "Marshal McBride", 12, 48.9, 41.6 }, { "Sprecht mit Marshal McBride." }, { 72, 50 }, 40 },
			[7] = { "Säuberung im Koboldlager", 2, 1, 12, "A", 15, { 783 }, { "Marshal McBride", 12, 48.9, 41.6 }, { "Marshal McBride", 12, 48.9, 41.6 }, { "Tötet 10 Koboldgezücht." }, { 72, 100 }, 170 },
			[15] = { "Überprüfung der Echokammmine", 3, 1, 12, "A", false, { 7 }, { "Marshal McBride", 12, 48.9, 41.6 }, false, { "Tötet 10 Koboldarbeiter." }, { 72, 100 }, 250 },
			[16] = { "Nur für die Horde", 3, 1, 12, "H", false, { 7 } },
			[17] = { "Briefe nach Goldhain", 3, 1, 12, "A", false, { 7 }, { "Wache Thomas", 12, 74, 72 } },
		} }
	local list, pos = QG:Chain(7)
	check(#list == 3 and list[1] == 783 and list[3] == 15 and pos == 2, "quest chain built: " .. table.concat(list, ","))
	STUB.factionNames = { [72] = "Sturmwind" }
	STUB.fire("QUEST_DETAIL")
	local win = _G.LeeroylinkQuestGuide
	check(win and win.shown, "window opens at the quest giver (own frame)")
	local txt = QG:Text(783)
	for _, want in ipairs({ "Teil 1 von 3", "Säuberung im Koboldlager", "Rekrutenhemd", "40 XP", "Sturmwind +50", "Bis zum Ende der Reihe (3 Quests)", "460 XP", "Bei: Marshal McBride" }) do
		check(txt:find(want, 1, true), "companion text has: " .. want)
	end
	check(QG:Text(7):find("Briefe nach Goldhain", 1, true) and not QG:Text(7):find("Nur für die Horde", 1, true), "branches of my faction only")
	STUB.fire("QUEST_FINISHED")
	check(not win.shown, "auto-opened window closes with the quest giver")
	-- quest log: any quest log selects through SelectQuestLogEntry
	STUB.questLog[#STUB.questLog + 1] = { title = "Säuberung im Koboldlager", level = 2, id = 7 }
	STUB.reward = { money = 75, items = {}, choice = { { name = "Stiefel", q = 2, id = 2000 }, { name = "Handschuhe", q = 2, id = 2001 } } }
	SelectQuestLogEntry(#STUB.questLog)
	check(win.shown and LZ.gdb.questRewards[7] and #LZ.gdb.questRewards[7].choice == 2, "quest log selection shows and remembers rewards")
	check(QG:Text(783):find("Stiefel", 1, true) and QG:Text(783):find("75", 1, true), "remembered rewards of a follow-up quest are shown")
	STUB.completed[783] = true
	check(QG:Text(7):find("(erledigt)", 1, true), "completed quests are marked")
	win:Hide()
	LZ:HandleSlash("quest echokammmine")
	check(win.shown, "/lz quest <name> opens the window")
	STUB.areaNames = { [12] = "Wald von Elwynn" }
	QG:CollectNames()
	check(LZ.gdb.names.zones[12] == "Wald von Elwynn" and LZ.gdb.names.factions[72] == "Sturmwind", "zone and faction names for Leeroyzan")
	table.remove(STUB.questLog)
	STUB.reward = nil

	STUB.fire("PLAYER_LOGOUT")
	check(s.played > 200, "played time: " .. s.played)
else
	-- after /reload: the same session continues
	check(#LZ.gdb.sessionLog == 1, "no new session after reload: " .. #LZ.gdb.sessionLog)
	check(s.resumes == 1, "resume counted")
	check(s.kills.n == 9, "kills kept across reload")

	-- dungeon run with boss, wipe and death
	STUB.instance = { "Die Todesminen", "party", 1, "Normal", 5, 0, false, 36 }
	STUB.group = 5
	STUB.fire("ZONE_CHANGED_NEW_AREA")
	STUB.advance(2)
	check(#s.runs == 1 and s.runs[1].name == "Die Todesminen", "dungeon run opened")
	kill(644, "Rhahk'Zor", 20, "elite", { xp = 500, level = 19 })
	STUB.fire("ENCOUNTER_START", 1, "Edwin VanCleef", 1, 5)
	STUB.fire("ENCOUNTER_END", 1, "Edwin VanCleef", 1, 5, 0)
	STUB.cleu("SWING_DAMAGE", mob(639, 21), "Edwin VanCleef", HOSTILE_NPC, ME, "Rosenbert", MINE_PLAYER, 900, -1, 1, nil, nil, nil, true, false, false)
	STUB.dead = true
	STUB.fire("PLAYER_DEAD")
	STUB.advance(20)
	STUB.dead = false
	STUB.fire("ENCOUNTER_START", 1, "Edwin VanCleef", 1, 5)
	kill(639, "Edwin VanCleef", 22, "elite", { xp = 800 })
	STUB.fire("ENCOUNTER_END", 1, "Edwin VanCleef", 1, 5, 1)
	STUB.advance(5)
	loot(5463, 1)
	STUB.advance(55)
	STUB.instance = { "Westfall", "none", 0, "", 0, 0, false, 0 }
	STUB.fire("ZONE_CHANGED_NEW_AREA")
	STUB.advance(2)

	check(s.runs[1].stop ~= nil, "dungeon run closed")
	check(s.runs[1].kills == 2 and #s.runs[1].bosses == 1 and s.runs[1].wipes == 1 and s.runs[1].deaths == 1, "run stats")
	check(#s.bosses == 1 and s.bosses[1].name == "Edwin VanCleef", "boss recorded")
	check(s.kills.boss == 1, "boss classified by encounter: " .. s.kills.boss)
	check(s.deathN == 1 and s.deaths[1].by == "Edwin VanCleef", "death with killer")
	check(s.time.instance >= 80, "time in instance: " .. s.time.instance)
	local drops = s.runs[1].drops
	check(drops and drops[1].name == "Kristall des Testens" and drops[1].boss == "Edwin VanCleef", "boss loot in the run")

	-- flight path
	STUB.taxi, STUB.speed = true, 30
	STUB.advance(20)
	STUB.taxi, STUB.speed = false, 0
	STUB.fire("PLAYER_LOGOUT")
	check(s.move.taxi > 500, "flight path distance")
	check(s.played > 300, "played time grows across reload: " .. s.played)
	check(s.xpNow == STUB.xp and s.xpMax == STUB.xpMax, "xp progress saved")
end

-- the in-game page and the overview line must render without errors
local okR, report = pcall(mod.Report, mod)
check(okR and type(report) == "string" and report:find("Sitzung", 1, true) ~= nil, "in-game report: " .. tostring(report):sub(1, 200))
local okS, summary = pcall(mod.Summary, mod)
check(okS and type(summary) == "string", "summary line: " .. tostring(summary))
for _, e in pairs(LZ.gdb.errors or {}) do
	if e.addon == "Leeroylink" then check(false, "error logged by the addon: " .. tostring(e.msg)) end
end

print(string.format("RESULT[part %d] %d checks, %d failures", PART, checks, failures))
for _, line in ipairs(STUB.chat) do
	if line:find("ERROR") and not line:find("FooBar", 1, true) then print(line) end -- FooBar's error is planted
end
return failures
