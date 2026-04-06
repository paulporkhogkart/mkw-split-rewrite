"""
Generate placeholder Tauri icons from scratch (no PIL/cv2 required).

Run once locally, then commit src-tauri/icons/ to the repo.
Also called by CI before `tauri build` so the build never fails on missing icons.

Usage:
    python scripts/gen_icons.py
"""

import os
import struct
import zlib

ICON_DIR = os.path.join(os.path.dirname(__file__), "..", "src-tauri", "icons")

# MKW-ish blue-orange palette: solid dark blue square
R, G, B = 14, 100, 220  # RGB


def _png_bytes(width: int, height: int, r: int, g: int, b: int) -> bytes:
    """Build a minimal solid-colour RGB PNG in pure Python."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    # filter byte (0 = none) + RGB row, repeated height times
    row = bytes([0] + [r, g, b] * width)
    idat = chunk(b"IDAT", zlib.compress(row * height))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


def _ico_bytes(size: int, r: int, g: int, b: int) -> bytes:
    """Wrap a PNG inside an .ico container (modern Windows supports this)."""
    png = _png_bytes(size, size, r, g, b)
    img_offset = 6 + 16  # ICONDIR(6) + one ICONDIRENTRY(16)
    # ICONDIR
    ico = struct.pack("<HHH", 0, 1, 1)
    # ICONDIRENTRY: w, h, palette, reserved, planes, bit_depth, data_size, data_offset
    w = h = size if size < 256 else 0  # 0 means 256 in the ICO spec
    ico += struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(png), img_offset)
    ico += png
    return ico


def main() -> None:
    os.makedirs(ICON_DIR, exist_ok=True)

    sizes_png = {
        "32x32.png": 32,
        "128x128.png": 128,
        "128x128@2x.png": 256,
    }
    for name, size in sizes_png.items():
        path = os.path.join(ICON_DIR, name)
        if os.path.exists(path):
            print(f"  skipped {path} (already exists)")
            continue
        with open(path, "wb") as f:
            f.write(_png_bytes(size, size, R, G, B))
        print(f"  wrote {path}")

    ico_path = os.path.join(ICON_DIR, "icon.ico")
    if os.path.exists(ico_path):
        print(f"  skipped {ico_path} (already exists)")
    else:
        with open(ico_path, "wb") as f:
            f.write(_ico_bytes(32, R, G, B))
        print(f"  wrote {ico_path}")

    print("Icons generated. Commit src-tauri/icons/ to the repo.")


if __name__ == "__main__":
    main()
