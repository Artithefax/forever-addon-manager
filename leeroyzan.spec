# -*- mode: python -*-
# PyInstaller-Bauplan für Leeroyzan.exe (Release-Workflow und setup.bat):
#     python -m PyInstaller --noconfirm --clean leeroyzan.spec
# Gegenüber einem einfachen „pyinstaller --onefile“ fehlt alles, was Leeroyzan nie braucht – die .exe muss
# sich bei jedem Start entpacken, jede Datei weniger macht den Start schneller und Virenscanner ruhiger:
#   - die Zeitzonen-Datenbank und Datumsformate von Tcl (~700 Dateien, nur für Tcls „clock“-Befehl)
#   - setuptools und Testmodule
# Drag & Drop (tkinterdnd2) sammelt der mitgelieferte PyInstaller-Hook – nur für das eigene System.
import os

TCL_UNUSED = ("_tcl_data/tzdata/", "_tcl_data/msgs/")

a = Analysis(
    ["leeroyzan.py"],
    pathex=[],
    binaries=[],
    datas=[("Leeroylink", "Leeroylink")],
    hiddenimports=[],
    excludes=["unittest", "pydoc", "doctest", "pdb", "lib2to3", "test", "setuptools", "pkg_resources", "distutils"],
    noarchive=False,
)
a.datas = [d for d in a.datas if not d[0].replace("\\", "/").startswith(TCL_UNUSED)]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Leeroyzan",
    icon="leeroyzan.ico" if os.path.exists("leeroyzan.ico") else None,
    console=False,
    upx=False,
    runtime_tmpdir=None,
)
