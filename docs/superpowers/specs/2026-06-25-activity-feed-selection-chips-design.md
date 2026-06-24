# Activity-feed selection chips — design

**Date:** 2026-06-25
**Status:** approved, pre-implementation

## Context

The website activity feed (`web/src/ActivityLog.svelte`, the `/live` page) renders plain
text rows: `when · who · where · what`. We want to extend the relevant rows with small,
stylish **game-HUD-style chips** showing the character / kart / course (and costume) for
that line — a parallelogram "icon" cluster at the right edge of the row (hand-drawn target:
`temp/stylechipdesign.png`).

The chip images come from real in-game frames. The existing capture tool
(`mkw_tracker.tools.capture_sources`) already saves one full 1920×1080 screenshot per
character/costume/kart/course into `captures/en_uk/<category>/` — those exist and aren't
lost. Two gaps: (1) we have **no character×costume combos** (one shot per character name,
one per costume name — never "Peach in Aero"), and (2) the existing captures are
**HDR-tinted** (washed colors), unusable as display assets.

## Goals

- Capture clean **SDR** frames of every character×costume **combo**, plus karts and courses.
- A crop-authoring tool with a **live chip preview** to frame each item by eye, exporting a
  version-controlled crop spec so chips are regenerable forever from the full frames.
- Surface character/kart/costume on run-based feed events so result lines can show all chips.
- Render the chip cluster in the feed, matching the sketch.

## Non-goals

- No re-capture of the HDR template set; detection is grayscale/edge-based and unaffected.
- No costume fidelity beyond "the exact captured frame" (no compositing).
- Chips on `presence` (login/logout) rows.

## Decisions (locked)

- **Full costume fidelity, all combos captured upfront** (user's call); base look = the
  `<char>__base` combo (default outfit), which is also the render fallback.
- **HDR off on the Switch** for the asset-capture session → into a fresh `captures_sdr/`
  folder, leaving the HDR template set untouched.
- **Crop spec JSON + full SDR frames = source of truth**; chips regenerate from them.
- **Rectangular crops; parallelogram shape applied by CSS** at render (restylable without re-crop).
- **Canonical slug = `pi/src/db/slug.ts:slugify()`** (drops apostrophes → `bowsers_castle`).
  The existing capture filenames already match it; the export must not diverge.

---

## Piece 1 — Asset pipeline (capture → crop → chips)

### 1A. Combo capture mode — extend `mkw_tracker/tools/capture_sources.py`

On `CHARACTER_SELECT` the tracker already reports `character` + `costume` simultaneously
(`_SCREEN_FIELDS`), and the hero render shows the exact outfit, so **one frame per pair** is
the chip source. Add a combo path to `CaptureGate`:

- A pair fires when **both** `character_conf` and `costume_conf` ≥ `min_conf` and the
  *(character, costume)* pair is stable for `hold` frames (mirror the existing per-field
  streak logic, keyed on the joined base).
- Save to `captures_sdr/<lang>/combos/<char_base>__<costume_base>.png` (full frame).
- Default outfit reports costume "Base" → `<char>__base.png` (the fallback asset).
- Karts → `captures_sdr/<lang>/karts/<base>.png`, courses → `.../courses/<base>.png`
  (existing fields, new out-dir). Add `--combos` and have `--out captures_sdr` default for
  this mode.
- HUD: show combo coverage (count captured; combos can't be pre-enumerated, so it's a live
  counter, not a fixed checklist). Resume-from-disk already handles re-runs.

Keep all decision logic in the pure, unit-tested `CaptureGate`; the camera/HUD shell stays
hardware-only. New unit tests cover the pair-gate (fires once per pair, dedups, respects
`min_conf`/`hold`, "Base" handling).

### 1B. Crop tool — `tools/chip-cropper.html` + `scripts/chip_cropper_server.py`

A tiny stdlib `http.server` (mirrors the project's Python-tooling style; avoids
File-System-Access-API quirks) that:
- serves `captures_sdr/` thumbnails + the HTML,
- serves the current `tools/chips.crops.json`,
- accepts a POST to persist it.

The HTML tool (mirrors the `temp/wordmark-editor.html` → JSON workflow):
- Iterates every capture (combos, karts, courses) with a **draggable, fixed-aspect crop box**.
- **Live chip preview using the real chip CSS** (`clip-path` parallelogram) so what you frame
  is what ships.
- **Per-character default crop box** inherited across that character's combos, with **per-combo
  override** (a combo with no override uses the character default; "reset to default" clears it).
- Karts framed per-item; courses default to the shared portrait region (top-left on
  COURSE_SELECT), adjustable.
- Writes `tools/chips.crops.json`: `{ combos: { "<char>__<costume>": {x,y,w,h} }, karts: {...},
  courses: {...}, defaults: { character: { "<char>": {x,y,w,h} }, course: {x,y,w,h} }, meta:
  { crop_aspect, chip_px } }`.

### 1C. Export — `scripts/gen_chips.py`

Pure-core + thin-shell (project convention). Reads `tools/chips.crops.json` + `captures_sdr/`,
resolves each item's rect (override → character-default → category-default), cuts and resizes
to the standard chip size (~2× display, e.g. 96px tall) and writes **rectangular** PNGs to:

- `web/public/chips/combos/<char>__<costume>.png`
- `web/public/chips/karts/<kart>.png`
- `web/public/chips/courses/<course>.png`

**Not Git LFS** (Pi-deploy LFS gotcha — would serve pointer stubs). Output slugs derived to
match `slugify()` exactly; a unit test slugs known apostrophe names (`Bowser's Castle`,
`Wario's Galleon`) on the Python side and asserts equality with the locked TS rule.

### Storage

`captures_sdr/` is bulky (hundreds of full frames) and **never served by the Pi** (it serves
`web/` + runs `pi/`, and the deploy has no `git lfs pull`), so track it via **Git LFS** (or
local-only) — LFS pointer-stubs on the Pi are harmless, and we keep full frames for "redo later".
Only the small `web/public/chips/*.png` + `tools/chips.crops.json` are committed normally.

---

## Piece 2 — Server: surface character/kart/costume on run events

`pi/src/activity/cascade.ts:buildRunCascade()` builds `pb` (and `rank`) events but
`RunCascadeArgs` lacks the run's character/kart/costume. Change:

- Extend `RunCascadeArgs` with `characterSlug | kartSlug | costumeSlug` (nullable), slugified
  via `slugify()`, populated at **both** call sites:
  - `pi/src/api/runs.ts:110` (live ingest) — from the posted run payload `p`.
  - `pi/src/activity/backfill.ts:90` (historical) — from the run row's `character`/`kart`/
    `costume` columns (add them to the `RunRow` SELECT if not already projected). Without this,
    backfilled rows render chip-less.
- Add them to the `pb` and `rank` payloads (the mover's run is the subject of both).
- They persist in `activity_events.payload`, so `recentActivity()` returns them for both live
  and backfilled events with no read-path change.
- `turf_*` / `wr` carry course only for v1 (course is already on the event row).

Tests: extend the cascade unit tests to assert the three slugs ride along on `pb`/`rank`.

---

## Piece 3 — Frontend: render the chips

### Data → chips — `web/src/lib/chips.js`

- `chipsFor(event)` → ordered `[{category, slug}]`:
  - `pb`, `rank` → `combos/<characterSlug>__<costumeSlug>`, `karts/<kartSlug>`,
    `courses/<courseSlug>` (skip any missing slug).
  - racing `session` → character combo + course.
  - `turf_*`, `wr` → course only.
  - `presence` → none.
- `chipUrl(category, slug)` → `/chips/<category>/<slug>.png`. Combo fallback: if a combo asset
  is absent, fall back to `<char>__base`; `<img onerror>` hides a chip whose asset is missing,
  so partial coverage degrades cleanly.

### Render — `web/src/ActivityLog.svelte` + `Chip` markup

- A right-edge **chip cluster** per row. Cleanest fit for the existing 4-col grid
  (`112px 74px 150px 1fr`): a `margin-left:auto` flex element inside `.what` (text left,
  chips pushed right), so no grid surgery.
- Each chip: `<img object-fit:cover>` clipped to a parallelogram with `clip-path`
  (image upright, frame slanted — matches the sketch), subtle 1px border + drop-shadow for the
  HUD-sticker pop. ~26–30px tall display, ~−16° lean.
- **Starting style only** — final shape/skew/border tuned live in the cropper preview (same CSS).

Tests: `chips.test.js` covers `chipsFor` per event type and `chipUrl`/fallback slug building.

---

## Data flow

```
in-game (HDR off) ──capture_sources --combos──▶ captures_sdr/<lang>/{combos,karts,courses}/*.png
captures_sdr ──chip-cropper.html──▶ tools/chips.crops.json ──gen_chips.py──▶ web/public/chips/**.png
run ingest ──slugify──▶ cascade pb/rank payload ──▶ activity_events.payload ──/v1/activity──▶ store
ActivityLog row ──chipsFor/chipUrl──▶ <Chip clip-path> images from /chips/**
```

## Error handling / degradation

- Missing chip asset → `onerror` hides that chip; row text unchanged.
- Missing slug on an event (older backfilled rows) → that chip is simply not requested.
- Combo not yet captured → falls back to `<char>__base`, else hidden.
- Failed capture save already retries (existing `gate.unmark`); export skips items with no
  capture and logs them.

## Testing

- Python: `CaptureGate` pair-gate unit tests; `gen_chips.py` core (rect resolution + slug
  consistency) unit tests.
- Pi: cascade payload tests assert character/kart/costume slugs on `pb`/`rank`.
- Web: `chips.test.js` for `chipsFor`/`chipUrl`; `svelte-check` clean.
- Manual: HDR-off capture session → crop a handful → `gen_chips.py` → `web` dev server, confirm
  chips render on real feed rows and degrade when an asset is absent.

## Build order

1. **1A combo capture mode** first — it unblocks the long-pole SDR capture grind, which can run
   in parallel with everything below.
2. 1B crop tool + 1C export.
3. Piece 2 server payload.
4. Piece 3 frontend rendering, then live-tune the chip style in the cropper.

## Deferred / open

- Character chip on `turf_claim` (claimer's run) — needs the leader's run joined; v1 = course only.
- Costume-accurate compositing if a combo is never captured (not planned; base fallback suffices).
- Desktop `EventLog` chips (out of scope; website only).
