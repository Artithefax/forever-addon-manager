-- Compat.lua: thin wrappers so the rest of the addon does not care whether the
-- client offers the modern namespaced API (C_AddOns, C_Container, C_Spell, ...)
-- or the older global functions. Every call is defensive: a missing or changed
-- API returns nil instead of raising an error.
local ADDON, LZ = ...

local C = {}
LZ.C = C

local CA = _G.C_AddOns

local function firstFn(...)
	for i = 1, select("#", ...) do
		local f = select(i, ...)
		if type(f) == "function" then return f end
	end
end

----------------------------------------------------------------------
-- AddOn API
----------------------------------------------------------------------
local rawMeta = firstFn(CA and CA.GetAddOnMetadata, _G.GetAddOnMetadata)
local rawNum = firstFn(CA and CA.GetNumAddOns, _G.GetNumAddOns)
local rawLoaded = firstFn(CA and CA.IsAddOnLoaded, _G.IsAddOnLoaded)
local rawLoD = firstFn(CA and CA.IsAddOnLoadOnDemand, _G.IsAddOnLoadOnDemand)
local rawInfo = firstFn(CA and CA.GetAddOnInfo, _G.GetAddOnInfo)
local rawDeps = firstFn(CA and CA.GetAddOnDependencies, _G.GetAddOnDependencies)

function C.GetMetadata(name, field)
	if not rawMeta then return nil end
	local ok, v = pcall(rawMeta, name, field)
	if ok and type(v) == "string" and v ~= "" then return v end
end

function C.GetNumAddOns()
	if not rawNum then return 0 end
	local ok, n = pcall(rawNum)
	return (ok and tonumber(n)) or 0
end

function C.IsLoaded(name)
	if not rawLoaded then return false end
	local ok, loaded = pcall(rawLoaded, name)
	return (ok and loaded) and true or false
end

function C.IsLoadOnDemand(name)
	if not rawLoD then return false end
	local ok, v = pcall(rawLoD, name)
	return (ok and v) and true or false
end

-- Returns a table { name, title, notes, loadable, reason, security } for an index or name.
function C.GetAddOnInfo(indexOrName)
	if not rawInfo then return nil end
	local ok, a, b, c, d, e, f = pcall(rawInfo, indexOrName)
	if not ok or a == nil then return nil end
	if type(a) == "table" then
		return { name = a.name, title = a.title, notes = a.notes, loadable = a.loadable, reason = a.reason, security = a.security }
	end
	return { name = a, title = b, notes = c, loadable = d, reason = e, security = f }
end

function C.GetDeps(name)
	local deps = {}
	if not rawDeps then return deps end
	local res = { pcall(rawDeps, name) }
	if not res[1] then return deps end
	if type(res[2]) == "table" then
		for _, v in ipairs(res[2]) do
			if type(v) == "string" then deps[#deps + 1] = v end
		end
		return deps
	end
	for i = 2, #res do
		if type(res[i]) == "string" then deps[#deps + 1] = res[i] end
	end
	return deps
end

-- true / false, or nil when the client does not tell us.
function C.IsEnabled(name)
	local v
	local player = UnitName and UnitName("player")
	if CA and CA.GetAddOnEnableState then
		local ok, r = pcall(CA.GetAddOnEnableState, name, player)
		if ok then v = r end
	elseif _G.GetAddOnEnableState then
		local ok, r = pcall(_G.GetAddOnEnableState, player, name)
		if ok then v = r end
	end
	if v == nil then return nil end
	if type(v) == "boolean" then return v end
	return (tonumber(v) or 0) > 0
end

-- Enables/disables an addon for the current character. The enable/disable
-- signatures changed between client versions, so verify the result and fall
-- back to the other call shapes.
function C.SetAddOnEnabled(name, enabled)
	local fn
	if enabled then
		fn = firstFn(CA and CA.EnableAddOn, _G.EnableAddOn)
	else
		fn = firstFn(CA and CA.DisableAddOn, _G.DisableAddOn)
	end
	if not fn then return false end
	pcall(fn, name, UnitName and UnitName("player"))
	local state = C.IsEnabled(name)
	if state ~= nil and state ~= enabled and UnitGUID then
		pcall(fn, name, UnitGUID("player"))
		state = C.IsEnabled(name)
	end
	if state ~= nil and state ~= enabled then
		pcall(fn, name)
		state = C.IsEnabled(name)
	end
	return state == enabled
end

local rawUpdateMem = firstFn(CA and CA.UpdateAddOnMemoryUsage, _G.UpdateAddOnMemoryUsage)
local rawMem = firstFn(CA and CA.GetAddOnMemoryUsage, _G.GetAddOnMemoryUsage)

function C.UpdateMemory()
	if rawUpdateMem then pcall(rawUpdateMem) end
end

function C.GetMemory(name)
	if not rawMem then return nil end
	local ok, kb = pcall(rawMem, name)
	if ok and type(kb) == "number" then return kb end
end

-- Recent average CPU time in ms per frame via the addon profiler (if the client has it).
function C.GetCPU(name)
	local P = _G.C_AddOnProfiler
	if P and P.GetAddOnMetric and _G.Enum and Enum.AddOnProfilerMetric then
		local ok, v = pcall(P.GetAddOnMetric, name, Enum.AddOnProfilerMetric.RecentAverageTime)
		if ok and type(v) == "number" then return v end
	end
end

----------------------------------------------------------------------
-- Containers / items
----------------------------------------------------------------------
local CC = _G.C_Container

function C.NumSlots(bag)
	local f = firstFn(CC and CC.GetContainerNumSlots, _G.GetContainerNumSlots)
	if not f then return 0 end
	local ok, n = pcall(f, bag)
	return (ok and tonumber(n)) or 0
end

-- itemID, count, quality, link, noValue
function C.ItemAt(bag, slot)
	if CC and CC.GetContainerItemInfo then
		local ok, info = pcall(CC.GetContainerItemInfo, bag, slot)
		if not ok or type(info) ~= "table" then return nil end
		return info.itemID, info.stackCount or 1, info.quality, info.hyperlink, info.hasNoValue
	end
	if _G.GetContainerItemInfo then
		local ok, icon, count, _, quality, _, _, link, _, noValue, itemID = pcall(_G.GetContainerItemInfo, bag, slot)
		if not ok or not icon then return nil end
		return itemID, count or 1, quality, link, noValue
	end
end

function C.UseItem(bag, slot)
	local f = firstFn(CC and CC.UseContainerItem, _G.UseContainerItem)
	if f then f(bag, slot) end
end

function C.BagIDs()
	local n = _G.NUM_BAG_SLOTS or 4
	local ids = {}
	for b = 0, n do ids[#ids + 1] = b end
	return ids
end

function C.BankBagIDs()
	local ids = { _G.BANK_CONTAINER or -1 }
	local first = (_G.NUM_BAG_SLOTS or 4) + 1
	local last = (_G.NUM_BAG_SLOTS or 4) + (_G.NUM_BANKBAGSLOTS or 7)
	for b = first, last do ids[#ids + 1] = b end
	return ids
end

function C.GetItemName(id)
	if _G.C_Item and C_Item.GetItemNameByID then
		local ok, n = pcall(C_Item.GetItemNameByID, id)
		if ok and n then return n end
	end
	if _G.GetItemInfo then
		local ok, n = pcall(_G.GetItemInfo, id)
		if ok and n then return n end
	end
end

function C.GetSellPrice(linkOrId)
	local f = (_G.C_Item and C_Item.GetItemInfo) or _G.GetItemInfo
	if not f then return 0 end
	local ok, a, b, c, d, e, f6, g, h, i, j, sell = pcall(f, linkOrId)
	if not ok then return 0 end
	if type(a) == "table" then return a.sellPrice or 0 end
	return tonumber(sell) or 0
end

function C.GetQuality(linkOrId)
	if _G.C_Item and C_Item.GetItemQualityByID then
		local ok, q = pcall(C_Item.GetItemQualityByID, linkOrId)
		if ok and type(q) == "number" then return q end
	end
	local f = (_G.C_Item and C_Item.GetItemInfo) or _G.GetItemInfo
	if not f then return nil end
	local ok, a, _, q = pcall(f, linkOrId)
	if not ok then return nil end
	if type(a) == "table" then return a.itemQuality or a.quality end
	return tonumber(q)
end

----------------------------------------------------------------------
-- Spells
----------------------------------------------------------------------
function C.GetSpellCooldown(id)
	if _G.C_Spell and C_Spell.GetSpellCooldown then
		local ok, t = pcall(C_Spell.GetSpellCooldown, id)
		if ok and type(t) == "table" then return t.startTime, t.duration end
		return nil
	end
	if _G.GetSpellCooldown then
		local ok, s, d = pcall(_G.GetSpellCooldown, id)
		if ok then return s, d end
	end
end

function C.GetSpellName(id)
	if _G.C_Spell and C_Spell.GetSpellName then
		local ok, n = pcall(C_Spell.GetSpellName, id)
		if ok and n then return n end
	end
	if _G.GetSpellInfo then
		local ok, n = pcall(_G.GetSpellInfo, id)
		if ok and type(n) == "string" then return n end
	end
end

----------------------------------------------------------------------
-- Misc
----------------------------------------------------------------------
function C.InterfaceVersion()
	if type(GetBuildInfo) == "function" then
		local _, _, _, iface = GetBuildInfo()
		return iface
	end
end
