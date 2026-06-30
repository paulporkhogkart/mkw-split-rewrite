"""Generate the text-free "blank plate" = masked median over one character's per-kart recordings.
Build python (cv2 + numpy, no GPU). This is the permanent generator for the artifact the kart
matte depends on: tools/asset_matte/assets/blank_plate_masked.npy.

Why this works (kart-chip-matte spec / memory kart-chip-matte-pipeline): baby_daisy owns one of
every kart (40 clips). In the nameplate, the serrated "tire-tread" plate + 1-UP badge are CONSTANT
across karts; only the yellow kart-name TEXT changes. So per clip we grab a settled idle frame,
NaN out the saturated-yellow name text, and take np.nanmedian across all karts: the per-kart text
(the "tourists") dissolves, the constant serration + badge (the "landmark") survive -> a clean,
text-free plate. solve_tc(BLANK, clean_bg) then yields the kart-independent un-darken transform.

  python tools/asset_matte/build_blank_plate.py                 # -> assets/blank_plate_masked.npy
  python tools/asset_matte/build_blank_plate.py --char baby_daisy --compare
"""
import argparse
import glob
import json
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nametag_core as nc
import pre_darken as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
ASSETS = os.path.join(_HERE, "assets")

# Committed kart plate mask — the region the text/serration/badge live in.
_, _, _, _MASK = pd.load_template(False)
IN_PLATE = _MASK > 0.05


def idle_frame(clip):
    """A settled idle frame (prod-crop 988x1080). Uses the events.json idle window midpoint
    (swap_t..flourish_t) when present, else the clip midpoint."""
    cap = cv2.VideoCapture(clip)
    if not cap.isOpened():
        return None
    fps = cap.get(cv2.CAP_PROP_FPS) or 60.0
    nframes = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    target = nframes // 2 if nframes else int(6 * fps)
    ev = os.path.splitext(clip)[0] + ".events.json"
    if os.path.exists(ev):
        try:
            d = json.load(open(ev))
            sw, fl = d.get("swap_t"), d.get("flourish_t")
            if sw is not None and fl is not None and fl > sw:
                target = int(((sw + fl) / 2.0) * fps)
        except (OSError, ValueError):
            pass
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, target))
    ok, fr = cap.read()
    cap.release()
    return nc.prod_crop(fr) if ok else None


def nan_yellow_text(frame_bgr):
    """Float copy with the saturated-yellow name text (inside the plate) set to NaN."""
    hsv = cv2.cvtColor(frame_bgr.astype(np.uint8), cv2.COLOR_BGR2HSV)
    H, S, V = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    yellow = (H >= 18) & (H <= 42) & (S > 150) & (V > 150) & IN_PLATE   # OpenCV hue 0..180
    f = frame_bgr.astype(np.float32)
    f[yellow] = np.nan
    return f


def build(clips_dir, char, verbose=True):
    clips = sorted(glob.glob(os.path.join(clips_dir, f"{char}__base__*.mkv")))
    if not clips:
        raise RuntimeError(f"no '{char}__base__*' kart clips in {clips_dir!r}")
    stack = []
    for clip in clips:
        fr = idle_frame(clip)
        if fr is None:
            if verbose:
                print(f"  [skip] {os.path.basename(clip)} (no frame)", flush=True)
            continue
        stack.append(nan_yellow_text(fr))
        if verbose:
            print(f"  {os.path.basename(clip)}", flush=True)
    if verbose:
        print(f"masked-median over {len(stack)} karts ...", flush=True)
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)        # all-NaN slices handled below
        blank = np.nanmedian(np.stack(stack), axis=0)          # per pixel, text NaNs ignored
    nanpx = np.isnan(blank)                                    # text yellow at that pixel in EVERY kart
    if nanpx.any():
        blank[nanpx] = float(np.nanmedian(blank[IN_PLATE]))    # neutral plate grey, not a black hole
    return blank.astype(np.float32)


def main():
    ap = argparse.ArgumentParser(description="Build the text-free blank plate (masked median).")
    ap.add_argument("--clips", default=os.path.join(_REPO, "captures_sdr", "en_uk", "clips"))
    ap.add_argument("--char", default="baby_daisy", help="character that owns one of every kart")
    ap.add_argument("--out", default=os.path.join(ASSETS, "blank_plate_masked.npy"))
    ap.add_argument("--compare", action="store_true", help="diff vs the existing committed artifact")
    a = ap.parse_args()

    blank = build(a.clips, a.char)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    np.save(a.out, blank)
    cv2.imwrite(os.path.splitext(a.out)[0] + ".png", blank.astype(np.uint8))
    print(f"wrote {a.out}  shape={blank.shape} mean={blank.mean():.2f}", flush=True)

    if a.compare:
        ref = os.path.join(_REPO, "temp", "notch_poc", "blank_plate_masked.npy")
        if os.path.exists(ref):
            r = np.load(ref).astype(np.float32)
            d = np.abs(blank - r)
            ip = d[IN_PLATE]
            print(f"vs {os.path.relpath(ref, _REPO)}: mean abs diff={d.mean():.2f}  "
                  f"in-plate mean={ip.mean():.2f}  in-plate p99={np.percentile(ip, 99):.1f}", flush=True)


if __name__ == "__main__":
    main()
