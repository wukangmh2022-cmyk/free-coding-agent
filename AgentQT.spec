# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys

exe_icon = 'assets/icon-windowed.ico' if sys.platform.startswith('win') else 'assets/icon-windowed.icns'
bundle_version = '0.99'


def build_asset_datas():
    asset_root = Path('assets')
    keep_files = [
        'app_icon.png',
        'copy-transparent.png',
        'copy-transparent.svg',
        'icon-windowed.icns',
        'icon-windowed.ico',
    ]
    keep_dirs = ['homepage', 'icon.iconset']
    datas = []
    for rel_path in keep_files:
        datas.append((str(asset_root / rel_path), f'assets/{rel_path}'))
    for rel_dir in keep_dirs:
        src_dir = asset_root / rel_dir
        for path in sorted(src_dir.rglob('*')):
            if path.is_file():
                target = path.parent.as_posix()
                datas.append((str(path), target))
    return datas


a = Analysis(
    ['agent_qt_stream_quick.py'],
    pathex=[],
    binaries=[],
    datas=[('plugins', 'plugins'), *build_asset_datas()],
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
    info_plist={
        'CFBundleShortVersionString': bundle_version,
        'CFBundleVersion': bundle_version,
    },
)
