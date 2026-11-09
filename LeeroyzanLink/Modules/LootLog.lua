-- LootLog: diary of what dropped where. Items below the quality threshold are skipped.
local ADDON, LZ = ...
local T, P, C = LZ.T, LZ.P, LZ.C

local mod = LZ:NewModule("LootLog", {
	title = { "Beute-Tagebuch", "Loot diary" },
	desc = {
		"Protokolliert Beute mit Zone und Quelle ab einer Mindest-Qualität (Standard: ungewöhnlich/grün).",
		"Logs loot with zone and source from a minimum quality (default: uncommon/green).",
	},
	defaults = { minQuality = 2, others = false },
	options = { { key = "others", label = { "Auch Beute anderer Spieler mitschreiben", "Also log other players' loot" } } },
})

local MAX_ENTRIES = 500
local QUALITY_NAMES = { [0] = { "Schlecht", "Poor" }, { "Gewöhnlich", "Common" }, { "Ungewöhnlich", "Uncommon" }, { "Selten", "Rare" }, { "Episch", "Epic" }, { "Legendär", "Legendary" } }
local QUALITY_COLORS = { [0] = "9d9d9d", "ffffff", "1eff00", "0070dd", "a335ee", "ff8000" }

local selfPatterns, otherPatterns

local function buildPatterns()
	local function pat(name) return LZ.FormatToPattern(_G[name]) end
	-- multiples first: the plain pattern would also swallow "...x2" lines
	selfPatterns = {
		pat("LOOT_ITEM_SELF_MULTIPLE"), pat("LOOT_ITEM_SELF"),
		pat("LOOT_ITEM_PUSHED_SELF_MULTIPLE"), pat("LOOT_ITEM_PUSHED_SELF"),
	}
	otherPatterns = { pat("LOOT_ITEM_MULTIPLE"), pat("LOOT_ITEM"), pat("LOOT_ITEM_PUSHED_MULTIPLE"), pat("LOOT_ITEM_PUSHED") }
end

function mod:OnInit()
	LZ.gdb.loot = LZ.gdb.loot or {}
end

function mod:OnEnable()
	buildPatterns()
end

local function matchAny(patterns, msg)
	for _, p in ipairs(patterns or {}) do
		if p then
			local a, b = msg:match(p)
			if a then return a, b end
		end
	end
end

mod:Event("CHAT_MSG_LOOT", function(self, _, msg)
	if type(msg) ~= "string" then return end
	local mine = matchAny(selfPatterns, msg)
	local looter
	if not mine then
		if not self.opt.others then return end
		looter = matchAny(otherPatterns, msg)
		if not looter then return end
	end
	local link = msg:match("|c[^|]+|Hitem:[^|]+|h%[.-%]|h|r")
	if not link then return end
	local itemID = LZ.ItemID(link)
	local quality = LZ.QualityFromLink(link) or 1
	if quality < (self.opt.minQuality or 2) then return end
	local count = tonumber(msg:match("|h|rx(%d+)")) or 1

	local source
	if UnitExists and UnitExists("target") and UnitIsDead and UnitIsDead("target") then source = UnitName("target") end
	local list = LZ.gdb.loot
	list[#list + 1] = {
		t = time(), item = itemID, link = link, q = quality, n = count,
		zone = (GetRealZoneText and GetRealZoneText()) or (GetZoneText and GetZoneText()) or nil,
		src = source, who = looter, char = looter == nil and LZ.charKey or nil,
	}
	while #list > MAX_ENTRIES do table.remove(list, 1) end
	if not looter then LZ.Emit("RARE_LOOT", itemID, quality, link) end
end)

function mod:Summary()
	return string.format(T("%d Einträge", "%d entries"), #LZ.gdb.loot)
end

function mod:Report()
	local list = LZ.gdb.loot
	local lines = {}
	local counts = {}
	for _, e in ipairs(list) do counts[e.q] = (counts[e.q] or 0) + (e.n or 1) end
	lines[#lines + 1] = LZ.Color("ffd100", T("Übersicht", "Overview"))
	local any = false
	for q = 0, 5 do
		if counts[q] then
			any = true
			lines[#lines + 1] = string.format("%s: %d", LZ.Color(QUALITY_COLORS[q], P(QUALITY_NAMES[q])), counts[q])
		end
	end
	if not any then lines[#lines + 1] = T("Noch keine Beute aufgezeichnet.", "No loot recorded yet.") end
	lines[#lines + 1] = ""
	lines[#lines + 1] = LZ.Color("ffd100", T("Zuletzt", "Latest"))
	for i = #list, math.max(1, #list - 14), -1 do
		local e = list[i]
		lines[#lines + 1] = string.format("%s%s – %s%s%s", e.link, e.n > 1 and (" x" .. e.n) or "", e.zone or "?",
			e.src and (" (" .. e.src .. ")") or "", e.who and (" [" .. e.who .. "]") or "")
	end
	lines[#lines + 1] = ""
	lines[#lines + 1] = T("Mindest-Qualität: /lz beute qualität 0-5", "Minimum quality: /lz loot quality 0-5")
	return table.concat(lines, "\n")
end

mod.actions = {
	{ label = { "Tagebuch leeren", "Clear diary" }, fn = function() LZ.gdb.loot = {}; LZ.UI:Refresh() end },
}

mod:Command({ "beute", "loot" }, function(self, rest)
	local sub, arg = rest:match("^(%S*)%s*(.-)$")
	sub = (sub or ""):lower()
	if sub == "qualität" or sub == "qualitaet" or sub == "quality" then
		local q = tonumber(arg)
		if q and q >= 0 and q <= 5 then
			self.opt.minQuality = math.floor(q)
			self:Print(string.format(T("Mindest-Qualität: %s", "Minimum quality: %s"), P(QUALITY_NAMES[self.opt.minQuality])))
		else
			self:Print(T("Wert 0 (grau) bis 5 (legendär).", "Value 0 (poor) to 5 (legendary)."))
		end
		return
	end
	LZ.UI:Show(self.name)
end, { "beute [qualität 0-5]", "Beute-Tagebuch / Mindest-Qualität", "loot diary / minimum quality" })
