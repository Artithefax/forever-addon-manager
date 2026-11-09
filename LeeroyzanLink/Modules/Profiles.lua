-- Profiles: named sets of enabled addons ("Raid", "Questing", "Minimal"), switchable
-- in game. Profiles you save here are exported to Leeroyzan; profiles defined in
-- Leeroyzan arrive through Data.lua and show up here as well.
local ADDON, LZ = ...
local T, P, C = LZ.T, LZ.P, LZ.C

local mod = LZ:NewModule("Profiles", {
	group = "link",
	title = { "Addon-Profile", "Addon profiles" },
	desc = {
		"Addon-Sätze umschalten (Raid, Questen, Minimal). Profile aus Leeroyzan werden mit angezeigt; pro Charakter kann ein Profil automatisch gelten.",
		"Switch addon sets (raid, questing, minimal). Profiles from Leeroyzan are listed too; a character can have an automatic profile.",
	},
	defaults = { autoReload = false },
	options = { { key = "autoReload", label = { "Nach automatischem Profilwechsel sofort neu laden", "Reload right away after an automatic profile switch" } } },
})

local function isProtected(name)
	return name == ADDON or name:find("^Blizzard_") ~= nil
end

function mod:OnInit()
	LZ.gdb.profiles = LZ.gdb.profiles or {}
end

-- Returns profile table + source ("local" | "leeroyzan")
function mod:Get(name)
	local key = (name or ""):lower()
	for pname, p in pairs(LZ.gdb.profiles) do
		if pname:lower() == key then return p, "local", pname end
	end
	if LZ.import then
		for _, p in ipairs(LZ.import.profiles) do
			if p.name:lower() == key then return p, "leeroyzan", p.name end
		end
	end
end

function mod:Names()
	local out = {}
	for pname in pairs(LZ.gdb.profiles) do out[#out + 1] = { name = pname, source = "local" } end
	if LZ.import then
		for _, p in ipairs(LZ.import.profiles) do out[#out + 1] = { name = p.name, source = "leeroyzan" } end
	end
	table.sort(out, function(a, b) return a.name:lower() < b.name:lower() end)
	return out
end

function mod:Capture(name)
	local enable = {}
	for i = 1, C.GetNumAddOns() do
		local info = C.GetAddOnInfo(i)
		if info and info.name and not isProtected(info.name) and C.IsEnabled(info.name) then
			enable[#enable + 1] = info.name
		end
	end
	LZ.gdb.profiles[name] = { enable = enable, created = time() }
	return #enable
end

-- Applies a profile; returns number of addons whose state changed (-1 if unknown).
function mod:Apply(name)
	local p = self:Get(name)
	if not p then return -1 end
	local want = {}
	for _, n in ipairs(p.enable or {}) do
		want[n] = true
		for _, d in ipairs(C.GetDeps(n)) do want[d] = true end
	end
	local changes = 0
	for i = 1, C.GetNumAddOns() do
		local info = C.GetAddOnInfo(i)
		local n = info and info.name
		if n and not isProtected(n) then
			local should = want[n] and true or false
			if C.IsEnabled(n) ~= should then
				C.SetAddOnEnabled(n, should)
				changes = changes + 1
			end
		end
	end
	return changes
end

mod:Event("PLAYER_ENTERING_WORLD", function(self, _, isLogin)
	if not isLogin then return end -- only on a fresh login, not on /reload or zoning
	local auto = LZ.char and LZ.char.autoProfile
	if not auto then return end
	local changes = self:Apply(auto)
	if changes > 0 then
		self:Print(string.format(T("Profil '%s' angewendet (%d Änderungen).", "Profile '%s' applied (%d changes)."), auto, changes))
		if self.opt.autoReload then ReloadUI() else self:Print(T("Zum Übernehmen: /lz reload", "To take effect: /lz reload")) end
	end
end)

function mod:Summary()
	local n = #self:Names()
	local auto = LZ.char and LZ.char.autoProfile
	return string.format(T("%d Profile", "%d profiles"), n) .. (auto and (", auto: " .. auto) or "")
end

function mod:Report()
	local lines = {}
	lines[#lines + 1] = LZ.Color("ffd100", T("Profile", "Profiles"))
	local names = self:Names()
	if #names == 0 then lines[#lines + 1] = T("Noch keine. Speichern: /lz profil speichern <Name>", "None yet. Save one: /lz profile save <name>") end
	local auto = LZ.char and LZ.char.autoProfile
	for _, e in ipairs(names) do
		local p = self:Get(e.name)
		local mark = e.source == "leeroyzan" and LZ.Color("40c0ff", " [Leeroyzan]") or ""
		local isAuto = (auto and auto:lower() == e.name:lower()) and LZ.Color("40ff40", "  (auto)") or ""
		lines[#lines + 1] = string.format("%s%s%s – %d Addons", e.name, mark, isAuto, #(p.enable or {}))
	end
	lines[#lines + 1] = ""
	lines[#lines + 1] = T("Befehle:", "Commands:")
	lines[#lines + 1] = "/lz profil speichern <Name>"
	lines[#lines + 1] = "/lz profil laden <Name> [reload]"
	lines[#lines + 1] = "/lz profil auto <Name|aus>"
	lines[#lines + 1] = "/lz profil löschen <Name>"
	return table.concat(lines, "\n")
end

mod.actions = {
	{ label = { "UI neu laden", "Reload UI" }, fn = function() ReloadUI() end },
}

mod:Command({ "profil", "profile" }, function(self, rest)
	local sub, arg = rest:match("^(%S*)%s*(.-)$")
	sub = (sub or ""):lower()
	if sub == "" then LZ.UI:Show(self.name) return end

	if sub == "speichern" or sub == "save" then
		if arg == "" then self:Print(T("Name fehlt.", "Name missing.")) return end
		local n = self:Capture(arg)
		self:Print(string.format(T("Profil '%s' gespeichert (%d Addons).", "Profile '%s' saved (%d addons)."), arg, n))
	elseif sub == "laden" or sub == "load" then
		local name, flag = arg:match("^(.-)%s+reload$")
		name = name or arg
		local changes = self:Apply(name)
		if changes < 0 then self:Print(T("Profil nicht gefunden: ", "Profile not found: ") .. name) return end
		self:Print(string.format(T("Profil '%s': %d Änderungen.", "Profile '%s': %d changes."), name, changes))
		if flag and changes > 0 then ReloadUI()
		elseif changes > 0 then self:Print(T("Zum Übernehmen: /lz reload", "To take effect: /lz reload")) end
	elseif sub == "auto" then
		local off = LZ.ParseState(arg) == false
		if off then LZ.char.autoProfile = nil; self:Print(T("Automatisches Profil aus.", "Automatic profile off.")) return end
		if not self:Get(arg) then self:Print(T("Profil nicht gefunden: ", "Profile not found: ") .. arg) return end
		LZ.char.autoProfile = arg
		self:Print(string.format(T("Dieser Charakter lädt beim Login Profil '%s'.", "This character loads profile '%s' on login."), arg))
	elseif sub == "löschen" or sub == "loeschen" or sub == "delete" then
		local _, source, real = self:Get(arg)
		if source == "local" then LZ.gdb.profiles[real] = nil; self:Print(T("Profil gelöscht.", "Profile deleted."))
		elseif source == "leeroyzan" then self:Print(T("Dieses Profil kommt aus Leeroyzan – dort löschen.", "That profile comes from Leeroyzan – delete it there."))
		else self:Print(T("Profil nicht gefunden.", "Profile not found.")) end
	else
		self:Print(T("Unbekannt. Siehe /lz hilfe", "Unknown. See /lz help"))
	end
end, { "profil speichern|laden|auto|löschen <Name>", "Addon-Profile verwalten", "manage addon profiles" })
