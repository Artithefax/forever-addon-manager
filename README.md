# Forever Addon Manager

A lightweight, personal addon manager for **World of Warcraft: Forever** (and other WoW versions), written in Python with a Tkinter GUI for Windows.

> Hobby project – non-commercial, no ads, no telemetry. Not affiliated with Blizzard Entertainment or CurseForge.

## Features

- Detects the WoW installation and game versions (`_forever_`, `_classic_beta_`, `_retail_`, …)
- Lists installed addons from their `.toc` files (title, version, interface, dependencies)
- Checks each addon's interface version against the installed game build (`.build.info`)
- Installs addons from ZIP files or GitHub releases, with a file picker when a release ships several variants (e.g. a `-forever` build)
- Update checks (optionally on start) with release notes shown before updating
- Addon sets: save groups of addons and switch between them with one click
- Enable/disable addons, warn when WoW is running
- WTF backups (manual and automatic before updates) with restore
- Share addon selections with friends as `.faddons` packages
- Drag & drop for ZIP and `.faddons` files (optional, via `tkinterdnd2`)
- Log file of all changes
- Self-update from GitHub releases

## CurseForge integration (planned)

CurseForge support is being added through the official CurseForge API and is designed to respect addon authors:

- **No redistribution.** CurseForge files are never rehosted, mirrored or bundled. Every download is fetched directly from CurseForge by the user's own client.
- **Shared packages contain references only.** For CurseForge projects, a `.faddons` package stores the project and file ID, never the files themselves. The recipient's client downloads the file from CurseForge or opens the project page.
- **`allowModDistribution` is respected.** If an author disables third-party distribution, the tool only links to the CurseForge project page.
- **Attribution.** The project name, author and a link back to the CurseForge page are always shown, plus the author's donation link where available.
- **Own API key per user.** The key is entered locally in the settings and stored only in the user's config. It is never embedded in the source code or the built executable.
- **Minimal traffic.** Update checks run on app start or on manual request only.

## Requirements

- Windows 10/11
- Python 3.10 or newer (only needed to run from source or to build the `.exe`)

## Running from source

```bat
python forever_addon_manager.py
```

## Building the executable

Run `build_exe.bat`. It creates an isolated virtual environment, installs PyInstaller (and `tkinterdnd2` for drag & drop) and writes `dist\ForeverAddonManager.exe`.

## Releases and auto-update

The manager checks this repository's latest GitHub release on start (can be turned off) and offers to update itself. It downloads `ForeverAddonManager.exe`, verifies it against `ForeverAddonManager.exe.sha256`, replaces itself and restarts.

Publishing a new version:

1. Set `APP_VERSION` in `forever_addon_manager.py` (e.g. `2.3.0`) and commit.
2. Create a release with the tag `v2.3.0` (Releases → Draft a new release → Publish).
3. The *Build release exe* workflow builds the `.exe` on a Windows runner and attaches it plus its SHA-256 checksum to the release (takes a few minutes).

## Where data is stored

Configuration, backups and the log file are stored in `%APPDATA%\ForeverAddonManager`. The manager only reads and writes files inside the WoW `Interface\AddOns`, `Interface\AddOns_Disabled` and `WTF` folders – it never modifies the game client.

## License

MIT – see [LICENSE](LICENSE).
