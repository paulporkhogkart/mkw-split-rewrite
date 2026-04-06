"""
Builds the Python tracker with PyInstaller and copies the result into
src-tauri/binaries/ so Tauri can bundle it as a sidecar.

Called automatically via beforeBuildCommand in tauri.conf.json.
Can also be run directly: python scripts/prepare_sidecar.py
"""

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


def main() -> None:
    dst_exe = os.path.join(SIDECAR_DIR, SIDECAR_EXE)
    dst_internal = os.path.join(SIDECAR_DIR, "_internal")
    if os.path.exists(dst_exe) and os.path.isdir(dst_internal):
        print(f"==> Sidecar already present at {dst_exe}, skipping PyInstaller build.")
        return

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
