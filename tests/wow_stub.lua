-- wow_stub.lua: a small imitation of the WoW client API, enough to load
-- Leeroylink and drive it with events. Not a full emulator: every value
-- comes from the STUB table so tests can change the game state.
STUB = {
	now = 1794265109, clock = 1000.0,
	frames = {}, timers = {}, tickers = {}, hooks = {},
	level = 21, xp = 1000, xpMax = 10000, money = 50000,
	zone = "Rotkammgebirge", subzone = "Seenhain", speed = 0,
	swimming = false, mounted = false, taxi = false, dead = false, afk = false, combat = false, resting = false,
	instance = { "", "none", 0, "", 0, 0, false, 0 },
	units = {}, cleu = {}, group = 0,
	skills = {
		{ "Berufe", true, 0, 0, 0, 0, 0 },
		{ "Kräuterkunde", false, 0, 45, 0, 0, 75 },
		{ "Alchemie", false, 0, 30, 0, 0, 75 },
		{ "Waffenfertigkeiten", true, 0, 0, 0, 0, 0 },
		{ "Schwerter", false, 0, 100, 0, 0, 105 },
		{ "Sprachen", true, 0, 0, 0, 0, 0 },
		{ "Gemeinsprache", false, 0, 300, 0, 0, 300 },
	},
	questLog = {},
	items = {
		[2589] = { "Leinenstoff", 1, 13 }, [765] = { "Friedensblume", 1, 10 }, [2449] = { "Wilddornrose", 1, 20 },
		[118] = { "Schwacher Heiltrank", 1, 5 }, [1210] = { "Schattenedelstein", 2, 250 }, [6256] = { "Angel", 1, 4 },
		[4306] = { "Seidenstoff", 1, 150 }, [10000] = { "Klinge der Tests", 3, 5000 },
		[858] = { "Geringer Heiltrank", 1, 20 }, [3371] = { "Leere Phiole", 1, 1 }, [5463] = { "Kristall des Testens", 3, 900 },
	},
	spells = { [2366] = "Kräutersammeln", [2575] = "Bergbau", [8613] = "Kürschnerei" },
	abandon = nil, questID = 0, questTitle = "",
	errhandler = nil, chat = {},
}

-- Newer clients: some values are "secret" in combat. Tests mark them in STUB.secrets.
STUB.secrets = {}
function issecretvalue(v) return v ~= nil and STUB.secrets[v] == true end

-- reputation list (Classic API)
STUB.factions = { { "Allianz", true, 0 }, { "Sturmwind", false, 3000 }, { "Eisenschmiede", false, 1200 } }
function GetNumFactions() return #STUB.factions end
function GetFactionInfo(i)
	local f = STUB.factions[i]
	return f[1], "", 4, 0, 6000, f[3], false, true, f[2], false, f[2] and false or true
end

-- Lua 5.1 has no bit library; WoW does.
bit = {
	band = function(a, b)
		local r, m = 0, 1
		a, b = math.floor(a or 0), math.floor(b or 0)
		while a > 0 and b > 0 do
			if a % 2 == 1 and b % 2 == 1 then r = r + m end
			a, b, m = math.floor(a / 2), math.floor(b / 2), m * 2
		end
		return r
	end,
}

local realTime = time
time = function(t) if t then return realTime(t) end return STUB.now end
GetTime = function() return STUB.clock end
date = os.date -- WoW offers os.date as the global date()

function strsplit(sep, str)
	local out = {}
	for part in (str .. sep):gmatch("(.-)" .. sep:gsub("%p", "%%%0")) do out[#out + 1] = part end
	return unpack(out)
end
function wipe(t) for k in pairs(t) do t[k] = nil end return t end
function tContains(t, v) for _, x in ipairs(t) do if x == v then return true end end return false end

function hooksecurefunc(a, b, c)
	local tbl, name, fn = _G, a, b
	if type(a) == "table" then tbl, name, fn = a, b, c end
	local orig = tbl[name]
	tbl[name] = function(...)
		local r = { orig(...) }
		fn(...)
		return unpack(r)
	end
end

-- frames ---------------------------------------------------------------
local Frame = {}
Frame.__index = function(t, k)
	local v = rawget(Frame, k)
	if v ~= nil then return v end
	return function() return nil end -- every other widget method: no-op
end
function Frame:SetScript(name, fn) self.scripts[name] = fn end
function Frame:GetScript(name) return self.scripts[name] end
function Frame:HookScript(name, fn) self.scripts[name] = fn end
function Frame:RegisterEvent(e) self.events[e] = true end
function Frame:RegisterUnitEvent(e, unit) self.events[e] = unit or true end
function Frame:UnregisterEvent(e) self.events[e] = nil end
function Frame:UnregisterAllEvents() self.events = {} end
function Frame:IsShown() return self.shown end
function Frame:Show() self.shown = true end
function Frame:Hide() self.shown = false end
function Frame:GetWidth() return 140 end
function Frame:GetHeight() return 140 end
function Frame:GetStringHeight() return 12 end
function Frame:GetCenter() return 0, 0 end
function Frame:GetEffectiveScale() return 1 end
function Frame:GetChecked() return false end
function Frame:CreateFontString() return CreateFrame("FontString") end
function Frame:CreateTexture() return CreateFrame("Texture") end

function CreateFrame(kind, name)
	local f = setmetatable({ kind = kind, scripts = {}, events = {}, shown = true }, Frame)
	table.insert(STUB.frames, f)
	if name then _G[name] = f end
	return f
end
UIParent = CreateFrame("Frame")
Minimap = CreateFrame("Frame")
GameTooltip = CreateFrame("GameTooltip")
DEFAULT_CHAT_FRAME = { AddMessage = function(_, msg) table.insert(STUB.chat, msg) end }
UIErrorsFrame = { AddMessage = function() end }
UISpecialFrames = {}
SlashCmdList = {}

function STUB.fire(event, ...)
	for _, f in ipairs(STUB.frames) do
		local reg = f.events[event]
		if reg and f.scripts.OnEvent then
			local unit = ...
			if reg == true or reg == unit then f.scripts.OnEvent(f, event, ...) end
		end
	end
end

function STUB.cleu(sub, srcGUID, srcName, srcFlags, dstGUID, dstName, dstFlags, ...)
	STUB.cleuArgs = { STUB.now, sub, false, srcGUID, srcName, srcFlags, 0, dstGUID, dstName, dstFlags, 0, ... }
	STUB.fire("COMBAT_LOG_EVENT_UNFILTERED")
end
function CombatLogGetCurrentEventInfo() return unpack(STUB.cleuArgs, 1, 30) end

-- timers -----------------------------------------------------------------
C_Timer = {
	After = function(sec, fn) table.insert(STUB.timers, { at = STUB.clock + sec, fn = fn }) end,
	NewTicker = function(sec, fn)
		local t = { every = sec, nextAt = STUB.clock + sec, fn = fn }
		t.Cancel = function() t.cancelled = true end
		table.insert(STUB.tickers, t)
		return t
	end,
}

-- Moves the clock forward in steps of 0.25 s, firing tickers and timers.
function STUB.advance(sec)
	local stop = STUB.clock + sec
	while STUB.clock < stop - 1e-9 do
		local step = math.min(0.25, stop - STUB.clock)
		STUB.clock = STUB.clock + step
		STUB.now = STUB.now + step
		for _, t in ipairs(STUB.tickers) do
			if not t.cancelled and STUB.clock >= t.nextAt - 1e-9 then
				t.nextAt = t.nextAt + t.every
				t.fn()
			end
		end
		local due = {}
		for i = #STUB.timers, 1, -1 do
			if STUB.clock >= STUB.timers[i].at then table.insert(due, table.remove(STUB.timers, i)) end
		end
		for i = #due, 1, -1 do due[i].fn() end
	end
	STUB.now = math.floor(STUB.now + 0.5)
end

-- player and world --------------------------------------------------------
local PLAYER_GUID = "Player-5096-0ABCDEF1"
STUB.playerGUID = PLAYER_GUID
function UnitName(u)
	if u == "player" then return "Rosenbert" end
	local d = STUB.units[u]; return d and d.name
end
function UnitGUID(u)
	if u == "player" then return PLAYER_GUID end
	local d = STUB.units[u]; return d and d.guid
end
function UnitExists(u) return u == "player" or STUB.units[u] ~= nil end
function UnitIsPlayer(u) return u == "player" or (STUB.units[u] and STUB.units[u].player) or false end
function UnitIsDead(u) if u == "player" then return STUB.dead end local d = STUB.units[u]; return d and d.dead or false end
function UnitIsDeadOrGhost(u) return u == "player" and STUB.dead end
function UnitIsTapDenied(u) return u ~= "player" and STUB.tapDenied or false end
function UnitCanAttack(a, u) return u ~= "player" and STUB.units[u] ~= nil end
function UnitClassification(u) local d = STUB.units[u]; return d and d.class or "normal" end
function UnitClass() return "Krieger", "WARRIOR" end
function UnitRace() return "Mensch", "Human" end
function UnitFactionGroup() return "Alliance" end
function UnitLevel(u) if u == "player" then return STUB.level end local d = STUB.units[u]; return d and d.level or 1 end
function UnitXP() return STUB.xp end
function UnitXPMax() return STUB.xpMax end
function UnitAffectingCombat() return STUB.combat end
function UnitIsAFK() return STUB.afk end
function UnitOnTaxi() return STUB.taxi end
function IsResting() return STUB.resting end
function IsSwimming() return STUB.swimming end
function IsMounted() return STUB.mounted end
function IsFlying() return false end
function IsShiftKeyDown() return false end
function GetUnitSpeed() return STUB.speed end
function GetMoney() return STUB.money end
function GetRealmName() return "CAMWOWEU5Realm2PvP" end
function GetNormalizedRealmName() return "CAMWOWEU5Realm2PvP" end
function GetRealZoneText() return STUB.zone end
function GetZoneText() return STUB.zone end
function GetSubZoneText() return STUB.subzone end
function GetGuildInfo() return nil end
function IsInInstance() return STUB.instance[2] ~= "none", STUB.instance[2] end
function GetInstanceInfo() return unpack(STUB.instance) end
function GetNumGroupMembers() return STUB.group end
function GetLocale() return "deDE" end
function GetBuildInfo() return "1.60.1", "12345", "Nov 1 2026", 16001 end
function ReloadUI() end
function Screenshot() end
function IsFishingLoot() return STUB.fishing or false end

function GetNumSkillLines() return #STUB.skills end
function GetSkillLineInfo(i) return unpack(STUB.skills[i]) end

function GetNumQuestLogEntries() return #STUB.questLog end
function GetQuestLogTitle(i)
	local q = STUB.questLog[i]
	return q.title, q.level, nil, false, false, false, nil, q.id
end
function GetQuestID() return STUB.questID end
function GetTitleText() return STUB.questTitle end
function SetAbandonQuest() end
function GetAbandonQuestName() return STUB.abandon end
function AbandonQuest() STUB.abandon = nil end
function JumpOrAscendStart() end

-- profession window (Classic trade skill API)
STUB.trade = nil  -- { line = {name, rank, max}, recipes = { { name, type, item, made, reagents = { {id, n} } } } }
local function tradeRecipe(i) return STUB.trade and STUB.trade.recipes[i] end
function GetTradeSkillLine() if STUB.trade then return unpack(STUB.trade.line) end return "UNKNOWN", 0, 0 end
function GetNumTradeSkills() return STUB.trade and #STUB.trade.recipes or 0 end
function GetTradeSkillInfo(i) local r = tradeRecipe(i); if r then return r.name, r.type or "optimal", 0, true end end
function GetTradeSkillItemLink(i) local r = tradeRecipe(i); return r and r.item and STUB.link(r.item) end
function GetTradeSkillNumMade(i) local r = tradeRecipe(i); return r and (r.made or 1), r and (r.made or 1) end
function GetTradeSkillNumReagents(i) local r = tradeRecipe(i); return r and r.reagents and #r.reagents or 0 end
function GetTradeSkillReagentInfo(i, k)
	local g = tradeRecipe(i).reagents[k]
	return STUB.items[g.id][1], "icon", g.n, 0
end
function GetTradeSkillReagentItemLink(i, k) return STUB.link(tradeRecipe(i).reagents[k].id) end

-- bags: bag 0 holds STUB.bag = { itemID, ... }
STUB.bag = {}
function GetContainerNumSlots(bag) return bag == 0 and #STUB.bag or 0 end
function GetContainerItemInfo(bag, slot)
	local e = STUB.bag[slot]
	if not e then return nil end
	return "icon", e.n, false, STUB.items[e.id][2], false, false, STUB.link(e.id), false, false, e.id
end

function GetItemInfo(x)
	local id = type(x) == "number" and x or tonumber(tostring(x):match("item:(%d+)"))
	local it = id and STUB.items[id]
	if not it then return nil end
	return it[1], "link", it[2], 10, 1, "Misc", "Misc", 20, "", "icon", it[3]
end
function GetSpellInfo(id) return STUB.spells[id] end

-- addon API (old global style)
local ADDONS = { { name = "Leeroylink", title = "Leeroylink", version = "0.2.0" } }
function GetNumAddOns() return #ADDONS end
function GetAddOnInfo(i)
	local a = type(i) == "number" and ADDONS[i] or ADDONS[1]
	return a.name, a.title, "", true, "", "INSECURE"
end
function GetAddOnMetadata(name, field) if field == "Version" then return "0.2.0" end end
function IsAddOnLoaded() return true end
function IsAddOnLoadOnDemand() return false end
function GetAddOnDependencies() return end
function GetAddOnEnableState() return 2 end
function GetAddOnMemoryUsage() return 100 end
function UpdateAddOnMemoryUsage() end

local defaultErrors = function(e) table.insert(STUB.chat, "ERROR: " .. tostring(e)) end
STUB.defaultErrors = defaultErrors
function geterrorhandler() return STUB.errhandler or defaultErrors end
function seterrorhandler(h) STUB.errhandler = h end
function debugstack() return "" end

-- global strings (English client)
COMBATLOG_XPGAIN_FIRSTPERSON = "%s dies, you gain %d experience."
COMBATLOG_XPGAIN_FIRSTPERSON_UNNAMED = "You gain %d experience."
LOOT_ITEM_SELF = "You receive loot: %s."
LOOT_ITEM_SELF_MULTIPLE = "You receive loot: %sx%d."
LOOT_ITEM_PUSHED_SELF = "You receive item: %s."
LOOT_ITEM_PUSHED_SELF_MULTIPLE = "You receive item: %sx%d."
LOOT_ITEM_CREATED_SELF = "You create: %s."
LOOT_ITEM_CREATED_SELF_MULTIPLE = "You create: %sx%d."
LOOT_ITEM = "%s receives loot: %s."
LOOT_ITEM_MULTIPLE = "%s receives loot: %sx%d."
SKILL_RANK_UP = "Your skill in %s has increased to %d."
ERR_LEARN_RECIPE_S = "You have learned how to create a new item: %s."
ERR_LEARN_SPELL_S = "You have learned a new spell: %s."
ERR_LEARN_ABILITY_S = "You have learned a new ability: %s."
ERR_ZONE_EXPLORED = "Discovered: %s"
ERR_ZONE_EXPLORED_XP = "Discovered %s: %d experience gained"
FACTION_STANDING_INCREASED = "Your %s reputation has increased by %d."
FACTION_STANDING_DECREASED = "Your %s reputation has decreased by %d."
NUM_BAG_SLOTS = 4

function STUB.link(id, q)
	local colors = { [0] = "9d9d9d", "ffffff", "1eff00", "0070dd", "a335ee" }
	local it = STUB.items[id]
	q = q or it[2]
	return "|cff" .. colors[q] .. "|Hitem:" .. id .. "::::::::20:::::|h[" .. it[1] .. "]|h|r"
end

-- Loads the addon files in .toc order.
function STUB.loadAddon(dir)
	local NS = {}
	for line in io.lines(dir .. "/Leeroylink.toc") do
		line = line:gsub("\r", "")
		if line:match("%S") and not line:match("^#") then
			local chunk = assert(loadfile(dir .. "/" .. line:gsub("\\", "/")))
			chunk("Leeroylink", NS)
		end
	end
	return NS
end
