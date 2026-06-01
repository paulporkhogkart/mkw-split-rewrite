# Unified Edge-Match Selection Templates — Design

- **Date:** 2026-06-02
- **Status:** Implemented
- **Branch:** `selection-grayscale-templates` (off `capture-template-sources`)

> Supersedes the initial "grayscale+slack" design. We migrated char/kart/course to
> grayscale first, then — after measuring that grayscale cross-scores between similar
> names stayed high (Mario/Wario ~0.89, margin ~0.11) — switched **all four**
> categories to **Canny-edge** matching, which strips the shared name-plate
> background and triples the margins. This doc describes the final design.

## 1. Purpose

Match the four selection readouts (characters, karts, courses, costumes) with one
robust, background-agnostic method, and regenerate every template from the
full-screenshot `captures/` (produced by `mkw_tracker.tools.capture_sources`).

## 2. Why edges (not grayscale or binary)

The name ROI is mostly a shared UI plate with a thin text overlay, so whole-ROI
`TM_CCOEFF_NORMED` correlates highly between *different* names. Measured min margins
(self minus best wrong), with the live ±8px pad:

| category | grayscale | **edges** | wrong-pairs >0.7 (grayscale -> edges) |
|----------|-----------|-----------|---------------------------------------|
| characters | 0.113 | **0.389** | 139 -> 0 |
| karts | 0.203 | **0.484** | 10 -> 0 |
| courses | 0.148 | **0.337** | 6 -> 0 |
| costumes (per-character subset) | 0.109 | **0.613** | — |

Canny edges strip the smooth shared background (no gradient -> no edge) and key on
letter strokes, so similar names separate cleanly. This is the method costumes
already used; we extend it to all four and unify the pipeline.

## 3. Pipeline (one path for all four categories)

- **Template on disk:** grayscale ROI crop cut from each capture at the category's
  **settings** ROI (`char_name_roi` etc., same source the tracker reads).
- **Load:** `load_edge_template_groups` -> `prepare_text_edges` (Canny) per file,
  grouped by display name into `Dict[name, list[edge_template]]`.
- **Live:** `prepare_text_edges` of the ROI crop padded by `SELECTION_SEARCH_PAD=8`
  (the pad gives the ROI-size template room to slide, absorbing a few-px capture-
  setup offset — a 5px offset otherwise collapses the score).
- **Score:** `match_variants` takes, per name, the best score over its template list,
  returns `(best, score, scores_map)` with an incumbent-reconfirm hysteresis.

## 4. Costume background augmentation

Costume name banners vary at runtime (very bright / very dark / split), which
collapses a single edge template's score (self 1.0 -> ~0.3 when the live background
differs from the template's). Stress test (background swapped, matched against
templates cut on a different background): discrimination **never** broke (0 misranks
across dark/bright/split/gradient), but the score dropped a lot.

Fix: `synth_bg_variants` keeps the text (bright fill | dark outline mask) and swaps
the background, emitting `{"", bgdark, bgbright, bgsplit}` grayscale crops per
costume. `match_variants` takes the best, so a costume scores high whatever the live
background does. On a **held-out** gradient background, augmented self recovers to
~0.71 (vs ~0.44 single-template), 0 misranks. Char/kart/course sit on a stable dark
plate and are **not** augmented (one template each).

## 5. Thresholds (tuned from measured edge scores)

| knob | value | rationale |
|------|-------|-----------|
| `SELECTION_SEARCH_PAD` | 8 | slide room for the offset |
| `SELECTION_CHAR_FLOOR` | 0.60 | above worst char cross (~0.57); char also has a 5-frame streak |
| `SELECTION_KART_FLOOR` / `_COURSE_FLOOR` | 0.70 | single-frame commit -> above worst cross (~0.50 / 0.66) |
| `SELECTION_COSTUME_FLOOR` | 0.30 | soft: bg varies; discrimination (never a misread) protects it |
| `SELECTION_RECONFIRM_THRESHOLD` | 0.80 | above every char/kart/course cross-score, below self -> switch is never sticky |
| `SELECTION_COSTUME_RECONFIRM_THRESHOLD` | 0.50 | matches costumes' softer scores |

## 6. Components touched

- **New:** `scripts/gen_selection_templates.py` (cuts all four categories;
  costumes get bg variants), `tests/test_selection_matching.py`.
- **`detection/templates.py`:** add `_text_mask`, `synth_bg_variants`,
  `load_edge_template_groups`, `match_variants`; **remove** the
  dead binary path (`load_template_dir`, `prepare_roi`, `match_top_n`, `match_best`)
  and the short-lived `prepare_roi_gray`.
- **`detection/selection.py`:** load all four via `load_edge_template_groups`, match
  via `prepare_text_edges` + `match_variants`, unified `SELECTION_SEARCH_PAD`, new
  threshold constants; `Dict[name, list]` template type.
- **`tools/verify_captures.py`:** one edge + `match_variants` path for all categories.
- **`main.py`:** the `capture_asset_template` IPC now saves selection templates as
  grayscale crops (was binary for char/kart/course — would mismatch the edge loader);
  costumes also emit bg variants.
- **Regenerated:** `images/{characters,karts,courses,costumes}/en_uk/*.png`
  (50 + 40 + 30 + 39x4 = 276 files).

## 7. Validation

`tests/test_selection_matching.py`: pure units (`synth_bg_variants`,
`load_edge_template_groups`, `match_variants`), capture-backed top-1 discrimination,
shift robustness (5px offset, self stays >=0.8), Mario/Wario non-stickiness, costume
subset discrimination, and costume robustness under a **held-out** gradient
background. `verify_captures` reports all 159 en_uk captures clean.

**Honest limitation:** discrimination + the synthetic background stress test prove
separability and that the augmentation generalises; true cross-capture-card
robustness (a different card's colour/sharpness) is the inherited rationale and is
only fully provable on a second physical setup / the live feed.
