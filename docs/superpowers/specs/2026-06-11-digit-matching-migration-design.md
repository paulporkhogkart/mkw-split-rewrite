# Digit matching migration: binarize/free-slide -> grayscale common-support NCC

**Date:** 2026-06-11
**Status:** approved (user "Go"), building
**Motivated by:** the race-clock validation finding - koops.mp4's frozen
finish reads 1:31.115 instead of 1:38.185 *consistently* ('8' -> '1' in two
slots), which (a) blocks the value finish latch, (b) drags the RaceTimer
estimate ~700ms low via frequent low misreads, and (c) implicates
TimestampTracker burst totals/splits (same reader -> possible wrong stored
PBs today).

## Scope

One shared reader serves five systems - `load_digit_templates` +
`read_digit_roi` in `race/laps.py`, consumed by LapTracker (h=40/28, thr
0.70), CoinTracker (h=35, thr 0.60), TimestampTracker (h=42, thr 0.50),
RaceTimer and FinishValueLatch (thr 0.50). The fix replaces the matcher and
the template assets behind the existing `(digit, score)` seam; consumers keep
their call sites, with thresholds re-derived for the new score scale.

## Root causes (two independent bugs)

1. **Fixed binarize (crop at 170, assets pre-binarized)**: washed/HDR-flattened
   capture pushes the 8's thin waist and anti-aliased loops below threshold -
   the glyph loses strokes. Same class of failure the screens migration
   removed. The binary assets compound it: per-consumer rescaling of binary
   images re-binarized at 127 produces lumpy references (current assets:
   ~50px, 2 unique values).
2. **Free-slide max-over-digits**: every digit template slides anywhere in the
   slot crop; best position wins. A narrow '1' (26px wide) only has to explain
   one surviving stroke of a damaged '8' (39px); the '8' must explain its
   whole damaged glyph. Small templates always win on damaged glyphs - this is
   the decisive 1-vs-8 bug, independent of representation.

## Design

### Matching

- **Representation:** grayscale crop, light blur, `TM_CCOEFF_NORMED`
  (gain/offset-invariant; digits are self-contrasting bright fill + dark
  outline). Canny-edge matching is the measured fallback if grayscale
  underperforms at the 28px lap-digit scale - the harness compares both
  before the swap is committed.
- **Common support:** all ten templates are rendered onto one canvas size
  (widest digit's box + small pad), glyph horizontally centred. Matching
  slides this common window over the slot with +/-2-3px slack only. A '1'
  proposed for an '8' slot is penalised by the unexplained loops, because
  they are inside the same compared window for every candidate.
- **Margin gate:** `read_digit_roi` returns `None` unless best beats the
  second-best by a relative margin (re-using the `match_variants`
  best-vs-second pattern from selection). `None` is safe for every consumer
  (burst re-reads, RaceTimer skips, latch resets, lap/coin keep last value);
  a confidently wrong digit is not. Signature stays `(digit, score)`.
- `reconfirm_digit` fast-path keeps its semantics (score the cached digit
  first, accept early at the reconfirm threshold) on the new scale.

### Template assets (harvested from real capture, per user direction)

- Sources: `temp/bootest.mp4` (washed) + `temp/koops.mp4` (washed, contains
  the failing 8s) + `temp/short.mp4` (healthy contrast).
- Labels without trusting the broken reader:
  - **Freeze segments with known totals** (bootest 1:36.713 -> digits
    1,3,6,7,1,3; koops 1:38.185 -> 1,3,8,1,8,5 *including the worst-case
    washed 8s*) give exactly-labeled samples, many frames each.
  - **Running segments**: the displayed timer is linear in frame index, so a
    robust line fit (median offset over consensus frames) predicts every
    slot's digit; harvest only slots whose predicted digit phase is
    mid-period (A-D; the E/F wheels spin too fast for fit certainty).
- Per digit: median-stack the registered grayscale samples (across clips, so
  washed and clean variants average into a robust reference) -> one grayscale
  PNG per digit at native timer height (~46px), stored in
  `images/digits/` with a regen script in `scripts/` (precedent:
  `gen_selection_templates.py`). Per-consumer heights come from grayscale
  INTER_AREA rescaling at load. Old `images/timestamps/cropped/*.png` stay
  on disk for the legacy monolith; the package stops reading them.
- Hand verification gate: a contact sheet of the ten final templates +
  per-digit sample counts is reviewed before the swap (one-time, stills).

### Validation harness (`temp/digit_lab.py`)

Ground truth: per clip, fit the linear race clock on RANSAC-style consensus,
then score matchers per frame on the timer slots:
- read success rate (non-None), wrong-read rate, per-digit confusion matrix
  (8->1 explicitly tracked);
- score distributions per digit -> thresholds + margins re-derived from
  measured separation (per-consumer threshold defaults updated in
  `config/defaults.py`).

Downstream acceptance:
- **koops' finish latches 98185ms** via FinishValueLatch (the currently
  failing case) in `temp/finish_lab.py`;
- bootest keeps latching 96713ms with latency <= 0.2s;
- RaceTimer estimate error vs the fitted clock shrinks materially (report
  before/after; if the lag collapses, note that `TOLERANCE_MS=800` can be
  revisited - not changed in this project);
- lap and coin reads: no regression on a sampled sweep (lap counter
  monotonic 1..5 on the KTB clips; coin reads stable between pickups).
- engine pytest + frontend vitest suites green.

## Out of scope

Colon/period templates (unused by the package reader); re-tuning
`FinishValueLatch.TOLERANCE_MS`/`DELAY_MS` (follow-up once estimate quality
is measured); auditing historical stored runs for 8->1 damage (flagged for
the user - server-side data, separate task).
