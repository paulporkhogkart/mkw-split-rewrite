"""Measure idle-animation loop length from the SILHOUETTE, not raw pixels.

`loop_probe` autocorrelates the grayscale hero crop. That works for a bare
character, but on a kart combo the spinning wheel's rotating spokes are strong
periodic *texture* that the grayscale signal locks onto — so the reported period
is the wheel's rotation (~4 s on some karts), not the rider's breathing idle.

This module first reduces each frame to a binary SILHOUETTE (foreground mask) and
autocorrelates a shape signal derived from it. A filled silhouette has no internal
texture, so a spinning wheel becomes a constant filled blob: temporal-mean removal
kills it, and only genuine shape change (breathing, bob, limb sway) survives.

Three signals are computed; the default `mask` (the whole downsampled silhouette)
is the robust one — it captures every kind of idle motion (vertical bob, horizontal
sway, limb/outline change) and matches the grayscale reference periods across all
characters. `centroid` (the silhouette's vertical centre of mass) is the user's
original idea but is a single scalar: it nails vertical-bob idles yet misses idles
that wiggle or rotate without bobbing (e.g. the dolphin), so it under-detects.
`rowprofile` (foreground mass per row) sits in between. Measured on the sample:
`mask` recovers baby_mario 1.67 / dolphin 1.33 / DK 1.67 / koopa 1.00 / mario 1.33 s
at conf 0.75-0.90 (== grayscale loop_probe). On a kart combo the silhouette correctly
REFUSES the wheel period (grayscale locks to the ~4 s wheel) but the kart dominates
the mask, so confidence is low — detect a character's period from its bare idle clip
and reuse it for that character's kart combos.

Silhouette extraction is classical (no GPU): the hero sits on a smooth, desaturated,
blurred background, so Lab distance from the border-ring background colour + Otsu +
largest connected component gives a clean body mask. The analysis window is read
from the clip's <name>.events.json (settled idle = swap_t..flourish_t) so the
spawn-in and the flourish are excluded automatically.

Run:
    python -m mkw_tracker.tools.silhouette_loop captures_sdr/en_uk/clips/dolphin__base.mkv
    python -m mkw_tracker.tools.silhouette_loop captures_sdr/en_uk/clips/*.mkv --signal centroid
    python -m mkw_tracker.tools.silhouette_loop <clip> --dump 8     # write masks to inspect

Reads the clip SEQUENTIALLY (no seeking) — same constraint as the raw feed.
"""
import argparse
import json
import os
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from mkw_tracker.tools.loop_probe import (
    HERO_ROI_1080, scale_roi, find_period, sparkline, expand_paths,
)

# Trim the bottom of the hero ROI: the static name banner + ground strip live there.
# (They are static so temporal-mean removal would drop them anyway, but trimming keeps
# the silhouette to the body and stops the banner anchoring the largest component.)
_BOTTOM_TRIM = 0.10
SIGNALS = ("centroid", "rowprofile", "mask")


# ── silhouette ───────────────────────────────────────────────────────────────
def silhouette(crop: np.ndarray, size: int) -> np.ndarray:
    """Binary foreground mask (size×size uint8 0/1) of the hero in `crop`.

    Lab distance from the border-ring background colour -> Otsu -> largest connected
    component. Background is smooth/desaturated, subject is saturated/sharp, so the
    border ring is reliably background and the body separates cleanly.
    """
    small = cv2.resize(crop, (size, size), interpolation=cv2.INTER_AREA)
    lab = cv2.cvtColor(small, cv2.COLOR_BGR2LAB).astype(np.float32)
    ring = np.ones((size, size), bool)
    b = max(2, size // 16)
    ring[b:-b, b:-b] = False
    bg = np.median(lab[ring], axis=0)
    dist = np.linalg.norm(lab - bg, axis=2)
    d8 = cv2.normalize(dist, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    _, mask = cv2.threshold(d8, 0, 1, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # Close holes: dark interior parts (a kart's frame/wheel) threshold in/out frame-to-frame;
    # that flicker is non-periodic noise that drowns the rider's subtle idle. A morphological
    # close fills it so the silhouette is temporally stable.
    k = max(3, size // 16) | 1                         # odd kernel, scales with resolution
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    n, lbl, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if n > 1:                                          # keep the biggest non-background blob
        big = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        mask = (lbl == big).astype(np.uint8)
    return mask


def signal_from_masks(masks: np.ndarray, signal: str) -> np.ndarray:
    """Reduce stacked masks (N×size×size) to an N×D shape-signal for autocorrelation."""
    n, h, w = masks.shape
    area = masks.reshape(n, -1).sum(1).astype(np.float32)
    if signal == "mask":
        return masks.reshape(n, -1).astype(np.float32)
    if signal == "rowprofile":
        return masks.sum(2).astype(np.float32)         # foreground mass per row
    # centroid: vertical centre of mass (rows). Empty mask -> middle (contributes nothing
    # after mean removal). Breathing bobs this; a spinning wheel is centroid-neutral.
    ys = np.arange(h, dtype=np.float32)[None, :]
    cy = (masks.sum(2).astype(np.float32) * ys).sum(1) / np.where(area > 0, area, 1.0)
    cy[area == 0] = h / 2.0
    return cy[:, None]


# ── decode ───────────────────────────────────────────────────────────────────
def idle_window(path: str, settle_pad: float, end_pad: float) -> Tuple[float, float]:
    """(start_s, end_s) of the settled idle, read from <name>.events.json if present:
    swap_t (spawn-in end) .. flourish_t, padded inward. Falls back to (settle_pad, inf)."""
    ev = os.path.splitext(path)[0] + ".events.json"
    if not os.path.exists(ev):
        return settle_pad, float("inf")
    with open(ev) as fh:
        d = json.load(fh)
    start = (d.get("swap_t") or 0.0) + settle_pad
    end = d.get("flourish_t")
    return start, (end - end_pad if end else float("inf"))


def load_silhouettes(path: str, *, size: int, every: int, settle_pad: float,
                     end_pad: float, bottom_trim: float = _BOTTOM_TRIM,
                     progress: bool = True) -> Tuple[float, np.ndarray]:
    """Decode `path` sequentially within its idle window -> (effective_fps, N×size×size masks)."""
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError(f"cannot open {path!r}")
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 60.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    x1, y1, x2, y2 = scale_roi(HERO_ROI_1080, w, h)
    y2 = y1 + int((y2 - y1) * (1.0 - bottom_trim))
    start_s, end_s = idle_window(path, settle_pad, end_pad)
    start_f, end_f = int(start_s * src_fps), (float("inf") if end_s == float("inf") else int(end_s * src_fps))
    masks: List[np.ndarray] = []
    idx = kept = 0
    while True:
        ret, frame = cap.read()
        if not ret or frame is None or idx > end_f:
            break
        if idx >= start_f and (idx - start_f) % every == 0:
            masks.append(silhouette(frame[y1:y2, x1:x2], size))
            kept += 1
            if progress and kept % 120 == 0:
                print(f"    ...{kept} frames", end="\r", flush=True)
        idx += 1
    cap.release()
    if progress:
        print(" " * 40, end="\r")
    if not masks:
        raise RuntimeError(f"no frames in idle window of {path!r}")
    return src_fps / every, np.stack(masks)


# ── autocorrelation ──────────────────────────────────────────────────────────
def autocorr(X: np.ndarray, lo: int, hi: int) -> Tuple[np.ndarray, np.ndarray]:
    """Normalised cross-correlation of the temporal signal at each lag in [lo, hi] frames.

    Removes the temporal mean (kills the static pose AND any constant blob, e.g. a
    spinning wheel's filled silhouette), then pools over all dims — so it works for a
    1-D centroid or a high-D mask alike. NCC at lag 0 is 1.0; the loop period is the
    first strong peak.
    """
    Xc = X - X.mean(0, keepdims=True)
    n = len(Xc)
    hi = min(hi, n - 1)
    lags = np.arange(lo, hi + 1)
    scores = np.empty(len(lags), dtype=np.float32)
    for i, tau in enumerate(lags):
        a, b = Xc[:n - tau], Xc[tau:]
        denom = np.sqrt(float((a * a).sum()) * float((b * b).sum())) + 1e-9
        scores[i] = float((a * b).sum()) / denom
    return lags, scores


def analyze(path: str, *, size: int, every: int, settle_pad: float, end_pad: float,
            min_period: float, max_period: float,
            signals=SIGNALS) -> dict:
    print(f"  {os.path.basename(path)}: decoding...")
    fps, masks = load_silhouettes(path, size=size, every=every,
                                  settle_pad=settle_pad, end_pad=end_pad)
    lo = max(1, int(min_period * fps))
    hi = int(max_period * fps)
    out = {"path": path, "name": os.path.splitext(os.path.basename(path))[0],
           "fps": fps, "frames": len(masks), "by_signal": {}}
    for sig in signals:
        lags, scores = autocorr(signal_from_masks(masks, sig), lo, hi)
        best, conf, top = find_period(lags, scores)
        out["by_signal"][sig] = {
            "period_s": (best / fps if best else None), "confidence": conf,
            "lags": lags, "scores": scores,
            "top_periods_s": [(t / fps, sc) for t, sc in top],
        }
    return out


def dump_masks(path: str, n: int, size: int, settle_pad: float, end_pad: float, out_dir: str):
    """Write `n` evenly-spaced silhouettes (crop|mask side by side) to inspect mask quality."""
    fps, masks = load_silhouettes(path, size=256, every=1, settle_pad=settle_pad,
                                  end_pad=end_pad, progress=False)
    os.makedirs(out_dir, exist_ok=True)
    name = os.path.splitext(os.path.basename(path))[0]
    pick = np.linspace(0, len(masks) - 1, n).astype(int)
    for i in pick:
        m = (masks[i] * 255).astype(np.uint8)
        cv2.imwrite(os.path.join(out_dir, f"{name}_{i:03d}_mask.png"), m)
    print(f"  dumped {n} masks for {name} -> {out_dir}")


# ── reporting ────────────────────────────────────────────────────────────────
def run(args) -> None:
    try:
        import sys
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    paths = [p for p in expand_paths(args.paths) if os.path.exists(p)]
    results = []
    for path in paths:
        if args.dump:
            dump_masks(path, args.dump, args.size, args.settle_pad, args.end_pad,
                       args.dump_dir or "temp/silhouette_masks")
            continue
        try:
            r = analyze(path, size=args.size, every=args.every, settle_pad=args.settle_pad,
                        end_pad=args.end_pad, min_period=args.min_period, max_period=args.max_period)
        except Exception as e:
            print(f"  [error] {path}: {type(e).__name__}: {e}")
            continue
        results.append(r)
        for sig in SIGNALS:
            s = r["by_signal"][sig]
            p = s["period_s"]
            tag = f"{p:.2f}s conf {s['confidence']:.2f}" if p else "none"
            print(f"    {sig:<10} {tag:<20} [{args.min_period:g}..{args.max_period:g}s] {sparkline(s['scores'])}")

    measured = [r for r in results if r["by_signal"][args.signal]["period_s"]]
    if measured:
        print(f"\n-- Summary (signal: {args.signal}) ------------------------------")
        for r in sorted(measured, key=lambda r: r["name"]):
            s = r["by_signal"][args.signal]
            print(f"  {r['name']:<28} {s['period_s']:>6.2f}s   conf {s['confidence']:.2f}   "
                  f"({r['fps']:.0f}fps x {r['frames']}f)")


def main():
    p = argparse.ArgumentParser(description="Measure idle loop length from the silhouette (wheel-robust).")
    p.add_argument("paths", nargs="+", help="Clip file(s) or glob(s).")
    p.add_argument("--signal", choices=SIGNALS, default="mask",
                   help="Which silhouette signal drives the headline period (default mask — the "
                        "robust one; centroid is the single-scalar variant, weaker on non-bob idles).")
    p.add_argument("--size", type=int, default=64, help="Silhouette resolution NxN (default 64).")
    p.add_argument("--every", type=int, default=2, help="Keep every Nth frame (default 2).")
    p.add_argument("--settle-pad", type=float, default=0.5, dest="settle_pad",
                   help="Skip this many seconds after the spawn-in/window start (default 0.5).")
    p.add_argument("--end-pad", type=float, default=0.3, dest="end_pad",
                   help="Stop this many seconds before the flourish (default 0.3).")
    p.add_argument("--min-period", type=float, default=0.5, dest="min_period", help="Shortest loop, s.")
    p.add_argument("--max-period", type=float, default=6.0, dest="max_period", help="Longest loop, s.")
    p.add_argument("--dump", type=int, default=0, help="Dump N silhouettes per clip to inspect, then exit.")
    p.add_argument("--dump-dir", default=None, dest="dump_dir", help="Where --dump writes (default temp/silhouette_masks).")
    run(p.parse_args())


if __name__ == "__main__":
    main()
