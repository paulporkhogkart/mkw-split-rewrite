# MatAnyone2 Matte Engine Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the per-frame birefnet chip matte with MatAnyone2 mask-guided video matting (bidirectional), running everything in one unified GPU venv, with the old engine kept behind a flag.

**Architecture:** A new `matte_matanyone.py` (GPU-venv-only, lazy heavy imports) ports the validated `f267585a` prototypes into a model-cached, in-process engine: given a segment's predark frames + birefnet first/last-frame masks, it propagates forward and backward and merges position-weighted. `matte_blankplate.matte_loopframes` gains a `MATTE_ENGINE` switch that routes to it; `process_all.py`/console/viewer are unchanged except the console's GPU python is repointed to the unified venv.

**Tech Stack:** Python 3.12, onnxruntime-gpu (birefnet via rembg), torch 2.11.0+cu128 (MatAnyone2), OpenCV, NumPy, Pillow. RTX 5080 (Blackwell, CUDA 12.x / cuDNN 9.x).

## Global Constraints

- All new GPU code runs in a **single unified venv** `temp/asset-venv-matte` (py3.12) = onnxruntime-gpu + rembg + torch cu128 + MatAnyone2. No cross-venv piping, no worker process. (spec §Architecture)
- `matte_matanyone.py` top-level imports **only** `numpy`, `os`, `sys`, `glob` — all heavy imports (`cv2`, `torch`, `matanyone2`) are **lazy inside functions**, so build python can import the module to unit-test the pure merge. (plan File Structure)
- MatAnyone2 stock params: `n_warmup=10`, `r_erode=10`, `r_dilate=10`. (spec §Matte flow)
- Bidirectional merge weight: `w = 1 − t/max(1, N−1)`, `alpha[t] = clip(w·fwd[t] + (1−w)·bwd[t], 0, 1)`. (spec §Matte flow step 6)
- First/last-frame masks are birefnet **binary** `alpha > 0.5` (no soft "fuller" mask). (spec §Non-goals)
- Predark stays exactly as today: kart→`_kart_predark`, char→`pre_darken`; `apply_predark` ON for spawn+idle, OFF for kart flourish only. (spec §Matte flow step 1)
- `matte_loopframes(framedir, name, out_base, clip=None, backdrop=None, apply_predark=True, is_kart=None)` signature and output artifacts (`<name>_frames/NNN.png`, `<name>_loop.webp`, `<name>_checker.webp`) are **unchanged**. (spec §Module layout)
- MatAnyone2 repo lives at `temp/MatAnyone2`; model auto-downloads (`matanyone2.pth`, ~135 MB). (spec §Architecture)
- Reuse the validated prototypes verbatim where possible; do not re-derive. (spec §Reuse)
- Build-python tests import flat (conftest adds `tools/asset_matte` + `tools/sweep_console` to `sys.path`).

---

## File Structure

- **Create** `tools/asset_matte/matte_matanyone.py` — MatAnyone2 engine: `merge_bidir` (pure), `_model` (cached load), `_propagate` (ported `main()`), `matte_segment` (bidir orchestration).
- **Create** `tools/asset_matte/smoke_coexist.py` — one-process coexistence gate (birefnet + MatAnyone2 both on CUDA).
- **Create** `tests/test_matte_matanyone.py` — build-python unit tests for `merge_bidir`.
- **Modify** `tools/asset_matte/matte_blankplate.py` — extract `_build_predark_frames` + `_write_chip`; add `MATTE_ENGINE` switch in `matte_loopframes`.
- **Modify** `tools/sweep_console/supervisor.py:34` — repoint `self.gpu_py` to the unified venv.
- **Modify** `tests/test_console_supervisor.py` — assert the new gpu_py path.
- **Docs** `docs/asset-matte-venv.md` — reproducible unified-venv build steps (env is gitignored).

---

## Task 1: Unified venv + coexistence smoke gate

Builds the single venv and proves birefnet (onnxruntime) and MatAnyone2 (torch) coexist on CUDA in one process. **Blocking** — nothing else proceeds until the smoke test passes.

**Files:**
- Create: `tools/asset_matte/smoke_coexist.py`
- Create: `docs/asset-matte-venv.md`
- Env (gitignored, not committed): `temp/asset-venv-matte`

**Interfaces:**
- Produces: a working `temp/asset-venv-matte/Scripts/python.exe` that can `import rembg`, `import torch` (CUDA), and `import matanyone2` in one process.

- [ ] **Step 1: Create the unified venv and install onnx/rembg + torch cu128**

```bash
cd /c/development/mkw-split-rewrite
py -3.12 -m venv temp/asset-venv-matte
temp/asset-venv-matte/Scripts/python.exe -m pip install --upgrade pip
# torch cu128 (Blackwell 5080)
temp/asset-venv-matte/Scripts/python.exe -m pip install torch==2.11.0 torchvision==0.26.0 --index-url https://download.pytorch.org/whl/cu128
# birefnet stack + matte deps
temp/asset-venv-matte/Scripts/python.exe -m pip install onnxruntime-gpu==1.22.0 rembg==2.0.76 opencv-python numpy pillow
# MatAnyone2 minimal deps + the package itself (--no-deps: its pyproject drags cchardet/PySide6/netifaces)
temp/asset-venv-matte/Scripts/python.exe -m pip install hydra-core omegaconf einops tqdm imageio imageio-ffmpeg safetensors huggingface_hub easydict requests av
temp/asset-venv-matte/Scripts/python.exe -m pip install --no-deps -e temp/MatAnyone2
```

Expected: all installs succeed. (If torch/onnxruntime resolve conflicting `numpy`, pin `numpy<2.3` and re-run the two failing installs.)

- [ ] **Step 2: Write the coexistence smoke test**

Create `tools/asset_matte/smoke_coexist.py`:

```python
"""Coexistence gate: prove birefnet (onnxruntime-gpu) and MatAnyone2 (torch cu128) both run on
CUDA in ONE process (the unified temp/asset-venv-matte). Mirrors the real pipeline's import order:
birefnet first (rembg + its nvidia-cudnn), then torch/MatAnyone2. Exit 0 = gate passed.

Run: temp/asset-venv-matte/Scripts/python.exe tools/asset_matte/smoke_coexist.py
"""
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)                                   # matte_blankplate (for _setup_cuda + _birefnet)
_MA2 = os.path.abspath(os.path.join(_HERE, "..", "..", "temp", "MatAnyone2"))
sys.path.insert(0, _MA2)


def main():
    # 1) birefnet on CUDA (this also runs matte_blankplate._setup_cuda at import)
    import matte_blankplate as mb
    img = (np.random.rand(256, 256, 3) * 255).astype(np.uint8)
    alpha, _ = mb._birefnet(img)
    assert alpha.shape == (256, 256), alpha.shape
    prov = mb._session().get_providers()
    assert "CUDAExecutionProvider" in prov, prov
    print(f"birefnet OK on {prov[0]} alpha[min,max]={alpha.min():.3f},{alpha.max():.3f}", flush=True)

    # 2) torch + MatAnyone2 on CUDA, same process
    import torch
    assert torch.cuda.is_available(), "torch reports no CUDA"
    x = torch.ones(4, 4, device="cuda") * 2
    assert float(x.sum().item()) == 32.0
    from matanyone2.utils.download_util import load_file_from_url
    from matanyone2.utils.get_default_model import get_matanyone2_model
    from matanyone2.utils.device import get_default_device
    dev = get_default_device()
    url = "https://github.com/pq-yang/MatAnyone2/releases/download/v1.0.0/matanyone2.pth"
    ckpt = load_file_from_url(url, os.path.join(_MA2, "pretrained_models"))
    model = get_matanyone2_model(ckpt, dev)
    from matanyone2.inference.inference_core import InferenceCore
    proc = InferenceCore(model, cfg=model.cfg)
    frame = torch.rand(3, 64, 64, device=dev)
    mask = torch.zeros(64, 64, device=dev); mask[20:44, 20:44] = 1.0
    with torch.inference_mode():
        proc.step(frame, mask, objects=[1])
        op = proc.step(frame, first_frame_pred=True)
        out = proc.output_prob_to_mask(op)
    assert tuple(out.shape) == (64, 64), out.shape
    print(f"MatAnyone2 OK on {dev} step-out {tuple(out.shape)}", flush=True)
    print("COEXIST OK", flush=True)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run the smoke gate**

Run:
```bash
temp/asset-venv-matte/Scripts/python.exe tools/asset_matte/smoke_coexist.py
```
Expected: prints `birefnet OK on CUDAExecutionProvider ...`, `MatAnyone2 OK on cuda ...`, then `COEXIST OK`, exit 0.

**If it fails with a cuDNN/CUDA DLL error** (e.g. `Could not load library cudnn...` or an onnxruntime CUDA init failure after torch import): the two vendored cuDNN copies conflict. Resolution levers before proceeding — do NOT build a worker fallback:
1. Try importing torch **before** birefnet (swap steps 1/2 in the script) — establishes whether load order is the trigger.
2. Align cuDNN: `pip install "nvidia-cudnn-cu12==9.*"` matching torch's bundled version, or add torch's `torch/lib` to the front of the DLL path via `os.add_dll_directory` at process start.
3. If unresolvable, pin a known-compatible onnxruntime-gpu / torch pairing (onnxruntime-gpu 1.22 + torch cu128 both target CUDA 12.x/cuDNN 9.x, so alignment is expected to work).
Iterate the venv until the gate passes.

- [ ] **Step 4: Document the venv build**

Create `docs/asset-matte-venv.md` recording the exact commands from Step 1 + the smoke command from Step 3 + any cuDNN pin discovered in Step 3, so the gitignored env is reproducible.

- [ ] **Step 5: Commit**

```bash
git add tools/asset_matte/smoke_coexist.py docs/asset-matte-venv.md
git commit -m "asset-matte: unified venv + birefnet/MatAnyone2 coexistence smoke gate"
```

---

## Task 2: `matte_matanyone.py` — ported engine + bidirectional merge

Ports the validated prototypes into a model-cached, in-process engine. The pure merge is unit-tested in build python; the GPU path is validated by a one-segment run in the unified venv.

**Files:**
- Create: `tools/asset_matte/matte_matanyone.py`
- Test: `tests/test_matte_matanyone.py`

**Interfaces:**
- Consumes: `temp/MatAnyone2` (MatAnyone2 package), the unified venv from Task 1.
- Produces:
  - `merge_bidir(fwd: list[np.ndarray], bwd: list[np.ndarray]) -> list[np.ndarray]` — pure, position-weighted merge (both lists same order, HxW float01).
  - `matte_segment(frames_bgr: list[np.ndarray], first_mask_u8: np.ndarray, last_mask_u8: np.ndarray, warmup=10, erode=10, dilate=10) -> list[np.ndarray]` — bidirectional alpha (HxW float01) per input frame. `frames_bgr` are BGR uint8; masks are HxW uint8 (0/255).

- [ ] **Step 1: Write the failing unit test for `merge_bidir`**

Create `tests/test_matte_matanyone.py`:

```python
import numpy as np
import matte_matanyone as mm      # FLAT import — conftest adds tools/asset_matte to sys.path


def _const(v, n):
    return [np.full((2, 2), v, dtype=np.float32) for _ in range(n)]


def test_merge_weights_forward_early_backward_late():
    # fwd all 1.0, bwd all 0.0 -> weight w=1-t/(N-1): first frame=1.0, last frame=0.0, mid=0.5
    fwd, bwd = _const(1.0, 3), _const(0.0, 3)
    out = mm.merge_bidir(fwd, bwd)
    assert np.allclose(out[0], 1.0)
    assert np.allclose(out[1], 0.5)
    assert np.allclose(out[2], 0.0)


def test_merge_single_frame_is_forward_only():
    # N=1 -> w=1.0 (max(1,N-1) guard, no divide-by-zero), backward ignored
    out = mm.merge_bidir(_const(0.7, 1), _const(0.2, 1))
    assert np.allclose(out[0], 0.7)


def test_merge_clips_to_unit_range():
    out = mm.merge_bidir([np.full((2, 2), 2.0, np.float32)], [np.full((2, 2), -1.0, np.float32)])
    assert out[0].max() <= 1.0 and out[0].min() >= 0.0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_matte_matanyone.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'matte_matanyone'`.

- [ ] **Step 3: Create `matte_matanyone.py` with the pure merge (light imports only)**

Create `tools/asset_matte/matte_matanyone.py`:

```python
"""MatAnyone2 mask-guided VIDEO matte engine (bidirectional). Runs in the unified GPU venv
(temp/asset-venv-matte: onnxruntime birefnet + torch cu128 MatAnyone2 in ONE process).

Port of the validated f267585a prototypes (prep_matanyone_seg.py / bidir_prep.py / bidir_merge.py
/ MatAnyone2's inference_matanyone2.py) into an importable, model-cached engine. Given a segment's
predark input frames plus birefnet first- and last-frame binary masks, it propagates the mask
FORWARD and (over reversed frames) BACKWARD, then merges position-weighted -> a temporally stable
alpha per frame that kills the per-frame-birefnet flicker.

Only numpy/os/sys/glob are imported at module top so build python can unit-test merge_bidir; all
heavy imports (cv2, torch, matanyone2) are lazy inside the GPU functions.
"""
import glob
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_MA2 = os.path.abspath(os.path.join(_HERE, "..", "..", "temp", "MatAnyone2"))
_MURL = "https://github.com/pq-yang/MatAnyone2/releases/download/v1.0.0/matanyone2.pth"

_MODEL = None
_DEVICE = None


def merge_bidir(fwd, bwd):
    """Position-weighted forward/backward alpha merge (== bidir_merge.py). fwd/bwd are equal-length
    lists of HxW float01 arrays in the SAME frame order (backward already un-reversed). Forward
    strong early, backward strong late: w = 1 - t/max(1, N-1). Returns a list of HxW float32."""
    n = len(fwd)
    out = []
    for t in range(n):
        w = 1.0 - t / max(1, n - 1)
        out.append(np.clip(w * fwd[t] + (1.0 - w) * bwd[t], 0.0, 1.0).astype(np.float32))
    return out
```

- [ ] **Step 4: Run the unit test to verify it passes**

Run: `python -m pytest tests/test_matte_matanyone.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Add the model loader + ported propagation + bidir orchestration**

Append to `tools/asset_matte/matte_matanyone.py`:

```python
def _model():
    """Load MatAnyone2 once (module singleton). Returns (model, device)."""
    global _MODEL, _DEVICE
    if _MODEL is None:
        if _MA2 not in sys.path:
            sys.path.insert(0, _MA2)
        from matanyone2.utils.download_util import load_file_from_url
        from matanyone2.utils.get_default_model import get_matanyone2_model
        from matanyone2.utils.device import get_default_device
        _DEVICE = get_default_device()
        ckpt = load_file_from_url(_MURL, os.path.join(_MA2, "pretrained_models"))
        _MODEL = get_matanyone2_model(ckpt, _DEVICE)
    return _MODEL, _DEVICE


def _propagate(frames_bgr, mask_u8, n_warmup=10, r_erode=10, r_dilate=10):
    """One MatAnyone2 propagation over frames_bgr (list of HxWx3 BGR uint8), anchored by mask_u8
    (HxW uint8 0/255 for the FIRST frame). Returns a list of HxW float01 alpha, one per input
    frame. Faithful port of inference_matanyone2.main: warmup repeats frame 0; mask dilate+erode;
    the ti==0 / ti<=warmup / else step schedule; warmup frames dropped from the output."""
    if _MA2 not in sys.path:
        sys.path.insert(0, _MA2)
    import cv2
    import torch
    from matanyone2.utils.inference_utils import gen_dilate, gen_erosion
    from matanyone2.utils.device import safe_autocast_decorator
    from matanyone2.inference.inference_core import InferenceCore

    model, device = _model()

    @torch.inference_mode()
    @safe_autocast_decorator()
    def _run():
        processor = InferenceCore(model, cfg=model.cfg)          # fresh memory per call
        rgb = [cv2.cvtColor(f, cv2.COLOR_BGR2RGB) for f in frames_bgr]
        vt = torch.from_numpy(np.stack(rgb)).permute(0, 3, 1, 2).float()   # N,C,H,W
        warm = vt[0:1].repeat(int(n_warmup), 1, 1, 1)            # repeat first frame for warmup
        vt = torch.cat([warm, vt], dim=0)
        length = vt.shape[0]

        m = mask_u8.copy()
        if r_dilate > 0:
            m = gen_dilate(m, r_dilate, r_dilate)
        if r_erode > 0:
            m = gen_erosion(m, r_erode, r_erode)
        m = torch.from_numpy(m).float().to(device)

        out = []
        for ti in range(length):
            image = (vt[ti] / 255.0).float().to(device)
            if ti == 0:
                processor.step(image, m, objects=[1])            # encode given mask
                op = processor.step(image, first_frame_pred=True)
            elif ti <= n_warmup:
                op = processor.step(image, first_frame_pred=True)
            else:
                op = processor.step(image)
            alpha = processor.output_prob_to_mask(op)            # HxW float01 tensor
            if ti > n_warmup - 1:                                # drop warmup frames
                out.append(alpha.detach().cpu().numpy().astype(np.float32))
        return out

    return _run()


def matte_segment(frames_bgr, first_mask_u8, last_mask_u8, warmup=10, erode=10, dilate=10):
    """Bidirectional matte for one segment. frames_bgr: predark input frames (list of HxWx3 BGR
    uint8, in order). first_mask_u8 / last_mask_u8: birefnet BINARY masks (0/255) of the first /
    last frame. Returns a list of HxW float01 alpha (position-weighted fwd/bwd merge). In-memory
    equivalent of bidir_prep.py (reverse frames + last-frame mask) + bidir_merge.py."""
    fwd = _propagate(frames_bgr, first_mask_u8, warmup, erode, dilate)
    bwd_rev = _propagate(list(reversed(frames_bgr)), last_mask_u8, warmup, erode, dilate)
    bwd = list(reversed(bwd_rev))                                # un-reverse to frame order
    return merge_bidir(fwd, bwd)
```

- [ ] **Step 6: Re-run the unit test (import still light, must still pass)**

Run: `python -m pytest tests/test_matte_matanyone.py -v`
Expected: PASS (3 passed) — confirms the heavy imports stayed lazy (build python has no torch).

- [ ] **Step 7: GPU validation — one segment end-to-end in the unified venv**

Create a throwaway check `temp/_val_matseg.py`:

```python
import os, sys, glob
import cv2, numpy as np
sys.path.insert(0, "tools/asset_matte")
import matte_blankplate as mb
import matte_matanyone as mm
import extract_loop as el

CLIP = "D:/kartoff/captures_sdr/en_uk/clips/mario__base__standard_kart.mkv"
segbase = "temp/_val_seg"
counts = el.extract_segments(CLIP, segbase, "k")
paths = sorted(glob.glob(os.path.join(segbase, "k__idle", "*.png")))[:40]
pres = [mb._kart_predark(cv2.imread(p), mb._kart_text_mask(
    np.median(np.stack([cv2.imread(q).astype(np.float32) for q in paths[::3]]), axis=0))) for p in paths]
first = (mb._birefnet(pres[0])[0] > 0.5).astype(np.uint8) * 255
last = (mb._birefnet(pres[-1])[0] > 0.5).astype(np.uint8) * 255
alphas = mm.matte_segment(pres, first, last)
print("frames", len(alphas), "alpha0 range", float(alphas[0].min()), float(alphas[0].max()),
      "coverage", float((alphas[len(alphas)//2] > 0.5).mean()), flush=True)
assert len(alphas) == len(pres)
assert alphas[0].shape == pres[0].shape[:2]
assert (alphas[len(alphas)//2] > 0.5).mean() > 0.02      # subject present, not empty
print("MATSEG OK", flush=True)
```

Run:
```bash
temp/asset-venv-matte/Scripts/python.exe temp/_val_matseg.py
```
Expected: prints frame count == input, alpha in [0,1], non-empty coverage, then `MATSEG OK`.

- [ ] **Step 8: Commit**

```bash
git add tools/asset_matte/matte_matanyone.py tests/test_matte_matanyone.py
git commit -m "asset-matte: MatAnyone2 bidirectional matte engine (ported f267585a prototypes)"
```

---

## Task 3: Wire `MATTE_ENGINE` into `matte_blankplate` + repoint console venv

Routes `matte_loopframes` through MatAnyone2 by default, keeps the per-frame birefnet path behind `MATTE_ENGINE=birefnet`, and points the console's Process button at the unified venv.

**Files:**
- Modify: `tools/asset_matte/matte_blankplate.py` (extract helpers + engine switch in `matte_loopframes`, ~lines 237-289)
- Modify: `tools/sweep_console/supervisor.py:34`
- Modify: `tests/test_console_supervisor.py`

**Interfaces:**
- Consumes: `matte_matanyone.matte_segment` (Task 2), existing `_birefnet` / `_kart_predark` / `_kart_text_mask` / `pre_darken` / `_checker_rgba`.
- Produces: `matte_loopframes(...)` with unchanged signature/outputs; module-level `MATTE_ENGINE` (str). `Supervisor.gpu_py` pointing at `temp/asset-venv-matte`.

- [ ] **Step 1: Add the engine constant and predark/writer helpers**

In `tools/asset_matte/matte_blankplate.py`, after the `BIREFNET_MODEL` definition (~line 80) add:

```python
# Matte engine: MatAnyone2 video matting (default, kills per-frame flicker) vs the legacy per-frame
# birefnet path. Set MATTE_ENGINE=birefnet to A/B or roll back. matte_matanyone is imported lazily
# (only under the matanyone branch) so the birefnet path still runs in a torch-less venv.
MATTE_ENGINE = os.environ.get("MATTE_ENGINE", "matanyone")
```

Then, just above `def matte_loopframes(` (~line 237), add the two extracted helpers:

```python
def _build_predark_frames(paths, kart, apply_predark):
    """Predark (or raw, for a plate-dropped flourish) BGR uint8 frame per path. Kart text mask is
    computed once from the segment median (== the old inline path)."""
    text = None
    if kart and apply_predark:
        sample = [cv2.imread(p).astype(np.float32) for p in paths[::3]]
        text = _kart_text_mask(np.median(np.stack(sample), axis=0))
    out = []
    for p in paths:
        raw = cv2.imread(p)
        if not apply_predark:
            out.append(raw)                                  # flourish: plate dropped -> raw
        elif kart:
            out.append(_kart_predark(raw, text))
        else:
            out.append(pd.pre_darken(raw, _t_char, _C_char, _A_char, _MASK_char))
    return out


def _write_chip(pairs, name, out_base):
    """Write RGBA frames + _loop.webp + _checker.webp from (bgr_uint8, alpha_float01) pairs. Shared
    by both engines. Returns the frame count."""
    fdir = os.path.join(out_base, f"{name}_frames")
    os.makedirs(fdir, exist_ok=True)
    rgba_frames = []
    for i, (bgr, alpha) in enumerate(pairs):
        rgb = cv2.cvtColor(np.asarray(bgr).astype(np.uint8), cv2.COLOR_BGR2RGB)
        rgba = np.dstack([rgb, (np.clip(alpha, 0, 1) * 255).astype(np.uint8)])
        cv2.imwrite(os.path.join(fdir, f"{i:03d}.png"), cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA))
        rgba_frames.append(Image.fromarray(rgba, "RGBA"))
        if (i + 1) % 15 == 0 or i + 1 == len(pairs):
            print(f"  matte {name} {i + 1}/{len(pairs)}", flush=True)
    W, H = rgba_frames[0].size
    rgba_frames[0].save(os.path.join(out_base, f"{name}_loop.webp"), save_all=True,
                        append_images=rgba_frames[1:], duration=DUR_MS, loop=0, lossless=True, disposal=2)
    chk = _checker_rgba(W, H)
    comp = [Image.alpha_composite(chk, f) for f in rgba_frames]
    comp[0].save(os.path.join(out_base, f"{name}_checker.webp"), save_all=True,
                 append_images=comp[1:], duration=DUR_MS, loop=0)
    return len(pairs)
```

- [ ] **Step 2: Replace the body of `matte_loopframes` with the engine switch**

In `tools/asset_matte/matte_blankplate.py`, replace the current `matte_loopframes` body (from `paths = sorted(...)` through the final `return len(paths)`, ~lines 245-289) — keep the existing docstring — with:

```python
    paths = sorted(glob.glob(os.path.join(framedir, "*.png")))
    if not paths:
        raise RuntimeError(f"no loop frames in {framedir!r}")
    kart = is_kart_combo(name) if is_kart is None else is_kart
    pres = _build_predark_frames(paths, kart, apply_predark)   # predark input frames (shared)

    if MATTE_ENGINE == "matanyone":
        import matte_matanyone as mm                           # lazy: torch only loads on this path
        first = (_birefnet(pres[0])[0] > 0.5).astype(np.uint8) * 255
        last = (_birefnet(pres[-1])[0] > 0.5).astype(np.uint8) * 255
        alphas = mm.matte_segment(pres, first, last)           # bidirectional, memory-propagated
        pairs = list(zip(pres, alphas))                        # RGB = predark input (no decontam)
    else:                                                      # legacy per-frame birefnet
        pairs = []
        for pre in pres:
            alpha, bgr = _birefnet(pre)
            pairs.append((bgr, alpha))
    return _write_chip(pairs, name, out_base)
```

(This drops the old commented-out `_repair_holes` blocks — hole-repair is OFF under full birefnet, per the spec; the code remains in git history.)

- [ ] **Step 3: GPU smoke — both engines produce a chip for one segment**

Create `temp/_val_engines.py`:

```python
import os, sys, glob
sys.path.insert(0, "tools/asset_matte")
import extract_loop as el

CLIP = "D:/kartoff/captures_sdr/en_uk/clips/mario__base__standard_kart.mkv"
segbase = "temp/_val_seg2"
el.extract_segments(CLIP, segbase, "k")
fd = os.path.join(segbase, "k__idle")

for engine in ("matanyone", "birefnet"):
    os.environ["MATTE_ENGINE"] = engine
    import importlib
    import matte_blankplate as mb
    importlib.reload(mb)                                       # pick up the env each pass
    out = f"temp/_val_out_{engine}"
    os.makedirs(out, exist_ok=True)
    n = mb.matte_loopframes(fd, f"k_{engine}", out, apply_predark=True, is_kart=True)
    got = len(glob.glob(os.path.join(out, f"k_{engine}_frames", "*.png")))
    assert got == n and os.path.exists(os.path.join(out, f"k_{engine}_loop.webp")), (engine, got, n)
    print(f"{engine}: {n} frames + webp OK", flush=True)
print("ENGINES OK", flush=True)
```

Run:
```bash
temp/asset-venv-matte/Scripts/python.exe temp/_val_engines.py
```
Expected: `matanyone: N frames + webp OK`, `birefnet: N frames + webp OK`, `ENGINES OK`.

- [ ] **Step 4: Repoint the console GPU python to the unified venv**

In `tools/sweep_console/supervisor.py`, change line 34:

```python
        # the matte batch driver needs the unified GPU venv (birefnet + MatAnyone2, CUDA), not the
        # console's build python
        self.gpu_py = os.path.join(repo_root, "temp", "asset-venv-matte", "Scripts", "python.exe")
```

- [ ] **Step 5: Update the supervisor test to assert the new venv path**

In `tests/test_console_supervisor.py`, add (uses the file's existing `ProcessSupervisor` import + positional-arg construction style, `def __init__(self, repo_root, on_line, py=...)`):

```python
def test_gpu_py_points_at_unified_matte_venv():
    sup = ProcessSupervisor("/repo", lambda *_: None, py="python")
    assert sup.gpu_py.replace("\\", "/").endswith("temp/asset-venv-matte/Scripts/python.exe")
```

- [ ] **Step 6: Run the build-python suite**

Run: `python -m pytest tests/test_console_supervisor.py tests/test_matte_matanyone.py tests/test_console_commands.py -v`
Expected: all PASS, including `test_gpu_py_points_at_unified_matte_venv`.

- [ ] **Step 7: Commit**

```bash
git add tools/asset_matte/matte_blankplate.py tools/sweep_console/supervisor.py tests/test_console_supervisor.py
git commit -m "asset-matte: MATTE_ENGINE switch (matanyone default) + console unified venv"
```

---

## Task 4: Reference reproduction + full-suite gate

Reproduces the accepted `f267585a` validation (mario kart + char, all segments) end-to-end through `process_all`, confirms the flicker win in the viewer, confirms the birefnet flag still works, and gates on the full build-python suite.

**Files:**
- No production code changes (validation only).
- Docs: append a "Validated" note to `docs/asset-matte-venv.md`.

**Interfaces:**
- Consumes: `process_all.py` (unchanged), Task 1-3 deliverables.

- [ ] **Step 1: Run the full pipeline on the two reference clips (default engine)**

```bash
temp/asset-venv-matte/Scripts/python.exe tools/asset_matte/process_all.py \
  --clips D:/kartoff/captures_sdr/en_uk/clips --out temp/_ref_chips --limit 0 \
  --manifest temp/_ref_chips/manifest.json --keep-loopframes
```
Then restrict to the two references by pre-seeding the manifest, OR (simpler) copy just `mario__base__standard_kart.mkv` + `mario__base.mkv` into a scratch `temp/_ref_clips/` and point `--clips` there. Expected: `PROCESSED mario__base__standard_kart ... {spawn:…, idle:…, flourish:…}` and `PROCESSED mario__base ... {idle:…, flourish:…}` (char has no spawn), `DONE processed=2`.

- [ ] **Step 2: Build the viewer and eyeball the flicker win**

```bash
python tools/asset_matte/make_viewer.py --matte temp/_ref_chips/matte --title "matanyone ref"
```
Open the emitted `temp/_ref_chips/matte/index.html`. Confirm: idle loop has **no** steering-wheel-rung / thin-part flicker across frames; spawn and flourish are clean; the char (`mario__base`) jump-flourish is intact. This is the acceptance check the user validated in `f267585a`.

- [ ] **Step 3: Confirm the birefnet flag still reproduces the old path**

```bash
MATTE_ENGINE=birefnet temp/asset-venv-matte/Scripts/python.exe tools/asset_matte/process_all.py \
  --clips temp/_ref_clips --out temp/_ref_chips_biref --manifest temp/_ref_chips_biref/manifest.json
```
Expected: completes with the same frame counts as Step 1; output visually matches today's per-frame birefnet chips (flicker present — that's the point of keeping it as the comparison baseline).

- [ ] **Step 4: Full build-python suite green**

Run: `python -m pytest tests/ -q`
Expected: all pass (the prior green count + the 3 new `test_matte_matanyone` tests + the new supervisor test), no import errors.

- [ ] **Step 5: Record validation + clean up scratch**

Append a dated "Validated end-to-end on mario__base__standard_kart + mario__base" line to `docs/asset-matte-venv.md`. Remove throwaway validation dirs/scripts:
```bash
rm -rf temp/_val_seg temp/_val_seg2 temp/_val_out_matanyone temp/_val_out_birefnet temp/_val_matseg.py temp/_val_engines.py temp/_ref_chips temp/_ref_chips_biref temp/_ref_clips
```

- [ ] **Step 6: Commit**

```bash
git add docs/asset-matte-venv.md
git commit -m "asset-matte: MatAnyone2 integration validated on reference clips"
```

---

## Self-Review

**Spec coverage:**
- Unified venv, single process, in-process call → Task 1 (venv) + Task 3 Step 2 (in-process `matte_segment` call). ✓
- Smoke-test gate, no worker fallback → Task 1 Steps 2-3 + the "resolution levers" note. ✓
- `matte_matanyone.py` bidirectional, first/last binary masks, stock params → Task 2. ✓
- Reuse f267585a prototypes → Task 2 docstrings/port + explicit mapping. ✓
- `MATTE_ENGINE` flag, birefnet path retained → Task 3 Steps 1-2. ✓
- Unchanged `process_all`/console/viewer signature+outputs; console venv repointed → Task 3 Steps 4-5, Task 4 Step 1 uses `process_all` unchanged. ✓
- Predark rules (spawn/idle ON, kart flourish OFF) → `_build_predark_frames` + `process_all`'s existing `apply_predark` arg (unchanged). ✓
- Testing: coexistence, reference reproduction, engine flag, suite green → Task 1 Step 3, Task 4 Steps 1-4. ✓
- Non-goals (no worker, no fuller mask, no seg change) → honored; no task adds them. ✓

**Placeholder scan:** No TBD/TODO; every code step shows full code; commands have expected output. The one judgement call (Task 3 Step 5 "match the existing constructor style") is flagged because the test file's exact constructor kwargs must be read at implementation time — the assertion itself is concrete.

**Type consistency:** `merge_bidir(fwd, bwd)` and `matte_segment(frames_bgr, first_mask_u8, last_mask_u8, warmup, erode, dilate)` are used with matching names/types in Task 3 (`mm.matte_segment(pres, first, last)`). `_build_predark_frames`/`_write_chip`/`_birefnet` signatures match their call sites. `pairs` is a list of `(bgr_uint8, alpha_float01)` in both engine branches and in `_write_chip`. ✓
