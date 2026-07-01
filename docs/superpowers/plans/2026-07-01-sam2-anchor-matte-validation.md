# SAM2-anchor Matte Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove by eye whether a SAM2-refined anchor mask (localized by birefnet, then propagated by MatAnyone2) mattes better than today's birefnet-only anchor, via an offline side-by-side viewer — no production code changed.

**Architecture:** One reusable, dependency-light module (`sam2_anchor.py`, pure numpy) derives a SAM2 prompt from a birefnet rough mask and picks the best SAM2 candidate. Three throwaway per-venv driver scripts run the engines **sequentially in separate processes** (birefnet → SAM2 → MatAnyone2), writing intermediates to the session scratchpad, sidestepping the onnxruntime-vs-torch GPU-monopoly problem. A stdlib HTML viewer renders the two mattes side by side.

**Tech Stack:** Python; numpy (all venvs); birefnet/rembg (`temp/asset-venv-matte`); SAM2 (`temp/sam2-venv`, torch cu128); MatAnyone2 (`temp/asset-venv-matte`, torch cu128); cv2/PIL for compositing (asset-venv-matte); stdlib for the viewer.

## Global Constraints

- **No production code touched.** Do not modify `matte_blankplate.py`, `matte_matanyone.py`, `process_all.py`, or the console. The only repo file added under an existing module dir is `tools/asset_matte/sam2_anchor.py` (new, reusable) + its test; drivers live under `tools/asset_matte/sam2val/`.
- **`sam2_anchor.py` must import only numpy at module top** (no torch, no cv2, no sam2) — it runs in build python (tests), `asset-venv-matte`, and `sam2-venv`. SAM2 is reached only through a `predictor` object passed in.
- **Process isolation is the whole point:** the birefnet stage may import rembg; the SAM2 stage and the MatAnyone2 stage must **never** import rembg/onnxruntime or `matte_blankplate` (which pulls rembg). Stage C imports only `matte_matanyone` (torch-only) + numpy/cv2.
- **Clips dir:** `D:/kartoff/captures_sdr/en_uk/clips` (5 clips: `mario__base`, `mario__base__hot_rod`, `mario__base__plushbuggy`, `mario__base__standard_kart`, `mario__base__zoom_buggy`).
- **Output root (scratchpad, not repo):** `C:/Users/Paul/AppData/Local/Temp/claude/C--development-mkw-split-rewrite/209024a0-084e-4c91-8a28-32378aa23e69/scratchpad/sam2val_out`
- **Venv pythons:** birefnet/matanyone = `temp/asset-venv-matte/Scripts/python.exe`; SAM2 = `temp/sam2-venv/Scripts/python.exe`; build/tests/viewer = system `python` (Python 3.14, has numpy+cv2).
- **SAM2 model:** ckpt `temp/sam2_ckpt/sam2.1_hiera_base_plus.pt`, config `configs/sam2.1/sam2.1_hiera_b+.yaml`.
- **Matte mode:** forward-only (`bidir=False`) — production CLI default, and it isolates the anchor as the only variable.
- Run all commands from repo root `C:/development/mkw-split-rewrite`.

## Data layout (under Output root)

```
sam2val_out/
  work/<subject>__<seg>/
    frames/NNN.png        predark input frames        (stage A, PNG for stage C)
    anchor_rgb.npy        frame-0 RGB uint8 HxWx3     (stage A -> stage B)
    biref_anchor.npy      frame-0 birefnet mask 0/255 (stage A -> stage B & C)
    sam2_anchor.npy       frame-0 SAM2 mask 0/255     (stage B -> stage C)
    meta.json             {kart, seg, n}              (stage A)
  view/<subject>__<seg>/
    biref/NNN.png         checker-composited matte    (stage C)
    sam2/NNN.png          checker-composited matte    (stage C)
    anchors.png           biref | sam2 | overlay      (stage C)
  index.html                                          (viewer)
```

`<subject>__<seg>` examples: `mario__base__idle`, `mario__base__standard_kart__idle`, `mario__base__standard_kart__spawn`.

---

### Task 1: `sam2_anchor.py` — prompt derivation + best-candidate selection (pure numpy, TDD)

**Files:**
- Create: `tools/asset_matte/sam2_anchor.py`
- Test: `tests/test_sam2_anchor.py`

**Interfaces:**
- Consumes: nothing (leaf module).
- Produces:
  - `mask_bbox(mask, pad_frac=0.04) -> np.float32[4]` `[x1,y1,x2,y2]`, clamped to frame; raises `ValueError` on empty mask.
  - `positive_points(mask, n=3, seed=0) -> np.float32[k,2]` `(x,y)`, `k<=n`, sampled from mask interior.
  - `corner_points(h, w, inset=6) -> np.float32[4,2]`.
  - `build_prompt(mask, pad_frac=0.04, n_pos=3, seed=0) -> {"box":[4], "point_coords":[k,2], "point_labels":[k] int32}`.
  - `iou(a, b) -> float`.
  - `select_best(candidates, ref) -> int`.
  - `sam2_anchor_mask(predictor, image_rgb, biref_mask, pad_frac=0.04, n_pos=3, seed=0) -> np.uint8 HxW (0/255)` — prompts a built SAM2 `predictor` (set_image + predict) and returns the best-IoU candidate. (Not unit-tested; exercised in Task 3.)

- [ ] **Step 1: Write the failing test**

Create `tests/test_sam2_anchor.py`:

```python
import numpy as np
import sam2_anchor as sa      # FLAT import — conftest adds tools/asset_matte to sys.path


def _box_mask(h=20, w=30, x1=5, y1=4, x2=15, y2=12):
    m = np.zeros((h, w), np.uint8)
    m[y1:y2, x1:x2] = 255
    return m


def test_mask_bbox_tight_and_clamped():
    m = _box_mask()
    b = sa.mask_bbox(m, pad_frac=0.0)
    assert list(b) == [5, 4, 14, 11]          # inclusive max index, no pad
    # heavy pad clamps inside the frame
    b2 = sa.mask_bbox(m, pad_frac=5.0)
    assert b2[0] >= 0 and b2[1] >= 0 and b2[2] <= 29 and b2[3] <= 19


def test_mask_bbox_empty_raises():
    try:
        sa.mask_bbox(np.zeros((5, 5), np.uint8))
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_positive_points_inside_mask():
    m = _box_mask()
    pts = sa.positive_points(m, n=3, seed=1)
    assert 1 <= len(pts) <= 3
    for x, y in pts:
        assert m[int(y), int(x)] == 255


def test_corner_points_are_four_corners():
    c = sa.corner_points(20, 30, inset=2)
    assert set(map(tuple, c.astype(int))) == {(2, 2), (27, 2), (2, 17), (27, 17)}


def test_iou_known_values():
    a = np.zeros((4, 4), np.uint8); a[:2, :2] = 1
    b = np.zeros((4, 4), np.uint8); b[:2, :2] = 1
    assert sa.iou(a, b) == 1.0
    b2 = np.zeros((4, 4), np.uint8); b2[2:, 2:] = 1
    assert sa.iou(a, b2) == 0.0
    assert sa.iou(np.zeros((4, 4), np.uint8), np.zeros((4, 4), np.uint8)) == 0.0


def test_select_best_picks_highest_overlap():
    ref = _box_mask()
    good = _box_mask()                          # identical -> IoU 1
    bad = np.zeros_like(ref); bad[0:2, 0:2] = 255
    assert sa.select_best([bad, good], ref) == 1


def test_build_prompt_labels_positive_then_negative():
    p = sa.build_prompt(_box_mask(), n_pos=3, seed=0)
    labels = p["point_labels"]
    assert labels.tolist() == [1] * (len(labels) - 4) + [0, 0, 0, 0]
    assert p["box"].shape == (4,)
    assert p["point_coords"].shape[0] == len(labels)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sam2_anchor.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sam2_anchor'`.

- [ ] **Step 3: Write minimal implementation**

Create `tools/asset_matte/sam2_anchor.py`:

```python
"""Derive a SAM2 prompt from a birefnet rough mask, and pick the SAM2 candidate that best agrees
with birefnet. Pure numpy (no torch/cv2/sam2 at import) so it unit-tests under build python and
imports cleanly in every venv. The one SAM2-touching function takes an already-built predictor."""
import numpy as np


def mask_bbox(mask, pad_frac=0.04):
    """Padded, frame-clamped [x1,y1,x2,y2] float bbox of nonzero `mask` (HxW). Pads by pad_frac of
    the box's own width/height. Raises ValueError if the mask is empty."""
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        raise ValueError("empty mask")
    h, w = mask.shape
    x1, x2, y1, y2 = xs.min(), xs.max(), ys.min(), ys.max()
    pw, ph = (x2 - x1) * pad_frac, (y2 - y1) * pad_frac
    return np.array([max(0, x1 - pw), max(0, y1 - ph),
                     min(w - 1, x2 + pw), min(h - 1, y2 + ph)], dtype=np.float32)


def _interior(mask):
    """Boolean HxW of pixels whose 4-neighbours are all set (cheap 1px erosion, numpy-only)."""
    m = mask.astype(bool)
    e = m.copy()
    e[1:, :] &= m[:-1, :]; e[:-1, :] &= m[1:, :]
    e[:, 1:] &= m[:, :-1]; e[:, :-1] &= m[:, 1:]
    return e


def positive_points(mask, n=3, seed=0):
    """`n` (x,y) interior points, spatially spread by greedy farthest-point. Deterministic given
    seed. Falls back to the raw mask if the interior is empty; returns fewer than n if scarce."""
    e = _interior(mask)
    ys, xs = np.nonzero(e if e.any() else mask)
    pts = np.stack([xs, ys], 1).astype(np.float32)
    if len(pts) <= n:
        return pts
    rng = np.random.default_rng(seed)
    chosen = [int(rng.integers(len(pts)))]
    d = np.full(len(pts), np.inf)
    for _ in range(1, n):
        d = np.minimum(d, ((pts - pts[chosen[-1]]) ** 2).sum(1))
        chosen.append(int(np.argmax(d)))
    return pts[chosen]


def corner_points(h, w, inset=6):
    """The 4 frame corners (x,y), inset a few px — negatives that reject in-box background."""
    i = inset
    return np.array([[i, i], [w - 1 - i, i], [i, h - 1 - i], [w - 1 - i, h - 1 - i]], np.float32)


def build_prompt(mask, pad_frac=0.04, n_pos=3, seed=0):
    """SAM2 prompt dict from a birefnet binary mask: padded box + n_pos positive interior points
    (label 1) + 4 corner negatives (label 0)."""
    pos = positive_points(mask, n_pos, seed)
    neg = corner_points(*mask.shape)
    coords = np.concatenate([pos, neg], 0).astype(np.float32)
    labels = np.concatenate([np.ones(len(pos)), np.zeros(len(neg))]).astype(np.int32)
    return {"box": mask_bbox(mask, pad_frac), "point_coords": coords, "point_labels": labels}


def iou(a, b):
    """IoU of two binary masks (HxW); 0.0 if the union is empty."""
    a = a.astype(bool); b = b.astype(bool)
    u = int((a | b).sum())
    return float((a & b).sum() / u) if u else 0.0


def select_best(candidates, ref):
    """Index of the candidate (list of HxW binaries) with highest IoU vs the birefnet reference —
    agrees with birefnet where it's confident, free to fill its blind spots elsewhere."""
    return int(np.argmax([iou(c, ref) for c in candidates]))


def sam2_anchor_mask(predictor, image_rgb, biref_mask, pad_frac=0.04, n_pos=3, seed=0):
    """Prompt a built SAM2 `predictor` (set_image + predict) from the birefnet mask and return the
    best-IoU candidate as HxW uint8 (0/255). `image_rgb` is HxWx3 uint8 RGB."""
    ref = np.asarray(biref_mask).astype(bool)
    p = build_prompt(ref, pad_frac, n_pos, seed)
    predictor.set_image(image_rgb)
    masks, scores, _ = predictor.predict(
        point_coords=p["point_coords"], point_labels=p["point_labels"],
        box=p["box"], multimask_output=True)
    masks = np.asarray(masks).astype(bool)
    if masks.ndim == 4:                     # (1,C,H,W) if the box batches -> drop batch dim
        masks = masks[0]
    best = select_best(list(masks), ref)
    return (masks[best].astype(np.uint8) * 255)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_sam2_anchor.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add tools/asset_matte/sam2_anchor.py tests/test_sam2_anchor.py
git commit -m "feat(asset-matte): SAM2 anchor prompt derivation + best-IoU selection (pure numpy)"
```

---

### Task 2: Stage A driver — birefnet anchor + predark frames

**Files:**
- Create: `tools/asset_matte/sam2val/stage_a_birefnet.py`

**Interfaces:**
- Consumes: `extract_loop.extract_segments(clip, out_base, name) -> {seg: count}`; `matte_blankplate._build_predark_frames(paths, kart, apply_predark) -> [BGR uint8]`; `matte_blankplate._birefnet(bgr) -> (alpha_float32, bgr)`; `matte_blankplate.is_kart_combo(name) -> bool`.
- Produces (per `<subject>__<seg>` under `work/`): `frames/NNN.png`, `anchor_rgb.npy`, `biref_anchor.npy`, `meta.json`. Runs in `asset-venv-matte` (birefnet only).

- [ ] **Step 1: Write the driver**

Create `tools/asset_matte/sam2val/stage_a_birefnet.py`:

```python
"""Stage A (asset-venv-matte, birefnet/onnxruntime): per subject, extract the wanted segments,
predark them, and dump the frame-0 birefnet anchor. Writes work/<subject>__<seg>/{frames,anchor}.
NEVER imports matanyone. Run from repo root:

  temp/asset-venv-matte/Scripts/python.exe tools/asset_matte/sam2val/stage_a_birefnet.py
"""
import glob
import json
import os
import sys

import cv2
import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
for _p in (os.path.join(_REPO, "tools", "asset_matte"), _REPO):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import extract_loop as el
import matte_blankplate as mb

CLIPS = "D:/kartoff/captures_sdr/en_uk/clips"
OUT = ("C:/Users/Paul/AppData/Local/Temp/claude/C--development-mkw-split-rewrite/"
       "209024a0-084e-4c91-8a28-32378aa23e69/scratchpad/sam2val_out")

# (subject, [segments]) — idle for all 5; spawn also for one kart (off-center test).
JOBS = [
    ("mario__base", ["idle"]),
    ("mario__base__hot_rod", ["idle"]),
    ("mario__base__plushbuggy", ["idle"]),
    ("mario__base__standard_kart", ["idle", "spawn"]),
    ("mario__base__zoom_buggy", ["idle"]),
]


def run_subject(subject, wanted):
    clip = os.path.join(CLIPS, subject + ".mkv")
    kart = el.is_kart_combo(subject)
    seg_raw = os.path.join(OUT, "raw", subject)        # kept out of work/ so stage B/C glob(work/*) is clean
    os.makedirs(seg_raw, exist_ok=True)
    counts = el.extract_segments(clip, seg_raw, subject)          # writes <seg_raw>/<subject>__<seg>/NNN.png
    print(f"{subject}: segments={counts} kart={kart}", flush=True)
    for seg in wanted:
        if seg not in counts:
            print(f"  !! {seg} not detected for {subject}; skipping", flush=True)
            continue
        raw_dir = os.path.join(seg_raw, f"{subject}__{seg}")
        paths = sorted(glob.glob(os.path.join(raw_dir, "*.png")))
        pres = mb._build_predark_frames(paths, kart, apply_predark=True)   # BGR uint8 list
        work = os.path.join(OUT, "work", f"{subject}__{seg}")
        fdir = os.path.join(work, "frames")
        os.makedirs(fdir, exist_ok=True)
        for i, bgr in enumerate(pres):
            cv2.imwrite(os.path.join(fdir, f"{i:03d}.png"), bgr)
        anchor_bgr = pres[0]
        biref = (mb._birefnet(anchor_bgr)[0] > 0.5).astype(np.uint8) * 255
        np.save(os.path.join(work, "anchor_rgb.npy"),
                cv2.cvtColor(anchor_bgr, cv2.COLOR_BGR2RGB))
        np.save(os.path.join(work, "biref_anchor.npy"), biref)
        with open(os.path.join(work, "meta.json"), "w") as f:
            json.dump({"kart": bool(kart), "seg": seg, "n": len(pres)}, f)
        print(f"  {seg}: {len(pres)}f  biref_px={int((biref > 0).sum())}", flush=True)


def main():
    for subject, wanted in JOBS:
        run_subject(subject, wanted)
    print("STAGE_A DONE", flush=True)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it (real smoke — this is the verification)**

Run:
```bash
temp/asset-venv-matte/Scripts/python.exe tools/asset_matte/sam2val/stage_a_birefnet.py
```
Expected: prints `segments={...}` per subject, a `biref_px=` count in the tens-of-thousands for each segment, and `STAGE_A DONE`.

- [ ] **Step 3: Verify outputs exist and are well-formed**

Run:
```bash
python -c "import numpy as np, glob, os; \
b='C:/Users/Paul/AppData/Local/Temp/claude/C--development-mkw-split-rewrite/209024a0-084e-4c91-8a28-32378aa23e69/scratchpad/sam2val_out/work'; \
d=os.path.join(b,'mario__base__idle'); \
m=np.load(os.path.join(d,'biref_anchor.npy')); r=np.load(os.path.join(d,'anchor_rgb.npy')); \
print('mask', m.shape, m.dtype, 'nz', int((m>0).sum())); \
print('rgb', r.shape, r.dtype); \
print('frames', len(glob.glob(os.path.join(d,'frames','*.png'))))"
```
Expected: `mask (1080, 988) uint8 nz <big>`, `rgb (1080, 988, 3) uint8`, `frames <N>` (≥ ~90).

- [ ] **Step 4: Commit**

```bash
git add tools/asset_matte/sam2val/stage_a_birefnet.py
git commit -m "feat(sam2val): stage A — birefnet anchor + predark frames driver"
```

---

### Task 3: Stage B driver — SAM2 anchor

**Files:**
- Create: `tools/asset_matte/sam2val/stage_b_sam2.py`

**Interfaces:**
- Consumes: `sam2_anchor.sam2_anchor_mask(predictor, image_rgb, biref_mask) -> uint8 HxW`; stage A's `anchor_rgb.npy` + `biref_anchor.npy`. Builds `SAM2ImagePredictor` in `sam2-venv`.
- Produces (per `<subject>__<seg>`): `sam2_anchor.npy` (uint8 0/255). Runs in `sam2-venv` (torch/sam2 only; numpy for I/O).

- [ ] **Step 1: Write the driver**

Create `tools/asset_matte/sam2val/stage_b_sam2.py`:

```python
"""Stage B (sam2-venv, torch+sam2): read each stage-A anchor frame + birefnet mask and write the
SAM2-refined anchor mask. NEVER imports rembg/onnxruntime/matte_blankplate. Run from repo root:

  temp/sam2-venv/Scripts/python.exe tools/asset_matte/sam2val/stage_b_sam2.py
"""
import glob
import os
import sys

import numpy as np
import torch
from sam2.build_sam import build_sam2
from sam2.sam2_image_predictor import SAM2ImagePredictor

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(_REPO, "tools", "asset_matte"))     # flat import of sam2_anchor
import sam2_anchor as sa

OUT = ("C:/Users/Paul/AppData/Local/Temp/claude/C--development-mkw-split-rewrite/"
       "209024a0-084e-4c91-8a28-32378aa23e69/scratchpad/sam2val_out")
CKPT = os.path.join(_REPO, "temp", "sam2_ckpt", "sam2.1_hiera_base_plus.pt")
CFG = "configs/sam2.1/sam2.1_hiera_b+.yaml"


def main():
    predictor = SAM2ImagePredictor(build_sam2(CFG, CKPT, device="cuda"))
    work = os.path.join(OUT, "work")
    dirs = sorted(d for d in glob.glob(os.path.join(work, "*"))
                  if os.path.exists(os.path.join(d, "biref_anchor.npy")))
    for d in dirs:
        rgb = np.load(os.path.join(d, "anchor_rgb.npy"))
        biref = np.load(os.path.join(d, "biref_anchor.npy"))
        with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
            mask = sa.sam2_anchor_mask(predictor, rgb, biref)
        np.save(os.path.join(d, "sam2_anchor.npy"), mask)
        name = os.path.basename(d)
        print(f"{name}: sam2_px={int((mask > 0).sum())}  biref_px={int((biref > 0).sum())}", flush=True)
    print("STAGE_B DONE", flush=True)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it (real smoke — the verification)**

Run:
```bash
temp/sam2-venv/Scripts/python.exe tools/asset_matte/sam2val/stage_b_sam2.py
```
Expected: one `sam2_px= ... biref_px= ...` line per segment dir and `STAGE_B DONE`. `sam2_px` should be the same order of magnitude as `biref_px` (a full subject mask), **not** ~0 (missed) and not ~1,000,000 (grabbed the whole frame = 988×1080≈1.07M).

- [ ] **Step 3: Verify the SAM2 mask is a plausible subject, not the whole frame or empty**

Run:
```bash
python -c "import numpy as np, os; \
b='C:/Users/Paul/AppData/Local/Temp/claude/C--development-mkw-split-rewrite/209024a0-084e-4c91-8a28-32378aa23e69/scratchpad/sam2val_out/work/mario__base__idle'; \
s=np.load(os.path.join(b,'sam2_anchor.npy')); tot=s.size; nz=int((s>0).sum()); \
print('sam2 frac', round(nz/tot,3)); \
assert 0.02 < nz/tot < 0.85, 'implausible coverage'"
```
Expected: `sam2 frac` between ~0.05 and ~0.5, assertion passes.

- [ ] **Step 4: Commit**

```bash
git add tools/asset_matte/sam2val/stage_b_sam2.py
git commit -m "feat(sam2val): stage B — SAM2 anchor from birefnet localization"
```

---

### Task 4: Stage C driver — MatAnyone2 ×2 + checker composites

**Files:**
- Create: `tools/asset_matte/sam2val/stage_c_matanyone.py`

**Interfaces:**
- Consumes: `matte_matanyone.matte_segment(frames_bgr, first_mask_u8, last_mask_u8, bidir=False) -> [HxW float01]`; stage A `frames/`, `biref_anchor.npy`; stage B `sam2_anchor.npy`.
- Produces (per `<subject>__<seg>` under `view/`): `biref/NNN.png`, `sam2/NNN.png` (checker-composited), `anchors.png`. Runs in `asset-venv-matte`; imports **only** `matte_matanyone` (torch), numpy, cv2 — never `matte_blankplate`/rembg.

- [ ] **Step 1: Write the driver**

Create `tools/asset_matte/sam2val/stage_c_matanyone.py`:

```python
"""Stage C (asset-venv-matte, torch MatAnyone2 IN-PROCESS — no birefnet in this process, so no
GPU-monopoly): for each segment, matte forward-only twice (birefnet anchor vs SAM2 anchor) and
write checker-composited PNGs + an anchor-diff image. Run from repo root:

  temp/asset-venv-matte/Scripts/python.exe tools/asset_matte/sam2val/stage_c_matanyone.py
"""
import glob
import os
import sys

import cv2
import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..")))   # tools/asset_matte for flat import
import matte_matanyone as mm                            # torch only; NEVER import matte_blankplate here

OUT = ("C:/Users/Paul/AppData/Local/Temp/claude/C--development-mkw-split-rewrite/"
       "209024a0-084e-4c91-8a28-32378aa23e69/scratchpad/sam2val_out")


def _checker(h, w, s=22, a=205, b=150):
    yy, xx = np.mgrid[0:h, 0:w]
    m = ((xx // s + yy // s) % 2 == 0)
    return np.where(m[..., None], a, b).astype(np.uint8).repeat(3, 2)   # HxWx3 RGB


def _composite(frames_bgr, alphas, dst):
    os.makedirs(dst, exist_ok=True)
    h, w = alphas[0].shape
    chk = _checker(h, w)
    for i, (bgr, al) in enumerate(zip(frames_bgr, alphas)):
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB).astype(np.float32)
        a = np.clip(al, 0, 1)[..., None]
        comp = (chk * (1 - a) + rgb * a).astype(np.uint8)
        cv2.imwrite(os.path.join(dst, f"{i:03d}.png"), cv2.cvtColor(comp, cv2.COLOR_RGB2BGR))


def _anchor_diff(rgb_frame_bgr, biref, sam2, path):
    """biref | sam2 | overlay(red=biref-only, green=sam2-only, yellow=both) on the frame."""
    b = (biref > 0); s = (sam2 > 0)
    over = cv2.cvtColor(rgb_frame_bgr, cv2.COLOR_BGR2RGB).copy()
    over[b & ~s] = [255, 0, 0]; over[s & ~b] = [0, 255, 0]; over[b & s] = [255, 255, 0]
    col = lambda m: cv2.cvtColor((m > 0).astype(np.uint8) * 255, cv2.COLOR_GRAY2RGB)
    sheet = np.concatenate([col(biref), col(sam2), over], axis=1)
    cv2.imwrite(path, cv2.cvtColor(sheet, cv2.COLOR_RGB2BGR))


def run_seg(d):
    name = os.path.basename(d)
    frames = [cv2.imread(p) for p in sorted(glob.glob(os.path.join(d, "frames", "*.png")))]
    biref = np.load(os.path.join(d, "biref_anchor.npy"))
    sam2 = np.load(os.path.join(d, "sam2_anchor.npy"))
    view = os.path.join(OUT, "view", name)
    os.makedirs(view, exist_ok=True)
    for tag, anchor in (("biref", biref), ("sam2", sam2)):
        alphas = mm.matte_segment(frames, anchor, anchor, bidir=False)
        _composite(frames, alphas, os.path.join(view, tag))
        print(f"  {name}/{tag}: {len(alphas)}f", flush=True)
    _anchor_diff(frames[0], biref, sam2, os.path.join(view, "anchors.png"))


def main():
    work = os.path.join(OUT, "work")
    dirs = sorted(d for d in glob.glob(os.path.join(work, "*"))
                  if os.path.exists(os.path.join(d, "sam2_anchor.npy")))
    for d in dirs:
        print(f"--- {os.path.basename(d)}", flush=True)
        run_seg(d)
    print("STAGE_C DONE", flush=True)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it (real smoke — the verification)**

Run:
```bash
temp/asset-venv-matte/Scripts/python.exe tools/asset_matte/sam2val/stage_c_matanyone.py
```
Expected: per segment, `.../biref: Nf` and `.../sam2: Nf` lines, then `STAGE_C DONE`. No CUDA thrash (each matte is seconds, since no birefnet runs in this process).

- [ ] **Step 3: Verify composites exist**

Run:
```bash
python -c "import glob, os; \
v='C:/Users/Paul/AppData/Local/Temp/claude/C--development-mkw-split-rewrite/209024a0-084e-4c91-8a28-32378aa23e69/scratchpad/sam2val_out/view/mario__base__idle'; \
print('biref', len(glob.glob(os.path.join(v,'biref','*.png')))); \
print('sam2', len(glob.glob(os.path.join(v,'sam2','*.png')))); \
print('anchors', os.path.exists(os.path.join(v,'anchors.png')))"
```
Expected: `biref <N>`, `sam2 <N>` (equal, ≥ ~90), `anchors True`.

- [ ] **Step 4: Commit**

```bash
git add tools/asset_matte/sam2val/stage_c_matanyone.py
git commit -m "feat(sam2val): stage C — MatAnyone2 forward matte x2 + checker composites"
```

---

### Task 5: HTML viewer

**Files:**
- Create: `tools/asset_matte/sam2val/build_viewer.py`

**Interfaces:**
- Consumes: `view/<subject>__<seg>/{biref,sam2}/NNN.png` + `anchors.png`.
- Produces: `view` root `index.html` (self-contained, references PNGs by relative path). Stdlib only.

- [ ] **Step 1: Write the viewer builder**

Create `tools/asset_matte/sam2val/build_viewer.py`:

```python
"""Build a self-contained index.html scrubber over view/<subject>__<seg>/. Subject dropdown,
biref<->sam2 toggle, frame slider (swaps the <img> src), and the anchor-diff image. Stdlib only.

  python tools/asset_matte/sam2val/build_viewer.py
"""
import glob
import json
import os

OUT = ("C:/Users/Paul/AppData/Local/Temp/claude/C--development-mkw-split-rewrite/"
       "209024a0-084e-4c91-8a28-32378aa23e69/scratchpad/sam2val_out")


def main():
    view = os.path.join(OUT, "view")
    segs = {}
    for d in sorted(glob.glob(os.path.join(view, "*"))):
        if not os.path.isdir(d):
            continue
        name = os.path.basename(d)
        n = len(glob.glob(os.path.join(d, "biref", "*.png")))
        if n:
            segs[name] = n
    data = json.dumps(segs)
    html = """<!doctype html><meta charset=utf-8><title>SAM2 vs birefnet anchor</title>
<style>
 body{margin:0;background:#1a1a1a;color:#ddd;font:13px system-ui}
 header{padding:8px 12px;display:flex;gap:14px;align-items:center;flex-wrap:wrap}
 select,button{font:13px system-ui;padding:3px 8px}
 button.on{background:#3a7;color:#000}
 #stage{display:flex;gap:12px;padding:0 12px 12px;align-items:flex-start}
 #stage img{background:#000;max-height:78vh;image-rendering:pixelated}
 #anchors{max-height:78vh}
 .col{display:flex;flex-direction:column;gap:4px}
 label{opacity:.7}
</style>
<header>
 <select id=subj></select>
 <button id=tgl class=on>anchor: SAM2</button>
 <input id=frame type=range min=0 value=0 style=width:340px>
 <span id=fnum></span>
 <label>(toggle compares the two mattes on the same frame; right = anchor diff)</label>
</header>
<div id=stage>
 <div class=col><label id=mlabel></label><img id=matte></div>
 <div class=col><label>anchors: red=birefnet-only  green=SAM2-only  yellow=both</label><img id=anchors></div>
</div>
<script>
const SEGS=__DATA__;
const names=Object.keys(SEGS);
const subj=document.getElementById('subj'), tgl=document.getElementById('tgl'),
      frame=document.getElementById('frame'), fnum=document.getElementById('fnum'),
      matte=document.getElementById('matte'), anchors=document.getElementById('anchors'),
      mlabel=document.getElementById('mlabel');
let anchor='sam2';
names.forEach(n=>{const o=document.createElement('option');o.value=n;o.textContent=n+' ('+SEGS[n]+'f)';subj.appendChild(o);});
function pad(i){return String(i).padStart(3,'0');}
function render(){
 const n=subj.value, i=+frame.value;
 matte.src=n+'/'+anchor+'/'+pad(i)+'.png';
 anchors.src=n+'/anchors.png';
 fnum.textContent=i+' / '+(SEGS[n]-1);
 mlabel.textContent='matte anchor = '+anchor;
}
function selectSeg(){frame.max=SEGS[subj.value]-1; if(+frame.value>frame.max)frame.value=0; render();}
subj.onchange=selectSeg;
frame.oninput=render;
tgl.onclick=()=>{anchor=(anchor==='sam2')?'biref':'sam2'; tgl.textContent='anchor: '+(anchor==='sam2'?'SAM2':'birefnet'); tgl.classList.toggle('on',anchor==='sam2'); render();};
subj.value=names[0]; selectSeg();
</script>
""".replace("__DATA__", data)
    path = os.path.join(view, "index.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print("wrote", path, "segments:", len(segs), flush=True)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it**

Run:
```bash
python tools/asset_matte/sam2val/build_viewer.py
```
Expected: `wrote .../view/index.html segments: 6`.

- [ ] **Step 3: Verify the HTML embeds the segment map**

Run:
```bash
python -c "p='C:/Users/Paul/AppData/Local/Temp/claude/C--development-mkw-split-rewrite/209024a0-084e-4c91-8a28-32378aa23e69/scratchpad/sam2val_out/view/index.html'; h=open(p,encoding='utf-8').read(); assert 'mario__base__idle' in h and '__DATA__' not in h; print('ok', len(h), 'bytes')"
```
Expected: `ok <n> bytes` (placeholder substituted, at least one segment present).

- [ ] **Step 4: Commit**

```bash
git add tools/asset_matte/sam2val/build_viewer.py
git commit -m "feat(sam2val): HTML scrubber comparing SAM2 vs birefnet anchor mattes"
```

---

### Task 6: Full run + present to user

**Files:** none (orchestration + judgement).

**Interfaces:** Consumes all four drivers.

- [ ] **Step 1: Run the full three-stage pipeline over all 6 segments**

Run, in order (each must print its `DONE`):
```bash
temp/asset-venv-matte/Scripts/python.exe tools/asset_matte/sam2val/stage_a_birefnet.py
temp/sam2-venv/Scripts/python.exe tools/asset_matte/sam2val/stage_b_sam2.py
temp/asset-venv-matte/Scripts/python.exe tools/asset_matte/sam2val/stage_c_matanyone.py
python tools/asset_matte/sam2val/build_viewer.py
```
Expected: `STAGE_A DONE`, `STAGE_B DONE`, `STAGE_C DONE`, `wrote .../index.html segments: 6`.

- [ ] **Step 2: Sanity-read the anchor-diff images**

Read each `view/<subject>__<seg>/anchors.png` (there are 6) with the Read tool. Confirm for `mario__base__idle`: the overlay shows **green** (SAM2-only) filling the neck-blob region that birefnet drops. Note any subject where SAM2 grabbed background (large stray green outside the subject) or lost thin parts (red where SAM2 should agree).

- [ ] **Step 3: Present findings to the user**

Tell the user the viewer is at `.../sam2val_out/view/index.html` (they open it in a browser), summarize the anchor-diff observations per subject (neck-blob recovered? off-center spawn OK? any regressions?), and give a recommendation on whether SAM2 anchoring is worth a production-integration session. Do not integrate — that is a separate, approved-later step.

---

## Notes for the executor

- **Stage order is mandatory:** A (birefnet) → B (SAM2) → C (MatAnyone2). They are separate processes/venvs precisely so onnxruntime and torch never share a process. Never merge them.
- If stage A reports a segment "not detected" (e.g. a char has no `spawn`), that's expected — only `mario__base__standard_kart` requests `spawn`.
- If SAM2 coverage is implausible on some subject (Task 3 assertion), first inspect that subject's `anchors.png`; the likely fix is a prompt tweak in `sam2_anchor.build_prompt` (more positive points / different pad), not a pipeline change. Re-run stage B + C for that change.
- Disk: predark `frames/` for 6 segments × ~100 frames × ~3MB PNG ≈ a few GB in scratchpad; fine, and it's session-scoped.
