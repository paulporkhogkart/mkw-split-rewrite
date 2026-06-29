# Kart-Chip Matte Pipeline (blank-plate un-darken + flourish bumper restore) — Design

**Date:** 2026-06-29
**Status:** Stages 1–4 + matte = interim approach (validated, in use). **Stage 5 (flourish bumper fill) is SHELVED** — see the "Stage 5 status" note below. The big text-occlusions (idle and spawn-in) await a generative AI-inpaint design.

> **Stage 5 status (2026-06-29, later):** Live testing shelved the single-flourish-donor bumper fill. A static flourish frame aligned by translation matches only ONE dip frame (d010↔f70); the spawn-in bounce **pitches** the body, so the bumper angle changes through the dip and no single flourish pose fits f71+. This is a fundamental ceiling, not tuning. **Interim:** run stages 1–4 + matte with the flourish fill OFF (`tune_blankplate.py` defaults the toggle off) — text removed, small idle occlusions filled by the interior TELEA inpaint, the large spawn-in bumper notch left for later. **Future:** a generative AI inpaint (LaMa / SD-inpaint) to reconstruct large text-occluded regions, needed for BOTH the idle loop (parts of the kart/character can cross the text) AND the spawn-in one-shot. The chip needs both an idle loop and a spawn-in one-shot asset.
**Branch:** asset-clip-sweep
**Builds on:** `2026-06-28-nameplate-prematte-design.md` (pre-matte paint-then-stamp; pre_darken/matte pipeline) and memory `nametag-mask-undark`. This spec is the **kart-combo path** the prematte spec explicitly deferred ("the decisive kart case: a kart dipping into the plate").
**Calibrator:** `tools/asset_matte/tune_blankplate.py` (browser tuner, GPU venv) — the live reference implementation of everything below.

## Problem

We extract transparent "chip" cutouts of each kart combo (character × costume × kart) from MKW select-screen idle-loop clips. The select-screen **nameplate** (semi-transparent serrated "tire-tread" plate + opaque yellow item name + opaque 1-UP badge) sits over the bottom of the subject and must be removed cleanly. Two hard cases:

1. **Generic text removal.** Each kart has a *different* name, so the opaque text can't be a fixed template.
2. **The bumper-dip notch.** On spawn-in the car body bounces down (sprung mass dips, wheels don't); for those frames the front bumper dips **behind the opaque name text**. The text is unrecoverable (opaque), so the matte takes notched chunks out of the bumper — chunks that are present once the car settles. The lost pixels are never visible in the idle loop (always behind the car's own text band when dipped).

## Validated pipeline

```
extract_loop(clip)                          -> loopframes/<name>/NNN.png      (raw O, prod crop 988×1080)
                                             -> donors/<name>/dNNN.png          (flourish segment, plate dropped)
A, _, _, mask  = load_template(is_char)      (committed clean-background reference + plate mask)
BLANK          = blank_plate (text-free)     (one-time, baby_daisy 40-kart masked median)
T_B, C_B       = solve_tc(BLANK, A)          (kart-independent un-darken transform)
per clip:
  TEXT         = per-clip text mask          (median frames -> solve_tc -> t<T_OPAQUE in text band)
  donor        = held-before-fade flourish   (biggest edge-energy drop), matted -> fl_alpha, fl_draw
  per frame:
    pre        = pre_darken(O)               (paint plate->A, stamp recovered subject, inpaint notches)
    pre        = flourish_fill(pre)          (align flourish bumper to the dip; fill text∩shape∩~visible)
    chip       = matte(pre)                  (birefnet-general-lite -> RGBA)
```

### 1. Clean-background + plate mask (reused)
`load_template(is_char)` returns the committed clean background `A`, the plate `mask` (`IN_PLATE = mask > 0.05`), and a fallback `t,C`. Crop/geometry: `PROD_CROP_4K=(2100,36,3720,1806)` → `OUT_W,OUT_H=988,1080`; `T_OPAQUE=0.20`.

### 2. Blank-plate un-darken transform (kart-independent)
The un-darken floor is solved from a **text-free** plate, not per-clip. **Why:** a per-clip `t,C` is contaminated by whatever kart sits behind the plate (produced a black blob); the committed single template carries one kart's text. The blank plate is the **masked median of many karts** (baby_daisy owns one of every kart, 40 clips): per clip drop the saturated yellow text to NaN, then `np.nanmedian` — the per-kart text (the "tourists") dissolves, the constant serration + badge (the "landmark") survives.
- `BLANK` → `T_B, C_B = nametag_core.solve_tc(BLANK, A)`.
- `BADGE = (T_B < T_OPAQUE) & IN_PLATE` (the opaque 1-UP badge, ~2920 px on this asset).
- **Current artifact:** `temp/notch_poc/blank_plate_masked.npy` (validated; masked median beat plain median — user-confirmed). **Generation must become a permanent script** (currently scratch `build_blank_plate2.py`).

### 3. Per-clip text mask
`P_clip = median(loopframes[::3])`; `t,_ = solve_tc(P_clip, A)`; `TEXT = dilate((t < T_OPAQUE) & TEXT_BAND, 5×5)`. `TEXT_BAND` = the text rows (from the template's opaque region left of the badge) ∩ `IN_PLATE`. `M = dilate(TEXT, 3×21)` — the fill search band (text footprint, widened).

### 4. Pre-darken (paint-then-stamp + interior inpaint)
- `S = clip((O − CSUB·C_B) / clip(T_B, TFLOOR, 1.6), 0, 255)` — un-darken via the blank transform.
- `subject = IN_PLATE & (|S − A|max ≥ KEY_THR) & ~((BADGE | TEXT) & IN_PLATE)`.
- `out = O; out[IN_PLATE] = A[IN_PLATE]` (paint the whole plate footprint to clean background) `; out[subject] = S[subject]` (stamp back the recovered colliding subject).
- **Interior notch inpaint:** morphological-close `subject` (ellipse `FILL_K`), `holes = IN_PLATE & closed & ~subject`, keep connected components ≤ 2000 px, `cv2.inpaint(TELEA)`. Fills the small text holes *inside* the kart body.

### 5. Flourish bumper fill (the new piece)
The settled idle frame is itself text-occluded, so a shifted idle donor restores only a **truncated** bumper. The **flourish segment** (`donors/<name>/`) has the plate dropped entirely → the car's **complete** bumper shape + **real** colour. We use it as the donor.
- **Donor selection — held frame just before the fade-out.** The flourish settles into a held idle pose (text gone, kart level), then dissolves to empty. The dissolve is a sharp **edge-energy collapse**, so the donor is the frame right before the biggest consecutive drop in per-frame Laplacian variance (`held = argmax(edges[:-1] − edges[1:])`, gated `> 0.4·max`, else last frame). Length-independent; ignores trailing empty/stray captured frames. **Validated:** lands on **d011** for DK roadster, Mario hot_rod, and Mario b_dasher alike — the flourish is fixed-length, the hold is at a fixed index, only the trailing empties vary in count. A **manual donor scrubber** in the tuner overrides the pick per clip (retained as a feature).
- **Matte the donor once** → `fl_alpha` (complete bumper silhouette), `fl_draw` (real colour).
- **Per-frame align** the flourish bumper to the current dip: coarse NCC on the text-free bumper-top band (`ER/EC` crop, left of the text, below the chin) → **ECC `MOTION_TRANSLATION`** sub-pixel refine → `(dx,dy)`. Gate on the coarse NCC score ≥ `SCORE_GATE` (skips frames where the bumper can't be located — wrong/absent kart). This tracks the **true bumper dip** directly (the bumper dips slightly more than the chassis).
- **Fill:** warp `fl_alpha`/`fl_draw` by `(dx,dy)`; `fill = M & warped_alpha & ~subject & IN_PLATE`; connectivity-gate to keep only fill touching the current visible subject; `out[fill] = warped_fl_draw[fill]`. Bounded by the flourish's true shape ⇒ ~empty at settled frames (no tail), grows only as the bumper dips. Optional **FEATHER** distance-transform blend at the boundary (locked **0** — the sub-pixel align is clean enough that no feather is wanted).

### 6. Matte
`birefnet-general-lite` (rembg, CUDA, `temp/asset-venv-gpu`, headless) on the pre-darkened+filled BGR → RGBA chip; emit the transparent idle loop (webp/apng/checker) as the existing `matte_loop` does.

## Locked parameters (user-approved this session)

| Param | Value | Role |
|---|---|---|
| `KEY_THR` | **120** | subject-vs-background discriminator on recovered `S` |
| `CSUB` | **0.5** | additive-floor removal for the **blank-plate** transform (not the 1.0 of the per-template prematte spec) |
| `TFLOOR` | **0.01** | lower clamp on transmission `t` (also the divide-by-zero guard) |
| `FILL_K` | **51** | morphological-close kernel for interior-notch detection (raised 15→51 to close larger idle text-occlusions) |
| `SCORE_GATE` | **0.45** | min coarse-NCC bumper-match to engage the flourish fill |
| `FEATHER` | **0** | fill-boundary blend width (0 = hard fill) |
| `T_OPAQUE` | **0.20** | `t` below which a plate pixel is opaque (text/badge) |

## Components / where code lives

- **Reused as-is:** `nametag_core.py` (`solve_tc`, `prod_crop`, crop/ROI constants), `pre_darken.py` (`load_template`), `matte_loop` encoding.
- **Calibrator (built, validated):** `tools/asset_matte/tune_blankplate.py` — blank transform + per-clip text + inpaint + flourish fill + donor scrubber + the locked sliders. This is the executable spec.
- **To build (production driver):** a headless batch tool that, per clip, builds the text mask, picks+mattes the donor, runs pre_darken+flourish_fill per frame, mattes, and emits the chip — no browser, no per-clip interaction (donor auto-picked by the fade rule). Promote the validated functions out of the tuner into importable pipeline modules.
- **To build (permanent):** the blank-plate generation script (masked median over a one-of-every-kart character's clips).

## Open items / scope of the next plan

- **Scale-out generalization.** Validated on 3 kart combos (DK roadster, Mario hot_rod, Mario b_dasher). The full sweep is character × costume × kart (~40 h capture). Must confirm: the blank-plate transform holds across characters/costumes (different bodies behind the plate); the flourish fade-rule donor pick holds across all clips; SCORE_GATE robustness on clips with weaker bumper texture.
- **Non-dip clips.** Karts/characters that never dip behind the text: the flourish fill must be a clean no-op (fill ~0). The shape-bound + gate should guarantee this, but verify.
- **Character-only chips** (no kart): the bumper-fill stage is kart-specific; characters use only stages 1–4 + matte (per the prematte spec).
- **Frame-window upstream.** Loopframe selection must start at spawn-in; pre-spawn navigation frames (a neighbour kart) are a segmentation concern, not matting (see memory `asset-clip-segmentation`).

## Testing

- **Synthetic unit tests** (`tests/test_pre_darken.py`, build python): planted plate-band frames — serration over known background → recovers to background; over a known subject colour → recovers that colour; yellow text + bright badge → erased to `A`. (Existing; keep.)
- **Donor-selection unit test:** synthetic edge-energy series with a fade collapse → asserts held-before-fade index.
- **User eye-test (acceptance, as every prior asset step):** the tuner across the three karts — bumper bottom full + seamless through the dip, no tail at settled frames, text gone, body intact. **Passed this session** at the locked params.

## Non-goals

- Re-deriving the un-darken transform (reused), a new matte model, per-language text handling (leaving the yellow font as opaque UI is language-independent), the capture/segmentation path, instant (no-re-matte) slider preview.
