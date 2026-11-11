-- Recipes: whenever a profession window is open, remembers every visible recipe
-- with its reagents. Leeroyzan builds shopping lists across all characters from it
-- ("which reagents are missing, and which alt already has them?").
-- Classic API: GetTradeSkill* for most professions, GetCraft* for Enchanting.
local ADDON, LZ = ...
local T, P, C = LZ.T, LZ.P, LZ.C

local mod = LZ:NewModule("Recipes", {
	group = "link",
	title = { "Rezepte", "Recipes" },
	desc = {
		"Merkt sich beim Öffnen eines Berufsfensters alle Rezepte mit ihren Zutaten. Leeroyzan erstellt daraus Einkaufslisten über alle Charaktere. Eingeklappte Kategorien im Berufsfenster werden nicht gelesen.",
		"Remembers all recipes with their reagents when you open a profession window. Leeroyzan builds shopping lists across all your characters from them. Collapsed categories in the profession window are skipped.",
	},
})

local MAX_RECIPES = 500
local queued = {}

local API = {
	trade = {
		line = "GetTradeSkillLine", num = "GetNumTradeSkills", info = "GetTradeSkillInfo",
		itemLink = "GetTradeSkillItemLink", made = "GetTradeSkillNumMade",
		numReagents = "GetTradeSkillNumReagents", reagent = "GetTradeSkillReagentInfo",
		reagentLink = "GetTradeSkillReagentItemLink",
	},
	craft = {
		line = "GetCraftDisplaySkillLine", num = "GetNumCrafts", info = "GetCraftInfo",
		itemLink = "GetCraftItemLink", made = nil,
		numReagents = "GetCraftNumReagents", reagent = "GetCraftReagentInfo",
		reagentLink = "GetCraftReagentItemLink",
	},
}

local function call(name, ...)
	local f = name and _G[name]
	if type(f) ~= "function" then return nil end
	local res = { pcall(f, ...) }
	if not res[1] then return nil end
	return unpack(res, 2, 12)
end

function mod:OnInit()
	LZ.char.recipes = LZ.char.recipes or {}
end

-- Reads the open profession window. Returns the number of recipes seen.
function mod:Scan(kind)
	local api = API[kind]
	if not api or type(_G[api.num]) ~= "function" then return 0 end
	local prof, rank, maxRank = call(api.line)
	if type(prof) ~= "string" or prof == "" or prof == "UNKNOWN" then return 0 end
	local book = LZ.char.recipes[prof]
	if not book then
		book = { list = {} }
		LZ.char.recipes[prof] = book
	end
	book.rank, book.max, book.t = tonumber(rank), tonumber(maxRank), time()
	local count = 0
	for _ in pairs(book.list) do count = count + 1 end

	local seen = 0
	for i = 1, (call(api.num) or 0) do
		local name, kindOf = call(api.info, i)
		if type(name) == "string" and name ~= "" and kindOf ~= "header" and kindOf ~= "subheader" then
			local e = book.list[name]
			if not e and count < MAX_RECIPES then
				e = {}
				book.list[name] = e
				count = count + 1
			end
			if e then
				seen = seen + 1
				local link = call(api.itemLink, i)
				e.item = LZ.ItemID(link) or e.item
				if e.item then LZ.RememberItem(e.item, type(link) == "string" and link:match("%[(.-)%]") or nil, LZ.QualityFromLink(link)) end
				if api.made then
					local lo = call(api.made, i)
					e.made = tonumber(lo) or e.made
				end
				local reagents = {}
				for r = 1, (call(api.numReagents, i) or 0) do
					local rname, _, need = call(api.reagent, i, r)
					local rlink = call(api.reagentLink, i, r)
					local id = LZ.ItemID(rlink)
					if rname or id then
						reagents[#reagents + 1] = { id = id, n = tonumber(need) or 1, name = rname }
						if id then LZ.RememberItem(id, rname, LZ.QualityFromLink(rlink)) end
					end
				end
				if #reagents > 0 then e.r = reagents end
			end
		end
	end
	return seen
end

-- The window fires many update events while it fills: read once shortly after.
local function queue(kind)
	if queued[kind] then return end
	queued[kind] = true
	LZ.After(0.4, function()
		queued[kind] = nil
		if mod.enabled then mod:Scan(kind) end
	end)
end

mod:Event("TRADE_SKILL_SHOW", function() queue("trade") end)
mod:Event("TRADE_SKILL_UPDATE", function() queue("trade") end)
mod:Event("CRAFT_SHOW", function() queue("craft") end)
mod:Event("CRAFT_UPDATE", function() queue("craft") end)

function mod:Summary()
	local profs, n = 0, 0
	for _, book in pairs(LZ.char.recipes or {}) do
		profs = profs + 1
		for _ in pairs(book.list or {}) do n = n + 1 end
	end
	return string.format(T("%d Rezepte in %d Berufen", "%d recipes in %d professions"), n, profs)
end

function mod:Report()
	local lines = { LZ.Color("ffd100", T("Bekannte Rezepte (dieser Charakter)", "Known recipes (this character)")) }
	local any = false
	for prof, book in pairs(LZ.char.recipes or {}) do
		local n = 0
		for _ in pairs(book.list or {}) do n = n + 1 end
		any = true
		lines[#lines + 1] = string.format("%s %s/%s – %d %s, %s", prof, tostring(book.rank or "?"), tostring(book.max or "?"),
			n, T("Rezepte", "recipes"), LZ.Ago(book.t))
	end
	if not any then
		lines[#lines + 1] = T("Noch keine. Öffne einmal dein Berufsfenster (z. B. Alchemie).", "None yet. Open your profession window once (e.g. Alchemy).")
	end
	lines[#lines + 1] = ""
	lines[#lines + 1] = T("Einkaufslisten über alle Charaktere erstellt Leeroyzan auf der Seite „Charaktere“.",
		"Leeroyzan builds shopping lists across all characters on its \"Characters\" page.")
	return table.concat(lines, "\n")
end

mod:Command({ "rezepte", "recipes" }, function(self) LZ.UI:Show(self.name) end,
	{ "rezepte", "Gespeicherte Rezepte anzeigen", "show stored recipes" })
