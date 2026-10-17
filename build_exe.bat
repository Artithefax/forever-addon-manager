@echo off
REM ==========================================================
REM  Baut ForeverAddonManager.exe aus forever_addon_manager.py
REM  Voraussetzung: Python 3.10+ (python.org, "Add to PATH")
REM  Diese Datei in denselben Ordner wie das .py legen und
REM  doppelklicken.
REM ==========================================================
setlocal
cd /d "%~dp0"

REM Echtes Python suchen: zuerst den py-Launcher, dann python.
REM (Der Microsoft-Store-Platzhalter besteht den Versionstest nicht.)
set "PY="
py -3 -c "import sys; assert sys.version_info >= (3, 10)" >nul 2>&1 && set "PY=py -3"
if not defined PY (
    python -c "import sys; assert sys.version_info >= (3, 10)" >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo [FEHLER] Kein Python 3.10+ gefunden.
    echo.
    echo  Bitte Python von https://www.python.org/downloads/ installieren
    echo  und im Setup "Add python.exe to PATH" anhaken.
    echo  Danach dieses Fenster schliessen und das Skript neu starten.
    echo.
    start "" https://www.python.org/downloads/
    pause
    exit /b 1
)
echo Verwende: %PY%

if not exist forever_addon_manager.py (
    echo [FEHLER] forever_addon_manager.py liegt nicht in diesem Ordner.
    pause
    exit /b 1
)

echo [1/3] Lege isolierte Build-Umgebung an ...
%PY% -m venv .build_venv || goto :fail
call .build_venv\Scripts\activate.bat

echo [2/3] Installiere PyInstaller ...
python -m pip install --upgrade pip pyinstaller --quiet || goto :fail
REM optional: Drag & Drop - ohne das Paket laeuft der Manager trotzdem
python -m pip install --upgrade tkinterdnd2 --quiet
set "DND_OPT="
python -c "import tkinterdnd2" >nul 2>&1 && set "DND_OPT=--collect-all tkinterdnd2"
if defined DND_OPT (echo Drag ^& Drop: aktiviert) else (echo [Hinweis] tkinterdnd2 nicht verfuegbar - Drag ^& Drop deaktiviert.)

echo [3/3] Baue EXE ...
set "ICON_OPT="
if exist forever_addon_manager.ico (
    set "ICON_OPT=--icon forever_addon_manager.ico"
    echo Verwende Icon: forever_addon_manager.ico
) else (
    echo [Hinweis] forever_addon_manager.ico nicht gefunden - baue ohne eigenes Icon.
)
pyinstaller --noconfirm --clean --onefile --windowed ^
    --name ForeverAddonManager %ICON_OPT% %DND_OPT% ^
    forever_addon_manager.py || goto :fail

call deactivate
rmdir /s /q build 2>nul
del /q ForeverAddonManager.spec 2>nul

echo.
echo Fertig: %~dp0dist\ForeverAddonManager.exe
explorer dist
pause
exit /b 0

:fail
echo.
echo [FEHLER] Build fehlgeschlagen - siehe Ausgabe oben.
pause
exit /b 1
