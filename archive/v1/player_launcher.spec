# -*- mode: python ; coding: utf-8 -*-
import os

SPEC_ROOT = os.path.abspath(".")

a = Analysis(
    ["player_launcher.py"],
    pathex=[SPEC_ROOT],
    binaries=[],
    datas=[(os.path.join(SPEC_ROOT, "agent", "knowledge"), os.path.join("agent", "knowledge"))],
    hiddenimports=["agent"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="StarCoPlayAgent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
