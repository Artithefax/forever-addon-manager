-- Notes: short notes on players, NPCs and zones, shown in tooltips / on entering the zone.
local ADDON, LZ = ...
local T, P, C = LZ.T, LZ.P, LZ.C

local mod = LZ:NewModule("Notes", {
	title = { "Notizen", "Notes" },
	desc = {
		"Kurze Notizen zu Spielern, NPCs und Orten – im Tooltip bzw. beim Betreten der Zone sichtbar.",
		"Short notes on players, NPCs and zones – shown in tooltips or when entering the zone.",
	},
	defaults = { zoneMessage = true },
	options = { { key = "zoneMessage", label = { "Zonen-Notiz beim Betreten im Chat zeigen", "Show a zone note in chat when entering" } } },
})

local hooked = false

function mod:OnInit()
	local g = LZ.gdb
	g.notes = g.notes or {}
	g.notes.units = g.notes.units or {}
	g.notes.zones = g.notes.zones or {}
end

-- Key for a unit: players by name, creatures by NPC id (from the GUID).
local function unitKey(unit)
	if not unit or not UnitExists or not UnitExists(unit) then return nil end
	local guid = UnitGUID and UnitGUID(unit)
	local name = UnitName and UnitName(unit)
	if type(guid) == "string" then
		local kind, _, _, _, _, npcID = strsplit("-", guid)
		if (kind == "Creature" or kind == "Vehicle") and npcID then return "npc:" .. npcID, name end
	end
	if UnitIsPlayer and UnitIsPlayer(unit) and name then return "player:" .. name, name end
	return nil, name
end

local function onUnitTooltip(tooltip)
	if not mod.enabled then return end
	local _, unit = tooltip:GetUnit()
	local key = unitKey(unit or "mouseover")
	local note = key and LZ.gdb.notes.units[key]
	if note then
		tooltip:AddLine(LZ.Color("ffd100", T("Notiz: ", "Note: ")) .. note.text, 1, 1, 1, true)
		if tooltip.Show then tooltip:Show() end
	end
end

function mod:OnEnable()
	if not hooked then hooked = LZ.HookTooltip("unit", onUnitTooltip) end
end

local function zoneName()
	return (GetRealZoneText and GetRealZoneText()) or (GetZoneText and GetZoneText()) or nil
end

mod:Event("ZONE_CHANGED_NEW_AREA", function(self)
	if not self.opt.zoneMessage then return end
	local zone = zoneName()
	local note = zone and LZ.gdb.notes.zones[zone]
	if note then self:Print(string.format("%s: %s", zone, note.text)) end
end)

function mod:Summary()
	local u, z = 0, 0
	for _ in pairs(LZ.gdb.notes.units) do u = u + 1 end
	for _ in pairs(LZ.gdb.notes.zones) do z = z + 1 end
	return string.format(T("%d Einheiten, %d Orte", "%d units, %d zones"), u, z)
end

function mod:Report()
	local lines = { LZ.Color("ffd100", T("Notizen zu Spielern und NPCs", "Notes on players and NPCs")) }
	local any = false
	for _, n in pairs(LZ.gdb.notes.units) do
		any = true
		lines[#lines + 1] = string.format("%s: %s", n.name or "?", n.text)
	end
	if not any then lines[#lines + 1] = T("Keine. Ziel anvisieren und /lz note <Text>", "None. Target something and /lz note <text>") end
	lines[#lines + 1] = ""
	lines[#lines + 1] = LZ.Color("ffd100", T("Notizen zu Orten", "Notes on zones"))
	any = false
	for zone, n in pairs(LZ.gdb.notes.zones) do
		any = true
		lines[#lines + 1] = string.format("%s: %s", zone, n.text)
	end
	if not any then lines[#lines + 1] = T("Keine. /lz note ort <Text>", "None. /lz note zone <text>") end
	return table.concat(lines, "\n")
end

mod:Command({ "note", "notiz" }, function(self, rest)
	if rest == "" then LZ.UI:Show(self.name) return end
	local sub, arg = rest:match("^(%S+)%s*(.-)$")
	local s = sub:lower()
	if s == "ort" or s == "zone" then
		local zone = zoneName()
		if not zone or arg == "" then self:Print(T("Benutzung: /lz note ort <Text>", "Usage: /lz note zone <text>")) return end
		LZ.gdb.notes.zones[zone] = { text = arg:sub(1, 200), t = time() }
		self:Print(string.format(T("Notiz für %s gespeichert.", "Note saved for %s."), zone))
		return
	end
	if s == "lösch" or s == "loesch" or s == "del" or s == "löschen" then
		local key = unitKey("target")
		if key and LZ.gdb.notes.units[key] then
			LZ.gdb.notes.units[key] = nil
			self:Print(T("Notiz gelöscht.", "Note deleted."))
		else
			self:Print(T("Keine Notiz zum Ziel.", "No note for the target."))
		end
		return
	end
	local key, name = unitKey("target")
	if not key then self:Print(T("Kein Ziel – erst Spieler oder NPC anvisieren.", "No target – target a player or NPC first.")) return end
	LZ.gdb.notes.units[key] = { text = rest:sub(1, 200), name = name, t = time() }
	self:Print(string.format(T("Notiz zu %s gespeichert.", "Note on %s saved."), name or key))
end, { "note <Text> | ort <Text> | lösch", "Notiz zum Ziel / Ort", "note for the target / zone" })
