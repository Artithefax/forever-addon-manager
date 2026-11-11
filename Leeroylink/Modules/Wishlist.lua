-- Wishlist: "/lz merken Details" while playing; Leeroyzan offers to install the
-- entries on its next start and reports back what it installed.
local ADDON, LZ = ...
local T, P, C = LZ.T, LZ.P, LZ.C

local mod = LZ:NewModule("Wishlist", {
	group = "link",
	title = { "Wunschliste", "Wishlist" },
	desc = {
		"Addons im Spiel vormerken; Leeroyzan bietet die Installation beim nächsten Start an.",
		"Bookmark addons in game; Leeroyzan offers to install them on its next start.",
	},
})

local function keyOf(text) return LZ.Trim(text):lower() end

function mod:OnInit()
	LZ.gdb.wishlist = LZ.gdb.wishlist or {}
end

function mod:Add(text, note)
	text = LZ.Trim(text)
	if text == "" then return false end
	LZ.gdb.wishlist[keyOf(text)] = { name = text:sub(1, 120), note = note and note:sub(1, 200) or nil, added = time() }
	return true
end

function mod:Remove(text)
	local k = keyOf(text)
	if LZ.gdb.wishlist[k] then LZ.gdb.wishlist[k] = nil return true end
	return false
end

-- Drop entries Leeroyzan has installed or that exist as an addon folder now.
function mod:Resolve()
	local removed = 0
	local done = {}
	if LZ.import then
		for _, n in ipairs(LZ.import.installed) do done[keyOf(n)] = true end
	end
	for i = 1, C.GetNumAddOns() do
		local info = C.GetAddOnInfo(i)
		if info and info.name then done[keyOf(info.name)] = true end
	end
	for k in pairs(LZ.gdb.wishlist) do
		if done[k] then LZ.gdb.wishlist[k] = nil; removed = removed + 1 end
	end
	return removed
end

function mod:Count()
	local n = 0
	for _ in pairs(LZ.gdb.wishlist) do n = n + 1 end
	return n
end

function mod:OnEnable()
	local removed = self:Resolve()
	if removed > 0 then
		self:Print(string.format(T("%d Wunschlisten-Einträge sind jetzt installiert.", "%d wishlist entries are installed now."), removed))
	end
end

function mod:Summary() return string.format(T("%d vorgemerkt", "%d bookmarked"), self:Count()) end

function mod:TooltipLines()
	local n = self:Count()
	if n > 0 then return { string.format(T("%d Addons auf der Wunschliste", "%d addons on the wishlist"), n) } end
end

function mod:Report()
	local lines = { LZ.Color("ffd100", T("Wunschliste", "Wishlist")) }
	local list = {}
	for _, w in pairs(LZ.gdb.wishlist) do list[#list + 1] = w end
	table.sort(list, function(a, b) return (a.added or 0) < (b.added or 0) end)
	if #list == 0 then lines[#lines + 1] = T("Leer. Vormerken: /lz merken <Name oder Link>", "Empty. Bookmark one: /lz wish <name or link>") end
	for _, w in ipairs(list) do
		lines[#lines + 1] = string.format("%s  %s%s", w.name, LZ.Color("a0a0a0", LZ.Ago(w.added)), w.note and ("  – " .. w.note) or "")
	end
	lines[#lines + 1] = ""
	lines[#lines + 1] = T("Befehle: /lz merken <Text> · /lz merken entfernen <Text>", "Commands: /lz wish <text> · /lz wish remove <text>")
	return table.concat(lines, "\n")
end

mod:Command({ "merken", "wish", "wunschliste" }, function(self, rest)
	if rest == "" then LZ.UI:Show(self.name) return end
	local sub, arg = rest:match("^(%S+)%s*(.-)$")
	sub = sub:lower()
	if sub == "entfernen" or sub == "remove" then
		self:Print(self:Remove(arg) and T("Entfernt.", "Removed.") or T("Nicht gefunden.", "Not found."))
		return
	end
	if self:Add(rest) then
		self:Print(string.format(T("Vorgemerkt: %s – Leeroyzan bietet die Installation beim nächsten Start an.", "Bookmarked: %s – Leeroyzan will offer to install it next time it starts."), rest))
	end
end, { "merken <Text>", "Addon für Leeroyzan vormerken", "bookmark an addon for Leeroyzan" })
