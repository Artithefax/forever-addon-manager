-- ErrorLog: catches Lua errors, attributes them to the responsible addon (by file
-- path), counts them per addon *and per addon version* so Leeroyzan can tell
-- "errors since the last update", and counts blocked/forbidden actions.
local ADDON, LZ = ...
local T, P, C = LZ.T, LZ.P, LZ.C

local mod = LZ:NewModule("ErrorLog", {
	group = "link",
	title = { "Fehlerprotokoll", "Error log" },
	desc = {
		"Fängt Lua-Fehler ab, ordnet sie dem verursachenden Addon zu und meldet sie an Leeroyzan. Zählt blockierte Aktionen.",
		"Catches Lua errors, attributes them to the responsible addon and reports them to Leeroyzan. Counts blocked actions.",
	},
	defaults = { notify = true },
	options = { { key = "notify", label = { "Ersten Fehler jedes Addons im Chat melden", "Announce the first error of each addon in chat" } } },
})

local MAX_ENTRIES = 150
local sessionByAddon, sessionTotal = {}, 0
local prevHandler, inHandler, calling = nil, false, false

local function attribute(text, skipSelf)
	if type(text) ~= "string" then return nil end
	for name in text:gmatch("AddOns[\\/]+([^\\/:%s]+)[\\/]") do
		if not (skipSelf and name == ADDON) then return name end
	end
end

function mod:OnInit()
	local g = LZ.gdb
	g.errors = g.errors or {}
	g.errorStats = g.errorStats or {}
	g.blocked = g.blocked or {}
end

function mod:Prune()
	local g, n, oldest = LZ.gdb, 0, nil
	for _ in pairs(g.errors) do n = n + 1 end
	if n <= MAX_ENTRIES then return end
	local list = {}
	for sig, e in pairs(g.errors) do list[#list + 1] = { sig = sig, last = e.last or 0 } end
	table.sort(list, function(a, b) return a.last < b.last end)
	for i = 1, n - MAX_ENTRIES + 10 do
		if list[i] then g.errors[list[i].sig] = nil end
	end
end

function mod:Record(msg, stack)
	msg = tostring(msg or "?")
	local addon = attribute(msg) or attribute(stack, true)
	if not addon then
		if msg:find("FrameXML", 1, true) or (stack and stack:find("FrameXML", 1, true)) then addon = "Blizzard" else addon = "?" end
	end
	if addon:find("^Blizzard_") then addon = "Blizzard" end

	local first = msg:match("^[^\n]*") or msg
	if #first > 240 then first = first:sub(1, 240) end
	local sig = addon .. "|" .. first
	local now = time()
	local g = LZ.gdb
	local version = (addon ~= "?" and addon ~= "Blizzard") and C.GetMetadata(addon, "Version") or nil

	local e = g.errors[sig]
	if not e then
		e = { addon = addon, msg = first, count = 0, first = now }
		if type(stack) == "string" then e.stack = stack:sub(1, 600) end
		g.errors[sig] = e
		self:Prune()
	end
	e.count = e.count + 1
	e.last = now
	e.version = version

	local st = g.errorStats[addon]
	if not st then st = { total = 0, byVersion = {} }; g.errorStats[addon] = st end
	st.total = st.total + 1
	st.last = now
	st.version = version
	local vkey = version or "?"
	st.byVersion[vkey] = (st.byVersion[vkey] or 0) + 1

	sessionTotal = sessionTotal + 1
	local seen = sessionByAddon[addon]
	sessionByAddon[addon] = (seen or 0) + 1
	if not seen and self.opt.notify then
		LZ:Print(string.format(T("Fehler in %s – Details mit /lz fehler", "Error in %s – details with /lz errors"), LZ.Color("ff6060", addon)))
	end
end

local function handler(msg)
	if calling then return msg end -- another handler chains back to us: stop the loop
	if not inHandler then
		inHandler = true
		local stack = (type(debugstack) == "function") and debugstack(2) or nil
		pcall(mod.Record, mod, msg, stack)
		inHandler = false
	end
	if prevHandler then
		calling = true
		local ok, r = pcall(prevHandler, msg)
		calling = false
		if ok then return r end
	end
	return msg
end

function mod:Install()
	if not (seterrorhandler and geterrorhandler) then return end
	local cur = geterrorhandler()
	if cur == handler then return end
	prevHandler = cur
	seterrorhandler(handler)
end

function mod:OnEnable() self:Install() end

-- Another addon may have replaced the handler after we installed ours.
mod:Event("PLAYER_ENTERING_WORLD", function(self) self:Install() end)

function mod:Blocked(addon, func, forbidden)
	addon = tostring(addon or "?")
	func = tostring(func or "?")
	local g = LZ.gdb
	local key = addon .. "|" .. func
	local b = g.blocked[key]
	if not b then b = { addon = addon, func = func, count = 0 }; g.blocked[key] = b end
	b.count = b.count + 1
	b.last = time()
	b.forbidden = forbidden and true or nil
end

mod:Event("ADDON_ACTION_BLOCKED", function(self, _, addon, func) self:Blocked(addon, func, false) end)
mod:Event("ADDON_ACTION_FORBIDDEN", function(self, _, addon, func) self:Blocked(addon, func, true) end)

function mod:Reset()
	local g = LZ.gdb
	g.errors, g.errorStats, g.blocked = {}, {}, {}
	sessionByAddon, sessionTotal = {}, 0
end

function mod:SessionCount() return sessionTotal end

function mod:Summary()
	local total = 0
	for _, st in pairs(LZ.gdb.errorStats) do total = total + (st.total or 0) end
	return string.format(T("%d Fehler gesamt, %d seit Login", "%d errors total, %d since login"), total, sessionTotal)
end

function mod:TooltipLines()
	if sessionTotal > 0 then
		return { LZ.Color("ff6060", string.format(T("%d Lua-Fehler seit Login", "%d Lua errors since login"), sessionTotal)) }
	end
end

function mod:Report()
	local g = LZ.gdb
	local lines = {}
	local stats = {}
	for addon, st in pairs(g.errorStats) do stats[#stats + 1] = { addon = addon, st = st } end
	table.sort(stats, function(a, b) return (a.st.last or 0) > (b.st.last or 0) end)

	lines[#lines + 1] = LZ.Color("ffd100", T("Fehler pro Addon", "Errors per addon"))
	if #stats == 0 then lines[#lines + 1] = T("Keine Fehler aufgezeichnet.", "No errors recorded.") end
	for _, s in ipairs(stats) do
		local ver = s.st.version and (" v" .. s.st.version) or ""
		local cur = s.st.byVersion and s.st.byVersion[s.st.version or "?"] or s.st.total
		lines[#lines + 1] = string.format("%s%s: %d (%s), %s %d", LZ.Color("ff8080", s.addon), ver, s.st.total,
			LZ.Ago(s.st.last), T("aktuelle Version", "current version"), cur or 0)
	end

	local entries = {}
	for _, e in pairs(g.errors) do entries[#entries + 1] = e end
	table.sort(entries, function(a, b) return (a.last or 0) > (b.last or 0) end)
	lines[#lines + 1] = ""
	lines[#lines + 1] = LZ.Color("ffd100", T("Letzte Meldungen", "Latest messages"))
	for i = 1, math.min(#entries, 12) do
		local e = entries[i]
		lines[#lines + 1] = string.format("%dx [%s] %s", e.count, e.addon, e.msg)
	end

	local blocked = {}
	for _, b in pairs(g.blocked) do blocked[#blocked + 1] = b end
	table.sort(blocked, function(a, b) return a.count > b.count end)
	lines[#lines + 1] = ""
	lines[#lines + 1] = LZ.Color("ffd100", T("Blockierte Aktionen", "Blocked actions"))
	if #blocked == 0 then lines[#lines + 1] = T("Keine.", "None.") end
	for i = 1, math.min(#blocked, 10) do
		local b = blocked[i]
		lines[#lines + 1] = string.format("%dx %s -> %s%s", b.count, b.addon, b.func, b.forbidden and " (forbidden)" or "")
	end
	return table.concat(lines, "\n")
end

mod.actions = {
	{ label = { "Protokoll leeren", "Clear log" }, fn = function(self) self:Reset(); LZ.UI:Refresh() end },
}

mod:Command({ "fehler", "errors" }, function(self, rest)
	rest = rest:lower()
	if rest == "reset" or rest == "leeren" or rest == "clear" then
		self:Reset()
		self:Print(T("Fehlerprotokoll geleert.", "Error log cleared."))
		return
	end
	LZ.UI:Show(self.name)
end, { "fehler [reset]", "Fehlerprotokoll öffnen / leeren", "open / clear the error log" })
