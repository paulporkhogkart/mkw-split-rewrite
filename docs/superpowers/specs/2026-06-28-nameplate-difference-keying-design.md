# Nameplate Difference-Keying — Design

**Date:** 2026-06-28
**Status:** Approved approach (user picked option B), pending spec review
**Branch:** asset-clip-sweep
**Builds on:** `tools/asset_matte/` nametag productionization (`undark.py`, `nametag_core.py`, committed `assets/`); the validated un-darkening (memory `nametag-mask-undark`).

## Problem

The current post-matte nameplate handling (`undark.py`) removes the plate via `drop_nameplate` — a **connectivity** rule: it drops a matte component only when that component lies almost entirely inside the plate footprint (a *detached* blob). It works for characters that don't touch the plate (birefnet leaves the plate as a separate blob). The moment a character's silhouette **connects** to the plate (DK's hands, and many others), the plate is one component with the body, can't be dropped, and only gets *lightened* by the un-darken. Result: the plate stays.

Root cause framed usefully: there are two operations on the plate — **(1) un-darken** (recover the true colour of pixels that *are* behind the semi-transparent plate) and **(2) cut** (make the plate transparent where there's *nothing* behind it). On **karts**, birefnet performs (2) for free (a kart is large + salient, the plate is non-salient UI clutter → birefnet discards it), so only (1) had to be built. On **characters**, birefnet *keeps* the connected plate, so (2) never happens. This spec adds operation (2) deterministically; operation (1) is unchanged and keeps its validated kart-tuned values.

## Approach (option B: post-matte difference-keying)

We already have `P` — the **measured plate-over-empty-background** (median of the Rally-Bike idle plate region, per screen, committed in `assets/`). In any frame's plate band we can decide, **per pixel, with no connectivity dependency**, what to do:

| plate-band pixel | test | action |
|---|---|---|
| name text | the font is the **same yellow** for every character → HSV yellow + dilation | **cut** (character-independent) |
| 1-UP badge (kart screens) | opaque plate core: `t < T_OPAQUE` | **cut** (already cut today) |
| serration over empty background | semi-transparent (`t ≥ T_OPAQUE`) **and** `|O − P|` small → nothing behind it | **cut** (NEW) |
| serration over a character | semi-transparent **and** `|O − P|` large → a character is behind it | **keep + un-darken** (unchanged) |

`O` is the raw loopframe pixel (already available in `process`). This is the *cut* birefnet does for free on karts, made explicit so it also works when the plate is stuck to a character.

**Validated** (scratch `key_probe.py`, DK + koopa): serration-over-bg and the yellow text cut cleanly even though DK's plate is connected; only a faint text-*outline* ghost remained (the anti-aliased letter edge that isn't saturated-yellow) — closed by the dilation knob. Confirms the mechanism before building.

## Architecture & components

Three units, each with one responsibility:

1. **`key_plate(rgba, raw_bgr, P, t, mask, params) -> rgba`** (new, in `tools/asset_matte/undark.py`) — the deterministic cut. Pure cv2/numpy. Computes the **new** cut mask (yellow-text-dilated `|` serration-over-bg-by-`|O−P|`), feathers it, and zeroes the matte alpha there. Returns a new RGBA. **Replaces `drop_nameplate`.** Does NOT touch colour (that's `undark_rgba`).
   - `undark_rgba` (the validated recover + opaque badge/glyph cut at `t < T_OPAQUE`) is **unchanged** and still runs first; the opaque-badge cut stays its job, so `key_plate` only adds the serration-over-bg and robust-yellow-text cuts. Order is irrelevant to correctness (cut pixels' colour doesn't matter), but recover-then-cut keeps `undark_rgba` untouched.

2. **`load_template(is_char)`** (modify) — additionally return `P` (the prod-cropped plate-over-bg reference: `prod_crop(place_in_canvas(<screen>_P, ROI))`), so `key_plate` has its reference. Now returns `(t, C, mask, P)`.

3. **`tune_nameplate.py`** (new, `tools/asset_matte/`) — a live **cv2-trackbar** tuner (the same shape as the prior `undark_tune.py` the user liked). Loads a fixed set of *already-matted* representative frames — **DK** (touching), **koopa** (detached), **mario**, and **one kart combo** — runs the full `undark_rgba → key_plate` pipeline live, and shows each result composited over a checkerboard, updating on every trackbar move (instant — no re-matte, the whole reason B beats pre-matte). Prints the chosen params on quit for baking into `undark.py`.

### Parameters (the tuner's knobs)

New, for `key_plate`:
- `KEY_THR` — max-channel `|O − P|` threshold (units 0–255). Below ⇒ "matches empty background" ⇒ cut. Validated starting point ≈ 18.
- `YELLOW_S`, `YELLOW_V` — saturation/value floors for the name-text detector (hue fixed ≈ [18,40]). Start ≈ 90 / 120.
- `TEXT_DILATE` — px dilation of the yellow-text mask to swallow the anti-aliased letter outline. Start ≈ 2–4.
- `FEATHER` — px Gaussian softening of the cut edge (avoid hard jaggies). Start ≈ 1–2.

Unchanged (the validated kart values, reused for the un-darken half): `ALPHA_GAIN=5.0`, `STRENGTH=1.02`, `CSUB=0.69`, `TFLOOR=0.05`, `T_OPAQUE=0.20`.

## Data flow

`process(base, names, is_char)` per frame becomes:
```
rgba   = matte/<name>_frames/NNN.png            # birefnet matte (may include the connected plate)
raw    = loopframes/<name>/NNN.png              # the observed plate O
t,C,mask,P = load_template(is_char)
rgba   = undark_rgba(rgba, t, C, mask)          # UNCHANGED: recover colour + cut opaque badge/glyph
rgba   = key_plate(rgba, raw, P, t, mask, KEY_PARAMS)   # NEW: cut text + serration-over-bg, feathered
write  matte/<name>_undark/NNN.png
```
`drop_nameplate` is removed (the keying subsumes the detached-blob case: a detached blob *is* plate-over-bg + text, so `key_plate` cuts it). The plate-presence gate and per-screen template selection are unchanged.

## Error handling / edge cases

- **A character body part that is genuinely yellow *and* dips into the plate band** (rare) could be cut by the yellow rule. Mitigation: the yellow detector is confined to the plate footprint (`mask > floor`) where character pixels are uncommon; `KEY_THR`/`YELLOW_*` are tunable. Flag any such case in the eye-test; not solved pre-emptively (YAGNI).
- **`|O − P|` false "keep" from background animation/HDR drift:** the select-screen background is static/blurred so `P` matches closely; residual drift is absorbed by `KEY_THR`. If a clip's background differs, that surfaces as stray kept specks in the tuner.
- **Karts:** `key_plate` runs uniformly but is ~a no-op for karts (birefnet already cut the plate → little plate alpha to cut); `undark_rgba` still recovers kart-dipping-in. No kart regression expected; verified in the eye-test on the kart frame.
- **Detached-blob halo:** if removing `drop_nameplate` leaves a faint matte halo just outside the footprint on detached blobs, retain `drop_nameplate` as a cheap first pass (documented fallback) — decided by the eye-test, not pre-built.

## Testing

- **Synthetic unit tests** (`tests/test_undark.py`, build python): planted plate-band frames — (a) serration matching `P` over a synthetic matte ⇒ cut; (b) serration differing from `P` (character behind) ⇒ kept + colour recovered; (c) a yellow text patch ⇒ cut (incl. dilation); (d) a non-yellow character patch in the band ⇒ kept. Assert the cut mask and resulting alpha.
- **User trackbar eye-test** (the real acceptance, as with every prior asset step): tune on DK/koopa/mario/kart in `tune_nameplate.py` until clean across all four; bake params; re-run the idle loops; eyeball the animated `_undark_checker.webp`.

## Scope

In: the character idle path (the immediate problem) + the shared `undark.py` change + the tuner. Karts continue to work (un-darken unchanged; keying no-ops). Out: the kart-combo *capture* path (separate effort — spawn-in window + wheel-robust period); any new matte model.

## Out of scope / non-goals

- Re-tuning the un-darken values (frozen, reused).
- Pre-matte un-darkening (rejected: birefnet-dependent + cannot give live slider tuning — every change needs a GPU re-matte).
- Per-language text handling (the yellow-font cut is language-independent).
