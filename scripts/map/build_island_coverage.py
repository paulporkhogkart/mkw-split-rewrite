#!/usr/bin/env python3
"""Bake the territory island coverage asset for the World Map (SP2).

Reads the hand-traced island mask, ROUNDS the coastline (blur -> re-threshold,
removes the faceting of the 161-segment trace) and FEATHERS the edge (small blur)
into one grayscale coverage PNG. The web client uses it for BOTH the land test
(px > 127) and the anti-aliased coast (px / 255), so it does no mask work at runtime.
Re-run with a different SMOOTH_FRAC to re-tune coastline rounding.

  python scripts/map/build_island_coverage.py
"""
from pathlib import Path
import cv2, numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
SRC = Path(__file__).resolve().parent / "sources" / "island_mask.png"
OUT = ROOT / "web" / "public" / "map" / "island.png"

W = 2200            # match base.jpg frame width
SMOOTH_FRAC = 0.0080   # coastline shape rounding radius (fraction of width)
FEATHER_FRAC = 0.0020  # anti-alias feather radius (fraction of width)

def box(a, r):
    k = 2 * r + 1
    return cv2.blur(a, (k, k), borderType=cv2.BORDER_REPLICATE)

def main():
    src = np.asarray(Image.open(SRC).convert("L"))
    H = round(W * src.shape[0] / src.shape[1])
    a = cv2.resize(src, (W, H), interpolation=cv2.INTER_AREA)
    binary = (a > 127).astype(np.float32)
    rs = max(1, round(SMOOTH_FRAC * W))
    shape = (box(box(binary, rs), rs) >= 0.5).astype(np.float32)   # rounded binary coastline
    rf = max(1, round(FEATHER_FRAC * W))
    cov = np.clip(box(shape, rf), 0.0, 1.0)                        # AA feather
    OUT.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray((cov * 255).astype(np.uint8), "L").save(OUT)
    print(f"wrote {OUT} ({W}x{H}), feather px = {((cov > 0.02) & (cov < 0.98)).sum()}")

if __name__ == "__main__":
    main()
