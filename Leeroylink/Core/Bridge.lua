-- Bridge.lua: the two file channels between the game and Leeroyzan.
--
--   Leeroyzan -> game : Data.lua (global LeeroylinkImport), written by Leeroyzan
--                       into this addon's folder. Read once at login.
--   game -> Leeroyzan : the SavedVariables file (LeeroylinkDB). WoW writes it
--                       on /reload and on logout; Leeroyzan reads it afterwards.
--
-- The addon never writes Data.lua and Leeroyzan must not write the
-- SavedVariables file while the game is running (WoW would overwrite it).
local ADDON, LZ = ...
local Bridge = {}
LZ.Bridge = Bridge

local MAX_LIST = 400

local function str(v, max)
	if type(v) ~= "string" then return nil end
	return v:sub(1, max or 200)
end

local function strList(t, max)
	local out = {}
	if type(t) ~= "table" then return out end
	for i = 1, math.min(#t, max or MAX_LIST) do
		local s = str(t[i], 120)
		if s then out[#out + 1] = s end
	end
	return out
end

-- Validates and copies the imported table so later code can rely on its shape.
local function sanitize(raw)
	local imp = { schema = raw.schema, generated = tonumber(raw.generated) or 0 }
	imp.managerVersion = str(raw.managerVersion, 40)

	imp.updates = {}
	if type(raw.updates) == "table" then
		for i = 1, math.min(#raw.updates, MAX_LIST) do
			local u = raw.updates[i]
			if type(u) == "table" and str(u.name) then
				imp.updates[#imp.updates + 1] = {
					name = str(u.name), installed = str(u.installed, 40), available = str(u.available, 40),
					note = str(u.note, 200),
				}
			end
		end
	end

	imp.managed = strList(raw.managed, 2000)
	imp.installed = strList(raw.installed, MAX_LIST) -- wishlist items Leeroyzan installed meanwhile

	imp.profiles = {}
	if type(raw.profiles) == "table" then
		for i = 1, math.min(#raw.profiles, 50) do
			local p = raw.profiles[i]
			if type(p) == "table" and str(p.name, 60) then
				imp.profiles[#imp.profiles + 1] = { name = str(p.name, 60), enable = strList(p.enable, 500), note = str(p.note, 200) }
			end
		end
	end

	imp.whatsnew = {}
	if type(raw.whatsnew) == "table" then
		for i = 1, math.min(#raw.whatsnew, 50) do
			local n = raw.whatsnew[i]
			if type(n) == "table" and str(n.id, 80) then
				imp.whatsnew[#imp.whatsnew + 1] = {
					id = str(n.id, 80), addon = str(n.addon, 80), version = str(n.version, 40),
					lines = strList(n.lines, 12),
				}
			end
		end
	end

	imp.knownIssues = {}
	if type(raw.knownIssues) == "table" then
		for i = 1, math.min(#raw.knownIssues, 100) do
			local k = raw.knownIssues[i]
			if type(k) == "table" and str(k.addon) then
				imp.knownIssues[#imp.knownIssues + 1] = { addon = str(k.addon), text = str(k.text, 240) }
			end
		end
	end
	return imp
end

function Bridge:Load()
	LZ.import, LZ.importProblem = nil, nil
	local raw = _G.LeeroylinkImport
	if raw == nil then return end
	if type(raw) ~= "table" then LZ.importProblem = "format"; return end
	if raw.schema ~= LZ.schema then
		LZ.importProblem = "schema"
		return
	end
	LZ.import = sanitize(raw)
end

-- Seconds since Leeroyzan last wrote Data.lua, or nil if there is no data.
function Bridge:Age()
	local imp = LZ.import
	if not imp or imp.generated <= 0 then return nil end
	return math.max(0, time() - imp.generated)
end

-- Called on logout; the rest of the database is already in LZ.db.
function Bridge:Export()
	local db = LZ.db
	if not db then return end
	db.export = {
		schema = LZ.schema,
		writtenAt = time(),
		addonVersion = LZ.version,
		interface = LZ.C.InterfaceVersion(),
		char = LZ.charKey,
		-- Leeroyzan can tell from this that the game has actually read its last Data.lua.
		importSeen = LZ.import and LZ.import.generated or nil,
	}
end
