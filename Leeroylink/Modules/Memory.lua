-- Memory: ranking of the heaviest addons plus growth since login (hint for leaks).
-- Measured on demand and at logout only - the memory API causes a small hitch.
local ADDON, LZ = ...
local T, P, C = LZ.T, LZ.P, LZ.C

local mod = LZ:NewModule("Memory", {
	group = "link",
	title = { "Ressourcen", "Resources" },
	desc = {
		"Speicherverbrauch pro Addon (und CPU, falls der Client den Profiler anbietet). Zeigt Wachstum seit dem Login.",
		"Memory use per addon (and CPU if the client offers the profiler). Shows growth since login.",
	},
})

local baseline = nil
local round1 = function(x) return math.floor(x * 10 + 0.5) / 10 end

function mod:Collect()
	C.UpdateMemory()
	local list, total = {}, 0
	for i = 1, C.GetNumAddOns() do
		local info = C.GetAddOnInfo(i)
		if info and info.name and C.IsLoaded(info.name) then
			local kb = C.GetMemory(info.name)
			if kb then
				list[#list + 1] = { name = info.name, kb = kb, cpu = C.GetCPU(info.name) }
				total = total + kb
			end
		end
	end
	table.sort(list, function(a, b) return a.kb > b.kb end)
	return list, total
end

function mod:Snapshot(setBaseline)
	local list, total = self:Collect()
	if setBaseline then
		baseline = {}
		for _, e in ipairs(list) do baseline[e.name] = e.kb end
	end
	local saved = {}
	for i = 1, math.min(#list, 40) do
		local e = list[i]
		saved[i] = { name = e.name, kb = round1(e.kb), cpu = e.cpu and (math.floor(e.cpu * 100 + 0.5) / 100) or nil }
	end
	LZ.gdb.memory = { t = time(), totalKB = round1(total), list = saved }
	return list, total
end

function mod:OnEnable()
	LZ.After(10, function() if self.enabled and not baseline then self:Snapshot(true) end end)
end

function mod:OnLogout() self:Snapshot(false) end

function mod:Summary()
	local m = LZ.gdb.memory
	if not m then return T("noch keine Messung", "no measurement yet") end
	return string.format(T("gesamt %.1f MB (%s)", "total %.1f MB (%s)"), m.totalKB / 1024, LZ.Ago(m.t))
end

function mod:Report()
	local list, total = self:Snapshot(false)
	local lines = {}
	lines[#lines + 1] = LZ.Color("ffd100", string.format(T("Speicher gesamt: %.1f MB", "Total memory: %.1f MB"), total / 1024))
	lines[#lines + 1] = ""
	lines[#lines + 1] = LZ.Color("ffd100", T("Die schwersten Addons", "Heaviest addons"))
	for i = 1, math.min(#list, 15) do
		local e = list[i]
		local cpu = e.cpu and string.format("  CPU %.2f ms", e.cpu) or ""
		lines[#lines + 1] = string.format("%2d. %-28s %7.1f MB%s", i, e.name, e.kb / 1024, cpu)
	end
	if baseline then
		local growth = {}
		for _, e in ipairs(list) do
			local d = e.kb - (baseline[e.name] or e.kb)
			if d > 100 then growth[#growth + 1] = { name = e.name, d = d } end
		end
		table.sort(growth, function(a, b) return a.d > b.d end)
		lines[#lines + 1] = ""
		lines[#lines + 1] = LZ.Color("ffd100", T("Wachstum seit Login", "Growth since login"))
		if #growth == 0 then lines[#lines + 1] = T("Nichts Auffälliges.", "Nothing notable.") end
		for i = 1, math.min(#growth, 8) do
			lines[#lines + 1] = string.format("%s: +%.1f MB", growth[i].name, growth[i].d / 1024)
		end
	end
	return table.concat(lines, "\n")
end

mod:Command({ "speicher", "memory", "mem" }, function(self) LZ.UI:Show(self.name) end,
	{ "speicher", "Addon-Speicherverbrauch anzeigen", "show addon memory use" })
