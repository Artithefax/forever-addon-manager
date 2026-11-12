-- SessionKills: mob classification and kills for the session log. Part of the Session module
-- (Session.lua holds the session itself); shared helpers come through LZ.SessionInternal.
local ADDON, LZ = ...
local I = LZ.SessionInternal
local on, now, text, isSecret, zoneEntry, zoneName, rt = I.on, I.now, I.text, I.isSecret, I.zoneEntry, I.zoneName, I.rt
local s
I.onSession(function(new) s = new end)

----------------------------------------------------------------------
-- Mob classification (normal / elite / rare / boss) by GUID
----------------------------------------------------------------------
local classByGUID, classCount = {}, 0
local classByName, nameCount = {}, 0

local function cacheUnit(unit)
	if not (UnitExists and UnitExists(unit)) or (UnitIsPlayer and UnitIsPlayer(unit)) then return end
	local guid = UnitGUID(unit)
	if not guid or isSecret(guid) or classByGUID[guid] then return end
	local c = UnitClassification and UnitClassification(unit) or "normal"
	if isSecret(c) then return end
	if c == "worldboss" then c = "boss" end
	local level = UnitLevel and UnitLevel(unit)
	if c ~= "boss" and not isSecret(level) and level == -1 and _G.IsInInstance and IsInInstance() then c = "boss" end
	if classCount > 3000 then wipe(classByGUID); classCount = 0 end
	classByGUID[guid] = c
	classCount = classCount + 1
	local name = UnitName and UnitName(unit)
	if text(name) then
		if nameCount > 1000 then wipe(classByName); nameCount = 0 end
		if not classByName[name] then nameCount = nameCount + 1 end
		classByName[name] = c
	end
end

----------------------------------------------------------------------
-- Kills. Three sources describe the same kill, each can be missing:
--   "unit": the combat log (UNIT_DIED) or a dead unit we fought (target,
--           nameplates, boss frames) – WoW Forever has no combat log for addons
--   "xp":   the chat line "X dies, you gain N experience" (also protected in
--           combat on newer clients – then without a name)
-- A kill seen by both is counted once; the unit side brings the classification.
----------------------------------------------------------------------
local counted, countedN = {}, 0   -- GUIDs already counted
local fought, foughtN = {}, 0     -- GUIDs of living enemies that were in combat with us
local recent = {}                 -- kills of the last two minutes, for pairing

local KILL_CLASSES = { normal = true, elite = true, rare = true, rareelite = true, boss = true }

local function classOf(guid, name)
	local c = (guid and classByGUID[guid]) or (name and classByName[name])
	if rt.encounter and name and rt.encounter.name == name then c = "boss" end
	return KILL_CLASSES[c] and c or "normal"
end

local function mobEntry(name, c)
	if not name then return end
	local e = s.kills.mobs[name]
	if not e then
		e = { name = name, n = 0, c = c }
		s.kills.mobs[name] = e
	end
	return e
end

local function bookKill(name, c)
	local K = s.kills
	K.n = K.n + 1
	K[c] = K[c] + 1
	local e = mobEntry(name, c)
	if e then e.n = e.n + 1; e.c = c end
	local z = zoneEntry(zoneName())
	z.kills = (z.kills or 0) + 1
	if rt.run then rt.run.kills = rt.run.kills + 1 end
end

-- a kill counted from the XP line gets its name/class from the unit later
local function improve(k, name, c)
	local K = s.kills
	if not k.name and name then
		k.name = name
		local e = mobEntry(name, c)
		e.n = e.n + 1
	end
	if c ~= k.c then
		K[k.c] = K[k.c] - 1
		K[c] = K[c] + 1
		k.c = c
		local e = k.name and K.mobs[k.name]
		if e then e.c = c end
	end
end

local function onKill(src, name, guid)
	if guid then
		if counted[guid] then return end
		if countedN > 2000 then wipe(counted); countedN = 0 end
		counted[guid] = true
		countedN = countedN + 1
		if fought[guid] then fought[guid] = nil; foughtN = foughtN - 1 end
	end
	local t = now()
	for i = #recent, 1, -1 do
		if t - recent[i].at > 120 then table.remove(recent, i) end
	end
	-- the XP line comes right with the death; the dead unit may only be seen after the fight.
	-- First look for the same name, then for a kill without a name (protected line).
	for pass = 1, 2 do
		for i, k in ipairs(recent) do
			local sameMob
			if pass == 1 then sameMob = name ~= nil and k.name == name
			else sameMob = k.name == nil or name == nil end
			local fits = (src == "unit" and k.src == "xp") or (src == "xp" and k.src == "unit" and t - k.at <= 5)
			if sameMob and fits then
				table.remove(recent, i)
				if src == "unit" then improve(k, name, classOf(guid, name)) end
				return
			end
		end
	end
	local c = classOf(guid, name)
	bookKill(name, c)
	recent[#recent + 1] = { src = src, name = name, c = c, at = t }
end

local function known(v) if isSecret(v) then return nil end return v end

-- Looks at one unit: remembers enemies in combat with us and counts them once they are dead.
local function watchUnit(unit)
	if not (UnitExists and known(UnitExists(unit))) then return end
	if known(UnitIsPlayer and UnitIsPlayer(unit)) then return end
	local guid = known(UnitGUID and UnitGUID(unit))
	if not guid or counted[guid] then return end
	local dead = UnitIsDead and UnitIsDead(unit)
	if isSecret(dead) then return end
	if dead then
		if not fought[guid] then return end -- an old corpse or someone else's fight
		local denied = _G.UnitIsTapDenied and UnitIsTapDenied(unit)
		if isSecret(denied) then return end  -- try again after the fight
		if denied then
			fought[guid] = nil; foughtN = foughtN - 1
			return
		end
		local name = UnitName and UnitName(unit)
		onKill("unit", text(name) and name or fought[guid].name, guid)
		return
	end
	if fought[guid] then return end
	if not known(UnitAffectingCombat and UnitAffectingCombat("player")) then return end
	if not known(UnitAffectingCombat and UnitAffectingCombat(unit)) then return end
	if _G.UnitCanAttack and not known(UnitCanAttack("player", unit)) then return end
	if foughtN > 300 then wipe(fought); foughtN = 0 end
	local name = UnitName and UnitName(unit)
	fought[guid] = { name = text(name) and name or nil }
	foughtN = foughtN + 1
	cacheUnit(unit)
end

local WATCHED = { "target", "focus", "mouseover", "pettarget", "boss1", "boss2", "boss3", "boss4", "boss5" }
local function watchAll()
	for _, u in ipairs(WATCHED) do watchUnit(u) end
	for i = 1, 40 do
		local u = "nameplate" .. i
		if UnitExists and known(UnitExists(u)) then watchUnit(u) end
	end
end

local function isWatched(unit)
	return type(unit) == "string" and (unit == "target" or unit == "focus" or unit == "pettarget"
		or unit:find("^nameplate%d") or unit:find("^boss%d")) and true or false
end

on("UNIT_HEALTH", function(unit) if isWatched(unit) then watchUnit(unit) end end)
on("UNIT_FLAGS", function(unit) if isWatched(unit) then watchUnit(unit) end end)
on("NAME_PLATE_UNIT_REMOVED", function(unit) if isWatched(unit) then watchUnit(unit) end end)
I.watchAll = watchAll
I.onKill = onKill
I.killFromXP = function(name) onKill("xp", name) end

on("PLAYER_REGEN_DISABLED", function() LZ.After(0.2, function() if s then pcall(watchAll) end end) end)

on("PLAYER_TARGET_CHANGED", function() cacheUnit("target") end)
on("UPDATE_MOUSEOVER_UNIT", function() cacheUnit("mouseover") end)
on("NAME_PLATE_UNIT_ADDED", function(unit) if unit then cacheUnit(unit) end end)
on("INSTANCE_ENCOUNTER_ENGAGE_UNIT", function()
	for i = 1, 5 do
		local u = "boss" .. i
		if UnitExists and UnitExists(u) then
			local guid = UnitGUID(u)
			if guid and not isSecret(guid) then classByGUID[guid] = "boss" end
		end
	end
end)

