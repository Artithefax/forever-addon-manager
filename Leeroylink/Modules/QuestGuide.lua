-- QuestGuide: the quest companion. A window of its own (no other addon needed, also
-- not the quest frame of the game or of DialogueUI) that shows where a quest belongs
-- in its quest chain and what the next quests ask for and give.
--
-- Data: Quests.lua (LeeroylinkQuests), written by Leeroyzan from QuestieDB. Rewards
-- are not in that data: every character remembers the rewards it sees at a quest giver
-- or in the quest log (global.questRewards) – alts then know them in advance.
local ADDON, LZ = ...
local T, P, C = LZ.T, LZ.P, LZ.C

local mod = LZ:NewModule("QuestGuide", {
	group = "link",
	title = { "Questbegleiter", "Quest companion" },
	desc = {
		"Zeigt in einem eigenen Fenster, zu welcher Questreihe eine Quest gehört und was die Folgequests verlangen und bringen. Die Questdaten schreibt Leeroyzan aus QuestieDB; Belohnungen merkt sich jeder Charakter, sobald er sie sieht.",
		"Shows in a window of its own which quest chain a quest belongs to and what the follow-up quests ask for and give. Leeroyzan writes the quest data from QuestieDB; every character remembers rewards as soon as it sees them.",
	},
	defaults = { auto = true, autoLog = true },
	options = {
		{ key = "auto", label = { "Beim Questgeber automatisch zeigen", "Show automatically at quest givers" } },
		{ key = "autoLog", label = { "Beim Auswählen einer Quest im Questlog zeigen", "Show when selecting a quest in the quest log" } },
	},
})

local TEMPLATE = _G.BackdropTemplateMixin and "BackdropTemplate" or nil
local BACKDROP = {
	bgFile = "Interface\\Tooltips\\UI-Tooltip-Background",
	edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
	tile = true, tileSize = 16, edgeSize = 16,
	insets = { left = 4, right = 4, top = 4, bottom = 4 },
}
local WIDTH, TEXT_W = 360, 318
local MAX_SHOWN_NEXT = 6
local QUALITY = { [0] = "9d9d9d", "ffffff", "1eff00", "0070dd", "a335ee", "ff8000", "e6cc80" }

-- fields of LeeroylinkQuests.q[id]
local NAME, LVL, REQ, ZONE, SIDE, NEXT, PRE, START, FINISH, TEXT, REP, XP = 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12

local frame, shownID, autoShown
local nextOf, prevOf

local function known(v)
	if _G.issecretvalue and issecretvalue(v) then return nil end
	return v
end

local function data()
	local d = _G.LeeroylinkQuests
	if type(d) == "table" and type(d.q) == "table" then return d end
end

local function quest(id) local d = data(); return d and id and d.q[id] end

local function mySide()
	local f = UnitFactionGroup and known(UnitFactionGroup("player"))
	return f == "Alliance" and "A" or f == "Horde" and "H" or nil
end

local function fits(id)
	local q = quest(id)
	if not q then return false end
	local side, mine = q[SIDE], mySide()
	return not side or not mine or side == mine
end

-- quests unlocked by a quest and quests leading to it (built once from the data)
local function links()
	if nextOf then return end
	nextOf, prevOf = {}, {}
	local d = data()
	if not d then return end
	local function add(map, k, v)
		local l = map[k]
		if not l then l = {}; map[k] = l end
		for _, x in ipairs(l) do if x == v then return end end
		l[#l + 1] = v
	end
	for id, q in pairs(d.q) do
		if type(q[PRE]) == "table" then
			for _, p in ipairs(q[PRE]) do
				if d.q[p] then add(nextOf, p, id); add(prevOf, id, p) end
			end
		end
		local n = q[NEXT]
		if n and d.q[n] then
			local l = nextOf[id]
			if not l then l = {}; nextOf[id] = l end
			local found
			for _, x in ipairs(l) do if x == n then found = true end end
			if not found then table.insert(l, 1, n) end
			add(prevOf, n, id)
		end
	end
end

-- The chain as a list: start … id … end (with branches: the main path)
function mod:Chain(id)
	links()
	if not quest(id) then return { id }, 1 end
	local seen = { [id] = true }
	local back, cur = {}, id
	while #back < 40 do
		local cand, pick = prevOf[cur] or {}, nil
		for _, p in ipairs(cand) do
			if not seen[p] and fits(p) and quest(p)[NEXT] == cur then pick = p; break end
		end
		if not pick then
			for _, p in ipairs(cand) do if not seen[p] and fits(p) then pick = p; break end end
		end
		if not pick then break end
		seen[pick] = true
		back[#back + 1] = pick
		cur = pick
	end
	local list = {}
	for i = #back, 1, -1 do list[#list + 1] = back[i] end
	list[#list + 1] = id
	local pos = #list
	cur = id
	while #list < 80 do
		local pick
		for _, n in ipairs(nextOf[cur] or {}) do
			if not seen[n] and fits(n) then pick = n; break end
		end
		if not pick then break end
		seen[pick] = true
		list[#list + 1] = pick
		cur = pick
	end
	return list, pos
end

----------------------------------------------------------------------
-- Rewards: remembered by every character, for all of them
----------------------------------------------------------------------
local function call(f, ...)
	if type(f) ~= "function" then return nil end
	local r = { pcall(f, ...) }
	if not r[1] then return nil end
	return unpack(r, 2, 14)
end

local function itemEntry(name, count, quality, link)
	name, count, quality = known(name), known(count), known(quality)
	local id = LZ.ItemID(known(link))
	if not name and not id then return nil end
	if type(quality) ~= "number" or quality < 0 then quality = LZ.QualityFromLink(known(link)) end
	if id then LZ.RememberItem(id, name, quality) end
	return { id = id, n = tonumber(count) or 1, q = tonumber(quality) or 1, name = name }
end

local function store(id, r)
	if not id or id <= 0 then return end
	local all = LZ.gdb.questRewards
	if not all then all = {}; LZ.gdb.questRewards = all end
	r.t = time()
	r.lvl = known(UnitLevel and UnitLevel("player"))
	all[id] = r
end

-- quest giver (QUEST_DETAIL / QUEST_COMPLETE)
local function learnFromGiver(id)
	local r = { items = {}, choice = {} }
	r.m = tonumber(known(call(_G.GetRewardMoney))) or 0
	r.xp = tonumber(known(call(_G.GetRewardXP))) or 0
	for i = 1, tonumber(known(call(_G.GetNumQuestRewards))) or 0 do
		local name, _, count, quality = call(_G.GetQuestItemInfo, "reward", i)
		local e = itemEntry(name, count, quality, call(_G.GetQuestItemLink, "reward", i))
		if e then r.items[#r.items + 1] = e end
	end
	for i = 1, tonumber(known(call(_G.GetNumQuestChoices))) or 0 do
		local name, _, count, quality = call(_G.GetQuestItemInfo, "choice", i)
		local e = itemEntry(name, count, quality, call(_G.GetQuestItemLink, "choice", i))
		if e then r.choice[#r.choice + 1] = e end
	end
	local spell = known(call(_G.GetRewardSpell))
	if type(spell) == "string" and spell ~= "" then r.spell = spell end
	store(id, r)
end

-- quest log (after a quest was selected there)
local function learnFromLog(id)
	if not _G.GetNumQuestLogRewards then return end
	local r = { items = {}, choice = {} }
	r.m = tonumber(known(call(_G.GetQuestLogRewardMoney))) or 0
	r.xp = tonumber(known(call(_G.GetQuestLogRewardXP))) or 0
	for i = 1, tonumber(known(call(_G.GetNumQuestLogRewards))) or 0 do
		local name, _, count, quality = call(_G.GetQuestLogRewardInfo, i)
		local e = itemEntry(name, count, quality, call(_G.GetQuestLogItemLink, "reward", i))
		if e then r.items[#r.items + 1] = e end
	end
	for i = 1, tonumber(known(call(_G.GetNumQuestLogChoices))) or 0 do
		local name, _, count, quality = call(_G.GetQuestLogChoiceInfo, i)
		local e = itemEntry(name, count, quality, call(_G.GetQuestLogItemLink, "choice", i))
		if e then r.choice[#r.choice + 1] = e end
	end
	local old = LZ.gdb.questRewards and LZ.gdb.questRewards[id]
	-- the giver's view is the more exact one (the log has no XP on older clients)
	if old and r.xp == 0 then r.xp = old.xp end
	store(id, r)
end

----------------------------------------------------------------------
-- Text
----------------------------------------------------------------------
local function zoneName(z)
	if not z then return nil end
	if z > 0 and _G.C_Map and C_Map.GetAreaInfo then
		local ok, n = pcall(C_Map.GetAreaInfo, z)
		if ok and type(n) == "string" and n ~= "" then return n end
	end
	local d = data()
	return d and d.zones and d.zones[z] or nil
end

local function factionName(f)
	if _G.GetFactionInfoByID then
		local ok, n = pcall(GetFactionInfoByID, f)
		if ok and type(n) == "string" and n ~= "" then return n end
	end
	local d = data()
	return d and d.factions and d.factions[f] or ("#" .. tostring(f))
end

local function done(id)
	local QL = _G.C_QuestLog
	local f = (QL and QL.IsQuestFlaggedCompleted) or _G.IsQuestFlaggedCompleted
	if type(f) ~= "function" then return false end
	local ok, v = pcall(f, id)
	return ok and known(v) == true
end

local function inLog(id)
	if not (_G.GetNumQuestLogEntries and _G.GetQuestLogTitle) then return false end
	for i = 1, GetNumQuestLogEntries() or 0 do
		local _, _, _, isHeader, _, _, _, qid = GetQuestLogTitle(i)
		if not isHeader and qid == id then return true end
	end
	return false
end

local function where(g)
	if type(g) ~= "table" then return nil end
	local s = tostring(g[1])
	local z = zoneName(g[2])
	if g[5] == "o" then s = s .. " " .. T("(Gegenstand in der Welt)", "(object in the world)")
	elseif g[5] == "i" then s = s .. " " .. T("(Gegenstand)", "(item)") end
	if z then
		s = s .. " – " .. z
		if g[3] and g[4] then s = s .. string.format(" %.0f, %.0f", g[3], g[4]) end
	end
	return s
end

local function itemText(e)
	local name = e.name or (e.id and C.GetItemName(e.id)) or ("#" .. tostring(e.id))
	local s = LZ.Color(QUALITY[e.q or 1] or "ffffff", "[" .. name .. "]")
	if (e.n or 1) > 1 then s = s .. " x" .. e.n end
	return s
end

-- one line with everything a quest gives; sums go into total (optional)
local function rewardLine(id, total)
	local q, parts = quest(id), {}
	local r = LZ.gdb.questRewards and LZ.gdb.questRewards[id]
	local xp = (r and r.xp and r.xp > 0 and r.xp) or (q and q[XP]) or 0
	if xp > 0 then
		parts[#parts + 1] = LZ.Num and LZ.Num(xp) .. " XP" or (xp .. " XP")
		if total then total.xp = total.xp + xp end
	end
	if r and (r.m or 0) > 0 then
		parts[#parts + 1] = LZ.FormatMoney(r.m)
		if total then total.m = total.m + r.m end
	end
	if q and type(q[REP]) == "table" then
		for i = 1, #q[REP], 2 do
			local f, v = q[REP][i], q[REP][i + 1]
			parts[#parts + 1] = factionName(f) .. " " .. (v > 0 and "+" or "") .. v
			if total then total.rep[f] = (total.rep[f] or 0) + v end
		end
	end
	if r then
		for _, e in ipairs(r.items or {}) do
			parts[#parts + 1] = itemText(e)
			if total then total.items = total.items + 1 end
		end
		if r.choice and #r.choice > 0 then
			local c = {}
			for _, e in ipairs(r.choice) do c[#c + 1] = itemText(e) end
			parts[#parts + 1] = T("Wahl: ", "choice: ") .. table.concat(c, T(" oder ", " or "))
			if total then total.items = total.items + 1 end
		end
		if r.spell then parts[#parts + 1] = r.spell end
	elseif total then
		total.unknown = total.unknown + 1
	end
	if #parts == 0 then return nil end
	local s = table.concat(parts, " · ")
	if not r then s = s .. LZ.Color("808080", "  " .. T("(Gegenstände noch nicht gesehen)", "(items not seen yet)")) end
	return s
end

local function title(id)
	local q = quest(id)
	if not q then return "#" .. tostring(id) end
	local s = string.format("%s [%d]", q[NAME], q[LVL] or 0)
	if done(id) then return LZ.Color("40c040", s .. " " .. T("(erledigt)", "(done)")) end
	if inLog(id) then return LZ.Color("ffd100", s .. " " .. T("(im Questlog)", "(in quest log)")) end
	return s
end

function mod:Text(id)
	local d = data()
	if not d then
		return T("Noch keine Questdaten. Leeroyzan schreibt sie, sobald QuestieDB installiert ist und Leeroyzan läuft – danach /reload.",
			"No quest data yet. Leeroyzan writes it as soon as QuestieDB is installed and Leeroyzan is running – then /reload.")
	end
	local q = quest(id)
	if not q then
		return T("Diese Quest kennt die Questdatenbank nicht", "The quest database does not know this quest") .. " (#" .. tostring(id) .. ")."
	end
	local lines = {}
	local function add(s) lines[#lines + 1] = s or "" end
	local list, pos = self:Chain(id)
	add(LZ.Color("ffd100", q[NAME]) .. LZ.Color("a0a0a0", string.format("  %s %d", T("Stufe", "level"), q[LVL] or 0)))
	local info = {}
	if #list > 1 then info[#info + 1] = string.format(T("Questreihe: Teil %d von %d", "Quest chain: part %d of %d"), pos, #list) end
	local z = zoneName(q[ZONE])
	if z then info[#info + 1] = z end
	if (q[REQ] or 0) > 1 then info[#info + 1] = string.format(T("ab Stufe %d", "from level %d"), q[REQ]) end
	if #info > 0 then add(LZ.Color("a0a0a0", table.concat(info, " · "))) end
	add()
	if type(q[TEXT]) == "table" and #q[TEXT] > 0 then
		add(LZ.Color("ffd100", T("Aufgabe", "Objective")))
		for _, t in ipairs(q[TEXT]) do add(t) end
		add()
	end
	local rl = rewardLine(id)
	if rl then add(LZ.Color("ffd100", T("Belohnung", "Reward")) .. "  " .. rl) end
	local fin = where(q[FINISH])
	if fin then add(LZ.Color("ffd100", T("Abgabe", "Turn in")) .. "  " .. fin) end

	-- what comes next
	if pos < #list then
		add()
		add(LZ.Color("ffd100", T("Danach", "Next")))
		for i = pos + 1, math.min(#list, pos + MAX_SHOWN_NEXT) do
			local nid, nq = list[i], quest(list[i])
			add(string.format("%d. %s", i, title(nid)))
			local giver = where(nq[START])
			if giver then add("   " .. T("Bei: ", "From: ") .. giver) end
			if type(nq[TEXT]) == "table" and nq[TEXT][1] then add("   " .. LZ.Color("c8c8c8", nq[TEXT][1])) end
			local r = rewardLine(nid)
			if r then add("   " .. r) end
		end
		if #list > pos + MAX_SHOWN_NEXT then
			add(LZ.Color("a0a0a0", string.format(T("… und %d weitere", "… and %d more"), #list - pos - MAX_SHOWN_NEXT)))
		end
	end
	-- other quests this one unlocks (branches)
	links()
	local extra = {}
	for _, n in ipairs(nextOf[id] or {}) do
		if fits(n) and n ~= list[pos + 1] then extra[#extra + 1] = title(n) end
	end
	if #extra > 0 then
		add()
		add(LZ.Color("ffd100", T("Schaltet außerdem frei", "Also unlocks")))
		for _, e in ipairs(extra) do add("• " .. e) end
	end
	if pos > 1 then
		add()
		add(LZ.Color("ffd100", T("Vorher", "Before")))
		for i = 1, pos - 1 do add(string.format("%d. %s", i, title(list[i]))) end
	end
	-- sum of the rest of the chain (this quest and everything after it)
	if pos < #list then
		local total = { xp = 0, m = 0, rep = {}, items = 0, unknown = 0 }
		for i = pos, #list do rewardLine(list[i], total) end
		local parts = {}
		if total.xp > 0 then parts[#parts + 1] = (LZ.Num and LZ.Num(total.xp) or total.xp) .. " XP" end
		if total.m > 0 then parts[#parts + 1] = LZ.FormatMoney(total.m) end
		for f, v in pairs(total.rep) do parts[#parts + 1] = factionName(f) .. " +" .. v end
		if total.items > 0 then parts[#parts + 1] = string.format(T("%d Gegenstandsbelohnungen", "%d item rewards"), total.items) end
		add()
		add(LZ.Color("ffd100", string.format(T("Bis zum Ende der Reihe (%d Quests)", "Until the end of the chain (%d quests)"), #list - pos + 1)))
		add(#parts > 0 and table.concat(parts, " · ") or "–")
		if total.unknown > 0 then
			add(LZ.Color("808080", string.format(T("Gegenstände von %d Quests noch nicht gesehen.", "Items of %d quests not seen yet."), total.unknown)))
		end
	end
	return table.concat(lines, "\n")
end

----------------------------------------------------------------------
-- Window (independent of every quest frame)
----------------------------------------------------------------------
local function savePosition()
	if not frame then return end
	local point, _, rel, x, y = frame:GetPoint(1)
	mod.opt.pos = { point, rel, x, y }
end

local function step(dir)
	if not shownID then return end
	local list, pos = mod:Chain(shownID)
	local target = list[pos + dir]
	if target then mod:Show(target) end
end

local function build()
	if frame then return end
	frame = CreateFrame("Frame", "LeeroylinkQuestGuide", UIParent, TEMPLATE)
	frame:SetSize(WIDTH, 440)
	local p = mod.opt.pos
	if type(p) == "table" and p[1] then
		frame:SetPoint(p[1], UIParent, p[2] or p[1], p[3] or 0, p[4] or 0)
	else
		frame:SetPoint("RIGHT", UIParent, "RIGHT", -80, 40)
	end
	frame:SetFrameStrata("HIGH")
	frame:SetClampedToScreen(true)
	frame:SetMovable(true)
	frame:EnableMouse(true)
	frame:RegisterForDrag("LeftButton")
	frame:SetScript("OnDragStart", function(s) s:StartMoving() end)
	frame:SetScript("OnDragStop", function(s) s:StopMovingOrSizing(); savePosition() end)
	if frame.SetBackdrop then
		frame:SetBackdrop(BACKDROP)
		frame:SetBackdropColor(0, 0, 0, 0.9)
	end
	frame:Hide()
	if _G.UISpecialFrames then table.insert(UISpecialFrames, "LeeroylinkQuestGuide") end

	local head = frame:CreateFontString(nil, "ARTWORK", "GameFontNormal")
	head:SetPoint("TOPLEFT", 14, -12)
	head:SetText(P(mod.title))
	local close = CreateFrame("Button", nil, frame, "UIPanelCloseButton")
	close:SetPoint("TOPRIGHT", -2, -2)

	local scroll = CreateFrame("ScrollFrame", "LeeroylinkQuestGuideScroll", frame, "UIPanelScrollFrameTemplate")
	scroll:SetPoint("TOPLEFT", 12, -34)
	scroll:SetPoint("BOTTOMRIGHT", -30, 40)
	local content = CreateFrame("Frame", nil, scroll)
	content:SetSize(TEXT_W, 10)
	scroll:SetScrollChild(content)
	local text = content:CreateFontString(nil, "ARTWORK", "GameFontHighlightSmall")
	text:SetPoint("TOPLEFT", 0, 0)
	text:SetWidth(TEXT_W)
	text:SetJustifyH("LEFT")
	text:SetJustifyV("TOP")
	if text.SetSpacing then text:SetSpacing(2) end
	frame.scroll, frame.content, frame.text = scroll, content, text

	local function button(label, x, fn)
		local b = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
		b:SetSize(96, 22)
		b:SetPoint("BOTTOMLEFT", x, 12)
		b:SetText(label)
		b:SetScript("OnClick", fn)
		return b
	end
	frame.prev = button(T("< Vorher", "< Previous"), 12, function() step(-1) end)
	frame.next = button(T("Danach >", "Next >"), 112, function() step(1) end)
	frame.log = button(T("Questlog", "Quest log"), 212, function() mod:ShowSelected(true) end)
	frame:SetScript("OnHide", function() autoShown = false end)
end

function mod:Show(id, auto)
	build()
	shownID = id
	if not frame:IsShown() then autoShown = auto and true or false
	elseif not auto then autoShown = false end
	frame.text:SetText(self:Text(id))
	frame.content:SetHeight(math.max(20, (frame.text:GetStringHeight() or 0) + 8))
	frame.scroll:SetVerticalScroll(0)
	local list, pos = self:Chain(id)
	if pos > 1 then frame.prev:Enable() else frame.prev:Disable() end
	if pos < #list then frame.next:Enable() else frame.next:Disable() end
	frame:Show()
end

function mod:Hide()
	if frame then frame:Hide() end
end

----------------------------------------------------------------------
-- When to show
----------------------------------------------------------------------
local function giverQuestID()
	local id = known(call(_G.GetQuestID))
	return type(id) == "number" and id > 0 and id or nil
end

local function logQuestID(index)
	local QL = _G.C_QuestLog
	if not index and QL and QL.GetSelectedQuest then
		local id = known(call(QL.GetSelectedQuest))
		if type(id) == "number" and id > 0 then return id end
	end
	index = index or known(call(_G.GetQuestLogSelection))
	if not index or index <= 0 then return nil end
	if QL and QL.GetQuestIDForLogIndex then
		local id = known(call(QL.GetQuestIDForLogIndex, index))
		if type(id) == "number" and id > 0 then return id end
	end
	local _, _, _, isHeader, _, _, _, id = call(_G.GetQuestLogTitle, index)
	if not known(isHeader) and type(known(id)) == "number" and id > 0 then return id end
end

function mod:ShowSelected(force)
	local id = logQuestID()
	if id then
		learnFromLog(id)
		if force or (self.opt.autoLog and quest(id)) then self:Show(id, not force) end
	elseif force then
		self:Print(T("Wähle zuerst eine Quest im Questlog aus – oder /lz quest <Name>.", "Select a quest in the quest log first – or /lz quest <name>."))
	end
end

mod:Event("QUEST_DETAIL", function(self)
	local id = giverQuestID()
	if not id then return end
	learnFromGiver(id)
	if self.opt.auto and quest(id) then self:Show(id, true) end
end)

mod:Event("QUEST_COMPLETE", function(self)
	local id = giverQuestID()
	if not id then return end
	learnFromGiver(id)
	-- on turn-in, show what comes next
	if self.opt.auto and quest(id) then
		local list, pos = self:Chain(id)
		self:Show(list[pos + 1] and quest(list[pos + 1]) and list[pos + 1] or id, true)
	end
end)

mod:Event("QUEST_FINISHED", function()
	if autoShown and frame then frame:Hide() end
end)

-- completed/accepted quests change the markers in an open window
mod:Event("QUEST_LOG_UPDATE", function(self)
	if frame and frame:IsShown() and shownID and not self.refreshing then
		self.refreshing = true
		LZ.After(0.5, function()
			self.refreshing = nil
			if frame:IsShown() and shownID then
				frame.text:SetText(self:Text(shownID))
				frame.content:SetHeight(math.max(20, (frame.text:GetStringHeight() or 0) + 8))
			end
		end)
	end
end)

local hooked = false
function mod:OnEnable()
	-- any quest log (the game's or one of an addon) selects through this function
	if not hooked and _G.hooksecurefunc and _G.SelectQuestLogEntry then
		hooked = true
		hooksecurefunc("SelectQuestLogEntry", function()
			if mod.enabled then LZ:Safe(function() mod:ShowSelected(false) end) end
		end)
	end
	if not hooked and _G.hooksecurefunc and _G.C_QuestLog and C_QuestLog.SetSelectedQuest then
		hooked = true
		hooksecurefunc(C_QuestLog, "SetSelectedQuest", function()
			if mod.enabled then LZ:Safe(function() mod:ShowSelected(false) end) end
		end)
	end
	-- names from the game for Leeroyzan (zones and factions in the language of the game)
	LZ.After(8, function() LZ:Safe(mod.CollectNames, mod) end)
end

function mod:OnDisable()
	self:Hide()
end

function mod:CollectNames()
	local d = data()
	if not d then return end
	local names = LZ.gdb.names
	if not names then names = {}; LZ.gdb.names = names end
	names.zones, names.factions = names.zones or {}, names.factions or {}
	if _G.C_Map and C_Map.GetAreaInfo then
		for z in pairs(d.zones or {}) do
			if z > 0 and not names.zones[z] then
				local ok, n = pcall(C_Map.GetAreaInfo, z)
				if ok and type(n) == "string" and n ~= "" then names.zones[z] = n end
			end
		end
	end
	if _G.GetFactionInfoByID then
		for f in pairs(d.factions or {}) do
			if not names.factions[f] then
				local ok, n = pcall(GetFactionInfoByID, f)
				if ok and type(n) == "string" and n ~= "" then names.factions[f] = n end
			end
		end
	end
end

----------------------------------------------------------------------
-- Find by name, command, report
----------------------------------------------------------------------
function mod:Find(text)
	local d = data()
	if not d then return nil end
	local id = tonumber(text)
	if id and d.q[id] then return id end
	local low = text:lower()
	local best
	for qid, q in pairs(d.q) do
		local n = tostring(q[NAME]):lower()
		if n == low and fits(qid) then return qid end
		if not best and n:find(low, 1, true) and fits(qid) then best = qid end
	end
	return best
end

mod:Command({ "quest", "quests", "questbegleiter" }, function(self, rest)
	rest = LZ.Trim(rest)
	if rest == "" then
		if frame and frame:IsShown() then return self:Hide() end
		if logQuestID() then return self:ShowSelected(true) end
		if shownID then return self:Show(shownID) end
		return LZ.UI:Show(self.name)
	end
	local id = self:Find(rest)
	if id then self:Show(id) else self:Print(T("Keine Quest gefunden: ", "No quest found: ") .. rest) end
end, { "quest [Name|ID]", "Questbegleiter: Questreihe und Folgequests", "quest companion: quest chain and follow-up quests" })

function mod:Summary()
	local d = data()
	if not d then return T("keine Questdaten", "no quest data") end
	local n, r = 0, 0
	for _ in pairs(d.q) do n = n + 1 end
	for _ in pairs(LZ.gdb.questRewards or {}) do r = r + 1 end
	return string.format(T("%d Quests, %d Belohnungen gesehen", "%d quests, %d rewards seen"), n, r)
end

function mod:Report()
	local d = data()
	local lines = {}
	if d then
		lines[#lines + 1] = string.format(T("Questdaten: %s (%s), %s", "Quest data: %s (%s), %s"), tostring(d.src), tostring(d.locale), self:Summary())
	else
		lines[#lines + 1] = T("Noch keine Questdaten. Leeroyzan schreibt sie aus QuestieDB (muss nur installiert sein, nicht geladen) – danach /reload.",
			"No quest data yet. Leeroyzan writes it from QuestieDB (only needs to be installed, not loaded) – then /reload.")
	end
	lines[#lines + 1] = ""
	lines[#lines + 1] = T("Das Fenster öffnet sich beim Questgeber und beim Auswählen einer Quest im Questlog (abschaltbar unter Einstellungen). /lz quest <Name> sucht eine Quest.",
		"The window opens at quest givers and when you select a quest in the quest log (can be turned off in the settings). /lz quest <name> looks up a quest.")
	return table.concat(lines, "\n")
end

mod.actions = {
	{ label = { "Fenster öffnen", "Open window" }, fn = function(self)
		if logQuestID() then self:ShowSelected(true) elseif shownID then self:Show(shownID) else
			self:Print(T("Wähle zuerst eine Quest im Questlog aus – oder /lz quest <Name>.", "Select a quest in the quest log first – or /lz quest <name>."))
		end
	end },
}
