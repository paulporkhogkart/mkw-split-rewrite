# Nameplate Removal via Pre-Matte Un-darkening — Design

**Date:** 2026-06-28
**Status:** Approved approach (unified pre-matte), pending spec review
**Branch:** asset-clip-sweep
**Supersedes:** `2026-06-28-nameplate-difference-keying-design.md` (post-matte keying — abandoned in favour of pre-matte; see "Why pre-matte" below).
**Builds on:** `tools/asset_matte/` (`nametag_core.py`, committed `assets/` templates, `build_templates.py`); the validated difference method + un-darken transform (memory `nametag-mask-undark`).

## Problem

The select-screen "nametag" plate (a fixed, semi-transparent, tire-tread strip carrying the item's yellow name + a 1-UP badge) must be removed from the transparent cutouts. The current post-matte handling (`undark.py`) removes it with a **connectivity** rule (`drop_nameplate`): it only drops a matte component that lies almost entirely inside the plate footprint — a *detached* blob. The instant a character's silhouette **connects** to the plate (DK's hands; many characters), the plate is one component with the body, can't be dropped, and is only lightened. The plate stays.

## Why pre-matte (the chosen approach)

There are two operations on the plate: **(1) un-darken** — recover the true colour of pixels behind the semi-transparent plate; **(2) cut** — make the plate transparent where nothing is behind it. On karts, birefnet performs (2) for free (large salient subject ⇒ it discards the plate as non-salient UI). On connected characters it does not. The post-matte path tries to do (2) explicitly (drop/keying) and is fragile.

**Pre-matte does (1) in the raw frame and lets birefnet do (2) naturally.** If we **fully** un-darken the semi-transparent serration *before* matting:
- serration over empty background → recovered to *true background* → birefnet sees continuous background → **drops it**;
- serration over a character → recovered to the *character's* colour → birefnet sees character → **keeps it**;
- the yellow name text + 1-UP badge are opaque (unrecoverable) → left as UI → birefnet **drops them** as non-salient clutter.

The plate detaches by itself, connected or not. **Validated this session** (scratch): DK and koopa plates came out **completely gone**, bodies intact (`prematte_compare.png`); a rob_hog kart combo through full-recovery pre-matte was as clean as the plain matte, no fringe (`kart_prematte.png`).

**Full recovery is the key parameter.** The post-matte un-darken used `CSUB=0.69` (tuned only to *lighten* the kept overlap). To make plate-over-bg become *true* background — so birefnet excludes it — the additive plate floor must be removed completely: **`CSUB=1.0`**. The historical pre-matte kart failure (hot_rod dark fringe) is consistent with *partial* recovery (`CSUB<1`) leaving residual plate structure that birefnet then kept — i.e. a wrong-value artifact, not a pre-matte limitation. Full recovery removed it on rob_hog.

One unified pre-matte path replaces the entire post-matte plate-removal step for both characters and karts.

## Architecture & components

1. **`pre_darken(raw_bgr, t, C, mask, params) -> bgr`** (new, `tools/asset_matte/pre_darken.py`) — pure cv2/numpy. Un-darkens the **strip-only** serration of the raw frame and returns the modified BGR frame (no alpha). Strip-only = within the plate footprint (`mask > floor`), semi-transparent (`t ≥ T_OPAQUE`), and **excluding** the opaque UI we want birefnet to drop: the yellow name text (`HSV S > YELLOW_S`) and the bright badge (`HSV V > BRIGHT_V`). Recovery on the kept serration: `S = (O − CSUB·C) / clip(t, TFLOOR)` with `CSUB = 1.0`, blended in by the strip coverage. Text/badge pixels pass through unchanged.

2. **Pipeline** becomes `extract_loop → pre_darken → matte_loop` (replacing `extract_loop → matte_loop → undark`). `extract_loop` writes the raw loopframes `loopframes/<name>/`; `pre_darken` writes `loopframes/<name>_pre/`; `matte_loop` mattes the `_pre` set → `matte/<name>_pre_frames/` and the transparent loop. Raw loopframes are kept (the tuner re-darkens from them).

3. **Per-screen template** — character clips use the char template (`CHAR_ROI`, char `P/A/mask` → `t,C`), kart combos use the kart template (`PLATE_ROI`). Same `load_template(is_char)` already committed (returns `t, C, mask`; no `P` needed for pre-matte — `P` was only for the abandoned keying).

4. **`tune_prematte.py`** (new, `tools/asset_matte/`) — a cv2-trackbar tuner. Loads a fixed set of representative raw frames — **DK** (touching), **koopa**, **mario**, and a **kart combo** (incl. a kart dipping into the plate when available, e.g. B-Dasher) — and on every trackbar move re-runs `pre_darken` **then re-mattes those few frames** and shows the results over a checkerboard. Defaults are the **pre-matte** values (`CSUB=1.0`, full recovery) — explicitly not the post-matte `0.69` that caused the historical kart fringe. Prints the chosen params on quit. (Preview is "drag, wait ~1 s/frame" — re-matte is required because pre-matte feeds birefnet; acceptable since the default already looks clean.)

5. **Retired vs reused:** the post-matte plate-removal functions `drop_nameplate` + `undark_rgba` (and `tests/test_undark.py`) are **retired** — superseded by pre-matte. `load_template(is_char)` (loads the per-screen `t,C,mask`) moves to `pre_darken.py`. `undark.py`'s `_reencode` is **not** carried over — `matte_loop.py` already emits `_loop.webp`/`_checker.webp`/`_apng.png` from the matte, so the pre-matte pipeline gets the final encoding for free. `nametag_core.py`, `build_templates.py`, and the committed `assets/` are **kept and reused** unchanged.

6. **Optional fallback (build only if the eye-test needs it):** a raw-alpha-gated refine (intersect the pre-matte alpha with the plain-matte alpha dilated a few px) to kill any stray floor blob a pre-darkened frame might introduce. Not built pre-emptively (YAGNI); the scratch validation showed none on DK/koopa/rob_hog.

### Parameters (the tuner's knobs, pre-matte defaults)

- `CSUB = 1.0` — additive-floor removal; **1.0 = full recovery** (the fix). Lower only if over-recovery brightens the kept overlap wrong.
- `TFLOOR = 0.05` — lower clamp on transmission `t` (unchanged).
- `YELLOW_S = 60` — saturation above which a plate pixel is treated as name text → left as UI (not un-darkened).
- `BRIGHT_V = 200` — value above which a plate pixel is treated as the badge → left as UI.
- (No `STRENGTH`/`ALPHA_GAIN` — those were post-matte blend knobs; pre-matte writes the recovered serration directly.)

## Data flow

```
extract_loop(clip)                         -> loopframes/<name>/NNN.png        (raw O, prod crop 988×1080)
t,C,mask = load_template(is_char)          (committed assets, per screen)
pre_darken(O, t, C, mask, PARAMS)          -> loopframes/<name>_pre/NNN.png    (serration fully recovered)
matte_loop(<name>_pre)                     -> matte/<name>_pre_frames/NNN.png + _loop.webp / _checker.webp / _apng.png
```

## Error handling / edge cases

- **Deep overlap recovery quality:** where a character genuinely sits behind the serration, full recovery must reproduce its colour faithfully. DK (shallow) is clean; confirm a deeper-overlap case in the eye-test. If over-/under-recovered, `CSUB` is the dial.
- **Kart dipping into the plate (B-Dasher-class):** the decisive kart case for the no-fringe claim — confirm in the tuner with the proper kart framing; the refine fallback exists if a fringe appears.
- **Prod-crop vs kart plate footprint:** `PROD_CROP_4K=(2100,36,3720,1806)` was sized for `CHAR_ROI`; the kart `PLATE_ROI` (x2=3738, y2=1828) is clipped ~18–22 px at the right/bottom (the badge edge). Verify in the build; extend the crop to contain both ROIs if the clipped badge edge survives the matte (the badge is opaque UI birefnet usually drops, so this may be moot).
- **Yellow character parts in the plate band** (e.g. a yellow foot dipping in) would be left un-recovered (treated as "text") — rare; surfaces in the eye-test, tunable via `YELLOW_S`.
- **Background drift:** the select-screen background is static/blurred so full recovery lands on the right background; HDR/animation drift would show as residual the matte keeps — visible in the tuner.

## Testing

- **Synthetic unit tests** (`tests/test_pre_darken.py`, build python): planted plate-band frames — (a) serration over a known background, full recovery ⇒ output ≈ the background; (b) serration over a known "character" colour ⇒ output ≈ that colour; (c) a yellow text patch and a bright badge patch ⇒ passed through unchanged (left as UI). Assert the recovered BGR within tolerance.
- **User trackbar eye-test** (the real acceptance, as with every prior asset step): tune `CSUB`/`YELLOW_S`/`BRIGHT_V` in `tune_prematte.py` on DK/koopa/mario/kart until the plate is gone and bodies are clean across all; bake the params; re-run the idle loops; eyeball the animated `_pre` loops. Karts confirmed here.

## Scope

In: the unified pre-matte plate removal for **characters and karts** (idle path), the `pre_darken` step + pipeline rewire, the tuner, retiring the post-matte plate removal. Out: the kart-combo **capture** path (separate effort — spawn-in-window exclusion + wheel-robust period); any new matte model; re-deriving the un-darken transform (reused).

## Non-goals

- Post-matte keying / `drop_nameplate` (abandoned — pre-matte is cleaner and connectivity-free).
- Instant (no-re-matte) slider preview — impossible for pre-matte; accepted, since the default is already clean.
- Per-language text handling (leaving the yellow font as UI is language-independent).
