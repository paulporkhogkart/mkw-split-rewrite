# Asset matte pipeline — transparent looping character cutouts

Self-captured MKW footage → seamless, transparent, **looping** cutouts (animated WebP /
APNG) for thekartoff.com (animated player cards, roster/combo showcase). This directory
holds the **matte** half (the final "v1" pipeline). Recording + loop-measurement tools
live in `mkw_tracker/tools/` (build python). Background/context: memory `chip-asset-extraction`
and `chip-video-animation`.

> **Status (2026-06-26):** the per-character **idle** path is fully working end-to-end and
> v1 is FINAL (see *Matte findings*). NOT yet built: the **kart-sweep combo** capture
> automation and **flourishes** — see *Open / next work*.

---

> **Running the matte batch on two machines:** see `docs/two-machine-sweep.md` (shared claim
> queue + ship-and-delete; either box Start/Stops independently).

## The pipeline

```
record (4K60)  ──►  measure loop  ──►  extract 1 seamless loop  ──►  matte  ──►  transparent loop
record_clips        loop_probe          extract_loop.py              matte_loop.py
(build py)          (build py)          (build py, needs repo path)  (asset-venv[-gpu])
```

**1. Record** — `python -m mkw_tracker.tools.record_clips`
- 4K60 HEVC of the hovered character on **Time Trials → character-select** (the big hero
  render). Live preview via a tee'd ffmpeg (one card consumer). Type `mario_idle 40` → records 40 s.
- Capture **HDR OFF** (SDR). **Win11 camera-sharing must be OFF** (exclusive DirectShow;
  the frame server otherwise steals access → `Could not set video options`).
- NVENC preset is **p5**, not p6/p7: p6/p7 can't sustain 4K60 (~0.8×) → dropped frames; p5 is ~2.3× real-time, visually identical for source footage.
- The Elgato 4K X delivers **MJPEG** at 4K60; the bottleneck was the encoder preset, not the decode.

**2. Measure the idle loop** — `python -m mkw_tracker.tools.loop_probe temp/clips/*.mkv`
- Period via temporal-residual self-similarity autocorrelation. **Loops vary by character.**
  Measured so far: Mario / Luigi **1.33 s (80f)**, Bowser **1.67 s (100f)**, Wario·Wicked-Wasp **1.33 s**.
- → idle dwell is **per-character**, not one global number. (Finish measuring the roster.)

**3. Extract one seamless loop** — `PYTHONPATH=. python tools/asset_matte/extract_loop.py <scratch_dir> temp/clips/mario_idle.mkv [...]`
- Finds the most seamless N-frame window (matches start vs start+P in position *and* velocity),
  crops the hero ROI `(1075,30,1800,845)@1080p` (scaled to the clip res), downscales to 860 px tall,
  writes `<scratch_dir>/loopframes/<name>/NNN.png`. Imports `mkw_tracker.tools.loop_probe`, so run with `PYTHONPATH=<repo root>`.

**4. Matte** — `<asset-venv[-gpu]>/Scripts/python.exe tools/asset_matte/matte_loop.py <scratch_dir> birefnet-general-lite - <name> [...]`
- Args: `<base> <model> <suffix> <name...>` (suffix `-` = none; use e.g. `_full` to compare models).
- Per-frame **birefnet** (rembg) + **pymatting** foreground decontam → transparent loop:
  `<name>_loop.webp` (alpha), `_checker.webp` (over checkerboard), `_apng.png`, `_sheet.png`,
  plus the raw RGBA `<name>_frames/`.
- **GPU ≈ 0.4 s/frame** on the RTX 5080 (`asset-venv-gpu`); CPU ≈ 6 s/frame (`asset-venv`).
  Same script runs on both — `_setup_cuda()` is a no-op without the nvidia packages.

Output formats: animated **WebP** + **APNG** carry alpha and play in browsers. **Transparent
WebM is NOT possible** in this ffmpeg build (libvpx silently strips VP8/VP9 alpha).

---

## Venvs (all gitignored under `temp/`)

| venv | python | for | key packages |
|---|---|---|---|
| `temp/asset-venv` | 3.14 | CPU matte (original) | rembg 2.0.76, onnxruntime 1.27 (CPU), pymatting |
| `temp/asset-venv-gpu` | 3.12 | **GPU matte (~15×)** | onnxruntime-gpu 1.22 + `nvidia-*-cu12` wheels + rembg (`--no-deps`) + pymatting + opencv |
| `temp/sam2-venv` | 3.12 | SAM2/RVM experiments | torch 2.11+cu128, sam2 |

**GPU gotchas (hard-won — see memory):**
- Default PyPI `onnxruntime` / `torch` are **CPU-only**; CUDA `torch` needs `--index-url https://download.pytorch.org/whl/cu128`.
- py3.14 + onnxruntime 1.27 wants **CUDA 13** runtime → **no Windows wheels** → use **py3.12 + onnxruntime-gpu 1.22** (CUDA 12) + `nvidia-*-cu12` wheels.
- Install rembg with `pip install --no-deps rembg` so it doesn't pull CPU `onnxruntime` and clobber the GPU build (they share the `onnxruntime/` dir).
- cuDNN lazy-loads engine DLLs → must `os.add_dll_directory()` every `site-packages/nvidia/*/bin` + `onnxruntime.preload_dlls()` *before* the session (done in `matte_loop._setup_cuda()`), else Conv fails.
- `torch.hub.load(..., trust_repo=True)` (non-interactive).

---

## Matte findings — v1 is FINAL

The residual artifact (the "eye/armpit/antenna flicker") = **sustained (~10-frame) pose-dependent
matte dropouts** = **open boundary notches**: birefnet nibbles into the silhouette next to
self-occlusion creases / thin structures (antennae, armpit, hand-on-belly) and holds the wrong
edge for a stretch of the loop, mostly the same way each cycle. Not random flicker, not low-contrast.

**9 approaches tested; none beats plain per-frame birefnet:**

| approach | result |
|---|---|
| clean (static) background | no change (helped general edges only) |
| temporal median (naive) | edge ghosting → rejected |
| optical-flow-guided temporal median | nothing (dropout outlasts the ±3 window) |
| full `birefnet-general` model | same calls |
| temporal interior consolidation | fixes dropouts but **edge ghosting** → rejected |
| SAM2 (video object segmentation) | hard jagged edges **+ worse** dropouts |
| RVM (robust video matting) | **empty matte on non-humans** (bee, Bowser); fine on humanoids |
| cross-cycle best-of-cycles | mostly deterministic across cycles → 11→10 |
| per-frame hole-fill | dropouts are **open notches, not enclosed** → 11→11 (no-op) |

**Conclusion:** v1 = `extract_loop.py` → `matte_loop.py` (birefnet-general-lite + decontam,
per-frame). The residual is minor and inherent. Only **manual touch-up** or a **video-roto app**
(DaVinci Resolve **Magic Mask** / AE **Roto Brush 2**) goes further — worth it only for individual
*hero* assets, not the ~200-unit roster.

Latest sample outputs: `temp/asset_proof/` (`*_checker.webp` to view; `*_full_*` = full-model comparison).

---

## Open / next work (the actual remaining value)

- **Per-character idle durations** — finish measuring the roster with `loop_probe`; encode the
  per-character dwell into the capture spec.
- **Kart-sweep combo capture (automation)** — for each char/costume, one continuous 4K60 clip
  cycling all 40 karts (preserves the kart→kart transition animation). Driven by `tools/autotemplate`
  (nxbt → Switch 2, VERIFIED). **Grounding:** the tee'd-ffmpeg frame feed lets the tracker read kart
  names *while* recording, to catch a no-reg / double-reg mid-sweep (a no-reg + later double-reg nets
  to the right end kart but corrupts the middle, so an end-only check is insufficient).
- **Flourishes** (A-press selection animation) — in scope, spec deferred.
- **Write the capture spec**, then do the full-roster run.

---

## Site pack pipeline

Encode matte'd chip animations into WebP sprite-sheet grids + sil masks, packaged as GitHub Release
assets and deployed to the Pi. See `docs/superpowers/specs/2026-07-18-chip-site-pack-design.md`.

**1. A/B eye test**

```
python tools/asset_matte/build_ab_lab.py --src D:\kartoff\asset_chips --out D:\kartoff\asset_chips\ab_lab
```
- Encodes a handful of representative combos (big char, small char, busy kart, standalone char;
  override with `--combos NAME...`) across scale/fps/quality/alpha-bit variations. Outputs a lab
  HTML page rendering real card-size chips against card-dark background, ink-ring CSS applied,
  for visual comparison. Paul's eye test locks the recipe (scale/fps/quality/alpha-bit knobs);
  nothing batch-encodes until sign-off.

**2. Batch encode**

```
python tools/asset_matte/build_site_pack.py --src D:\kartoff\asset_chips --out D:\kartoff\asset_chips\site_pack --scale <scale> --fps <fps> --quality <q> --alpha-bits <bits> --workers 12
```
- `--scale`/`--fps`/`--quality`/`--alpha-bits` are required and come from whatever the A/B eye
  test locks — no default recipe is baked into the tool. `--workers` defaults to CPU count − 2.
  `--only NAME...` limits the run to specific combos (sampling); `--force` ignores the resume
  book-keeping and re-encodes everything.
- Multiprocessing over all 6,273 char×costume×kart combos (+ 153 standalone). Per combo: decode
  pristine 1024×1080 PNG frames from `D:\kartoff\asset_chips\matte\`, downscale + premultiply-safe
  alpha quantize, tile frames into a WebP sprite-sheet grid (near-square, max side ≤4096px),
  generate sil masks (12-point radial jagged tearout, 4 sampled frames). Estimated wall-clock ~1.5–2h
  on the 9800X3D (decode+resize dominates). Resume via `<out>/book.json`; skip-if-done.
- Output: `D:\kartoff\asset_chips\site_pack/` with `chips/` sheets/masks + `manifest.json`.

**3. Pack shards**

```
python tools/asset_matte/pack_shards.py --pack D:\kartoff\asset_chips\site_pack --tag chips-v1
```
- Splits the pack into per-character shards (~50 shards, ~50MB each; under the 2GB GitHub asset
  limit). Optional `--base-url` overrides the default GitHub Releases download URL. Writes
  `<pack>/release/chips-<char>.tar` per character plus `chips-manifest.json`, and folds every
  shard's sha256 inline into `<pack>/release/chips.lock` (no separate checksums file) — that
  lock is what gets committed to `web/chips.lock`.

**4. Release & deploy** — see the Task 11 release runbook
- Upload shards + manifest to a dedicated GitHub Release (tag `chips-vN`, NOT a deploy tag). Commit
  `web/chips.lock` to git. `deploy/update.sh` pulls shards on the Pi, verifies sha256, unpacks into
  `$DATA/chips/`. `web/serve.mjs` serves the pack at `/chips/anim/` with manifest injection +
  immutable cache headers.
