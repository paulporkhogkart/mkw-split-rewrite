"""Measure the idle-animation loop length of a recorded hero clip.

For the asset-capture effort we need to know how long each character/costume's
idle animation takes to loop, so we can decide how long to dwell/record on each.
This probes that empirically: given one or more clips (recorded with
`record_clips` while hovering a character on the select screen), it crops the
hero render, builds a per-frame motion signal, and finds the dominant period via
self-similarity (autocorrelation of the temporal residual).

It reports each clip's detected loop length (seconds) with a confidence score and
an ASCII similarity curve, then a summary that says whether the loops are
consistent across clips or vary per character.

Run:
    python -m mkw_tracker.tools.loop_probe temp/clips/mario_base.mkv
    python -m mkw_tracker.tools.loop_probe temp/clips/*.mkv
    python -m mkw_tracker.tools.loop_probe temp/clips/bowser.mkv --max-period 18 --csv out.csv

Notes:
- The clip is read SEQUENTIALLY (no seeking) — same constraint as the raw feed.
- The hero ROI defaults to the character-select hero region in 1080p coords and is
  scaled to whatever resolution the clip actually is (so 4K clips Just Work).
- Decoding 4K frames in Python is slow; use --every 2 to halve it (still fine for
  periods >= ~0.4 s), or --max-seconds to cap how much of the clip is analysed.
"""
import argparse
import glob
import os
from typing import List, Optional, Tuple

import cv2
import numpy as np

# Character-select hero render, 1080p reference coords (x1, y1, x2, y2).
# Scaled to the clip's real resolution at read time.
HERO_ROI_1080 = (1075, 30, 1800, 845)
_REF_W, _REF_H = 1920, 1080


def scale_roi(roi_1080: Tuple[int, int, int, int], w: int, h: int) -> Tuple[int, int, int, int]:
    """Scale a 1080p-reference ROI to a frame of size (w, h)."""
    sx, sy = w / _REF_W, h / _REF_H
    x1, y1, x2, y2 = roi_1080
    return (max(0, int(x1 * sx)), max(0, int(y1 * sy)),
            min(w, int(x2 * sx)), min(h, int(y2 * sy)))


def frame_feature(frame: np.ndarray, roi_1080: Tuple[int, int, int, int], size: int) -> np.ndarray:
    """Crop the hero ROI, grayscale, downsample to size×size, return a flat float32 vector."""
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = scale_roi(roi_1080, w, h)
    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        crop = frame
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (size, size), interpolation=cv2.INTER_AREA)
    return small.astype(np.float32).reshape(-1)


def load_features(path: str, roi_1080=HERO_ROI_1080, size: int = 48, every: int = 1,
                  settle: float = 0.6, max_seconds: float = 45.0,
                  progress: bool = True) -> Tuple[float, np.ndarray]:
    """Decode `path` sequentially -> (effective_fps, N×D feature matrix).

    `every` keeps every Nth frame (effective_fps = src_fps / every). `settle`
    drops the first seconds. `max_seconds` caps analysed source duration.
    """
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError(f"cannot open {path!r}")
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 60.0
    skip = int(settle * src_fps)
    max_src_frames = int(max_seconds * src_fps)
    feats: List[np.ndarray] = []
    idx = 0
    kept = 0
    while idx < skip + max_src_frames:
        ret, frame = cap.read()
        if not ret or frame is None:
            break
        if idx >= skip and (idx - skip) % every == 0:
            feats.append(frame_feature(frame, roi_1080, size))
            kept += 1
            if progress and kept % 120 == 0:
                print(f"    ...{kept} frames", end="\r", flush=True)
        idx += 1
    cap.release()
    if progress:
        print(" " * 40, end="\r")
    if not feats:
        raise RuntimeError(f"no frames decoded from {path!r}")
    return src_fps / every, np.stack(feats)


def temporal_residual(F: np.ndarray) -> np.ndarray:
    """Subtract the per-pixel temporal mean (kill the static pose) and unit-normalise each row.

    The static character pose is constant across frames, so it carries no period
    information and just inflates every lag's similarity. Removing it leaves the
    moving pixels, whose self-similarity peaks sharply at the loop period.
    """
    R = F - F.mean(axis=0, keepdims=True)
    norms = np.linalg.norm(R, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return R / norms


def autocorr_by_lag(F: np.ndarray, lo: int, hi: int) -> Tuple[np.ndarray, np.ndarray]:
    """Mean cosine self-similarity of the temporal residual at each lag in [lo, hi] frames."""
    Rhat = temporal_residual(F)
    n = len(Rhat)
    hi = min(hi, n - 1)
    lags = np.arange(lo, hi + 1)
    scores = np.empty(len(lags), dtype=np.float32)
    for i, tau in enumerate(lags):
        a, b = Rhat[:n - tau], Rhat[tau:]
        scores[i] = float(np.einsum("ij,ij->i", a, b).mean())
    return lags, scores


def find_period(lags: np.ndarray, scores: np.ndarray,
                rel: float = 0.9) -> Tuple[Optional[int], float, List[Tuple[int, float]]]:
    """Return (fundamental_lag_frames, confidence, top_peaks).

    The fundamental is the smallest-lag local maximum whose score is within `rel`
    of the global max — so a clean period P wins over its harmonics 2P, 3P (which
    score just as high). Confidence = peak prominence vs the curve's median.
    """
    if len(scores) == 0:
        return None, 0.0, []
    # Local maxima (strictly greater than both neighbours).
    peaks = [i for i in range(1, len(scores) - 1)
             if scores[i] >= scores[i - 1] and scores[i] > scores[i + 1]]
    if not peaks:
        peaks = [int(np.argmax(scores))]
    gmax = float(scores[peaks].max())
    median = float(np.median(scores))
    strong = [i for i in peaks if scores[i] >= rel * gmax]
    best = min(strong) if strong else int(np.argmax(scores))
    confidence = (float(scores[best]) - median) / (1.0 - median + 1e-6)
    top = sorted(((int(lags[i]), float(scores[i])) for i in peaks),
                 key=lambda t: -t[1])[:3]
    return int(lags[best]), max(0.0, min(1.0, confidence)), top


def sparkline(scores: np.ndarray, width: int = 56) -> str:
    """Compact ASCII rendering of the similarity-vs-lag curve."""
    if len(scores) == 0:
        return ""
    blocks = " .:-=+*#"
    if len(scores) > width:
        idx = np.linspace(0, len(scores) - 1, width).astype(int)
        s = scores[idx]
    else:
        s = scores
    lo, hi = float(s.min()), float(s.max())
    rng = hi - lo or 1.0
    return "".join(blocks[min(7, int((v - lo) / rng * 7))] for v in s)


def analyze(path: str, *, size: int, every: int, settle: float,
            min_period: float, max_period: float, max_seconds: float,
            roi_1080=HERO_ROI_1080) -> dict:
    print(f"  {os.path.basename(path)}: decoding...")
    fps, F = load_features(path, roi_1080=roi_1080, size=size, every=every,
                           settle=settle, max_seconds=max_seconds)
    lo = max(1, int(min_period * fps))
    hi = int(max_period * fps)
    lags, scores = autocorr_by_lag(F, lo, hi)
    best, conf, top = find_period(lags, scores)
    period_s = best / fps if best else None
    return {
        "path": path, "name": os.path.splitext(os.path.basename(path))[0],
        "fps": fps, "frames": len(F), "period_s": period_s, "confidence": conf,
        "top_periods_s": [(t / fps, sc) for t, sc in top],
        "lags": lags, "scores": scores,
    }


def expand_paths(patterns: List[str]) -> List[str]:
    out: List[str] = []
    for p in patterns:
        hits = glob.glob(p)
        out.extend(sorted(hits) if hits else [p])
    return out


def run(args) -> None:
    try:                                              # never crash on console encoding
        import sys
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    paths = expand_paths(args.paths)
    results = []
    for path in paths:
        if not os.path.exists(path):
            print(f"  [skip] not found: {path}")
            continue
        try:
            r = analyze(path, size=args.size, every=args.every, settle=args.settle,
                        min_period=args.min_period, max_period=args.max_period,
                        max_seconds=args.max_seconds)
        except Exception as e:
            print(f"  [error] {path}: {type(e).__name__}: {e}")
            continue
        results.append(r)
        if r["period_s"]:
            f = r["period_s"]
            lags_s = r["lags"] / r["fps"]
            h2 = float(r["scores"][int(np.argmin(np.abs(lags_s - 2 * f)))])
            h3 = float(r["scores"][int(np.argmin(np.abs(lags_s - 3 * f)))])
            tag = "clean loop" if (h2 > 0.7 and h3 > 0.7) else "CHECK: weak harmonics"
            print(f"    loop ~ {f:.2f}s  ({tag}: 2x={h2:.2f} 3x={h3:.2f}; "
                  f"conf {r['confidence']:.2f}, {r['fps']:.0f}fps x {r['frames']}f)")
            print(f"    curve [{args.min_period:g}..{args.max_period:g}s] {sparkline(r['scores'])}")
        else:
            print("    no period found")

    if args.csv and results:
        with open(args.csv, "w") as fh:
            fh.write("clip,lag_seconds,similarity\n")
            for r in results:
                for lag, sc in zip(r["lags"], r["scores"]):
                    fh.write(f"{r['name']},{lag / r['fps']:.4f},{sc:.5f}\n")
        print(f"\n  wrote {args.csv}")

    measured = [r for r in results if r["period_s"]]
    if len(measured) >= 1:
        print("\n-- Summary ------------------------------")
        for r in sorted(measured, key=lambda r: r["name"]):
            print(f"  {r['name']:<22} {r['period_s']:>6.2f}s   conf {r['confidence']:.2f}")
        periods = np.array([r["period_s"] for r in measured])
        spread = float(periods.max() - periods.min())
        print(f"\n  range {periods.min():.2f}-{periods.max():.2f}s  "
              f"(spread {spread:.2f}s, mean {periods.mean():.2f}s)")
        if spread <= 0.5:
            print(f"  -> loops look CONSISTENT - a single idle duration of "
                  f"~{np.ceil(periods.max()):.0f}s covers one full loop on all.")
        else:
            print(f"  -> loops VARY by character - use per-character durations, or a "
                  f"uniform ~{np.ceil(periods.max()):.0f}s to cover the longest.")
        print("  (low confidence on any row = re-record it longer, or eyeball the curve.)")


def main():
    p = argparse.ArgumentParser(description="Measure idle-animation loop length from hero clips.")
    p.add_argument("paths", nargs="+", help="Clip file(s) or glob(s), e.g. temp/clips/*.mkv")
    p.add_argument("--size", type=int, default=48, help="Downsample feature size NxN (default 48).")
    p.add_argument("--every", type=int, default=1, help="Keep every Nth frame (default 1; 2 halves decode time).")
    p.add_argument("--settle", type=float, default=0.6, help="Drop the first N seconds (default 0.6).")
    p.add_argument("--min-period", type=float, default=0.5, dest="min_period", help="Shortest loop to consider, s (default 0.5).")
    p.add_argument("--max-period", type=float, default=15.0, dest="max_period", help="Longest loop to consider, s (default 15).")
    p.add_argument("--max-seconds", type=float, default=45.0, dest="max_seconds", help="Cap analysed source duration (default 45).")
    p.add_argument("--csv", default=None, help="Write the full similarity curve to this CSV.")
    run(p.parse_args())


if __name__ == "__main__":
    main()
