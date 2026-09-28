# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for HumanType.app — the native menu-bar typing app.
# Build with:  pyinstaller --noconfirm HumanType.spec
#
# Entry point is humantype_app.py, which imports the typing engine
# (humantype.py) and the AppKit UI (humantype_ui.py). pynput's macOS keyboard
# backend is imported lazily, so we name it explicitly as a hidden import.

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = (
    collect_submodules("pynput")
    + ["humantype", "humantype_ui"]
)

a = Analysis(
    ["humantype_app.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="HumanType",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # windowed → no terminal window
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="HumanType",
)

app = BUNDLE(
    coll,
    name="HumanType.app",
    icon="HumanType.icns",
    bundle_identifier="com.humantype.app",
    info_plist={
        "LSUIElement": True,            # menu-bar only, no Dock icon
        "NSHighResolutionCapable": True,
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleVersion": "1.0.0",
    },
)
