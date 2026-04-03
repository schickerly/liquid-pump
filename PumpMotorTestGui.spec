# -*- mode: python ; coding: utf-8 -*-
# Build: py -m PyInstaller --clean PumpMotorTestGui.spec

a = Analysis(
    ["pump_motor_test_gui.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=["keyboard", "serial.tools.list_ports", "hid"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="PumpMotorTestGui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
