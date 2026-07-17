# Char Nameplate Blank-Plate Port — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the broken standalone-character nameplate predark with the kart-style blank-plate treatment (live-derived artifacts, HSV text mask, cut−9 flourish tail gate) so char chips stop shipping nameplate artifacts.

**Architecture:** Two new committed artifacts (`blank_plate_char.npy`, `clean_bg_char.npy`) are built from the existing 153 standalone captures by an extended `build_blank_plate.py`. All new predark math lives as **pure functions in `pre_darken.py`** (importable under build python, fully unit-testable); the GPU module `matte_blankplate.py` only wires them in. A segment-local `predark_raw_tail` passes the last 7 flourish frames raw. The kart path stays byte-identical.

**Tech Stack:** Python (build python: cv2 + numpy + pytest), GPU venv `temp/asset-venv-matte` only for the sample re-matte task.

**Spec:** `docs/superpowers/specs/2026-07-17-char-nameplate-blankplate-design.md` (committed in Task 1). All parameter values below come from it.

## Global Constraints

- Kart pipeline behavior must be **byte-identical** — never edit `_kart_predark`, `_kart_text_mask`, the kart setup block, kart builder path, or `pre_darken.pre_darken()` semantics (its locked-defaults test must keep passing).
- Locked char params, copied verbatim from spec: `KEY_THR=120, CSUB=0.5, TFLOOR=0.01, FILL_K=51`; text dilate `7`; body-alpha dilate `31`; `CHAR_PLATE_DEPART = 9`; raw tail = `CHAR_PLATE_DEPART - CHAR_CUT_GUARD` (= 7), never hardcoded.
- New pure logic goes in `pre_darken.py` / `nametag_core.py` (build-python importable). `matte_blankplate.py` imports rembg at module level and CANNOT be imported by the test suite.
- Data lives on this machine: clips `D:\kartoff\captures_sdr\en_uk\clips`, current mattes `D:\kartoff\asset_chips\matte`. The GPU venv is `C:\development\mkw-split-rewrite\temp\asset-venv-matte\Scripts\python.exe` (absolute — works from the worktree).
- Tests: `python -m pytest tests/<file> -q` (conftest adds `tools/asset_matte` to sys.path — use flat imports like `import pre_darken as pd`).
- Commit after every green test cycle. Work happens on branch `char-nameplate-blankplate` off `main` in an isolated worktree (the shared checkout belongs to another live session — do NOT run git there).

---

### Task 1: Branch setup + carry the spec and plan onto it

**Files:**
- Create (copy): `docs/superpowers/specs/2026-07-17-char-nameplate-blankplate-design.md`
- Create (copy): `docs/superpowers/plans/2026-07-17-char-nameplate-blankplate.md`

**Interfaces:**
- Produces: the working branch every later task commits to.

- [ ] **Step 1: Verify the worktree is on a fresh branch off main**

Run: `git status; git log --oneline -1`
Expected: clean tree, branch `char-nameplate-blankplate`, HEAD = `e273571` (main). If the worktree does not exist yet, it was created by the executing skill via superpowers:using-git-worktrees with `git worktree add <path> -b char-nameplate-blankplate main`.

- [ ] **Step 2: Copy the spec + this plan from the shared checkout (they are untracked/foreign-branch there)**

```powershell
Copy-Item "C:\development\mkw-split-rewrite\docs\superpowers\specs\2026-07-17-char-nameplate-blankplate-design.md" "docs\superpowers\specs\"
Copy-Item "C:\development\mkw-split-rewrite\docs\superpowers\plans\2026-07-17-char-nameplate-blankplate.md" "docs\superpowers\plans\"
```

(The spec is committed on `wr-fix-wave` as `ec981b1` by accident — identical content here means whichever branch merges second gets a clean add/add resolution.)

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-07-17-char-nameplate-blankplate-design.md docs/superpowers/plans/2026-07-17-char-nameplate-blankplate.md
git commit -m "docs: char nameplate blank-plate spec + plan"
```

---

### Task 2: `nametag_core.yellow_text_mask`

The shared HSV-yellow detector (same gates the blank builder uses inline).

**Files:**
- Modify: `tools/asset_matte/nametag_core.py` (append at end)
- Test: `tests/test_char_predark.py` (new file)

**Interfaces:**
- Produces: `yellow_text_mask(bgr_uint8: HxWx3) -> HxW bool` — True where saturated yellow name text (OpenCV hue 18–42, S>150, V>150).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_char_predark.py
"""Char blank-plate predark unit tests (spec 2026-07-17-char-nameplate-blankplate-design)."""
import numpy as np
import cv2
import nametag_core as nc          # FLAT imports — conftest adds tools/asset_matte to sys.path
import pre_darken as pd


def test_yellow_text_mask_catches_text_not_plate():
    img = np.full((20, 20, 3), 40, np.uint8)           # dark neutral plate
    img[5:10, 5:15] = (30, 190, 250)                   # saturated yellow text (BGR)
    m = nc.yellow_text_mask(img)
    assert m[7, 10] and not m[2, 2]
    # dark yellow-hue but low V (drop shadow) is NOT caught — dilation downstream covers it
    img2 = np.full((4, 4, 3), 0, np.uint8); img2[:] = (10, 60, 80)
    assert not nc.yellow_text_mask(img2).any()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_char_predark.py -q`
Expected: FAIL — `AttributeError: module 'nametag_core' has no attribute 'yellow_text_mask'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to tools/asset_matte/nametag_core.py
def yellow_text_mask(bgr_uint8):
    """Saturated-yellow name-text mask (OpenCV hue 18-42, S>150, V>150) — the same gates
    build_blank_plate.nan_yellow_text uses; shared so the char text mask matches the blank."""
    hsv = cv2.cvtColor(bgr_uint8, cv2.COLOR_BGR2HSV)
    return ((hsv[..., 0] >= 18) & (hsv[..., 0] <= 42)
            & (hsv[..., 1] > 150) & (hsv[..., 2] > 150))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_char_predark.py -q`
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add tools/asset_matte/nametag_core.py tests/test_char_predark.py
git commit -m "feat(asset-matte): shared HSV yellow-text mask in nametag_core"
```

---

### Task 3: `pre_darken.char_text_band` + `pre_darken.char_text_mask`

**Files:**
- Modify: `tools/asset_matte/pre_darken.py` (append)
- Test: `tests/test_char_predark.py` (append)

**Interfaces:**
- Consumes: `nc.yellow_text_mask` (Task 2).
- Produces:
  - `char_text_band(t_template: HxW float, mask: HxW float) -> HxW bool` — rows spanned by template glyphs (`t < T_OPAQUE` inside `mask > 0.05`) ±8, full footprint x-span, ∩ footprint.
  - `char_text_mask(median_bgr: HxWx3 float or uint8, text_band: HxW bool) -> HxW bool` — HSV-yellow ∩ band, dilated 7.
  - Module constant `CHAR_TEXT_DILATE = 7`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_char_predark.py
def _geometry():
    h, w = 60, 80
    mask = np.zeros((h, w)); mask[20:50, 10:70] = 1.0          # footprint
    t = np.ones((h, w));     t[30:36, 30:50] = 0.1             # template glyph rows 30..35
    return t, mask


def test_char_text_band_rows_padded_full_span():
    t, mask = _geometry()
    band = pd.char_text_band(t, mask)
    assert band[30, 15] and band[35, 65]        # glyph rows, full footprint x-span
    assert band[23, 40] and band[43, 40]        # +-8 rows (22..43), inside footprint
    assert not band[21, 40]                     # 30-8=22 is the first band row
    assert not band[30, 5]                      # outside footprint x
    assert not band[10, 40]                     # far row


def test_char_text_mask_is_yellow_in_band_dilated():
    t, mask = _geometry()
    band = pd.char_text_band(t, mask)
    med = np.full((60, 80, 3), 40, np.float32)
    med[31:34, 35:45] = (30, 190, 250)          # live yellow text inside the band
    med[25, 12] = (30, 190, 250)                # yellow inside band -> caught too
    med[10, 40] = (30, 190, 250)                # yellow OUTSIDE band -> excluded
    m = pd.char_text_mask(med, band)
    assert m[32, 40]
    assert m[32, 47]                            # dilation (7//2=3 px) covers the AA ring
    assert not m[10, 40]
    assert pd.CHAR_TEXT_DILATE == 7
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_char_predark.py -q`
Expected: FAIL — `AttributeError: ... 'char_text_band'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to tools/asset_matte/pre_darken.py
CHAR_TEXT_DILATE = 7   # covers the AA ring + dark drop shadow around the yellow glyphs


def char_text_band(t_template, mask):
    """Rows the template glyphs occupy (+-8), full footprint x-span, clipped to the footprint.
    Geometry only — char_P's stale LEVELS are never used (spec: live-derived clean_bg_char)."""
    in_plate = mask > 0.05
    glyph = (t_template < T_OPAQUE) & in_plate
    ys = np.where(glyph.any(1))[0]
    xs = np.where(in_plate.any(0))[0]
    band = np.zeros_like(in_plate)
    band[max(0, ys.min() - 8):ys.max() + 9, xs.min():xs.max() + 1] = True
    return band & in_plate


def char_text_mask(median_bgr, text_band):
    """Per-clip live-text mask: HSV-yellow on the segment median, in-band, dilated.
    NOT the kart t<T_OPAQUE gate — that only works against a TINTED reference (kart A
    anti-correlates with yellow); vs the neutral live char bg it lands on solve_tc's
    ratio path and misses the text entirely (prototype-verified 2026-07-17)."""
    med = np.clip(np.asarray(median_bgr), 0, 255).astype(np.uint8)
    yellow = nc.yellow_text_mask(med) & text_band
    k = np.ones((CHAR_TEXT_DILATE, CHAR_TEXT_DILATE), np.uint8)
    return cv2.dilate(yellow.astype(np.uint8), k).astype(bool)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_char_predark.py -q`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add tools/asset_matte/pre_darken.py tests/test_char_predark.py
git commit -m "feat(asset-matte): char text band + HSV per-clip text mask"
```

---

### Task 4: `CHAR_PLATE_DEPART` constant + `predark_frame_count`

**Files:**
- Modify: `tools/asset_matte/extract_loop.py` (constants block, directly under `CHAR_CUT_GUARD = 2` at ~line 348)
- Modify: `tools/asset_matte/pre_darken.py` (append)
- Test: `tests/test_char_predark.py` (append)

**Interfaces:**
- Produces:
  - `extract_loop.CHAR_PLATE_DEPART = 9` (int).
  - `pre_darken.predark_frame_count(n_frames: int, raw_tail: int) -> int` — frames to predark from the segment start; the rest pass raw.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_char_predark.py
import extract_loop as el


def test_char_plate_depart_is_recorder_anchored():
    # 11/11 measurable chars on the 2026-07-17 survey: slide onset exactly cut-9.
    assert el.CHAR_PLATE_DEPART == 9
    # raw tail must stay derivable and positive: export ends at cut - CHAR_CUT_GUARD
    assert el.CHAR_PLATE_DEPART - el.CHAR_CUT_GUARD == 7


def test_predark_frame_count_partition():
    assert pd.predark_frame_count(78, 7) == 71
    assert pd.predark_frame_count(78, 0) == 78
    assert pd.predark_frame_count(5, 7) == 0     # tiny segment: all raw
    assert pd.predark_frame_count(78, -3) == 78  # negative clamps to no tail
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_char_predark.py -q`
Expected: FAIL — `AttributeError: ... 'CHAR_PLATE_DEPART'`

- [ ] **Step 3: Write minimal implementation**

In `tools/asset_matte/extract_loop.py`, directly after the `CHAR_CUT_GUARD = 2` constant (~line 348), add:

```python
# The char nameplate SLIDES LEFT + FADES OUT starting exactly this many frames before the
# hard cut (2026-07-17 survey: 11/11 measurable chars; fully gone by ~cut-5). Frames at or
# after cut-CHAR_PLATE_DEPART must NOT be predarked (static-template predark would paint a
# phantom plate); the flourish export ends at cut-CHAR_CUT_GUARD, so the raw tail is the
# last CHAR_PLATE_DEPART-CHAR_CUT_GUARD (=7) exported frames.
CHAR_PLATE_DEPART = 9
```

In `tools/asset_matte/pre_darken.py`, append:

```python
def predark_frame_count(n_frames, raw_tail):
    """How many frames from the segment start get predark; the trailing raw_tail pass raw
    (the departing/absent plate is the matte's job, like the kart flourish)."""
    return max(0, n_frames - max(0, raw_tail))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_char_predark.py -q`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add tools/asset_matte/extract_loop.py tools/asset_matte/pre_darken.py tests/test_char_predark.py
git commit -m "feat(asset-matte): CHAR_PLATE_DEPART=9 + predark tail partition"
```

---

### Task 5: `pre_darken.char_predark` — the ported blank-plate un-darken

**Files:**
- Modify: `tools/asset_matte/pre_darken.py` (append)
- Test: `tests/test_char_predark.py` (append)

**Interfaces:**
- Produces: `char_predark(raw_bgr: HxWx3 uint8, text: HxW bool, assets: dict, KEY_THR=120, CSUB=0.5, TFLOOR=0.01, FILL_K=51) -> HxWx3 uint8`.
  `assets` keys (exact): `"T_B" HxW float, "C_B" HxW float, "badge" HxW bool, "bg" HxWx3 float, "in_plate" HxW bool, "text_band" HxW bool`. Later tasks build this dict via `load_char_assets()` (Task 6) — keep the key names exact.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_char_predark.py
def _char_assets():
    """Synthetic self-consistent world: tinted bg, plate = t*bg (t=0.5, C=0 on the solve's
    covariance path because bg is tinted), footprint [20:50, 10:70]."""
    h, w = 60, 80
    bg = np.zeros((h, w, 3)) + np.array([120.0, 130.0, 140.0])
    in_plate = np.zeros((h, w), bool); in_plate[20:50, 10:70] = True
    blank = bg.copy(); blank[in_plate] = 0.5 * bg[in_plate]
    T_B, C_B = nc.solve_tc(blank.astype(np.float32), bg)
    badge = (T_B < pd.T_OPAQUE) & in_plate
    band = np.zeros((h, w), bool); band[30:40, 10:70] = True
    return {"T_B": T_B, "C_B": C_B, "badge": badge, "bg": bg,
            "in_plate": in_plate, "text_band": band}


def test_char_predark_empty_plate_recovers_to_bg():
    a = _char_assets()
    frame = a["bg"].copy(); frame[a["in_plate"]] = 0.5 * a["bg"][a["in_plate"]]
    out = pd.char_predark(frame.astype(np.uint8), np.zeros_like(a["in_plate"]), a)
    assert np.allclose(out[35, 40], a["bg"][35, 40], atol=4)   # recovered ~ bg
    assert np.array_equal(out[5, 5], frame.astype(np.uint8)[5, 5])  # outside untouched


def test_char_predark_subject_behind_plate_kept_full_s():
    a = _char_assets()
    subject = np.array([60.0, 200.0, 40.0])                    # far from bg
    frame = a["bg"].copy(); frame[a["in_plate"]] = 0.5 * a["bg"][a["in_plate"]]
    frame[30:40, 30:50] = 0.5 * subject                        # subject behind serration
    out = pd.char_predark(frame.astype(np.uint8), np.zeros_like(a["in_plate"]), a)
    assert np.allclose(out[35, 40], subject, atol=6)           # full-S stamp keeps it
    # no razor boundary: neighbouring empty plate still ~bg, not painted flat around subject
    assert np.allclose(out[35, 60], a["bg"][35, 60], atol=4)


def test_char_predark_text_painted_out_and_gone():
    a = _char_assets()
    frame = a["bg"].copy(); frame[a["in_plate"]] = 0.5 * a["bg"][a["in_plate"]]
    frame[32:36, 30:44] = (30.0, 190.0, 250.0)                 # opaque yellow text
    text = np.zeros_like(a["in_plate"]); text[30:38, 28:46] = True
    out = pd.char_predark(frame.astype(np.uint8), text, a)
    assert not nc.yellow_text_mask(out)[32:36, 30:44].any()    # yellow gone
    # text region ends near bg level (painted to bg, possibly TELEA-smoothed)
    assert abs(float(out[34, 36].mean()) - float(a["bg"][34, 36].mean())) < 25
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_char_predark.py -q`
Expected: FAIL — `AttributeError: ... 'char_predark'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to tools/asset_matte/pre_darken.py
def char_predark(raw_bgr, text, assets, KEY_THR=120, CSUB=0.5, TFLOOR=0.01, FILL_K=51):
    """Blank-transform un-darken for the CHAR plate — _kart_predark's exact math (full-S
    stamp `eac3c82` + interior TELEA), parameterized on the char assets dict so it stays
    pure/testable under build python. Params are the kart-locked values (spec 2026-07-17)."""
    T_B, C_B, bg = assets["T_B"], assets["C_B"], assets["bg"]
    in_plate = assets["in_plate"]
    O = raw_bgr.astype(np.float64)
    S = np.clip((O - CSUB * C_B[..., None]) / np.clip(T_B, TFLOOR, 1.6)[..., None], 0, 255)
    opaque = (assets["badge"] | text) & in_plate
    subject = in_plate & (np.abs(S - bg).max(2) >= KEY_THR) & ~opaque
    out = O.copy(); out[in_plate] = S[in_plate]; out[opaque] = bg[opaque]
    out = np.clip(out, 0, 255).astype(np.uint8)
    K = int(FILL_K) | 1
    closed = cv2.morphologyEx(subject.astype(np.uint8) * 255, cv2.MORPH_CLOSE,
                              cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (K, K))) > 0
    holes = in_plate & closed & ~subject
    n, lab, st, _ = cv2.connectedComponentsWithStats(holes.astype(np.uint8), 8)
    keep = np.zeros_like(holes)
    for i in range(1, n):
        if st[i, cv2.CC_STAT_AREA] <= 2000:
            keep |= (lab == i)
    return cv2.inpaint(out, keep.astype(np.uint8) * 255, 3, cv2.INPAINT_TELEA)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_char_predark.py -q`
Expected: 8 passed

- [ ] **Step 5: Run the full pre_darken suite (guard the untouched legacy path)**

Run: `python -m pytest tests/test_pre_darken.py tests/test_nametag_core.py -q`
Expected: all pass, zero modifications needed

- [ ] **Step 6: Commit**

```bash
git add tools/asset_matte/pre_darken.py tests/test_char_predark.py
git commit -m "feat(asset-matte): char_predark — blank-plate un-darken ported for chars"
```

---

### Task 6: `pre_darken.load_char_assets` + legacy note on `pre_darken()`

**Files:**
- Modify: `tools/asset_matte/pre_darken.py` (append + docstring edit)
- Test: `tests/test_char_predark.py` (append)

**Interfaces:**
- Consumes: `char_text_band` (Task 3), committed artifacts (built in Task 8 — the happy-path test uses a tmp dir, so order is fine).
- Produces: `load_char_assets(assets_dir: str = ASSETS) -> dict` with the exact keys Task 5 consumes. Raises `FileNotFoundError` naming `build_blank_plate.py --screen char` when the npys are absent.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_char_predark.py
import os
import pytest


def test_load_char_assets_missing_artifacts_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="--screen char"):
        pd.load_char_assets(assets_dir=str(tmp_path))


def test_load_char_assets_happy_path(tmp_path, monkeypatch):
    h, w = 60, 80
    bg = np.zeros((h, w, 3), np.float32) + np.array([120, 130, 140], np.float32)
    blank = bg.copy(); blank[20:50, 10:70] *= 0.5
    np.save(tmp_path / "blank_plate_char.npy", blank)
    np.save(tmp_path / "clean_bg_char.npy", bg)
    t = np.ones((h, w)); t[30:36, 30:50] = 0.1
    mask = np.zeros((h, w)); mask[20:50, 10:70] = 1.0
    monkeypatch.setattr(pd, "load_template", lambda is_char: (t, np.zeros((h, w)), bg, mask))
    a = pd.load_char_assets(assets_dir=str(tmp_path))
    assert set(a) == {"T_B", "C_B", "badge", "bg", "in_plate", "text_band"}
    assert a["in_plate"][30, 40] and not a["in_plate"][5, 5]
    assert 0.3 < float(np.median(a["T_B"][a["in_plate"]])) < 0.8
    assert a["text_band"][33, 40]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_char_predark.py -q`
Expected: FAIL — `AttributeError: ... 'load_char_assets'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to tools/asset_matte/pre_darken.py
def load_char_assets(assets_dir=ASSETS):
    """Char blank-plate assets dict for char_predark. Solves (T_B, C_B) from the two
    committed live-derived artifacts; char_P/char_A contribute GEOMETRY ONLY (footprint
    mask + text-band rows) — their stale bright-era levels are never used (spec)."""
    blank_p = os.path.join(assets_dir, "blank_plate_char.npy")
    bg_p = os.path.join(assets_dir, "clean_bg_char.npy")
    for p in (blank_p, bg_p):
        if not os.path.exists(p):
            raise FileNotFoundError(
                f"{p} missing — build it with: python tools/asset_matte/build_blank_plate.py --screen char")
    blank = np.load(blank_p).astype(np.float32)
    bg = np.load(bg_p).astype(np.float64)
    t_tmpl, _, _, mask = load_template(True)
    in_plate = mask > 0.05
    T_B, C_B = nc.solve_tc(blank, bg)
    return {"T_B": T_B, "C_B": C_B, "badge": (T_B < T_OPAQUE) & in_plate,
            "bg": bg, "in_plate": in_plate,
            "text_band": char_text_band(t_tmpl, mask)}
```

Also edit the `pre_darken()` docstring first line (keep everything else) from:

```
    """Paint the WHOLE plate footprint to the clean background A, then stamp back only the genuine
```

to:

```
    """LEGACY (tuner/tests only; production chars now use char_predark + live-derived assets).
    Paint the WHOLE plate footprint to the clean background A, then stamp back only the genuine
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_char_predark.py tests/test_pre_darken.py -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add tools/asset_matte/pre_darken.py tests/test_char_predark.py
git commit -m "feat(asset-matte): load_char_assets loader + mark pre_darken legacy"
```

---

### Task 7: Builder — `build_blank_plate.py --screen char`

**Files:**
- Modify: `tools/asset_matte/build_blank_plate.py`
- Test: `tests/test_build_blank_plate.py` (new)

**Interfaces:**
- Consumes: `extract_loop.find_segments`, `extract_loop.CHAR_CUT_GUARD`, `nc.yellow_text_mask`, existing `idle_frame`.
- Produces (files, when run in Task 8): `assets/blank_plate_char.npy|.png`, `assets/clean_bg_char.npy|.png`, `assets/char_cuts.json`; `templates_meta.json` gains `"char_blank": {"date", "clips", "bg_clips"}`.
- Produces (functions): `standalone_names(clips_dir) -> list[str]`; `nan_body(f32_frame, alpha_png_path, dilate=31) -> None (in-place)`; `char_cut_for(clip, cache: dict) -> int | None`; `finish_median(stack: list[HxWx3 float16], in_plate) -> float32 HxWx3`; `build_char(args) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_build_blank_plate.py
"""Char-mode builder pure helpers (kart default path untouched)."""
import json
import numpy as np
import cv2
import build_blank_plate as bbp


def test_standalone_names_filters_two_part(tmp_path):
    for n in ("mario__base.mkv", "mario__base__b_dasher.mkv", "peepa__base.mkv", "junk.txt"):
        (tmp_path / n).write_bytes(b"x")
    assert bbp.standalone_names(str(tmp_path)) == ["mario__base", "peepa__base"]


def test_nan_body_masks_dilated_alpha(tmp_path):
    f = np.zeros((20, 20, 3), np.float32)
    rgba = np.zeros((20, 20, 4), np.uint8); rgba[10, 10, 3] = 255
    p = tmp_path / "000.png"; cv2.imwrite(str(p), rgba)
    bbp.nan_body(f, str(p), dilate=5)
    assert np.isnan(f[10, 10, 0]) and np.isnan(f[12, 12, 0])   # dilated
    assert not np.isnan(f[0, 0, 0])


def test_nan_body_missing_alpha_is_noop(tmp_path):
    f = np.zeros((8, 8, 3), np.float32)
    bbp.nan_body(f, str(tmp_path / "nope.png"))
    assert not np.isnan(f).any()


def test_finish_median_ignores_nan_and_fills_all_nan(tmp_path):
    in_plate = np.zeros((4, 4), bool); in_plate[1:3, 1:3] = True
    a = np.full((4, 4, 3), 10, np.float16)
    b = np.full((4, 4, 3), 20, np.float16); b[1, 1] = np.nan
    c = np.full((4, 4, 3), 30, np.float16)
    c[2, 2] = np.nan; a[2, 2] = np.nan; b[2, 2] = np.nan       # all-NaN pixel
    out = bbp.finish_median([a, b, c], in_plate)
    assert out.dtype == np.float32
    assert abs(float(out[1, 1, 0]) - 20.0) < 0.1               # median of {10,30}
    assert not np.isnan(out).any()                             # all-NaN filled


def test_char_cut_for_uses_cache_without_decoding():
    cache = {"mario__base": 681}
    assert bbp.char_cut_for("D:/nowhere/mario__base.mkv", cache) == 681
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_build_blank_plate.py -q`
Expected: FAIL — `AttributeError: ... 'standalone_names'`

- [ ] **Step 3: Implement the char build mode**

Append to `tools/asset_matte/build_blank_plate.py` (before `main()`), and extend `main()`:

```python
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
```

Extend `main()`: add the two arguments and the mode branch (kart default untouched):

```python
    ap.add_argument("--screen", choices=("kart", "char"), default="kart",
                    help="kart: baby_daisy 40-kart blank (default, unchanged). "
                         "char: blank_plate_char + clean_bg_char from ALL standalone clips")
    ap.add_argument("--matte-dir", default=r"D:\kartoff\asset_chips\matte",
                    help="char mode: current mattes, for body-exclusion alphas")
```

and at the top of the existing body (after `a = ap.parse_args()`):

```python
    if a.screen == "char":
        build_char(a)
        return
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_build_blank_plate.py tests/test_char_predark.py -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add tools/asset_matte/build_blank_plate.py tests/test_build_blank_plate.py
git commit -m "feat(asset-matte): build_blank_plate --screen char (blank + live clean bg)"
```

---

### Task 8: Run the builder; commit the artifacts

**Files:**
- Create (generated): `tools/asset_matte/assets/blank_plate_char.npy`, `.png`, `clean_bg_char.npy`, `.png`, `char_cuts.json`; modify `templates_meta.json`.

**Interfaces:**
- Consumes: Task 7 CLI. Produces: the artifacts Task 6's loader reads in production.

- [ ] **Step 1: Run the char build (~1–1.5 h: find_segments ≈17 s × 153 + sequential tail decodes; cuts cache makes re-runs minutes)**

Run:
```powershell
python tools/asset_matte/build_blank_plate.py --screen char --clips D:\kartoff\captures_sdr\en_uk\clips --matte-dir D:\kartoff\asset_chips\matte
```
Expected final lines: `blank=153 frames  bg=~459 frames  bg-skipped=[]  T_B median=0.6-0.8` (hard-fails outside 0.3–1.0). If any `bg-skipped` names appear, list them in the commit message — they are fallback-flourish clips, acceptable if ≤3.

- [ ] **Step 2: Eyeball the two PNG previews**

Open `tools/asset_matte/assets/blank_plate_char.png` and `clean_bg_char.png`: the blank must show clean serration with no character silhouettes and no readable text; the bg must be a smooth backdrop with no plate and no body shadows. (Reference: the 2026-07-17 prototype `D_blank_char_diag.png` looked exactly like this.)

- [ ] **Step 3: Loader smoke against the real artifacts**

Run: `python -c "import sys; sys.path.insert(0, 'tools/asset_matte'); import pre_darken as pd, numpy as np; a = pd.load_char_assets(); print('T_B median', float(np.median(a['T_B'][a['in_plate']])), 'badge px', int(a['badge'].sum()))"`
Expected: `T_B median 0.6-0.8`, `badge px` under ~5000.

- [ ] **Step 4: Commit (npys are ~12.8 MB each — committed raw, kart-blank precedent)**

```bash
git add tools/asset_matte/assets/blank_plate_char.npy tools/asset_matte/assets/blank_plate_char.png tools/asset_matte/assets/clean_bg_char.npy tools/asset_matte/assets/clean_bg_char.png tools/asset_matte/assets/char_cuts.json tools/asset_matte/assets/templates_meta.json
git commit -m "assets(asset-matte): char blank plate + live clean bg (153-clip build)"
```

---

### Task 9: Wire `matte_blankplate.py` (GPU module)

**Files:**
- Modify: `tools/asset_matte/matte_blankplate.py` — char setup (after the kart setup block ending ~line 133), `_build_predark_frames` (~lines 273–289), `matte_loopframes` signature + docstring (~lines 323–337).

**Interfaces:**
- Consumes: `pd.load_char_assets`, `pd.char_text_mask`, `pd.char_predark`, `pd.predark_frame_count`.
- Produces: `matte_loopframes(framedir, name, out_base, clip=None, backdrop=None, apply_predark=True, is_kart=None, direction=None, predark_raw_tail=0)` — the new kwarg is what `process_all` passes in Task 10.

- [ ] **Step 1: Add the char setup block** (directly after the `_TEXT_BAND &= _IN_PLATE` line):

```python
# ── char blank-plate setup (once) — live-derived committed artifacts (spec 2026-07-17;
# build_blank_plate.py --screen char). char_P/char_A contribute geometry only.
_CHAR = pd.load_char_assets()
```

- [ ] **Step 2: Replace `_build_predark_frames`** (keep the same position in the file):

```python
def _build_predark_frames(paths, kart, apply_predark, predark_raw_tail=0):
    """Predark (or raw) BGR uint8 frame per path. Text mask computed once per segment from
    the segment median — for chars, from the PREDARK-ELIGIBLE frames only, so the departing-
    plate tail (last predark_raw_tail frames, passed raw per CHAR_PLATE_DEPART) can't pollute
    the median. Kart flourish keeps its whole-segment predark-off behaviour (raw_tail unused)."""
    n_pre = pd.predark_frame_count(len(paths), predark_raw_tail) if apply_predark else 0
    text = None
    if kart and apply_predark:
        sample = [cv2.imread(p).astype(np.float32) for p in paths[::3]]
        text = _kart_text_mask(np.median(np.stack(sample), axis=0))
    elif apply_predark and n_pre > 0:
        sample = [cv2.imread(p).astype(np.float32) for p in paths[:n_pre:3]]
        text = pd.char_text_mask(np.median(np.stack(sample), axis=0), _CHAR["text_band"])
    out = []
    for i, p in enumerate(paths):
        raw = cv2.imread(p)
        if not apply_predark or (not kart and i >= n_pre):
            out.append(raw)                              # flourish plate dropped/departing -> raw
        elif kart:
            out.append(_kart_predark(raw, text))
        else:
            out.append(pd.char_predark(raw, text, _CHAR))
    return out
```

- [ ] **Step 3: Thread the kwarg through `matte_loopframes`**

Signature becomes:

```python
def matte_loopframes(framedir, name, out_base, clip=None, backdrop=None,
                     apply_predark=True, is_kart=None, direction=None, predark_raw_tail=0):
```

Append to its docstring (after the `direction` sentence):

```
    `predark_raw_tail`: CHAR flourish only — number of trailing frames to pass RAW (the
    plate departs at cut-CHAR_PLATE_DEPART inside the export; predarking them painted a
    phantom plate). Callers pass extract_loop.CHAR_PLATE_DEPART - CHAR_CUT_GUARD (= 7).
```

and the call becomes:

```python
    pres = _build_predark_frames(paths, kart, apply_predark, predark_raw_tail)
```

- [ ] **Step 4: Import smoke under the GPU venv (loads rembg + the new artifacts; no GPU work)**

Run:
```powershell
C:\development\mkw-split-rewrite\temp\asset-venv-matte\Scripts\python.exe -c "import sys; sys.path.insert(0, 'tools/asset_matte'); import matte_blankplate as mb; import numpy as np; print('ok  char T_B median', float(np.median(mb._CHAR['T_B'][mb._CHAR['in_plate']])))"
```
Expected: `ok  char T_B median 0.6-0.8` (no traceback).

- [ ] **Step 5: Build-python test suite still green (matte_blankplate is never imported by tests)**

Run: `python -m pytest tests/ -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add tools/asset_matte/matte_blankplate.py
git commit -m "feat(asset-matte): route char predark through blank-plate assets + raw tail"
```

---

### Task 10: Plumb `flourish_fallback` + `predark_raw_tail` through `extract_segments`/`process_all`

**Files:**
- Modify: `tools/asset_matte/extract_loop.py` — `extract_segments` counts (~line 511)
- Modify: `tools/asset_matte/process_all.py` — seg loop (~lines 174–190)
- Test: `tests/test_char_predark.py` (append; `char_flourish_raw_tail` is pure)

**Interfaces:**
- Consumes: `el.CHAR_PLATE_DEPART`, `el.CHAR_CUT_GUARD`.
- Produces: `extract_segments` counts dict gains reserved key `"flourish_fallback": bool`; `process_all.char_flourish_raw_tail(kart: bool, seg: str, counts: dict) -> int`; manifest entries gain `"flourish_fallback"`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_char_predark.py
import process_all as pa


def test_char_flourish_raw_tail_rules():
    # char flourish with a real cut: the derived 7-frame raw tail
    assert pa.char_flourish_raw_tail(False, "flourish", {"flourish_fallback": False}) == 7
    # fallback flourish (no cut found): keep predark-all, unchanged legacy behaviour
    assert pa.char_flourish_raw_tail(False, "flourish", {"flourish_fallback": True}) == 0
    # never for karts or non-flourish segments
    assert pa.char_flourish_raw_tail(True, "flourish", {"flourish_fallback": False}) == 0
    assert pa.char_flourish_raw_tail(False, "idle", {}) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_char_predark.py -q`
Expected: FAIL — importing `process_all` succeeds (it does not import matte deps at top level? It DOES: `import matte_blankplate as mb` → rembg → ImportError under build python). **If the import fails**, place `char_flourish_raw_tail` in `extract_loop.py` instead (same signature, exported as `el.char_flourish_raw_tail`), update the test import accordingly, and have `process_all` call `el.char_flourish_raw_tail`. Re-run; expected: FAIL with AttributeError, then proceed.

- [ ] **Step 3: Implement**

In `tools/asset_matte/extract_loop.py`, `extract_segments()` — after `counts = {seg: (e - s) for seg, (s, e) in segs.items()}` add:

```python
    counts["flourish_fallback"] = bool(fell_back)      # reserved non-segment key (like idle_resume)
```

and append the pure rule (module level, near `CHAR_PLATE_DEPART`):

```python
def char_flourish_raw_tail(kart, seg, counts):
    """Trailing raw-frame count for matte_loopframes(predark_raw_tail=...): only a CHAR
    flourish whose hard cut was actually found (no fallback) has the deterministic
    departing-plate tail; karts keep predark-off-for-the-whole-flourish instead."""
    if kart or seg != "flourish" or counts.get("flourish_fallback"):
        return 0
    return CHAR_PLATE_DEPART - CHAR_CUT_GUARD
```

In `tools/asset_matte/process_all.py` seg loop, the matte call (~line 182) becomes:

```python
                matted[seg] = int(mb.matte_loopframes(
                    fd, segname, mattedir, clip=clip,
                    apply_predark=not (kart and seg == "flourish"), is_kart=kart,
                    predark_raw_tail=el.char_flourish_raw_tail(kart, seg, counts),
                    direction=mm.segment_direction(kart, seg)))
```

and the manifest write (~line 188) becomes:

```python
            manifest[name] = {"status": "done", "kart": kart,
                              "segments": matted, "idle_resume": idle_resume,
                              "flourish_fallback": bool(counts.get("flourish_fallback", False)),
                              "secs": round(time.time() - t0, 1)}
```

(Adjust the test to `el.char_flourish_raw_tail` per Step 2's note — `import extract_loop as el` is already in the test file.)

- [ ] **Step 4: Run tests + full suite**

Run: `python -m pytest tests/test_char_predark.py tests/test_extract_loop.py tests/test_process_all_dist.py -q` then `python -m pytest tests/ -q`
Expected: all pass (test_process_all_dist stubs the matte layer; the extra manifest key and kwarg must not break it — if a stub asserts exact kwargs, extend the stub, never the production call).

- [ ] **Step 5: Commit**

```bash
git add tools/asset_matte/extract_loop.py tools/asset_matte/process_all.py tests/test_char_predark.py
git commit -m "feat(asset-matte): flourish_fallback plumbed; char flourish raw tail wired"
```

---

### Task 11: `validate_char_predark.py` — sample-validation tool

**Files:**
- Create: `tools/asset_matte/validate_char_predark.py`

**Interfaces:**
- Consumes: a matte output dir (Task 12's sample run).
- Produces: per-char band-zoom + alpha×3 contact sheets and hard PASS/FAIL gates; exit 0 = pass.

- [ ] **Step 1: Write the tool**

```python
"""Validate re-matted standalone chars against the 2026-07-17 artifact symptoms.

  python tools/asset_matte/validate_char_predark.py --matte-dir <out>/matte \
      --chars peepa__base,penguin__base,rosalina__base,mario__base,luigi__base \
      --out temp/char_predark_validation

Sheets per char/segment: plate-band checker composite + alpha x3 heat, sampled across the
segment. Hard gates (exit 1 on failure):
  G1  peepa__base idle: plate-band alpha == 0 on every sampled frame (was 0 pre-fix; any
      junk the new predark introduced would show here first).
  G2  every char: flourish tail spike gone — mean band alpha over the LAST 6 frames must be
      <= 1.15 x the mean over the preceding 6 (old peepa spiked 93 -> 627, rosalina 19.4k ->
      23.3k at the tail; a settled hold pose is flat).
Everything subtler (text ghosts at low alpha) is for the eyetest sheets, not gated."""
import argparse
import glob
import os
import sys

import cv2
import numpy as np

BAND_Y = 860


def band_alpha(png):
    im = cv2.imread(png, cv2.IMREAD_UNCHANGED)
    return int(im[..., 3][BAND_Y:, :].astype(np.uint32).sum() / 255)


def checker(h, w, s=22):
    yy, xx = np.mgrid[0:h, 0:w]
    m = ((xx // s + yy // s) % 2 == 0)[..., None]
    return np.where(m, 205, 150).astype(np.uint8).repeat(3, axis=2)


def label(img, txt):
    im = img.copy()
    cv2.putText(im, txt, (6, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(im, txt, (6, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 1, cv2.LINE_AA)
    return im


def sheet_rows(pngs, idxs, tag):
    cells = []
    for i in idxs:
        im = cv2.imread(pngs[i], cv2.IMREAD_UNCHANGED)
        a = im[..., 3:4].astype(np.float32) / 255.0
        comp = (im[..., :3].astype(np.float32) * a
                + checker(*im.shape[:2]) * (1 - a)).astype(np.uint8)[BAND_Y:, :]
        heat = cv2.applyColorMap(np.clip(im[..., 3].astype(np.float32) * 3, 0, 255)
                                 .astype(np.uint8), cv2.COLORMAP_INFERNO)[BAND_Y:, :]
        cells += [label(comp, f"{tag}[{i}] checker"), label(heat, f"{tag}[{i}] alpha x3")]
    return cells


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--matte-dir", required=True)
    ap.add_argument("--chars", required=True, help="comma-separated item names")
    ap.add_argument("--out", default=os.path.join("temp", "char_predark_validation"))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    failures = []
    for name in a.chars.split(","):
        for seg in ("idle", "flourish"):
            d = os.path.join(a.matte_dir, f"{name}__{seg}_frames")
            pngs = sorted(glob.glob(os.path.join(d, "*.png")))
            if not pngs:
                failures.append(f"{name} {seg}: NO FRAMES at {d}")
                continue
            n = len(pngs)
            idxs = sorted(set([0, n // 4, n // 2, 3 * n // 4, n - 1]
                              + (list(range(max(0, n - 12), n)) if seg == "flourish" else [])))
            cells = sheet_rows(pngs, idxs, seg)
            h = max(c.shape[0] for c in cells); w = max(c.shape[1] for c in cells)
            cells = [cv2.copyMakeBorder(c, 0, h - c.shape[0], 0, w - c.shape[1],
                                        cv2.BORDER_CONSTANT, value=(30, 30, 30)) for c in cells]
            rows = [np.hstack(cells[i:i + 2]) for i in range(0, len(cells), 2)]
            out_png = os.path.join(a.out, f"{name}__{seg}.png")
            cv2.imwrite(out_png, np.vstack(rows))
            series = [band_alpha(p) for p in pngs]
            print(f"{name} {seg}: band-alpha min={min(series)} max={max(series)} -> {out_png}")
            if seg == "idle" and name == "peepa__base" and max(series) > 0:
                failures.append(f"G1 {name} idle: band alpha max {max(series)} != 0")
            if seg == "flourish" and n >= 12:
                pre = np.mean(series[-12:-6]); tail = np.mean(series[-6:])
                if tail > max(1.15 * pre, pre + 60):
                    failures.append(f"G2 {name} flourish tail spike: {pre:.0f} -> {tail:.0f}")
    if failures:
        print("FAIL\n" + "\n".join("  " + f for f in failures))
        sys.exit(1)
    print("PASS")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke the CLI parses + fails cleanly on a missing dir**

Run: `python tools/asset_matte/validate_char_predark.py --matte-dir C:\nope --chars peepa__base; echo $LASTEXITCODE`
Expected: `... NO FRAMES ...` then `FAIL`, exit code 1.

- [ ] **Step 3: Commit**

```bash
git add tools/asset_matte/validate_char_predark.py
git commit -m "feat(asset-matte): char predark sample-validation tool"
```

---

### Task 12: GPU sample run (5 chars) + validation

**Files:** none (generated sample output under `temp/`)

**Interfaces:**
- Consumes: everything above; the GPU venv; the D: clips.

- [ ] **Step 1: Stage the 5 sample clips (+ their events.json) into a temp clips dir**

```powershell
$src = "D:\kartoff\captures_sdr\en_uk\clips"; $dst = "temp\char_sample_clips"
New-Item -ItemType Directory -Force $dst | Out-Null
foreach ($n in "peepa__base","penguin__base","rosalina__base","mario__base","luigi__base") {
  Copy-Item "$src\$n.mkv" $dst; Copy-Item "$src\$n.events.json" $dst -ErrorAction SilentlyContinue
}
```

- [ ] **Step 2: Run the production batch path end-to-end on the sample (~8 min: 5 clips × ~90 s)**

```powershell
C:\development\mkw-split-rewrite\temp\asset-venv-matte\Scripts\python.exe tools/asset_matte/process_all.py --clips temp\char_sample_clips --out temp\char_sample_out --prefetch 1
```
Expected: `PROCESSED <name> ...` × 5, `DONE processed=5`, zero `ERROR` lines. (GPU note: do not run while a capture/recording is active on this box.)

- [ ] **Step 3: Run the validation gates + sheets**

```powershell
python tools/asset_matte/validate_char_predark.py --matte-dir temp\char_sample_out\matte --chars peepa__base,penguin__base,rosalina__base,mario__base,luigi__base --out temp\char_predark_validation
```
Expected: per-segment band-alpha lines, then `PASS` (exit 0). On `FAIL`: STOP — do not tune constants ad hoc; report which gate failed and return to the systematic-debugging loop against the spec.

- [ ] **Step 4: Read the sheets** (`temp\char_predark_validation\*.png`) — confirm visually: no readable text ghost in any alpha×3 panel at any sampled frame, no Mario shape anywhere, rosalina's dress hem clean and intact, penguin idle alpha stable frame-0-to-last (no fade-in growth).

- [ ] **Step 5: Full test suite one more time, then commit the run note**

```bash
python -m pytest tests/ -q
git commit --allow-empty -m "chore(asset-matte): 5-char GPU sample validated (gates PASS, sheets eyeballed)"
```

---

### Task 13: Handoff for eyetest (NO merge)

- [ ] **Step 1: Push nothing / merge nothing** — leave the branch in the worktree. Summarize for the user (Paul): branch name + head, the validation sheet paths (`temp\char_predark_validation\*.png`), the gate results, and the two follow-ups that wait on his eyetest:
  1. Merge decision (superpowers:finishing-a-development-branch).
  2. The full 153-item re-matte, after merge, on this box:
     `C:\development\mkw-split-rewrite\temp\asset-venv-matte\Scripts\python.exe tools/asset_matte/process_all.py --clips D:\kartoff\captures_sdr\en_uk\clips --out D:\kartoff\asset_chips` — **only the 153 standalone items need re-matting**: before running, clear their manifest entries and old `matte/<name>__*` dirs (kart combos stay `done` and are skipped). A one-liner to clear them:
     `python -c "import json,glob,os,shutil; mp=r'D:\kartoff\asset_chips\manifest.json'; m=json.load(open(mp)); names=[n for n in m if len(n.split('__'))==2]; [m.pop(n) for n in names]; json.dump(m,open(mp,'w'),indent=1); [shutil.rmtree(p,ignore_errors=True) for n in names for p in glob.glob(rf'D:\kartoff\asset_chips\matte\{n}__*_frames')]; print(len(names),'cleared')"`
     (~4 GPU-hr; do not run concurrently with recording.)

---

## Self-Review

- **Spec coverage:** artifacts+builder (T7/T8), `_char_predark` port (T5, pure in pre_darken per the build-python constraint; matte_blankplate wiring T9), HSV text mask + band (T2/T3), tail gate constant/derivation/plumbing (T4/T10), fallback rule (T10), legacy note (T6), validation ladder (T11/T12), kart byte-identical (global constraint + T5 Step 5 + T10 stub note), eyetest gate + batch re-matte (T13). Spec's "module-level char setup beside the kart one" is honored by `_CHAR` in T9 with the pure math in pre_darken — deviation from the spec's letter, kept for testability, matches its intent.
- **Placeholder scan:** none — every step carries code or an exact command + expected output. The one conditional (T10 Step 2 import-fails fallback) specifies both branches concretely.
- **Type consistency:** `assets` dict keys identical in T5 (`char_predark`), T6 (`load_char_assets`), T9 (`_CHAR[...]`); `predark_raw_tail` name identical in T9/T10; `char_flourish_raw_tail(kart, seg, counts)` identical in T10 test + impl; `CHAR_PLATE_DEPART`/`CHAR_CUT_GUARD` referenced consistently.
