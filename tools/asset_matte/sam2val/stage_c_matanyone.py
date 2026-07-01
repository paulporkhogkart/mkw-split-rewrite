"""Stage C (asset-venv-matte, torch MatAnyone2 IN-PROCESS — no birefnet in this process, so no
GPU-monopoly): for each segment, matte forward-only twice (birefnet anchor vs SAM2 anchor) and
write checker-composited PNGs + an anchor-diff image. Run from repo root:

  temp/asset-venv-matte/Scripts/python.exe tools/asset_matte/sam2val/stage_c_matanyone.py
"""
import glob
import os
import sys

import cv2
import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..")))   # tools/asset_matte for flat import
import matte_matanyone as mm                            # torch only; NEVER import matte_blankplate here

OUT = ("C:/Users/Paul/AppData/Local/Temp/claude/C--development-mkw-split-rewrite/"
       "209024a0-084e-4c91-8a28-32378aa23e69/scratchpad/sam2val_out")


def _checker(h, w, s=22, a=205, b=150):
    yy, xx = np.mgrid[0:h, 0:w]
    m = ((xx // s + yy // s) % 2 == 0)
    return np.where(m[..., None], a, b).astype(np.uint8).repeat(3, 2)   # HxWx3 RGB


def _composite(frames_bgr, alphas, dst):
    os.makedirs(dst, exist_ok=True)
    h, w = alphas[0].shape
    chk = _checker(h, w)
    for i, (bgr, al) in enumerate(zip(frames_bgr, alphas)):
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB).astype(np.float32)
        a = np.clip(al, 0, 1)[..., None]
        comp = (chk * (1 - a) + rgb * a).astype(np.uint8)
        cv2.imwrite(os.path.join(dst, f"{i:03d}.png"), cv2.cvtColor(comp, cv2.COLOR_RGB2BGR))


def _anchor_diff(rgb_frame_bgr, biref, sam2, path):
    """biref | sam2 | overlay(red=biref-only, green=sam2-only, yellow=both) on the frame."""
    b = (biref > 0); s = (sam2 > 0)
    over = cv2.cvtColor(rgb_frame_bgr, cv2.COLOR_BGR2RGB).copy()
    over[b & ~s] = [255, 0, 0]; over[s & ~b] = [0, 255, 0]; over[b & s] = [255, 255, 0]
    col = lambda m: cv2.cvtColor((m > 0).astype(np.uint8) * 255, cv2.COLOR_GRAY2RGB)
    sheet = np.concatenate([col(biref), col(sam2), over], axis=1)
    cv2.imwrite(path, cv2.cvtColor(sheet, cv2.COLOR_RGB2BGR))


def run_seg(d):
    name = os.path.basename(d)
    frames = [cv2.imread(p) for p in sorted(glob.glob(os.path.join(d, "frames", "*.png")))]
    biref = np.load(os.path.join(d, "biref_anchor.npy"))
    sam2 = np.load(os.path.join(d, "sam2_anchor.npy"))
    view = os.path.join(OUT, "view", name)
    os.makedirs(view, exist_ok=True)
    for tag, anchor in (("biref", biref), ("sam2", sam2)):
        alphas = mm.matte_segment(frames, anchor, anchor, bidir=False)
        _composite(frames, alphas, os.path.join(view, tag))
        print(f"  {name}/{tag}: {len(alphas)}f", flush=True)
    _anchor_diff(frames[0], biref, sam2, os.path.join(view, "anchors.png"))


def main():
    work = os.path.join(OUT, "work")
    dirs = sorted(d for d in glob.glob(os.path.join(work, "*"))
                  if os.path.exists(os.path.join(d, "sam2_anchor.npy")))
    for d in dirs:
        print(f"--- {os.path.basename(d)}", flush=True)
        run_seg(d)
    print("STAGE_C DONE", flush=True)


if __name__ == "__main__":
    main()
