# Asset-Matte Nametag Removal + Un-darkening (productionization) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Promote the validated `temp/asset_eyetest/nametag/` nameplate-removal + un-darkening into the `tools/asset_matte/` pipeline at full resolution, so idle (and later kart-combo) transparent loops have the select-screen nametag plate removed and any subject pixels that dip under it un-darkened — using the user's locked method and params, unchanged.

**Architecture:** The matte pipeline becomes `extract_loop → matte_loop → undark`. `extract_loop` is re-cropped to a nameplate-inclusive region (the validated combo crop) at ~1080 tall. A new `undark` stage loads committed template artifacts (a soft-alpha plate mask + the measured `P,A` background pair, per screen), derives the per-pixel transmission/tint `t,C`, and applies the verbatim-ported `drop_nameplate` + `undark_rgba`. The validated algorithm is **ported, not re-tuned**; only the crop/resolution it runs at is generalized.

**Tech Stack:** Python 3 (build python `python` = Python314 has cv2/numpy; GPU matte venv `temp/asset-venv-gpu/Scripts/python.exe` has cv2/numpy/PIL/rembg), OpenCV, NumPy. Tests run under build python via `pytest` (tools are flat modules; `tests/conftest.py` puts `tools/asset_matte` on `sys.path` — verify and add if missing).

## Global Constraints

- **Method is frozen.** Port `drop_nameplate`, `undark_rgba`, `solve_tc`, `classify_presence`, `median_reduce`, `diff_to_alpha` VERBATIM from `temp/asset_eyetest/nametag/{undark_all,undark_poc,mask_core}.py`. A prior "unified()" rewrite broke B-Dasher and was reverted — do NOT alter the math.
- **Locked params (un-darken):** `ALPHA_GAIN=5.0`, `STRENGTH=1.02`, `CSUB=0.69`, `TFLOOR=0.05`, `T_OPAQUE=0.20`. **Locked gates:** `PRESENT_LUMA=125.0`, `NAMEPLATE_OUT_FRAC=0.30`. Copy these exact values; never re-tune in this plan.
- **Production crop (1080p ref):** `NAMEPLATE_HERO_ROI = (1050, 18, 1860, 903)` → at 4K `(2100, 36, 3720, 1806)`; output size `OUT_W, OUT_H = 988, 1080`. This is the validated combo crop `(700,12,1240,602)@720p ×1.5/×3`.
- **ROIs (native 4K):** kart plate `PLATE_ROI = (2360, 1602, 1378, 226)`; character plate `CHAR_ROI = (2378, 1604, 1178, 226)`.
- **birefnet is 1024² internally** — output resolution above ~1080 does not improve the matte edge; do not raise `OUT_H` past 1440.
- All asset outputs/scratch live under gitignored `temp/`; committed template artifacts live under `tools/asset_matte/assets/` (small PNGs).
- Run all commands from repo root `C:/development/mkw-split-rewrite`.

---

## File Structure

- `tools/asset_matte/nametag_core.py` (NEW) — pure cv2/numpy core: crop constants, `classify_presence`, `median_reduce`, `diff_to_alpha`, `solve_tc`, `place_in_canvas`, `prod_crop` (place an ROI image into the 4K canvas and crop+resize to the production output). Ported from `mask_core.py` + `undark_poc.py`.
- `tools/asset_matte/build_templates.py` (NEW, run-once) — derive the char + kart templates from the surviving `temp/asset_eyetest/nametag/_work/` caches + validated 4K masks; write committed artifacts to `tools/asset_matte/assets/`.
- `tools/asset_matte/assets/` (NEW, committed) — `nametag_char_mask4k.png`, `nametag_kart_mask4k.png`, `char_P.png`, `char_A.png`, `kart_P.png`, `kart_A.png`, `templates_meta.json`.
- `tools/asset_matte/undark.py` (NEW) — load a template (mask+`t`+`C`) at the production crop, `drop_nameplate`, `undark_rgba`, the plate-presence gate, and a `process(base, names, is_char)` driver writing `<base>/matte/<name>_undark/NNN.png` and re-encoding `<name>_undark_loop.webp` / `_checker.webp` / `_apng.png`.
- `tools/asset_matte/extract_loop.py` (MODIFY) — crop the nameplate-inclusive ROI at 1080 tall.
- `tests/test_nametag_core.py` (NEW), `tests/test_undark.py` (NEW).

---

### Task 1: Port the pure-cv2 nametag core

**Files:**
- Create: `tools/asset_matte/nametag_core.py`
- Test: `tests/test_nametag_core.py`

**Interfaces:**
- Produces:
  - Constants `PLATE_ROI`, `CHAR_ROI`, `FULL_4K=(3840,2160)`, `PROD_CROP_4K=(2100,36,3720,1806)`, `OUT_W=988`, `OUT_H=1080`, `NAMEPLATE_HERO_ROI=(1050,18,1860,903)`.
  - `classify_presence(luma_series, smooth=5) -> np.bool_ array`
  - `median_reduce(list_of_HxWx3_uint8) -> HxWx3 float64`
  - `diff_to_alpha(P, A, floor=0.05, pct=95.0) -> HxW float64 [0,1]`
  - `solve_tc(P, A) -> (t HxW float64, C HxW float64)`
  - `place_in_canvas(img_roi, roi) -> 4K canvas` (HxW for 2-D, HxWx3 for 3-D)
  - `prod_crop(canvas_4k) -> OUT_HxOUT_W[...]` (crop `PROD_CROP_4K`, resize to `(OUT_W, OUT_H)`, INTER_AREA)

- [ ] **Step 1: Write failing tests** — `tests/test_nametag_core.py`:

```python
import numpy as np
import tools.asset_matte.nametag_core as nc   # if import fails, see conftest note in plan header

def test_constants():
    assert nc.PROD_CROP_4K == (2100, 36, 3720, 1806)
    assert (nc.OUT_W, nc.OUT_H) == (988, 1080)
    assert nc.CHAR_ROI == (2378, 1604, 1178, 226)

def test_classify_presence_splits_dark_and_light():
    luma = np.array([40, 42, 41, 200, 198, 201, 39, 43])  # dark=present, light=absent
    pres = nc.classify_presence(luma, smooth=1)
    assert pres[:3].all() and pres[6:].all() and not pres[3:6].any()

def test_solve_tc_recovers_planted_transform():
    # A = known background, P = t*A + C with planted t=0.5, C=30 -> solve recovers them.
    rng = np.random.default_rng(0)
    A = rng.uniform(20, 220, (8, 8, 3))
    t_true, C_true = 0.5, 30.0
    P = t_true * A + C_true
    t, C = nc.solve_tc(P, A)
    assert np.allclose(t, t_true, atol=0.05) and np.allclose(C, C_true, atol=3.0)

def test_prod_crop_shape_and_alignment():
    canvas = np.zeros((nc.FULL_4K[1], nc.FULL_4K[0]), np.float64)
    x, y, w, h = nc.CHAR_ROI
    canvas[y:y+h, x:x+w] = 1.0                       # mark the char plate footprint
    out = nc.prod_crop(canvas)
    assert out.shape == (nc.OUT_H, nc.OUT_W)
    assert out.max() > 0.5                            # the footprint survives the crop
    assert out[: nc.OUT_H // 3].max() < 0.5           # and sits in the lower part of the crop

def test_diff_to_alpha_planted_footprint():
    A = np.full((20, 40, 3), 100.0)
    P = A.copy(); P[5:15, 10:30] += 60               # a darker/brighter plate patch
    a = nc.diff_to_alpha(P, A, floor=0.05)
    assert a[10, 20] > 0.8 and a[1, 1] == 0.0
```

- [ ] **Step 2: Run, expect failure**

Run: `python -m pytest tests/test_nametag_core.py -q`
Expected: FAIL (`ModuleNotFoundError: tools.asset_matte.nametag_core` or attribute errors).

- [ ] **Step 3: Implement `tools/asset_matte/nametag_core.py`** (port verbatim from `mask_core.py` + `undark_poc.solve_tc`; add `PROD_CROP_4K`/`prod_crop`):

```python
"""Pure cv2/numpy core for nametag-plate removal, ported from the validated
temp/asset_eyetest/nametag prototype and generalized to the production crop."""
import numpy as np
import cv2

PLATE_ROI = (2360, 1602, 1378, 226)        # kart-screen plate, native 4K x,y,w,h
CHAR_ROI = (2378, 1604, 1178, 226)         # character-screen plate (narrower, no 1-UP badge)
FULL_4K = (3840, 2160)                     # w, h
PROD_CROP_4K = (2100, 36, 3720, 1806)      # x1,y1,x2,y2 — validated combo crop at 4K
OUT_W, OUT_H = 988, 1080                   # production loopframe/matte size
NAMEPLATE_HERO_ROI = (1050, 18, 1860, 903)  # PROD_CROP at 1080p ref (for extract_loop.scale_roi)


def _majority(mask, win):
    n = len(mask); half = win // 2; out = mask.copy()
    for i in range(n):
        out[i] = mask[max(0, i - half):min(n, i + half + 1)].mean() >= 0.5
    return out


def classify_presence(luma_series, smooth=5):
    s = np.asarray(luma_series, dtype=np.float64)
    lo, hi = float(s.min()), float(s.max())
    if hi - lo < 1e-6:
        return np.ones(len(s), dtype=bool)
    remap = ((s - lo) / (hi - lo) * 255.0).astype(np.uint8)
    thr, _ = cv2.threshold(remap, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    present = remap <= thr
    if smooth and smooth > 1:
        present = _majority(present, smooth)
    return present


def median_reduce(rois):
    return np.median(np.stack([r.astype(np.float64) for r in rois], axis=0), axis=0)


def diff_to_alpha(P, A, floor=0.05, pct=95.0):
    d = np.abs(np.asarray(P, float) - np.asarray(A, float)).max(axis=2)
    scale = float(np.percentile(d, pct))
    if scale < 1e-6:
        return np.zeros(d.shape, dtype=np.float64)
    a = np.clip(d / scale, 0.0, 1.0)
    a[a < floor] = 0.0
    return a


def solve_tc(P, A):
    """Per-pixel grayscale-plate least squares P_ch = t*A_ch + C; ratio fallback on flat bg."""
    xb = A.mean(axis=2); yb = P.mean(axis=2)
    dx = A - xb[..., None]; dy = P - yb[..., None]
    sxx = (dx * dx).sum(axis=2); sxy = (dx * dy).sum(axis=2)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(sxx > 1.0, sxy / sxx, yb / np.maximum(xb, 1e-3))
        C = yb - t * xb
    C = np.where(sxx > 1.0, C, 0.0)
    t = np.clip(t, 0.0, 1.6)
    return t, C


def place_in_canvas(img_roi, roi):
    """Place an ROI image (HxW or HxWx3) at `roi` into a zeroed full-4K canvas."""
    x, y, w, h = roi
    W, H = FULL_4K
    chan = () if img_roi.ndim == 2 else (img_roi.shape[2],)
    canvas = np.zeros((H, W) + chan, dtype=img_roi.dtype)
    canvas[y:y + h, x:x + w] = img_roi
    return canvas


def prod_crop(canvas_4k):
    """Crop PROD_CROP_4K from a full-4K image and resize to (OUT_W, OUT_H), INTER_AREA."""
    x1, y1, x2, y2 = PROD_CROP_4K
    sub = canvas_4k[y1:y2, x1:x2]
    return cv2.resize(sub, (OUT_W, OUT_H), interpolation=cv2.INTER_AREA)
```

- [ ] **Step 4: Run tests, expect pass**

Run: `python -m pytest tests/test_nametag_core.py -q`
Expected: PASS (5 passed). If `import tools.asset_matte.nametag_core` fails, add `sys.path.insert(0, repo_root)` handling to `tests/conftest.py` (it already adds `tools/` flat dirs — confirm `tools/asset_matte` is importable as a package or import the module by file path).

- [ ] **Step 5: Commit**

```bash
git add tools/asset_matte/nametag_core.py tests/test_nametag_core.py
git commit -m "feat(asset-matte): port nametag difference-method core + production crop"
```

---

### Task 2: Build + commit the template assets (one-time rescue from temp/ cache)

**Files:**
- Create: `tools/asset_matte/build_templates.py`
- Create (committed outputs): `tools/asset_matte/assets/{nametag_char_mask4k.png,nametag_kart_mask4k.png,char_P.png,char_A.png,kart_P.png,kart_A.png,templates_meta.json}`

**Interfaces:**
- Consumes: `nametag_core` (`classify_presence`, `median_reduce`), the surviving caches under `temp/asset_eyetest/nametag/_work/` and validated masks under `temp/asset_eyetest/nametag_mask/`.
- Produces: committed `assets/` consumed by Task 3's `undark.load_template`.

**Context — the surviving caches (clip `mario__base__rally_bike.mkv` is GONE; these are the only source):**
- char present: `temp/asset_eyetest/nametag/_work/mario__base__roi2378_1604_1178_226/*.png` (Mario idle at CHAR_ROI — all plate-present)
- char absent: `temp/asset_eyetest/nametag/_work/mario__base__rally_bike__roi2378_1604_1178_226/*.png` (Rally flourish at CHAR_ROI — has plate-absent frames)
- kart present+absent: `temp/asset_eyetest/nametag/_work/mario__base__rally_bike/*.png` (Rally at PLATE_ROI)
- masks: `temp/asset_eyetest/nametag_mask/char/nametag_char_4k.png`, `temp/asset_eyetest/nametag_mask/nametag_mask_4k.png`

- [ ] **Step 1: Implement `tools/asset_matte/build_templates.py`:**

```python
"""ONE-TIME: derive the char + kart nametag templates from the surviving
temp/asset_eyetest/nametag caches and commit them under tools/asset_matte/assets/.
The source clip mario__base__rally_bike.mkv is gone; the _work ROI caches are the
only remaining source, so this rescues the validated templates into the repo.

Run from repo root with build python:  python tools/asset_matte/build_templates.py
"""
import json, os, glob
import numpy as np, cv2
import tools.asset_matte.nametag_core as nc

WORK = "temp/asset_eyetest/nametag/_work"
MASKS = "temp/asset_eyetest/nametag_mask"
OUT = "tools/asset_matte/assets"

CHAR_PRESENT = f"{WORK}/mario__base__roi2378_1604_1178_226"
CHAR_ABSENT  = f"{WORK}/mario__base__rally_bike__roi2378_1604_1178_226"
KART_BOTH    = f"{WORK}/mario__base__rally_bike"


def _paths(d):
    p = sorted(glob.glob(os.path.join(d, "*.png")))
    if not p:
        raise SystemExit(f"no cached frames in {d} (temp cache missing — cannot rebuild templates)")
    return p


def _present_absent(paths):
    luma = np.array([float(np.median(cv2.imread(p, cv2.IMREAD_GRAYSCALE))) for p in paths])
    pres = nc.classify_presence(luma, smooth=5)
    return ([p for p, f in zip(paths, pres) if f], [p for p, f in zip(paths, pres) if not f])


def _even(paths, k=150):
    if len(paths) <= k:
        return paths
    idx = np.linspace(0, len(paths) - 1, k).round().astype(int)
    return [paths[i] for i in sorted(set(idx.tolist()))]


def _median_png(paths):
    return np.clip(nc.median_reduce([cv2.imread(p) for p in _even(paths)]), 0, 255).astype(np.uint8)


def main():
    os.makedirs(OUT, exist_ok=True)
    # CHAR: present from Mario idle, absent (background) from Rally flourish, both at CHAR_ROI.
    char_P = _median_png(_present_absent(_paths(CHAR_PRESENT))[0])
    char_A = _median_png(_present_absent(_paths(CHAR_ABSENT))[1])
    # KART: present + absent both from Rally at PLATE_ROI.
    k_pres, k_abs = _present_absent(_paths(KART_BOTH))
    kart_P = _median_png(k_pres); kart_A = _median_png(k_abs)
    cv2.imwrite(f"{OUT}/char_P.png", char_P); cv2.imwrite(f"{OUT}/char_A.png", char_A)
    cv2.imwrite(f"{OUT}/kart_P.png", kart_P); cv2.imwrite(f"{OUT}/kart_A.png", kart_A)
    # validated hand-checked 4K masks (copied verbatim)
    cv2.imwrite(f"{OUT}/nametag_char_mask4k.png",
                cv2.imread(f"{MASKS}/char/nametag_char_4k.png", cv2.IMREAD_GRAYSCALE))
    cv2.imwrite(f"{OUT}/nametag_kart_mask4k.png",
                cv2.imread(f"{MASKS}/nametag_mask_4k.png", cv2.IMREAD_GRAYSCALE))
    json.dump({"char_roi": list(nc.CHAR_ROI), "kart_roi": list(nc.PLATE_ROI),
               "prod_crop_4k": list(nc.PROD_CROP_4K), "out_wh": [nc.OUT_W, nc.OUT_H],
               "params": {"ALPHA_GAIN": 5.0, "STRENGTH": 1.02, "CSUB": 0.69,
                          "TFLOOR": 0.05, "T_OPAQUE": 0.20}},
              open(f"{OUT}/templates_meta.json", "w"), indent=2)
    print("char_P/A", char_P.shape, char_A.shape, "kart_P/A", kart_P.shape, kart_A.shape, "-> ", OUT)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it; verify shapes**

Run: `python tools/asset_matte/build_templates.py`
Expected: prints `char_P/A (226, 1178, 3) ... kart_P/A (226, 1378, 3) ...` and writes 7 files under `tools/asset_matte/assets/`. If it dies with "temp cache missing", STOP and surface to the user — the templates cannot be rebuilt without the cache.

- [ ] **Step 3: Sanity-view the medians** (optional but recommended): open `tools/asset_matte/assets/char_P.png` (should show the "Mario" plate) and `char_A.png` (should show plate-free background).

- [ ] **Step 4: Commit the script + assets**

```bash
git add tools/asset_matte/build_templates.py tools/asset_matte/assets
git commit -m "feat(asset-matte): rescue + commit validated nametag templates (char + kart)"
```

---

### Task 3: The undark stage (verbatim method, production crop, driver)

**Files:**
- Create: `tools/asset_matte/undark.py`
- Test: `tests/test_undark.py`

**Interfaces:**
- Consumes: `nametag_core` (`place_in_canvas`, `prod_crop`, `solve_tc`, `CHAR_ROI`, `PLATE_ROI`, `OUT_H`, `OUT_W`); committed `assets/`.
- Produces:
  - `load_template(is_char) -> (t HxW, C HxW, mask HxW float[0,1])` at the production crop.
  - `drop_nameplate(rgba, mask) -> (rgba, n_dropped)`
  - `undark_rgba(rgba, t, C, mask) -> rgba`
  - `plate_present(loopframe_bgr) -> bool`
  - `process(base, names, is_char) -> None` (writes `<base>/matte/<name>_undark/` + re-encoded loop files)

- [ ] **Step 1: Write failing tests** — `tests/test_undark.py`:

```python
import numpy as np
import tools.asset_matte.undark as ud

def _rgba(h=1080, w=988):
    a = np.zeros((h, w, 4), np.uint8); a[..., 3] = 0
    return a

def test_drop_nameplate_drops_blob_inside_footprint_keeps_big_subject():
    mask = np.zeros((1080, 988), np.float64)
    mask[900:1000, 300:700] = 1.0                       # nameplate footprint (lower strip)
    rgba = _rgba()
    rgba[920:980, 350:650] = (200, 200, 200, 255)       # detached blob INSIDE the footprint
    rgba[100:800, 400:600] = (180, 120, 90, 255)        # big subject spanning far outside
    out, n = ud.drop_nameplate(rgba, mask)
    assert n == 1                                        # blob dropped
    assert out[940, 500, 3] == 0                         # blob alpha cleared
    assert out[400, 500, 3] == 255                       # subject untouched

def test_undark_rgba_lightens_darkened_strip():
    # t<1 (plate darkens): a subject pixel under the plate should get lighter after undark.
    t = np.full((1080, 988), 0.5); C = np.zeros((1080, 988))
    mask = np.zeros((1080, 988)); mask[950:1000, 400:600] = 1.0
    rgba = _rgba(); rgba[960:990, 450:550] = (80, 80, 80, 255)   # darkened subject in the strip
    out = ud.undark_rgba(rgba, t, C, mask)
    assert out[975, 500, :3].mean() > 80                 # recovered brighter
    assert out[975, 500, 3] == 255                       # still opaque (t=0.5 > T_OPAQUE)

def test_undark_rgba_cuts_opaque_text():
    t = np.full((1080, 988), 0.5); C = np.zeros((1080, 988))
    t[950:1000, 400:600] = 0.1                           # opaque badge/text region (t<0.20)
    mask = np.zeros((1080, 988)); mask[950:1000, 400:600] = 1.0
    rgba = _rgba(); rgba[950:1000, 400:600] = (50, 50, 50, 255)
    out = ud.undark_rgba(rgba, t, C, mask)
    assert out[975, 500, 3] == 0                          # opaque plate content cut

def test_locked_params():
    assert (ud.ALPHA_GAIN, ud.STRENGTH, ud.CSUB, ud.TFLOOR) == (5.0, 1.02, 0.69, 0.05)
    assert (ud.T_OPAQUE, ud.PRESENT_LUMA, ud.NAMEPLATE_OUT_FRAC) == (0.20, 125.0, 0.30)
```

- [ ] **Step 2: Run, expect failure**

Run: `python -m pytest tests/test_undark.py -q`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `tools/asset_matte/undark.py`** (`drop_nameplate`/`undark_rgba` are VERBATIM from `undark_all.py`; templates load from committed assets and are placed into the production crop):

```python
"""Stage C: post-matte nametag removal + un-darkening, productionized at the
combo crop / OUT_H. drop_nameplate + undark_rgba are the VERBATIM validated kart
method (see memory nametag-mask-undark) — char uses the same method, char template.
Run in the matte venv (cv2/numpy/PIL):  temp/asset-venv-gpu/Scripts/python.exe ...
"""
import glob, os, subprocess, sys
import numpy as np, cv2
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
import tools.asset_matte.nametag_core as nc

ASSETS = os.path.join(os.path.dirname(__file__), "assets")
ALPHA_GAIN, STRENGTH, CSUB, TFLOOR = 5.0, 1.02, 0.69, 0.05
T_OPAQUE, PRESENT_LUMA, NAMEPLATE_OUT_FRAC = 0.20, 125.0, 0.30
# plate band near the bottom of the OUT_H crop (validated y[578:589] of 590 -> scaled)
_STRIP = (int(0.979 * nc.OUT_H), int(0.999 * nc.OUT_H))


def _mask_prod(path):
    m = cv2.imread(path, cv2.IMREAD_GRAYSCALE)            # full 4K (3840x2160)
    return nc.prod_crop(m.astype(np.float64) / 255.0)


def load_template(is_char):
    roi = nc.CHAR_ROI if is_char else nc.PLATE_ROI
    pre = "char" if is_char else "kart"
    P = nc.prod_crop(nc.place_in_canvas(cv2.imread(f"{ASSETS}/{pre}_P.png"), roi)).astype(np.float64)
    A = nc.prod_crop(nc.place_in_canvas(cv2.imread(f"{ASSETS}/{pre}_A.png"), roi)).astype(np.float64)
    t, C = nc.solve_tc(P, A)
    mask = _mask_prod(f"{ASSETS}/nametag_{pre}_mask4k.png")
    return t, C, mask


def plate_present(loopframe):
    strip = loopframe[_STRIP[0]:_STRIP[1], :, :]
    return float(np.median(cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY))) < PRESENT_LUMA


def drop_nameplate(rgba, mask):
    a = rgba[..., 3]
    fg = (a > 30).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(fg, 8)
    inside = mask > 0.05
    out = rgba.copy(); dropped = 0
    for i in range(1, n):
        comp = lab == i
        area = stats[i, cv2.CC_STAT_AREA]
        outside_frac = float((comp & ~inside).sum()) / max(area, 1)
        if outside_frac < NAMEPLATE_OUT_FRAC:
            out[..., 3][comp] = 0; dropped += 1
    return out, dropped


def undark_rgba(rgba, t, C, mask):
    rgb = rgba[..., :3].astype(np.float64)
    a = rgba[..., 3].astype(np.float64) / 255.0
    tt = np.clip(t, TFLOOR, 1.6)
    corrected = np.clip((rgb - CSUB * C[..., None]) / tt[..., None], 0, 255)
    cov = np.clip(mask * ALPHA_GAIN, 0, 1)[..., None]
    rgb2 = np.clip(rgb + STRENGTH * cov * (corrected - rgb), 0, 255)
    opaque = (mask > 0.05) & (t < T_OPAQUE)
    a2 = a * (~opaque)
    return np.dstack([rgb2, a2 * 255.0]).astype(np.uint8)


def _reencode(frames_dir, outbase):
    from PIL import Image
    fr = [Image.open(p).convert("RGBA") for p in sorted(glob.glob(f"{frames_dir}/*.png"))]
    dur = int(round(1000 / 60))
    fr[0].save(f"{outbase}_loop.webp", save_all=True, append_images=fr[1:], duration=dur,
               loop=0, lossless=True, disposal=2)
    fr[0].save(f"{outbase}_apng.png", save_all=True, append_images=fr[1:], duration=dur, loop=0)
    yy, xx = np.mgrid[0:fr[0].height, 0:fr[0].width]
    chk = Image.fromarray(np.where(((xx // 22 + yy // 22) % 2 == 0), 205, 150)
                          .astype(np.uint8)[..., None].repeat(3, 2), "RGB").convert("RGBA")
    comp = [Image.alpha_composite(chk, f) for f in fr]
    comp[0].save(f"{outbase}_checker.webp", save_all=True, append_images=comp[1:], duration=dur, loop=0)


def process(base, names, is_char):
    t, C, mask = load_template(is_char)
    for name in names:
        fdir, ldir = f"{base}/matte/{name}_frames", f"{base}/loopframes/{name}"
        odir = f"{base}/matte/{name}_undark"; os.makedirs(odir, exist_ok=True)
        files = sorted(os.listdir(fdir))
        nap = ndrop = 0
        for n in files:
            rgba = cv2.imread(f"{fdir}/{n}", cv2.IMREAD_UNCHANGED)
            lf = cv2.imread(f"{ldir}/{n}")
            if rgba is None or lf is None:
                continue
            rgba, dr = drop_nameplate(rgba, mask); ndrop += dr
            if is_char or plate_present(lf):
                rgba = undark_rgba(rgba, t, C, mask); nap += 1
            cv2.imwrite(f"{odir}/{n}", rgba)
        _reencode(odir, f"{base}/matte/{name}_undark")
        print(f"{name}: {len(files)}f, {nap} un-darkened, {ndrop} blobs dropped "
              f"({'char' if is_char else 'kart'}) -> {odir}", flush=True)


if __name__ == "__main__":
    a = sys.argv[1:]
    is_char = "--kart" not in a
    a = [x for x in a if x != "--kart"]
    process(a[0], a[1:], is_char)
```

- [ ] **Step 4: Run tests, expect pass**

Run: `python -m pytest tests/test_undark.py -q`
Expected: PASS (4 passed). (The driver `process`/`_reencode`/`load_template` are not unit-tested here — they need real frames + PIL + committed assets; they're exercised in Task 5.)

- [ ] **Step 5: Commit**

```bash
git add tools/asset_matte/undark.py tests/test_undark.py
git commit -m "feat(asset-matte): undark stage (verbatim drop_nameplate + undark_rgba, prod crop)"
```

---

### Task 4: Re-crop extract_loop to the nameplate-inclusive region

**Files:**
- Modify: `tools/asset_matte/extract_loop.py`

**Interfaces:**
- Consumes: `nametag_core.NAMEPLATE_HERO_ROI`, `nametag_core.OUT_H`.
- Produces: loopframes at the production crop (988×1080), aligned to the templates.

- [ ] **Step 1: Edit `tools/asset_matte/extract_loop.py`** — replace the crop ROI and `CROP_H`:

Change the imports + constant near the top:
```python
from mkw_tracker.tools.loop_probe import (
    load_features, autocorr_by_lag, find_period, scale_roi, HERO_ROI_1080,
)
from tools.asset_matte.nametag_core import NAMEPLATE_HERO_ROI, OUT_H

CROP_H = OUT_H   # 1080 — nameplate-inclusive crop (was 860 hero-only)
```
In `extract()`, period detection stays on the original `HERO_ROI_1080` (subject only, no static plate), but the written crop uses the nameplate-inclusive ROI:
```python
def extract(clip: str, outdir: str):
    fps, F = load_features(clip, size=48, every=1, settle=0.6, max_seconds=10, progress=False)
    lags, scores = autocorr_by_lag(F, int(0.5 * fps), int(15 * fps))
    P, conf, _ = find_period(lags, scores)
    N = len(F)
    best_s, best_d = 0, 1e18
    for s in range(0, N - P - 1):
        d = np.sum((F[s] - F[s + P]) ** 2) + np.sum((F[s + 1] - F[s + P + 1]) ** 2)
        if d < best_d:
            best_d, best_s = d, s
    start = int(0.6 * fps) + best_s
    os.makedirs(outdir, exist_ok=True)
    cap = cv2.VideoCapture(clip)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)); h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    x1, y1, x2, y2 = scale_roi(NAMEPLATE_HERO_ROI, w, h)   # nameplate-inclusive crop
    scale = CROP_H / (y2 - y1)
    out_w = int(round((x2 - x1) * scale))
    idx = saved = 0
    while saved < P:
        ok, frame = cap.read()
        if not ok:
            break
        if idx >= start:
            crop = cv2.resize(frame[y1:y2, x1:x2], (out_w, CROP_H), interpolation=cv2.INTER_AREA)
            cv2.imwrite(os.path.join(outdir, f"{saved:03d}.png"), crop)
            saved += 1
        idx += 1
    cap.release()
    print(f"{os.path.basename(clip)}: period={P}f start={start} saved={saved} crop={out_w}x{CROP_H} @ {w}x{h}")
```

- [ ] **Step 2: Smoke-run on one clip; verify crop size 988×1080**

Run: `PYTHONPATH=. python tools/asset_matte/extract_loop.py temp/asset_matte_run2 captures_sdr/en_uk/clips/donkey_kong__base.mkv`
Expected: prints `... crop=988x1080 @ 3840x2160`, writes `temp/asset_matte_run2/loopframes/donkey_kong__base/000.png` etc. Open `000.png`: DK's hands are NOT clipped, and the "Donkey Kong" nametag plate is fully visible at the bottom.

- [ ] **Step 3: Commit**

```bash
git add tools/asset_matte/extract_loop.py
git commit -m "feat(asset-matte): crop the nameplate-inclusive region at 1080 (was hero-only 860)"
```

---

### Task 5: End-to-end run on the 6 idle clips + visual verification

**Files:** none (pipeline run). Produces `temp/asset_matte_run2/matte/<name>_undark_*`.

- [ ] **Step 1: Extract loops (build python), all 6 idle clips**

Run:
```bash
PYTHONPATH=. python tools/asset_matte/extract_loop.py temp/asset_matte_run2 \
  captures_sdr/en_uk/clips/baby_mario__base.mkv captures_sdr/en_uk/clips/dolphin__base.mkv \
  captures_sdr/en_uk/clips/donkey_kong__base.mkv captures_sdr/en_uk/clips/koopa_troopa__base.mkv \
  captures_sdr/en_uk/clips/mario__base.mkv captures_sdr/en_uk/clips/mario__touring.mkv
```
Expected: six `... crop=988x1080 ...` lines.

- [ ] **Step 2: Matte (GPU venv)**

Run:
```bash
temp/asset-venv-gpu/Scripts/python.exe tools/asset_matte/matte_loop.py temp/asset_matte_run2 \
  birefnet-general-lite - baby_mario__base dolphin__base donkey_kong__base koopa_troopa__base \
  mario__base mario__touring
```
Expected: `ALL DONE`, `temp/asset_matte_run2/matte/<name>_frames/` populated.

- [ ] **Step 3: Undark (GPU venv — needs cv2/numpy/PIL; characters → char template)**

Run:
```bash
temp/asset-venv-gpu/Scripts/python.exe tools/asset_matte/undark.py temp/asset_matte_run2 \
  baby_mario__base dolphin__base donkey_kong__base koopa_troopa__base mario__base mario__touring
```
Expected: per-clip line `... un-darkened, N blobs dropped (char) -> ..._undark`. For the kartless standing characters expect ≥1 nameplate blob dropped on the plate-present frames; DK should drop its detached "Donkey Kong" plate blob.

- [ ] **Step 4: Build a before/after montage and eyeball**

Run (GPU venv has PIL):
```bash
temp/asset-venv-gpu/Scripts/python.exe - <<'PY'
import glob, numpy as np
from PIL import Image
B="temp/asset_matte_run2/matte"
def chk(w,h,s=18,a=210,b=150):
    yy,xx=np.mgrid[0:h,0:w]
    return Image.fromarray(np.where(((xx//s+yy//s)%2==0),a,b).astype(np.uint8)[...,None].repeat(3,2),"RGB").convert("RGBA")
rows=[]
for n in ["mario__base","donkey_kong__base","dolphin__base","koopa_troopa__base","baby_mario__base","mario__touring"]:
    raw=sorted(glob.glob(f"{B}/{n}_frames/*.png")); und=sorted(glob.glob(f"{B}/{n}_undark/*.png"))
    if not raw or not und: continue
    i=len(raw)//2
    pair=[np.array(Image.alpha_composite(chk(*Image.open(p).convert("RGBA").size),Image.open(p).convert("RGBA")).convert("RGB")) for p in (raw[i],und[i])]
    h=max(x.shape[0] for x in pair); pair=[np.pad(x,((0,h-x.shape[0]),(0,0),(0,0)),constant_values=255) for x in pair]
    rows.append(np.hstack(pair))
w=max(r.shape[1] for r in rows); rows=[np.pad(r,((0,0),(0,w-r.shape[1]),(0,0)),constant_values=255) for r in rows]
Image.fromarray(np.vstack(rows)).save("temp/asset_matte_run2/before_after.png")
print("wrote temp/asset_matte_run2/before_after.png  (left=raw matte, right=undark)")
PY
```
Then Read `temp/asset_matte_run2/before_after.png`. **Acceptance:** in the right column, the detached nameplate blob is gone for every character, the subject body is intact (no eaten limbs), and DK's hands where they dip toward the plate are un-darkened (not cut). If a character body is damaged, STOP — do not re-tune params; surface to the user (the method is frozen; damage means a crop/template-alignment bug to investigate, not a tuning issue).

- [ ] **Step 5: Present to the user** the `before_after.png` and the `<name>_undark_checker.webp` paths for in-browser review. Do not commit `temp/` (gitignored).

---

## Self-Review

- **Spec coverage:** crop change (Task 4), undark method ported verbatim (Task 3), per-screen templates rescued+committed (Task 2), full-res production crop (constants in Task 1), tests (Tasks 1/3), end-to-end run (Task 5). Kart-combo run is explicitly the *next* effort (needs spawn-in window + character-period handling) — out of scope here, called out for follow-up.
- **Placeholder scan:** none — all code is concrete and ported from validated sources.
- **Type consistency:** `prod_crop`/`place_in_canvas`/`solve_tc`/`drop_nameplate`/`undark_rgba` signatures match across Tasks 1/3; `NAMEPLATE_HERO_ROI`/`OUT_H` consumed by Task 4 are defined in Task 1; asset filenames in Task 2 match `load_template` in Task 3.

## Follow-up (not in this plan)
- Kart-combo idle loops: `_is_kart` items need the spawn-in excluded from the loop window and the wheel-robust period (use the bare-character idle period, per `silhouette_loop`), then `undark.py ... --kart`.
- Promote `extract_loop`/`undark` into a single `clip_segment`-driven entry point if desired.
