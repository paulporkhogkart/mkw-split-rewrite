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
    version=None,
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
