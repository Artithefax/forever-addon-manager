-- LeeroyMode: death counter with statistics ("deaths from reckless pulls") and an
-- optional battle cry. Purely for fun.
local ADDON, LZ = ...
local T, P, C = LZ.T, LZ.P, LZ.C

local mod = LZ:NewModule("LeeroyMode", {
	title = { "Leeroy-Modus", "Leeroy mode" },
	desc = {
		"Todeszähler mit Statistik (Tode kurz nach Kampfbeginn = „unüberlegter Pull“), Leeroy-Rang und optional ein Schlachtruf.",
		"Death counter with statistics (deaths shortly after combat starts = \"reckless pull\"), a Leeroy rank and an optional battle cry.",
	},
	defaults = { cry = false },
	options = { { key = "cry", label = { "Schlachtruf bei Kampfbeginn (nur lokal sichtbar)", "Battle cry when combat starts (local only)" } } },
})

local RECKLESS_SECONDS = 15
local combatStart = nil

local RANKS = {
	{ 0, { "Vorsichtiger Abenteurer", "Cautious adventurer" } },
	{ 10, { "Mutiger Draufgänger", "Bold daredevil" } },
	{ 30, { "Waghalsiger Pull-Meister", "Reckless pull master" } },
	{ 60, { "Ruhmreicher Wipe-Veteran", "Glorious wipe veteran" } },
	{ 100, { "Leeroy Jenkins persönlich", "Leeroy Jenkins himself" } },
}

local function stats()
	LZ.char.leeroy = LZ.char.leeroy or { deaths = 0, pullDeaths = 0, byZone = {} }
	return LZ.char.leeroy
end

local function totals()
	local d, p = 0, 0
	for _, c in pairs(LZ.db.chars) do
		if c.leeroy then d = d + (c.leeroy.deaths or 0); p = p + (c.leeroy.pullDeaths or 0) end
	end
	return d, p
end

local function rankFor(deaths)
	local r = RANKS[1][2]
	for _, e in ipairs(RANKS) do if deaths >= e[1] then r = e[2] end end
	return P(r)
end

mod:Event("PLAYER_REGEN_DISABLED", function(self)
	combatStart = GetTime and GetTime() or 0
	if self.opt.cry then
		local msg = "LEEEROOOY JENKINS!"
		if _G.UIErrorsFrame and UIErrorsFrame.AddMessage then UIErrorsFrame:AddMessage(msg, 1, 0.5, 0) end
		if _G.DoEmote then pcall(DoEmote, "CHARGE") end
	end
end)

mod:Event("PLAYER_REGEN_ENABLED", function() combatStart = nil end)

mod:Event("PLAYER_DEAD", function(self)
	local s = stats()
	s.deaths = s.deaths + 1
	local zone = (GetRealZoneText and GetRealZoneText()) or (GetZoneText and GetZoneText()) or "?"
	if zone == "" then zone = "?" end
	s.byZone[zone] = (s.byZone[zone] or 0) + 1
	local reckless = combatStart and ((GetTime() - combatStart) < RECKLESS_SECONDS)
	if reckless then s.pullDeaths = s.pullDeaths + 1 end
	s.last = time()
	self:Print(reckless and T("Wenigstens hast du Hühnchen. … Unüberlegter Pull gezählt!", "At least you have chicken. … Reckless pull counted!")
		or string.format(T("Tod Nr. %d notiert.", "Death #%d recorded."), s.deaths))
	combatStart = nil
end)

function mod:Summary()
	local d = totals()
	return string.format(T("%d Tode – %s", "%d deaths – %s"), d, rankFor(d))
end

function mod:Report()
	local s = stats()
	local d, p = totals()
	local lines = {
		LZ.Color("ffd100", T("Leeroy-Rang: ", "Leeroy rank: ") .. rankFor(d)),
		"",
		string.format(T("Dieser Charakter: %d Tode, davon %d unüberlegte Pulls", "This character: %d deaths, %d of them reckless pulls"), s.deaths, s.pullDeaths),
		string.format(T("Alle Charaktere: %d Tode, davon %d unüberlegte Pulls", "All characters: %d deaths, %d of them reckless pulls"), d, p),
		"",
		LZ.Color("ffd100", T("Tode nach Zone (dieser Charakter)", "Deaths by zone (this character)")),
	}
	local zones = {}
	for z, n in pairs(s.byZone) do zones[#zones + 1] = { z = z, n = n } end
	table.sort(zones, function(a, b) return a.n > b.n end)
	if #zones == 0 then lines[#lines + 1] = T("Noch keine.", "None yet.") end
	for i = 1, math.min(#zones, 10) do lines[#lines + 1] = string.format("%s: %d", zones[i].z, zones[i].n) end
	return table.concat(lines, "\n")
end

mod:Command({ "leeroy" }, function(self, rest)
	local want = LZ.ParseState(rest:match("^%S*%s*(%S*)$"))
	if rest:lower():find("^schrei") or rest:lower():find("^cry") then
		if want == nil then want = not self.opt.cry end
		self.opt.cry = want
		self:Print(T("Schlachtruf: ", "Battle cry: ") .. (want and T("an", "on") or T("aus", "off")))
		return
	end
	LZ.UI:Show(self.name)
end, { "leeroy [schrei an|aus]", "Todesstatistik / Schlachtruf", "death statistics / battle cry" })
