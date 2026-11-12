-- Session: records a whole play session (login -> logout) for Leeroyzan:
-- play time and date, experience and gold (with sources), quests, loot, combat
-- (damage, healing, deaths, swings, spells, buffs), distance and steps,
-- professions, exploration, dungeons/raids and kills (normal, elite, rare, boss).
--
-- A /reload or logging back in with the same character within a few minutes
-- continues the same session. Sessions are stored in
-- LeeroylinkDB.global.sessionLog (newest MAX_SESSIONS); WoW writes that file on
-- logout and Leeroyzan then imports, keeps and summarizes them.
local ADDON, LZ = ...
local T, P, C = LZ.T, LZ.P, LZ.C

local mod = LZ:NewModule("Session", {
	group = "link",
	title = { "Sitzungsprotokoll", "Session log" },
	desc = {
		"Zeichnet jede Spielsitzung auf (Zeit, Erfahrung, Gold, Quests, Beute, Kampf, Strecke, Berufe, Entdeckungen, Instanzen, Kills). Nach dem Ausloggen übernimmt Leeroyzan die Sitzung und fasst sie zusammen.",
		"Records every play session (time, experience, gold, quests, loot, combat, distance, professions, discoveries, instances, kills). After you log out, Leeroyzan imports the session and summarizes it.",
	},
	defaults = { resumeMinutes = 15 },
})

local VERSION = 2
local MAX_SESSIONS = 30
local TICK = 0.5
local YARD_M = 0.9144
local LIMIT = {
	spells = 40, heals = 25, casts = 40, auras = 30, takenBy = 20, mobs = 80, items = 150, notable = 50,
	accepted = 80, done = 80, abandoned = 40, deaths = 30, found = 60, zones = 30, subs = 25, skills = 40,
	crafted = 40, gathered = 40, learned = 40, runs = 20, bosses = 40, rep = 20, ups = 20, levelUps = 20,
}

local s = nil       -- current session (a table inside the saved variables)
local rt = {}       -- runtime state that is not saved
local zoneEntry     -- defined with the zone helpers below
local band = bit and bit.band

-- Newer clients hand addons some values in combat only as "secret values" (chat lines,
-- combat log, unit data). Reading them throws an error, so they are skipped here.
local isSecret = _G.issecretvalue or function() return false end
local function text(v) return type(v) == "string" and not isSecret(v) end

----------------------------------------------------------------------
-- Helpers
----------------------------------------------------------------------
local function now() return GetTime and GetTime() or 0 end
local function within(key, sec) return rt[key] and (now() - rt[key]) <= sec end

local function add(t, k, v)
	if k == nil then return end
	t[k] = (t[k] or 0) + (v or 1)
end

local function push(list, entry, max)
	list[#list + 1] = entry
	while max and #list > max do table.remove(list, 1) end
end

-- Blizzard format string -> Lua pattern; also understands "%1$s" style arguments.
local function pattern(fmt, open)
	if type(fmt) ~= "string" or fmt == "" then return nil end
	local p = fmt:gsub("%%%d%$", "%%")
	p = p:gsub("([%(%)%.%%%+%-%*%?%[%]%^%$])", "%%%1")
	p = p:gsub("%%%%s", "(.+)")
	p = p:gsub("%%%%d", "(%%d+)")
	return "^" .. p .. (open and "" or "$")
end

local function zoneName()
	local z = (GetRealZoneText and GetRealZoneText()) or (GetZoneText and GetZoneText()) or ""
	if z == "" then z = "?" end
	return z
end

local function npcID(guid)
	if type(guid) ~= "string" then return nil end
	local kind, _, _, _, _, id = strsplit("-", guid)
	if kind == "Creature" or kind == "Vehicle" then return tonumber(id) end
end

local function itemName(link)
	return type(link) == "string" and link:match("%[(.-)%]") or nil
end

-- Keeps only the `max` biggest entries of a map. `value(v)` returns the sort value.
local function trimMap(t, max, value)
	local keys = {}
	for k in pairs(t) do keys[#keys + 1] = k end
	if #keys <= max then return end
	table.sort(keys, function(a, b) return value(t[a]) > value(t[b]) end)
	for i = max + 1, #keys do t[keys[i]] = nil end
end
local function byNumber(v) return type(v) == "number" and v or 0 end
local function byFirst(v) return type(v) == "table" and (v[1] or 0) or 0 end
local function byN(v) return type(v) == "table" and (v.n or 0) or 0 end
local function bySec(v) return type(v) == "table" and (v.sec or 0) or 0 end

local function round(x, digits)
	local m = 10 ^ (digits or 0)
	return math.floor((x or 0) * m + 0.5) / m
end

-- Thousands separator in the UI language.
function LZ.Num(n)
	n = math.floor((tonumber(n) or 0) + 0.5)
	local neg = n < 0
	local str = tostring(neg and -n or n)
	local sep = LZ.lang == "de" and "." or ","
	str = str:reverse():gsub("(%d%d%d)", "%1" .. sep):reverse()
	if str:sub(1, 1) == sep then str = str:sub(2) end
	return (neg and "-" or "") .. str
end

local function km(yards)
	local v = (yards or 0) * YARD_M / 1000
	local str = string.format("%.1f", v)
	if LZ.lang == "de" then str = str:gsub("%.", ",") end
	return str .. " km"
end

----------------------------------------------------------------------
-- Several handlers per event inside this module
----------------------------------------------------------------------
local handlers = {}
local failed = {}   -- event -> number of errors this login (the first one goes to the error log, silently)

local function onError(event, err)
	local n = (failed[event] or 0) + 1
	failed[event] = n
	if s then
		s.recErrors = s.recErrors or {}
		s.recErrors[event] = (s.recErrors[event] or 0) + 1
	end
	if n == 1 then
		local log = LZ.modules.ErrorLog
		if log and log.enabled and log.Record then
			-- the path in front lets the error log attribute it to this addon
			pcall(log.Record, log, "Interface\\AddOns\\Leeroylink\\Modules\\Session.lua (" .. event .. "): " .. tostring(err),
				_G.debugstack and debugstack(2) or nil)
		end
	end
end

local function on(event, fn)
	local list = handlers[event]
	if not list then
		list = {}
		handlers[event] = list
		mod:Event(event, function(self, ev, ...)
			if not s then return end
			for i = 1, #list do
				local ok, err = pcall(list[i], ...)
				if not ok then onError(event, err) end
			end
		end)
	end
	list[#list + 1] = fn
end

----------------------------------------------------------------------
-- Session table
----------------------------------------------------------------------
-- shared with SessionKills.lua and SessionCombat.lua (loaded right after this file)
local I = { rt = rt, band = band }
LZ.SessionInternal = I
local sessionListeners = {}
function I.onSession(fn) sessionListeners[#sessionListeners + 1] = fn end
local function setSession(new)
	s = new
	for i = 1, #sessionListeners do sessionListeners[i](new) end
end

local function playerLevel() return (UnitLevel and UnitLevel("player")) or 0 end
local function money() return (GetMoney and GetMoney()) or 0 end

local function newSession()
	local t = time()
	local _, class = UnitClass("player")
	local _, race = UnitRace("player")
	local lvl, m = playerLevel(), money()
	return {
		v = VERSION, id = LZ.charKey .. ":" .. t, char = LZ.charKey,
		name = UnitName and UnitName("player") or nil,
		realm = GetRealmName and GetRealmName() or nil,
		class = class, race = race, faction = UnitFactionGroup and UnitFactionGroup("player") or nil,
		addon = LZ.version, interface = C.InterfaceVersion(), lang = LZ.lang,
		start = t, stop = t, saved = t, played = 0, resumes = 0,
		time = { combat = 0, afk = 0, rest = 0, dead = 0, taxi = 0, instance = 0 },
		level = { from = lvl, to = lvl, ups = {} },
		xp = { total = 0, kills = 0, quests = 0, explore = 0, other = 0, killsN = 0, combat = 0 },
		money = { start = m, stop = m, income = 0, spent = 0, gain = {}, spend = {} },
		quests = { accepted = {}, done = {}, abandoned = {} },
		loot = { n = 0, value = 0, q = {}, items = {}, notable = {} },
		combat = {
			dmg = 0, dmgPet = 0, hits = 0, crits = 0, maxHit = 0, spells = {},
			heal = 0, over = 0, maxHeal = 0, heals = {},
			taken = 0, maxTaken = 0, takenBy = {}, healTaken = 0, env = {},
			swings = { n = 0, hit = 0, crit = 0, miss = 0, dodge = 0, parry = 0, block = 0, glance = 0 },
			swingsTaken = { n = 0, hit = 0, crit = 0, miss = 0, dodge = 0, parry = 0, block = 0 },
			casts = {}, castN = 0, buffs = {}, buffN = 0, debuffs = {}, debuffN = 0, buffsGiven = 0,
			interrupts = 0, dispels = 0,
		},
		kills = { n = 0, normal = 0, elite = 0, rare = 0, rareelite = 0, boss = 0, players = 0, blows = 0, mobs = {} },
		bosses = {}, wipes = 0,
		deaths = {}, deathN = 0,
		move = { foot = 0, mount = 0, swim = 0, taxi = 0, ghost = 0, steps = 0, jumps = 0 },
		prof = { skills = {}, ups = 0, crafted = {}, gathered = {}, learned = {} },
		spellsLearned = {},
		explore = { found = {}, zones = {} },
		runs = {},
		rep = {},
	}
end

----------------------------------------------------------------------
-- Experience
----------------------------------------------------------------------
local function readXP()
	if UnitXP and UnitXPMax then return UnitXP("player") or 0, UnitXPMax("player") or 0 end
	return 0, 0
end

on("PLAYER_XP_UPDATE", function(unit)
	if unit and unit ~= "player" then return end
	local cur, max = readXP()
	if isSecret(cur) or isSecret(max) then return end
	local gained
	if max == rt.xpMax and cur >= rt.xp then
		gained = cur - rt.xp
	else
		gained = (rt.xpMax - rt.xp) + cur -- level-up: rest of the old bar + start of the new one
	end
	rt.xp, rt.xpMax = cur, max
	if gained <= 0 then return end
	s.xp.total = s.xp.total + gained
	-- XP in combat (or right after) is kill XP – also when the chat line was protected
	if (UnitAffectingCombat and UnitAffectingCombat("player")) or within("combatEnd", 4) then
		s.xp.combat = (s.xp.combat or 0) + gained
	end
	local z = zoneEntry(zoneName())
	z.xp = (z.xp or 0) + gained
	if rt.run then rt.run.xp = (rt.run.xp or 0) + gained end
end)

on("PLAYER_LEVEL_UP", function(level)
	level = tonumber(level) or playerLevel()
	push(s.level.ups, { l = level, t = time() }, LIMIT.levelUps)
	s.level.to = level
end)

on("CHAT_MSG_COMBAT_XP_GAIN", function(msg)
	if not text(msg) then
		-- protected line: in a fight that is (almost always) the XP of a kill
		if msg ~= nil and I.killFromXP and ((UnitAffectingCombat and UnitAffectingCombat("player") == true) or within("combatEnd", 4)) then
			I.killFromXP(nil)
		end
		return
	end
	if rt.pKillXP then
		local mobName, amount = msg:match(rt.pKillXP)
		if amount then
			if I.killFromXP then I.killFromXP(mobName) end
			s.xp.kills = s.xp.kills + (tonumber(amount) or 0)
			s.xp.killsN = s.xp.killsN + 1
			return
		end
	end
	-- "You gain %d experience." right after a quest turn-in when the event had no XP value
	if rt.pXP and rt.questWaitXP and now() - rt.questWaitXP.at < 3 then
		local amount = tonumber(msg:match(rt.pXP))
		if amount then
			rt.questWaitXP.e.xp = amount
			s.xp.quests = s.xp.quests + amount
			rt.questWaitXP = nil
		end
	end
end)

on("TIME_PLAYED_MSG", function(total)
	if not isSecret(total) then s.playedTotal = tonumber(total) or s.playedTotal end
end)
on("PLAYER_REGEN_ENABLED", function()
	rt.combatEnd = now()
	-- values protected during the fight are readable again: catch the dead we could not check
	LZ.After(0.5, function() if s and I.watchAll then pcall(I.watchAll) end end)
end)

----------------------------------------------------------------------
-- Gold: every change is booked to the window that was open at that moment
----------------------------------------------------------------------
local function moneySource()
	if rt.lootOpen or within("lootClosed", 1.5) or within("moneyMsg", 1) then return "loot" end
	if within("questAt", 2) then return "quest" end
	if rt.merchant then return "vendor" end
	if rt.mail then return "mail" end
	if rt.auction then return "auction" end
	if rt.trainer then return "trainer" end
	if rt.taxiMap or within("taxiClosed", 3) then return "taxi" end
	if rt.trade or within("tradeClosed", 2) then return "trade" end
	return "other"
end

on("PLAYER_MONEY", function()
	local m = money()
	local d = m - (rt.money or m)
	rt.money = m
	if d == 0 then return end
	local src = moneySource()
	if d > 0 then
		s.money.income = s.money.income + d
		add(s.money.gain, src, d)
		if src == "loot" or src == "quest" or src == "vendor" then -- what the zone itself brought (farm ranking)
			local z = zoneEntry(zoneName())
			z.money = (z.money or 0) + d
		end
	else
		s.money.spent = s.money.spent - d
		add(s.money.spend, src, -d)
	end
	if rt.run then rt.run.money = (rt.run.money or 0) + d end
end)

on("CHAT_MSG_MONEY", function() rt.moneyMsg = now() end)
on("MERCHANT_SHOW", function() rt.merchant = true end)
on("MERCHANT_CLOSED", function() rt.merchant = false end)
on("MAIL_SHOW", function() rt.mail = true end)
on("MAIL_CLOSED", function() rt.mail = false end)
on("AUCTION_HOUSE_SHOW", function() rt.auction = true end)
on("AUCTION_HOUSE_CLOSED", function() rt.auction = false end)
on("TRAINER_SHOW", function() rt.trainer = true end)
on("TRAINER_CLOSED", function() rt.trainer = false end)
on("TAXIMAP_OPENED", function() rt.taxiMap = true end)
on("TAXIMAP_CLOSED", function() rt.taxiMap = false; rt.taxiClosed = now() end)
on("TRADE_SHOW", function() rt.trade = true end)
on("TRADE_CLOSED", function() rt.trade = false; rt.tradeClosed = now() end)

----------------------------------------------------------------------
-- Quests
----------------------------------------------------------------------
local function questInfoFromLog(questID)
	if not (_G.GetNumQuestLogEntries and _G.GetQuestLogTitle) then return nil end
	local n = GetNumQuestLogEntries() or 0
	for i = 1, n do
		local title, level, _, isHeader, _, _, _, id = GetQuestLogTitle(i)
		if not isHeader and id == questID then return title, level end
	end
end

local function questTitle(questID)
	if not questID then return nil end
	local cached = rt.qtitle[questID]
	if cached then return cached end
	local title
	local QL = _G.C_QuestLog
	if QL and QL.GetTitleForQuestID then
		local ok, v = pcall(QL.GetTitleForQuestID, questID)
		if ok and type(v) == "string" and v ~= "" then title = v end
	end
	if not title and QL and QL.GetQuestInfo then
		local ok, v = pcall(QL.GetQuestInfo, questID)
		if ok and type(v) == "string" and v ~= "" then title = v end
	end
	if not title then title = questInfoFromLog(questID) end
	if title then rt.qtitle[questID] = title end
	return title
end

on("QUEST_ACCEPTED", function(a, b)
	local questID = (type(b) == "number" and b > 0) and b or a -- Classic: (logIndex, questID), newer: (questID)
	if type(questID) ~= "number" then return end
	local title, level = questInfoFromLog(questID)
	title = title or questTitle(questID)
	if title then rt.qtitle[questID] = title end
	push(s.quests.accepted, { id = questID, title = title, lvl = level, t = time() }, LIMIT.accepted)
end)

-- The reward window knows the title for sure.
on("QUEST_COMPLETE", function()
	local id = _G.GetQuestID and GetQuestID()
	local title = _G.GetTitleText and GetTitleText()
	if id and id > 0 and type(title) == "string" and title ~= "" then rt.qtitle[id] = title end
end)

on("QUEST_TURNED_IN", function(questID, xp, cash)
	questID = tonumber(questID)
	local e = { id = questID, title = questTitle(questID), xp = tonumber(xp), money = tonumber(cash), t = time(), zone = zoneName() }
	push(s.quests.done, e, LIMIT.done)
	rt.questAt = now()
	if e.xp then
		s.xp.quests = s.xp.quests + e.xp
	else
		rt.questWaitXP = { e = e, at = now() }
	end
end)

-- The name is only known while the quest is selected for abandoning, not afterwards.
local function onSetAbandon()
	rt.abandonName = _G.GetAbandonQuestName and GetAbandonQuestName() or nil
end

local function onAbandon()
	if not s or not mod.enabled then return end
	local title = rt.abandonName or (_G.GetAbandonQuestName and GetAbandonQuestName())
	rt.abandonName = nil
	if type(title) ~= "string" or title == "" then return end
	push(s.quests.abandoned, { title = title, t = time() }, LIMIT.abandoned)
end

----------------------------------------------------------------------
-- Loot (and what professions produced)
----------------------------------------------------------------------
local lootPatterns -- { pattern, kind }

local function buildLootPatterns()
	lootPatterns = {}
	local function addP(name, kind)
		local p = pattern(_G[name])
		if p then lootPatterns[#lootPatterns + 1] = { p, kind } end
	end
	-- multiples first: the plain pattern would also swallow "...x2" lines
	addP("LOOT_ITEM_CREATED_SELF_MULTIPLE", "created")
	addP("LOOT_ITEM_CREATED_SELF", "created")
	addP("LOOT_ITEM_SELF_MULTIPLE", "loot")
	addP("LOOT_ITEM_SELF", "loot")
	addP("LOOT_ITEM_PUSHED_SELF_MULTIPLE", "pushed")
	addP("LOOT_ITEM_PUSHED_SELF", "pushed")
end

-- gathering spell name -> kind
local GATHER_SPELLS = { herb = { 2366, 2368, 3570, 11993 }, mining = { 2575, 2576, 3564, 10248 }, skinning = { 8613, 8617, 8618, 10768 } }
local gatherNames

local function buildGatherNames()
	gatherNames = {}
	for kind, ids in pairs(GATHER_SPELLS) do
		for _, id in ipairs(ids) do
			local n = C.GetSpellName(id)
			if n then gatherNames[n] = kind end
		end
	end
end

local function lootKind()
	if rt.lootOpen or within("lootClosed", 1.5) then return rt.lootKind end
	return nil
end

local function bookItem(list, id, link, count)
	local e = list[id]
	if not e then
		e = { name = itemName(link), q = LZ.QualityFromLink(link), n = 0 }
		list[id] = e
	end
	e.n = e.n + count
	return e
end

on("CHAT_MSG_LOOT", function(msg)
	if not text(msg) or not lootPatterns then return end
	local kind, link, count
	for _, p in ipairs(lootPatterns) do
		local a, b = msg:match(p[1])
		if a then kind, link, count = p[2], a, tonumber(b) or 1; break end
	end
	if not kind then return end
	local id = LZ.ItemID(link)
	if not id then return end
	local q = LZ.QualityFromLink(link) or 1

	if kind == "created" then
		bookItem(s.prof.crafted, id, link, count)
		return
	end

	local gk = (kind == "loot") and lootKind() or nil
	if gk and gk ~= "mob" then
		s.prof.gathered[gk] = s.prof.gathered[gk] or {}
		bookItem(s.prof.gathered[gk], id, link, count)
	end

	local L = s.loot
	local e = bookItem(L.items, id, link, count)
	if e.v == nil then
		local v = C.GetSellPrice(link)
		if v and v > 0 then e.v = v end
	end
	if kind == "pushed" then e.p = (e.p or 0) + count end
	L.n = L.n + count
	add(L.q, q, count)
	L.value = L.value + (e.v or 0) * count
	if q >= 2 then
		local boss = rt.lastBoss and (now() - rt.lastBoss.at) < 120 and rt.lastBoss.name or nil
		push(L.notable, { id = id, name = e.name, q = q, n = count, t = time(), zone = zoneName(),
			src = (kind == "pushed") and "quest" or (gk ~= "mob" and gk) or rt.lootSrc, boss = boss }, LIMIT.notable)
		if rt.run and kind ~= "pushed" then
			rt.run.drops = rt.run.drops or {}
			push(rt.run.drops, { id = id, name = e.name, q = q, n = count, boss = boss }, 40)
		end
	end
	if rt.run then rt.run.loot = (rt.run.loot or 0) + count end
end)

on("LOOT_OPENED", function()
	rt.lootOpen = true
	rt.lootSrc = nil
	if rt.gatherAt and now() - rt.gatherAt < 3 then
		rt.lootKind = rt.gatherKind
	elseif _G.IsFishingLoot and IsFishingLoot() then
		rt.lootKind = "fishing"
	else
		rt.lootKind = "mob"
		if I.watchAll then I.watchAll() end
		if UnitExists and UnitExists("target") and UnitIsDead and UnitIsDead("target") then
			local n = UnitName("target")
			if text(n) then rt.lootSrc = n end
		end
	end
	rt.gatherAt = nil
end)

on("LOOT_CLOSED", function()
	rt.lootOpen = false
	rt.lootClosed = now()
end)

----------------------------------------------------------------------
-- Professions and skills
----------------------------------------------------------------------
local function readSkills()
	local out = {}
	if _G.GetNumSkillLines and _G.GetSkillLineInfo then
		local header = ""
		for i = 1, GetNumSkillLines() or 0 do
			local name, isHeader, _, rank, _, _, maxRank = GetSkillLineInfo(i)
			if name then
				if isHeader then
					header = name
				elseif (maxRank or 0) > 1 then
					out[name] = { rank = rank or 0, max = maxRank or 0, h = header }
				end
			end
		end
	elseif _G.GetProfessions and _G.GetProfessionInfo then
		for _, idx in pairs({ GetProfessions() }) do
			if type(idx) == "number" then
				local name, _, rank, maxRank = GetProfessionInfo(idx)
				if name then out[name] = { rank = rank or 0, max = maxRank or 0, h = "prof" } end
			end
		end
	end
	return out
end

local function snapshotSkills(initial)
	local skills = s.prof.skills
	local n = 0
	for _ in pairs(skills) do n = n + 1 end
	for name, info in pairs(readSkills()) do
		local e = skills[name]
		if e then
			e.b, e.m = info.rank, info.max
		elseif n < LIMIT.skills then
			skills[name] = { h = info.h, a = info.rank, b = info.rank, m = info.max }
			n = n + 1
		end
	end
end

on("SKILL_LINES_CHANGED", function()
	if rt.skillQueued then return end
	rt.skillQueued = true
	LZ.After(1, function()
		rt.skillQueued = nil
		if s then pcall(snapshotSkills) end
	end)
end)

on("CHAT_MSG_SKILL", function(msg)
	if not text(msg) or not rt.pSkill then return end
	local name, rank = msg:match(rt.pSkill)
	rank = tonumber(rank)
	if not name or not rank then return end
	s.prof.ups = s.prof.ups + 1
	local e = s.prof.skills[name]
	if e then
		e.b = math.max(e.b or 0, rank)
	else
		s.prof.skills[name] = { a = rank - 1, b = rank }
	end
end)

on("CHAT_MSG_SYSTEM", function(msg)
	if not text(msg) then return end
	if rt.pRecipe then
		local what = msg:match(rt.pRecipe)
		if what then push(s.prof.learned, { name = LZ.Strip(what), t = time() }, LIMIT.learned); return end
	end
	for _, p in ipairs(rt.pSpell or {}) do
		local what = msg:match(p)
		if what then push(s.spellsLearned, { name = LZ.Strip(what), t = time() }, LIMIT.learned); return end
	end
end)

----------------------------------------------------------------------
-- Exploration and zones
----------------------------------------------------------------------
local function discovered(name, xp)
	name = LZ.Strip(name)
	if not name or name == "" then return end
	local last = rt.lastFound
	if last and last.name == name and now() - last.at < 10 then
		if xp and not last.e.xp then
			last.e.xp = xp
			s.xp.explore = s.xp.explore + xp
		end
		return
	end
	local e = { name = name, xp = xp, zone = zoneName(), t = time() }
	push(s.explore.found, e, LIMIT.found)
	if xp then s.xp.explore = s.xp.explore + xp end
	rt.lastFound = { name = name, at = now(), e = e }
end

local function checkDiscovery(msg)
	if not text(msg) then return end
	if rt.pExploredXP then
		local name, xp = msg:match(rt.pExploredXP)
		if name then discovered(name, tonumber(xp)); return end
	end
	if rt.pExplored then
		local name = msg:match(rt.pExplored)
		if name then discovered(name, nil) end
	end
end

on("CHAT_MSG_SYSTEM", checkDiscovery)
-- newer clients: (messageType, message), older: (message)
on("UI_INFO_MESSAGE", function(a, b)
	if isSecret(a) or isSecret(b) then return end
	checkDiscovery(type(b) == "string" and b or a)
end)

zoneEntry = function(zone)
	local zones = s.explore.zones
	local e = zones[zone]
	if not e then
		e = { sec = 0, subs = {} }
		zones[zone] = e
	end
	return e
end

local function noteSubzone()
	local sub = _G.GetSubZoneText and GetSubZoneText() or ""
	if sub == "" then return end
	local e = zoneEntry(zoneName())
	if e.subs[sub] then return end
	local n = 0
	for _ in pairs(e.subs) do n = n + 1 end
	if n < LIMIT.subs then e.subs[sub] = true end
end

on("ZONE_CHANGED", noteSubzone)
on("ZONE_CHANGED_INDOORS", noteSubzone)

----------------------------------------------------------------------
-- Dungeons, raids, battlegrounds
----------------------------------------------------------------------
local function groupSize()
	if _G.GetNumGroupMembers then return GetNumGroupMembers() or 0 end
	if _G.GetNumRaidMembers and GetNumRaidMembers() > 0 then return GetNumRaidMembers() end
	if _G.GetNumPartyMembers then return GetNumPartyMembers() + 1 end
	return 0
end

local function closeRun()
	if rt.run then
		rt.run.stop = time()
		rt.run = nil
	end
end

local function checkInstance()
	if not s or not _G.GetInstanceInfo then return end
	local name, kind, _, diffName, maxPlayers, _, _, instanceID = GetInstanceInfo()
	local inside = kind and kind ~= "none" and name and name ~= ""
	if not inside then closeRun(); return end
	if rt.run and rt.run.name == name then return end
	closeRun()
	-- back into the same instance shortly after leaving (ghost run, quick port out): same run
	local last = s.runs[#s.runs]
	if last and last.name == name and last.stop and time() - last.stop < 600 then
		last.stop = nil
		rt.run = last
		return
	end
	local run = { name = name, kind = kind, diff = (diffName ~= "" and diffName) or nil, size = maxPlayers,
		id = instanceID, start = time(), kills = 0, bosses = {}, wipes = 0, deaths = 0, xp = 0, money = 0, loot = 0,
		group = groupSize() }
	push(s.runs, run, LIMIT.runs)
	rt.run = run
end

on("PLAYER_ENTERING_WORLD", function() LZ.After(1, checkInstance) end)
on("ZONE_CHANGED_NEW_AREA", function() LZ.After(1, checkInstance); noteSubzone() end)

on("GROUP_ROSTER_UPDATE", function()
	if rt.run then rt.run.group = math.max(rt.run.group or 0, groupSize()) end
end)

on("ENCOUNTER_START", function(id, name)
	if isSecret(name) then name = nil end
	rt.encounter = { name = name, at = now() }
end)

on("ENCOUNTER_END", function(id, name, diff, size, success)
	if isSecret(name) or isSecret(success) then rt.encounter = nil; return end
	local started = rt.encounter and rt.encounter.at
	rt.encounter = nil
	if success == 1 or success == true then
		rt.lastBoss = { name = name, at = now() }
		push(s.bosses, { name = name, t = time(), inst = rt.run and rt.run.name or zoneName(),
			sec = started and math.floor(now() - started) or nil }, LIMIT.bosses)
		if rt.run then push(rt.run.bosses, name, 30) end
	else
		s.wipes = s.wipes + 1
		if rt.run then rt.run.wipes = rt.run.wipes + 1 end
	end
end)

----------------------------------------------------------------------
-- Deaths
----------------------------------------------------------------------
on("PLAYER_DEAD", function()
	s.deathN = s.deathN + 1
	local hit = rt.lastHit
	if hit and now() - hit.at > 20 then hit = nil end
	push(s.deaths, { t = time(), zone = zoneName(), sub = _G.GetSubZoneText and GetSubZoneText() or nil,
		by = hit and hit.by or nil, spell = hit and hit.spell or nil, lvl = playerLevel(),
		inst = rt.run and rt.run.name or nil }, LIMIT.deaths)
	if rt.run then rt.run.deaths = rt.run.deaths + 1 end
end)

----------------------------------------------------------------------
-- Reputation
----------------------------------------------------------------------
-- name -> current value; changes between two reads are booked (works in combat, no chat parsing)
local function readRep()
	local out = {}
	local R = _G.C_Reputation
	if R and R.GetNumFactions and R.GetFactionDataByIndex then
		for i = 1, R.GetNumFactions() or 0 do
			local d = R.GetFactionDataByIndex(i)
			if d and d.name and not d.isHeader and not isSecret(d.currentStanding) then out[d.name] = d.currentStanding end
		end
	elseif _G.GetNumFactions and _G.GetFactionInfo then
		for i = 1, GetNumFactions() or 0 do
			local name, _, _, _, _, value, _, _, isHeader, _, hasRep = GetFactionInfo(i)
			if name and (not isHeader or hasRep) and value and not isSecret(value) then out[name] = value end
		end
	end
	return out
end

local function updateRep()
	local cur = readRep()
	local old = rt.rep
	rt.rep = cur
	if not old then return end
	for name, v in pairs(cur) do
		local before = old[name]
		if before and v ~= before then add(s.rep, name, v - before) end
	end
end

on("UPDATE_FACTION", function()
	if rt.repQueued then return end
	rt.repQueued = true
	LZ.After(1, function()
		rt.repQueued = nil
		if s then pcall(updateRep) end
	end)
end)

----------------------------------------------------------------------
-- Kills (SessionKills.lua) and the combat log (SessionCombat.lua) use the
-- helpers above through LZ.SessionInternal (filled at the end of this file).
----------------------------------------------------------------------

----------------------------------------------------------------------
-- Player-only unit events (own frame: RegisterUnitEvent keeps it cheap)
----------------------------------------------------------------------
local unitFrame

-- Fires for every spell you cast, also in combat – protected spell IDs/names are skipped.
local function onSpellcast(spellID)
	if isSecret(spellID) then return end
	local name = C.GetSpellName(spellID)
	if not text(name) then return end
	local kind = gatherNames and gatherNames[name]
	if kind then rt.gatherKind, rt.gatherAt = kind, now() end
end

local function onUnitEvent(_, event, unit, castGUID, spellID)
	if not s or event ~= "UNIT_SPELLCAST_SUCCEEDED" then return end
	local ok, err = pcall(onSpellcast, spellID)
	if not ok then onError(event, err) end
end

local function startUnitEvents()
	unitFrame = unitFrame or CreateFrame("Frame")
	unitFrame:SetScript("OnEvent", onUnitEvent)
	if unitFrame.RegisterUnitEvent then
		pcall(unitFrame.RegisterUnitEvent, unitFrame, "UNIT_SPELLCAST_SUCCEEDED", "player")
	else
		pcall(unitFrame.RegisterEvent, unitFrame, "UNIT_SPELLCAST_SUCCEEDED")
	end
end

----------------------------------------------------------------------
-- Ticker: time categories, zone time and distance
----------------------------------------------------------------------
local ticker

local function tick()
	if not s then return end
	local t = now()
	local dt = t - (rt.last or t)
	rt.last = t
	if dt <= 0 or dt > 5 then return end -- loading screens and freezes do not count

	local tm = s.time
	local function yes(v) return v and not isSecret(v) end
	if yes(UnitIsAFK and UnitIsAFK("player")) then tm.afk = tm.afk + dt end
	local fighting = yes(UnitAffectingCombat and UnitAffectingCombat("player"))
		or (_G.InCombatLockdown and InCombatLockdown() == true)
	if fighting then tm.combat = tm.combat + dt; rt.fightTick = t end
	if yes(IsResting and IsResting()) then tm.rest = tm.rest + dt end
	local dead = yes(UnitIsDeadOrGhost and UnitIsDeadOrGhost("player"))
	if dead then tm.dead = tm.dead + dt end
	local taxi = yes(UnitOnTaxi and UnitOnTaxi("player"))
	if taxi then tm.taxi = tm.taxi + dt end
	if rt.run then tm.instance = tm.instance + dt end

	local z = zoneEntry(zoneName())
	z.sec = z.sec + dt
	-- look at target, nameplates and boss frames only in combat and shortly after (kills are counted there);
	-- the unit events cover the rest, outside of fights this saves 49 unit checks every half second
	if I.watchAll and (fighting or (rt.fightTick and t - rt.fightTick < 5)) then pcall(I.watchAll) end

	-- the speed is protected in combat on newer clients: no distance then, everything else still counts
	local speed = GetUnitSpeed and GetUnitSpeed("player") or 0
	if isSecret(speed) then speed = 0 end
	if speed > 0 and dt <= 2 then
		local mv = s.move
		local mode
		if taxi then mode = "taxi"
		elseif dead then mode = "ghost"
		elseif IsSwimming and IsSwimming() then mode = "swim"
		elseif IsMounted and IsMounted() then mode = "mount"
		else mode = "foot" end
		mv[mode] = mv[mode] + speed * dt
		if mode == "foot" or mode == "ghost" then
			-- estimate: about 2.9 steps per second when running, 2 when walking
			mv.steps = mv.steps + dt * (speed >= 4 and 2.9 or 2.0)
		end
	end
end

local function safeTick()
	local ok, err = pcall(tick)
	if not ok then onError("tick", err) end
end

local function startTicker()
	if ticker then return end
	rt.last = now()
	if _G.C_Timer and C_Timer.NewTicker then
		ticker = C_Timer.NewTicker(TICK, safeTick)
	else
		local f = CreateFrame("Frame")
		local acc = 0
		f:SetScript("OnUpdate", function(_, e)
			acc = acc + (e or 0)
			if acc >= TICK then acc = 0; safeTick() end
		end)
		ticker = { Cancel = function() f:SetScript("OnUpdate", nil) end }
	end
end

local function stopTicker()
	if ticker then ticker:Cancel(); ticker = nil end
end

local function onJump()
	if not s or not mod.enabled then return end
	if (IsSwimming and IsSwimming()) or (IsFlying and IsFlying()) then return end
	s.move.jumps = (s.move.jumps or 0) + 1
end

----------------------------------------------------------------------
-- Lifecycle
----------------------------------------------------------------------
local function buildPatterns()
	buildLootPatterns()
	buildGatherNames()
	rt.pKillXP = pattern(_G.COMBATLOG_XPGAIN_FIRSTPERSON, true)   -- open end: rested/group bonus text follows
	rt.pXP = pattern(_G.COMBATLOG_XPGAIN_FIRSTPERSON_UNNAMED, true)
	rt.pSkill = pattern(_G.SKILL_RANK_UP)
	rt.pRecipe = pattern(_G.ERR_LEARN_RECIPE_S)
	rt.pSpell = { pattern(_G.ERR_LEARN_SPELL_S), pattern(_G.ERR_LEARN_ABILITY_S) }
	rt.pExploredXP = pattern(_G.ERR_ZONE_EXPLORED_XP)
	rt.pExplored = pattern(_G.ERR_ZONE_EXPLORED)
	rt.pRepUp = pattern(_G.FACTION_STANDING_INCREASED)
	rt.pRepDown = pattern(_G.FACTION_STANDING_DECREASED)
end

function mod:OnInit()
	LZ.gdb.sessionLog = LZ.gdb.sessionLog or {}
	if not self.hooked then
		self.hooked = true
		if _G.hooksecurefunc then
			if _G.JumpOrAscendStart then hooksecurefunc("JumpOrAscendStart", onJump) end
			if _G.SetAbandonQuest then hooksecurefunc("SetAbandonQuest", onSetAbandon) end
			if _G.AbandonQuest then hooksecurefunc("AbandonQuest", onAbandon) end
			local QL = _G.C_QuestLog
			if QL and QL.SetAbandonQuest then hooksecurefunc(QL, "SetAbandonQuest", onSetAbandon) end
			if QL and QL.AbandonQuest then hooksecurefunc(QL, "AbandonQuest", onAbandon) end
		end
	end
end

function mod:OnEnable()
	buildPatterns()
	local list = LZ.gdb.sessionLog
	if not s then
		local last = list[#list]
		local window = (tonumber(self.opt.resumeMinutes) or 15) * 60
		if last and last.v == VERSION and last.char == LZ.charKey and last.saved and (time() - last.saved) <= window then
			setSession(last) -- /reload or quick relog: same session continues
			s.resumes = (s.resumes or 0) + 1
		else
			setSession(newSession())
			push(list, s, MAX_SESSIONS)
		end
	end
	rt.guid = UnitGUID and UnitGUID("player")
	rt.loginAt = time()
	rt.playedBefore = s.played or 0
	rt.money = money()
	rt.xp, rt.xpMax = readXP()
	if isSecret(rt.xp) or isSecret(rt.xpMax) then rt.xp, rt.xpMax = 0, 0 end
	rt.qtitle = rt.qtitle or {}
	rt.rep = nil
	pcall(updateRep)
	snapshotSkills(true)
	I.startCombatLog()
	startUnitEvents()
	startTicker()
	LZ.After(1, checkInstance)
end

-- Brings the session table up to date. Safe to call any time (also for the report).
function mod:Update()
	if not s then return end
	tick()
	local t = time()
	s.stop, s.saved = t, t
	s.played = rt.playedBefore + (t - (rt.loginAt or t))
	s.level.to = playerLevel()
	local xn, xm = readXP()
	if not isSecret(xn) and not isSecret(xm) then s.xpNow, s.xpMax = xn, xm end
	s.money.stop = money()
	s.addon = LZ.version
	local x = s.xp
	x.other = math.max(0, x.total - math.max(x.kills, x.combat or 0) - x.quests - x.explore)
end

function mod:Save()
	if not s then return end
	self:Update()
	snapshotSkills()
	if rt.run then rt.run.stop = time() end
	for id, e in pairs(s.loot.items) do
		if e.v == nil then
			local v = C.GetSellPrice(id)
			if v and v > 0 then
				e.v = v
				s.loot.value = s.loot.value + v * e.n
			end
		end
	end
	local cb = s.combat
	trimMap(cb.spells, LIMIT.spells, byFirst)
	trimMap(cb.heals, LIMIT.heals, byFirst)
	trimMap(cb.casts, LIMIT.casts, byNumber)
	trimMap(cb.buffs, LIMIT.auras, byNumber)
	trimMap(cb.debuffs, LIMIT.auras, byNumber)
	trimMap(cb.takenBy, LIMIT.takenBy, byNumber)
	trimMap(s.kills.mobs, LIMIT.mobs, byN)
	trimMap(s.loot.items, LIMIT.items, byN)
	trimMap(s.prof.crafted, LIMIT.crafted, byN)
	for _, g in pairs(s.prof.gathered) do trimMap(g, LIMIT.gathered, byN) end
	trimMap(s.explore.zones, LIMIT.zones, bySec)
	trimMap(s.rep, LIMIT.rep, function(v) return math.abs(v or 0) end)
	for k, v in pairs(s.time) do s.time[k] = round(v) end
	for k, v in pairs(s.move) do s.move[k] = round(v, 1) end
	for _, z in pairs(s.explore.zones) do z.sec = round(z.sec) end
end

function mod:OnLogout()
	local ok, err = pcall(self.Save, self)
	if not ok then onError("PLAYER_LOGOUT", err) end
end

function mod:OnDisable()
	self:Save()
	stopTicker()
	I.stopCombatLog()
	if unitFrame then unitFrame:UnregisterAllEvents() end
	rt.run = nil
	setSession(nil)
end

-- For other modules and tests.
function mod:Current() return s end

----------------------------------------------------------------------
-- In-game report
----------------------------------------------------------------------
local function perHour(v, sec)
	if not sec or sec < 600 then return 0 end -- shorter sessions give silly rates
	return v * 3600 / sec
end

local function topOf(map, n, value)
	local keys = {}
	for k in pairs(map) do keys[#keys + 1] = k end
	table.sort(keys, function(a, b) return value(map[a]) > value(map[b]) end)
	local out = {}
	for i = 1, math.min(n, #keys) do out[i] = keys[i] end
	return out
end

local function countOf(t)
	local n = 0
	for _ in pairs(t or {}) do n = n + 1 end
	return n
end

function mod:Summary()
	if not s then return "-" end
	self:Update()
	return string.format("%s · %s XP · %d %s", LZ.Duration(s.played), LZ.Num(s.xp.total), s.kills.n, T("Kills", "kills"))
end

function mod:TooltipLines()
	if not s then return nil end
	return { T("Sitzung: ", "Session: ") .. self:Summary() }
end

function mod:Report()
	local lines = {}
	local function head(text) lines[#lines + 1] = ""; lines[#lines + 1] = LZ.Color("ffd100", text) end
	local function line(text) lines[#lines + 1] = text end
	if not s then
		line(T("Keine laufende Sitzung.", "No session running."))
	else
		self:Update()
		local sec = s.played
		lines[1] = LZ.Color("ffd100", string.format(T("Aktuelle Sitzung – seit %s", "Current session – since %s"), date("%H:%M", s.start)))
		line(string.format(T("Spielzeit %s · im Kampf %s · AFK %s", "Play time %s · in combat %s · AFK %s"),
			LZ.Duration(sec), LZ.Duration(s.time.combat), LZ.Duration(s.time.afk)))

		head(T("Erfahrung und Gold", "Experience and gold"))
		local lvl = s.level.from == s.level.to and tostring(s.level.to) or (s.level.from .. " → " .. s.level.to)
		local rate = perHour(s.xp.total, sec)
		line(string.format(T("Stufe %s · %s XP", "Level %s · %s XP"), lvl, LZ.Num(s.xp.total)) .. (rate > 0 and string.format(" (%s/h)", LZ.Num(rate)) or ""))
		line(string.format(T("aus Kills %s · Quests %s · Entdecken %s", "from kills %s · quests %s · exploring %s"),
			LZ.Num(s.xp.kills), LZ.Num(s.xp.quests), LZ.Num(s.xp.explore)))
		local net = money() - s.money.start
		line(string.format(T("Gold netto %s · eingenommen %s · ausgegeben %s", "Gold net %s · earned %s · spent %s"),
			LZ.FormatMoney(net), LZ.FormatMoney(s.money.income), LZ.FormatMoney(s.money.spent)))

		head(T("Quests und Beute", "Quests and loot"))
		line(string.format(T("%d abgeschlossen · %d angenommen · %d abgebrochen", "%d completed · %d accepted · %d abandoned"),
			#s.quests.done, #s.quests.accepted, #s.quests.abandoned))
		line(string.format(T("%s Gegenstände · Händlerwert %s", "%s items · vendor value %s"), LZ.Num(s.loot.n), LZ.FormatMoney(s.loot.value)))

		head(T("Kampf", "Combat"))
		local K = s.kills
		line(string.format(T("%d Gegner besiegt (%d Elite, %d selten, %d Bosse) · %d Todesstöße", "%d enemies killed (%d elite, %d rare, %d bosses) · %d killing blows"),
			K.n, K.elite + K.rareelite, K.rare + K.rareelite, math.max(K.boss, #s.bosses), K.blows))
		local cb = s.combat
		line(string.format(T("Schaden %s · Heilung %s · erlitten %s · Tode %d", "Damage %s · healing %s · taken %s · deaths %d"),
			LZ.Num(cb.dmg), LZ.Num(cb.heal), LZ.Num(cb.taken), s.deathN))
		line(string.format(T("%s Hiebe · %s Zauber · %s Buffs erhalten", "%s swings · %s spells · %s buffs received"),
			LZ.Num(cb.swings.n), LZ.Num(cb.castN), LZ.Num(cb.buffN)))
		if cb.err then line(LZ.Color("ff8080", T("Kampflog nicht verfügbar: ", "Combat log not available: ") .. cb.err)) end

		head(T("Unterwegs", "Travel"))
		local mv = s.move
		line(string.format(T("%s zu Fuß (≈ %s Schritte) · %s beritten · %s geschwommen · %s Flugroute", "%s on foot (≈ %s steps) · %s mounted · %s swum · %s flight path"),
			km(mv.foot + mv.ghost), LZ.Num(mv.steps), km(mv.mount), km(mv.swim), km(mv.taxi)))
		line(string.format(T("%s Sprünge · %d Gebiete entdeckt", "%s jumps · %d areas discovered"), LZ.Num(mv.jumps), #s.explore.found))

		local skillLines = {}
		for name, e in pairs(s.prof.skills) do
			if (e.b or 0) > (e.a or 0) then skillLines[#skillLines + 1] = string.format("%s %d → %d", name, e.a or 0, e.b or 0) end
		end
		if #skillLines > 0 then
			head(T("Fertigkeiten", "Skills"))
			table.sort(skillLines)
			line(table.concat(skillLines, " · "))
		end

		if #s.runs > 0 then
			head(T("Dungeons und Schlachtzüge", "Dungeons and raids"))
			for _, r in ipairs(s.runs) do
				line(string.format(T("%s: %d Kills, %d Bosse", "%s: %d kills, %d bosses"), r.name or "?", r.kills or 0, #(r.bosses or {})))
			end
		end

		local mobs = topOf(s.kills.mobs, 5, byN)
		if #mobs > 0 then
			head(T("Häufigste Gegner", "Most killed"))
			for _, k in ipairs(mobs) do
				local e = s.kills.mobs[k]
				line(string.format("%s ×%d", e.name or "?", e.n))
			end
		end
	end
	head(T("Gespeicherte Sitzungen", "Stored sessions"))
	line(string.format(T("%d im Spiel gespeichert. Leeroyzan übernimmt sie nach dem Ausloggen dauerhaft und fasst sie zusammen.",
		"%d stored in the game. Leeroyzan imports them after you log out, keeps them and summarizes them."), #(LZ.gdb.sessionLog or {})))
	return table.concat(lines, "\n")
end

mod:Command({ "sitzung", "session" }, function(self) LZ.UI:Show(self.name) end,
	{ "sitzung", "Laufende Sitzung anzeigen", "show the current session" })

-- helpers for SessionKills.lua and SessionCombat.lua (zoneEntry is only defined above)
I.on, I.now, I.text, I.isSecret, I.add, I.zoneName, I.zoneEntry = on, now, text, isSecret, add, zoneName, zoneEntry
