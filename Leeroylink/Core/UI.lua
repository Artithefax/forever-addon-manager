-- UI.lua: main window (left navigation + scrollable text page + action buttons),
-- settings page with module switches, and the minimap button.
local ADDON, LZ = ...
local T, P = LZ.T, LZ.P

local UI = {}
LZ.UI = UI

local TEMPLATE = _G.BackdropTemplateMixin and "BackdropTemplate" or nil
local BACKDROP = {
	bgFile = "Interface\\Tooltips\\UI-Tooltip-Background",
	edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
	tile = true, tileSize = 16, edgeSize = 16,
	insets = { left = 4, right = 4, top = 4, bottom = 4 },
}

local NAV_W, CONTENT_W = 150, 420
local pages, navButtons, actionButtons, settingsRows = {}, {}, {}, {}

local function makeButton(parent, w, h, onClick)
	local b = CreateFrame("Button", nil, parent, "UIPanelButtonTemplate")
	b:SetSize(w, h)
	b:SetScript("OnClick", onClick)
	return b
end

local function buildPages()
	pages = { { key = "overview", label = function() return T("Übersicht", "Overview") end } }
	for _, mod in ipairs(LZ.moduleList) do
		if mod.Report then
			pages[#pages + 1] = { key = mod.name, mod = mod, label = function() return P(mod.title) end }
		end
	end
	pages[#pages + 1] = { key = "settings", label = function() return T("Einstellungen", "Settings") end }
end

----------------------------------------------------------------------
-- Settings page
----------------------------------------------------------------------
local function buildSettings(parent)
	local f = CreateFrame("Frame", nil, parent)
	f:SetPoint("TOPLEFT", parent, "TOPLEFT", 0, 0)
	f:SetSize(CONTENT_W, 600)
	local y = -2

	local function header(textFn)
		local fs = f:CreateFontString(nil, "ARTWORK", "GameFontNormal")
		fs:SetPoint("TOPLEFT", 0, y)
		settingsRows[#settingsRows + 1] = { update = function() fs:SetText(textFn()) end }
		y = y - 22
	end

	local function checkbox(x, labelFn, getFn, setFn)
		local cb = CreateFrame("CheckButton", nil, f, "UICheckButtonTemplate")
		cb:SetPoint("TOPLEFT", x, y + 4)
		cb:SetSize(24, 24)
		local fs = f:CreateFontString(nil, "ARTWORK", "GameFontHighlight")
		fs:SetPoint("LEFT", cb, "RIGHT", 2, 0)
		fs:SetWidth(CONTENT_W - x - 34)
		fs:SetJustifyH("LEFT")
		cb:SetScript("OnClick", function(self) setFn(self:GetChecked() and true or false) end)
		settingsRows[#settingsRows + 1] = {
			update = function() fs:SetText(labelFn()); cb:SetChecked(getFn()) end,
		}
		y = y - 24
	end

	-- language
	header(function() return T("Sprache", "Language") end)
	local de = makeButton(f, 80, 22, function() LZ.coreCommands["sprache"]("de") end)
	de:SetPoint("TOPLEFT", 0, y + 2)
	de:SetText("Deutsch")
	local en = makeButton(f, 80, 22, function() LZ.coreCommands["sprache"]("en") end)
	en:SetPoint("LEFT", de, "RIGHT", 6, 0)
	en:SetText("English")
	y = y - 30

	checkbox(0, function() return T("Minimap-Symbol anzeigen", "Show minimap button") end,
		function() return not LZ.settings.minimap.hide end,
		function(v) LZ.settings.minimap.hide = not v; UI:UpdateMinimap() end)
	y = y - 6

	local groups = {
		{ "link", function() return T("Verbindung zu Leeroyzan", "Leeroyzan link") end },
		{ "extra", function() return T("Zusatzfunktionen", "Extras") end },
	}
	for _, g in ipairs(groups) do
		header(g[2])
		for _, mod in ipairs(LZ.moduleList) do
			if mod.group == g[1] then
				checkbox(0, function() return P(mod.title) end,
					function() return mod.enabled end,
					function(v) LZ:SetModuleEnabled(mod, v) end)
				for _, o in ipairs(mod.options or {}) do
					checkbox(24, function() return P(o.label) end,
						function() return mod.opt[o.key] and true or false end,
						function(v) mod.opt[o.key] = v end)
				end
			end
		end
		y = y - 6
	end
	f:SetHeight(-y + 10)
	return f
end

----------------------------------------------------------------------
-- Main window
----------------------------------------------------------------------
function UI:Build()
	if self.frame then return end
	buildPages()

	local f = CreateFrame("Frame", "LeeroylinkFrame", UIParent, TEMPLATE)
	f:SetSize(660, 520)
	f:SetPoint("CENTER")
	f:SetFrameStrata("DIALOG")
	f:SetMovable(true)
	f:EnableMouse(true)
	f:RegisterForDrag("LeftButton")
	f:SetScript("OnDragStart", function(s) s:StartMoving() end)
	f:SetScript("OnDragStop", function(s) s:StopMovingOrSizing() end)
	if f.SetBackdrop then
		f:SetBackdrop(BACKDROP)
		f:SetBackdropColor(0, 0, 0, 0.92)
	end
	f:Hide()
	if _G.UISpecialFrames then table.insert(UISpecialFrames, "LeeroylinkFrame") end
	self.frame = f

	local title = f:CreateFontString(nil, "ARTWORK", "GameFontNormalLarge")
	title:SetPoint("TOPLEFT", 16, -14)
	title:SetText("Leeroylink  v" .. LZ.version)
	self.title = title

	local close = CreateFrame("Button", nil, f, "UIPanelCloseButton")
	close:SetPoint("TOPRIGHT", -4, -4)

	-- navigation
	local y = -44
	for i, page in ipairs(pages) do
		local b = makeButton(f, NAV_W, 22, function() UI:Select(page.key) end)
		b:SetPoint("TOPLEFT", 14, y)
		navButtons[i] = b
		y = y - 24
	end

	-- scrollable page
	local scroll = CreateFrame("ScrollFrame", "LeeroylinkScroll", f, "UIPanelScrollFrameTemplate")
	scroll:SetPoint("TOPLEFT", NAV_W + 28, -44)
	scroll:SetPoint("BOTTOMRIGHT", -34, 48)
	local content = CreateFrame("Frame", nil, scroll)
	content:SetSize(CONTENT_W, 10)
	scroll:SetScrollChild(content)
	self.scroll, self.content = scroll, content

	local text = content:CreateFontString(nil, "ARTWORK", "GameFontHighlight")
	text:SetPoint("TOPLEFT", 0, 0)
	text:SetWidth(CONTENT_W)
	text:SetJustifyH("LEFT")
	text:SetJustifyV("TOP")
	self.text = text

	self.settings = buildSettings(content)
	self.settings:Hide()

	for i = 1, 3 do
		local b = makeButton(f, 140, 22, function() end)
		b:SetPoint("BOTTOMLEFT", NAV_W + 28 + (i - 1) * 146, 16)
		b:Hide()
		actionButtons[i] = b
	end

	f:SetScript("OnShow", function() UI:Refresh() end)
end

function UI:Rebuild()
	if not self.frame then return end
	self:Refresh()
end

function UI:Select(key)
	self.page = key
	if self.scroll and self.scroll.SetVerticalScroll then self.scroll:SetVerticalScroll(0) end
	self:Refresh()
end

local function overviewText()
	local lines = {}
	lines[#lines + 1] = LZ.Color("ffd100", "Leeroylink v" .. LZ.version)
	local imp = LZ.import
	if imp then
		lines[#lines + 1] = string.format(T("Leeroyzan-Daten: Stand %s, %d Updates", "Leeroyzan data: as of %s, %d updates"), LZ.Ago(imp.generated), #imp.updates)
	else
		lines[#lines + 1] = T("Leeroyzan-Daten: noch keine (Manager starten, synchronisieren, /reload)", "Leeroyzan data: none yet (start the manager, sync, /reload)")
	end
	lines[#lines + 1] = ""
	for _, mod in ipairs(LZ.moduleList) do
		local summary = ""
		if mod.enabled and mod.Summary then
			local ok, s = pcall(mod.Summary, mod)
			summary = ok and tostring(s) or "?"
		elseif not mod.enabled then
			summary = LZ.Color("808080", T("aus", "off"))
		end
		lines[#lines + 1] = string.format("%s: %s", LZ.Color("ffffff", P(mod.title)), summary)
	end
	lines[#lines + 1] = ""
	lines[#lines + 1] = T("Hilfe: /lz hilfe", "Help: /lz help")
	return table.concat(lines, "\n")
end

function UI:Refresh()
	local f = self.frame
	if not f then return end
	if not self.page then self.page = "overview" end

	for i, page in ipairs(pages) do
		navButtons[i]:SetText(page.label())
		if page.key == self.page and navButtons[i].LockHighlight then navButtons[i]:LockHighlight()
		elseif navButtons[i].UnlockHighlight then navButtons[i]:UnlockHighlight() end
	end

	local acts = nil
	local textValue = ""
	if self.page == "settings" then
		for _, row in ipairs(settingsRows) do row.update() end
		self.text:SetText("")
		self.settings:Show()
		self.content:SetHeight(self.settings:GetHeight())
	else
		self.settings:Hide()
		local mod
		for _, page in ipairs(pages) do if page.key == self.page then mod = page.mod end end
		if not mod then
			textValue = overviewText()
		elseif not mod.enabled then
			textValue = LZ.Color("ff8080", T("Dieses Modul ist ausgeschaltet. Einschalten im Reiter Einstellungen.", "This module is off. Switch it on in the Settings tab."))
		else
			local ok, result = pcall(mod.Report, mod)
			textValue = ok and tostring(result) or ("|cffff4040Error:|r " .. tostring(result))
			if mod.desc then textValue = LZ.Color("a0a0a0", P(mod.desc)) .. "\n\n" .. textValue end
			acts = mod.actions
		end
		self.text:SetText(textValue)
		self.content:SetHeight(math.max(20, (self.text:GetStringHeight() or 0) + 12))
	end

	for i, b in ipairs(actionButtons) do
		local a = acts and acts[i]
		if a then
			b:SetText(P(a.label))
			b:SetScript("OnClick", function() local mod = LZ.modules[self.page]; a.fn(mod) end)
			b:Show()
		else
			b:Hide()
		end
	end
end

function UI:Show(key)
	self:Build()
	if key then self.page = key end
	self.frame:Show()
	self:Refresh()
end

function UI:Toggle()
	self:Build()
	if self.frame:IsShown() then self.frame:Hide() else self:Show() end
end

----------------------------------------------------------------------
-- Minimap button
----------------------------------------------------------------------
local button

local function placeButton()
	if not button then return end
	local angle = LZ.settings.minimap.angle or 3.9
	local r = (Minimap:GetWidth() / 2) + 5
	button:ClearAllPoints()
	button:SetPoint("CENTER", Minimap, "CENTER", math.cos(angle) * r, math.sin(angle) * r)
end

function UI:BuildMinimap()
	if button or not _G.Minimap then return end
	button = CreateFrame("Button", "LeeroylinkMinimapButton", Minimap)
	button:SetSize(31, 31)
	button:SetFrameStrata("MEDIUM")
	button:SetFrameLevel(8)
	button:RegisterForClicks("LeftButtonUp", "RightButtonUp")
	button:RegisterForDrag("LeftButton")
	button:SetHighlightTexture("Interface\\Minimap\\UI-Minimap-ZoomButton-Highlight")

	local icon = button:CreateTexture(nil, "BACKGROUND")
	icon:SetSize(20, 20)
	icon:SetTexture("Interface\\Icons\\Ability_Warrior_Charge")
	icon:SetPoint("CENTER", 0, 1)
	local border = button:CreateTexture(nil, "OVERLAY")
	border:SetSize(53, 53)
	border:SetTexture("Interface\\Minimap\\MiniMap-TrackingBorder")
	border:SetPoint("TOPLEFT")

	button:SetScript("OnClick", function(_, btn)
		if btn == "RightButton" then
			UI:Show("settings")
		elseif IsShiftKeyDown and IsShiftKeyDown() then
			ReloadUI()
		else
			UI:Toggle()
		end
	end)

	button:SetScript("OnDragStart", function(self)
		self:SetScript("OnUpdate", function()
			local mx, my = Minimap:GetCenter()
			local px, py = GetCursorPosition()
			local scale = Minimap:GetEffectiveScale()
			LZ.settings.minimap.angle = math.atan2(py / scale - my, px / scale - mx)
			placeButton()
		end)
	end)
	button:SetScript("OnDragStop", function(self) self:SetScript("OnUpdate", nil) end)

	button:SetScript("OnEnter", function(self)
		GameTooltip:SetOwner(self, "ANCHOR_LEFT")
		GameTooltip:AddLine("Leeroylink", 1, 0.5, 0)
		for _, mod in ipairs(LZ.moduleList) do
			if mod.enabled and mod.TooltipLines then
				local ok, lines = pcall(mod.TooltipLines, mod)
				if ok and type(lines) == "table" then
					for _, l in ipairs(lines) do GameTooltip:AddLine(l, 1, 1, 1) end
				end
			end
		end
		GameTooltip:AddLine(" ")
		GameTooltip:AddLine(T("Linksklick: Fenster · Rechtsklick: Einstellungen", "Left-click: window · Right-click: settings"), 0.6, 0.6, 0.6)
		GameTooltip:AddLine(T("Shift-Klick: UI neu laden · Ziehen: verschieben", "Shift-click: reload UI · Drag: move"), 0.6, 0.6, 0.6)
		GameTooltip:Show()
	end)
	button:SetScript("OnLeave", function() GameTooltip:Hide() end)
end

function UI:UpdateMinimap()
	self:BuildMinimap()
	if not button then return end
	placeButton()
	if LZ.settings.minimap.hide then button:Hide() else button:Show() end
end

function UI:Init()
	self:UpdateMinimap()
end
