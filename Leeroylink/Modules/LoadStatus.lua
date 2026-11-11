-- LoadStatus: what the game really did with each addon - loaded or not, and why not.
-- Leeroyzan uses this to spot missing dependencies, outdated addons and manual installs.
local ADDON, LZ = ...
local T, P, C = LZ.T, LZ.P, LZ.C

local mod = LZ:NewModule("LoadStatus", {
	group = "link",
	title = { "Ladestatus", "Load status" },
	desc = {
		"Meldet an Leeroyzan, welche Addons wirklich geladen sind (Version, Grund bei Nicht-Laden, fehlende Abhängigkeiten).",
		"Tells Leeroyzan which addons are really loaded (version, reason if not, missing dependencies).",
	},
})

local REASONS = {
	DISABLED = { "deaktiviert", "disabled" },
	MISSING = { "Dateien fehlen", "files missing" },
	CORRUPT = { "beschädigt", "corrupt" },
	INCOMPATIBLE = { "inkompatibel", "incompatible" },
	INTERFACE_VERSION = { "veraltet (Interface-Version)", "out of date (interface version)" },
	DEP_MISSING = { "Abhängigkeit fehlt", "dependency missing" },
	DEP_DISABLED = { "Abhängigkeit deaktiviert", "dependency disabled" },
	DEP_CORRUPT = { "Abhängigkeit beschädigt", "dependency corrupt" },
	DEP_INTERFACE_VERSION = { "Abhängigkeit veraltet", "dependency out of date" },
	DEP_INCOMPATIBLE = { "Abhängigkeit inkompatibel", "dependency incompatible" },
	DEMAND_LOADED = { "bei Bedarf", "load on demand" },
	NOT_DEMAND_LOADED = { "bei Bedarf", "load on demand" },
}

local function reasonText(r)
	if not r then return "?" end
	local pair = REASONS[tostring(r):upper()]
	return pair and P(pair) or tostring(r)
end

function mod:OnInit()
	LZ.gdb.addons = LZ.gdb.addons or {}
end

function mod:Scan()
	local list, names = {}, {}
	local count = C.GetNumAddOns()
	for i = 1, count do
		local info = C.GetAddOnInfo(i)
		if info and info.name then names[info.name] = true end
	end
	for i = 1, count do
		local info = C.GetAddOnInfo(i)
		if info and info.name then
			local name = info.name
			local deps, missing = C.GetDeps(name), {}
			for _, d in ipairs(deps) do
				if not names[d] then missing[#missing + 1] = d end
			end
			list[name] = {
				title = LZ.Strip(info.title or name),
				version = C.GetMetadata(name, "Version"),
				loaded = C.IsLoaded(name),
				enabled = C.IsEnabled(name),
				loadable = info.loadable and true or false,
				reason = info.reason,
				onDemand = C.IsLoadOnDemand(name) or nil,
				deps = #deps > 0 and deps or nil,
				missingDeps = #missing > 0 and missing or nil,
			}
		end
	end
	LZ.gdb.addons = list
	LZ.gdb.addonsScannedAt = time()
	return list
end

-- Addons that are switched on but did not load, plus anything with missing dependencies.
function mod:Problems()
	local out = {}
	for name, a in pairs(LZ.gdb.addons) do
		if a.missingDeps then
			out[#out + 1] = { name = name, text = T("Fehlende Abhängigkeit: ", "Missing dependency: ") .. table.concat(a.missingDeps, ", ") }
		elseif a.enabled and not a.loaded and not a.onDemand then
			out[#out + 1] = { name = name, text = reasonText(a.reason) }
		end
	end
	table.sort(out, function(a, b) return a.name:lower() < b.name:lower() end)
	return out
end

function mod:OnEnable()
	self:Scan()
end

mod:Event("PLAYER_ENTERING_WORLD", function(self)
	-- load-on-demand addons and late loaders have settled a few seconds after login
	LZ.After(4, function() if self.enabled then self:Scan() end end)
end)

function mod:OnLogout() self:Scan() end

function mod:Summary()
	local n, loaded = 0, 0
	for _, a in pairs(LZ.gdb.addons) do
		n = n + 1
		if a.loaded then loaded = loaded + 1 end
	end
	return string.format(T("%d Addons, %d geladen, %d Probleme", "%d addons, %d loaded, %d problems"), n, loaded, #self:Problems())
end

function mod:TooltipLines()
	local p = #self:Problems()
	if p > 0 then return { LZ.Color("ffa040", string.format(T("%d Addon-Probleme", "%d addon problems"), p)) } end
end

function mod:Report()
	self:Scan()
	local lines = {}
	local problems = self:Problems()
	lines[#lines + 1] = LZ.Color("ffd100", T("Probleme", "Problems"))
	if #problems == 0 then lines[#lines + 1] = T("Keine – alle aktivierten Addons sind geladen.", "None – every enabled addon is loaded.") end
	for _, p in ipairs(problems) do lines[#lines + 1] = LZ.Color("ff8080", p.name) .. ": " .. p.text end

	lines[#lines + 1] = ""
	lines[#lines + 1] = LZ.Color("ffd100", T("Geladene Addons", "Loaded addons"))
	local names = {}
	for name, a in pairs(LZ.gdb.addons) do
		if a.loaded and not name:find("^Blizzard_") then names[#names + 1] = name end
	end
	table.sort(names, function(a, b) return a:lower() < b:lower() end)
	for _, name in ipairs(names) do
		local a = LZ.gdb.addons[name]
		lines[#lines + 1] = string.format("%s  %s", name, a.version and LZ.Color("a0a0a0", "v" .. a.version) or "")
	end
	return table.concat(lines, "\n")
end

mod:Command({ "status", "ladestatus" }, function(self) LZ.UI:Show(self.name) end,
	{ "status", "Ladestatus aller Addons anzeigen", "show the load status of all addons" })
