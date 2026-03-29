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
BINARIES_DIR = os.path.join("src-tauri", "binaries")
SIDECAR_EXE  = "mkw-tracker-x86_64-pc-windows-msvc.exe"


def main() -> None:
    dst_exe = os.path.join(BINARIES_DIR, SIDECAR_EXE)
    if os.path.exists(dst_exe):
        print(f"==> Sidecar already present at {dst_exe}, skipping PyInstaller build.")
        return

    # Run PyInstaller
    print("==> Building Python sidecar…")
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", SPEC_FILE, "--noconfirm"],
        check=True,
    )

    os.makedirs(BINARIES_DIR, exist_ok=True)

    # Copy the entry-point exe (renamed to include the Rust target triple)
    src_exe = os.path.join(SIDECAR_SRC, "mkw-tracker.exe")
    shutil.copy2(src_exe, dst_exe)
    print(f"    {src_exe} → {dst_exe}")

    # Copy the _internal/ dependency directory that PyInstaller places next to the exe
    src_internal = os.path.join(SIDECAR_SRC, "_internal")
    dst_internal = os.path.join(BINARIES_DIR, "_internal")
    if os.path.exists(dst_internal):
        shutil.rmtree(dst_internal)
    shutil.copytree(src_internal, dst_internal)
    print(f"    {src_internal}/ → {dst_internal}/")

    print("==> Sidecar ready.")


if __name__ == "__main__":
    main()
