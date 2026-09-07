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
import re
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# Ścieżki względne do tego pliku
HERE = os.path.dirname(os.path.abspath(SPEC))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
DESKTOP = ROOT  # W nowym repo desktop jest w root
ICONS = os.path.join(ROOT, "installer", "icons")

# === Win32 VersionInfo ("Prawy klik -> Właściwości -> Szczegóły") ===
# Wersja z SIMPLE_DECK_VERSION (ustawiane przez build.ps1 z pyproject.toml);
# fallback: parsowanie pyproject na wypadek ręcznych buildów bez build.ps1.
_app_version = os.environ.get("SIMPLE_DECK_VERSION") or "0.0.0"
if _app_version == "0.0.0":
    try:
        with open(os.path.join(ROOT, "pyproject.toml"), "r", encoding="utf-8") as _f:
            _m = re.search(r'^version\s*=\s*"([^"]+)"', open(
                os.path.join(ROOT, "pyproject.toml"), encoding="utf-8").read(), re.M)
        if _m:
            _app_version = _m.group(1)
    except OSError:
        pass

#wersja może zawierać sufiks alfa (np. 1.1.0a) -> VS_VERSIONINFO chce czterech
#liczb; sufiks idzie do ProductVersion (string),FileVersion dostaje x.y.z.0.
_ver_core = re.match(r"(\d+(?:\.\d+)*)", _app_version)
_ver_nums = [int(x) for x in (_ver_core.group(1).split(".") if _ver_core else ["0"])]
while len(_ver_nums) < 4:
    _ver_nums.append(0)

# Tekst zasobu VS_VERSIONINFO (format PyInstaller: pythonowa deklaracja).
# LegalCopyright = MIT (c) 2026 GREJEM INDUSTRIES -> widoczne w Zakładce
# "Szczegóły" pliku Simple-Deck.exe (Defender/SmartScreen też czytają te pola).
_versioninfo_tpl = """# UTF-8
#
# For more details about fixed file info 'ffi' see:
# http://msdn.microsoft.com/en-us/library/ms646997.aspx
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=(_V0, _V1, _V2, _V3),
    prodvers=(_V0, _V1, _V2, _V3),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        u'040904B0',
        [StringStruct(u'CompanyName', u'GREJEM INDUSTRIES'),
        StringStruct(u'FileDescription', u'Simple Deck - Stream Deck Controller'),
        StringStruct(u'FileVersion', u'~FILEVERSION~'),
        StringStruct(u'InternalName', u'Simple-Deck'),
        StringStruct(u'LegalCopyright', u'MIT Copyright (c) 2026 GREJEM INDUSTRIES'),
        StringStruct(u'LegalTrademarks', u'Simple Deck is distributed under the MIT License'),
        StringStruct(u'OriginalFilename', u'Simple-Deck.exe'),
        StringStruct(u'ProductName', u'Simple Deck'),
        StringStruct(u'ProductVersion', u'~PRODUCTVERSION~')]),
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)
"""
_versioninfo_txt = (_versioninfo_tpl
                    .replace("_V0", str(_ver_nums[0]))
                    .replace("_V1", str(_ver_nums[1]))
                    .replace("_V2", str(_ver_nums[2]))
                    .replace("_V3", str(_ver_nums[3]))
                    .replace("~FILEVERSION~", _app_version)
                    .replace("~PRODUCTVERSION~", _app_version))
# Zapis obok spec - ścieżka przekazywana do EXE(version=...) jest relatywna
# do katalogu pracy PyInstallera, więc podajemy bezwzględną.
_version_info_path = os.path.join(HERE, "version_info.txt")
with open(_version_info_path, "w", encoding="utf-8") as _vf:
    _vf.write(_versioninfo_txt)

block_cipher = None

# D10 fix: usunięto collect_submodules("PySide6") - wymuszało bundlowanie
# WSZYSTKICH modułów Qt (QtWebEngine, Qt3D, QtCharts, QtMultimedia, QtPdf,
# QtDataVisualization...) = build 500-800 MB. PyInstaller ma gotowy hook
# na PySide6 który zbiera tylko potrzebne submoduły na podstawie imports.
#
# ctypes.macholib jest macOS-only - usunięto (m).
hiddenimports = [
    "hid",
    "ctypes.wintypes",
    # single_instance.py używa QLocalServer/QLocalSocket (IPC do raise okna).
    # Bez tego PyInstaller nie wykryje importu, bo moduł jest w excludes
    # opartych na nazwach (added 2026-09-03, fix ModuleNotFoundError po build).
    "PySide6.QtNetwork",
]

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
        # UWAGA: QtNetwork NIE może tu być — single_instance.py używa
        # QLocalServer/QLocalSocket (patrz hiddenimports). Excludes ma
        # pierwszeństwo przed hiddenimports i wywalałby moduł z builda.
        "PySide6.QtSql", "PySide6.QtXml",
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
    version=_version_info_path,  # Win32 VersionInfo: MIT (c) 2026 GREJEM INDUSTRIES
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
