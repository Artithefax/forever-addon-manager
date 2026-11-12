-- SessionCombat: the combat log (damage, healing, swings, kills of other units) for the session
-- log. Part of the Session module (Session.lua); shared helpers come through LZ.SessionInternal.
-- Clients without a readable combat log (WoW Forever) are noticed and not listened to.
local ADDON, LZ = ...
local I = LZ.SessionInternal
local P, add, band, isSecret, now, rt = LZ.P, I.add, I.band, I.isSecret, I.now, I.rt
local s
I.onSession(function(new) s = new end)
local clFrame
local function onKill(...) return I.onKill(...) end

----------------------------------------------------------------------
-- Combat log
----------------------------------------------------------------------
local MINE = _G.COMBATLOG_OBJECT_AFFILIATION_MINE or 0x1
local GROUP = (_G.COMBATLOG_OBJECT_AFFILIATION_MINE or 0x1) + (_G.COMBATLOG_OBJECT_AFFILIATION_PARTY or 0x2) + (_G.COMBATLOG_OBJECT_AFFILIATION_RAID or 0x4)
local FRIENDLY = _G.COMBATLOG_OBJECT_REACTION_FRIENDLY or 0x10
local TYPE_PLAYER = _G.COMBATLOG_OBJECT_TYPE_PLAYER or 0x400
local TYPE_NPC = _G.COMBATLOG_OBJECT_TYPE_NPC or 0x800
local PETS = (_G.COMBATLOG_OBJECT_TYPE_PET or 0x1000) + (_G.COMBATLOG_OBJECT_TYPE_GUARDIAN or 0x2000)

local tagged, taggedCount = {}, 0
local MELEE = { "Nahkampf", "Melee" }

local function has(flags, mask) return flags and band and band(flags, mask) ~= 0 end

local function tag(guid, flags)
	if tagged[guid] or not guid then return end
	if has(flags, FRIENDLY) then return end
	if taggedCount > 3000 then wipe(tagged); taggedCount = 0 end
	tagged[guid] = true
	taggedCount = taggedCount + 1
end

local function dealt(cb, spell, amount, crit, pet)
	if pet then cb.dmgPet = cb.dmgPet + amount end
	cb.dmg = cb.dmg + amount
	cb.hits = cb.hits + 1
	if crit then cb.crits = cb.crits + 1 end
	local e = cb.spells[spell]
	if not e then
		e = { 0, 0, 0 } -- amount, hits, crits
		cb.spells[spell] = e
	end
	e[1], e[2] = e[1] + amount, e[2] + 1
	if crit then e[3] = e[3] + 1 end
	if amount > cb.maxHit then
		cb.maxHit, cb.maxHitSpell, cb.maxHitCrit = amount, spell, crit and true or nil
	end
end

local function healed(cb, spell, amount, over, crit)
	local eff = math.max(0, amount - (over or 0))
	cb.heal = cb.heal + eff
	cb.over = cb.over + (over or 0)
	local e = cb.heals[spell]
	if not e then
		e = { 0, 0, 0 } -- effective, casts, overheal
		cb.heals[spell] = e
	end
	e[1], e[2], e[3] = e[1] + eff, e[2] + 1, e[3] + (over or 0)
	if eff > cb.maxHeal then cb.maxHeal, cb.maxHealSpell = eff, spell end
end

local function taken(cb, by, spell, amount)
	cb.taken = cb.taken + amount
	if by then add(cb.takenBy, by, amount) end
	if amount > cb.maxTaken then cb.maxTaken, cb.maxTakenBy, cb.maxTakenSpell = amount, by, spell end
	rt.lastHit = { by = by, spell = spell, at = now() }
end

local function countKill(guid, name, flags)
	if tagged[guid] then
		tagged[guid] = nil
		taggedCount = taggedCount - 1
	end
	if has(flags, TYPE_PLAYER) then
		s.kills.players = s.kills.players + 1
		return
	end
	if not has(flags, TYPE_NPC) then return end
	onKill("unit", name, guid)
end

local SWING_RESULT = { MISS = "miss", DODGE = "dodge", PARRY = "parry", BLOCK = "block", ABSORB = "miss", RESIST = "miss", EVADE = "miss", IMMUNE = "miss", DEFLECT = "miss", REFLECT = "miss" }

local function processCombatLog()
	local _, sub, _, srcGUID, srcName, srcFlags, _, dstGUID, dstName, dstFlags, _,
		a12, a13, a14, a15, a16, a17, a18, a19, a20, a21 = CombatLogGetCurrentEventInfo()
	if isSecret(sub) or isSecret(srcGUID) or isSecret(dstGUID) or isSecret(srcFlags) or isSecret(dstFlags) then
		rt.secretCL = (rt.secretCL or 0) + 1
		if rt.secretCL == 25 then s.combat.err = "protected by the game client" end
		-- only protected events so far (e.g. WoW Forever): stop listening, every event would cost time for nothing
		if rt.secretCL >= 200 and not rt.openCL and clFrame then clFrame:UnregisterAllEvents() end
		return
	end
	rt.openCL = true
	local cb = s.combat
	local me = rt.guid
	local fromMe = srcGUID == me
	local fromPet = not fromMe and has(srcFlags, MINE) and has(srcFlags, PETS)
	local toMe = dstGUID == me

	if sub == "UNIT_DIED" then
		if tagged[dstGUID] then countKill(dstGUID, dstName, dstFlags) end
		return
	elseif sub == "PARTY_KILL" then
		if fromMe or fromPet then s.kills.blows = s.kills.blows + 1 end
		return
	end

	if sub == "SWING_DAMAGE" then
		local amount, crit, glancing = a12 or 0, a18, a19
		if fromMe or fromPet then
			if fromMe then
				local sw = cb.swings
				sw.n, sw.hit = sw.n + 1, sw.hit + 1
				if crit then sw.crit = sw.crit + 1 end
				if glancing then sw.glance = sw.glance + 1 end
			end
			dealt(cb, P(MELEE), amount, crit, fromPet)
			tag(dstGUID, dstFlags)
		elseif has(srcFlags, GROUP) then
			tag(dstGUID, dstFlags)
		end
		if toMe then
			local st = cb.swingsTaken
			st.n, st.hit = st.n + 1, st.hit + 1
			if crit then st.crit = st.crit + 1 end
			taken(cb, srcName, P(MELEE), amount)
		end
		return
	elseif sub == "SWING_MISSED" then
		local r = SWING_RESULT[a12 or ""] or "miss"
		if fromMe then
			local sw = cb.swings
			sw.n = sw.n + 1
			sw[r] = (sw[r] or 0) + 1
			tag(dstGUID, dstFlags)
		end
		if toMe then
			local st = cb.swingsTaken
			st.n = st.n + 1
			st[r] = (st[r] or 0) + 1
		end
		return
	elseif sub == "SPELL_DAMAGE" or sub == "SPELL_PERIODIC_DAMAGE" or sub == "RANGE_DAMAGE" or sub == "DAMAGE_SHIELD" or sub == "DAMAGE_SPLIT" then
		local spell, amount, crit = a13 or "?", a15 or 0, a21
		if fromMe or fromPet then
			dealt(cb, spell, amount, crit, fromPet)
			tag(dstGUID, dstFlags)
		elseif has(srcFlags, GROUP) then
			tag(dstGUID, dstFlags)
		end
		if toMe then taken(cb, srcName, spell, amount) end
		return
	elseif sub == "ENVIRONMENTAL_DAMAGE" then
		if toMe then
			local kind, amount = a12 or "?", a13 or 0
			add(cb.env, kind, amount)
			taken(cb, nil, kind, amount)
		end
		return
	elseif sub == "SPELL_HEAL" or sub == "SPELL_PERIODIC_HEAL" then
		local spell, amount, over, crit = a13 or "?", a15 or 0, a16 or 0, a18
		if fromMe then healed(cb, spell, amount, over, crit) end
		if toMe then cb.healTaken = cb.healTaken + math.max(0, amount - over) end
		return
	elseif sub == "SPELL_CAST_SUCCESS" then
		if fromMe and a13 then
			cb.castN = cb.castN + 1
			add(cb.casts, a13, 1)
		end
		return
	elseif sub == "SPELL_AURA_APPLIED" then
		local spell, auraType = a13, a15
		if toMe and spell then
			if auraType == "BUFF" then
				cb.buffN = cb.buffN + 1
				add(cb.buffs, spell, 1)
			else
				cb.debuffN = cb.debuffN + 1
				add(cb.debuffs, spell, 1)
			end
		elseif fromMe and auraType == "BUFF" and not toMe then
			cb.buffsGiven = cb.buffsGiven + 1
		end
		return
	elseif sub == "SPELL_INTERRUPT" then
		if fromMe or fromPet then cb.interrupts = cb.interrupts + 1 end
		return
	elseif sub == "SPELL_DISPEL" or sub == "SPELL_STOLEN" then
		if fromMe or fromPet then cb.dispels = cb.dispels + 1 end
		return
	elseif sub == "SPELL_MISSED" or sub == "RANGE_MISSED" or sub == "SPELL_PERIODIC_MISSED" then
		if fromMe or fromPet or has(srcFlags, GROUP) then tag(dstGUID, dstFlags) end
		return
	end
end

local clErrors = 0

local function onCombatLog()
	if not s then return end
	local ok, err = pcall(processCombatLog)
	if not ok then
		clErrors = clErrors + 1
		if clErrors >= 5 then
			s.combat.err = tostring(err):sub(1, 200)
			if clFrame then clFrame:UnregisterAllEvents() end
		end
	end
end

local function startCombatLog()
	if not _G.CombatLogGetCurrentEventInfo or not band then
		s.combat.err = "no combat log API"
		return
	end
	clFrame = clFrame or CreateFrame("Frame")
	clFrame:SetScript("OnEvent", onCombatLog)
	clErrors = 0
	pcall(clFrame.RegisterEvent, clFrame, "COMBAT_LOG_EVENT_UNFILTERED")
end


I.startCombatLog = startCombatLog
function I.stopCombatLog()
	if clFrame then clFrame:UnregisterAllEvents() end
end
