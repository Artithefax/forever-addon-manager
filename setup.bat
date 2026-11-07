@echo off
REM ==========================================================
REM  setup.bat - baut Leeroyzan.exe aus leeroyzan.py und
REM  installiert es (Startmenue + Desktop), ohne Adminrechte.
REM  Nebenbei entsteht dist\Leeroyzan-Portable.zip.
REM
REM  Python muss NICHT installiert sein: Fehlt es, laedt das
REM  Skript das Werkzeug "uv" in den Unterordner .build_tools
REM  und holt sich darueber ein portables Python - am System
REM  wird nichts installiert. Ist Python 3.10+ vorhanden, wird
REM  das genommen.
REM
REM  Normalerweise nicht noetig: Jedes GitHub-Release enthaelt
REM  Leeroyzan-Setup.exe und Leeroyzan-Portable.zip fertig.
REM ==========================================================
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
if not exist leeroyzan.py (
    echo [ERROR] leeroyzan.py not found next to setup.bat.
    pause
    exit /b 1
)

echo.
echo   Leeroyzan Setup
echo   ---------------
echo   1 = Deutsch    2 = English
echo.
choice /c 12 /n /m "  > "
if errorlevel 2 (set "LZ_LANG=en") else (set "LZ_LANG=de")
call :lang_%LZ_LANG%
echo.

REM Echtes Python suchen: zuerst den py-Launcher, dann python.
REM Der Microsoft-Store-Platzhalter besteht den Versionstest nicht.
set "PY="
py -3 -c "import sys; assert sys.version_info >= (3, 10)" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys; assert sys.version_info >= (3, 10)" >nul 2>&1 && set "PY=python"
if defined PY goto :with_python

:with_uv
if exist .build_venv rmdir /s /q .build_venv
echo %M_NOPY%
echo [1/5] %M_ENV%
if exist .build_tools\uv.exe goto :uv_ready
echo       %M_UV%
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue';" ^
  "Invoke-WebRequest 'https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip' -OutFile '.build_tools.zip';" ^
  "Expand-Archive '.build_tools.zip' '.build_tools' -Force; Remove-Item '.build_tools.zip';" ^
  "$u = Get-ChildItem '.build_tools' -Recurse -Filter 'uv.exe' | Select-Object -First 1;" ^
  "if (-not $u) { throw 'uv.exe not found' };" ^
  "if ($u.FullName -ne (Join-Path (Resolve-Path '.build_tools') 'uv.exe')) { Copy-Item $u.FullName '.build_tools\uv.exe' }"
if errorlevel 1 goto :fail
:uv_ready
.build_tools\uv.exe venv --python 3.12 .build_venv
if errorlevel 1 goto :fail
echo [2/5] %M_DEPS%
.build_tools\uv.exe pip install --python .build_venv\Scripts\python.exe pyinstaller tkinterdnd2
if errorlevel 1 goto :fail
goto :build

:with_python
echo %M_USING% %PY%
if exist .build_venv rmdir /s /q .build_venv
echo [1/5] %M_ENV%
%PY% -m venv .build_venv
if errorlevel 1 goto :fail
echo [2/5] %M_DEPS%
.build_venv\Scripts\python.exe -m pip install --upgrade pip pyinstaller --quiet
if errorlevel 1 goto :fail
.build_venv\Scripts\python.exe -m pip install --upgrade tkinterdnd2 --quiet

:build
set "DND_OPT="
.build_venv\Scripts\python.exe -c "import tkinterdnd2" >nul 2>&1 && set "DND_OPT=--collect-all tkinterdnd2"
if not defined DND_OPT echo %M_NODND%
set "ICON_OPT="
if exist leeroyzan.ico set "ICON_OPT=--icon leeroyzan.ico"
echo [3/5] %M_BUILD%
.build_venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name Leeroyzan %ICON_OPT% %DND_OPT% leeroyzan.py
if errorlevel 1 goto :fail
if exist build rmdir /s /q build
if exist Leeroyzan.spec del /q Leeroyzan.spec

REM ---- Portable Version: .exe + Ordner "Leeroyzan-Data" -------------------
echo [4/5] %M_PORT%
if exist dist\portable rmdir /s /q dist\portable
mkdir dist\portable\Leeroyzan\Leeroyzan-Data
copy /y dist\Leeroyzan.exe dist\portable\Leeroyzan\ >nul
if exist portable-readme.txt copy /y portable-readme.txt dist\portable\Leeroyzan\Leeroyzan-Data\README.txt >nul
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Compress-Archive -Path 'dist\portable\Leeroyzan' -DestinationPath 'dist\Leeroyzan-Portable.zip' -Force"
if errorlevel 1 goto :fail
rmdir /s /q dist\portable

REM ---- Installation nach %LOCALAPPDATA%\Programs\Leeroyzan ----------------
if not defined LOCALAPPDATA goto :fail
set "LZ_DIR=%LOCALAPPDATA%\Programs\Leeroyzan"
set "LZ_EXE=%LOCALAPPDATA%\Programs\Leeroyzan\Leeroyzan.exe"
:check_running
tasklist /FI "IMAGENAME eq Leeroyzan.exe" 2>nul | find /I "Leeroyzan.exe" >nul
if errorlevel 1 goto :install
echo %M_RUNNING%
pause >nul
goto :check_running

:install
echo [5/5] %M_INSTALL% %LZ_DIR%
if not exist "%LZ_DIR%" mkdir "%LZ_DIR%"
copy /y dist\Leeroyzan.exe "%LZ_EXE%" >nul
if errorlevel 1 goto :fail
REM Sprache an Leeroyzan weitergeben (wie beim Leeroyzan-Setup.exe)
if not exist "%APPDATA%\Leeroyzan" mkdir "%APPDATA%\Leeroyzan"
> "%APPDATA%\Leeroyzan\setup.ini" (echo [Setup]& echo Language=%LZ_LANG%)
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$w = New-Object -ComObject WScript.Shell;" ^
  "foreach ($d in [Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('Desktop')) {" ^
  "  $l = $w.CreateShortcut((Join-Path $d 'Leeroyzan.lnk')); $l.TargetPath = $env:LZ_EXE;" ^
  "  $l.WorkingDirectory = $env:LZ_DIR; $l.IconLocation = $env:LZ_EXE + ',0'; $l.Save() }"

echo.
echo %M_DONE%
echo   %LZ_EXE%
echo %M_PORTDONE%
echo   %~dp0dist\Leeroyzan-Portable.zip
echo.
choice /c %M_YN% /n /m "%M_START% "
if errorlevel 2 goto :end
start "" "%LZ_EXE%"
:end
endlocal
exit /b 0

:fail
echo.
echo %M_FAIL%
pause
exit /b 1

REM ---- Texte ---------------------------------------------------------------
:lang_de
set "M_NOPY=Kein Python gefunden – verwende eine portable Build-Umgebung, am System wird nichts installiert."
set "M_USING=Verwende:"
set "M_ENV=Lege Build-Umgebung an …"
set "M_UV=Lade uv herunter …"
set "M_DEPS=Installiere PyInstaller und Drag-und-Drop-Unterstützung …"
set "M_NODND=[Hinweis] tkinterdnd2 nicht verfügbar – Drag-und-Drop ist deaktiviert."
set "M_BUILD=Baue Leeroyzan.exe – das dauert ein bis zwei Minuten …"
set "M_PORT=Erstelle die portable Version …"
set "M_RUNNING=Leeroyzan läuft noch. Bitte schließen und dann eine Taste drücken …"
set "M_INSTALL=Installiere nach"
set "M_DONE=Fertig! Leeroyzan ist installiert (Startmenü und Desktop):"
set "M_PORTDONE=Portable Version (ohne Installation, z. B. für einen USB-Stick):"
set "M_YN=JN"
set "M_START=Leeroyzan jetzt starten? [J/N]"
set "M_FAIL=[FEHLER] Setup fehlgeschlagen – siehe Ausgabe oben."
exit /b 0

:lang_en
set "M_NOPY=No Python found – using a portable build environment, nothing is installed on your system."
set "M_USING=Using:"
set "M_ENV=Creating build environment …"
set "M_UV=Downloading uv …"
set "M_DEPS=Installing PyInstaller and drag-and-drop support …"
set "M_NODND=[Note] tkinterdnd2 not available – drag and drop is disabled."
set "M_BUILD=Building Leeroyzan.exe – this takes a minute or two …"
set "M_PORT=Creating the portable version …"
set "M_RUNNING=Leeroyzan is still running. Please close it, then press any key …"
set "M_INSTALL=Installing to"
set "M_DONE=Done! Leeroyzan is installed (Start menu and desktop):"
set "M_PORTDONE=Portable version (no installation, e.g. for a USB stick):"
set "M_YN=YN"
set "M_START=Start Leeroyzan now? [Y/N]"
set "M_FAIL=[ERROR] Setup failed – see the output above."
exit /b 0
