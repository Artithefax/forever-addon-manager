-- Tasks: per-character checklist with daily/weekly resets, instance lockouts and
-- spell cooldown tracking (e.g. profession cooldowns).
local ADDON, LZ = ...
local T, P, C = LZ.T, LZ.P, LZ.C

local mod = LZ:NewModule("Tasks", {
	title = { "Checklisten", "Checklists" },
	desc = {
		"Abhakbare Aufgaben pro Charakter mit täglichem/wöchentlichem Reset, Instanz-Sperren und Cooldown-Verfolgung.",
		"Checkable tasks per character with daily/weekly reset, instance lockouts and cooldown tracking.",
	},
})

local KINDS = {
	daily = { "täglich", "daily" }, weekly = { "wöchentlich", "weekly" }, once = { "einmalig", "once" },
}
local ALIAS = {
	["täglich"] = "daily", ["taeglich"] = "daily", daily = "daily",
	["wöchentlich"] = "weekly", ["woechentlich"] = "weekly", weekly = "weekly",
	einmalig = "once", once = "once",
}

local function nextReset(kind)
	local secs
	if kind == "daily" then
		if _G.C_DateAndTime and C_DateAndTime.GetSecondsUntilDailyReset then secs = C_DateAndTime.GetSecondsUntilDailyReset() end
		if not secs and _G.GetQuestResetTime then secs = GetQuestResetTime() end
		return time() + ((secs and secs > 0) and secs or 86400)
	elseif kind == "weekly" then
		if _G.C_DateAndTime and C_DateAndTime.GetSecondsUntilWeeklyReset then secs = C_DateAndTime.GetSecondsUntilWeeklyReset() end
		return time() + ((secs and secs > 0) and secs or 7 * 86400)
	end
end
mod.NextReset = nextReset

local function tasks()
	LZ.char.tasks = LZ.char.tasks or {}
	return LZ.char.tasks
end

function mod:CheckResets()
	local now, changed = time(), 0
	for _, t in ipairs(tasks()) do
		if t.reset and now >= t.reset then
			if t.done then changed = changed + 1 end
			t.done = false
			t.reset = nextReset(t.kind)
		end
	end
	return changed
end

function mod:Add(kind, text)
	text = LZ.Trim(text)
	if text == "" then return false end
	local list = tasks()
	list[#list + 1] = { text = text:sub(1, 120), kind = kind, done = false, reset = nextReset(kind) }
	return true
end

function mod:UpdateLockouts()
	local out = {}
	if _G.GetNumSavedInstances and _G.GetSavedInstanceInfo then
		for i = 1, GetNumSavedInstances() do
			local name, _, reset, _, locked, _, _, isRaid = GetSavedInstanceInfo(i)
			if name and locked and reset and reset > 0 then
				out[#out + 1] = { name = name, reset = time() + reset, raid = isRaid and true or nil }
			end
		end
	end
	LZ.char.lockouts = out
end

function mod:UpdateCooldowns()
	local cds = LZ.char.cooldowns
	if not cds then return end
	for id, cd in pairs(cds) do
		local start, duration = C.GetSpellCooldown(id)
		if type(start) == "number" and type(duration) == "number" then
			if start > 0 and duration > 1.5 then
				cd.ready = time() + math.max(0, (start + duration) - GetTime())
			else
				cd.ready = 0
			end
		end
	end
end

function mod:OnEnable()
	self:CheckResets()
	if _G.RequestRaidInfo then pcall(RequestRaidInfo) end
end

mod:Event("UPDATE_INSTANCE_INFO", function(self) self:UpdateLockouts() end)
mod:Event("PLAYER_ENTERING_WORLD", function(self) self:CheckResets() end)

function mod:OnLogout()
	self:UpdateCooldowns()
end

function mod:Summary()
	local open = 0
	for _, t in ipairs(tasks()) do if not t.done then open = open + 1 end end
	return string.format(T("%d offene Aufgaben", "%d open tasks"), open)
end

function mod:TooltipLines()
	local open = 0
	for _, t in ipairs(tasks()) do if not t.done then open = open + 1 end end
	if open > 0 then return { string.format(T("%d offene Aufgaben", "%d open tasks"), open) } end
end

function mod:Report()
	self:CheckResets()
	local lines = { LZ.Color("ffd100", T("Aufgaben", "Tasks") .. " – " .. (LZ.charKey or "")) }
	local list = tasks()
	if #list == 0 then lines[#lines + 1] = T("Keine. Hinzufügen: /lz todo add [täglich|wöchentlich] Text", "None. Add one: /lz todo add [daily|weekly] text") end
	for i, t in ipairs(list) do
		local box = t.done and LZ.Color("40ff40", "[x]") or "[ ]"
		local reset = t.reset and ("  " .. LZ.Color("a0a0a0", T("Reset in ", "resets in ") .. LZ.Duration(t.reset - time()))) or ""
		lines[#lines + 1] = string.format("%d. %s %s (%s)%s", i, box, t.text, P(KINDS[t.kind] or KINDS.once), reset)
	end

	lines[#lines + 1] = ""
	lines[#lines + 1] = LZ.Color("ffd100", T("Instanz-Sperren", "Instance lockouts"))
	local lock = LZ.char.lockouts or {}
	local shown = 0
	for _, l in ipairs(lock) do
		if l.reset > time() then
			shown = shown + 1
			lines[#lines + 1] = string.format("%s – %s", l.name, LZ.Duration(l.reset - time()))
		end
	end
	if shown == 0 then lines[#lines + 1] = T("Keine bekannt.", "None known.") end

	lines[#lines + 1] = ""
	lines[#lines + 1] = LZ.Color("ffd100", "Cooldowns")
	local any = false
	for id, cd in pairs(LZ.char.cooldowns or {}) do
		any = true
		local state = (cd.ready or 0) <= time() and LZ.Color("40ff40", T("bereit", "ready")) or LZ.Duration(cd.ready - time())
		lines[#lines + 1] = string.format("%s: %s", cd.name or tostring(id), state)
	end
	if not any then lines[#lines + 1] = T("Keine. Verfolgen: /lz todo cd <Zauber-ID>", "None. Track one: /lz todo cd <spell id>") end
	return table.concat(lines, "\n")
end

mod.actions = {
	{ label = { "Erledigt zurücksetzen", "Uncheck all" }, fn = function() for _, t in ipairs(tasks()) do t.done = false end; LZ.UI:Refresh() end },
}

mod:Command({ "todo", "tasks", "aufgaben" }, function(self, rest)
	local sub, arg = rest:match("^(%S*)%s*(.-)$")
	sub = (sub or ""):lower()
	if sub == "" then LZ.UI:Show(self.name) return end
	local list = tasks()
	if sub == "add" or sub == "neu" then
		local first, remainder = arg:match("^(%S+)%s*(.-)$")
		local kind = first and ALIAS[first:lower()]
		local text = kind and remainder or arg
		if self:Add(kind or "once", text) then self:Print(T("Aufgabe hinzugefügt.", "Task added.")) end
	elseif sub == "done" or sub == "erledigt" then
		local t = list[tonumber(arg) or 0]
		if t then t.done = not t.done; self:Print(t.text .. ": " .. (t.done and "[x]" or "[ ]")) else self:Print(T("Nummer unbekannt.", "Unknown number.")) end
	elseif sub == "del" or sub == "löschen" or sub == "loeschen" then
		local n = tonumber(arg or "")
		if n and list[n] then table.remove(list, n); self:Print(T("Gelöscht.", "Deleted.")) else self:Print(T("Nummer unbekannt.", "Unknown number.")) end
	elseif sub == "cd" then
		local id = LZ.ItemID(arg) or tonumber(arg:match("spell:(%d+)") or "")
		local name = id and C.GetSpellName(id)
		if not id or not name then self:Print(T("Zauber nicht gefunden.", "Spell not found.")) return end
		LZ.char.cooldowns = LZ.char.cooldowns or {}
		LZ.char.cooldowns[id] = { name = name, ready = 0 }
		self:Print(string.format(T("Verfolge Cooldown: %s", "Tracking cooldown: %s"), name))
	else
		self:Print(T("Befehle: add, done, del, cd – siehe /lz hilfe", "Commands: add, done, del, cd – see /lz help"))
	end
end, { "todo add [täglich|wöchentlich] <Text> | done <Nr> | del <Nr> | cd <ID>", "Checkliste verwalten", "manage the checklist" })
