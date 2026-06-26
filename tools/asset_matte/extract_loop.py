"""Stage A: extract the most seamless one-loop window from a hero clip, cropped to
the character region, as a PNG sequence. Run in the build python (needs cv2)."""
import os
import sys

import cv2
import numpy as np

from mkw_tracker.tools.loop_probe import (
    load_features, autocorr_by_lag, find_period, scale_roi, HERO_ROI_1080,
)

CROP_H = 860   # downscale crop to this height (speed up matting; still crisp)


def extract(clip: str, outdir: str):
    fps, F = load_features(clip, size=48, every=1, settle=0.6, max_seconds=10, progress=False)
    lags, scores = autocorr_by_lag(F, int(0.5 * fps), int(15 * fps))
    P, conf, _ = find_period(lags, scores)
    # Most seamless start: frame s matches s+P, and s+1 matches s+P+1 (velocity).
    N = len(F)
    best_s, best_d = 0, 1e18
    for s in range(0, N - P - 1):
        d = np.sum((F[s] - F[s + P]) ** 2) + np.sum((F[s + 1] - F[s + P + 1]) ** 2)
        if d < best_d:
            best_d, best_s = d, s
    skip = int(0.6 * fps)
    start = skip + best_s

    os.makedirs(outdir, exist_ok=True)
    cap = cv2.VideoCapture(clip)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    x1, y1, x2, y2 = scale_roi(HERO_ROI_1080, w, h)
    scale = CROP_H / (y2 - y1)
    out_w = int(round((x2 - x1) * scale))
    idx = saved = 0
    while saved < P:
        ok, frame = cap.read()
        if not ok:
            break
        if idx >= start:
            crop = frame[y1:y2, x1:x2]
            crop = cv2.resize(crop, (out_w, CROP_H), interpolation=cv2.INTER_AREA)
            cv2.imwrite(os.path.join(outdir, f"{saved:03d}.png"), crop)
            saved += 1
        idx += 1
    cap.release()
    print(f"{os.path.basename(clip)}: period={P}f start={start} saved={saved} crop={out_w}x{CROP_H} @ {w}x{h}")


if __name__ == "__main__":
    base = sys.argv[1]
    for clip in sys.argv[2:]:
        name = os.path.splitext(os.path.basename(clip))[0]
        extract(clip, os.path.join(base, "loopframes", name))
