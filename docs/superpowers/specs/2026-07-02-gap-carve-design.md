# Gap-carve post-pass (backdrop-diff hole carving) — design

**Date:** 2026-07-02
**Problem:** The matte (birefnet anchor → MatAnyone2) systematically *fills* real see-through
gaps in the subject — e.g. the hole under `mario__base__hot_rod`'s rear spoiler — freezing a
rectangle of the capture backdrop into the chip (idle f115 @ (697,683)–(713,692), flourish f39
@ (752,692)–(799,717), spawn f0 same rect). The alpha there also flickers 0↔255 across the
idle loop (measured mean 127), so the hole pops in and out during playback.

This is the *inverse* of the `_repair_holes` defect class: instead of birefnet wrongly cutting
subject, it wrongly keeps background. Semantic matters (birefnet, SAM2 — both measured) make
the same call, so no model swap fixes it; and the two-bg/animated-bg capture routes are ruled
out (scene relighting / loss of the clean plate). The fix uses the one signal models don't
have: the backdrop is **frozen and exactly known** (`clean_backdrop`, already extracted per
clip from the fade tail).

**Measured basis (mario__base__hot_rod idle, 120 frames):** defect-gap temporal std 0.52 and
RGB ≈ backdrop; real dark kart panel std 8.34; red body std 6.13. Order-of-magnitude
separation between "static backdrop showing through" and "subject that putters/wobbles".

## Design

New pure-numpy post-pass `_carve_gaps(alphas, raw_paths, backdrop, temporal_gate)` in
`tools/asset_matte/matte_blankplate.py`, applied to the whole segment's alpha stack after the
matte engine (both `matanyone` and legacy `birefnet` branches), before `_write_chip`.

Per segment:

1. **Backdrop match** (per frame): `diff_i = ||raw_i − B||₂` per pixel against the per-clip
   `clean_backdrop` plate `B`; `bgmatch_i = diff_i ≤ CARVE_THR` (absolute levels — the gap
   shows the *same* static backdrop pixels, so the diff is codec noise only).
2. **Temporal-static gate** (idle only, `temporal_gate=True`): per-pixel temporal std of the
   raw stack `< CARVE_STD_THR`. A real subject pixel wobbles with the kart's putter; a gap
   pixel is frozen backdrop. Spawn/flourish move through the gap region, so they rely on the
   per-frame backdrop match + component gates alone.
3. **Candidates:** `carve_i = bgmatch_i & (alpha_i > 0) [& static]`.
4. **Component gates** (per frame): drop connected components `< CARVE_MIN_AREA` px
   (speckle); keep only components whose **median** diff is at the clip's codec-noise floor
   (`CARVE_MED_DIFF`) — a true gap IS the plate, so its median tracks the floor, while a
   dark kart part merely *near* a dark backdrop sits measurably higher; on idle additionally
   require the component's mean temporal std ≤ `CARVE_COMP_STD`.
5. **Apply + feather:** `alpha_i = min(alpha_i, 1 − blur3(carve_i))` — carved region goes to
   0 with a ~1px soft edge.

**Measured tuning (hot_rod, dark set):** outside-background noise floors 2.45–3.0; true gap
components median 2.45–3.74 (incl. two *additional* real gaps the pass found on its own:
Mario's arm-body gap and the steering-wheel ring); harmful coincidental matches (engine
intake slots, grille stripe, skirt shadows) ≥ 4.4 → `CARVE_MED_DIFF = 4.0`. On idle the true
gap's component mean-std was 0.66 vs ≥ 0.90 for every false candidate → `CARVE_COMP_STD =
0.8`. With these gates the idle carve heat map reduces to exactly the real gap; the worst
spawn/flourish frames show no visible subject damage.

## v2 — per-segment decisions (flicker fix)

v1 made carve decisions independently PER FRAME with hard thresholds; kart parts near the
backdrop colour flipped in/out with codec noise (user-observed flicker; measured on hot_rod
spawn: ≥4-transition pixels 1845 vs 847 pre-carve). v2 makes every decision ONCE PER
SEGMENT, so carve flicker is structurally impossible:

- **Idle**: one global mask from temporal aggregates (per-pixel *median* diff, static gate,
  any-frame alpha support, component gates on the aggregate) applied identically to every
  frame. Idle carve px/frame is now literally constant.
- **Spawn/flourish**: a pixel qualifies only while it matches the backdrop for ≥
  `CARVE_RUN = 4` consecutive frames (noise can't sustain a run), and each spatial
  component (projected over the segment) gets one keep/drop verdict from the median diff
  over its whole space-time support. Pixels enter/leave only at real occlusion boundaries.

Verified: hot_rod spawn ≥4-transition pixels 698 (below the 847 pre-carve baseline); across
all 13 sweep clips no segment exceeds baseline due to carve — the two segments above
baseline measure IDENTICAL flicker with `MATTE_CARVE=0` on the same engine run (engine
variance, not carve). Defect fixes retained (idle f115 rect alpha 0.02, constant).

Raw (pre-predark) frames are used for the diff — predark repaints the nameplate band and must
not perturb the comparison; frames are re-read from `raw_paths` (already on disk).

**Wiring:** `matte_loopframes` already accepts `clip=`/`backdrop=` (currently unused since
hole-repair was disabled); compute `backdrop = clean_backdrop(clip)` when not supplied, pass
`temporal_gate=(segment is the idle loop)` — in practice: temporal gate on when
`apply_predark` is irrelevant; the caller (`process_all`) knows the segment name, so it
passes `carve_temporal=seg == "idle"`. If the backdrop can't be computed → carve skipped
(same failure mode as hole-repair). Applies to karts **and** standalone chars (chars have
under-arm gaps too); the sweep check below validates both, and the char haze-blob lesson
doesn't transfer (that was the additive direction with percentile-normalised soft-diff — the
carve uses a tight absolute threshold).

**Config:** module constants `CARVE_THR = 12`, `CARVE_MED_DIFF = 4.0`, `CARVE_STD_THR =
2.0`, `CARVE_COMP_STD = 0.8`, `CARVE_MIN_AREA = 25` (tuned on the real defects, see above),
env kill switch `MATTE_CARVE=0`.

## Failure analysis

- **Over-carve risk** = a genuinely static, backdrop-coloured subject pixel. On idle both
  gates must fail simultaneously (motionless while the kart vibrates AND within ~12 levels of
  the backdrop at that exact pixel). On spawn/flourish only the colour gate holds, but those
  segments have the most subject motion (least static coincidence), and the failure is
  benign on the intended dark render bg (swaps backdrop-grey for render-dark).
- **Under-carve** (gap pixel missed) = status quo, not a regression.
- Alpha-edge interactions: carving a matched pixel adjacent to the outer silhouette removes
  backdrop fringe — desirable; feather keeps the new hole edge soft.

## Validation

1. Standalone prototype on the existing `D:\kartoff\newerdump\asset_chips_bidir` output
   (re-extract loopframes CPU-only; reuse the stored alpha channel): the three cited
   defect regions must carve; before/after crops for the user.
2. Sweep across all available processed chips (mario set + 41-kart baby_daisy dark set):
   carved-px-per-frame report per clip; manual eyeball of outliers, esp. dark karts
   (billdozer, dread_sled, bowser_bruiser) and the cream-collar big_horn case.
3. Unit tests (no GPU): synthetic jittering-square-with-gap stack — gap carved, moving
   subject kept, bg-coloured *moving* patch kept (temporal gate), speckle kept, kill
   switch + missing-backdrop no-ops.
