# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for mkw-tracker sidecar binary.
#
# Build:  pyinstaller mkw_tracker.spec
# Output: dist/mkw-tracker/mkw-tracker.exe  (--onedir mode)
#
# The resulting dist/mkw-tracker/ folder is:
#   - Zipped and uploaded as a GitHub Release artifact (portable bundle)
#   - Copied into src-tauri/binaries/ as the Tauri sidecar (when Tauri is added)

block_cipher = None

import shutil as _shutil

# Include ffmpeg/ffprobe if they are on PATH at build time.
# On the CI runner (windows-latest) ffmpeg is pre-installed.
# Locally, install ffmpeg and ensure it's on PATH before running pyinstaller.
_ffmpeg_path  = _shutil.which('ffmpeg')
_ffprobe_path = _shutil.which('ffprobe')
if not _ffmpeg_path or not _ffprobe_path:
    raise SystemExit(
        "ERROR: ffmpeg/ffprobe not found in PATH — cannot build a working bundle.\n"
        "  Windows: choco install ffmpeg   or download a static build and add to PATH."
    )
_ffmpeg_bins = [(_ffmpeg_path, '.'), (_ffprobe_path, '.')]

a = Analysis(
    ['mkw_tracker/__main__.py'],
    pathex=[],
    binaries=_ffmpeg_bins,
    datas=[
        # Bundle the entire images/ directory alongside the exe.
        # resource_path() in utils/paths.py resolves these via sys._MEIPASS.
        ('images', 'images'),
    ],
    hiddenimports=[
        # cv2 plugin DLLs are sometimes missed by the hook; list explicitly.
        'cv2',
        'numpy',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Trim unused large packages to keep bundle small.
        'tkinter',
        'matplotlib',
        'PIL',
        'scipy',
        'pandas',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# --- Engine version metadata -------------------------------------------------
# Without an embedded version resource the sidecar lists in Windows Task Manager as
# a bare "mkw-tracker-engine.exe" (and as plain "Python" in dev). Embedding a
# FileDescription/ProductName makes Windows show it as "pbenguin engine", grouped
# under the pbenguin app that spawns it. Generated from package.json's version into
# the gitignored build/ dir. Frozen build only - in dev the engine is python.exe.
import json as _json, os as _os

def _engine_version():
    try:
        with open(_os.path.join(SPECPATH, 'package.json'), encoding='utf-8') as _f:
            return str(_json.load(_f).get('version') or '0.0.0')
    except Exception:
        return '0.0.0'

_ev = _engine_version()
_evt = tuple(([int(''.join(c for c in p if c.isdigit()) or 0) for p in _ev.split('.')] + [0, 0, 0, 0])[:4])
_os.makedirs(_os.path.join(SPECPATH, 'build'), exist_ok=True)
_version_file = _os.path.join(SPECPATH, 'build', 'engine_version_info.txt')
with open(_version_file, 'w', encoding='utf-8') as _f:
    _f.write(
        'VSVersionInfo(\n'
        f'  ffi=FixedFileInfo(filevers={_evt}, prodvers={_evt}, mask=0x3f, flags=0x0,\n'
        '                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),\n'
        '  kids=[\n'
        "    StringFileInfo([StringTable('040904B0', [\n"
        "      StringStruct('CompanyName', 'paulporkhogkart'),\n"
        "      StringStruct('FileDescription', 'pbenguin engine'),\n"
        f"      StringStruct('FileVersion', '{_ev}'),\n"
        "      StringStruct('InternalName', 'pbenguin-engine'),\n"
        "      StringStruct('OriginalFilename', 'mkw-tracker-engine.exe'),\n"
        "      StringStruct('ProductName', 'pbenguin'),\n"
        f"      StringStruct('ProductVersion', '{_ev}'),\n"
        '    ])]),\n'
        "    VarFileInfo([VarStruct('Translation', [1033, 1200])]),\n"
        '  ],\n'
        ')\n'
    )

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='mkw-tracker-engine',
    icon='src-tauri/icons/icon.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,      # UPX-packed PyInstaller exes trigger Windows Defender false positives
    console=True,   # Must be True: sidecar communicates over stdio
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=_version_file,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='mkw-tracker',
)
