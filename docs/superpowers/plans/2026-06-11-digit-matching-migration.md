# Digit Matching Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the binarize/free-slide digit matcher with grayscale common-support NCC + margin gate, on templates harvested from real capture, fixing the 8->1 misread for all five digit consumers.

**Architecture:** New grayscale assets in `images/digits/` built by `scripts/harvest_digit_templates.py` (freeze-frame labels from known totals + linear-clock labels for running frames; median-stacked). `race/laps.py` keeps the `load_digit_templates`/`read_digit_roi` seam but matches all ten digits over one common canvas with +/-slack from the slot size, returning `None` on a failed margin. Old binary assets stay on disk for the legacy monolith only. Spec: `docs/superpowers/specs/2026-06-11-digit-matching-migration-design.md`.

**Tech Stack:** Python/OpenCV/numpy/pytest. Validation vs ground truth in `temp/digit_lab.py` (gitignored), finish re-validation via `temp/finish_lab.py`.

**Repo rules:** stage files explicitly; branch `digit-matching-migration`; ff-merge to main at the end. `src-tauri/Cargo.toml` stays untouched (line-endings-only dirt).

---

### Task 1: Branch + commit spec/plan

- [ ] **Step 1:**

```bash
git checkout -b digit-matching-migration
git add docs/superpowers/specs/2026-06-11-digit-matching-migration-design.md docs/superpowers/plans/2026-06-11-digit-matching-migration.md
git commit -m "docs(digits): matching-migration spec + plan

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Harvest script + grayscale templates

**Files:**
- Create: `scripts/harvest_digit_templates.py`
- Create (generated): `images/digits/0.png` .. `9.png` + `images/digits/_sheet.png` (verification contact sheet)

The script needs no labels from the broken reader for the hard cases: freeze
segments carry known totals; running segments get labels from a robust linear
clock fitted on inlier reads of the OLD matcher (majority of digits read
correctly; the fit rejects outliers).

- [ ] **Step 1: Write `scripts/harvest_digit_templates.py`**

```python
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
"""
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.abspath("."))
from mkw_tracker.race.laps import load_digit_templates, read_digit_roi
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


def fit_clock(cap, fps, t0, t1, old_templates):
    """Median-offset linear clock over inlier old-matcher reads."""
    from mkw_tracker.race.timer import read_timer_ms
    offsets = []
    for idx in range(int(t0 * fps), int(t1 * fps), int(fps * 0.25)):
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok:
            continue
        ms = read_timer_ms(frame, old_templates, 0.50)
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
    old_templates = load_digit_templates("images/timestamps/cropped", 42)

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
        canvas = np.zeros((ch, cw), dtype=np.uint8)
        # estimate background as the median border value so the canvas matches
        bg = int(np.median(np.concatenate([stacked[d][0], stacked[d][-1]])))
        canvas[:] = bg
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
```

- [ ] **Step 2: Run and hand-verify**

Run: `python scripts/harvest_digit_templates.py`
Expected: all 10 digits with >= 3 samples (freeze segments alone give 1,3,6,7
+ 8,5; running C/D slots give the rest densely). View `images/digits/_sheet.png`
- every glyph must be the right digit, clean and centred. If a digit is
missing or dirty, widen the running segment sampling step or fix the phase
window before proceeding.

- [ ] **Step 3: Commit**

```bash
git add scripts/harvest_digit_templates.py images/digits/
git commit -m "feat(digits): harvest grayscale digit templates from real capture

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Baseline harness (old matcher vs ground truth)

**Files:**
- Create: `temp/digit_lab.py` (gitignored; numbers go into the spec/commit message)

- [ ] **Step 1: Write the harness**

```python
"""Digit-matcher evaluation vs linear-clock ground truth.

For each clip: fit the clock (same robust method as the harvest), then for
every ~0.1s frame in the racing segment score a matcher on slots A-D (mid-
period phase only) and on freeze frames for all six slots vs the known total.
Reports per-matcher: success rate, wrong rate, confusion pairs, and per-digit
true-score distributions (threshold derivation).

Usage: python temp/digit_lab.py old|new
"""
import os
import sys
from collections import Counter, defaultdict

import cv2
import numpy as np

sys.path.insert(0, os.path.abspath("."))
from mkw_tracker.race.timestamp import TIMESTAMP_ROIS
import mkw_tracker.race.laps as laps_mod

SLOTS = ("A", "B", "C", "D", "E", "F")
SLOT_PERIOD_MS = {"A": 60_000, "B": 10_000, "C": 1_000, "D": 100}
CLIPS = [
    (os.path.join("temp", "bootest.mp4"), (52.0, 135.0), (138.6, 144.0), 96_713),
    (os.path.join("temp", "koops.mp4"),   (25.0, 111.0), (119.0, 121.5), 98_185),
    (os.path.join("temp", "short.mp4"),   (6.0,  74.0),  None,           None),
]


def total_to_digits(ms):
    a, rest = divmod(ms, 60_000)
    bc, defv = divmod(rest, 1000)
    return [a, bc // 10, bc % 10, defv // 100, (defv // 10) % 10, defv % 10]


# Frozen copy of the OLD matcher (binarize + free slide), so the baseline
# survives the production swap.
def old_read_digit_roi(frame, roi, templates, threshold=0.5):
    x1, y1, x2, y2 = roi
    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        return None, 0.0
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
    if best_score < threshold or best_name is None:
        return None, best_score
    return int(best_name), best_score


def old_binary_templates():
    # the old loader, pinned: binary assets at h=42
    import importlib
    return _OLD_LOAD("images/timestamps/cropped", 42)


def main(which):
    if which == "old":
        templates = _OLD_LOAD("images/timestamps/cropped", 42)
        reader = lambda f, r: old_read_digit_roi(f, r, templates, 0.50)
    else:
        templates = laps_mod.load_digit_templates("images/digits", 42)
        reader = lambda f, r: laps_mod.read_digit_roi(f, r, templates,
                                                      threshold=0.60)
    n_total = n_none = n_wrong = 0
    confusion = Counter()
    true_scores = defaultdict(list)
    from mkw_tracker.race.timer import read_timer_ms  # only for clock fitting

    for video, run_seg, frz_seg, frz_ms in CLIPS:
        cap = cv2.VideoCapture(video)
        fps = cap.get(cv2.CAP_PROP_FPS)
        # clock fit with the OLD matcher (label source must not change between runs)
        offsets = []
        oldt = _OLD_LOAD("images/timestamps/cropped", 42)
        for idx in range(int(run_seg[0] * fps), int(run_seg[1] * fps), int(fps * 0.25)):
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, frame = cap.read()
            if not ok:
                continue
            vals = [old_read_digit_roi(frame, TIMESTAMP_ROIS[s], oldt, 0.5)[0]
                    for s in SLOTS]
            if any(v is None for v in vals):
                continue
            a, b, c, d, e, f = vals
            ms = a * 60_000 + (b * 10 + c) * 1000 + d * 100 + e * 10 + f
            offsets.append(ms - idx / fps * 1000.0)
        med = float(np.median(offsets))
        inl = [o for o in offsets if abs(o - med) < 50.0]
        offset = float(np.median(inl))

        def eval_slot(frame, slot, want):
            nonlocal n_total, n_none, n_wrong
            d, s = reader(frame, TIMESTAMP_ROIS[slot])
            n_total += 1
            if d is None:
                n_none += 1
            elif d != want:
                n_wrong += 1
                confusion[(want, d)] += 1
            else:
                true_scores[want].append(s)

        for idx in range(int(run_seg[0] * fps), int(run_seg[1] * fps), int(fps * 0.1)):
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, frame = cap.read()
            if not ok:
                continue
            t_ms = idx / fps * 1000.0 + offset
            for slot in ("A", "B", "C", "D"):
                period = SLOT_PERIOD_MS[slot]
                if not (0.25 <= (t_ms % period) / period <= 0.75):
                    continue
                eval_slot(frame, slot, total_to_digits(int(t_ms))[SLOTS.index(slot)])
        if frz_seg and frz_ms is not None:
            digits = total_to_digits(frz_ms)
            for idx in range(int(frz_seg[0] * fps), int(frz_seg[1] * fps), int(fps * 0.1)):
                cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                ok, frame = cap.read()
                if not ok:
                    continue
                for slot, want in zip(SLOTS, digits):
                    eval_slot(frame, slot, want)
        cap.release()

    print(f"[{which}] reads={n_total} none={n_none} ({100*n_none/n_total:.2f}%) "
          f"wrong={n_wrong} ({100*n_wrong/n_total:.2f}%)")
    for (want, got), n in confusion.most_common(12):
        print(f"  confusion {want}->{got}: {n}")
    for d in sorted(true_scores):
        v = sorted(true_scores[d])
        print(f"  digit {d}: n={len(v)} p01={v[int(0.01*len(v))]:.3f} "
              f"med={v[len(v)//2]:.3f}")


# late-bound old loader pin (works before and after the production swap)
def _OLD_LOAD(directory, target_height, binary_thresh=127):
    import cv2 as _cv2
    import numpy as _np
    from mkw_tracker.utils.paths import resource_path
    templates = {}
    directory = resource_path(directory)
    for filename in sorted(os.listdir(directory)):
        stem = filename[:-4]
        if not filename.endswith(".png") or not (len(stem) == 1 and stem.isdigit()):
            continue
        src = _cv2.imread(os.path.join(directory, filename), _cv2.IMREAD_GRAYSCALE)
        _, binary = _cv2.threshold(src, binary_thresh, 255, _cv2.THRESH_BINARY)
        coords = _cv2.findNonZero(binary)
        x, y, w, h = _cv2.boundingRect(coords)
        cropped = binary[y:y + h, x:x + w]
        scale = target_height / h
        scaled = _cv2.resize(cropped, (max(1, int(w * scale)), target_height),
                             interpolation=_cv2.INTER_AREA if scale < 1 else _cv2.INTER_LINEAR)
        _, scaled = _cv2.threshold(scaled, 127, 255, _cv2.THRESH_BINARY)
        templates[stem] = scaled
    return templates


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "old")
```

- [ ] **Step 2: Run the baseline**

Run: `python temp/digit_lab.py old`
Expected output shape: a wrong-rate visibly > 0 with `8->1` among the top
confusion pairs (koops freeze contributes ~2 slots x ~25 frames of it).
Record the numbers - they go in the final commit message.

---

### Task 4: New matcher (TDD)

**Files:**
- Modify: `mkw_tracker/race/laps.py` (replace `load_digit_templates` + `read_digit_roi`)
- Test: `tests/test_digit_matching.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Grayscale common-support digit matching."""
import cv2
import numpy as np
import pytest

from mkw_tracker.race.laps import load_digit_templates, read_digit_roi

DIGIT_DIR = "images/digits"


@pytest.fixture(scope="module")
def templates():
    t = load_digit_templates(DIGIT_DIR, 42)
    assert len(t) == 10
    return t


def slot_with(tmpl, slot_w=42, slot_h=52, bg=60, jitter=(0, 0)):
    """Place a template into a synthetic slot crop (BGR) at centre + jitter."""
    canvas = np.full((slot_h, slot_w), bg, dtype=np.uint8)
    th, tw = tmpl.shape[:2]
    oy = (slot_h - th) // 2 + jitter[1]
    ox = (slot_w - tw) // 2 + jitter[0]
    canvas[oy:oy + th, ox:ox + tw] = tmpl
    return cv2.cvtColor(canvas, cv2.COLOR_GRAY2BGR)


def test_templates_share_one_canvas(templates):
    shapes = {t.shape for t in templates.values()}
    assert len(shapes) == 1


def test_each_digit_self_matches(templates):
    for name, tmpl in templates.items():
        frame = slot_with(tmpl)
        d, s = read_digit_roi(frame, (0, 0, frame.shape[1], frame.shape[0]),
                              templates, threshold=0.6)
        assert d == int(name), f"digit {name} misread as {d}"
        assert s > 0.9


def test_jitter_tolerated(templates):
    tmpl = templates["8"]
    for j in ((2, 1), (-2, -1), (1, 2)):
        frame = slot_with(tmpl, jitter=j)
        d, _ = read_digit_roi(frame, (0, 0, frame.shape[1], frame.shape[0]),
                              templates, threshold=0.6)
        assert d == 8


def test_crushed_eight_is_eight_or_none_never_one(templates):
    """Washout simulation: crush mid-tones (what binarize-at-170 amplified)."""
    tmpl = templates["8"].astype(np.float32)
    crushed = np.clip((tmpl - 90) * 1.6 + 150, 0, 255).astype(np.uint8)
    frame = slot_with(crushed, bg=170)
    d, _ = read_digit_roi(frame, (0, 0, frame.shape[1], frame.shape[0]),
                          templates, threshold=0.6)
    assert d in (8, None)
    assert d != 1


def test_ambiguous_blend_returns_none(templates):
    blend = cv2.addWeighted(templates["3"], 0.5, templates["9"], 0.5, 0)
    frame = slot_with(blend)
    d, _ = read_digit_roi(frame, (0, 0, frame.shape[1], frame.shape[0]),
                          templates, threshold=0.6)
    assert d is None


def test_reconfirm_fast_path(templates):
    tmpl = templates["5"]
    frame = slot_with(tmpl)
    d, s = read_digit_roi(frame, (0, 0, frame.shape[1], frame.shape[0]),
                          templates, threshold=0.6,
                          reconfirm_digit=5, reconfirm_threshold=0.85)
    assert d == 5 and s >= 0.85
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_digit_matching.py -v`
Expected: failures - the old loader binarizes the new grayscale assets and
the old reader free-slides (`test_templates_share_one_canvas` fails first:
old loader crops each glyph to its own box).

- [ ] **Step 3: Replace loader + reader in `race/laps.py`**

```python
def load_digit_templates(
    directory: str,
    target_height: int,
    blur_ksize: int = 3,
) -> Dict[str, np.ndarray]:
    """Load grayscale common-canvas digit templates, scaled so the glyph is
    ~target_height tall (canvas = glyph + 2*2px pad, see
    scripts/harvest_digit_templates.py). All ten templates share one shape -
    matching compares every candidate over the same support, which is what
    stops a narrow '1' from winning inside a damaged '8'."""
    templates: Dict[str, np.ndarray] = {}
    directory = resource_path(directory)
    if not os.path.exists(directory):
        print(f"[LapTracker] Template directory not found: {directory}")
        return templates
    for filename in sorted(os.listdir(directory)):
        stem = filename[:-4]
        if not filename.lower().endswith('.png') or not (len(stem) == 1 and stem.isdigit()):
            continue
        src = cv2.imread(os.path.join(directory, filename), cv2.IMREAD_GRAYSCALE)
        if src is None:
            continue
        scale = target_height / (src.shape[0] - 4)      # pad = 2 each side
        out_w = max(1, int(round(src.shape[1] * scale)))
        out_h = max(1, int(round(src.shape[0] * scale)))
        interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
        scaled = cv2.resize(src, (out_w, out_h), interpolation=interp)
        templates[stem] = cv2.GaussianBlur(scaled, (blur_ksize, blur_ksize), 0)
    print(f"[LapTracker] Loaded {len(templates)} digit templates "
          f"(glyph h~{target_height}px, canvas {out_h}x{out_w}) from '{directory}'")
    return templates


def read_digit_roi(
    frame: np.ndarray,
    roi: tuple,
    templates: Dict[str, np.ndarray],
    threshold: float = LAP_DIGIT_THRESHOLD,
    binary_thresh: int = 170,            # kept for call-site compatibility; unused
    reconfirm_digit: Optional[int] = None,
    reconfirm_threshold: float = 0.85,
    margin: float = 0.05,
) -> tuple:
    """Match the slot against all digit templates over a common support.

    Grayscale NCC (TM_CCOEFF_NORMED) - gain/offset invariant, survives
    washed capture. Returns (digit, score); (None, best_score) when below
    threshold OR when the winner fails the best-vs-second margin (an
    ambiguous slot is safer unread than guessed: every consumer re-reads)."""
    x1, y1, x2, y2 = roi
    crop = frame[y1:y2, x1:x2]
    if crop.size == 0 or not templates:
        return None, 0.0
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop
    gray = cv2.GaussianBlur(gray, (3, 3), 0)

    def _score(tmpl: np.ndarray) -> float:
        if tmpl.shape[0] > gray.shape[0] or tmpl.shape[1] > gray.shape[1]:
            return 0.0
        result = cv2.matchTemplate(gray, tmpl, cv2.TM_CCOEFF_NORMED)
        return float(cv2.minMaxLoc(result)[1])

    reconfirm_key = str(reconfirm_digit) if reconfirm_digit is not None else None
    if reconfirm_key and reconfirm_key in templates:
        s = _score(templates[reconfirm_key])
        if s >= reconfirm_threshold:
            return reconfirm_digit, s

    scores = sorted(((name, _score(t)) for name, t in templates.items()),
                    key=lambda kv: kv[1], reverse=True)
    best_name, best = scores[0]
    second = scores[1][1] if len(scores) > 1 else 0.0
    if best < threshold or (best - second) < margin:
        return None, best
    return int(best_name), best
```

(The old binary-asset loader logic is deleted; `binary_thresh` stays in the
signature because `coins.py`/`timestamp.py` pass it positionally or by name.)

- [ ] **Step 4: Point the five consumers at the new assets**

In each of `race/laps.py` (LapTracker `digit_dir` default), `race/coins.py`,
`race/timestamp.py`, `race/timer.py`, `race/finish.py` (FinishValueLatch):
`digit_dir: str = 'images/timestamps/cropped'` -> `'images/digits'`.
Grep to confirm none remain: `grep -rn "timestamps/cropped" mkw_tracker/`.

- [ ] **Step 5: Run tests + full suite**

Run: `python -m pytest tests/test_digit_matching.py tests/ -q`
Expected: digit tests pass; full suite passes (test_race_timer mocks reads;
nothing else binds to the binary assets).

- [ ] **Step 6: Commit**

```bash
git add mkw_tracker/race/laps.py mkw_tracker/race/coins.py mkw_tracker/race/timestamp.py mkw_tracker/race/timer.py mkw_tracker/race/finish.py tests/test_digit_matching.py
git commit -m "feat(digits): grayscale common-support NCC matching with margin gate

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Measure, retune thresholds, lock

- [ ] **Step 1: Run both harness arms**

Run: `python temp/digit_lab.py old` then `python temp/digit_lab.py new`
Acceptance: new wrong-rate < 0.2x old wrong-rate; `8->1` count == 0 on the
new arm; none-rate not pathologically higher (some increase is fine - that's
the margin gate converting wrong reads into safe re-reads).

- [ ] **Step 2: Derive thresholds**

From the new arm's per-digit `p01` scores: set per-consumer defaults in
`mkw_tracker/config/defaults.py` (`lap_digit_threshold`,
`coin_digit_threshold`, `timestamp_digit_threshold`) to ~0.10 below the
weakest digit's p01, clamped to [0.45, 0.75]. Update the matching literals in
`race/laps.py` (`LAP_DIGIT_THRESHOLD`), `race/coins.py`
(`COIN_DIGIT_THRESHOLD`), `race/timestamp.py` (`TIMESTAMP_DIGIT_THRESHOLD`),
and the `RaceTimer`/`FinishValueLatch` `digit_threshold` defaults to the same
derived values.

- [ ] **Step 3: Re-run suites + commit**

```bash
python -m pytest tests/ -q
git add mkw_tracker/config/defaults.py mkw_tracker/race/laps.py mkw_tracker/race/coins.py mkw_tracker/race/timestamp.py mkw_tracker/race/timer.py mkw_tracker/race/finish.py
git commit -m "tune(digits): thresholds re-derived from measured score distributions

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Downstream validation + merge

- [ ] **Step 1: Finish latch re-validation**

Run: `python temp/finish_lab.py`
Acceptance: **koops latches 98185ms** (the previously-failing case) with
latency <= 0.6s; bootest still latches 96713ms with latency <= 0.2s; zero
false latches.

- [ ] **Step 2: Full suites**

Run: `python -m pytest tests/ -q` and `npx vitest run`
Expected: both green.

- [ ] **Step 3: Merge**

```bash
git checkout main
git merge digit-matching-migration
python -m pytest tests/ -q
git branch -d digit-matching-migration
```

---

## Self-review notes

- Spec coverage: harvest+labels (Task 2), harness+baseline (Task 3), matcher +
  margin + consumer pointing (Task 4), threshold derivation (Task 5),
  downstream acceptance incl. koops latch (Task 6). Out-of-scope respected.
- Signature consistency: `read_digit_roi(frame, roi, templates, threshold=,
  binary_thresh=, reconfirm_digit=, reconfirm_threshold=, margin=)` keeps the
  old positional shape; `load_digit_templates(directory, target_height,
  blur_ksize=)` replaces `(directory, target_height, binary_thresh=)` - the
  only caller passing `binary_thresh` to the LOADER is checked in Task 4 step 4.
- Risk noted: lap/coin slots are smaller than timer slots; if a scaled canvas
  exceeds a slot crop, `_score` returns 0.0 -> digit unreadable. Task 4 step 5's
  full-suite run plus Task 6's harness catch it; the fix would be reducing the
  pad or the consumer's `digit_h`.
