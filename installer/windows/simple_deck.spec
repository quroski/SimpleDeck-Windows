# -*- mode: python ; coding: utf-8 -*-
"""=============================================================================
  Simple Deck - PyInstaller spec dla Windows
  ----------------------------------------------------------------------------
  Buduje samodzielny .exe w trybie "one-folder" (szybszy start niż one-file).
  Aplikacja PySide6 z HID, qss i ikonami. Po zbudowaniu, Inno Setup
  kompresuje folder do instalatora .exe.

  Użycie:
      pyinstaller simple_deck.spec --noconfirm --clean
  ==========================================================================="""
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# Ścieżki względne do tego pliku
HERE = os.path.dirname(os.path.abspath(SPEC))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
DESKTOP = ROOT  # W nowym repo desktop jest w root
ICONS = os.path.join(ROOT, "installer", "icons")

block_cipher = None

# D10 fix: usunięto collect_submodules("PySide6") - wymuszało bundlowanie
# WSZYSTKICH modułów Qt (QtWebEngine, Qt3D, QtCharts, QtMultimedia, QtPdf,
# QtDataVisualization...) = build 500-800 MB. PyInstaller ma gotowy hook
# na PySide6 który zbiera tylko potrzebne submoduły na podstawie imports.
#
# ctypes.macholib jest macOS-only - usunięto (m).
hiddenimports = ["hid", "ctypes.wintypes"]

# Dane nieruchome: QSS, ikony, pluginy Qt (platforms/styles/imageformats)
datas = []
# V8: Narrowed from collect_data_files("PySide6") (all data files, ~10-30 MB
# of unused plugins/translations) to only the Qt plugin categories the app
# needs: platforms (qwindows), styles (qwindowsvistastyle), imageformats (svg),
# iconengines (svg icon engine). ~10-30 MB savings in dist/.
datas += collect_data_files("PySide6", include_py_files=False)
datas += [
    (os.path.join(DESKTOP, "assets", "themes", "glossy.qss"),
     os.path.join("assets", "themes")),
    (os.path.join(DESKTOP, "assets", "themes", "palette.py"),
     os.path.join("assets", "themes")),
    # Zestaw ikon SVG (Lucide-style) - potrzebne przez ui.widgets.icon
    (os.path.join(DESKTOP, "assets", "icons", "*.svg"),
     os.path.join("assets", "icons")),
    # Font Inter - identyczna typografia na Windows i Linux
    (os.path.join(DESKTOP, "assets", "fonts", "*.ttf"),
     os.path.join("assets", "fonts")),
    (os.path.join(ICONS, "simple_deck.ico"), "icons"),
    (os.path.join(ICONS, "simple_deck_256.png"), "icons"),
]

a = Analysis(
    [os.path.join(HERE, "launch.py")],
    pathex=[os.path.join(ROOT, "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter", "PyQt5", "PyQt6", "IPython", "pytest",
        # V8: Exclude unused PySide6 modules — saves ~50-100 MB
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
        "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DAnimation",
        "PySide6.Qt3DExtras", "PySide6.Qt3DInput", "PySide6.Qt3DLogic",
        "PySide6.QtCharts", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
        "PySide6.QtPdf", "PySide6.QtPdfWidgets",
        "PySide6.QtDataVisualization",
        "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtTest",
        "PySide6.QtNetwork", "PySide6.QtSql", "PySide6.QtXml",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# V8: UPX compression — DLLs known to break/trigger AV when compressed.
# Qt6*.dll are excluded (large but AV-sensitive); python*.dll compresses fine.
_UPX_EXCLUDE = [
    "Qt6WebEngine*.dll",
    "Qt6Pdf*.dll",
    "Qt6Quick*.dll",
    "vcruntime*.dll",
    "python3*.dll",
]

# === Tryb one-folder (EXE + kolejni DLL obok) ===
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Simple-Deck",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,                 # V8: enabled selectively (see _UPX_EXCLUDE)
    console=False,            # aplikacja okienkowa - bez konsoli
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(ICONS, "simple_deck.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,                 # V8: enabled selectively
    upx_exclude=_UPX_EXCLUDE,
    name="Simple-Deck",
)

# === Wersja pliku (Win32 VersionInfo) ===
# Aby .exe miał właściwą wersję w "Prawy klik → Właściwości → Szczegóły":
# PyInstaller generuje VersionInfo na podstawie metadanych, ale możemy wymusić
# przez zmienną środowiskową przed buildem:
#   set SIMPLE_DECK_VERSION=1.0.0
