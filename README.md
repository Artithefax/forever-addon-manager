# Leeroyzan

A lightweight, **portable** addon manager for **World of Warcraft** – Forever, Classic and Retail – written in Python with a Tkinter GUI for Windows. No installation: unzip, start, done. Because updating addons should take one click, not a raid plan.

> Hobby project – non-commercial, no ads, no telemetry. Not affiliated with Blizzard Entertainment or CurseForge.

## Download

Get **`Leeroyzan-Portable.zip`** from the [Releases page](https://github.com/Artithefax/leeroyzan/releases/latest), unzip it anywhere – a folder in *Documents*, a USB stick – and start `Leeroyzan.exe`. **No installation, no Python, no admin rights.**

- Everything Leeroyzan stores (settings, CurseForge key, sets, logs, backups) lives in the **`Leeroyzan-Data`** folder next to the `.exe`. Copy the whole folder and you have taken everything with you.
- **Don't pass on your own `Leeroyzan-Data` folder** – it contains your CurseForge API key. To share Leeroyzan, share the release ZIP; to share addons, use a download list or a package.
- Coming from version 1.1 or older? On first start, your settings (and backups up to 500 MB) are copied over from `%APPDATA%\Leeroyzan` – the old folder is left untouched.
- The bare `Leeroyzan.exe` on the release page is used by the auto-update; started on its own it creates `Leeroyzan-Data` next to itself.
- Leeroyzan updates itself from then on. Windows SmartScreen may warn the first time because the file is not code-signed – click *More info → Run anyway*.
- If the folder of the `.exe` is not writable (e.g. *Program Files*), Leeroyzan falls back to `%APPDATA%\Leeroyzan` – better put it somewhere writable.

## Languages

Leeroyzan speaks **Deutsch and English**. On first start it uses your Windows display language (German for a German Windows, English for everything else). Switch any time under **Settings → General → Language** – the window changes immediately, no restart needed.

## The interface

One window, no pop-ups. A sidebar with the game version, the pages and a big **Play** button; questions, confirmations and messages appear as a bar at the top of the window instead of separate dialogs.

| Page | What you do there |
|---|---|
| **My addons** | All installed addons as a list or as tiles, with search, grouping and a detail panel (update, undo an update, hold, open source, link with CurseForge, open folder, remove). Addons with an update are yellow with a ⬇ marker. |
| **Discover** | Install directly from a CurseForge link, a GitHub repository or a ZIP from your download folder – and search CurseForge (WoW addons only) with file choice. |
| **Updates** | Check and update all or single addons, read the changes, see which blocked downloads Leeroyzan is waiting for, link manual addons, and keep Leeroyzan itself up to date. |
| **Leeroylink** | Every play session recorded by the companion addon **Leeroylink** – a summary per session, totals, charts, forecast, records, death statistics, farming spots and dungeon history; optionally posted to Discord (see below). |
| **Characters** | All your characters with level, gold, professions, bags and bank; *Who has what?* search and a shopping list for any stored recipe across all characters. |
| **Quests** | Search the quest database of QuestieDB (read straight from its addon folder): quest chains, quest givers with coordinates, objectives, requirements, XP and reputation – plus the item rewards your characters have seen. |
| **Auction house** | Prices from Auctionator's database (scan the auction house in the game): current minimum price, 14-day average, change, quantity and a price chart per item; filter by name, category, quality and level, find bargains, items below vendor price and what your characters own (with its worth). |
| **Wowhead** | Search Wowhead while you type, preview results (tooltip), open them in the browser and keep favorites. |
| **Diagnostics** | Lua errors per addon and version (with one-click rollback), load problems and missing dependencies, memory ranking, wishlist from the game. |
| **Share** | Create a download-list code for Discord/SMS or a `.lzpack` package – and paste a code you received to install it. |
| **Sets & backups** | Addon sets (switch groups of addons on/off) and WTF backups with restore. |
| **Settings** | WoW folder, CurseForge API key, download folder, language, update options, log and data folder. |

## Features

- Detects the WoW installation and game versions (`_forever_`, `_classic_beta_`, `_retail_`, …)
- Lists installed addons from their `.toc` files (title, version, interface, dependencies)
- Checks each addon's interface version against the installed game build (`.build.info`)
- Installs addons from ZIP files or GitHub releases, with a file picker when a release ships several variants (e.g. a `-forever` build)
- Update checks (optionally on start) with release notes, update all or single addons
- **Undo an update:** before an addon is updated, its current version is kept. If the new one breaks something, *↶ Back to …* in the detail panel (or right-click) restores it with one click – the addon is then held so it is not updated again right away
- **Hold addons:** a held addon gets no updates (e.g. when a new version does not work on Forever) until you allow them again; it stays visible as *held* with the newer version shown
- **Required addons are installed automatically:** when a CurseForge addon needs other addons (required dependencies according to CurseForge), Leeroyzan installs the missing ones as well – also their own requirements; optional ones are left out
- Addon sets: save groups of addons and switch between them with one click
- Enable/disable addons, warn when WoW is running
- WTF backups (manual and automatic before updates) with restore
- Share addon selections with friends as `.lzpack` packages (saved straight to your download folder)
- Share **download lists** (`.lzlist` file or a plain-text code that fits into Discord/SMS messages)
- Drag & drop for ZIP, `.lzpack` and `.lzlist` files (optional, via `tkinterdnd2`)
- Log file of all changes
- Remembers window size and position; keyboard shortcuts: **Ctrl+F** search, **F5** rescan (on *Updates*: check for updates), **Del** remove the selected addons (with confirmation), **Ctrl+1 … Ctrl+9** switch pages, **Ctrl+,** settings
- Self-update from GitHub releases

## Play sessions (Leeroylink)

Leeroyzan comes with its own companion addon, **Leeroylink** (folder `Leeroylink` in this repository, also attached to every release as `Leeroylink.zip`). Leeroyzan installs or updates it with one click on the **Leeroylink** page. Up to version 0.3.2 the addon was called *Leeroyzan Link* (folder `LeeroyzanLink`); Leeroyzan 1.5 switches it over with one click and keeps its data. In the game it records every session; after you log out, Leeroyzan imports it, keeps it and writes a summary:

- **Time and date:** start and end, play time, time in combat, AFK, resting, in instances, on flight paths, dead
- **Experience and gold:** XP split into kills, quests and exploring, level-ups; gold start → end with income and expenses by source (loot, quests, vendor, mail, auction house, trainer, flight master, trade)
- **Quests:** completed (with XP and money), accepted, abandoned
- **Loot:** every item with quality and vendor value, notable loot (green and better), quest rewards
- **Combat:** damage (by ability, pet included), healing and overhealing, damage and healing taken, biggest hits, melee swings (hits, crits, misses, dodges, parries, blocks), spells cast, buffs and debuffs received, interrupts, dispels, deaths with place and killer
- **Travel:** distance on foot (with an estimated step count), mounted, swimming, by flight path, jumps
- **Professions:** skill-ups (before → after), crafted items, gathered herbs/ore/leather/fish, new recipes
- **Exploration:** discovered areas (with XP), time per zone and visited subzones
- **Dungeons and raids:** every instance with duration, kills, bosses, wipes, deaths, XP
- **Enemies:** kills split into normal, elite, rare and bosses, killing blows, most killed enemies

**Back into the game:** Leeroyzan writes `Data.lua` into the addon folder: pending updates, the changelog of addons it just updated (shown once after login), your addon sets as in-game profiles (`/lz profil laden Raid`), held addons and installed wishlist entries. `/lz neu` shows it in the game.

**Diagnostics:** the addon counts Lua errors per addon *and* version. If an addon has errors since its last update and Leeroyzan kept the previous version, it offers the rollback right away. `/lz problem <addon>` in the game reports and disables an addon – Leeroyzan then offers the rollback too.

**Quest companion:** Leeroylink shows a small window of its own at quest givers and when you pick a quest in the quest log: which quest chain it belongs to, what the next quests ask for, where they start and what they give, and the sum until the end of the chain. It works with any quest UI (also DialogueUI) and does not need Questie or QuestieDB to be loaded: Leeroyzan writes the quest data from the installed QuestieDB into the small load-on-demand addon `Leeroylink_Quests` next to Leeroylink – the game loads it only when the companion is used for the first time. Rewards (items, choices, gold, spells) come from three sources, best first: what your characters have seen at quest givers or in the quest log; the open game database of the [cMaNGOS](https://github.com/cmangos/classic-db) project (Classic quests – Leeroyzan downloads it once from GitHub, about 13 MB, and keeps only the rewards in `Leeroyzan-Data\questdata`); and QuestieDB itself, which knows the reward items of WoW Forever's own quests. `/lz quest <name>` looks up any quest; `/lz quest off` (or the switch on Leeroyzan's *Quests* page) turns the companion off – then Leeroyzan leaves the quest data out and it takes no memory in the game.

**Characters and recipes:** opening a profession window stores its recipes with reagents (Leeroylink 0.3). Leeroyzan then tells you, for any recipe and quantity, what you have, which alt has the rest and what is missing.

How it works: WoW addons cannot open network connections or write files, so the addon keeps its data in its *SavedVariables* (`WTF\Account\<account>\SavedVariables\Leeroylink.lua`, the newest 30 sessions), which WoW writes on logout and on `/reload`. Leeroyzan watches that file, **only reads it** and stores each session as a JSON file in `Leeroyzan-Data\sessions\<game version>`. A `/reload` or logging back in with the same character within 15 minutes continues the same session. If Leeroyzan is open while you play, the summary appears as soon as the game is closed; otherwise on the next start. In the game, `/lz session` shows the current session.

## CurseForge integration

Addons can be found and installed from CurseForge right inside the manager (**Discover** page): a search that only covers World of Warcraft addons, sorted by popularity, downloads, last update or name, with a filter for your game version (Classic, TBC, WotLK, Cata, MoP or Retail), paging, and multi-select install. Pasting a project link or ID still works. Installed addons are included in the normal update check. It uses the official CurseForge API and is designed to respect addon authors:

- **No redistribution.** CurseForge files are never rehosted, mirrored or bundled. Every download is fetched directly from CurseForge by the user's own client.
- **Shared packages contain references only.** For CurseForge projects, a `.lzpack` package stores the project and file ID, never the files themselves. The recipient's client downloads the file from CurseForge or opens the project page.
- **`allowModDistribution` is respected.** If an author disables third-party distribution, the tool only links to the CurseForge project page.
- **Attribution.** The project name, author and a link back to the CurseForge project page (where the author's donation options live) are shown in the details and after each install.
- **Own API key per user.** Get a free key at <https://console.curseforge.com/> and paste it into the settings. It is stored only in the user's local config (`Leeroyzan-Data\config.json`), never embedded in the source code or the built executable, and only ever sent to `api.curseforge.com`.
- **Verified downloads.** Files are only fetched from CurseForge's CDN over HTTPS and checked against the SHA-1 hash published by the API.
- **Blocked downloads stay blocked.** If an author disables third-party downloads, the manager does not work around it: it opens the normal CurseForge download page in your browser, you click download, and the manager installs the file from your download folder once it appears.
- Addons with an available update are highlighted in **yellow with a ⬇ marker** in every view.
- **Direct download link for updates.** When a CurseForge update is available, the manager offers a shortcut to that file's download page on CurseForge: right-click an addon, double-click its *Update* column, the button in the detail panel, or *Get in browser* on the Updates page. Once the ZIP lands in your download folder, the manager installs it automatically – also when you download an available update from the CurseForge website yourself. The file is matched by name and verified against the SHA-1 hash published by CurseForge before it is installed.
- **Manually installed addons** can be linked to their CurseForge project (automatically via the `X-Curse-Project-ID` in the `.toc`, or by pasting the project link). Afterwards they are included in update checks like any other addon.
- **Minimal traffic.** Update checks run on app start or on manual request only (one request per installed CurseForge addon).

## Download lists

A download list is the lightweight way to share many addons: it contains **only references** (CurseForge project/file IDs and GitHub `owner/repo` names) – no addon files, no paths, no personal data.

- **Share:** on the **Share** page choose *selected* or *all* addons (or click *Share …* under the addon list). You get a text code that can be split into messages of a chosen size – *Discord/chat* (1800 characters), *Short message* (500) or *SMS* (150) – or saved as a small `.lzlist` file. A *readable list* with links can be copied as well. Optionally the exact CurseForge file versions and your addon sets are included.
- **Import:** paste the code into *Receive* on the **Share** page – it accepts (all parts, in any order; surrounding chat text and code-block backticks are ignored) or a `.lzlist` file, also via drag & drop. The manager shows what will be installed, skips addons you already have, and downloads the rest with *your own* CurseForge key. Downloads blocked by the author use the same browser-download flow as everywhere else.
- **Safe by design:** the code is `LZ1:<part>/<total>:<data>` (compressed JSON, Base32 so it survives chat apps). Imported lists are strictly validated; only known CurseForge IDs and valid GitHub repo names are accepted, and the size is capped.
- Addons without a download source (installed manually and not linked to CurseForge) cannot be listed – the Share page tells you which ones; use *Link with CurseForge* or a `.lzpack` package for those.

## Tests

```bat
pip install -r requirements-dev.txt
python -m unittest discover -s tests
ruff check .
```

- `tests/test_sessions.py` drives Leeroylink through a small imitation of the WoW API (a scripted play session with a `/reload` in the middle, the quest companion with real quest data) and checks the import, the summaries, QuestieDB, rewards, the auction house and the self-update.
- `tests/test_files.py` covers installing, undo, interrupted updates, parked sources, settings, encryption and signatures.
- `tests/test_gui.py` opens the window in every layout and language and shows every page (on Linux: `xvfb-run -a python -m unittest tests.test_gui`).
- `tests/test_i18n.py`, `tests/test_static.py` and `tests/test_lua_globals.py` check translations, typical mistakes in the source and stray Lua globals (needs `luac`).

The same runs on GitHub for every push and pull request and before every release build.

## Requirements

- Windows 10/11 – nothing else. Python is only needed if you want to run the source code directly.

## Running from source

```bat
python leeroyzan.py
```

From source, data goes to `%APPDATA%\Leeroyzan` – unless a `Leeroyzan-Data` folder exists next to `leeroyzan.py` (it is git-ignored).

The code lives in the package `leeroy/`: the logic in `leeroy/*.py` (WoW folders, installing, GitHub, CurseForge, Leeroylink, QuestieDB, Auctionator) and the window in `leeroy/ui/*.py` (one module per page, put together in `leeroy/ui/app.py`). `leeroyzan.py` only starts it; `leeroy/api.py` offers all functions in one place for tests and scripts.

## Building the executable

Every GitHub release builds `Leeroyzan.exe` and `Leeroyzan-Portable.zip` automatically (see below), so you normally never build by hand.

To build the portable version from the source, double-click **`setup.bat`**. It asks for the language of its messages, then:

1. builds `dist\Leeroyzan.exe` in an isolated environment with the pinned versions from `requirements-build.txt` and the build plan `leeroyzan.spec` (PyInstaller, `tkinterdnd2` for drag & drop),
2. updates the ready-to-use folder `dist\Leeroyzan-Portable\` (the `.exe` plus `Leeroyzan-Data`; data you already have in there is kept),
3. creates `dist\Leeroyzan-Portable.zip` for sharing – always from a clean folder, so your own settings and API key never end up in it.

Nothing is installed. **Python does not have to be installed either:** if none is found, the script downloads the standalone tool [uv](https://github.com/astral-sh/uv) into `.build_tools`, which fetches a portable Python into its cache – nothing is installed system-wide. With Python 3.10+ present, that one is used.

## Releases and auto-update

The manager checks this repository's latest GitHub release on start. With *Leeroyzan updates: Install automatically* (default) it updates itself without any download by hand: it downloads `Leeroyzan.exe`, verifies it against `Leeroyzan.exe.sha256`, replaces itself and restarts. If a release was just published and the `.exe` is still being built, the manager waits in the background (checking every minute for up to 30 minutes) and installs it as soon as it is ready. Releases without a `Leeroyzan.exe` (e.g. a failed build) are skipped, and if a downloaded update could not be put in place, the manager says so on the next start (details in `update.log`). When run as a Python script, it replaces `leeroyzan.py`, `leeroy/` and `Leeroylink/` with the release's source instead (keeping `.bak` copies); inside a git checkout it leaves the files alone – use `git pull` there. Once releases are code-signed, the updater only accepts a new `.exe` signed by the same publisher.

Publishing a new version:

1. Set `APP_VERSION` in `leeroy/constants.py` (e.g. `2.1.0`), add a `## 2.1.0` section to `CHANGELOG.md` and commit.
2. Actions → *Build release exe* → *Run workflow*, enter `2.1.0`.
3. The workflow runs all tests, creates the release as a draft (text from `CHANGELOG.md`), builds the `.exe` on a Windows runner (with the `Leeroylink` addon inside), runs the finished `.exe` once in self-test mode, attaches it plus its SHA-256 checksum, `Leeroyzan-Portable.zip` and `Leeroylink.zip`, and only then publishes the release – nobody ever sees a release without its `.exe`.

Publishing a release by hand still works: the build then starts after publishing, and an empty release text is filled in from `CHANGELOG.md`.

**Code signing (optional):** the workflow signs the `.exe` through the [SignPath Foundation](https://signpath.org/) (free for open source) as soon as the repository secret `SIGNPATH_API_TOKEN` and the variable `SIGNPATH_ORGANIZATION_ID` are set (project slug `leeroyzan`, signing policy `release-signing`). Signed releases no longer trigger the SmartScreen warning.

## Where data is stored

Configuration, backups, kept previous addon versions (for *undo*), imported play sessions, caches and the log file are stored in the `Leeroyzan-Data` folder next to `Leeroyzan.exe` (*Settings → Help → Open data folder*). On Windows, the CurseForge API key, the GitHub token and the Discord webhook are stored encrypted for your Windows account; on another PC Leeroyzan asks for them again. While installing or updating, Leeroyzan briefly uses a work folder `Interface\.leeroyzan-…` next to `AddOns` – that makes swapping folders instant and safe, and it disappears right after. The manager only reads and writes files inside the WoW `Interface\AddOns`, `Interface\AddOns_Disabled` and `WTF` folders – it never modifies the game client.

## License

MIT – see [LICENSE](LICENSE).
