-- Prices: records auction house prices whenever you browse the AH and shows the
-- last price and a 30-day average in item tooltips. Both the modern
-- C_AuctionHouse API and the classic list API are supported (best effort).
local ADDON, LZ = ...
local T, P, C = LZ.T, LZ.P, LZ.C

local mod = LZ:NewModule("Prices", {
	title = { "AH-Preisverlauf", "AH price history" },
	desc = {
		"Merkt sich Auktionshaus-Preise beim Stöbern und zeigt letzten Preis und 30-Tage-Schnitt im Item-Tooltip.",
		"Remembers auction house prices while you browse and shows the last price and a 30-day average in item tooltips.",
	},
	defaults = { tooltip = true },
	options = { { key = "tooltip", label = { "Preis im Item-Tooltip anzeigen", "Show price in item tooltips" } } },
})

local MAX_HIST = 40
local SAME_ENTRY_WINDOW = 6 * 3600
local DAY = 86400
local hooked = false

function mod:OnInit()
	LZ.gdb.prices = LZ.gdb.prices or {}
end

function mod:Record(itemID, unitPrice)
	if type(itemID) ~= "number" or type(unitPrice) ~= "number" or unitPrice <= 0 then return end
	unitPrice = math.floor(unitPrice)
	local now = time()
	local e = LZ.gdb.prices[itemID]
	if not e then e = { hist = {} }; LZ.gdb.prices[itemID] = e end
	e.last, e.t = unitPrice, now
	local h = e.hist
	local lastEntry = h[#h]
	if lastEntry and (now - lastEntry.t) < SAME_ENTRY_WINDOW then
		lastEntry.t, lastEntry.p = now, unitPrice
	else
		h[#h + 1] = { t = now, p = unitPrice }
		while #h > MAX_HIST do table.remove(h, 1) end
	end
end

function mod:Average(itemID, days)
	local e = LZ.gdb.prices[itemID]
	if not e then return nil end
	local cutoff = time() - (days or 30) * DAY
	local sum, n = 0, 0
	for _, x in ipairs(e.hist) do
		if x.t >= cutoff then sum = sum + x.p; n = n + 1 end
	end
	if n == 0 then return nil end
	return sum / n, n
end

----------------------------------------------------------------------
-- Collecting prices
----------------------------------------------------------------------
-- legacy list API: itemId is return value 17, buyoutPrice is 10, count is 3
local function scanLegacy(self)
	if not (_G.GetNumAuctionItems and _G.GetAuctionItemInfo) then return end
	local best = {}
	for i = 1, GetNumAuctionItems("list") do
		local ok, name, _, count, _, _, _, _, _, _, buyout, _, _, _, _, _, _, itemID = pcall(GetAuctionItemInfo, "list", i)
		if ok and itemID and buyout and buyout > 0 and count and count > 0 then
			local unit = buyout / count
			if not best[itemID] or unit < best[itemID] then best[itemID] = unit end
		end
	end
	for id, p in pairs(best) do self:Record(id, p) end
end

mod:Event("AUCTION_ITEM_LIST_UPDATE", scanLegacy)

-- modern commodities
mod:Event("COMMODITY_SEARCH_RESULTS_UPDATED", function(self, _, itemID)
	local AH = _G.C_AuctionHouse
	if not (AH and itemID and AH.GetCommoditySearchResultInfo) then return end
	local ok, r = pcall(AH.GetCommoditySearchResultInfo, itemID, 1)
	if ok and type(r) == "table" then self:Record(itemID, r.unitPrice) end
end)

-- modern non-commodity items
mod:Event("ITEM_SEARCH_RESULTS_UPDATED", function(self, _, itemKey)
	local AH = _G.C_AuctionHouse
	if not (AH and itemKey and AH.GetNumItemSearchResults and AH.GetItemSearchResultInfo) then return end
	local ok, n = pcall(AH.GetNumItemSearchResults, itemKey)
	if not ok or not n then return end
	local best
	for i = 1, math.min(n, 50) do
		local ok2, r = pcall(AH.GetItemSearchResultInfo, itemKey, i)
		if ok2 and type(r) == "table" and r.buyoutAmount and r.buyoutAmount > 0 then
			if not best or r.buyoutAmount < best then best = r.buyoutAmount end
		end
	end
	if best and itemKey.itemID then self:Record(itemKey.itemID, best) end
end)

-- modern browse list (minimum price per item)
mod:Event("AUCTION_HOUSE_BROWSE_RESULTS_UPDATED", function(self)
	local AH = _G.C_AuctionHouse
	if not (AH and AH.GetBrowseResults) then return end
	local ok, results = pcall(AH.GetBrowseResults)
	if not ok or type(results) ~= "table" then return end
	for i = 1, math.min(#results, 200) do
		local r = results[i]
		if r and r.itemKey and r.minPrice then self:Record(r.itemKey.itemID, r.minPrice) end
	end
end)

----------------------------------------------------------------------
-- Tooltip
----------------------------------------------------------------------
local function onItemTooltip(tooltip, data)
	if not (mod.enabled and mod.opt.tooltip) then return end
	local itemID = data and data.id
	if not itemID and tooltip.GetItem then
		local _, link = tooltip:GetItem()
		itemID = LZ.ItemID(link)
	end
	local e = itemID and LZ.gdb.prices[itemID]
	if not e or not e.last then return end
	local avg, n = mod:Average(itemID, 30)
	local line = string.format("AH: %s (%s)", LZ.FormatMoney(e.last), LZ.Ago(e.t))
	if avg and n > 1 then line = line .. string.format("  Ø30d: %s", LZ.FormatMoney(avg)) end
	tooltip:AddLine(line, 0.9, 0.8, 0.4)
	if tooltip.Show then tooltip:Show() end
end

function mod:OnEnable()
	if not hooked then hooked = LZ.HookTooltip("item", onItemTooltip) end
end

function mod:Summary()
	local n = 0
	for _ in pairs(LZ.gdb.prices) do n = n + 1 end
	return string.format(T("%d Items erfasst", "%d items tracked"), n)
end

function mod:Report()
	local list = {}
	for id, e in pairs(LZ.gdb.prices) do list[#list + 1] = { id = id, e = e } end
	table.sort(list, function(a, b) return (a.e.t or 0) > (b.e.t or 0) end)
	local lines = { LZ.Color("ffd100", T("Zuletzt erfasste Preise", "Most recently recorded prices")) }
	if #list == 0 then lines[#lines + 1] = T("Noch nichts erfasst – öffne das Auktionshaus und suche etwas.", "Nothing recorded yet – open the auction house and search for something.") end
	for i = 1, math.min(#list, 20) do
		local x = list[i]
		local avg, n = self:Average(x.id, 30)
		lines[#lines + 1] = string.format("%s: %s (%s)%s", C.GetItemName(x.id) or ("item:" .. x.id), LZ.FormatMoney(x.e.last),
			LZ.Ago(x.e.t), (avg and n > 1) and ("  Ø30d " .. LZ.FormatMoney(avg)) or "")
	end
	lines[#lines + 1] = ""
	lines[#lines + 1] = T("Verlauf: /lz preis <Itemlink oder ID>", "History: /lz price <item link or id>")
	return table.concat(lines, "\n")
end

mod:Command({ "preis", "price" }, function(self, rest)
	local id = LZ.ItemID(rest)
	if not id then LZ.UI:Show(self.name) return end
	local e = LZ.gdb.prices[id]
	if not e then self:Print(T("Keine Preisdaten für dieses Item.", "No price data for this item.")) return end
	self:Print(string.format("%s: %s (%s)", C.GetItemName(id) or id, LZ.FormatMoney(e.last), LZ.Ago(e.t)))
	for i = math.max(1, #e.hist - 7), #e.hist do
		local x = e.hist[i]
		self:Print(string.format("  %s – %s", LZ.Ago(x.t), LZ.FormatMoney(x.p)))
	end
end, { "preis [Itemlink|ID]", "AH-Preisverlauf eines Items", "AH price history of an item" })
