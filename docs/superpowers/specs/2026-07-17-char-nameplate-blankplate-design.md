# Char Nameplate Removal — Blank-Plate Port — Design

**Date:** 2026-07-17
**Status:** Approved direction (user 2026-07-17); prototype-validated end-to-end at the predark level this session
**Branch:** `char-nameplate-blankplate` (to be created)
**Builds on:** `2026-06-28-nameplate-prematte-design.md` (the original unified pre-matte, still the char path today), `2026-06-29-kart-chip-matte-pipeline-design.md` + the 2026-07-02 full-S stamp fix `eac3c82` (the kart blank-plate treatment being ported), `tools/asset_matte/matte_blankplate.py`.

## Problem

Standalone character chips ship nameplate artifacts (user-reported; all reproduced): rosalina idle **and** flourish, penguin idle (ghost fades in over the loop), peepa flourish tail. Kart combos are clean. Root causes, verified frame-by-frame on 2026-07-17:

1. **Chars still run the original single-still `pre_darken`** (karts moved to the blank-plate path). Its live-text gate `HSV S > 250` misses the anti-aliased ring (39% of glyph pixels measure ≤ 250 on the template's own text), so the ring is "recovered" (divided by serration t ≈ 0.5, i.e. brightened) and stamped back — a pale ghost outline of the character's own name in every frame.
2. **The committed `char_P.png` template is a `mario__base` still**: the solved per-pixel transform is corrupt in and around the MARIO glyphs, stamping a Mario-shaped mis-recovery into every character's plate. On plate-gone frames it blows out to solid white — the shipped rosalina flourish f75–77 contains a readable white "Mario".
3. **The committed char templates are stale bright-era captures.** Template `char_A` ≈ [236,209,186] where the live SDR backdrop behind the plate is ≈ [65,69,69]. Two consequences: the old paint-footprint-to-A leaves a visibly bright band; and a naive kart port is IMPOSSIBLE against this A — `solve_tc(blank_live, A_template)` degenerates to t ≈ 0 in-plate (the dark neutral live serration carries no channel signal against a mismatched bright reference), which would make the un-darken divide by TFLOOR and blow out. Measured: t percentiles 0/0/0 vs the healthy kart blank's 0.0/0.51/0.9.
4. **The char plate slides left + fades out starting exactly `cut − 9`** (survey of 12 chars: 11/11 with measurable plates share the onset frame; piranha_plant and cataquack bodies fully occlude their plates — nothing to measure, rule harmless for them). The plate is fully gone by ~`cut − 5`. The flourish export runs to `fe = cut − CHAR_CUT_GUARD = cut − 2`, so the last ~7 exported frames contain the departing/absent plate — and char predark is currently unconditional (`apply_predark = not (kart and seg == "flourish")`), painting a phantom plate + blown text ghost onto them.

Why only some characters show it: the predark mess exists for every char in every frame, but it only survives the matte where the body touches the plate band (penguin's feet, rosalina's dress; floating peepa's idle band-alpha is literally 0), and MatAnyone2's forward memory **progressively latches** onto constant input junk — penguin's "fades in slowly til loop". Segments are matted independently, so the flourish transition does not cause the idle artifact.

## Design

Port the kart blank-plate treatment with three char-specific substitutions. Everything is derived from the existing 153 standalone `char__costume` captures — no new capture, no engine changes, kart path byte-identical.

### 1. Two committed artifacts (extend `build_blank_plate.py`)

- **`assets/blank_plate_char.npy`** — the text-free char plate: masked median over ONE settled idle frame per standalone clip (`idle_frame()` semantics, all 153). Per frame, NaN the saturated-yellow live text (existing `nan_yellow_text` gates) **and NaN the character body** using the clip's current matte idle alpha (`matte/<name>__idle_frames/000.png`, alpha > 10, dilated 31) so standing characters cannot vote. Prototype-validated: bodies and text dissolve; min in-plate valid samples 13 (median 150); zero all-NaN pixels; the plain (no body exclusion) variant showed real body contamination in the diff, so alpha-exclusion is required, not optional.
- **`assets/clean_bg_char.npy`** — the LIVE clean backdrop behind the plate, replacing stale `char_A.png` in **all** char predark math (solve, classify, paint). Masked median over the plate-gone hold frames `[cut − 5, cut − 2)` of each non-fallback clip: the plate has slid out, the scene is still up, and the incoming kart-tag only starts at ~`cut − 1`. Body excluded via the clip's flourish matte last-3 alphas (they correspond exactly to those global frames, since the flourish export ends at `cut − 2`), dilated 31. `cut` comes from `find_segments`, cached per clip in `assets/char_cuts.json` so re-runs skip the decode (the full build decodes 153 clips ≈ 45 min one-time). Prototype (12 clips) validated; measured live level ≈ [65,69,69].
- Both artifacts also write a `.png` preview beside the `.npy` (kart-blank convention). `templates_meta.json` gains a `char_blank` section recording build date + clip count.

### 2. `_char_predark` in `matte_blankplate.py`

Mirror `_kart_predark` verbatim with the char arrays:

- `(T_B, C_B) = solve_tc(blank_plate_char, clean_bg_char)` — measured healthy on the prototype: in-plate t p5/50/95 = 0.42/0.71/1.6 (ratio-path dominated; both references are live-level so the solve is consistent).
- `opaque = blank-derived (T_B < T_OPAQUE) ∪ per-clip text mask` — the blank-derived part is ~724 residual dark-speck px (chars have no 1-UP badge; this is the analog of the kart `_BADGE`).
- Full-S stamp over the whole footprint, opaque painted to `clean_bg_char`, then the identical `FILL_K=51` close + ≤2000 px TELEA interior inpaint.
- Params: kart-locked `KEY_THR=120, CSUB=0.5, TFLOOR=0.01, FILL_K=51` (prototype sheets confirmed: rosalina's dress-over-plate is preserved with no razor cut; hem crust gone; no Mario; no bright text).

Module-level char setup block sits beside the kart one (same shape: solve once at import).

### 3. Per-clip text mask — HSV-yellow, NOT the kart t<0.2 gate

`char_text_mask(segment_median)` = HSV yellow (H 18–42, S > 150, V > 150 — the blank builder's proven detector) ∩ `TEXT_BAND`, dilated 7 (covers the AA ring + dark drop shadow; the close+TELEA fills the text-shaped hole downstream).

The kart's `solve_tc → t < T_OPAQUE` text gate **does not transfer**: it only works because the kart reference A is strongly tinted (B>G>R) and anti-correlates with yellow text (R>G>B), driving the per-pixel covariance t to 0. Against the neutral live char backdrop the solve falls into the ratio path (`sxx ≤ 1`), yellow solves to t ≈ 2.3 → clipped 1.6, and the text passes straight through (prototype-verified failure mode).

`TEXT_BAND` = rows spanned by the template glyphs (`t_char < T_OPAQUE` rows) ± 8, full footprint x-span (no badge cut — char plates have no 1-UP badge). `char_P.png`/`load_template(True)` remain in use for this geometry (and the footprint mask) only — never again for levels.

### 4. Flourish tail gate — `CHAR_PLATE_DEPART = 9`

Predark only flourish frames with global index `< cut − 9`; later frames pass RAW (the genuinely fading, detaching real plate — the matte's job, as with the kart flourish). Threshold-free recorder anchor, the exact analog of `KART_FADE_GAP = 27`.

Plumbing is segment-local so no absolute frame numbers cross module boundaries: the flourish export ends at `cut − 2`, so the rule is "the last **7** frames of a char flourish segment go raw". `CHAR_PLATE_DEPART = 9` lives in `extract_loop.py` beside the other recorder-timing constants (`CHAR_CUT_GUARD`, `KART_FADE_GAP`); the raw-tail count is derived, never hardcoded: `predark_raw_tail = CHAR_PLATE_DEPART - CHAR_CUT_GUARD` (= 7). `matte_loopframes(..., predark_raw_tail: int = 0)` → `_build_predark_frames` predarks `frames[:len − N]` and passes the rest raw; `process_all` passes the derived value for char flourish segments, 0 otherwise. Fallback flourishes (no hard cut found — 0/153 on the survey, but the path exists) keep predark-all, unchanged: those clips are already flagged `FALLBACK` for spot eyetest.

### Routing + retirement

`_build_predark_frames`'s char branch calls `_char_predark` instead of `pd.pre_darken`. `pre_darken.pre_darken()` then has no production caller: keep it (tuner + tests reference it) with a docstring note that it is legacy, superseded by `_char_predark`. `load_template` stays (footprint mask + text-band geometry).

## What does not change

The kart path (blank, text mask, predark, flourish predark-off) is byte-identical. `extract_loop` segmentation is untouched (`fe = cut − 2` already encodes the cut). The matte engine, viewer, and ship flow are untouched; the manifest schema gains one ADDITIVE key (`flourish_fallback`, plumbed by Task 10 of the plan — existing consumers read via `.get()`, so this is backward-compatible).

## Validation

- **Unit tests** (build python, no GPU): HSV text mask on synthetic yellow-text-on-plate; tail-gate partition (N raw tail frames, fallback → 0) ; `_char_predark` invariants on synthetic frames (footprint stamped, opaque painted to bg, inpaint only ≤2000 px components); builder NaN/median logic on synthetic stacks (body exclusion, all-NaN fill).
- **GPU sample validation before the batch**: re-matte rosalina/penguin/peepa + mario/luigi (idle + flourish) under `asset-venv-matte`; regenerate the diagnosis sheets (band zoom + alpha×3). Pass = no text ghost at any loop phase, no growth over the loop, no tail spike (peepa flourish band alpha stays ~0 through the tail), rosalina dress intact.
- **User eyetest gate**, then the full 153-item re-matte (~4 GPU-hr; kart combos untouched by construction).

Known accepted residual: a faint *textural* ghost (flat text-hole fill vs mottled recovery) at near-zero brightness delta — the same class the validated kart pipeline ships to the matte, which drops it. If the GPU sample disagrees, revisit (e.g. inpaint from stamped-S surroundings) — not built now.

## Notes

- **After any future crop change**, rebuild `blank_plate_char` + `clean_bg_char` alongside the kart blank (extends the existing rebuild rule).
- The old sweep's char mattes are invalidated by this change (153 standalone items only); the re-matte slots in before the pending full-sweep batch so chars are not matted twice.
- Survey + prototype evidence (2026-07-17 session scratchpad `verify/`): slide-onset table (12 chars), blank/bg diagnostics, old-vs-new predark sheets, tail-gate sheets. All load-bearing numbers are restated inline above.
