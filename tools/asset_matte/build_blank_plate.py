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


# ── char-screen mode: blank_plate_char + clean_bg_char from the standalone captures ────
import extract_loop as el

CHAR_BODY_DILATE = 31
BG_WIN = 3          # bg frames per clip: [cut-5, cut-2) — plate gone, scene up, kart-tag not yet in


def standalone_names(clips_dir):
    return sorted(n for n in (os.path.splitext(os.path.basename(p))[0]
                              for p in glob.glob(os.path.join(clips_dir, "*.mkv")))
                  if len(n.split("__")) == 2)


def nan_body(f32_frame, alpha_png_path, dilate=CHAR_BODY_DILATE):
    """NaN the character body (matte alpha>10, dilated) in-place; silent no-op if absent."""
    m = cv2.imread(alpha_png_path, cv2.IMREAD_UNCHANGED)
    if m is None or m.ndim != 3 or m.shape[2] < 4:
        return
    body = cv2.dilate((m[..., 3] > 10).astype(np.uint8),
                      np.ones((dilate, dilate), np.uint8)).astype(bool)
    f32_frame[body] = np.nan


def char_cut_for(clip, cache):
    """Hard-cut frame for a standalone char clip, via cache else find_segments (17s decode).
    None when the flourish fell back (no cut) — the clip is skipped for the bg build."""
    name = os.path.splitext(os.path.basename(clip))[0]
    if name in cache:
        return cache[name]
    segs, _fps, _kart, fell_back, _res = el.find_segments(clip)
    cache[name] = None if fell_back else segs["flourish"][1] + el.CHAR_CUT_GUARD
    return cache[name]


def _seq_frames(clip, idxs):
    """Sequential decode (NO seek — unreliable on these HEVC clips) -> {idx: prod-crop bgr}."""
    idxs = sorted(set(idxs))
    cap = cv2.VideoCapture(clip)
    got, k = {}, 0
    while idxs and k <= idxs[-1]:
        ok, fr = cap.read()
        if not ok:
            break
        if k in idxs:
            got[k] = nc.prod_crop(fr)
        k += 1
    cap.release()
    return got


def finish_median(stack, in_plate):
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        out = np.nanmedian(np.stack(stack), axis=0).astype(np.float32)
    nanpx = np.isnan(out[..., 0])
    if nanpx.any():
        out[nanpx] = np.nanmedian(out[in_plate], axis=0)
    return out


def build_char(a):
    _, _, _, mask_c = pd.load_template(True)
    in_plate_c = mask_c > 0.05
    names = standalone_names(a.clips)
    if not names:
        raise RuntimeError(f"no standalone char clips in {a.clips!r}")
    cuts_path = os.path.join(ASSETS, "char_cuts.json")
    cache = {}
    if os.path.exists(cuts_path):
        cache = json.load(open(cuts_path))

    blank_stack, bg_stack, skipped_bg = [], [], []
    for i, name in enumerate(names):
        clip = os.path.join(a.clips, name + ".mkv")
        fr = idle_frame(clip)
        if fr is not None:
            f = fr.astype(np.float32)
            f[nc.yellow_text_mask(fr) & in_plate_c] = np.nan
            nan_body(f, os.path.join(a.matte_dir, f"{name}__idle_frames", "000.png"))
            blank_stack.append(f.astype(np.float16))
        cut = char_cut_for(clip, cache)
        if cut is None:
            skipped_bg.append(name)
        else:
            got = _seq_frames(clip, list(range(cut - 5, cut - 2)))
            fdir = os.path.join(a.matte_dir, f"{name}__flourish_frames")
            npng = len(glob.glob(os.path.join(fdir, "*.png")))
            for j, gi in enumerate(sorted(got)):
                f = got[gi].astype(np.float32)
                if npng >= BG_WIN:
                    nan_body(f, os.path.join(fdir, f"{npng - BG_WIN + j:03d}.png"))
                bg_stack.append(f.astype(np.float16))
        if (i + 1) % 10 == 0 or i + 1 == len(names):
            json.dump(cache, open(cuts_path + ".tmp", "w"), indent=0)
            os.replace(cuts_path + ".tmp", cuts_path)
            print(f"  {i + 1}/{len(names)} (bg-skipped: {len(skipped_bg)})", flush=True)

    blank = finish_median(blank_stack, in_plate_c)
    bg = finish_median(bg_stack, in_plate_c)
    for label, arr in (("blank_plate_char", blank), ("clean_bg_char", bg)):
        np.save(os.path.join(ASSETS, f"{label}.npy"), arr)
        cv2.imwrite(os.path.join(ASSETS, f"{label}.png"), np.clip(arr, 0, 255).astype(np.uint8))
    tmed = float(np.median(nc.solve_tc(blank, bg.astype(np.float64))[0][in_plate_c]))
    print(f"blank={len(blank_stack)} frames  bg={len(bg_stack)} frames  "
          f"bg-skipped={skipped_bg}  T_B median={tmed:.3f}", flush=True)
    if not (0.3 < tmed < 1.0):
        raise RuntimeError(f"T_B median {tmed:.3f} outside sanity band (0.3, 1.0) — stale/mismatched inputs?")
    meta_p = os.path.join(ASSETS, "templates_meta.json")
    meta = json.load(open(meta_p)) if os.path.exists(meta_p) else {}
    import datetime
    meta["char_blank"] = {"date": datetime.date.today().isoformat(),
                          "clips": len(blank_stack), "bg_clips": len(bg_stack) // BG_WIN}
    json.dump(meta, open(meta_p, "w"), indent=2)


def main():
    ap = argparse.ArgumentParser(description="Build the text-free blank plate (masked median).")
    ap.add_argument("--clips", default=os.path.join(_REPO, "captures_sdr", "en_uk", "clips"))
    ap.add_argument("--char", default="baby_daisy", help="character that owns one of every kart")
    ap.add_argument("--out", default=os.path.join(ASSETS, "blank_plate_masked.npy"))
    ap.add_argument("--compare", action="store_true", help="diff vs the existing committed artifact")
    ap.add_argument("--screen", choices=("kart", "char"), default="kart",
                    help="kart: baby_daisy 40-kart blank (default, unchanged). "
                         "char: blank_plate_char + clean_bg_char from ALL standalone clips")
    ap.add_argument("--matte-dir", default=r"D:\kartoff\asset_chips\matte",
                    help="char mode: current mattes, for body-exclusion alphas")
    a = ap.parse_args()

    if a.screen == "char":
        build_char(a)
        return

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
