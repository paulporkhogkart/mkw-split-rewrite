# MatAnyone2 matte engine — integration design

**Date:** 2026-07-01
**Status:** Design (approved shape; pending spec review)
**Supersedes for the matte step:** per-frame birefnet (`matte_blankplate.matte_loopframes`)

## Problem

The committed chip-matte pipeline mattes every frame independently with birefnet. Per-frame
birefnet **flickers** on thin/moving features (a steering-wheel rung, worse because the kart
vibrates/putters). Every per-frame lever was tried and rejected (see memory
`matanyone-matting-engine`): general-lite→full, soft alpha, hrsod, flow-guided temporal median
(broke the flourish). The chosen fix is **MatAnyone2** — mask-guided *video* matting: birefnet
supplies a first-frame mask, MatAnyone2 memory-propagates it across the segment for a temporally
stable alpha. Validated end-to-end in prototypes (`temp/matanyone_probe`, session `f267585a`):
kills idle flicker, clean on spawn/flourish, ~11 fps (faster than per-frame birefnet ~1.7 fps),
and only ONE birefnet call per segment end.

This spec integrates that validated recipe into the production pipeline.

## Goals

- Replace the per-frame birefnet matte with MatAnyone2 as the default engine.
- Keep the old per-frame birefnet path reachable behind a flag (A/B + safety net).
- **Bidirectional** propagation (forward + reversed, position-weighted merge) to cover
  late-segment memory drift and protect the idle-loop seam.
- No changes required to `process_all.py`, the run-console, or `make_viewer` — same function
  signature and same output artifacts.

## Non-goals

- No worker/subprocess/IPC architecture. The two engines run **in one process, one venv**.
- No cross-venv piping fallback. If the unified venv fails the coexistence smoke test, we fix the
  venv — we do not build a second architecture.
- No "fuller"/soft anchor mask (the `gen_fuller_mask.py` experiment). Low-contrast subject≈bg
  parts are unrecoverable by any matte (birefnet soft ≈ its binary) → AI-inpaint territory,
  deferred and out of scope.
- No new segmentation work — `extract_loop.extract_segments` is unchanged.

## Architecture

### Venv & process model — unified, single process

Today: birefnet/predark run in `temp/asset-venv-gpu` (onnxruntime-gpu 1.22 + rembg, cuDNN 9.x /
CUDA 12.x); MatAnyone2 runs in `temp/matanyone-venv` (torch 2.11.0+cu128, its own vendored cuDNN
9.x). Both are **Python 3.12.10** and both target CUDA 12.x / cuDNN 9.x — version-compatible.

Plan: build **one fresh unified venv** and run everything in-process.

- Venv: `temp/asset-venv-matte` (py3.12) = onnxruntime-gpu + rembg + torch cu128 + MatAnyone2
  (`pip install --no-deps -e temp/MatAnyone2` — its pyproject drags cchardet/PySide6/netifaces
  that break on new py) + MatAnyone2's minimal deps (numpy opencv hydra-core omegaconf einops
  tqdm imageio imageio-ffmpeg safetensors huggingface_hub easydict requests av) + the existing
  matte deps (pymatting is optional/off).
- The two existing venvs are left untouched (fallback for the operator, not a code path).
- `matte_loopframes` calls MatAnyone2 **in-process** via `import matanyone2` + `InferenceCore` —
  no subprocess, no pipe, no model reload (the model loads once per `process_all` process, which
  is already long-lived).

**Smoke-test gate (implementation step 1, blocking):** a script that, in ONE process, runs a
birefnet matte AND a MatAnyone2 propagation and asserts both hit CUDA and produce correct output.

- Pass → proceed with the in-process design above.
- Fail (cuDNN/CUDA DLL conflict between onnxruntime's `nvidia-cudnn-cu12` and torch's vendored
  copy) → **stop and resolve the venv** (pin/align cuDNN, adjust DLL load order via
  `os.add_dll_directory`, or choose a compatible onnxruntime/torch pairing). No worker fallback.

### Matte flow (per segment)

`extract_segments` already cuts each clip into spawn / idle / flourish. Per segment:

1. **Predark input frames** — exactly as today: kart → `_kart_predark` (blank-plate un-darken +
   per-clip text mask + interior TELEA inpaint, locked params KEY_THR=120/CSUB=0.5/TFLOOR=0.01/
   FILL_K=51); char → `pre_darken`. `apply_predark` is ON for spawn+idle, **OFF for the kart
   flourish** (plate drops) and ON for the char flourish (in-place bounce keeps the plate) —
   unchanged from `process_all`'s current `apply_predark = not (kart and seg == "flourish")`.
2. **First-frame mask** — birefnet on predark frame 0, binary `alpha > 0.5`.
3. **Last-frame mask** — birefnet on predark frame N−1, binary `alpha > 0.5`.
4. **Forward pass** — MatAnyone2 `InferenceCore` propagates the first-frame mask over the predark
   frames in order → `alpha_fwd[0..N-1]`.
5. **Backward pass** — MatAnyone2 propagates the last-frame mask over the **reversed** predark
   frames → reverse the result back → `alpha_bwd[0..N-1]`.
6. **Merge** — `w = 1 − t/max(1, N−1)`; `alpha[t] = clip(w·alpha_fwd[t] + (1−w)·alpha_bwd[t], 0,1)`.
   Forward strong early, backward strong late. Applied to **all three segments** (covers flourish
   end-drift and the idle-loop seam).
7. **Compose** — RGBA = predark RGB (the input frames) + merged alpha. Emit the same artifacts as
   today: `<name>_frames/NNN.png` (RGBA), `<name>_loop.webp`, `<name>_checker.webp`.

Stock MatAnyone2 params (the settings the accepted prototype used): `n_warmup=10`,
`r_erode=10`, `r_dilate=10`. Each of the forward and backward passes is one independent
`InferenceCore` propagation with default memory config; "bidirectional" is achieved by the two
passes + merge, not by any special mode.

### Module layout

- **New: `tools/asset_matte/matte_matanyone.py`** — the MatAnyone2 engine. Loads the model once
  (module-level lazy singleton, like `matte_blankplate._session`), exposes
  `matte_segment(predark_frames, first_mask, last_mask) -> [alpha...]` (bidirectional merge) and a
  compose helper. Faithful port of `prep_matanyone_seg.py` + `bidir_prep.py` + `bidir_merge.py`.
  Wraps MatAnyone2's `InferenceCore` directly (reproducing `inference_matanyone2.py`'s frame-read
  / warmup / erode-dilate / propagate, but load-model-once and no disk round-trip for the CLI).
- **Changed: `tools/asset_matte/matte_blankplate.py`** — `matte_loopframes` gains an engine
  switch. Predark + first/last-frame birefnet masks are computed here (birefnet already lives
  here); the frames+masks are handed to `matte_matanyone.matte_segment`; compose + webp writing
  stay here (shared with the birefnet path). Engine selected by env `MATTE_ENGINE`
  (`matanyone` default | `birefnet` = today's exact per-frame path). The per-frame birefnet code
  is retained verbatim under the flag.
- **Unchanged:** `process_all.py`, `extract_loop.py`, `tools/sweep_console/*` (Process button →
  `process_cmd` → `process_all.py`), `make_viewer.py`, `supervisor.build_viewer`. Same
  `matte_loopframes(framedir, name, out_base, clip=, apply_predark=, is_kart=)` signature and same
  outputs.

### Data flow

```
process_all (asset-venv-matte, one long-lived process)
  └ per clip: extract_segments → spawn/idle/flourish spans
       └ per segment: matte_loopframes(apply_predark=…)
            MATTE_ENGINE=matanyone (default):
              predark frames  ─┐
              birefnet 1st mask ─┤→ matte_matanyone.matte_segment (fwd+bwd+merge, in-process)
              birefnet last mask ┘        └ alpha[0..N-1]
              compose RGBA (predark RGB + alpha) → NNN.png + _loop.webp + _checker.webp
            MATTE_ENGINE=birefnet:
              today's per-frame birefnet path (unchanged)
```

## Error handling

- Smoke-test gate is blocking — the integration does not ship until both engines coexist.
- MatAnyone2 model auto-downloads (`matanyone2.pth`, ~135 MB) on first use, as in the prototypes.
- A segment with too few frames for a stable memory (e.g. very short spawn) still runs; warmup
  repeats frame 0, so N≥1 is safe. Merge handles N=1 (`w=1`, forward only).
- `process_all`'s existing per-clip try/except + manifest (`status: error`) + stop-file-between-
  clips behavior is unchanged, so a failure in one clip is isolated and resumable.

## Testing / validation

1. **Coexistence smoke test** (gate) — birefnet + MatAnyone2 in one process, both on CUDA,
   correct output.
2. **Reference reproduction** — re-matte `mario__base__standard_kart` (all 3 segments) and
   `mario__base` through `MATTE_ENGINE=matanyone`; compare in `make_viewer` against the current
   birefnet output for the flicker win (reproduces the `f267585a` validation the user accepted).
3. **Engine flag** — `MATTE_ENGINE=birefnet` reproduces today's per-frame artifacts unchanged.
4. **Build-python suite** stays green — the new module is GPU-venv-only and not imported by build
   python; `matte_blankplate`'s build-python-visible surface (signatures) is unchanged.

## Risks

- **cuDNN/CUDA DLL coexistence** in one process — the one real unknown; mitigated by the blocking
  smoke-test gate. Resolution levers if it fails: align cuDNN versions, control DLL load order,
  or pick a compatible onnxruntime/torch pairing. (No worker fallback by decision.)
- **All tooling lives in `temp/`** (venvs + `temp/MatAnyone2`), gitignored — consistent with the
  current pipeline but not reproducible from the repo alone. The unified-venv build steps are
  documented here so the environment can be rebuilt.
- **2× MatAnyone2 compute** from the bidirectional pass — still well above per-frame birefnet
  throughput; acceptable.

## Out of scope / deferred

- AI inpaint (LaMa/SD) for low-contrast subject≈bg parts and the big spawn-in bumper notch.
- Any change to segmentation, loop-period, or the blank-plate/predark math.
- Committing the venv/repo setup into the repository (remains a `temp/` bring-up).
