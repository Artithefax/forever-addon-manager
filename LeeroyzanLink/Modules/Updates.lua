-- Updates: shows what Leeroyzan knows - pending updates, "what's new" after an
-- update, known issues - and reports problems back ("problem" command).
local ADDON, LZ = ...
local T, P, C = LZ.T, LZ.P, LZ.C

local mod = LZ:NewModule("Updates", {
	group = "link",
	title = { "Updates", "Updates" },
	desc = {
		"Zeigt beim Login offene Updates aus Leeroyzan, den Changelog nach Updates und bekannte Probleme. Mit /lz problem <Addon> lässt sich ein Addon melden und abschalten.",
		"Shows pending updates from Leeroyzan on login, the changelog after updates and known issues. /lz problem <addon> reports and disables an addon.",
	},
	defaults = { loginNotice = true },
	options = { { key = "loginNotice", label = { "Hinweis beim Login anzeigen", "Show a notice on login" } } },
})

local STALE_AFTER = 7 * 86400

function mod:OnInit()
	LZ.gdb.seenNews = LZ.gdb.seenNews or {}
	LZ.gdb.reports = LZ.gdb.reports or {}
end

function mod:UnseenNews()
	local out = {}
	if LZ.import then
		for _, n in ipairs(LZ.import.whatsnew) do
			if not LZ.gdb.seenNews[n.id] then out[#out + 1] = n end
		end
	end
	return out
end

mod:Event("PLAYER_ENTERING_WORLD", function(self, _, isLogin)
	if not isLogin or not self.opt.loginNotice then return end
	local imp = LZ.import
	if not imp then
		if LZ.importProblem == "schema" then
			self:Print(T("Leeroyzan-Daten haben ein anderes Format – bitte Addon und Manager aktualisieren.", "Leeroyzan data uses a different format – please update the addon and the manager."))
		end
		return
	end
	local parts = {}
	if #imp.updates > 0 then
		parts[#parts + 1] = string.format(T("%d Updates verfügbar", "%d updates available"), #imp.updates)
	end
	local news = #self:UnseenNews()
	if news > 0 then parts[#parts + 1] = string.format(T("%d neue Changelogs (/lz neu)", "%d new changelogs (/lz news)"), news) end
	local age = LZ.Bridge:Age()
	if age and age > STALE_AFTER and #imp.updates > 0 then
		parts[#parts + 1] = T("Daten sind älter als eine Woche – Leeroyzan starten", "data is over a week old – start Leeroyzan")
	end
	if #parts > 0 then
		self:Print(table.concat(parts, " · "))
		if #imp.updates > 0 then self:Print(T("Installieren im nächsten Start von Leeroyzan.", "Install them the next time you start Leeroyzan.")) end
	end
end)

function mod:Summary()
	local imp = LZ.import
	if not imp then return T("keine Daten von Leeroyzan", "no data from Leeroyzan") end
	return string.format(T("%d Updates, Stand %s", "%d updates, as of %s"), #imp.updates, LZ.Ago(imp.generated))
end

function mod:TooltipLines()
	local imp = LZ.import
	if not imp then
		return { LZ.Color("a0a0a0", T("Leeroyzan: noch keine Daten", "Leeroyzan: no data yet")) }
	end
	local managed = #imp.managed
	local line = string.format(T("Leeroyzan: Sync %s · %d Updates", "Leeroyzan: sync %s · %d updates"), LZ.Ago(imp.generated), #imp.updates)
	if managed > 0 then line = line .. string.format(T(" · %d verwaltet", " · %d managed"), managed) end
	return { line }
end

function mod:Report()
	local imp = LZ.import
	local lines = {}
	if not imp then
		lines[#lines + 1] = T("Noch keine Daten von Leeroyzan. Starte den Manager und synchronisiere – danach /reload.", "No data from Leeroyzan yet. Start the manager and sync, then /reload.")
		return table.concat(lines, "\n")
	end
	lines[#lines + 1] = string.format("%s %s (%s)", T("Stand der Leeroyzan-Daten:", "Leeroyzan data as of:"), LZ.Ago(imp.generated),
		imp.managerVersion and ("Manager " .. imp.managerVersion) or "")
	lines[#lines + 1] = ""
	lines[#lines + 1] = LZ.Color("ffd100", T("Verfügbare Updates", "Available updates"))
	if #imp.updates == 0 then lines[#lines + 1] = T("Alles aktuell.", "All up to date.") end
	for _, u in ipairs(imp.updates) do
		lines[#lines + 1] = string.format("%s  %s -> %s%s", u.name, u.installed or "?", u.available or "?", u.note and ("  – " .. u.note) or "")
	end

	lines[#lines + 1] = ""
	lines[#lines + 1] = LZ.Color("ffd100", T("Neu in den letzten Updates", "New in recent updates"))
	if #imp.whatsnew == 0 then lines[#lines + 1] = T("Nichts.", "Nothing.") end
	for _, n in ipairs(imp.whatsnew) do
		local fresh = LZ.gdb.seenNews[n.id] and "" or LZ.Color("40ff40", " NEU")
		lines[#lines + 1] = string.format("%s %s%s", n.addon or "?", n.version or "", fresh)
		for _, l in ipairs(n.lines) do lines[#lines + 1] = "  • " .. l end
		LZ.gdb.seenNews[n.id] = time()
	end

	if #imp.knownIssues > 0 then
		lines[#lines + 1] = ""
		lines[#lines + 1] = LZ.Color("ffd100", T("Bekannte Probleme", "Known issues"))
		for _, k in ipairs(imp.knownIssues) do lines[#lines + 1] = string.format("%s: %s", k.addon, k.text or "") end
	end

	local reports = {}
	for name, r in pairs(LZ.gdb.reports) do reports[#reports + 1] = { name = name, r = r } end
	if #reports > 0 then
		lines[#lines + 1] = ""
		lines[#lines + 1] = LZ.Color("ffd100", T("Gemeldete Probleme (Leeroyzan schlägt Rollback vor)", "Reported problems (Leeroyzan will offer a rollback)"))
		for _, e in ipairs(reports) do lines[#lines + 1] = string.format("%s v%s – %s", e.name, e.r.version or "?", LZ.Ago(e.r.time)) end
	end
	return table.concat(lines, "\n")
end

mod:Command({ "neu", "news", "updates" }, function(self) LZ.UI:Show(self.name) end,
	{ "neu", "Updates und Changelogs aus Leeroyzan", "updates and changelogs from Leeroyzan" })

mod:Command({ "problem", "report" }, function(self, rest)
	local name = LZ.Trim(rest)
	if name == "" then self:Print(T("Benutzung: /lz problem <Addon-Ordnername>", "Usage: /lz problem <addon folder name>")) return end
	if name == ADDON or name:find("^Blizzard_") then self:Print(T("Dieses Addon kann nicht abgeschaltet werden.", "That addon cannot be disabled.")) return end
	local found
	for i = 1, C.GetNumAddOns() do
		local info = C.GetAddOnInfo(i)
		if info and info.name and info.name:lower() == name:lower() then found = info.name break end
	end
	if not found then self:Print(T("Addon nicht gefunden: ", "Addon not found: ") .. name) return end
	LZ.gdb.reports[found] = { time = time(), version = C.GetMetadata(found, "Version") }
	C.SetAddOnEnabled(found, false)
	self:Print(string.format(T("%s gemeldet und abgeschaltet. Leeroyzan schlägt beim nächsten Start ein Rollback vor. /lz reload zum Übernehmen.",
		"%s reported and disabled. Leeroyzan will offer a rollback next time it starts. /lz reload to apply."), found))
end, { "problem <Addon>", "Addon als fehlerhaft melden und abschalten", "report an addon as broken and disable it" })
