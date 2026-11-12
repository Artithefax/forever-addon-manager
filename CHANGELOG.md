# Changelog

The section of a version becomes the text of its GitHub release automatically
(`tools/release_notes.py`, used by the *Build release exe* workflow).

## 2.0.0

**The big performance and stability update.** Leeroyzan 2.0 comes with Leeroylink 0.7.0.

### Faster
- Refreshing the addon list takes a few milliseconds instead of a quarter second: `.toc` files are cached by size and date (also across restarts), and the huge data `.toc` of QuestieDB is read only after it changed.
- The window opens faster and stays responsive: the first addon scan, the quest database, the reward index and the auction house rows are prepared in the background.
- Update checks run six requests at once. CurseForge addons are checked with a single batch request first; only projects that changed since the last check need their own request. GitHub answers are cached with ETags.
- The game process list is read directly from Windows instead of starting `tasklist`.
- The `.exe` unpacks about 700 fewer files on every start (unused Tcl time zone and date data).

### Safer
- **Updates swap folders instead of deleting first.** The new version is unpacked next to the AddOns folder and swapped in; if anything fails (a locked file, a full disk), the old version stays. Leftovers of an interrupted update are restored on the next start.
- **Undo keeps the old version by moving it, not copying it** – updates of large addons no longer copy hundreds of megabytes. The settings show how much space undo versions take; versions of removed addons and those older than 60 days are cleaned up automatically.
- **Your CurseForge and GitHub links are no longer lost** when the WoW drive is briefly unavailable: links without a folder are parked and come back when the folder does.
- API key, GitHub token and Discord webhook are stored encrypted (Windows data protection, bound to your Windows account). On another PC Leeroyzan asks you to enter them again.
- Tokens and keys are never sent along when a download is redirected to another server.
- Prepared for code signing: once the release is signed, the updater only accepts a new version from the same publisher.

### Easier to use
- Checking for updates or searching no longer blocks other actions – you can start the game while Leeroyzan checks. Running background tasks are shown in the status bar.
- If WoW closes right after starting (Battle.net is updating or repairing the game – "container locked"), Leeroyzan explains why and offers to open Battle.net.
- The sidebar is grouped and adapts to small windows, so all pages and the Play button always fit.
- New shortcuts: F5 refreshes, Ctrl+F jumps to the search of the current page, Del removes the selected addons (after asking), Ctrl+1 … Ctrl+9 switch pages, Ctrl+, opens the settings.

### Leeroylink 0.7.0
- The quest data now lives in its own load-on-demand addon **Leeroylink_Quests** (written by Leeroyzan) and is loaded only when the quest companion is used for the first time.
- Target, nameplates and boss frames are only checked in and right after combat.
- On clients without a readable combat log, Leeroylink stops listening to it.

### Under the hood
- The code is split into the package `leeroy/` (logic) and `leeroy/ui/` (window); `leeroyzan.py` is just the starter.
- Every release is built only after all tests pass (logic, Leeroylink scenario, window smoke test, translations, code check), with pinned build tools, and the finished `.exe` runs a self-test before it is uploaded. "Run workflow" creates the release as a draft and publishes it only when everything is attached.
