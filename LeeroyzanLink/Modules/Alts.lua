-- Alts: overview of all your characters (class, level, gold, professions, bags and
-- bank contents) with an item search: "Who still has the ore?".
-- The character summary is also what Leeroyzan uses for addon recommendations.
local ADDON, LZ = ...
local T, P, C = LZ.T, LZ.P, LZ.C

local mod = LZ:NewModule("Alts", {
	title = { "Twink-Übersicht", "Alt overview" },
	desc = {
		"Klasse, Stufe, Gold, Berufe sowie Taschen- und Bankinhalt aller Charaktere – durchsuchbar. Die Charakterdaten nutzt Leeroyzan für Addon-Empfehlungen.",
		"Class, level, gold, professions and bag/bank contents of all characters – searchable. Leeroyzan uses the character data for addon recommendations.",
	},
})

local bankOpen = false
local scanQueued = false

local function scanContainers(ids)
	local counts = {}
	for _, bag in ipairs(ids) do
		for slot = 1, C.NumSlots(bag) do
			local itemID, count = C.ItemAt(bag, slot)
			if itemID then counts[itemID] = (counts[itemID] or 0) + (count or 1) end
		end
	end
	return counts
end

function mod:UpdateInfo()
	local info = LZ.char.info or {}
	local _, class = UnitClass("player")
	info.class = class
	info.level = UnitLevel("player")
	info.race = select(2, UnitRace("player"))
	info.faction = (UnitFactionGroup and UnitFactionGroup("player")) or nil
	local guild = GetGuildInfo and GetGuildInfo("player")
	info.guild = guild
	if _G.GetSpecialization and _G.GetSpecializationRole then
		local ok, role = pcall(function() return GetSpecializationRole(GetSpecialization()) end)
		if ok and role then info.role = role end
	end
	info.updated = time()
	LZ.char.info = info
	LZ.char.money = GetMoney and GetMoney() or LZ.char.money
end

function mod:UpdateSkills()
	local skills = {}
	if _G.GetNumSkillLines and _G.GetSkillLineInfo then
		for i = 1, GetNumSkillLines() do
			local name, header, _, rank, _, _, maxRank = GetSkillLineInfo(i)
			if name and not header and (maxRank or 0) > 1 and #skills < 20 then
				skills[#skills + 1] = { name = name, rank = rank, max = maxRank }
			end
		end
	elseif _G.GetProfessions and _G.GetProfessionInfo then
		for _, idx in pairs({ GetProfessions() }) do
			if type(idx) == "number" then
				local name, _, rank, maxRank = GetProfessionInfo(idx)
				if name then skills[#skills + 1] = { name = name, rank = rank, max = maxRank } end
			end
		end
	end
	LZ.char.skills = skills
end

function mod:ScanBags()
	LZ.char.bags = scanContainers(C.BagIDs())
	if bankOpen then LZ.char.bank = scanContainers(C.BankBagIDs()) end
	LZ.char.scannedAt = time()
end

function mod:QueueScan()
	if scanQueued then return end
	scanQueued = true
	LZ.After(2, function()
		scanQueued = false
		if self.enabled then self:ScanBags() end
	end)
end

function mod:OnEnable()
	self:UpdateInfo()
	self:UpdateSkills()
	LZ.After(5, function() if self.enabled then self:ScanBags() end end)
end

function mod:OnLogout()
	self:UpdateInfo()
	self:UpdateSkills()
	self:ScanBags()
end

mod:Event("BAG_UPDATE_DELAYED", function(self) self:QueueScan() end)
mod:Event("PLAYER_MONEY", function() LZ.char.money = GetMoney() end)
mod:Event("PLAYER_LEVEL_UP", function(self) self:UpdateInfo() end)
mod:Event("SKILL_LINES_CHANGED", function(self) self:UpdateSkills() end)
mod:Event("BANKFRAME_OPENED", function(self)
	bankOpen = true
	LZ.After(0.5, function() if self.enabled then self:ScanBags() end end)
end)
mod:Event("BANKFRAME_CLOSED", function() bankOpen = false end)

local function classColored(name, class)
	local c = _G.RAID_CLASS_COLORS and class and RAID_CLASS_COLORS[class]
	if c and c.colorStr then return "|c" .. c.colorStr .. name .. "|r" end
	return name
end

function mod:Search(text)
	text = LZ.Trim(text):lower()
	local wantID = tonumber(text)
	local results = {}
	for key, c in pairs(LZ.db.chars) do
		for _, where in ipairs({ "bags", "bank" }) do
			for itemID, n in pairs(c[where] or {}) do
				local name = C.GetItemName(itemID)
				local match = (wantID and itemID == wantID) or (name and name:lower():find(text, 1, true))
				if match then results[#results + 1] = { char = key, where = where, id = itemID, name = name or ("item:" .. itemID), n = n } end
			end
		end
	end
	table.sort(results, function(a, b) if a.name ~= b.name then return a.name < b.name end return a.char < b.char end)
	return results
end

function mod:Summary()
	local n, gold = 0, 0
	for _, c in pairs(LZ.db.chars) do
		if c.info then n = n + 1; gold = gold + (c.money or 0) end
	end
	return string.format(T("%d Charaktere, %s gesamt", "%d characters, %s total"), n, LZ.FormatMoney(gold))
end

function mod:Report()
	local lines = { LZ.Color("ffd100", T("Charaktere", "Characters")) }
	local list, total = {}, 0
	for key, c in pairs(LZ.db.chars) do
		if c.info then list[#list + 1] = { key = key, c = c }; total = total + (c.money or 0) end
	end
	table.sort(list, function(a, b) return (a.c.info.level or 0) > (b.c.info.level or 0) end)
	for _, e in ipairs(list) do
		local i = e.c.info
		local skills = {}
		for _, s in ipairs(e.c.skills or {}) do skills[#skills + 1] = string.format("%s %d", s.name, s.rank or 0) end
		lines[#lines + 1] = string.format("%s  %s %d  –  %s  (%s)", classColored(e.key, i.class), i.class or "?", i.level or 0,
			LZ.FormatMoney(e.c.money or 0), LZ.Ago(e.c.lastSeen))
		if #skills > 0 then lines[#lines + 1] = "    " .. table.concat(skills, ", ") end
	end
	lines[#lines + 1] = ""
	lines[#lines + 1] = string.format(T("Gold gesamt: %s", "Total gold: %s"), LZ.FormatMoney(total))
	lines[#lines + 1] = T("Suche: /lz suche <Item oder ID>", "Search: /lz find <item or id>")
	return table.concat(lines, "\n")
end

mod:Command({ "suche", "find" }, function(self, rest)
	if rest == "" then LZ.UI:Show(self.name) return end
	local res = self:Search(rest)
	if #res == 0 then self:Print(T("Nichts gefunden (nur bereits gespeicherte Taschen/Bank-Inhalte).", "Nothing found (only stored bag/bank contents).")) return end
	for i = 1, math.min(#res, 25) do
		local r = res[i]
		self:Print(string.format("%s: %s ×%d (%s)", r.char, r.name, r.n, r.where == "bank" and T("Bank", "bank") or T("Taschen", "bags")))
	end
	if #res > 25 then self:Print(string.format(T("… und %d weitere", "… and %d more"), #res - 25)) end
end, { "suche <Item|ID>", "Item bei allen Charakteren suchen", "find an item across all characters" })
