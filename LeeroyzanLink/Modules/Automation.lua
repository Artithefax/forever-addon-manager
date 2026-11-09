-- Automation: small conveniences at vendors and quest givers. Everything is OFF
-- by default - switch on what you want with /lz auto. Hold SHIFT to skip quest automation.
local ADDON, LZ = ...
local T, P, C = LZ.T, LZ.P, LZ.C

local mod = LZ:NewModule("Automation", {
	title = { "Automatiken", "Automation" },
	desc = {
		"Graue Gegenstände automatisch verkaufen, bei Händlern reparieren, Quests annehmen/abgeben (Shift hält an). Standardmäßig alles aus.",
		"Auto-sell gray items, repair at vendors, accept/turn in quests (Shift pauses). Everything is off by default.",
	},
	defaults = { sellGrey = false, repair = false, quests = false },
	options = {
		{ key = "sellGrey", label = { "Graue Gegenstände beim Händler verkaufen", "Sell gray items at vendors" } },
		{ key = "repair", label = { "Beim Händler automatisch reparieren", "Repair automatically at vendors" } },
		{ key = "quests", label = { "Quests automatisch annehmen/abgeben (Belohnung nur ohne Auswahl)", "Auto accept/turn in quests (reward only when there is no choice)" } },
	},
})

local function shiftDown() return IsShiftKeyDown and IsShiftKeyDown() end

mod:Event("MERCHANT_SHOW", function(self)
	if self.opt.sellGrey then
		local sold, value = 0, 0
		for _, bag in ipairs(C.BagIDs()) do
			for slot = 1, C.NumSlots(bag) do
				local itemID, count, quality, link, noValue = C.ItemAt(bag, slot)
				if itemID and quality == 0 and not noValue then
					value = value + C.GetSellPrice(link or itemID) * (count or 1)
					C.UseItem(bag, slot)
					sold = sold + 1
				end
			end
		end
		if sold > 0 then self:Print(string.format(T("%d graue Gegenstände verkauft (%s).", "Sold %d gray items (%s)."), sold, LZ.FormatMoney(value))) end
	end
	if self.opt.repair and _G.CanMerchantRepair and CanMerchantRepair() then
		local cost = GetRepairAllCost and GetRepairAllCost()
		if cost and cost > 0 then
			if GetMoney() >= cost then
				RepairAllItems()
				self:Print(string.format(T("Repariert für %s.", "Repaired for %s."), LZ.FormatMoney(cost)))
			else
				self:Print(T("Nicht genug Gold für die Reparatur.", "Not enough gold to repair."))
			end
		end
	end
end)

mod:Event("QUEST_DETAIL", function(self)
	if self.opt.quests and not shiftDown() and _G.AcceptQuest then AcceptQuest() end
end)

mod:Event("QUEST_PROGRESS", function(self)
	if self.opt.quests and not shiftDown() and _G.IsQuestCompletable and IsQuestCompletable() and _G.CompleteQuest then CompleteQuest() end
end)

mod:Event("QUEST_COMPLETE", function(self)
	if not self.opt.quests or shiftDown() then return end
	local choices = _G.GetNumQuestChoices and GetNumQuestChoices() or 0
	if choices <= 1 and _G.GetQuestReward then GetQuestReward(1) end
end)

function mod:Summary()
	local on = {}
	for _, o in ipairs(self.options) do if self.opt[o.key] then on[#on + 1] = o.key end end
	return #on > 0 and table.concat(on, ", ") or T("alles aus", "all off")
end

function mod:Report()
	local lines = { LZ.Color("ffd100", T("Aktueller Stand", "Current state")) }
	for _, o in ipairs(self.options) do
		lines[#lines + 1] = string.format("[%s] %s", self.opt[o.key] and "x" or " ", P(o.label))
	end
	lines[#lines + 1] = ""
	lines[#lines + 1] = T("Schalten: /lz auto grau|reparatur|quests an|aus (oder im Reiter Einstellungen)", "Toggle: /lz auto grey|repair|quests on|off (or in the Settings tab)")
	return table.concat(lines, "\n")
end

local KEYS = { grau = "sellGrey", grey = "sellGrey", gray = "sellGrey", reparatur = "repair", repair = "repair", quests = "quests" }

mod:Command({ "auto", "automatik" }, function(self, rest)
	local what, state = rest:match("^(%S*)%s*(%S*)$")
	local key = KEYS[(what or ""):lower()]
	local want = LZ.ParseState(state)
	if not key or want == nil then LZ.UI:Show(self.name) return end
	self.opt[key] = want
	self:Print(string.format("%s: %s", key, want and T("an", "on") or T("aus", "off")))
end, { "auto grau|reparatur|quests an|aus", "Automatiken schalten", "toggle automations" })
