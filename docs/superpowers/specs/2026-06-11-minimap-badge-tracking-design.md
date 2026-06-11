# Minimap "badge" tracking - design

**Date:** 2026-06-11
**Status:** awaiting review
**Evidence:** `temp/mm_lab.py` harness runs over `temp/bootest.mp4` (King Boo /
Aristocrat, Koopa Troopa Beach time trial, SDR recording of HDR output - the
worst-case washout footage we have). Artifacts in `temp/mm_lab_out/`.

## Problem

During RACING the published minimap position shakes, worst on bright/washed
courses (HDR input flattened by the capture path) and with detailed character
icons. The ring is still found; the position is what wobbles.

### Measured root causes (bootest.mp4, 6509 racing frames)

1. **Hough centre jitter is published unfiltered.** HoughCircles finds a ring on
   99.5% of frames even at peak washout - ring *detection* is not the problem.
   But its centre estimate wobbles (weak washed edges, King Boo's sprite breaking
   the circle, the TT ghost adjacent), and `_publish` stores it raw.
   `minimap_update` (protocol.py) and `MinimapRecorder` both consume **raw**
   `cx/cy`; the EMA-smoothed values are never used by anything downstream.
   Baseline raw frame-to-frame jump: median 2.24px, p90 5.83px, p99 8.06px.
2. **The identity check cannot do its job.**
   - Raw `TM_CCORR_NORMED` (not zero-mean) on an HSV stack barely
     discriminates: everything scores ~0.7-0.85, so `_MM_ACCEPT_SCORE=0.18`
     never fires, and the calibrated confident threshold (0.877 stored for this
     combo) is unreachable on washed footage - the whole race ran in
     `ring_only` (98.8%). The gate is decorative on exactly the footage where
     it is needed.
   - Single-point scoring at the Hough centre, no slide room - the same
     no-slide pathology fixed for costumes with `COSTUME_SEARCH_PAD`.
   - The interior crop (`0.6r x 0.9r` at the per-frame Hough radius, ~2x
     upscaled with INTER_AREA, CLAHE on 6x9px tiles) lands on the *blank centre
     of the face* and misses every distinctive feature (crown, mouth, tongue),
     while its scale jitters with the Hough radius.
3. Hygiene: `seed()` with no stored radius defaults to `(12+42)//2 = 27`, which
   is outside the Hough band [17, 25] - a baked-in ~29% template scale mismatch
   for unseeded courses. (All migration-seeded courses store r=20, so this only
   bites future/unseeded paths.)

## Decision

Replace the interior-face identity scorer with a **whole-marker "badge"
template**: a single masked, zero-mean NCC template covering the face *and the
white ring* (the marker the game draws for the local player). The ring is the
strongest, most washout-resistant structure available and is exactly the thing
that distinguishes the player marker from the ghost (no ring) and from map
content. Matching slides the template around the Hough centre and **publishes
the correlation argmax**, not the Hough centre - this removes the Hough wobble
from the output entirely.

### Measured comparison (same clip, same seed, closed loop)

| variant | raw jump med / p90 / p99 (px) | teleports >12px | state mix | identity separation |
|---|---|---|---|---|
| base (today) | 2.24 / 5.83 / 8.06 | 1 | 98.8% ring_only | none in practice |
| CLAHE pre-Hough | 3.16 / 6.40 / 8.94 | 1 | unchanged | - (worse jitter) |
| face-only NCC + slide | 1.41 / 4.00 / 5.39 | 0 | 97.6% tracking | true p10 0.553 vs imposter p99 ~0.63 - **overlaps** |
| **badge NCC + slide** | **1.00 / 1.00 / 1.41** | **0** | **99.5% tracking** | true p10 0.745 / med 0.806 vs imposter p99 <= 0.40, max 0.50 |

1px is the physical floor (actual kart movement per frame). The badge run held
score 0.78-0.81 with the Bowser ghost directly adjacent (stills:
`temp/mm_lab_out/badge_ghost.png`).

Imposter = max masked-NCC over 8 angles at fixed distances (20..60px) from the
tracked position, sampled every 5th frame (1301 samples per distance).

### Cross-validation: second clip, background bleed (`--clip short`)

`temp/short.mp4` - red Yoshi on Moo Moo Meadows (healthy contrast, 4199
frames). Yoshi's sprite fills ~75-80% of the ring interior, so ~20-25% of the
masked badge area is **map terrain bleeding through**, and the marker crosses
dirt <-> track-ribbon terrain repeatedly (template seeded over one, scored over
the other).

| variant | raw jump med/p90/p99 | state | score med / p10 / p01 |
|---|---|---|---|
| base | 1.00 / 2.24 / 2.83 | 100% tracking | 0.995 / 0.992 / 0.990 |
| badge | 0.00 / 1.00 / 1.41 | 99.8% tracking | 0.818 / 0.770 / **0.722** |

Conclusions:
- **Bleed costs score but not stability**: ~20-25% bleed dilutes the NCC by
  ~0.18 (uncorrelated pixels), but the worst 1% of frames (0.722) stays above
  the confident gate (0.65) with the accept gate (0.45) nowhere in sight. Bleed
  pixels dilute; the ring + sprite majority dominates the argmax, so position
  stays pinned.
- **No regression on healthy input**: base is fine on this clip (its failure
  is washout-specific); badge is still 2x tighter on raw jitter.
- The fixed 44x44/r21 geometry absorbed this course's slightly larger ring
  (annulus probe: r~22 vs ~20-21 on KTB) without tuning.
- Extrapolation for genuinely small sprites (~40-50% bleed): expected median
  ~0.7-0.77 with tails near the confident gate - the existing per-combo
  auto-calibration handles exactly this (confident is per-(course, character,
  costume)), and `ring_only` frames still publish. The accept floor (0.45) has
  measured margin to ~25% bleed; if a small-sprite clip ever shows tail dips
  near it, the Phase-3 stability mask is the designed mitigation.

### Cross-validation: small sprite (`--clip koops`)

`temp/koops.mp4` - Koopa Troopa on Koopa Troopa Beach TT (5999 frames, Bowser
ghost on course). Koopa's sprite leaves **~35-45% of the ring interior as
terrain bleed** - the small-sprite worst case requested in review. Run with the
production DB seed, which is 8.5px off the true badge centre (also a fidelity
test of off-centre seeding).

| variant | raw jump med/p90/p99 | teleports | score med / p10 / p01 |
|---|---|---|---|
| base | 1.00 / 2.24 / 5.00 | 0 | 0.952 / 0.948 / 0.941 |
| badge | 1.00 / 1.41 / 2.00 | 0 | 0.719 / 0.643 / 0.607 |

Separation (sepstats): badge true p01 0.607 vs far-imposter p99 <= 0.45 -
margin +0.16 at the extreme tails (King Boo: +0.29). Face-only scoring is
confirmed unusable at this sprite size (true med 0.559 vs imposters 0.43-0.51).

Consequences for gates:
- Accept 0.45 holds (touches the far-imposter p99; it is the fourth line of
  defence behind ring-first, closest-to-reference, and the jump gate).
- The 0.65 default confident gate puts ~12.5% of Koopa frames in `ring_only`
  (still published). `calibrate_from_race()` sets this combo's gate to ~0.58
  after one race - the per-(course, character, costume) auto-calibration is
  doing exactly its designed job on the new scale. Default stays 0.65.
- Scores landed inside the extrapolated band (~0.7-0.77 median), so the bleed
  model is predictive; the stability mask remains a designed-but-unbuilt
  Phase-3 lever.

### Phase-1 addition: seed self-centring

Both clips show the stored DB seed sitting 3-9px off the true badge centre
(hand-captured once, start position varies per setup). At seed time, before
cropping the template: refine the seed point with one annulus-NCC pass
(synthetic ring template, `TM_CCOEFF_NORMED`) in a +/-16px window around the
DB seed and crop the badge template at the refined centre. Removes baked-in
template offset for every course at negligible cost (one small matchTemplate
at race start). The koops run passed *without* this; it raises template
quality for free.

### Phase-3 addition: stability mask (only if small-sprite tails dip)

After ~2s of confident tracking, maintain per-pixel running variance of the
aligned badge window; down-weight high-variance pixels (terrain bleed) in the
mask and re-normalise the template. Self-supervised, character-agnostic,
trivial cost. Not built until a real clip shows the need.

### Why the alternatives lost

- **CLAHE before Hough**: amplifies sand texture; centre jitter got worse, and
  ring detection didn't need help (99.5% found). Dropped.
- **Annulus matched-filter fallback**: fired on 18/6509 frames - no measurable
  demand on this clip. Superseded by the Phase-2 badge-at-prediction fallback,
  which reuses the badge scorer instead of adding a parallel detector.
- **Face-only template** (even with Lab + slide + native res): imposter
  separation is inadequate on washed footage - slide+state-machine max-pooling
  over near-zero-structure patches reaches 0.63 on plain sand, overlapping the
  true-score distribution. The ring in the badge is what creates the margin.
- **HSV channel stack**: H is angular (red wraps 0/179) and is noise at low
  saturation; replaced by Lab (continuous chroma, washout concentrates into L).
- **"Detect HDR and switch pipelines"**: zero-mean NCC is gain/offset invariant
  per window, which is why badge scores stayed flat across this clip's washout
  swings. No input-mode detection needed for this fix; the planned global
  capture-normalization project remains the input-side lever. The live HDR
  test below is the validation gate for this claim.

## Production design

All changes inside `mkw_tracker/minimap/tracker.py` (+ config/docs/tests). No
IPC or frontend changes: the payload keeps raw `cx/cy`, which is now stable at
the source.

### Template (built in `seed()`)

- Crop `44x44` (`2*BADGE_HALF`) BGR centred on the seed point from the seed
  frame; convert Lab float32/255. Circular mask r=21 (face + ring; halo and
  corner terrain excluded).
- Keep `_make_circle_mask`; drop the HSV stack, the per-patch CLAHE, the
  canonical 24x36 resize, and the radius-scaled `_crop_interior` (the marker
  never changes on-screen size; fixed geometry).
- Seed radius default: clamp to the Hough band midpoint (21) instead of 27.

### Scoring (`_score_at`, every confirmed ring hit)

- Crop `(2*(BADGE_HALF+BADGE_PAD))^2` window at the Hough centre
  (`BADGE_PAD=8`; the ring's thin annulus makes the peak sharp, so slide reach
  must cover Hough centre error). Near ROI edges, replicate-pad the crop.
- `cv2.matchTemplate(window_lab, tpl_lab, TM_CCOEFF_NORMED, mask=mask)` ->
  `nan_to_num` -> `minMaxLoc`. Verified exact match with the reference NCC loop
  (OpenCV 4.13, diff 0.00000) at 1.56ms/call; ~2.5ms total minimap cost per
  frame, acceptable at 60fps. If it ever matters: PAD=6 while TRACKING,
  hierarchical search, or 1-channel mask are easy trims.
- `_on_confirmed_hit` / `_publish` use the **argmax position** (score's refined
  centre), not the Hough centre. Hough remains the detector/gate (ring-first
  stays: the ghost has no ring and is structurally rejected).

### Gates and calibration (new score scale)

- `_MM_ACCEPT_SCORE`: 0.18 -> **0.45** (imposter p99 0.40 < gate < true p10 0.745).
- `_MM_CONFIDENT_SCORE` default: 0.90 -> **0.65**.
- `_MM_CALIB_MIN/MAX`: 0.75/0.98 -> **0.55/0.90**; the median-margin formula in
  `calibrate_from_race()` is scale-free and stays.
- **Migration: stored `minimap_thresholds` are on the old CCORR scale and must
  be cleared once** (otherwise e.g. 0.877 loads as the confident gate and badge
  scores p10 0.756 ride ring_only forever). One-time `DELETE FROM
  minimap_thresholds` in a schema migration; auto-calibration repopulates per
  race. `minimap_seeds` / `minimap_rois` are unaffected.
- New constants join `config/Defaults` + `docs/config-reference.md`
  (`_MM_BADGE_HALF`, `_MM_BADGE_PAD`, `_MM_BADGE_MASK_R`, revised gates).

### Phasing

1. **Phase 1 (the fix):** badge scorer + argmax publish + gates + threshold
   migration + seed-radius hygiene + tests. Acceptance on the harness
   (bootest.mp4): raw p90 <= 1.5px, >= 95% tracking, 0 teleports; sanity sweep
   on `temp/aiden.mp4`. Then the **user's live HDR test** - bootest is an SDR
   recording of HDR output; live capture input is the one condition the clip
   cannot reproduce.
2. **Phase 2 (resilience, after live test):** when Hough misses while TRACKING,
   score the badge at the EMA-predicted position and accept on the same gate
   (track-before-detect bridge for ring outages). Mechanism validated by the
   gate margins; demand was too rare on this clip to measure (0.5% miss rate).
3. **Phase 3 (only if live testing shows template drift or small-sprite tail
   dips):** median-of-N seed frames; small high-confidence template bank
   (max-over-bank); stability mask (above).

### Testing

- Unit: badge build/self-match=1.0, argmax offset recovery on shifted synthetic
  frames, edge replicate-pad, NaN guard, gate boundaries, calibration on the
  new scale, threshold-migration row deletion.
- Harness: keep `temp/mm_lab.py` during implementation as the regression
  oracle (baseline vs badge numbers above); promote a parameterised version
  (course/seed/segment from DB + CLI) to `mkw_tracker/tools/` as part of
  Phase 1 so future CV changes can be measured, not vibed.

## Open questions

1. OK to clear all stored `minimap_thresholds` (auto-recalibration rebuilds
   them; the only cost is one race per combo at the default 0.65 gate)?
2. Recorded replay trails currently store raw positions; with raw now stable
   this stays as-is. Any desire to also persist the smoothed track instead?
3. Multiplayer (non-TT) races: do other players' markers carry rings on the
   minimap? Ring-first + badge identity handles either way, but if they do,
   the accept gate is the only thing separating two ringed markers in a pile-up
   - worth a VS-race clip through the harness before trusting it there.
