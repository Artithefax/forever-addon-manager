# Leeroyzan

A lightweight, personal addon manager for **World of Warcraft** – Forever, Classic and Retail – written in Python with a Tkinter GUI for Windows. Because updating addons should take one click, not a raid plan.

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
- Share addon selections with friends as `.lzpack` packages
- Share **download lists** (`.lzlist` file or a plain-text code that fits into Discord/SMS messages)
- Drag & drop for ZIP, `.lzpack` and `.lzlist` files (optional, via `tkinterdnd2`)
- Log file of all changes
- Self-update from GitHub releases

## CurseForge integration

Addons can be installed from CurseForge by pasting the project link (**Add → From CurseForge…**), and they are included in the normal update check. It uses the official CurseForge API and is designed to respect addon authors:

- **No redistribution.** CurseForge files are never rehosted, mirrored or bundled. Every download is fetched directly from CurseForge by the user's own client.
- **Shared packages contain references only.** For CurseForge projects, a `.lzpack` package stores the project and file ID, never the files themselves. The recipient's client downloads the file from CurseForge or opens the project page.
- **`allowModDistribution` is respected.** If an author disables third-party distribution, the tool only links to the CurseForge project page.
- **Attribution.** The project name, author and a link back to the CurseForge project page (where the author's donation options live) are shown in the details and after each install.
- **Own API key per user.** Get a free key at <https://console.curseforge.com/> and paste it into the settings. It is stored only in the user's local config (`%APPDATA%\Leeroyzan\config.json`), never embedded in the source code or the built executable, and only ever sent to `api.curseforge.com`.
- **Verified downloads.** Files are only fetched from CurseForge's CDN over HTTPS and checked against the SHA-1 hash published by the API.
- **Blocked downloads stay blocked.** If an author disables third-party downloads, the manager does not work around it: it opens the normal CurseForge download page in your browser, you click download, and the manager installs the file from your download folder once it appears.
- **Direct download link for updates.** When a CurseForge update is available, the manager offers a shortcut to that file's download page on CurseForge: right-click an addon, double-click its *Update* column, the button in the details panel (sidebar view), the link on the addon card (launcher view) or the link in the update dialog.
- **Manually installed addons** can be linked to their CurseForge project (automatically via the `X-Curse-Project-ID` in the `.toc`, or by pasting the project link). Afterwards they are included in update checks like any other addon.
- **Minimal traffic.** Update checks run on app start or on manual request only (one request per installed CurseForge addon).

## Download lists

A download list is the lightweight way to share many addons: it contains **only references** (CurseForge project/file IDs and GitHub `owner/repo` names) – no addon files, no paths, no personal data.

- **Share:** select addons (or none for all) and click **Liste teilen…** (Teilen page, Add menu or the *Mehr* menu). You get a text code that can be split into messages of a chosen size – *Discord/Chat* (1800 characters), *Kurznachricht* (500) or *SMS* (150) – or saved as a small `.lzlist` file. A *readable list* with links can be copied as well. Optionally the exact CurseForge file versions and your addon sets are included.
- **Import:** **Liste importieren…** accepts the pasted code (all parts, in any order; surrounding chat text and code-block backticks are ignored) or a `.lzlist` file, also via drag & drop. The manager shows what will be installed, skips addons you already have, and downloads the rest with *your own* CurseForge key. Downloads blocked by the author use the same browser-download flow as everywhere else.
- **Safe by design:** the code is `LZ1:<part>/<total>:<data>` (compressed JSON, Base32 so it survives chat apps). Imported lists are strictly validated; only known CurseForge IDs and valid GitHub repo names are accepted, and the size is capped.
- Addons without a download source (installed manually and not linked to CurseForge) cannot be listed – the dialog tells you which ones; use *Mit CurseForge verknüpfen* or a `.lzpack` package for those.

## Requirements

- Windows 10/11
- Python 3.10 or newer (only needed to run from source or to build the `.exe`)

## Running from source

```bat
python leeroyzan.py
```

## Building the executable

Run `build_exe.bat`. It creates an isolated virtual environment, installs PyInstaller (and `tkinterdnd2` for drag & drop) and writes `dist\Leeroyzan.exe`.

## Releases and auto-update

The manager checks this repository's latest GitHub release on start. With *Manager-Updates: Automatisch installieren* (default) it updates itself without any download by hand: it downloads `Leeroyzan.exe`, verifies it against `Leeroyzan.exe.sha256`, replaces itself and restarts. If a release was just published and the `.exe` is still being built, the manager waits in the background (checking every 90 seconds for up to 30 minutes) and installs it as soon as it is ready. When run as a Python script, it replaces `leeroyzan.py` with the release's version instead (keeping a `.py.bak`); inside a git checkout it leaves the file alone – use `git pull` there.

Publishing a new version:

1. Set `APP_VERSION` in `leeroyzan.py` (e.g. `1.0.3`) and commit.
2. Create a release with the tag `v1.0.3` (Releases → Draft a new release → Publish).
3. The *Build release exe* workflow builds the `.exe` on a Windows runner and attaches it plus its SHA-256 checksum to the release (takes a few minutes).

## Where data is stored

Configuration, backups and the log file are stored in `%APPDATA%\Leeroyzan`. The manager only reads and writes files inside the WoW `Interface\AddOns`, `Interface\AddOns_Disabled` and `WTF` folders – it never modifies the game client.

## License

MIT – see [LICENSE](LICENSE).
