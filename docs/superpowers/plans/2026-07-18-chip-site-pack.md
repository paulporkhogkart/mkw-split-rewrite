# Chip Site Pack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the pristine matte'd chip frames (`D:\kartoff\asset_chips\matte`) into a versioned, auto-deployed WebP sprite-sheet pack (+ sil masks + manifest + JS stepper), gated by an A/B eye test.

**Architecture:** A pure-Python encode library (`tools/asset_matte/site_pack.py`, `sil_masks.py`) drives a multiprocessing CLI (`build_site_pack.py`) that writes `D:\kartoff\asset_chips\site_pack\chips\`. A packaging tool shards the pack into per-character tars + a committed `web/chips.lock`; the Pi's `update.sh` pulls the shards from a GitHub Release into `$DATA/chips/<tag>/` and `web/serve.mjs` serves them at `/chips/anim/`. A shared JS stepper (`src/lib/chipSheet.js`) provides frame-exact playback + handoffs for the site and later pbenguin.

**Tech Stack:** Python 3.12+ (Pillow 12, numpy, multiprocessing, tarfile, hashlib), vanilla JS (vitest), bash (curl + sha256sum), Node http (serve.mjs).

**Spec:** `docs/superpowers/specs/2026-07-18-chip-site-pack-design.md` (rev 2). This plan amends one spec detail: the pack is served at **`/chips/anim/<tag>/…`** (not `/chips/`) because `/chips/<category>/<slug>.png` is already claimed by the activity-feed chips (`web/src/lib/chips.js:chipUrl`), and versioned tag-dirs make `immutable` caching safe across `chips-vN` bumps. Task 10 writes this amendment into the spec.

## Global Constraints

- `D:\kartoff\asset_chips\matte\` and `D:\kartoff\asset_chips\manifest.json` are **READ-ONLY masters** — no tool may write inside `matte/` or modify the master manifest.
- The pack **never** enters git or Git LFS. Only `web/chips.lock`, code, and docs are committed.
- **The shared checkout is on another session's live branch (`wr-client-dots`). Do all work in a dedicated worktree branched from `main`** (superpowers:using-git-worktrees). Never commit to or reset `wr-client-dots`.
- Candidate recipe (pending Task 6 eye-test lock): scale 0.2 (205×216), fps 30, quality 60, method 4, alpha 5 bits (snap <6→0, >249→255). All are CLI flags — nothing hardcodes the candidate values.
- Frame ops: premultiply → `Image.LANCZOS` → unpremultiply. Sheet grids ≤4096px per side, row-major.
- Python tests run from repo root: `python -m pytest tests/test_site_pack.py -v` etc. JS tests: `npm run test:js` (root) / `npm --prefix web test` (web).
- The ×6,273 batch, GitHub Release upload, and `chips.lock` commit happen **only after Paul locks the recipe on the A/B page** (Task 6 gate) — they are the Task 11 runbook, run with Paul.

---

### Task 1: Frame ops — `site_pack.py` core

**Files:**
- Create: `tools/asset_matte/site_pack.py`
- Test: `tests/test_site_pack.py`

**Interfaces:**
- Produces: `premul_resize(im: PIL.Image, size: tuple[int,int]) -> PIL.Image` (RGBA in/out); `quant_alpha(im: PIL.Image, bits: int) -> PIL.Image`; `subsample_step(fps: int) -> int` (60→1, 30→2); `encode_size(src_w, src_h, scale) -> tuple[int,int]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_site_pack.py
"""Site-pack encode core: frame ops, grids, sheets, manifest. Pure PIL/numpy — no D: access."""
import numpy as np
import pytest
from PIL import Image

from tools.asset_matte import site_pack as sp


def _rgba(w, h, rgba):
    return Image.new("RGBA", (w, h), rgba)


class TestFrameOps:
    def test_encode_size_rounds(self):
        assert sp.encode_size(1024, 1080, 0.2) == (205, 216)
        assert sp.encode_size(1024, 1080, 0.15) == (154, 162)

    def test_subsample_step(self):
        assert sp.subsample_step(60) == 1
        assert sp.subsample_step(30) == 2
        with pytest.raises(ValueError):
            sp.subsample_step(45)  # must divide 60

    def test_premul_resize_keeps_size_and_mode(self):
        out = sp.premul_resize(_rgba(64, 68, (200, 40, 40, 255)), (32, 34))
        assert out.size == (32, 34) and out.mode == "RGBA"

    def test_premul_resize_no_fringe_from_hidden_rgb(self):
        # Bright green hidden under alpha=0 next to an opaque red block must not
        # tint the red edge after downscale (the reason we premultiply).
        im = _rgba(64, 64, (0, 255, 0, 0))
        for x in range(32):
            for y in range(64):
                im.putpixel((x, y), (255, 0, 0, 255))
        out = np.asarray(sp.premul_resize(im, (32, 32)))
        edge = out[16, 15]  # just inside the red half
        assert edge[0] > 150 and edge[1] < 60  # red stays red, no green bleed

    def test_quant_alpha_levels_and_snaps(self):
        grad = Image.new("RGBA", (256, 1))
        grad.putdata([(120, 120, 120, a) for a in range(256)])
        out = np.asarray(sp.quant_alpha(grad, 5))[0, :, 3]
        assert len(np.unique(out)) <= 32 + 2          # ≤2^5 levels (+snapped 0/255)
        assert all(out[a] == 0 for a in range(6))     # <6 -> 0
        assert all(out[a] == 255 for a in range(250, 256))  # >249 -> 255
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_site_pack.py -v`
Expected: FAIL — `ModuleNotFoundError` / `AttributeError` (site_pack missing).

- [ ] **Step 3: Implement**

```python
# tools/asset_matte/site_pack.py
"""Site-pack encode core: matte PNG frames -> WebP sprite sheets + manifest entries.

Pure functions over PIL images; no D:\\ paths in here (the CLI wires those).
Recipe knobs (scale/fps/quality/alpha bits) are always parameters — the A/B lab
locks their production values, nothing is hardcoded.
"""
from __future__ import annotations

import math

import numpy as np
from PIL import Image

MAX_SHEET_SIDE = 4096  # GPU-texture-safe cap per spec


def encode_size(src_w: int, src_h: int, scale: float) -> tuple[int, int]:
    return (round(src_w * scale), round(src_h * scale))


def subsample_step(fps: int) -> int:
    """Source is 60fps; we keep every step-th frame."""
    if fps <= 0 or 60 % fps:
        raise ValueError(f"fps must divide 60, got {fps}")
    return 60 // fps


def premul_resize(im: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Premultiply -> Lanczos -> unpremultiply, so RGB hidden under alpha=0 can't fringe."""
    a = np.asarray(im.convert("RGBA"), dtype=np.float32)
    alpha = a[..., 3:4] / 255.0
    a[..., :3] *= alpha
    pm = Image.fromarray(a.astype("uint8"), "RGBA").resize(size, Image.LANCZOS)
    b = np.asarray(pm, dtype=np.float32)
    al = b[..., 3:4]
    b[..., :3] = np.where(al > 0, b[..., :3] * 255.0 / np.maximum(al, 1e-6), 0).clip(0, 255)
    return Image.fromarray(b.astype("uint8"), "RGBA")


def quant_alpha(im: Image.Image, bits: int) -> Image.Image:
    """Quantize the alpha plane (lossless in WebP -> fewer levels = smaller), snapping
    near-transparent to 0 and near-opaque to 255."""
    if bits >= 8:
        return im
    a = np.asarray(im.convert("RGBA")).copy()
    al = a[..., 3].astype(np.int32)
    step = 255 // ((1 << bits) - 1)
    q = ((al + step // 2) // step * step).clip(0, 255)
    q[al < 6] = 0
    q[al > 249] = 255
    a[..., 3] = q.astype(np.uint8)
    return Image.fromarray(a, "RGBA")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_site_pack.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add tools/asset_matte/site_pack.py tests/test_site_pack.py
git commit -m "feat(site-pack): frame ops core — premul resize, alpha quant, fps step"
```

---

### Task 2: Sheet grid + WebP encode + manifest entry

**Files:**
- Modify: `tools/asset_matte/site_pack.py`
- Test: `tests/test_site_pack.py`

**Interfaces:**
- Consumes: Task 1 functions.
- Produces: `grid_for(n_frames, fw, fh, max_side=4096) -> tuple[int,int]` (cols, rows); `build_sheet(frames: list[Image], fw, fh) -> Image` (row-major grid); `encode_anim(frames: list[Image], out_path: str, quality: int) -> int` (bytes written); `manifest_anim_entry(n_frames, fw, fh) -> dict` (`{"frames": n, "cols": c, "rows": r}`); `encode_idle_resume(master_idx: int, step: int, n_encoded: int) -> int`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_site_pack.py`)

```python
class TestSheet:
    def test_grid_near_square(self):
        assert sp.grid_for(60, 205, 216) == (8, 8)
        assert sp.grid_for(13, 205, 216) == (4, 4)
        assert sp.grid_for(120, 205, 216) == (11, 11)

    def test_grid_respects_max_side(self):
        cols, rows = sp.grid_for(120, 500, 216, max_side=4096)
        assert cols * 500 <= 4096 and rows * 216 <= 4096 and cols * rows >= 120

    def test_grid_impossible_raises(self):
        with pytest.raises(ValueError):
            sp.grid_for(4000, 500, 500, max_side=4096)

    def test_build_sheet_places_frames_row_major(self):
        frames = [_rgba(10, 12, (i * 20, 0, 0, 255)) for i in range(5)]
        sheet = sp.build_sheet(frames, 10, 12)
        cols, rows = sp.grid_for(5, 10, 12)
        assert sheet.size == (cols * 10, rows * 12)
        px = np.asarray(sheet)
        assert tuple(px[0, 0][:3]) == (0, 0, 0)          # frame 0 at (0,0)
        assert px[0, 3 * 10][0] == 60                    # frame 3 in row 0 (cols=3)
        assert px[12, 0][0] == 80                        # frame 4 wraps to row 1
        assert px[12, 2 * 10][3] == 0                    # unused cell transparent

    def test_encode_anim_writes_webp(self, tmp_path):
        frames = [_rgba(10, 12, (200, 0, 0, 255)) for _ in range(4)]
        n = sp.encode_anim(frames, str(tmp_path / "x.webp"), quality=60)
        assert n > 0
        with Image.open(tmp_path / "x.webp") as im:
            assert im.format == "WEBP" and getattr(im, "n_frames", 1) == 1

    def test_manifest_anim_entry(self):
        assert sp.manifest_anim_entry(60, 205, 216) == {"frames": 60, "cols": 8, "rows": 8}

    def test_encode_idle_resume(self):
        assert sp.encode_idle_resume(103, step=2, n_encoded=60) == 51
        assert sp.encode_idle_resume(103, step=1, n_encoded=120) == 103
        assert sp.encode_idle_resume(119, step=2, n_encoded=60) == 59  # clamped in range
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_site_pack.py -v -k Sheet`
Expected: FAIL — missing attributes.

- [ ] **Step 3: Implement** (append to `tools/asset_matte/site_pack.py`)

```python
def grid_for(n_frames: int, fw: int, fh: int, max_side: int = MAX_SHEET_SIDE) -> tuple[int, int]:
    """Near-square row-major grid, both sides <= max_side."""
    cols = max(1, math.ceil(math.sqrt(n_frames)))
    while cols > 1 and cols * fw > max_side:
        cols -= 1
    if cols * fw > max_side:
        raise ValueError(f"frame width {fw} exceeds max sheet side {max_side}")
    rows = math.ceil(n_frames / cols)
    if rows * fh > max_side:
        raise ValueError(f"{n_frames}f of {fw}x{fh} cannot fit a {max_side}px sheet")
    return cols, rows


def build_sheet(frames: list[Image.Image], fw: int, fh: int) -> Image.Image:
    cols, rows = grid_for(len(frames), fw, fh)
    sheet = Image.new("RGBA", (cols * fw, rows * fh), (0, 0, 0, 0))
    for i, f in enumerate(frames):
        sheet.paste(f, ((i % cols) * fw, (i // cols) * fh))
    return sheet


def encode_anim(frames: list[Image.Image], out_path: str, quality: int) -> int:
    """Sheet the frames and write a static lossy WebP. Returns bytes written."""
    fw, fh = frames[0].size
    build_sheet(frames, fw, fh).save(out_path, format="WEBP", quality=quality, method=4)
    import os
    return os.path.getsize(out_path)


def manifest_anim_entry(n_frames: int, fw: int, fh: int) -> dict:
    cols, rows = grid_for(n_frames, fw, fh)
    return {"frames": n_frames, "cols": cols, "rows": rows}


def encode_idle_resume(master_idx: int, step: int, n_encoded: int) -> int:
    """Map a 60fps master idle index onto the subsampled sheet (kept frames are 0, step, 2*step...)."""
    return min(max(master_idx // step, 0), n_encoded - 1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_site_pack.py -v`
Expected: PASS (12 tests).

- [ ] **Step 5: Commit**

```bash
git add tools/asset_matte/site_pack.py tests/test_site_pack.py
git commit -m "feat(site-pack): sprite-sheet grid + webp encode + manifest entries"
```

---

### Task 3: Sil tearout masks — `sil_masks.py`

**Files:**
- Create: `tools/asset_matte/sil_masks.py`
- Test: `tests/test_sil_masks.py`

**Interfaces:**
- Consumes: nothing repo-side (pure PIL/numpy).
- Produces: `keyframe_indices(n_frames) -> list[int]` (4 spread indices); `sil_mask(frame: Image, seed_key: str, margin_range=(18, 34), points=12, ref_h=540) -> Image` (RGBA mask, white-opaque inside the jagged cut, transparent outside, same size as frame); `write_sil_masks(frames: list[Image], name: str, anim: str, out_dir: str) -> list[str]` (writes `<name>__<anim>__sil_k{0..3}.png`, returns paths).

The tearout language comes from the locked live-card design: 12-point radial jagged cut around the silhouette, margins 18–34 px in 540px-reference space, **jag seeds shared across the 4 keyframes** (same margins/angle jitter; only the pose changes the radii). The original placeholder generator is lost; Task 6's lab page eyeballs these against `docs/design/site-redesign/sil/` on the `site-redesign-p1` branch (placeholders are 494×540 RGBA where opaque = keep).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_sil_masks.py
"""Sil tearout masks: deterministic seeded jagged cut around the frame silhouette."""
import numpy as np
from PIL import Image

from tools.asset_matte import sil_masks as sm


def _blob(w=100, h=108, cx=50, cy=60, r=25):
    """Opaque disc on transparent — a stand-in character silhouette."""
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    y, x = np.ogrid[:h, :w]
    mask = (x - cx) ** 2 + (y - cy) ** 2 <= r * r
    a = np.zeros((h, w, 4), np.uint8)
    a[mask] = (200, 50, 50, 255)
    a[..., :3][mask] = 200
    out = Image.fromarray(a, "RGBA")
    return out


def test_keyframe_indices_spread():
    assert sm.keyframe_indices(60) == [0, 20, 39, 59]
    assert sm.keyframe_indices(4) == [0, 1, 2, 3]


def test_mask_same_size_and_covers_silhouette():
    f = _blob()
    m = np.asarray(sm.sil_mask(f, "combo__idle"))
    assert m.shape[:2] == (108, 100)
    fa = np.asarray(f)[..., 3] > 0
    covered = (m[..., 3] > 0) | ~fa
    assert covered.mean() > 0.995  # cut (incl. margin) contains ~the whole silhouette


def test_mask_is_jagged_not_full_frame():
    m = np.asarray(sm.sil_mask(_blob(), "combo__idle"))[..., 3] > 0
    assert 0.15 < m.mean() < 0.95  # a cut, not everything / nothing


def test_same_seed_same_jags_different_pose_differs():
    a = np.asarray(sm.sil_mask(_blob(cx=50), "combo__idle"))
    b = np.asarray(sm.sil_mask(_blob(cx=50), "combo__idle"))
    c = np.asarray(sm.sil_mask(_blob(cx=58), "combo__idle"))
    d = np.asarray(sm.sil_mask(_blob(cx=50), "other__idle"))
    assert (a == b).all()            # deterministic
    assert not (a == c).all()        # pose moves the cut
    assert not (a == d).all()        # different seed key -> different jags


def test_write_sil_masks_names(tmp_path):
    frames = [_blob(cx=48 + i) for i in range(8)]
    paths = sm.write_sil_masks(frames, "a__base", "idle", str(tmp_path))
    assert [p.split("\\")[-1].split("/")[-1] for p in paths] == [
        f"a__base__idle__sil_k{i}.png" for i in range(4)
    ]
    with Image.open(paths[0]) as im:
        assert im.size == frames[0].size
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_sil_masks.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

```python
# tools/asset_matte/sil_masks.py
"""Tearout silhouette masks (sil_k0..3) for the live-card two-ply scrapbook cut.

Locked tearout language (live-card.html header): 12-point radial jagged cut with
margins 18-34px in the 540px-tall reference space, jag seeds SHARED across the four
keyframes of an animation — pose is the only variance. White-opaque = keep.
"""
from __future__ import annotations

import math
import os
import random

import numpy as np
from PIL import Image, ImageDraw


def keyframe_indices(n_frames: int) -> list[int]:
    return sorted(set(round(i * (n_frames - 1) / 3) for i in range(4)))


def _jags(seed_key: str, points: int, margin_range: tuple[int, int]):
    rng = random.Random(f"sil:{seed_key}")
    margins = [rng.uniform(*margin_range) for _ in range(points)]
    jitters = [rng.uniform(-0.5, 0.5) * (2 * math.pi / points) * 0.6 for _ in range(points)]
    return margins, jitters


def sil_mask(frame: Image.Image, seed_key: str, margin_range=(18, 34),
             points: int = 12, ref_h: int = 540) -> Image.Image:
    """Jagged 12-gon around the frame's alpha silhouette. seed_key fixes the jags
    (share one key across an animation's keyframes)."""
    w, h = frame.size
    scale = h / ref_h  # margins are specified in 540px-reference pixels
    al = np.asarray(frame.convert("RGBA"))[..., 3]
    ys, xs = np.nonzero(al > 32)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if len(xs) == 0:
        return out
    cx, cy = xs.mean(), ys.mean()
    ang = np.arctan2(ys - cy, xs - cx)
    dist = np.hypot(xs - cx, ys - cy)
    margins, jitters = _jags(seed_key, points, margin_range)
    verts = []
    sector = 2 * math.pi / points
    for j in range(points):
        theta = -math.pi + (j + 0.5) * sector + jitters[j]
        # silhouette extent in this sector (max radius of any silhouette pixel)
        lo, hi = -math.pi + j * sector, -math.pi + (j + 1) * sector
        in_sector = (ang >= lo) & (ang < hi)
        r = dist[in_sector].max() if in_sector.any() else dist.max() * 0.4
        r += margins[j] * scale
        verts.append((cx + r * math.cos(theta), cy + r * math.sin(theta)))
    ImageDraw.Draw(out).polygon(verts, fill=(255, 255, 255, 255))
    return out


def write_sil_masks(frames: list[Image.Image], name: str, anim: str, out_dir: str) -> list[str]:
    key = f"{name}__{anim}"
    paths = []
    for k, idx in enumerate(keyframe_indices(len(frames))):
        p = os.path.join(out_dir, f"{name}__{anim}__sil_k{k}.png")
        sil_mask(frames[idx], key).save(p, optimize=True)
        paths.append(p)
    return paths
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_sil_masks.py -v`
Expected: PASS (5 tests). Note `keyframe_indices(60)` → `[0, 20, 39, 59]` (round(1*59/3)=20, round(2*59/3)=39); if the implementation rounds differently, fix the TEST expectation to match `round` semantics — the contract is "4 spread indices, first=0, last=n-1, deterministic".

- [ ] **Step 5: Commit**

```bash
git add tools/asset_matte/sil_masks.py tests/test_sil_masks.py
git commit -m "feat(site-pack): seeded 12-point tearout sil masks"
```

---

### Task 4: Batch CLI — `build_site_pack.py`

**Files:**
- Create: `tools/asset_matte/build_site_pack.py`
- Test: `tests/test_build_site_pack.py`

**Interfaces:**
- Consumes: Tasks 1–3 (`site_pack.*`, `sil_masks.write_sil_masks`). Master layout: `<src>/matte/<name>__<anim>_frames/NNN.png` (60fps, RGBA) + `<src>/manifest.json` (`{name: {"status": "done", "kart": bool, "idle_resume": int, "segments": {anim: n_frames}}}`).
- Produces: `<out>/chips/<name>__<anim>.webp` + `__sil_k{0..3}.png` + `<out>/chips/manifest.json` + `<out>/book.json` (resume bookkeeping). Site manifest shape (Tasks 5/7/8 and the site rely on this exactly):

```json
{"version": 1, "scale": 0.2, "fps": 30, "fw": 205, "fh": 216,
 "combos": {"a__base__k1": {"kart": true, "idle_resume": 51,
   "anims": {"spawn": {"frames": 13, "cols": 4, "rows": 4},
             "idle": {"frames": 60, "cols": 8, "rows": 8},
             "flourish": {"frames": 31, "cols": 6, "rows": 6}}}}}
```

Functions: `plan_combos(src) -> dict[name, list[anim]]` (done entries whose frame dirs exist; missing dir → warn + skip anim); `process_combo(src, out_chips, name, anims, scale, fps, quality, alpha_bits) -> dict` (worker: returns the combo's manifest entry + bytes); `main(argv)` CLI.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_build_site_pack.py
"""Batch CLI over a synthetic mini master tree (no D:\\ dependency)."""
import json
import os

import numpy as np
from PIL import Image

from tools.asset_matte import build_site_pack as bsp


def _write_frames(d, n, w=64, h=68):
    os.makedirs(d, exist_ok=True)
    for i in range(n):
        a = np.zeros((h, w, 4), np.uint8)
        a[10:40, 10 + i % 8:40 + i % 8] = (180, 60, 60, 255)
        Image.fromarray(a, "RGBA").save(os.path.join(d, f"{i:03d}.png"))


def _mini_masters(root):
    m = {
        "a__base__k1": {"status": "done", "kart": True, "idle_resume": 9,
                        "secs": 1.0, "segments": {"spawn": 6, "idle": 12, "flourish": 8}},
        "b__base": {"status": "done", "kart": False, "idle_resume": 0,
                    "secs": 1.0, "segments": {"idle": 10, "flourish": 6}},
        "c__base": {"status": "error", "kart": False, "idle_resume": 0,
                    "secs": 1.0, "segments": {"idle": 10}},
    }
    matte = os.path.join(root, "matte")
    for name, e in m.items():
        if e["status"] != "done":
            continue
        for anim, n in e["segments"].items():
            _write_frames(os.path.join(matte, f"{name}__{anim}_frames"), n)
    with open(os.path.join(root, "manifest.json"), "w") as f:
        json.dump(m, f)
    return root


def test_plan_combos_skips_non_done_and_missing_dirs(tmp_path):
    src = _mini_masters(str(tmp_path))
    import shutil
    shutil.rmtree(os.path.join(src, "matte", "b__base__flourish_frames"))
    plan = bsp.plan_combos(src)
    assert set(plan) == {"a__base__k1", "b__base"}
    assert plan["a__base__k1"] == ["spawn", "idle", "flourish"]
    assert plan["b__base"] == ["idle"]  # missing flourish dir skipped with a warning


def test_full_build_outputs_and_manifest(tmp_path):
    src = _mini_masters(str(tmp_path / "masters"))
    out = str(tmp_path / "pack")
    rc = bsp.main(["--src", src, "--out", out, "--scale", "0.5", "--fps", "30",
                   "--quality", "60", "--alpha-bits", "5", "--workers", "1"])
    assert rc == 0
    chips = os.path.join(out, "chips")
    for f in ["a__base__k1__idle.webp", "a__base__k1__spawn.webp",
              "a__base__k1__flourish.webp", "a__base__k1__idle__sil_k0.png",
              "b__base__idle.webp", "b__base__flourish__sil_k3.png"]:
        assert os.path.exists(os.path.join(chips, f)), f
    man = json.load(open(os.path.join(chips, "manifest.json")))
    assert man["fps"] == 30 and man["fw"] == 32 and man["fh"] == 34
    a = man["combos"]["a__base__k1"]
    assert a["kart"] is True
    assert a["anims"]["idle"]["frames"] == 6          # 12 masters @30fps
    assert a["idle_resume"] == 4                      # 9 // 2
    assert man["combos"]["b__base"]["kart"] is False


def test_resume_skips_done_combos(tmp_path, capsys):
    src = _mini_masters(str(tmp_path / "masters"))
    out = str(tmp_path / "pack")
    args = ["--src", src, "--out", out, "--scale", "0.5", "--fps", "30",
            "--quality", "60", "--alpha-bits", "5", "--workers", "1"]
    assert bsp.main(args) == 0
    stamp = os.path.getmtime(os.path.join(out, "chips", "a__base__k1__idle.webp"))
    assert bsp.main(args) == 0                        # second run: all skipped
    assert os.path.getmtime(os.path.join(out, "chips", "a__base__k1__idle.webp")) == stamp
    book = json.load(open(os.path.join(out, "book.json")))
    assert book["a__base__k1"]["done"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_build_site_pack.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

```python
# tools/asset_matte/build_site_pack.py
"""Build the site chip pack: matte masters -> sprite-sheet webps + sil masks + manifest.

Usage (production values pending the A/B recipe lock):
  python tools/asset_matte/build_site_pack.py --src D:\\kartoff\\asset_chips \\
      --out D:\\kartoff\\asset_chips\\site_pack --scale 0.2 --fps 30 \\
      --quality 60 --alpha-bits 5 --workers 12

Masters are READ-ONLY. Resume: combos recorded done in <out>/book.json are skipped;
delete book.json (or --force) to re-encode. Safe to interrupt (book written per combo).
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
import time

from PIL import Image

# Runnable both as a module and as a script (repo-root imports).
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from tools.asset_matte import site_pack as sp                     # noqa: E402
from tools.asset_matte.sil_masks import write_sil_masks           # noqa: E402

ANIM_ORDER = ["spawn", "idle", "flourish"]


def plan_combos(src: str) -> dict[str, list[str]]:
    with open(os.path.join(src, "manifest.json"), encoding="utf-8") as f:
        masters = json.load(f)
    plan: dict[str, list[str]] = {}
    for name, e in sorted(masters.items()):
        if e.get("status") != "done":
            continue
        anims = []
        for anim in ANIM_ORDER:
            if anim not in e.get("segments", {}):
                continue
            d = os.path.join(src, "matte", f"{name}__{anim}_frames")
            if not os.path.isdir(d):
                print(f"warn: {name} missing {anim} frames dir, skipping that anim", file=sys.stderr)
                continue
            anims.append(anim)
        if anims:
            plan[name] = anims
    return plan


def _load_frames(d: str, step: int) -> list[Image.Image]:
    files = sorted(f for f in os.listdir(d) if f.endswith(".png"))
    return [Image.open(os.path.join(d, f)).convert("RGBA") for f in files[::step]]


def process_combo(src: str, out_chips: str, name: str, anims: list[str],
                  scale: float, fps: int, quality: int, alpha_bits: int) -> dict:
    """Worker: encode one combo's sheets + sil masks; return its manifest entry."""
    with open(os.path.join(src, "manifest.json"), encoding="utf-8") as f:
        master = json.load(f)[name]
    step = sp.subsample_step(fps)
    entry: dict = {"kart": bool(master.get("kart")), "anims": {}}
    total = 0
    fw = fh = None
    for anim in anims:
        frames = _load_frames(os.path.join(src, "matte", f"{name}__{anim}_frames"), step)
        if fw is None:
            fw, fh = sp.encode_size(*frames[0].size, scale)
        frames = [sp.quant_alpha(sp.premul_resize(f, (fw, fh)), alpha_bits) for f in frames]
        total += sp.encode_anim(frames, os.path.join(out_chips, f"{name}__{anim}.webp"), quality)
        write_sil_masks(frames, name, anim, out_chips)
        entry["anims"][anim] = sp.manifest_anim_entry(len(frames), fw, fh)
    if "idle" in entry["anims"]:
        entry["idle_resume"] = sp.encode_idle_resume(
            int(master.get("idle_resume", 0)), step, entry["anims"]["idle"]["frames"])
    return {"name": name, "entry": entry, "bytes": total, "fw": fw, "fh": fh}


def _worker(job):
    try:
        return process_combo(*job)
    except Exception as e:  # keep the batch alive; the combo stays pending in the book
        return {"name": job[2], "error": f"{type(e).__name__}: {e}"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", required=True, help="asset_chips root (matte/ + manifest.json)")
    ap.add_argument("--out", required=True, help="site_pack output root")
    ap.add_argument("--scale", type=float, required=True)
    ap.add_argument("--fps", type=int, required=True)
    ap.add_argument("--quality", type=int, required=True)
    ap.add_argument("--alpha-bits", type=int, required=True)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 2))
    ap.add_argument("--force", action="store_true", help="ignore book.json, re-encode everything")
    ap.add_argument("--only", nargs="*", help="limit to these combo names (A/B sampling)")
    args = ap.parse_args(argv)

    out_chips = os.path.join(args.out, "chips")
    os.makedirs(out_chips, exist_ok=True)
    book_path = os.path.join(args.out, "book.json")
    book = {}
    if os.path.exists(book_path) and not args.force:
        with open(book_path, encoding="utf-8") as f:
            book = json.load(f)

    plan = plan_combos(args.src)
    if args.only:
        plan = {k: v for k, v in plan.items() if k in set(args.only)}
    pending = {k: v for k, v in plan.items() if not book.get(k, {}).get("done")}
    print(f"{len(plan)} combos planned, {len(pending)} pending, workers={args.workers}")

    jobs = [(args.src, out_chips, name, anims, args.scale, args.fps,
             args.quality, args.alpha_bits) for name, anims in pending.items()]
    combos_manifest = {k: book[k]["entry"] for k in plan if book.get(k, {}).get("done")}
    fw = fh = None
    t0, done, failed = time.time(), 0, 0

    def _record(res):
        nonlocal fw, fh, done, failed
        if "error" in res:
            failed += 1
            print(f"FAIL {res['name']}: {res['error']}", file=sys.stderr)
            return
        done += 1
        fw, fh = fw or res["fw"], fh or res["fh"]
        combos_manifest[res["name"]] = res["entry"]
        book[res["name"]] = {"done": True, "entry": res["entry"], "bytes": res["bytes"]}
        tmp = book_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(book, f)
        os.replace(tmp, book_path)
        if done % 25 == 0:
            rate = done / (time.time() - t0)
            print(f"{done}/{len(jobs)} ({rate:.1f}/s, eta {int((len(jobs)-done)/max(rate,1e-6)/60)}m)")

    if args.workers <= 1:
        for j in jobs:
            _record(_worker(j))
    else:
        with mp.Pool(args.workers) as pool:
            for res in pool.imap_unordered(_worker, jobs):
                _record(res)

    if fw is None and combos_manifest:  # resume run with nothing new: recover fw/fh from a sheet
        any_name = next(iter(combos_manifest))
        anim = next(iter(combos_manifest[any_name]["anims"]))
        e = combos_manifest[any_name]["anims"][anim]
        with Image.open(os.path.join(out_chips, f"{any_name}__{anim}.webp")) as im:
            fw, fh = im.size[0] // e["cols"], im.size[1] // e["rows"]

    manifest = {"version": 1, "scale": args.scale, "fps": args.fps,
                "fw": fw, "fh": fh, "combos": combos_manifest}
    with open(os.path.join(out_chips, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, separators=(",", ":"))
    print(f"done: {done} encoded, {failed} failed, {len(combos_manifest)} in manifest")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_build_site_pack.py tests/test_site_pack.py tests/test_sil_masks.py -v`
Expected: PASS. (Note the idle_resume expectation: masters idle 12f, step 2 → 6 encoded frames; master idle_resume 9 → 9//2 = 4.)

- [ ] **Step 5: Smoke on ONE real combo (read-only against masters)**

Run:
```bash
python tools/asset_matte/build_site_pack.py --src "D:/kartoff/asset_chips" \
  --out "D:/kartoff/asset_chips/site_pack_smoke" --scale 0.2 --fps 30 \
  --quality 60 --alpha-bits 5 --workers 1 --only baby_daisy__base__b_dasher
```
Expected: exit 0; `site_pack_smoke/chips/` holds 3 webps (~190/45/105KB) + 12 sil PNGs + manifest.json with `"idle_resume": 51`. Verify NOTHING was written under `D:/kartoff/asset_chips/matte` (`ls -lt` the matte dir — no new mtimes).

- [ ] **Step 6: Commit**

```bash
git add tools/asset_matte/build_site_pack.py tests/test_build_site_pack.py
git commit -m "feat(site-pack): batch CLI — multiprocessing encode with book.json resume"
```

---

### Task 5: JS stepper — `src/lib/chipSheet.js`

**Files:**
- Create: `src/lib/chipSheet.js`
- Test: `src/lib/chipSheet.test.js`

**Interfaces:**
- Consumes: the site manifest shape from Task 4 (`{fps, fw, fh, combos:{name:{kart, idle_resume, anims:{...{frames,cols,rows}}}}}`).
- Produces (site + pbenguin + Task 6 lab all use exactly these):
  - `bgPos(index, cols, fw, fh) -> "-Xpx -Ypx"`
  - `sheetCss(entry, anim, fw, fh) -> {width, height, backgroundSize}` helper for the element
  - `createChipPlayer({entry, fps, now}) -> player` with `player.select()` (spawn-or-idle restart, interruptible), `player.confirm()` (flourish once → idle at `idle_resume` for karts / 0 for chars), `player.idle()`, `player.tick(nowMs) -> {anim, frame, bg}` (null-safe: returns last state when nothing changed).

Pure logic, zero DOM — the caller sets `background-image`/`background-position`. One shared rAF loop can tick many players.

- [ ] **Step 1: Write the failing tests**

```javascript
// src/lib/chipSheet.test.js
import { describe, it, expect } from "vitest";
import { bgPos, sheetCss, createChipPlayer } from "./chipSheet.js";

const ENTRY = {
  kart: true, idle_resume: 51,
  anims: {
    spawn: { frames: 13, cols: 4, rows: 4 },
    idle: { frames: 60, cols: 8, rows: 8 },
    flourish: { frames: 31, cols: 6, rows: 6 },
  },
};
const CHAR = { kart: false, idle_resume: 0, anims: { idle: { frames: 40, cols: 7, rows: 6 }, flourish: { frames: 27, cols: 6, rows: 5 } } };
const FPS = 30;

const player = (entry = ENTRY) => {
  let t = 0;
  const p = createChipPlayer({ entry, fps: FPS, now: () => t });
  return { p, at: (ms) => { t = ms; return p.tick(t); } };
};

describe("bgPos / sheetCss", () => {
  it("maps frame index to row-major grid offsets", () => {
    expect(bgPos(0, 8, 205, 216)).toBe("0px 0px");
    expect(bgPos(3, 8, 205, 216)).toBe("-615px 0px");
    expect(bgPos(8, 8, 205, 216)).toBe("0px -216px");
  });
  it("sizes the element and background to the grid", () => {
    expect(sheetCss(ENTRY, "idle", 205, 216)).toEqual({
      width: "205px", height: "216px", backgroundSize: "1640px 1728px",
    });
  });
});

describe("createChipPlayer", () => {
  it("starts looping idle and wraps", () => {
    const { p, at } = player();
    expect(at(0)).toMatchObject({ anim: "idle", frame: 0 });
    expect(at(1000 / FPS * 59)).toMatchObject({ frame: 59 });
    expect(at(1000 / FPS * 60)).toMatchObject({ frame: 0 }); // wrap
  });

  it("select() plays spawn once then hands to idle frame 0", () => {
    const { p, at } = player();
    p.select();
    expect(at(0)).toMatchObject({ anim: "spawn", frame: 0 });
    at(1000 / FPS * 12);                                   // last spawn frame
    expect(at(1000 / FPS * 13)).toMatchObject({ anim: "idle", frame: 0 });
  });

  it("select() is interruptible - re-select restarts spawn", () => {
    const { p, at } = player();
    p.select(); at(1000 / FPS * 6);
    p.select();
    expect(at(1000 / FPS * 6)).toMatchObject({ anim: "spawn", frame: 0 });
  });

  it("confirm() plays flourish once then enters idle at idle_resume (kart)", () => {
    const { p, at } = player();
    p.confirm();
    expect(at(0)).toMatchObject({ anim: "flourish", frame: 0 });
    expect(at(1000 / FPS * 31)).toMatchObject({ anim: "idle", frame: 51 });
  });

  it("confirm() on a char hard-cuts to idle frame 0", () => {
    const { p, at } = player(CHAR);
    p.confirm();
    expect(at(1000 / FPS * 27)).toMatchObject({ anim: "idle", frame: 0 });
  });

  it("select() on a char (no spawn) restarts idle", () => {
    const { p, at } = player(CHAR);
    at(1000 / FPS * 10);
    p.select();
    expect(at(1000 / FPS * 10)).toMatchObject({ anim: "idle", frame: 0 });
  });

  it("bg matches the current frame's grid cell", () => {
    const { p, at } = player();
    const s = at(1000 / FPS * 9); // idle frame 9, cols 8 -> col 1 row 1
    expect(s.bg).toBe(bgPos(9, 8, s.fw ?? 205, s.fh ?? 216));
  });
});
```

Note: the last test needs frame size — give `createChipPlayer` the pack-level `fw`/`fh`: change the factory call in the test helper to `createChipPlayer({ entry, fps: FPS, fw: 205, fh: 216, now: () => t })` and assert `s.bg === bgPos(9, 8, 205, 216)`. Use that signature everywhere (lab + site).

- [ ] **Step 2: Run tests to verify they fail**

Run: `npx vitest run src/lib/chipSheet.test.js`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

```javascript
// src/lib/chipSheet.js
// Frame-exact playback over chip sprite sheets (site pack manifest shape).
// Pure logic: callers own the DOM/rAF; tick(now) returns {anim, frame, bg}.
// Handoffs per the chip-site-pack spec: spawn once -> idle@0; flourish once ->
// idle@idle_resume for karts, idle@0 (hard cut) for chars; select() restarts
// spawn (interruptible) or idle when the combo has no spawn.

export function bgPos(index, cols, fw, fh) {
  const c = index % cols, r = Math.floor(index / cols);
  return `${c === 0 ? "0px" : `-${c * fw}px`} ${r === 0 ? "0px" : `-${r * fh}px`}`;
}

export function sheetCss(entry, anim, fw, fh) {
  const a = entry.anims[anim];
  return {
    width: `${fw}px`, height: `${fh}px`,
    backgroundSize: `${a.cols * fw}px ${a.rows * fh}px`,
  };
}

export function createChipPlayer({ entry, fps, fw, fh, now = () => performance.now() }) {
  let anim = "idle", start = 0, startFrame = 0, once = false, next = null;

  function set(name, opts = {}) {
    if (!entry.anims[name]) name = "idle";
    anim = name;
    start = now();
    startFrame = opts.startFrame ?? 0;
    once = !!opts.once;
    next = opts.next ?? null;
  }

  set("idle");
  return {
    select() { entry.anims.spawn ? set("spawn", { once: true, next: { anim: "idle", startFrame: 0 } })
                                 : set("idle"); },
    confirm() {
      const resume = entry.kart ? (entry.idle_resume ?? 0) : 0;
      set("flourish", { once: true, next: { anim: "idle", startFrame: resume } });
    },
    idle() { set("idle"); },
    tick(t = now()) {
      const a = entry.anims[anim];
      let frame = startFrame + Math.floor((t - start) * fps / 1000);
      if (once && frame >= a.frames) {
        const n = next; // play-once finished: hand off
        set(n.anim, { startFrame: n.startFrame });
        // re-enter as the new anim at its start frame, anchored at t
        start = t; frame = startFrame;
      } else if (!once) {
        frame %= a.frames;
      } else {
        frame = Math.min(frame, a.frames - 1);
      }
      const cur = entry.anims[anim];
      return { anim, frame, bg: bgPos(frame, cur.cols, fw, fh) };
    },
  };
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run src/lib/chipSheet.test.js` then the full `npm run test:js`
Expected: PASS (new tests + no regressions).

- [ ] **Step 5: Commit**

```bash
git add src/lib/chipSheet.js src/lib/chipSheet.test.js
git commit -m "feat(site-pack): shared chip sprite-sheet player (frame-exact handoffs)"
```

---

### Task 6: A/B eye-test lab page — THE RECIPE GATE

**Files:**
- Create: `tools/asset_matte/build_ab_lab.py`
- Test: `tests/test_ab_lab.py`

**Interfaces:**
- Consumes: `build_site_pack.main` (via `--only` sampling), `src/lib/chipSheet.js` source (inlined into the page — `file://` pages can't load ES modules).
- Produces: `<out>/index.html` + one pack subdir per recipe variant (`packs/<variant>/chips/...`). Not committed — generated into `D:\kartoff\asset_chips\ab_lab\`.

Variant set (change-one-thing around the candidate, per Paul's rule):

| variant id | scale | fps | q | alpha |
|---|---|---|---|---|
| `candidate` | 0.2 | 30 | 60 | 5 |
| `fps60` | 0.2 | 60 | 60 | 5 |
| `q75` | 0.2 | 30 | 75 | 5 |
| `q50` | 0.2 | 30 | 50 | 5 |
| `alpha8` | 0.2 | 30 | 60 | 8 |
| `alpha4` | 0.2 | 30 | 60 | 4 |
| `scale015` | 0.15 | 30 | 60 | 5 |

Plus one `animref` cell: an animated-webp encode of the candidate recipe (Pillow `save_all=True, duration=1000/fps, loop=0`) as the stepper-smoothness reference.

Default sample combos (`--combos` overrides; warn + skip if a name has no matte dirs): `baby_daisy__base__b_dasher` (small), `bowser__base__bowser_bruiser` (big/busy), `mario__base` (standalone char), `king_boo__base` (long char flourish).

Page layout: dark card-ground background (`#0b0c0e`), one row per combo × variant grid; each cell renders the chip at 112px CSS height inside the live-card ink-ring filter (`filter: drop-shadow(1px 0 0 #101114) drop-shadow(-1px 0 0 #101114) drop-shadow(0 1px 0 #101114) drop-shadow(0 -1px 0 #101114)`), file sizes labelled, buttons `SELECT` (spawn) / `CONFIRM` (flourish → idle_resume handoff) per cell driving the real inlined stepper. A second strip renders `candidate` at 92px and 76px (card + wall sizes). A third strip overlays each animation's `sil_k0..3` masks cycling at 7fps next to the placeholder sils for comparison (copy the 4 placeholder PNGs for `paul__idle` from the site-redesign-p1 branch via `git show site-redesign-p1:docs/design/site-redesign/sil/paul__idle_k0.png` etc. into the lab dir at generation; if the branch is gone, skip with a warning).

- [ ] **Step 1: Write the failing test** (the generator's pure parts — variant expansion + HTML wiring; browser look is Paul's job)

```python
# tests/test_ab_lab.py
import json
import os

from tools.asset_matte import build_ab_lab as ab


def test_variants_change_one_thing():
    v = {x["id"]: x for x in ab.VARIANTS}
    cand = v["candidate"]
    assert cand == {"id": "candidate", "scale": 0.2, "fps": 30, "quality": 60, "alpha_bits": 5}
    for vid, x in v.items():
        if vid == "candidate":
            continue
        diffs = [k for k in ("scale", "fps", "quality", "alpha_bits") if x[k] != cand[k]]
        assert len(diffs) == 1, f"{vid} changes {diffs}"


def test_html_embeds_stepper_and_variants(tmp_path):
    html = ab.render_html(
        combos=["a__base__k1"], variants=ab.VARIANTS,
        manifests={"candidate": {"fps": 30, "fw": 205, "fh": 216, "combos": {"a__base__k1": {
            "kart": True, "idle_resume": 51,
            "anims": {"idle": {"frames": 60, "cols": 8, "rows": 8}}}}}},
        sizes={("candidate", "a__base__k1", "idle"): 190000},
        stepper_js="/* STEPPER */ export function bgPos(){}",
    )
    assert "STEPPER" in html and "export " not in html   # inlined, exports stripped
    assert "candidate" in html and "a__base__k1__idle.webp" in html
    assert "drop-shadow(1px 0 0 #101114)" in html        # ink ring on
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ab_lab.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `build_ab_lab.py`**

```python
# tools/asset_matte/build_ab_lab.py
"""A/B eye-test lab: encode sample combos across recipe variants, emit a static page.

  python tools/asset_matte/build_ab_lab.py --src D:\\kartoff\\asset_chips \\
      --out D:\\kartoff\\asset_chips\\ab_lab
Open <out>/index.html in a real browser (file://). Paul's pick locks the recipe.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from tools.asset_matte import build_site_pack as bsp              # noqa: E402
from tools.asset_matte import site_pack as sp                     # noqa: E402

VARIANTS = [
    {"id": "candidate", "scale": 0.2, "fps": 30, "quality": 60, "alpha_bits": 5},
    {"id": "fps60",     "scale": 0.2, "fps": 60, "quality": 60, "alpha_bits": 5},
    {"id": "q75",       "scale": 0.2, "fps": 30, "quality": 75, "alpha_bits": 5},
    {"id": "q50",       "scale": 0.2, "fps": 30, "quality": 50, "alpha_bits": 5},
    {"id": "alpha8",    "scale": 0.2, "fps": 30, "quality": 60, "alpha_bits": 8},
    {"id": "alpha4",    "scale": 0.2, "fps": 30, "quality": 60, "alpha_bits": 4},
    {"id": "scale015",  "scale": 0.15, "fps": 30, "quality": 60, "alpha_bits": 5},
]
DEFAULT_COMBOS = ["baby_daisy__base__b_dasher", "bowser__base__bowser_bruiser",
                  "mario__base", "king_boo__base"]
INK_RING = ("filter:drop-shadow(1px 0 0 #101114) drop-shadow(-1px 0 0 #101114) "
            "drop-shadow(0 1px 0 #101114) drop-shadow(0 -1px 0 #101114)")


def _stepper_source() -> str:
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src", "lib", "chipSheet.js")
    return re.sub(r"^export ", "", open(p, encoding="utf-8").read(), flags=re.M)


def render_html(combos, variants, manifests, sizes, stepper_js) -> str:
    data = json.dumps({"combos": combos,
                       "variants": [v["id"] for v in variants],
                       "manifests": manifests,
                       "sizes": {"|".join(k): v for k, v in sizes.items()}})
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>chip pack A/B lab</title>
<style>
 body{{background:#0b0c0e;color:#f3f4f6;font:13px system-ui;padding:20px}}
 h2{{font-size:13px;margin:26px 0 8px;color:#9a9ca1}}
 .grid{{display:flex;gap:14px;flex-wrap:wrap}}
 .cell{{text-align:center}}
 .chip{{background-repeat:no-repeat;{INK_RING};margin:0 auto}}
 .lbl{{color:#6b6d73;font-size:10px;margin-top:4px}}
 button{{font-size:10px;margin:2px}}
 .silwrap{{position:relative;width:120px;height:126px;background:#191a1d}}
 .silwrap img{{position:absolute;inset:0;width:100%;height:100%}}
</style></head><body>
<div id="root"></div>
<script>
{stepper_js}
const DATA = {data};
// One shared rAF ticks every player; cells register {{el, player, fw, fh, scaleCss}}.
const cells = [];
function addCell(root, variant, combo, cssH) {{
  const man = DATA.manifests[variant]; if (!man || !man.combos[combo]) return;
  const entry = man.combos[combo];
  const wrap = document.createElement("div"); wrap.className = "cell";
  const el = document.createElement("div"); el.className = "chip";
  const s = cssH / man.fh;
  el.style.width = man.fw * s + "px"; el.style.height = man.fh * s + "px";
  const player = createChipPlayer({{entry, fps: man.fps, fw: man.fw, fh: man.fh}});
  cells.push({{el, player, man, entry, s, variant, combo}});
  const kb = (n, f) => {{ const b = document.createElement("button"); b.textContent = n; b.onclick = f; return b; }};
  wrap.append(el, kb("SELECT", () => player.select()), kb("CONFIRM", () => player.confirm()));
  const idleKB = (DATA.sizes[[variant, combo, "idle"].join("|")] / 1024) | 0;
  const lbl = document.createElement("div"); lbl.className = "lbl";
  lbl.textContent = `${{variant}} · idle ${{idleKB}}KB`;
  wrap.append(lbl); root.append(wrap);
}}
function tickAll(t) {{
  for (const c of cells) {{
    const st = c.player.tick(t);
    const a = c.entry.anims[st.anim];
    c.el.style.backgroundImage = `url(packs/${{c.variant}}/chips/${{c.combo}}__${{st.anim}}.webp)`;
    c.el.style.backgroundSize = `${{a.cols * c.man.fw * c.s}}px ${{a.rows * c.man.fh * c.s}}px`;
    const [x, y] = st.bg.split(" ");
    c.el.style.backgroundPosition = `${{parseFloat(x) * c.s}}px ${{parseFloat(y) * c.s}}px`;
  }}
  requestAnimationFrame(tickAll);
}}
const root = document.getElementById("root");
for (const combo of DATA.combos) {{
  const h = document.createElement("h2"); h.textContent = combo; root.append(h);
  const g = document.createElement("div"); g.className = "grid"; root.append(g);
  for (const v of DATA.variants) addCell(g, v, combo, 112);
  const ref = document.createElement("div"); ref.className = "cell";
  ref.innerHTML = `<img style="height:112px;{INK_RING}" src="animref/${{combo}}__idle.webp"><div class="lbl">animref (animated webp)</div>`;
  g.append(ref);
  const h2 = document.createElement("h2"); h2.textContent = combo + " · candidate at card sizes"; root.append(h2);
  const g2 = document.createElement("div"); g2.className = "grid"; root.append(g2);
  addCell(g2, "candidate", combo, 92); addCell(g2, "candidate", combo, 76);
}}
requestAnimationFrame(tickAll);
</script></body></html>"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--combos", nargs="*", default=DEFAULT_COMBOS)
    args = ap.parse_args(argv)

    plan = bsp.plan_combos(args.src)
    combos = [c for c in args.combos if c in plan] or sys.exit("no requested combos exist")
    for c in args.combos:
        if c not in plan:
            print(f"warn: {c} not in masters, skipped", file=sys.stderr)

    manifests, sizes = {}, {}
    for v in VARIANTS:
        vout = os.path.join(args.out, "packs", v["id"])
        rc = bsp.main(["--src", args.src, "--out", vout, "--scale", str(v["scale"]),
                       "--fps", str(v["fps"]), "--quality", str(v["quality"]),
                       "--alpha-bits", str(v["alpha_bits"]), "--workers", "4",
                       "--only", *combos])
        if rc:
            return rc
        man = json.load(open(os.path.join(vout, "chips", "manifest.json"), encoding="utf-8"))
        manifests[v["id"]] = man
        for c in combos:
            for anim in man["combos"].get(c, {}).get("anims", {}):
                p = os.path.join(vout, "chips", f"{c}__{anim}.webp")
                sizes[(v["id"], c, anim)] = os.path.getsize(p)

    # animated-webp reference at the candidate recipe (smoothness comparison)
    from PIL import Image
    ref_dir = os.path.join(args.out, "animref")
    os.makedirs(ref_dir, exist_ok=True)
    cand = VARIANTS[0]
    step = sp.subsample_step(cand["fps"])
    for c in combos:
        d = os.path.join(args.src, "matte", f"{c}__idle_frames")
        files = sorted(f for f in os.listdir(d) if f.endswith(".png"))[::step]
        fw, fh = sp.encode_size(*Image.open(os.path.join(d, files[0])).size, cand["scale"])
        frames = [sp.quant_alpha(sp.premul_resize(Image.open(os.path.join(d, f)).convert("RGBA"),
                                                  (fw, fh)), cand["alpha_bits"]) for f in files]
        frames[0].save(os.path.join(ref_dir, f"{c}__idle.webp"), save_all=True,
                       append_images=frames[1:], duration=round(1000 / cand["fps"]),
                       loop=0, quality=cand["quality"], method=4)

    # placeholder sils for visual comparison (best effort)
    sil_dir = os.path.join(args.out, "placeholder_sil")
    os.makedirs(sil_dir, exist_ok=True)
    for k in range(4):
        rel = f"docs/design/site-redesign/sil/paul__idle_k{k}.png"
        try:
            data = subprocess.run(["git", "show", f"site-redesign-p1:{rel}"],
                                  capture_output=True, check=True).stdout
            open(os.path.join(sil_dir, f"paul__idle_k{k}.png"), "wb").write(data)
        except subprocess.CalledProcessError:
            print(f"warn: placeholder sil {rel} unavailable", file=sys.stderr)
            break

    html = render_html(combos, VARIANTS, manifests, sizes, _stepper_source())
    with open(os.path.join(args.out, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    print(f"lab ready: {os.path.join(args.out, 'index.html')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

(The sil-comparison strip is intentionally minimal in v1 of the page — the generated `__sil_k*.png` files sit beside the sheets in each variant pack; if eyeballing them inline proves necessary, extend `render_html` after the first look. Do NOT gold-plate before Paul has seen the page once.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_ab_lab.py -v`
Expected: PASS.

- [ ] **Step 5: Generate the real lab**

Run: `python tools/asset_matte/build_ab_lab.py --src "D:/kartoff/asset_chips" --out "D:/kartoff/asset_chips/ab_lab"`
Expected: exit 0, ~7 variant packs × 4 combos (~2 min), `index.html` written. Open it in a real browser (per repo rule — never judge visuals in OpenCV) and sanity-check: chips animate, SELECT/CONFIRM handoffs land (confirm on the kart enters idle mid-bob — that's `idle_resume` working), sizes labelled.

- [ ] **Step 6: Commit**

```bash
git add tools/asset_matte/build_ab_lab.py tests/test_ab_lab.py
git commit -m "feat(site-pack): A/B recipe lab page generator"
```

- [ ] **Step 7: 🚧 USER GATE — Paul locks the recipe**

Show Paul `D:\kartoff\asset_chips\ab_lab\index.html`. He picks: scale, fps, quality, alpha bits (side-by-side, change-one-thing). Record the locked recipe in the spec (Task 10) and use it for the Task 11 batch. **Do not proceed to Task 11's batch/release with an unlocked recipe.** (Tasks 7–10 are recipe-independent and may proceed in parallel with the wait.)

---

### Task 7: Shards + `chips.lock` — `pack_shards.py`

**Files:**
- Create: `tools/asset_matte/pack_shards.py`
- Test: `tests/test_pack_shards.py`

**Interfaces:**
- Consumes: a built `<pack>/chips/` dir (Task 4 output).
- Produces: `<pack>/release/chips-<char>.tar` (one per leading `<char>` of combo names; contains that char's `.webp` + `__sil_k*.png` flat), `<pack>/release/chips-manifest.json` (copy of the manifest), `<pack>/release/chips.lock`. Lock format (plain text, bash-parseable, `sha256sum -c`-compatible tail):

```
tag chips-v1
base https://github.com/paulporkhogkart/mkw-split-rewrite/releases/download/chips-v1
<sha256>  chips-manifest.json
<sha256>  chips-baby_daisy.tar
...
```

Functions: `char_of(name) -> str` (`"baby_daisy__base__b_dasher"` → `"baby_daisy"`); `build_shards(chips_dir, release_dir, tag, base_url) -> list[tuple[str, str]]` (name, sha256 pairs; deterministic file order inside tars); `main(argv)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pack_shards.py
import hashlib
import json
import os
import tarfile

from tools.asset_matte import pack_shards as ps


def _mini_pack(root):
    chips = os.path.join(root, "chips")
    os.makedirs(chips)
    names = ["a__base__k1__idle.webp", "a__base__k1__idle__sil_k0.png",
             "a__base__k2__idle.webp", "b__base__idle.webp"]
    for n in names:
        open(os.path.join(chips, n), "wb").write(n.encode())
    json.dump({"version": 1, "combos": {}}, open(os.path.join(chips, "manifest.json"), "w"))
    return chips


def test_char_of():
    assert ps.char_of("baby_daisy__base__b_dasher__idle.webp") == "baby_daisy"
    assert ps.char_of("mario__base__flourish__sil_k2.png") == "mario"


def test_build_shards_layout_and_lock(tmp_path):
    chips = _mini_pack(str(tmp_path))
    rel = os.path.join(str(tmp_path), "release")
    files = ps.build_shards(chips, rel, "chips-v9", "https://example.test/dl/chips-v9")
    names = [n for n, _ in files]
    assert names[0] == "chips-manifest.json"
    assert set(names) == {"chips-manifest.json", "chips-a.tar", "chips-b.tar"}
    with tarfile.open(os.path.join(rel, "chips-a.tar")) as t:
        members = t.getnames()
    assert sorted(members) == ["a__base__k1__idle.webp", "a__base__k1__idle__sil_k0.png",
                               "a__base__k2__idle.webp"]
    lock = open(os.path.join(rel, "chips.lock")).read().splitlines()
    assert lock[0] == "tag chips-v9"
    assert lock[1] == "base https://example.test/dl/chips-v9"
    for line, (name, sha) in zip(lock[2:], files):
        assert line == f"{sha}  {name}"
    # shas are real
    h = hashlib.sha256(open(os.path.join(rel, "chips-b.tar"), "rb").read()).hexdigest()
    assert (f"{h}  chips-b.tar") in lock
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_pack_shards.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

```python
# tools/asset_matte/pack_shards.py
"""Shard a built site pack into per-character release tars + chips.lock.

  python tools/asset_matte/pack_shards.py --pack D:\\kartoff\\asset_chips\\site_pack \\
      --tag chips-v1
Writes <pack>/release/. The lock is committed to web/chips.lock by the release runbook;
the pack itself NEVER enters git.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import tarfile

DEFAULT_BASE = "https://github.com/paulporkhogkart/mkw-split-rewrite/releases/download"


def char_of(filename: str) -> str:
    return filename.split("__", 1)[0]


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_shards(chips_dir: str, release_dir: str, tag: str, base_url: str) -> list[tuple[str, str]]:
    os.makedirs(release_dir, exist_ok=True)
    by_char: dict[str, list[str]] = {}
    for f in sorted(os.listdir(chips_dir)):
        if f == "manifest.json":
            continue
        by_char.setdefault(char_of(f), []).append(f)

    files: list[tuple[str, str]] = []
    man_out = os.path.join(release_dir, "chips-manifest.json")
    shutil.copyfile(os.path.join(chips_dir, "manifest.json"), man_out)
    files.append(("chips-manifest.json", _sha256(man_out)))

    for char, members in sorted(by_char.items()):
        tar_path = os.path.join(release_dir, f"chips-{char}.tar")
        with tarfile.open(tar_path, "w") as t:  # uncompressed: webp/png don't recompress
            for m in members:
                t.add(os.path.join(chips_dir, m), arcname=m)
        files.append((f"chips-{char}.tar", _sha256(tar_path)))

    with open(os.path.join(release_dir, "chips.lock"), "w", newline="\n") as f:
        f.write(f"tag {tag}\nbase {base_url}\n")
        for name, sha in files:
            f.write(f"{sha}  {name}\n")
    return files


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pack", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--base-url", default=None)
    args = ap.parse_args(argv)
    base = args.base_url or f"{DEFAULT_BASE}/{args.tag}"
    files = build_shards(os.path.join(args.pack, "chips"),
                         os.path.join(args.pack, "release"), args.tag, base)
    total = sum(os.path.getsize(os.path.join(args.pack, "release", n)) for n, _ in files)
    print(f"{len(files)} release files, {total/1e9:.2f}GB; lock at "
          f"{os.path.join(args.pack, 'release', 'chips.lock')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_pack_shards.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/asset_matte/pack_shards.py tests/test_pack_shards.py
git commit -m "feat(site-pack): per-character release shards + chips.lock"
```

---

### Task 8: Serve the pack — `web/serve.mjs` `/chips/anim/` route

**Files:**
- Modify: `web/serve.mjs`
- Test: `web/serve.test.js` (append)

**Interfaces:**
- Consumes: `$MKW_CHIPS_DIR` layout produced by Task 9's fetcher: `<chipsDir>/<tag>/chips/...` + `<chipsDir>/current` (symlink or text file naming the tag — support BOTH: if `current` is a regular file, read the tag from it; a symlink resolves transparently).
- Produces: URL contract the site + pbenguin rely on:
  - `GET /chips/anim/manifest.json` → current tag's `chips/manifest.json`, `cache-control: public, max-age=300`, plus header `x-chips-tag: <tag>`. The JSON body gains `"base": "/chips/anim/<tag>/"` (injected at serve time).
  - `GET /chips/anim/<tag>/<file>` → `<chipsDir>/<tag>/chips/<file>`, `cache-control: public, max-age=31536000, immutable` (tag in path ⇒ content-addressed).
  - 404 for anything missing (no SPA fallback under `/chips/anim/`).

- [ ] **Step 1: Write the failing tests** (append to `web/serve.test.js`, matching its existing style — it tests exported helpers and/or the server via `http` requests; follow whichever pattern the file already uses. The tests below use supertest-free plain `fetch` against a listening server on an ephemeral port, which works with node's built-in test setup under vitest.)

```javascript
// append to web/serve.test.js
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createStaticServer } from "./serve.mjs";

async function withServer(distDir, chipsDir, fn) {
  const srv = createStaticServer(distDir, { chipsDir });
  await new Promise((r) => srv.listen(0, r));
  const base = `http://127.0.0.1:${srv.address().port}`;
  try { return await fn(base); } finally { srv.close(); }
}

function chipsFixture() {
  const root = mkdtempSync(join(tmpdir(), "chips-"));
  mkdirSync(join(root, "chips-v1", "chips"), { recursive: true });
  writeFileSync(join(root, "chips-v1", "chips", "manifest.json"),
    JSON.stringify({ version: 1, combos: {} }));
  writeFileSync(join(root, "chips-v1", "chips", "a__idle.webp"), "RIFFfake");
  writeFileSync(join(root, "current"), "chips-v1");  // text-file form of `current`
  return root;
}

describe("/chips/anim/", () => {
  const dist = mkdtempSync(join(tmpdir(), "dist-"));
  writeFileSync(join(dist, "index.html"), "<html>spa</html>");

  it("serves the current manifest with short cache and injected base", async () => {
    await withServer(dist, chipsFixture(), async (base) => {
      const r = await fetch(`${base}/chips/anim/manifest.json`);
      expect(r.status).toBe(200);
      expect(r.headers.get("cache-control")).toContain("max-age=300");
      expect(r.headers.get("x-chips-tag")).toBe("chips-v1");
      const j = await r.json();
      expect(j.base).toBe("/chips/anim/chips-v1/");
    });
  });

  it("serves tagged assets immutable", async () => {
    await withServer(dist, chipsFixture(), async (base) => {
      const r = await fetch(`${base}/chips/anim/chips-v1/a__idle.webp`);
      expect(r.status).toBe(200);
      expect(r.headers.get("content-type")).toBe("image/webp");
      expect(r.headers.get("cache-control")).toContain("immutable");
    });
  });

  it("404s missing chip files without SPA fallback", async () => {
    await withServer(dist, chipsFixture(), async (base) => {
      const r = await fetch(`${base}/chips/anim/chips-v1/nope.webp`);
      expect(r.status).toBe(404);
      const r2 = await fetch(`${base}/chips/anim/manifest.json`, { method: "GET" });
      expect(r2.status).toBe(200);
    });
  });

  it("without chipsDir the prefix 404s (extension) as before", async () => {
    await withServer(dist, undefined, async (base) => {
      const r = await fetch(`${base}/chips/anim/chips-v1/a__idle.webp`);
      expect(r.status).toBe(404);
    });
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm --prefix web test`
Expected: new tests FAIL (`createStaticServer` ignores the second argument today).

- [ ] **Step 3: Implement** — modify `web/serve.mjs`:

```javascript
// serve.mjs modifications (keep everything else as-is):
import { readFile, stat, readlink } from "node:fs/promises";   // + readlink

const CHIPS_PREFIX = "/chips/anim/";

/** Resolve the current chips tag: `current` may be a symlink to the tag dir or a
 *  text file containing the tag name. Returns null when unset/invalid. */
export async function currentChipsTag(chipsDir) {
  const p = join(chipsDir, "current");
  try { return (await readlink(p)).replace(/[/\\]+$/, "").split(/[/\\]/).pop(); }
  catch { /* not a symlink */ }
  try { return (await readFile(p, "utf8")).trim() || null; }
  catch { return null; }
}

export function createStaticServer(distDir, opts = {}) {
  const chipsDir = opts.chipsDir ?? process.env.MKW_CHIPS_DIR;
  const indexHtml = join(distDir, "index.html");
  return createServer(async (req, res) => {
    try {
      const rawPath = decodeURIComponent((req.url || "/").split("?")[0]);
      if (rawPath.startsWith(CHIPS_PREFIX)) {
        if (!chipsDir) { res.writeHead(404); res.end("not found"); return; }
        const rest = rawPath.slice(CHIPS_PREFIX.length);
        if (rest === "manifest.json") {
          const tag = await currentChipsTag(chipsDir);
          const file = tag && resolveFile(`/${tag}/chips/manifest.json`, chipsDir);
          const body = file && await readFile(file).catch(() => null);
          if (!body) { res.writeHead(404); res.end("not found"); return; }
          const j = JSON.parse(body);
          j.base = `${CHIPS_PREFIX}${tag}/`;
          res.writeHead(200, { "content-type": TYPES[".json"],
            "cache-control": "public, max-age=300", "x-chips-tag": tag });
          res.end(JSON.stringify(j)); return;
        }
        const [tag, ...restParts] = rest.split("/");
        const file = resolveFile(`/${restParts.join("/")}`, join(chipsDir, tag, "chips"));
        const ok = await stat(file).then((s) => s.isFile()).catch(() => false);
        if (!ok) { res.writeHead(404); res.end("not found"); return; }
        res.writeHead(200, { "content-type": contentType(file),
          "cache-control": "public, max-age=31536000, immutable" });
        res.end(await readFile(file)); return;
      }
      // ... existing dist/SPA logic unchanged ...
```

Keep the existing dist logic verbatim below the chips block. The direct-run block at the bottom stays the same (`createStaticServer(distDir)` — env picks up `MKW_CHIPS_DIR`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm --prefix web test`
Expected: PASS — new tests + all pre-existing serve tests (no regression to SPA fallback, 404s, cache headers on dist files).

- [ ] **Step 5: Commit**

```bash
git add web/serve.mjs web/serve.test.js
git commit -m "feat(site-pack): serve /chips/anim/ from MKW_CHIPS_DIR (tagged, immutable)"
```

---

### Task 9: Pi pull — `deploy/fetch_chips.sh` + `update.sh` + unit env

**Files:**
- Create: `deploy/fetch_chips.sh`
- Modify: `deploy/update.sh` (after the web build, before systemctl restart)
- Modify: `deploy/systemd/mkw-web.service` (add `Environment=MKW_CHIPS_DIR=/home/pi/mkw-data/chips`)
- Test: `tests/test_fetch_chips.py` (runs the bash script against `file://` URLs — no network)

**Interfaces:**
- Consumes: `web/chips.lock` (Task 7 format), GitHub Release download URLs (or any `base` — `file://` in tests).
- Produces: `$CHIPS_DIR/<tag>/chips/<files...>` + `$CHIPS_DIR/current` (text file containing the tag — text, not symlink, so it also works if `$CHIPS_DIR` is ever on a filesystem without symlinks; Task 8 supports both) + `$CHIPS_DIR/<tag>/.complete` stamp. Idempotent: same tag + `.complete` → exit 0 doing nothing. Keeps ONE previous tag, deletes older.

- [ ] **Step 1: Write the script**

```bash
#!/usr/bin/env bash
# deploy/fetch_chips.sh <chips.lock> <chips-data-dir>
# Download the chip pack pinned by chips.lock into <dir>/<tag>/chips/, verify sha256,
# then flip <dir>/current. Outbound-only, idempotent, atomic-ish (staging dir + rename).
# Exit 0 on success or already-current; non-zero on failure (caller treats as non-fatal).
set -euo pipefail

LOCK="$1"; DIR="$2"
command -v curl >/dev/null && command -v sha256sum >/dev/null && command -v tar >/dev/null

TAG=$(awk '$1=="tag"{print $2; exit}' "$LOCK")
BASE=$(awk '$1=="base"{print $2; exit}' "$LOCK")
[ -n "$TAG" ] && [ -n "$BASE" ] || { echo "chips: bad lock"; exit 1; }

if [ -f "$DIR/$TAG/.complete" ]; then
  echo "$TAG" > "$DIR/current"
  echo "chips: $TAG already present"; exit 0
fi

STAGE="$DIR/.stage-$TAG"
rm -rf "$STAGE"; mkdir -p "$STAGE/chips"

# lines after the two headers are "sha256  filename"
tail -n +3 "$LOCK" > "$STAGE/sums.txt"
while read -r _sha name; do
  [ -n "$name" ] || continue
  echo "chips: fetching $name"
  curl -fsSL --retry 3 --retry-delay 5 -o "$STAGE/$name" "$BASE/$name"
done < "$STAGE/sums.txt"
(cd "$STAGE" && sha256sum -c sums.txt --quiet)

for t in "$STAGE"/chips-*.tar; do
  [ -e "$t" ] || continue
  tar -xf "$t" -C "$STAGE/chips" && rm "$t"
done
mv "$STAGE/chips-manifest.json" "$STAGE/chips/manifest.json"
rm "$STAGE/sums.txt"

rm -rf "$DIR/$TAG"
mv "$STAGE" "$DIR/$TAG"
touch "$DIR/$TAG/.complete"
PREV=$(cat "$DIR/current" 2>/dev/null || true)
echo "$TAG" > "$DIR/current"
# retain only the previous tag; drop anything older
for d in "$DIR"/chips-v*; do
  b=$(basename "$d")
  [ "$b" = "$TAG" ] || [ "$b" = "$PREV" ] || rm -rf "$d"
done
echo "chips: $TAG deployed"
```

`chmod +x deploy/fetch_chips.sh` (and commit the bit: `git update-index --chmod=+x deploy/fetch_chips.sh`).

- [ ] **Step 2: Write the failing test**

```python
# tests/test_fetch_chips.py
"""fetch_chips.sh against file:// URLs (bash from Git Bash / Pi both fine)."""
import hashlib
import os
import shutil
import subprocess
import tarfile

import pytest

BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash unavailable")
SCRIPT = os.path.join(os.path.dirname(__file__), "..", "deploy", "fetch_chips.sh")


def _mk_release(root):
    rel = os.path.join(root, "release"); os.makedirs(rel)
    chip = os.path.join(root, "a__idle.webp"); open(chip, "wb").write(b"RIFFfake")
    with tarfile.open(os.path.join(rel, "chips-a.tar"), "w") as t:
        t.add(chip, arcname="a__idle.webp")
    open(os.path.join(rel, "chips-manifest.json"), "w").write('{"version":1}')
    lines = [f"tag chips-v1", f"base file://{rel.replace(os.sep, '/')}"]
    for n in ("chips-manifest.json", "chips-a.tar"):
        sha = hashlib.sha256(open(os.path.join(rel, n), "rb").read()).hexdigest()
        lines.append(f"{sha}  {n}")
    lock = os.path.join(root, "chips.lock")
    open(lock, "w", newline="\n").write("\n".join(lines) + "\n")
    return lock, rel


def _run(lock, data):
    return subprocess.run([BASH, SCRIPT, lock, data], capture_output=True, text=True)


def test_fetch_deploys_and_is_idempotent(tmp_path):
    lock, _ = _mk_release(str(tmp_path))
    data = os.path.join(str(tmp_path), "data")
    os.makedirs(data)
    r = _run(lock, data)
    assert r.returncode == 0, r.stderr
    assert open(os.path.join(data, "current")).read().strip() == "chips-v1"
    assert os.path.exists(os.path.join(data, "chips-v1", "chips", "a__idle.webp"))
    assert os.path.exists(os.path.join(data, "chips-v1", "chips", "manifest.json"))
    assert os.path.exists(os.path.join(data, "chips-v1", ".complete"))
    r2 = _run(lock, data)
    assert r2.returncode == 0 and "already present" in r2.stdout


def test_fetch_fails_on_bad_sha_and_leaves_no_tag(tmp_path):
    lock, rel = _mk_release(str(tmp_path))
    open(os.path.join(rel, "chips-a.tar"), "ab").write(b"corrupt")
    data = os.path.join(str(tmp_path), "data"); os.makedirs(data)
    r = _run(lock, data)
    assert r.returncode != 0
    assert not os.path.exists(os.path.join(data, "chips-v1"))
    assert not os.path.exists(os.path.join(data, "current"))
```

- [ ] **Step 3: Run the test**

Run: `python -m pytest tests/test_fetch_chips.py -v`
Expected: PASS (if the sha256sum-relative-path or `file://` curl quirks bite on Git Bash, fix the SCRIPT — e.g. `curl` needs `file:///C:/...` triple slash, which the `file://{rel}` construction above produces on absolute Windows paths converted with forward slashes; adjust until green — the script must stay POSIX-correct for the Pi).

- [ ] **Step 4: Wire into `update.sh`** — insert between `npm --prefix "$REPO/web" run build` and `sudo systemctl restart ...`:

```bash
# Chip pack (non-fatal: site serves the previous pack until a fetch succeeds)
if [ -f "$REPO/web/chips.lock" ]; then
  bash "$REPO/deploy/fetch_chips.sh" "$REPO/web/chips.lock" "$DATA/chips" \
    || echo "warn: chips fetch failed; keeping current pack"
fi
```

And in `deploy/systemd/mkw-web.service`, under `[Service]` add:

```ini
Environment=MKW_CHIPS_DIR=/home/pi/mkw-data/chips
```

- [ ] **Step 5: Re-run the full Python suite**

Run: `python -m pytest tests/ -x -q`
Expected: PASS (no regressions; the suite is large — expect several minutes).

- [ ] **Step 6: Commit**

```bash
git add deploy/fetch_chips.sh deploy/update.sh deploy/systemd/mkw-web.service tests/test_fetch_chips.py
git update-index --chmod=+x deploy/fetch_chips.sh
git commit -m "feat(site-pack): Pi chip-pack pull — fetch_chips.sh + update.sh step + unit env"
```

---

### Task 10: Docs — spec amendment, CLAUDE.md, README

**Files:**
- Modify: `docs/superpowers/specs/2026-07-18-chip-site-pack-design.md`
- Modify: `CLAUDE.md` (root — one line) and `web/CLAUDE.md` (one line)
- Modify: `tools/asset_matte/README.md` (short section)

- [ ] **Step 1: Amend the spec** — in the Delivery section, replace "Pi serves `$DATA/chips/` at `/chips/`" with the shipped contract:

```markdown
- **Pi serves** the pack at **`/chips/anim/`** (`web/serve.mjs`, `MKW_CHIPS_DIR`): the bare
  `/chips/` namespace was already taken by the activity-feed chips (`web/src/lib/chips.js`).
  `GET /chips/anim/manifest.json` (max-age=300) returns the current pack's manifest with an
  injected `"base": "/chips/anim/<tag>/"`; all sheet/sil URLs live under that tagged base and
  are served `immutable` (tag dir per `chips-vN` ⇒ cache-safe across re-encodes). The Pi keeps
  the current + one previous tag under `$DATA/chips/`.
```

Also append the **locked recipe** from the Task 6 gate to the "Encode recipe" section, e.g.:

```markdown
**LOCKED (A/B eye test, 2026-MM-DD):** scale ___, fps ___, quality ___, alpha ___ bits.
```

(fill with Paul's actual picks — this line is written only after the gate).

- [ ] **Step 2: Breadcrumbs** — root `CLAUDE.md` (in the asset/data table area) and `web/CLAUDE.md` (near the media rules) each get one line:

```markdown
Chip sprite-sheet pack: built by `tools/asset_matte/build_site_pack.py`, delivered as GitHub
Release assets pinned by `web/chips.lock`, served at `/chips/anim/` — see
`docs/superpowers/specs/2026-07-18-chip-site-pack-design.md`.
```

`tools/asset_matte/README.md` gets a "Site pack" section with the four commands (build_site_pack, build_ab_lab, pack_shards, and the release runbook pointer to Task 11's commands).

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-07-18-chip-site-pack-design.md CLAUDE.md web/CLAUDE.md tools/asset_matte/README.md
git commit -m "docs(site-pack): /chips/anim/ contract, locked recipe, breadcrumbs"
```

---

### Task 11: 🚧 Batch + release RUNBOOK (user-gated, run WITH Paul after the Task 6 recipe lock)

No new code. Exact commands, in order — each verified before the next:

- [ ] **Step 1: Full batch encode** (dev rig, ~1.5–2h at 12 workers; resumable):

```bash
python tools/asset_matte/build_site_pack.py --src "D:/kartoff/asset_chips" \
  --out "D:/kartoff/asset_chips/site_pack" \
  --scale <LOCKED> --fps <LOCKED> --quality <LOCKED> --alpha-bits <LOCKED> --workers 12
```
Expected: `done: 6426 encoded, 0 failed` (6,273 kart + 153 char combos). Any FAIL lines: re-run (book resumes); persistent failures → stop and investigate, do not ship a partial pack. Spot-check 3 random sheets in a browser.

- [ ] **Step 2: Shard + lock:**

```bash
python tools/asset_matte/pack_shards.py --pack "D:/kartoff/asset_chips/site_pack" --tag chips-v1
```
Expected: ~50 shard files, ~2.1–2.5GB total, `release/chips.lock` written.

- [ ] **Step 3: GitHub Release upload** (Paul-approved; public repo):

```bash
gh release create chips-v1 --title "Chip pack v1" --notes "Sprite-sheet chip pack (recipe: <LOCKED>). Pinned by web/chips.lock." --latest=false
cd "D:/kartoff/asset_chips/site_pack/release" && gh release upload chips-v1 chips-manifest.json chips-*.tar
```
Expected: all assets uploaded (re-run `gh release upload --clobber` on flaky failures). Verify: `curl -fsSL -o NUL -w "%{http_code}" https://github.com/paulporkhogkart/mkw-split-rewrite/releases/download/chips-v1/chips-manifest.json` → 200.

- [ ] **Step 4: Commit the lock:**

```bash
cp "D:/kartoff/asset_chips/site_pack/release/chips.lock" web/chips.lock
git add web/chips.lock
git commit -m "feat(site-pack): pin chip pack chips-v1"
```

- [ ] **Step 5: Local end-to-end check** (before any deploy): run fetch + serve against the REAL release:

```bash
bash deploy/fetch_chips.sh web/chips.lock /tmp/chips-e2e
MKW_CHIPS_DIR=/tmp/chips-e2e node web/serve.mjs &   # PORT 8788
curl -fsS http://127.0.0.1:8788/chips/anim/manifest.json | head -c 200
curl -fsS -o NUL -w "%{http_code} %{size_download}\n" \
  http://127.0.0.1:8788/chips/anim/chips-v1/baby_daisy__base__b_dasher__idle.webp
```
Expected: manifest JSON with `"base":"/chips/anim/chips-v1/"`; 200 + ~190KB sheet. Kill the server after.

- [ ] **Step 6: Ship via the normal tag deploy** (merge to main first — finishing-a-development-branch — then Paul's usual eye-check + tag per `docs/pi-deploy.md`). On the Pi's next timer tick, `update.sh` builds AND pulls the pack; verify `https://thekartoff.com/chips/anim/manifest.json` serves 200 with `x-chips-tag: chips-v1`.

---

## Explicitly OUT of this plan (later plans, per spec)

- Live-card/site integration of the stepper (site-redesign P1b consumes `src/lib/chipSheet.js` + `/chips/anim/` when its branch lands).
- pbenguin cache + opt-in full-pack download UI (contract is fixed by Tasks 5/7/8).
- Safari/HEVC fallback, animated AVIF, kart-alone chips.

## Self-review notes (already applied)

- Spec coverage: format+recipe (T1/2/6), sil masks (T3), manifest (T4), stepper+handoffs (T5), A/B gate (T6), shards+lock (T7), Pi serve (T8), Pi pull+unit (T9), spec sync (T10), batch+release (T11). pbenguin/site integration deliberately out (spec says later plans).
- The `/chips/` → `/chips/anim/` route change is a spec amendment, flagged in the header and written in T10.
- Type/name consistency: manifest shape defined once (T4) and consumed verbatim in T5/T6/T8; lock format defined in T7 and parsed in T9; `current` file contract defined in T9 and read in T8 (both symlink and text file supported).
