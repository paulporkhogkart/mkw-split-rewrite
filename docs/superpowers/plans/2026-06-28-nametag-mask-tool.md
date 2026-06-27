# Nametag Mask Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce one canonical soft-alpha mask of the static nametag plate (the dark serrated "tire-track" strip on the select screen) plus a tool to create, verify and validate it.

**Architecture:** A pure-logic Python core (`mask_core.py`) implements the *difference method*: on a clean vehicle (Rally Bike floats above the plate), median plate-present idle frames (`plate over background`) minus median plate-absent flourish frames (`pure background`) isolates the plate, whose `|diff|` magnitude becomes a soft alpha `[0..1]`. A CLI (`build_mask.py`) drives it from the 4K clips via ffmpeg and writes the 4K master + downscaled exports + meta. A tiny static+save server (`serve.py`) plus an HTML viewer (`nametag_mask_tool.html`) overlay the mask on any vehicle/frame to validate, and allow manual nudging/painting as a fallback.

**Tech Stack:** Python 3 (the repo's build python — `cv2` 4.13, `numpy` 2.4, `pytest` 9), `ffmpeg` (already on PATH), plain HTML5 canvas + `http.server`.

## Global Constraints

- **Plate ROI (native 4K, user-set):** `x=2360, y=1602, w=1378, h=226`. Used verbatim everywhere.
- **Resolutions:** 4K master `3840×2160`; 720p export `1280×720`; combo-crop `(x1=700, y1=12, x2=1240, y2=602)` at 720p → `540×590`.
- **Clips:** `captures_sdr/en_uk/clips/<name>.mkv`, 3840×2160 @ 60fps. Anchor clean vehicle = `rally_bike`. Validation overlap vehicle = `b_dasher`.
- **Frame I/O:** extract with `ffmpeg -vsync 0` (frame N in folder == decode-order frame N). NEVER `cv2.set`-seek these HEVC clips. The pre-extracted 720p `temp/asset_eyetest/frames/<veh>/fNNNNN.jpg` are reused by the viewer for overlay.
- **Location:** everything under `temp/asset_eyetest/nametag/` (gitignored, persists), except the viewer HTML at `temp/asset_eyetest/nametag_mask_tool.html`. NOT promoted into `tools/asset_matte/` in this effort.
- **No rembg / no GPU venv.** Core uses only `cv2` + `numpy` from the build python.
- **Scope:** mask only. No un-darkening / transmission recovery.
- All commands below are run from the repo root `C:\development\mkw-split-rewrite` unless stated.

---

### Task 1: Core — plate-presence classification

Classify each frame's plate-ROI crop as plate-present (dark serrated band) vs plate-absent (light floor, during the flourish lift) without a magic threshold — Otsu over the per-frame ROI median-luma series, then majority-smooth.

**Files:**
- Create: `temp/asset_eyetest/nametag/mask_core.py`
- Test: `temp/asset_eyetest/nametag/test_mask_core.py`

**Interfaces:**
- Produces:
  - `PLATE_ROI = (2360, 1602, 1378, 226)`, `FULL_4K = (3840, 2160)`, `FULL_720 = (1280, 720)`, `COMBO_CROP_720 = (700, 12, 1240, 602)` (module constants)
  - `roi_luma_series(rois: list[np.ndarray]) -> np.ndarray` — per-frame median grayscale luma (float64, len == #frames)
  - `classify_presence(luma_series, smooth=5) -> np.ndarray[bool]` — True == plate present (dark)

- [ ] **Step 1: Write the failing test**

```python
# temp/asset_eyetest/nametag/test_mask_core.py
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
import mask_core as mc


def _gray(val, h=20, w=40):
    return np.full((h, w, 3), val, dtype=np.uint8)


def test_roi_luma_series_tracks_brightness():
    rois = [_gray(80), _gray(200), _gray(128)]
    s = mc.roi_luma_series(rois)
    assert s.shape == (3,)
    assert s[0] < s[2] < s[1]


def test_classify_presence_splits_dark_and_light():
    # 60 dark (present) then 40 light (absent, flourish lift)
    series = np.array([80.0] * 60 + [200.0] * 40)
    present = mc.classify_presence(series, smooth=1)
    assert present[:60].all()
    assert not present[60:].any()


def test_classify_presence_majority_smooths_single_blip():
    series = np.array([80.0] * 30 + [200.0] * 1 + [80.0] * 29)  # one bright blip mid-idle
    present = mc.classify_presence(series, smooth=5)
    assert present.all()  # blip absorbed by majority window


def test_classify_presence_degenerate_all_same_is_present():
    present = mc.classify_presence(np.array([120.0] * 10), smooth=1)
    assert present.all()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest temp/asset_eyetest/nametag/test_mask_core.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mask_core'`

- [ ] **Step 3: Write minimal implementation**

```python
# temp/asset_eyetest/nametag/mask_core.py
"""Core logic for the canonical nametag soft-alpha mask (difference method).
Pure cv2 + numpy; no rembg / GPU. See docs/superpowers/plans/2026-06-28-nametag-mask-tool.md
"""
import numpy as np
import cv2

PLATE_ROI = (2360, 1602, 1378, 226)      # x, y, w, h in native 4K
FULL_4K = (3840, 2160)                    # w, h
FULL_720 = (1280, 720)                    # w, h
COMBO_CROP_720 = (700, 12, 1240, 602)     # x1, y1, x2, y2 at 720p -> 540x590


def roi_luma_series(rois):
    """rois: list of HxWx3 BGR uint8 plate-ROI crops -> float64 per-frame median luma."""
    out = []
    for r in rois:
        g = cv2.cvtColor(r, cv2.COLOR_BGR2GRAY)
        out.append(float(np.median(g)))
    return np.asarray(out, dtype=np.float64)


def _majority(mask, win):
    n = len(mask)
    half = win // 2
    out = mask.copy()
    for i in range(n):
        a = max(0, i - half)
        b = min(n, i + half + 1)
        out[i] = mask[a:b].mean() >= 0.5
    return out


def classify_presence(luma_series, smooth=5):
    """Split frames into plate-present(True, dark) / absent(False, light) via Otsu
    on the 1-D luma series, then majority-smooth over `smooth` frames."""
    s = np.asarray(luma_series, dtype=np.float64)
    lo, hi = float(s.min()), float(s.max())
    if hi - lo < 1e-6:
        return np.ones(len(s), dtype=bool)   # degenerate: treat all as present
    remap = ((s - lo) / (hi - lo) * 255.0).astype(np.uint8)
    thr, _ = cv2.threshold(remap, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    present = remap <= thr                    # dark side == plate present
    if smooth and smooth > 1:
        present = _majority(present, smooth)
    return present
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest temp/asset_eyetest/nametag/test_mask_core.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/plans/2026-06-28-nametag-mask-tool.md temp/asset_eyetest/nametag/mask_core.py temp/asset_eyetest/nametag/test_mask_core.py
git commit -m "feat(nametag-mask): core plate-presence classification"
```

(Note: `temp/` is gitignored; the `git add` of the temp files will no-op silently. The plan doc is the tracked artifact. This is intentional — the tool is a prototype that lives in `temp/`. Run the same `git add`/commit each task; only the plan doc commits.)

---

### Task 2: Core — median reduce + difference-to-alpha

Reduce each frame group to a robust median image, then turn `|P − A|` into a soft alpha map (1 under the opaque text/badge, fractional at the serrated edge, 0 clear).

**Files:**
- Modify: `temp/asset_eyetest/nametag/mask_core.py`
- Test: `temp/asset_eyetest/nametag/test_mask_core.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `median_reduce(rois: list[np.ndarray]) -> np.ndarray` — float64 HxWx3 median
  - `diff_to_alpha(P, A, floor=0.05, pct=95.0) -> np.ndarray` — float64 HxW alpha in [0,1]

- [ ] **Step 1: Write the failing test**

```python
# append to temp/asset_eyetest/nametag/test_mask_core.py

def test_median_reduce_is_robust_to_outliers():
    base = _gray(100, h=4, w=4).astype(np.uint8)
    out = _gray(250, h=4, w=4).astype(np.uint8)  # a single outlier frame
    P = mc.median_reduce([base, base, base, out])
    assert P.shape == (4, 4, 3)
    assert abs(P[0, 0, 0] - 100.0) < 1.0


def test_diff_to_alpha_recovers_planted_plate():
    h, w = 40, 80
    A = np.full((h, w, 3), 130.0)             # pure background
    P = A.copy()
    P[10:30, 20:60, :] -= 70.0                # planted dark plate (fully covered core)
    P[9, 20:60, :] -= 35.0                    # one soft edge row (half strength)
    alpha = mc.diff_to_alpha(P, A, floor=0.05, pct=95.0)
    assert alpha.shape == (h, w)
    assert alpha[20, 40] > 0.9                # core ~fully covered
    assert alpha[0, 0] == 0.0                 # clear corner zeroed by floor
    assert 0.2 < alpha[9, 40] < 0.8           # soft edge is fractional


def test_diff_to_alpha_uniform_returns_zero():
    A = np.full((10, 10, 3), 120.0)
    alpha = mc.diff_to_alpha(A.copy(), A, floor=0.05)
    assert alpha.max() == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest temp/asset_eyetest/nametag/test_mask_core.py -k "median_reduce or diff_to_alpha" -v`
Expected: FAIL — `AttributeError: module 'mask_core' has no attribute 'median_reduce'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to temp/asset_eyetest/nametag/mask_core.py

def median_reduce(rois):
    """rois: list of HxWx3 uint8 -> float64 HxWx3 per-pixel median."""
    stack = np.stack([r.astype(np.float64) for r in rois], axis=0)
    return np.median(stack, axis=0)


def diff_to_alpha(P, A, floor=0.05, pct=95.0):
    """P, A: HxWx3 float (plate-over-bg median, pure-bg median).
    Soft alpha [0,1] from per-pixel max-channel |P-A|, normalized by the `pct`
    percentile, floored to suppress noise."""
    d = np.abs(np.asarray(P, float) - np.asarray(A, float)).max(axis=2)
    scale = float(np.percentile(d, pct))
    if scale < 1e-6:
        return np.zeros(d.shape, dtype=np.float64)
    a = np.clip(d / scale, 0.0, 1.0)
    a[a < floor] = 0.0
    return a
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest temp/asset_eyetest/nametag/test_mask_core.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/plans/2026-06-28-nametag-mask-tool.md temp/asset_eyetest/nametag/mask_core.py temp/asset_eyetest/nametag/test_mask_core.py
git commit -m "feat(nametag-mask): median reduce + difference-to-alpha"
```

---

### Task 3: Core — aggregate + canvas/export helpers

Combine per-vehicle alpha maps, place the ROI strip into a full-frame canvas, and derive the 720p + combo-crop exports.

**Files:**
- Modify: `temp/asset_eyetest/nametag/mask_core.py`
- Test: `temp/asset_eyetest/nametag/test_mask_core.py`

**Interfaces:**
- Consumes: `PLATE_ROI`, `FULL_4K`, `FULL_720`, `COMBO_CROP_720`.
- Produces:
  - `aggregate(alphas: list[np.ndarray]) -> np.ndarray` — median across vehicles (single == identity)
  - `to_u8(alpha: np.ndarray) -> np.ndarray` — uint8 [0,255]
  - `place_in_canvas(alpha_roi, roi=PLATE_ROI, full=FULL_4K) -> np.ndarray` — float64 HxW canvas
  - `make_exports(mask_4k_u8) -> tuple[np.ndarray, np.ndarray]` — (mask_720 uint8 1280×720, combo uint8 540×590)

- [ ] **Step 1: Write the failing test**

```python
# append to temp/asset_eyetest/nametag/test_mask_core.py

def test_aggregate_single_is_identity():
    a = np.linspace(0, 1, 12).reshape(3, 4)
    out = mc.aggregate([a])
    assert np.array_equal(out, a)
    assert out is not a  # must be a copy


def test_aggregate_medians_across_vehicles():
    a = np.zeros((2, 2)); b = np.ones((2, 2)); c = np.full((2, 2), 0.5)
    out = mc.aggregate([a, b, c])
    assert np.allclose(out, 0.5)


def test_place_in_canvas_positions_roi():
    x, y, w, h = mc.PLATE_ROI
    roi = np.ones((h, w))
    canvas = mc.place_in_canvas(roi)
    assert canvas.shape == (mc.FULL_4K[1], mc.FULL_4K[0])
    assert canvas[y + 1, x + 1] == 1.0
    assert canvas[0, 0] == 0.0


def test_make_exports_dims():
    x, y, w, h = mc.PLATE_ROI
    mask4k = mc.to_u8(mc.place_in_canvas(np.ones((h, w))))
    m720, combo = mc.make_exports(mask4k)
    assert m720.shape == (720, 1280)
    assert combo.shape == (590, 540)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest temp/asset_eyetest/nametag/test_mask_core.py -k "aggregate or canvas or exports" -v`
Expected: FAIL — `AttributeError: module 'mask_core' has no attribute 'aggregate'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to temp/asset_eyetest/nametag/mask_core.py

def aggregate(alphas):
    """Median across per-vehicle alpha maps (same shape). Single map -> copy."""
    if len(alphas) == 1:
        return alphas[0].copy()
    return np.median(np.stack(alphas, axis=0), axis=0)


def to_u8(alpha):
    return np.clip(np.asarray(alpha, float) * 255.0 + 0.5, 0, 255).astype(np.uint8)


def place_in_canvas(alpha_roi, roi=PLATE_ROI, full=FULL_4K):
    x, y, w, h = roi
    W, H = full
    canvas = np.zeros((H, W), dtype=np.float64)
    canvas[y:y + h, x:x + w] = alpha_roi
    return canvas


def make_exports(mask_4k_u8):
    """mask_4k_u8: uint8 HxW at 4K -> (mask_720 1280x720, combo 540x590)."""
    m720 = cv2.resize(mask_4k_u8, FULL_720, interpolation=cv2.INTER_AREA)
    x1, y1, x2, y2 = COMBO_CROP_720
    combo = m720[y1:y2, x1:x2]
    return m720, combo
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest temp/asset_eyetest/nametag/test_mask_core.py -v`
Expected: PASS (11 passed)

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/plans/2026-06-28-nametag-mask-tool.md temp/asset_eyetest/nametag/mask_core.py temp/asset_eyetest/nametag/test_mask_core.py
git commit -m "feat(nametag-mask): aggregate + canvas/export helpers"
```

---

### Task 4: CLI — `build_mask.py` (extraction + full pipeline + recompose)

Drive the core from real 4K clips: extract the plate ROI via ffmpeg, classify, median, diff, aggregate, write the 4K master strip + full canvas + 720p + combo exports + meta. Also support `--from-roi` to recompose exports from a hand-edited strip (used by the viewer's save).

**Files:**
- Create: `temp/asset_eyetest/nametag/build_mask.py`
- Output dir (created at runtime): `temp/asset_eyetest/nametag_mask/`

**Interfaces:**
- Consumes: `mask_core` (`PLATE_ROI`, `roi_luma_series`, `classify_presence`, `median_reduce`, `diff_to_alpha`, `aggregate`, `to_u8`, `place_in_canvas`, `make_exports`).
- Produces (files in `temp/asset_eyetest/nametag_mask/`):
  - `nametag_mask_roi4k.png` (1378×226, grayscale — the editable master strip)
  - `nametag_mask_4k.png` (3840×2160), `nametag_mask_720.png` (1280×720), `nametag_mask_combo540.png` (540×590)
  - `nametag_mask_meta.json`

- [ ] **Step 1: Write the implementation**

```python
# temp/asset_eyetest/nametag/build_mask.py
#!/usr/bin/env python
"""Build the canonical nametag soft-alpha mask via the difference method.

  # derive from clean vehicle(s):
  python temp/asset_eyetest/nametag/build_mask.py --clips rally_bike
  python temp/asset_eyetest/nametag/build_mask.py --clips rally_bike standard_bike

  # recompose exports from a hand-edited master strip (viewer save):
  python temp/asset_eyetest/nametag/build_mask.py --from-roi temp/asset_eyetest/nametag_mask/nametag_mask_roi4k.png

Run from the repo root.
"""
import argparse, json, os, subprocess, sys
import numpy as np
import cv2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mask_core as mc

CLIP_DIR = "captures_sdr/en_uk/clips"
OUT_DIR = "temp/asset_eyetest/nametag_mask"
WORK_DIR = "temp/asset_eyetest/nametag/_work"


def extract_roi_frames(clip_path, roi, tmp_dir):
    """ffmpeg -vsync 0 crop the plate ROI to PNGs under tmp_dir; return sorted paths."""
    x, y, w, h = roi
    os.makedirs(tmp_dir, exist_ok=True)
    for f in os.listdir(tmp_dir):
        if f.endswith(".png"):
            os.remove(os.path.join(tmp_dir, f))
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", clip_path,
           "-vf", f"crop={w}:{h}:{x}:{y}", "-vsync", "0",
           os.path.join(tmp_dir, "f%05d.png")]
    subprocess.run(cmd, check=True)
    return [os.path.join(tmp_dir, f) for f in sorted(os.listdir(tmp_dir)) if f.endswith(".png")]


def even_sample(items, k):
    """Up to k items, evenly spaced, order preserved."""
    if len(items) <= k:
        return list(items)
    idx = np.linspace(0, len(items) - 1, k).round().astype(int)
    return [items[i] for i in sorted(set(idx.tolist()))]


def alpha_for_clip(clip_name, max_per_group=150):
    clip_path = os.path.join(CLIP_DIR, clip_name + ".mkv")
    if not os.path.exists(clip_path):
        sys.exit(f"clip not found: {clip_path}")
    tmp = os.path.join(WORK_DIR, clip_name)
    paths = extract_roi_frames(clip_path, mc.PLATE_ROI, tmp)
    if not paths:
        sys.exit(f"no frames extracted from {clip_path}")
    # luma series (read grayscale, cheap)
    luma = np.array([float(np.median(cv2.imread(p, cv2.IMREAD_GRAYSCALE))) for p in paths])
    present = mc.classify_presence(luma, smooth=5)
    pres_paths = [p for p, f in zip(paths, present) if f]
    abs_paths = [p for p, f in zip(paths, present) if not f]
    print(f"[{clip_name}] {len(paths)} frames -> {len(pres_paths)} present / {len(abs_paths)} absent")
    if not pres_paths or not abs_paths:
        sys.exit(f"[{clip_name}] need BOTH plate-present and plate-absent frames; "
                 f"got present={len(pres_paths)} absent={len(abs_paths)}. "
                 f"Is this a clean (non-overlapping) vehicle with a flourish in-clip?")
    P = mc.median_reduce([cv2.imread(p) for p in even_sample(pres_paths, max_per_group)])
    A = mc.median_reduce([cv2.imread(p) for p in even_sample(abs_paths, max_per_group)])
    return mc.diff_to_alpha(P, A), len(pres_paths), len(abs_paths)


def write_outputs(alpha_roi, meta):
    os.makedirs(OUT_DIR, exist_ok=True)
    roi_u8 = mc.to_u8(alpha_roi)
    cv2.imwrite(os.path.join(OUT_DIR, "nametag_mask_roi4k.png"), roi_u8)
    mask4k = mc.to_u8(mc.place_in_canvas(alpha_roi))
    cv2.imwrite(os.path.join(OUT_DIR, "nametag_mask_4k.png"), mask4k)
    m720, combo = mc.make_exports(mask4k)
    cv2.imwrite(os.path.join(OUT_DIR, "nametag_mask_720.png"), m720)
    cv2.imwrite(os.path.join(OUT_DIR, "nametag_mask_combo540.png"), combo)
    with open(os.path.join(OUT_DIR, "nametag_mask_meta.json"), "w") as f:
        json.dump(meta, f, indent=2)
    print("wrote outputs to", OUT_DIR)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clips", nargs="*", default=["rally_bike"],
                    help="clean (non-overlapping) vehicle clip names under " + CLIP_DIR)
    ap.add_argument("--from-roi", default=None,
                    help="recompose exports from an existing master strip PNG (skip derivation)")
    ap.add_argument("--floor", type=float, default=0.05)
    ap.add_argument("--max-per-group", type=int, default=150)
    args = ap.parse_args()

    if args.from_roi:
        roi_img = cv2.imread(args.from_roi, cv2.IMREAD_GRAYSCALE)
        if roi_img is None:
            sys.exit("could not read --from-roi: " + args.from_roi)
        alpha_roi = roi_img.astype(np.float64) / 255.0
        write_outputs(alpha_roi, {"method": "from-roi", "source": args.from_roi,
                                  "plate_roi": list(mc.PLATE_ROI)})
        return

    alphas = []
    counts = {}
    for name in args.clips:
        a, np_, na_ = alpha_for_clip(name, args.max_per_group)
        # re-apply floor at the requested level (alpha_for_clip used the default)
        a = a.copy(); a[a < args.floor] = 0.0
        alphas.append(a)
        counts[name] = {"present": np_, "absent": na_}
    mask = mc.aggregate(alphas)
    write_outputs(mask, {
        "method": "difference", "clips": args.clips, "counts": counts,
        "plate_roi": list(mc.PLATE_ROI), "full_4k": list(mc.FULL_4K),
        "full_720": list(mc.FULL_720), "combo_crop_720": list(mc.COMBO_CROP_720),
        "floor": args.floor, "max_per_group": args.max_per_group,
    })


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the integration build on Rally Bike**

Run: `python temp/asset_eyetest/nametag/build_mask.py --clips rally_bike`
Expected: prints `[rally_bike] 885 frames -> NNN present / MMM absent` with **both** counts > 0, then `wrote outputs to temp/asset_eyetest/nametag_mask`. (ffmpeg extraction takes ~10-30s.)

- [ ] **Step 3: Write + run a sanity check on the produced mask**

Create `temp/asset_eyetest/nametag/check_build.py`:

```python
# temp/asset_eyetest/nametag/check_build.py
import os, sys
import numpy as np, cv2
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mask_core as mc

OUT = "temp/asset_eyetest/nametag_mask"
for fn, shape in [("nametag_mask_roi4k.png", (226, 1378)),
                  ("nametag_mask_4k.png", (2160, 3840)),
                  ("nametag_mask_720.png", (720, 1280)),
                  ("nametag_mask_combo540.png", (590, 540))]:
    img = cv2.imread(os.path.join(OUT, fn), cv2.IMREAD_GRAYSCALE)
    assert img is not None, "missing " + fn
    assert img.shape == shape, f"{fn} shape {img.shape} != {shape}"

roi = cv2.imread(os.path.join(OUT, "nametag_mask_roi4k.png"), cv2.IMREAD_GRAYSCALE) / 255.0
cov = (roi > 0.5).mean()
assert 0.05 < cov < 0.95, f"implausible coverage fraction {cov:.3f}"
# top 8 rows of the padded ROI should be ~clear (plate sits below the top margin)
assert roi[:8, :].mean() < 0.15, "top margin not clear -> ROI/derivation suspect"
print(f"OK  coverage={cov:.3f}  top-margin-mean={roi[:8,:].mean():.3f}")
```

Run: `python temp/asset_eyetest/nametag/check_build.py`
Expected: `OK  coverage=0.NN  top-margin-mean=0.0N` (assertions pass)

- [ ] **Step 4: Eyeball the master strip**

Open `temp/asset_eyetest/nametag_mask/nametag_mask_roi4k.png` — it should be a white-ish serrated strip + the *Rally Bike* glyphs + the 1-UP badge on black. (This is the canonical mask; the viewer in Task 6 validates fit on overlapping vehicles.)

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/plans/2026-06-28-nametag-mask-tool.md temp/asset_eyetest/nametag/build_mask.py temp/asset_eyetest/nametag/check_build.py
git commit -m "feat(nametag-mask): build CLI (difference pipeline + recompose)"
```

---

### Task 5: Server — `serve.py` (static + POST save)

A tiny `http.server` that serves `temp/asset_eyetest/` over HTTP (so the viewer's canvas can `getImageData`, which `file://` blocks) and accepts `POST /save` to write an edited mask PNG back to disk. The save logic is factored into a tested helper.

**Files:**
- Create: `temp/asset_eyetest/nametag/serve.py`
- Test: `temp/asset_eyetest/nametag/test_serve.py`

**Interfaces:**
- Produces:
  - `save_png(root: str, rel_path: str, data_url: str) -> str` — decode a `data:image/png;base64,...` (or bare base64) string and write under `root`, guarding path traversal; returns the absolute path written.
  - Running `python temp/asset_eyetest/nametag/serve.py` serves `temp/asset_eyetest/` on `http://127.0.0.1:8777`.

- [ ] **Step 1: Write the failing test**

```python
# temp/asset_eyetest/nametag/test_serve.py
import os, sys, base64, tempfile
import pytest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import serve

# 1x1 transparent PNG
PNG_B64 = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=")


def test_save_png_writes_file(tmp_path):
    out = serve.save_png(str(tmp_path), "sub/mask.png", "data:image/png;base64," + PNG_B64)
    assert os.path.exists(out)
    with open(out, "rb") as f:
        assert f.read(8) == b"\x89PNG\r\n\x1a\n"


def test_save_png_accepts_bare_base64(tmp_path):
    out = serve.save_png(str(tmp_path), "m.png", PNG_B64)
    assert os.path.exists(out)


def test_save_png_rejects_traversal(tmp_path):
    with pytest.raises(ValueError):
        serve.save_png(str(tmp_path), "../escape.png", PNG_B64)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest temp/asset_eyetest/nametag/test_serve.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'serve'`

- [ ] **Step 3: Write minimal implementation**

```python
# temp/asset_eyetest/nametag/serve.py
"""Static file server (temp/asset_eyetest/) + POST /save for the nametag mask tool.
Run from the repo root:  python temp/asset_eyetest/nametag/serve.py
Then open http://127.0.0.1:8777/nametag_mask_tool.html
"""
import base64, json, os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

SERVE_ROOT = os.path.abspath("temp/asset_eyetest")
PORT = 8777


def save_png(root, rel_path, data_url):
    """Decode a base64 PNG (data-URL or bare) and write under `root`. Guards traversal."""
    root = os.path.abspath(root)
    dest = os.path.abspath(os.path.join(root, rel_path))
    if not (dest == root or dest.startswith(root + os.sep)):
        raise ValueError("path traversal rejected: " + rel_path)
    data = base64.b64decode(data_url.split(",")[-1])
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(data)
    return dest


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=SERVE_ROOT, **k)

    def do_POST(self):
        if self.path != "/save":
            self.send_error(404); return
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            dest = save_png(SERVE_ROOT, body["path"], body["png_base64"])
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(("saved " + dest).encode())
        except Exception as e:  # noqa: BLE001 - surface to the browser
            self.send_error(500, str(e))


if __name__ == "__main__":
    print(f"serving {SERVE_ROOT} on http://127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest temp/asset_eyetest/nametag/test_serve.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/plans/2026-06-28-nametag-mask-tool.md temp/asset_eyetest/nametag/serve.py temp/asset_eyetest/nametag/test_serve.py
git commit -m "feat(nametag-mask): static + save server"
```

---

### Task 6: Viewer — `nametag_mask_tool.html`

The create/verify/validate UI. Loads the 720p mask export + a 720p vehicle frame, overlays the mask (alpha-modulated magenta) so the soft serrated edge is visible, lets you scrub frames / switch vehicles / adjust opacity + soft-floor / nudge offset / brush add-erase, and saves the edited **master 4K strip** back via the server. Validation is by eye on an overlapping vehicle (B-Dasher).

**Files:**
- Create: `temp/asset_eyetest/nametag_mask_tool.html`

**Interfaces:**
- Consumes (over HTTP from `serve.py`, paths relative to `temp/asset_eyetest/`):
  - `nametag_mask/nametag_mask_roi4k.png` (master strip, edited), `frames/<veh>/fNNNNN.jpg` (overlay backgrounds).
- Produces: `POST /save` with `{path: "nametag_mask/nametag_mask_roi4k.png", png_base64}`.

The plate ROI in 720p frame space (4K ÷ 3): `x≈786.7, y≈534.0, w≈459.3, h≈75.3` — the viewer derives this from the 4K `PLATE_ROI` constant so it stays in sync.

- [ ] **Step 1: Write the viewer**

```html
<!-- temp/asset_eyetest/nametag_mask_tool.html -->
<!doctype html>
<meta charset="utf-8">
<title>Nametag mask — create / verify / validate</title>
<style>
  body{margin:0;background:#181818;color:#eee;font:13px/1.5 system-ui,sans-serif}
  #bar{position:fixed;top:0;left:0;right:0;background:#222;padding:8px 12px;z-index:9;
       display:flex;flex-wrap:wrap;gap:14px;align-items:center;box-shadow:0 2px 8px #000a}
  #bar label{display:flex;gap:5px;align-items:center}
  #stage{margin-top:64px;position:relative;display:inline-block}
  canvas{display:block;image-rendering:pixelated}
  select,input[type=range]{vertical-align:middle}
  button{background:#ff3df0;border:0;color:#000;font-weight:700;padding:5px 12px;border-radius:5px;cursor:pointer}
  button.alt{background:#3a3a3a;color:#eee}
  #msg{color:#9f9}
  .seg{display:flex;gap:4px}
</style>
<div id="bar">
  <label>vehicle
    <select id="veh">
      <option value="b_dasher_kart">b_dasher_kart (overlap)</option>
      <option value="rally_bike">rally_bike (clean)</option>
      <option value="standard_kart">standard_kart</option>
      <option value="hot_rod">hot_rod</option>
      <option value="big_horn">big_horn</option>
    </select>
  </label>
  <label>frame <input id="frame" type="range" min="1" max="884" value="120"><span id="frameN">120</span></label>
  <label>opacity <input id="op" type="range" min="0" max="100" value="60"></label>
  <label>soft-floor <input id="floor" type="range" min="0" max="100" value="0"></label>
  <label>offset X <input id="ox" type="range" min="-40" max="40" value="0"></label>
  <label>offset Y <input id="oy" type="range" min="-40" max="40" value="0"></label>
  <span class="seg">
    <button id="mOverlay" class="">overlay</button>
    <button id="mEdge" class="alt">edge</button>
    <button id="mMask" class="alt">mask</button>
  </span>
  <span class="seg">
    <button id="bOff" class="">brush off</button>
    <button id="bAdd" class="alt">add</button>
    <button id="bErase" class="alt">erase</button>
    <label>size <input id="bsize" type="range" min="2" max="60" value="18"></label>
  </span>
  <button id="save">Save master strip</button>
  <span id="msg"></span>
</div>
<div id="stage">
  <canvas id="cv" width="1280" height="720"></canvas>
</div>
<script>
// --- constants (kept in sync with mask_core.PLATE_ROI / FULL_4K) ---
const PLATE_ROI_4K = [2360, 1602, 1378, 226];   // x,y,w,h
const SCALE = 1280 / 3840;                        // 4K -> 720p display
const RX = PLATE_ROI_4K[0] * SCALE, RY = PLATE_ROI_4K[1] * SCALE,
      RW = PLATE_ROI_4K[2] * SCALE, RH = PLATE_ROI_4K[3] * SCALE;

const cv = document.getElementById('cv'), ctx = cv.getContext('2d');
const $ = id => document.getElementById(id);

// master strip lives at native ROI res in an offscreen canvas (edits happen here)
const strip = document.createElement('canvas');
strip.width = PLATE_ROI_4K[2]; strip.height = PLATE_ROI_4K[3];
const sctx = strip.getContext('2d', {willReadFrequently:true});

let frameImg = new Image();
let mode = 'overlay', brush = 'off';

function loadStrip(){
  const im = new Image();
  im.onload = () => { sctx.clearRect(0,0,strip.width,strip.height); sctx.drawImage(im,0,0); render(); };
  im.onerror = () => { sctx.clearRect(0,0,strip.width,strip.height); render(); $('msg').textContent='no mask yet — run build_mask.py'; };
  im.src = 'nametag_mask/nametag_mask_roi4k.png?' + Date.now();
}
function loadFrame(){
  const veh = $('veh').value, n = String(+$('frame').value).padStart(5,'0');
  frameImg = new Image();
  frameImg.onload = render;
  frameImg.onerror = () => { ctx.fillStyle='#333'; ctx.fillRect(0,0,1280,720); $('msg').textContent='no frame for this vehicle/index'; };
  frameImg.src = `frames/${veh}/f${n}.jpg?` + Date.now();
}

function render(){
  ctx.clearRect(0,0,1280,720);
  if(mode!=='mask' && frameImg.complete && frameImg.naturalWidth) ctx.drawImage(frameImg,0,0,1280,720);
  // build a tinted overlay from the strip alpha
  const sd = sctx.getImageData(0,0,strip.width,strip.height); // alpha encoded in grayscale R
  const ov = document.createElement('canvas'); ov.width=strip.width; ov.height=strip.height;
  const od = ov.getContext('2d').createImageData(strip.width,strip.height);
  const floor = (+$('floor').value)/100, op = (+$('op').value)/100;
  for(let i=0;i<sd.data.length;i+=4){
    let a = sd.data[i]/255; if(a<floor) a=0;
    if(mode==='mask'){ od.data[i]=od.data[i+1]=od.data[i+2]=Math.round(a*255); od.data[i+3]=255; }
    else if(mode==='edge'){ od.data[i]=255; od.data[i+1]=0; od.data[i+2]=240;
      od.data[i+3]=(a>0.25 && a<0.75)?255:0; }
    else { od.data[i]=255; od.data[i+1]=0; od.data[i+2]=240; od.data[i+3]=Math.round(a*op*255); }
  }
  ov.getContext('2d').putImageData(od,0,0);
  const dx = RX + (+$('ox').value), dy = RY + (+$('oy').value);
  if(mode==='mask'){ ctx.imageSmoothingEnabled=false; ctx.fillStyle='#000'; ctx.fillRect(0,0,1280,720); }
  ctx.drawImage(ov, dx, dy, RW, RH);
  $('frameN').textContent = $('frame').value;
}

// --- brushing on the strip (native ROI coords) ---
function paintAt(clientX, clientY, val){
  const r = cv.getBoundingClientRect();
  const px = (clientX - r.left) * (1280 / r.width);
  const py = (clientY - r.top) * (720 / r.height);
  const dx = RX + (+$('ox').value), dy = RY + (+$('oy').value);
  // display(720) -> strip(native): undo offset, undo ROI origin, undo display scale
  const sx = (px - dx) / RW * strip.width;
  const sy = (py - dy) / RH * strip.height;
  const rad = (+$('bsize').value) / RW * strip.width;
  sctx.globalCompositeOperation = 'source-over';
  sctx.fillStyle = val ? '#fff' : '#000';
  sctx.beginPath(); sctx.arc(sx, sy, rad, 0, 7); sctx.fill();
  render();
}
let painting=false;
cv.addEventListener('mousedown', e=>{ if(brush==='off')return; painting=true; paintAt(e.clientX,e.clientY,brush==='add'); });
window.addEventListener('mousemove', e=>{ if(painting) paintAt(e.clientX,e.clientY,brush==='add'); });
window.addEventListener('mouseup', ()=>painting=false);

// --- wiring ---
['op','floor','ox','oy'].forEach(id=>$(id).oninput=render);
$('frame').oninput=loadFrame;
$('veh').onchange=loadFrame;
function setMode(m){ mode=m; for(const [id,mm] of [['mOverlay','overlay'],['mEdge','edge'],['mMask','mask']]) $(id).className = mm===m?'':'alt'; render(); }
$('mOverlay').onclick=()=>setMode('overlay'); $('mEdge').onclick=()=>setMode('edge'); $('mMask').onclick=()=>setMode('mask');
function setBrush(b){ brush=b; for(const [id,bb] of [['bOff','off'],['bAdd','add'],['bErase','erase']]) $(id).className = bb===b?'':'alt'; }
$('bOff').onclick=()=>setBrush('off'); $('bAdd').onclick=()=>setBrush('add'); $('bErase').onclick=()=>setBrush('erase');

$('save').onclick=async()=>{
  // bake the current soft-floor into the saved strip; offsets are validation-only (not baked)
  const floor=(+$('floor').value)/100;
  const sd=sctx.getImageData(0,0,strip.width,strip.height);
  for(let i=0;i<sd.data.length;i+=4){ let a=sd.data[i]/255; if(a<floor)a=0; const v=Math.round(a*255); sd.data[i]=sd.data[i+1]=sd.data[i+2]=v; sd.data[i+3]=255; }
  const out=document.createElement('canvas'); out.width=strip.width; out.height=strip.height;
  out.getContext('2d').putImageData(sd,0,0);
  const png=out.toDataURL('image/png');
  const r=await fetch('/save',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({path:'nametag_mask/nametag_mask_roi4k.png', png_base64:png})});
  $('msg').textContent = r.ok ? 'saved — now run: build_mask.py --from-roi nametag_mask/nametag_mask_roi4k.png' : ('save failed: '+r.status);
};

loadStrip(); loadFrame();
</script>
```

- [ ] **Step 2: Start the server**

Run (leave it running): `python temp/asset_eyetest/nametag/serve.py`
Expected: `serving .../temp/asset_eyetest on http://127.0.0.1:8777`

- [ ] **Step 3: Open + validate by eye**

Open `http://127.0.0.1:8777/nametag_mask_tool.html`.
- Default vehicle `b_dasher_kart`, frame ~120, **overlay** mode: the magenta tint should sit on the plate strip, NOT on the kart nose/wheels (over-coverage) and should reach the serrated edge (no bare rim — under-coverage).
- Switch to `rally_bike`: the magenta should cover the plate exactly with the bike floating clear above it.
- Try **edge** mode (shows just the soft-edge band) and **mask** mode (the raw alpha).
- Confirm `frame`, `opacity`, `soft-floor`, `offset` sliders respond.

- [ ] **Step 4: Exercise save round-trip**

In the viewer, set **brush → erase**, scrub off a stray speck if any (or just click save unchanged), click **Save master strip** → expect `saved …` message. Then:

Run: `python temp/asset_eyetest/nametag/build_mask.py --from-roi temp/asset_eyetest/nametag_mask/nametag_mask_roi4k.png && python temp/asset_eyetest/nametag/check_build.py`
Expected: `wrote outputs to …` then `OK  coverage=…` (exports regenerated from the edited strip).

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/plans/2026-06-28-nametag-mask-tool.md temp/asset_eyetest/nametag_mask_tool.html
git commit -m "feat(nametag-mask): create/verify/validate viewer"
```

---

## Notes for the implementer

- **Why no test on the viewer/build-extraction:** the difference *math* is fully unit-tested in Tasks 1–3 (synthetic P/A). Tasks 4–6 are integration/manual by nature (ffmpeg, a browser canvas); their checks are the `check_build.py` assertions + the eye-test, matching how every prior asset step in this project was validated.
- **If `--clips rally_bike` reports `absent=0`:** the classifier didn't find flourish (plate-dropped) frames — confirm the clip actually contains the A-press flourish, or widen the clip. The build errors clearly rather than producing a bg-contaminated mask.
- **Adding more clean vehicles:** pass several to `--clips` (e.g. other bikes that float clear); `aggregate` medians them. Different *characters* would vary the background behind the plate and improve separation, but the current footage is Mario-only — Rally Bike alone is sufficient per the spec.
- **Coordinate sync:** `PLATE_ROI` lives in `mask_core.py` and is duplicated as `PLATE_ROI_4K` in the HTML. If the ROI ever changes, update both.
- **Standout-detection fallback (deferred, intentional):** the spec lists an alternate auto-segmentation (dark / low-saturation / serrated-edge on a single clean idle frame) as a fallback. Per the user's "try the difference method first, pursue the others only if it's unsatisfactory," it is **not built in this plan** (YAGNI). If the difference result proves inadequate, it slots in as a new `mask_core.standout_alpha(roi_bgr) -> alpha` function + a method toggle in the viewer's mode segment — no other changes. The **manual paint/offset** fallback *is* built (the brush + offset controls), since it's the cheap universal safety net.
```
