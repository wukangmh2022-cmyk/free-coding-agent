# -*- mode: python ; coding: utf-8 -*-

import sys

exe_icon = 'assets/icon-windowed.ico' if sys.platform.startswith('win') else 'assets/icon-windowed.icns'


a = Analysis(
    ['agent_qt.py'],
    pathex=[],
    binaries=[],
    datas=[('plugins', 'plugins'), ('assets', 'assets')],
    hiddenimports=[],
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
    [],
    exclude_binaries=True,
    name='AgentQT',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=exe_icon,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='AgentQT',
)
app = BUNDLE(
    coll,
    name='AgentQT.app',
    icon='assets/icon-windowed.icns',
    bundle_identifier=None,
)
