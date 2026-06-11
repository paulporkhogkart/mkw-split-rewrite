"""Harvest grayscale digit templates (0-9) from real race recordings.

Labels come from two trustworthy sources, never from a single raw read:
  * freeze segments with KNOWN final totals (engine-independent truth);
  * running segments, labeled by a robust linear clock fitted to inlier
    reads (median offset + |residual| < 50ms refit) - per-slot harvesting is
    restricted to slots whose predicted digit is mid-period (phase 25-75%),
    so fit error cannot mislabel.

Per digit: register samples to the first sample (+/-3px xcorr), median-stack,
crop the union glyph box, paste centred onto a common canvas (+PAD), save
images/digits/<d>.png + a contact sheet for hand verification.

Run from repo root: python scripts/harvest_digit_templates.py
(Requires the dev clips in temp/ - regen is a dev-machine operation, like
scripts/gen_selection_templates.py.)
"""
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.abspath("."))
from mkw_tracker.race.timestamp import TIMESTAMP_ROIS

OUT_DIR = os.path.join("images", "digits")
PAD = 2
SLOT_PERIOD_MS = {"A": 60_000, "B": 10_000, "C": 1_000, "D": 100}
SLOTS = ("A", "B", "C", "D", "E", "F")

CLIPS = [
    # video, racing segment (s), freeze segment (s), frozen total (ms)
    (os.path.join("temp", "bootest.mp4"), (52.0, 135.0), (138.6, 144.0), 96_713),
    (os.path.join("temp", "koops.mp4"),   (25.0, 111.0), (119.0, 121.5), 98_185),
    (os.path.join("temp", "short.mp4"),   (6.0,  74.0),  None,           None),
]


def total_to_digits(ms):
    a, rest = divmod(ms, 60_000)
    bc, defv = divmod(rest, 1000)
    return [a, bc // 10, bc % 10, defv // 100, (defv // 10) % 10, defv % 10]


def load_old_binary_templates(directory, target_height, binary_thresh=127):
    """The legacy binary loader, pinned here so harvesting labels stay stable
    after the production loader is replaced."""
    from mkw_tracker.utils.paths import resource_path
    templates = {}
    directory = resource_path(directory)
    for filename in sorted(os.listdir(directory)):
        stem = filename[:-4]
        if not filename.endswith(".png") or not (len(stem) == 1 and stem.isdigit()):
            continue
        src = cv2.imread(os.path.join(directory, filename), cv2.IMREAD_GRAYSCALE)
        if src is None:
            continue
        _, binary = cv2.threshold(src, binary_thresh, 255, cv2.THRESH_BINARY)
        coords = cv2.findNonZero(binary)
        x, y, w, h = cv2.boundingRect(coords)
        cropped = binary[y:y + h, x:x + w]
        scale = target_height / h
        scaled = cv2.resize(cropped, (max(1, int(w * scale)), target_height),
                            interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
        _, scaled = cv2.threshold(scaled, 127, 255, cv2.THRESH_BINARY)
        templates[stem] = scaled
    return templates


def old_read_digit(frame, roi, templates, threshold=0.5):
    """Legacy binarize + free-slide read (label fitting only)."""
    x1, y1, x2, y2 = roi
    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        return None
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    _, processed = cv2.threshold(gray, 170, 255, cv2.THRESH_BINARY)
    best_name, best_score = None, 0.0
    for name, tmpl in templates.items():
        if tmpl.shape[0] > processed.shape[0] or tmpl.shape[1] > processed.shape[1]:
            continue
        s = float(cv2.minMaxLoc(cv2.matchTemplate(processed, tmpl,
                                                  cv2.TM_CCOEFF_NORMED))[1])
        if s > best_score:
            best_score, best_name = s, name
    return int(best_name) if (best_name is not None and best_score >= threshold) else None


def read_old_timer_ms(frame, templates):
    vals = []
    for slot in SLOTS:
        d = old_read_digit(frame, TIMESTAMP_ROIS[slot], templates)
        if d is None:
            return None
        vals.append(d)
    a, b, c, d, e, f = vals
    return a * 60_000 + (b * 10 + c) * 1000 + d * 100 + e * 10 + f


def fit_clock(cap, fps, t0, t1, old_templates):
    """Median-offset linear clock over inlier old-matcher reads."""
    offsets = []
    for idx in range(int(t0 * fps), int(t1 * fps), int(fps * 0.25)):
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok:
            continue
        ms = read_old_timer_ms(frame, old_templates)
        if ms is not None:
            offsets.append(ms - idx / fps * 1000.0)
    if len(offsets) < 20:
        return None
    med = float(np.median(offsets))
    inliers = [o for o in offsets if abs(o - med) < 50.0]
    return float(np.median(inliers)) if len(inliers) >= 10 else None


def harvest():
    os.makedirs(OUT_DIR, exist_ok=True)
    samples = {d: [] for d in range(10)}
    old_templates = load_old_binary_templates("images/timestamps/cropped", 42)

    for video, run_seg, frz_seg, frz_ms in CLIPS:
        cap = cv2.VideoCapture(video)
        fps = cap.get(cv2.CAP_PROP_FPS)

        if frz_seg and frz_ms is not None:          # exactly-labeled freeze
            digits = total_to_digits(frz_ms)
            for idx in range(int(frz_seg[0] * fps), int(frz_seg[1] * fps),
                             int(fps * 0.2)):
                cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                ok, frame = cap.read()
                if not ok:
                    continue
                for slot, d in zip(SLOTS, digits):
                    x1, y1, x2, y2 = TIMESTAMP_ROIS[slot]
                    g = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
                    samples[d].append(g)

        offset = fit_clock(cap, fps, run_seg[0], run_seg[1], old_templates)
        print(f"{os.path.basename(video)}: clock offset "
              f"{'%.1fms' % offset if offset is not None else 'UNFIT'}")
        if offset is not None:
            for idx in range(int(run_seg[0] * fps), int(run_seg[1] * fps),
                             int(fps * 0.35)):
                cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                ok, frame = cap.read()
                if not ok:
                    continue
                t_ms = idx / fps * 1000.0 + offset
                for slot in ("A", "B", "C", "D"):
                    period = SLOT_PERIOD_MS[slot]
                    phase = (t_ms % period) / period
                    if not (0.25 <= phase <= 0.75):
                        continue
                    d = total_to_digits(int(t_ms))[SLOTS.index(slot)]
                    x1, y1, x2, y2 = TIMESTAMP_ROIS[slot]
                    g = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
                    samples[d].append(g)
        cap.release()

    # ── register + median-stack per digit ────────────────────────────────────
    stacked = {}
    for d, imgs in samples.items():
        if len(imgs) < 3:
            print(f"digit {d}: only {len(imgs)} samples - SKIPPED")
            continue
        ref = imgs[0].astype(np.float32)
        ref_in = ref[3:-3, 3:-3]
        reg = []
        for g in imgs:
            res = cv2.matchTemplate(g.astype(np.float32), ref_in,
                                    cv2.TM_CCOEFF_NORMED)
            _, _, _, loc = cv2.minMaxLoc(res)
            dx, dy = loc[0] - 3, loc[1] - 3
            M = np.float32([[1, 0, -dx], [0, 1, -dy]])
            reg.append(cv2.warpAffine(g, M, (g.shape[1], g.shape[0]),
                                      borderMode=cv2.BORDER_REPLICATE))
        stacked[d] = np.median(np.stack(reg), axis=0).astype(np.uint8)
        print(f"digit {d}: {len(imgs)} samples")

    # ── common canvas: union glyph box across digits ─────────────────────────
    boxes = {}
    for d, img in stacked.items():
        _, bw = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        coords = cv2.findNonZero(bw)
        boxes[d] = cv2.boundingRect(coords)
    max_w = max(b[2] for b in boxes.values())
    max_h = max(b[3] for b in boxes.values())
    cw, ch = max_w + 2 * PAD, max_h + 2 * PAD
    print(f"canvas {cw}x{ch} (glyph {max_w}x{max_h} + pad {PAD})")

    tiles = []
    for d in sorted(stacked):
        x, y, w, h = boxes[d]
        glyph = stacked[d][y:y + h, x:x + w]
        # background = median border value of the stacked slot, so the canvas
        # pad blends with what surrounds a real glyph
        bg = int(np.median(np.concatenate([stacked[d][0], stacked[d][-1]])))
        canvas = np.full((ch, cw), bg, dtype=np.uint8)
        ox = (cw - w) // 2
        oy = (ch - h) // 2
        canvas[oy:oy + h, ox:ox + w] = glyph
        cv2.imwrite(os.path.join(OUT_DIR, f"{d}.png"), canvas)
        big = cv2.resize(canvas, (cw * 4, ch * 4), interpolation=cv2.INTER_NEAREST)
        cv2.putText(big, str(d), (4, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.7, 255, 2)
        tiles.append(big)
    cv2.imwrite(os.path.join(OUT_DIR, "_sheet.png"), np.hstack(tiles))
    print(f"wrote {len(tiles)} templates + _sheet.png to {OUT_DIR}")


if __name__ == "__main__":
    harvest()
