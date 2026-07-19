"""
Builds the Python tracker with PyInstaller and copies the result into
src-tauri/binaries/ so Tauri can bundle it as a sidecar.

Called automatically via beforeBuildCommand in tauri.conf.json.
Can also be run directly: python scripts/prepare_sidecar.py
"""

import glob
import os
import shutil
import subprocess
import sys

SPEC_FILE    = "mkw_tracker.spec"
SIDECAR_SRC  = os.path.join("dist", "mkw-tracker")
SIDECAR_DIR  = os.path.join("src-tauri", "sidecar")
SIDECAR_EXE  = "mkw-tracker-engine.exe"
# Tauri installs resources into a bin/ subdirectory so the Python engine
# doesn't sit alongside the Tauri exe and confuse users or appear in search.
# prepare_sidecar.py populates src-tauri/sidecar/; the resources mapping
# in tauri.conf.json handles the bin/ destination at bundle time.


def newest_engine_source() -> tuple[float, str]:
    """(mtime, path) of the most recently touched engine input: every mkw_tracker/*.py,
    the spec, and requirements.txt."""
    newest, which = 0.0, "<none>"
    paths = glob.glob(os.path.join("mkw_tracker", "**", "*.py"), recursive=True)
    paths += [SPEC_FILE, "requirements.txt"]
    for p in paths:
        try:
            m = os.path.getmtime(p)
        except OSError:
            continue
        if m > newest:
            newest, which = m, p
    return newest, which


def main() -> None:
    dst_exe = os.path.join(SIDECAR_DIR, SIDECAR_EXE)
    dst_internal = os.path.join(SIDECAR_DIR, "_internal")
    if os.path.exists(dst_exe) and os.path.isdir(dst_internal):
        # Reuse only a FRESH exe. Skip-if-present with no staleness check is how the
        # 2026-07-19 Velopack rehearsal shipped an April engine that predated --video:
        # the WR service's args got argparse-rejected on every job, burning the Pi
        # queue's attempts. An exe older than any engine source rebuilds instead.
        src_mtime, src_path = newest_engine_source()
        if os.path.getmtime(dst_exe) >= src_mtime:
            print(f"==> Sidecar at {dst_exe} is newer than every engine source, reusing it.")
            return
        print(f"==> Sidecar at {dst_exe} is STALE (older than {src_path}), rebuilding.")

    # Run PyInstaller
    print("==> Building Python sidecar…")
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", SPEC_FILE, "--noconfirm"],
        check=True,
    )

    os.makedirs(SIDECAR_DIR, exist_ok=True)

    # Copy the entry-point exe
    src_exe = os.path.join(SIDECAR_SRC, "mkw-tracker-engine.exe")
    shutil.copy2(src_exe, dst_exe)
    print(f"    {src_exe} → {dst_exe}")

    # Copy the _internal/ dependency directory that PyInstaller places next to the exe
    src_internal = os.path.join(SIDECAR_SRC, "_internal")
    dst_internal = os.path.join(SIDECAR_DIR, "_internal")
    if os.path.exists(dst_internal):
        shutil.rmtree(dst_internal)
    shutil.copytree(src_internal, dst_internal)
    print(f"    {src_internal}/ → {dst_internal}/")

    print("==> Sidecar ready.")


if __name__ == "__main__":
    main()
