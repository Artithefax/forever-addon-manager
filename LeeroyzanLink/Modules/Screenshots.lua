-- Screenshots: takes a screenshot on level-up, achievements and rare loot.
local ADDON, LZ = ...
local T, P, C = LZ.T, LZ.P, LZ.C

local mod = LZ:NewModule("Screenshots", {
	title = { "Screenshot-Automatik", "Auto screenshots" },
	desc = {
		"Macht automatisch einen Screenshot bei Stufenaufstieg, Erfolgen und seltener Beute.",
		"Automatically takes a screenshot on level-up, achievements and rare loot.",
	},
	defaults = { level = true, achievement = true, loot = true, lootQuality = 4 },
	options = {
		{ key = "level", label = { "Bei Stufenaufstieg", "On level-up" } },
		{ key = "achievement", label = { "Bei Erfolgen", "On achievements" } },
		{ key = "loot", label = { "Bei seltener Beute (Qualität ab Episch)", "On rare loot (epic and above)" } },
	},
})

local taken = 0
local lastShot = 0

function mod:Shoot(reason)
	if not self.enabled or not _G.Screenshot then return end
	local now = GetTime and GetTime() or 0
	if now - lastShot < 2 then return end -- avoid bursts
	lastShot = now
	-- wait a moment so level-up banners and loot windows are on screen
	LZ.After(1, function()
		if pcall(Screenshot) then taken = taken + 1 end
	end)
end

mod:Event("PLAYER_LEVEL_UP", function(self) if self.opt.level then self:Shoot("level") end end)
mod:Event("ACHIEVEMENT_EARNED", function(self) if self.opt.achievement then self:Shoot("achievement") end end)

LZ.Listen("RARE_LOOT", function(itemID, quality)
	if mod.enabled and mod.opt.loot and (quality or 0) >= (mod.opt.lootQuality or 4) then mod:Shoot("loot") end
end)

function mod:Summary()
	return string.format(T("%d Screenshots in dieser Sitzung", "%d screenshots this session"), taken)
end

function mod:Report()
	local lines = { LZ.Color("ffd100", T("Auslöser", "Triggers")) }
	for _, o in ipairs(self.options) do
		lines[#lines + 1] = string.format("[%s] %s", self.opt[o.key] and "x" or " ", P(o.label))
	end
	lines[#lines + 1] = ""
	lines[#lines + 1] = string.format(T("In dieser Sitzung aufgenommen: %d", "Taken this session: %d"), taken)
	lines[#lines + 1] = T("Einstellen im Reiter Einstellungen.", "Configure in the Settings tab.")
	return table.concat(lines, "\n")
end
