-- Core.lua: namespace, module system, event dispatch, saved variables, slash commands.
local ADDON, LZ = ...
local C = LZ.C

_G.LeeroyzanLink = LZ
LZ.addon = ADDON
LZ.version = C.GetMetadata(ADDON, "Version") or "0.2.0"
LZ.schema = 1
LZ.lang = (type(GetLocale) == "function" and GetLocale() == "deDE") and "de" or "en"
LZ.modules, LZ.moduleList, LZ.commands, LZ.helpLines = {}, {}, {}, {}
LZ.coreCommands = {}
LZ.loggedIn = false

----------------------------------------------------------------------
-- Language helpers: T(de, en) resolves at call time, P({de, en}) for tables.
----------------------------------------------------------------------
function LZ.T(de, en)
	if LZ.lang == "de" then return de end
	return en or de
end

function LZ.P(pair)
	if type(pair) == "table" then
		if LZ.lang == "de" then return pair[1] or pair[2] end
		return pair[2] or pair[1]
	end
	return pair
end

local T, P = LZ.T, LZ.P

----------------------------------------------------------------------
-- Small utilities
----------------------------------------------------------------------
function LZ.Color(hex, text) return "|cff" .. hex .. tostring(text) .. "|r" end

function LZ.Strip(s)
	if type(s) ~= "string" then return s end
	s = s:gsub("|c%x%x%x%x%x%x%x%x", "")
	s = s:gsub("|r", "")
	return s
end

function LZ.Trim(s) return ((tostring(s or ""):gsub("^%s+", ""):gsub("%s+$", ""))) end

function LZ.FormatMoney(copper)
	copper = math.floor(tonumber(copper) or 0)
	local neg = copper < 0
	if neg then copper = -copper end
	local g = math.floor(copper / 10000)
	local s = math.floor((copper % 10000) / 100)
	local k = copper % 100
	local out
	if g > 0 then
		out = string.format("%dg %02ds %02dc", g, s, k)
	elseif s > 0 then
		out = string.format("%ds %02dc", s, k)
	else
		out = string.format("%dc", k)
	end
	return neg and ("-" .. out) or out
end

function LZ.Duration(sec)
	sec = math.floor(tonumber(sec) or 0)
	if sec < 0 then sec = 0 end
	local h = math.floor(sec / 3600)
	local m = math.floor((sec % 3600) / 60)
	if h > 0 then return string.format("%dh %02dm", h, m) end
	return string.format("%dm", m)
end

function LZ.Ago(ts)
	if not ts or ts <= 0 then return T("nie", "never") end
	local d = time() - ts
	if d < 0 then d = 0 end
	if d < 90 then return T("gerade eben", "just now") end
	if d < 3600 then return string.format(T("vor %d Min", "%d min ago"), math.floor(d / 60)) end
	if d < 86400 then return string.format(T("vor %d Std", "%d h ago"), math.floor(d / 3600)) end
	return string.format(T("vor %d T", "%d d ago"), math.floor(d / 86400))
end

function LZ.After(sec, fn)
	if _G.C_Timer and C_Timer.After then return C_Timer.After(sec, fn) end
	local f = CreateFrame("Frame")
	local elapsed = 0
	f:SetScript("OnUpdate", function(self, dt)
		elapsed = elapsed + (dt or 0)
		if elapsed >= sec then
			self:SetScript("OnUpdate", nil)
			fn()
		end
	end)
end

-- Turns a Blizzard format string (e.g. LOOT_ITEM_SELF) into a Lua pattern.
function LZ.FormatToPattern(fmt)
	if type(fmt) ~= "string" then return nil end
	local p = fmt:gsub("([%(%)%.%%%+%-%*%?%[%]%^%$])", "%%%1")
	p = p:gsub("%%%%s", "(.+)")
	p = p:gsub("%%%%d", "(%%d+)")
	return "^" .. p .. "$"
end

function LZ.ItemID(x)
	if type(x) == "number" then return x end
	if type(x) ~= "string" then return nil end
	return tonumber(x:match("item:(%d+)")) or tonumber(x)
end

-- Quality from the color code of an item link.
local QUALITY_BY_COLOR = { ["9d9d9d"] = 0, ["ffffff"] = 1, ["1eff00"] = 2, ["0070dd"] = 3, ["a335ee"] = 4, ["ff8000"] = 5, ["e6cc80"] = 6 }
function LZ.QualityFromLink(link)
	if type(link) ~= "string" then return nil end
	local q = tonumber(link:match("|cnIQ(%d+):"))   -- newer clients: |cnIQ2:...
	if q then return q end
	local hex = link:match("|cff(%x%x%x%x%x%x)")
	if hex and QUALITY_BY_COLOR[hex:lower()] then return QUALITY_BY_COLOR[hex:lower()] end
	return LZ.C.GetQuality(link)
end

function LZ:Print(...)
	local parts = {}
	for i = 1, select("#", ...) do parts[#parts + 1] = tostring((select(i, ...))) end
	local msg = "|cffff8000Leeroyzan|r: " .. table.concat(parts, " ")
	local cf = _G.DEFAULT_CHAT_FRAME
	if cf and cf.AddMessage then cf:AddMessage(msg) elseif print then print(msg) end
end

-- Errors inside our own code go to the real error handler (and therefore also
-- into our own error log).
local function errHandler(e)
	local h = geterrorhandler and geterrorhandler()
	if h then return h(e) end
	return e
end

function LZ:Safe(fn, ...)
	local args = { n = select("#", ...), ... }
	return xpcall(function() return fn(unpack(args, 1, args.n)) end, errHandler)
end

-- Tiny publish/subscribe between modules.
local listeners = {}
function LZ.Listen(name, fn)
	listeners[name] = listeners[name] or {}
	table.insert(listeners[name], fn)
end
function LZ.Emit(name, ...)
	for _, fn in ipairs(listeners[name] or {}) do LZ:Safe(fn, ...) end
end

-- Hook item or unit tooltips across client generations.
function LZ.HookTooltip(kind, fn)
	if _G.TooltipDataProcessor and TooltipDataProcessor.AddTooltipPostCall and _G.Enum and Enum.TooltipDataType then
		local t = (kind == "unit") and Enum.TooltipDataType.Unit or Enum.TooltipDataType.Item
		if t then
			TooltipDataProcessor.AddTooltipPostCall(t, function(tooltip, data)
				if tooltip == _G.GameTooltip then pcall(fn, tooltip, data) end
			end)
			return true
		end
	end
	if _G.GameTooltip and GameTooltip.HookScript then
		local script = (kind == "unit") and "OnTooltipSetUnit" or "OnTooltipSetItem"
		pcall(GameTooltip.HookScript, GameTooltip, script, function(tooltip) pcall(fn, tooltip) end)
		return true
	end
	return false
end

----------------------------------------------------------------------
-- Saved variables
----------------------------------------------------------------------
function LZ:InitDB()
	if type(_G.LeeroyzanLinkDB) ~= "table" then _G.LeeroyzanLinkDB = {} end
	local db = _G.LeeroyzanLinkDB
	db.schema = LZ.schema
	db.settings = db.settings or {}
	db.settings.modules = db.settings.modules or {}
	db.settings.opts = db.settings.opts or {}
	db.settings.minimap = db.settings.minimap or {}
	db.global = db.global or {}
	db.chars = db.chars or {}
	if db.settings.lang == "de" or db.settings.lang == "en" then LZ.lang = db.settings.lang end
	LZ.db, LZ.gdb, LZ.settings = db, db.global, db.settings
end

function LZ:CharKey()
	local name = (UnitName and UnitName("player")) or "?"
	local realm = (GetNormalizedRealmName and GetNormalizedRealmName()) or (GetRealmName and GetRealmName()) or ""
	if realm == "" then realm = "Forever" end
	return name .. "-" .. realm
end

----------------------------------------------------------------------
-- Modules
----------------------------------------------------------------------
local Proto = {}
Proto.__index = Proto

-- mod:Event("EVENT", function(self, event, ...) end)  or  mod:Event("EVENT", "MethodName")
function Proto:Event(event, fn) self.events[event] = fn or event end

-- mod:Command({"de", "en"}, function(self, rest) end, { "usage", "Hilfe de", "help en" })
function Proto:Command(names, fn, help)
	if type(names) == "string" then names = { names } end
	for _, n in ipairs(names) do LZ.commands[n:lower()] = { mod = self, fn = fn } end
	if help then LZ.helpLines[#LZ.helpLines + 1] = { usage = help[1], de = help[2], en = help[3] } end
end

function Proto:Print(...) LZ:Print("[" .. P(self.title) .. "]", ...) end

function LZ:NewModule(name, spec)
	spec = spec or {}
	local mod = setmetatable({
		name = name,
		title = spec.title or name,
		desc = spec.desc,
		defaults = spec.defaults or {},
		options = spec.options or {},
		default = spec.default,
		group = spec.group or "extra",
		events = {},
		opt = {},
		enabled = false,
	}, Proto)
	LZ.modules[name] = mod
	LZ.moduleList[#LZ.moduleList + 1] = mod
	return mod
end

local eventFrame = CreateFrame("Frame")
local eventMap = {}

eventFrame:SetScript("OnEvent", function(_, event, ...)
	local handlers = eventMap[event]
	if not handlers then return end
	for mod, spec in pairs(handlers) do
		local fn = spec
		if type(spec) == "string" then fn = mod[spec] end
		if type(fn) == "function" then LZ:Safe(fn, mod, event, ...) end
	end
end)

function LZ:IsModuleEnabled(mod)
	local v = LZ.settings and LZ.settings.modules[mod.name]
	if v == nil then return mod.default ~= false end
	return v and true or false
end

function LZ:EnableModule(mod)
	if mod.enabled then return end
	mod.enabled = true
	for event, spec in pairs(mod.events) do
		local h = eventMap[event]
		if not h then h = {}; eventMap[event] = h end
		h[mod] = spec
		pcall(eventFrame.RegisterEvent, eventFrame, event)
	end
	if mod.OnEnable then LZ:Safe(mod.OnEnable, mod) end
end

function LZ:DisableModule(mod)
	if not mod.enabled then return end
	mod.enabled = false
	for event in pairs(mod.events) do
		local h = eventMap[event]
		if h then
			h[mod] = nil
			if next(h) == nil then
				eventMap[event] = nil
				pcall(eventFrame.UnregisterEvent, eventFrame, event)
			end
		end
	end
	if mod.OnDisable then LZ:Safe(mod.OnDisable, mod) end
end

function LZ:SetModuleEnabled(mod, enabled)
	LZ.settings.modules[mod.name] = enabled and true or false
	if enabled then LZ:EnableModule(mod) else LZ:DisableModule(mod) end
	if LZ.UI and LZ.UI.frame then LZ.UI:Refresh() end
end

function LZ:FindModule(text)
	text = (text or ""):lower()
	for _, mod in ipairs(LZ.moduleList) do
		if mod.name:lower() == text or P(mod.title):lower() == text then return mod end
	end
end

----------------------------------------------------------------------
-- Lifecycle
----------------------------------------------------------------------
function LZ:OnLogin()
	if not LZ.db then LZ:InitDB() end
	LZ.charKey = LZ:CharKey()
	LZ.db.chars[LZ.charKey] = LZ.db.chars[LZ.charKey] or {}
	LZ.char = LZ.db.chars[LZ.charKey]
	LZ.char.lastSeen = time()
	if LZ.Bridge then LZ.Bridge:Load() end

	for _, mod in ipairs(LZ.moduleList) do
		local o = LZ.settings.opts[mod.name]
		if type(o) ~= "table" then o = {}; LZ.settings.opts[mod.name] = o end
		mod.opt = setmetatable(o, { __index = mod.defaults })
		if mod.OnInit then LZ:Safe(mod.OnInit, mod) end
	end
	LZ.loggedIn = true
	for _, mod in ipairs(LZ.moduleList) do
		if LZ:IsModuleEnabled(mod) then LZ:EnableModule(mod) end
	end
	if LZ.UI then LZ.UI:Init() end
end

function LZ:OnLogout()
	if not LZ.loggedIn then return end
	for _, mod in ipairs(LZ.moduleList) do
		if mod.enabled and mod.OnLogout then LZ:Safe(mod.OnLogout, mod) end
	end
	if LZ.char then LZ.char.lastSeen = time() end
	if LZ.Bridge then LZ.Bridge:Export() end
end

local core = CreateFrame("Frame")
core:RegisterEvent("ADDON_LOADED")
core:RegisterEvent("PLAYER_LOGIN")
core:RegisterEvent("PLAYER_LOGOUT")
core:SetScript("OnEvent", function(_, event, arg1)
	if event == "ADDON_LOADED" then
		if arg1 == ADDON then LZ:InitDB() end
	elseif event == "PLAYER_LOGIN" then
		LZ:Safe(LZ.OnLogin, LZ)
	elseif event == "PLAYER_LOGOUT" then
		LZ:Safe(LZ.OnLogout, LZ)
	end
end)

----------------------------------------------------------------------
-- Slash commands
----------------------------------------------------------------------
local function parseState(s)
	s = (s or ""):lower()
	if s == "an" or s == "on" or s == "ein" or s == "1" or s == "yes" or s == "ja" then return true end
	if s == "aus" or s == "off" or s == "0" or s == "no" or s == "nein" then return false end
end
LZ.ParseState = parseState

LZ.coreCommands["hilfe"] = function() LZ:ShowHelp() end
LZ.coreCommands["help"] = LZ.coreCommands["hilfe"]
LZ.coreCommands["?"] = LZ.coreCommands["hilfe"]

LZ.coreCommands["module"] = function()
	LZ:Print(T("Module (an/aus mit /lz modul <Name> an|aus):", "Modules (toggle with /lz module <name> on|off):"))
	for _, mod in ipairs(LZ.moduleList) do
		local state = mod.enabled and LZ.Color("40ff40", T("an", "on")) or LZ.Color("ff6060", T("aus", "off"))
		LZ:Print(string.format("  %s (%s): %s", P(mod.title), mod.name, state))
	end
end
LZ.coreCommands["modules"] = LZ.coreCommands["module"]

LZ.coreCommands["modul"] = function(rest)
	local name, state = rest:match("^(.-)%s+(%S+)$")
	local mod = name and LZ:FindModule(name)
	local want = parseState(state)
	if not mod or want == nil then
		LZ:Print(T("Benutzung: /lz modul <Name> an|aus  (Liste: /lz module)", "Usage: /lz module <name> on|off  (list: /lz modules)"))
		return
	end
	LZ:SetModuleEnabled(mod, want)
	LZ:Print(P(mod.title) .. ": " .. (want and T("an", "on") or T("aus", "off")))
end

LZ.coreCommands["sprache"] = function(rest)
	rest = rest:lower()
	if rest == "de" or rest == "en" then
		LZ.lang = rest
		LZ.settings.lang = rest
		if LZ.UI and LZ.UI.frame then LZ.UI:Rebuild() end
		LZ:Print(T("Sprache: Deutsch", "Language: English"))
	else
		LZ:Print("/lz sprache de|en")
	end
end
LZ.coreCommands["lang"] = LZ.coreCommands["sprache"]
LZ.coreCommands["language"] = LZ.coreCommands["sprache"]

LZ.coreCommands["reload"] = function() ReloadUI() end
LZ.coreCommands["rl"] = LZ.coreCommands["reload"]

LZ.coreCommands["minimap"] = function(rest)
	local want = parseState(rest)
	if want == nil then want = LZ.settings.minimap.hide and true or false end
	LZ.settings.minimap.hide = not want
	if LZ.UI then LZ.UI:UpdateMinimap() end
	LZ:Print(T("Minimap-Symbol: ", "Minimap button: ") .. (want and T("an", "on") or T("aus", "off")))
end

function LZ:ShowHelp()
	LZ:Print("Leeroyzan Link v" .. LZ.version)
	LZ:Print("  /lz  -  " .. T("Fenster öffnen/schließen", "open/close window"))
	LZ:Print("  /lz module  |  /lz modul <Name> an|aus  -  " .. T("Module verwalten", "manage modules"))
	LZ:Print("  /lz sprache de|en  |  /lz minimap an|aus  |  /lz reload")
	for _, h in ipairs(LZ.helpLines) do
		LZ:Print("  /lz " .. h.usage .. "  -  " .. T(h.de, h.en))
	end
end

function LZ:HandleSlash(msg)
	local cmd, rest = (msg or ""):match("^%s*(%S*)%s*(.-)%s*$")
	cmd = (cmd or ""):lower()
	rest = rest or ""
	if cmd == "" then
		if LZ.UI then LZ.UI:Toggle() end
		return
	end
	local c = LZ.coreCommands[cmd]
	if c then return c(rest) end
	local entry = LZ.commands[cmd]
	if entry then
		if not entry.mod.enabled then
			LZ:Print(T("Modul ist ausgeschaltet: ", "Module is off: ") .. P(entry.mod.title) .. "  (/lz module)")
			return
		end
		return LZ:Safe(entry.fn, entry.mod, rest)
	end
	LZ:Print(T("Unbekannter Befehl. Hilfe: /lz hilfe", "Unknown command. Help: /lz help"))
end

_G.SLASH_LEEROYZAN1 = "/lz"
_G.SLASH_LEEROYZAN2 = "/leeroyzan"
SlashCmdList["LEEROYZAN"] = function(msg) LZ:HandleSlash(msg) end
