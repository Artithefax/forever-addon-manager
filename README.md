# Leeroyzan

A lightweight, personal addon manager for **World of Warcraft** – Forever, Classic and Retail – written in Python with a Tkinter GUI for Windows. Because updating addons should take one click, not a raid plan.

> Hobby project – non-commercial, no ads, no telemetry. Not affiliated with Blizzard Entertainment or CurseForge.

## Download

Get the latest version from the [Releases page](https://github.com/Artithefax/leeroyzan/releases/latest) – **no Python needed**, everything is bundled:

- **`Leeroyzan-Setup.exe`** – installs Leeroyzan for your user (no admin rights) to `%LOCALAPPDATA%\Programs\Leeroyzan`, with a Start-menu entry, an optional desktop shortcut and an uninstaller. The setup asks for your language (Deutsch or English).
- **`Leeroyzan-Portable.zip`** – the portable version: unzip it anywhere (e.g. a USB stick) and start `Leeroyzan.exe`. No installation; settings, logs and backups stay in the `Leeroyzan-Data` folder next to the `.exe` instead of `%APPDATA%`.
- **`Leeroyzan.exe`** – the bare program file (used by the auto-update); also runs on its own from any folder.

All of them update themselves from then on. Windows SmartScreen may warn the first time because the file is not code-signed – click *More info → Run anyway*.

## Languages

Leeroyzan speaks **Deutsch and English**. On first start it uses the language chosen in the setup, otherwise your Windows display language (German for a German Windows, English for everything else). Switch any time under **Settings → General → Language** – the window changes immediately, no restart needed.

## The interface

One window, no pop-ups. A sidebar with the game version, the pages and a big **Play** button; questions, confirmations and messages appear as a bar at the top of the window instead of separate dialogs.

| Page | What you do there |
|---|---|
| **My addons** | All installed addons as a list or as tiles, with search, grouping and a detail panel (update, open source, link with CurseForge, open folder, remove). Addons with an update are yellow with a ⬇ marker. |
| **Discover** | Install directly from a CurseForge link, a GitHub repository or a ZIP from your download folder – and search CurseForge (WoW addons only) with file choice. |
| **Updates** | Check and update all or single addons, read the changes, see which blocked downloads Leeroyzan is waiting for, link manual addons, and keep Leeroyzan itself up to date. |
| **Share** | Create a download-list code for Discord/SMS or a `.lzpack` package – and paste a code you received to install it. |
| **Sets & backups** | Addon sets (switch groups of addons on/off) and WTF backups with restore. |
| **Settings** | WoW folder, CurseForge API key, download folder, language, update options, log. |

## Features

- Detects the WoW installation and game versions (`_forever_`, `_classic_beta_`, `_retail_`, …)
- Lists installed addons from their `.toc` files (title, version, interface, dependencies)
- Checks each addon's interface version against the installed game build (`.build.info`)
- Installs addons from ZIP files or GitHub releases, with a file picker when a release ships several variants (e.g. a `-forever` build)
- Update checks (optionally on start) with release notes, update all or single addons
- Addon sets: save groups of addons and switch between them with one click
- Enable/disable addons, warn when WoW is running
- WTF backups (manual and automatic before updates) with restore
- Share addon selections with friends as `.lzpack` packages (saved straight to your download folder)
- Share **download lists** (`.lzlist` file or a plain-text code that fits into Discord/SMS messages)
- Drag & drop for ZIP, `.lzpack` and `.lzlist` files (optional, via `tkinterdnd2`)
- Log file of all changes
- Self-update from GitHub releases

## CurseForge integration

Addons can be found and installed from CurseForge right inside the manager (**Discover** page): a search that only covers World of Warcraft addons, sorted by popularity, downloads, last update or name, with a filter for your game version (Classic, TBC, WotLK, Cata, MoP or Retail), paging, and multi-select install. Pasting a project link or ID still works. Installed addons are included in the normal update check. It uses the official CurseForge API and is designed to respect addon authors:

- **No redistribution.** CurseForge files are never rehosted, mirrored or bundled. Every download is fetched directly from CurseForge by the user's own client.
- **Shared packages contain references only.** For CurseForge projects, a `.lzpack` package stores the project and file ID, never the files themselves. The recipient's client downloads the file from CurseForge or opens the project page.
- **`allowModDistribution` is respected.** If an author disables third-party distribution, the tool only links to the CurseForge project page.
- **Attribution.** The project name, author and a link back to the CurseForge project page (where the author's donation options live) are shown in the details and after each install.
- **Own API key per user.** Get a free key at <https://console.curseforge.com/> and paste it into the settings. It is stored only in the user's local config (`%APPDATA%\Leeroyzan\config.json`), never embedded in the source code or the built executable, and only ever sent to `api.curseforge.com`.
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

## Requirements

- Windows 10/11 – nothing else. Python is only needed if you want to run the source code directly.

## Running from source

```bat
python leeroyzan.py
```

## Building the executable

Every GitHub release builds `Leeroyzan.exe`, `Leeroyzan-Portable.zip` and `Leeroyzan-Setup.exe` automatically (see below), so you normally never build by hand.

To build and install from the source, double-click **`setup.bat`**. It asks for your language, then:

1. builds `dist\Leeroyzan.exe` in an isolated environment (PyInstaller, `tkinterdnd2` for drag & drop),
2. creates `dist\Leeroyzan-Portable.zip`,
3. installs Leeroyzan to `%LOCALAPPDATA%\Programs\Leeroyzan` with Start-menu and desktop shortcuts (no admin rights; same place as the setup).

**Python does not have to be installed:** if none is found, the script downloads the standalone tool [uv](https://github.com/astral-sh/uv) into `.build_tools`, which fetches a portable Python into its cache – nothing is installed system-wide. With Python 3.10+ present, that one is used. The `Leeroyzan-Setup.exe` installer is built from `installer.iss` with [Inno Setup](https://jrsoftware.org/isinfo.php).

## Releases and auto-update

The manager checks this repository's latest GitHub release on start. With *Leeroyzan updates: Install automatically* (default) it updates itself without any download by hand: it downloads `Leeroyzan.exe`, verifies it against `Leeroyzan.exe.sha256`, replaces itself and restarts. If a release was just published and the `.exe` is still being built, the manager waits in the background (checking every minute for up to 30 minutes) and installs it as soon as it is ready. Releases without a `Leeroyzan.exe` (e.g. a failed build) are skipped, and if a downloaded update could not be put in place, the manager says so on the next start (details in `update.log`). When run as a Python script, it replaces `leeroyzan.py` with the release's version instead (keeping a `.py.bak`); inside a git checkout it leaves the file alone – use `git pull` there.

Publishing a new version:

1. Set `APP_VERSION` in `leeroyzan.py` (e.g. `1.0.3`) and commit.
2. Create a release with the tag `v1.0.3` (Releases → Draft a new release → Publish).
3. The *Build release exe* workflow builds the `.exe` on a Windows runner and attaches it plus its SHA-256 checksum, `Leeroyzan-Portable.zip` and `Leeroyzan-Setup.exe` to the release (takes a few minutes).

## Where data is stored

Configuration, backups and the log file are stored in `%APPDATA%\Leeroyzan` – or, in the portable version, in the `Leeroyzan-Data` folder next to `Leeroyzan.exe` (Leeroyzan switches to portable mode whenever that folder exists). The manager only reads and writes files inside the WoW `Interface\AddOns`, `Interface\AddOns_Disabled` and `WTF` folders – it never modifies the game client.

## License

MIT – see [LICENSE](LICENSE).
