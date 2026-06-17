# World Map — SP1: Interactive map foundation + course identity

**Date:** 2026-06-17
**Status:** Design — awaiting review
**Surface:** `web/` (the public thekartoff.com SPA)

## Project context (the bigger picture)

The end goal is an **interactive territory map** on thekartoff.com: a Mario Kart World map where each
competitor's PB dominance paints **territory** (color oozing from each course they control, connecting nearby
holds), strength tied to WR-proximity + margin over rivals, contested courses muted/mixed, a **season-0→now
timeline** scrubber, and a **hover popup** with the player's GIF and that track's leaderboard.

That is far too large for one spec, so it is decomposed into four sub-projects, each with its own spec → plan →
build:

- **SP1 — Map foundation + course identity** *(this spec)* — the interactive, labeled map substrate.
- **SP2 — Territory overlay** — per-course controller + strength → color-bleed field (present state).
- **SP3 — Hover popup** — animated popup: player-GIF strip + that track's leaderboard.
- **SP4 — Timeline scrubber** — replay territory/leaders across all PB history.

Build order: SP1 → SP3 → SP2 → SP4. Dependencies: everything needs SP1's labeled map; SP4 needs SP2.

**This spec covers SP1 only.** SP2–SP4 are out of scope here; SP1 must merely leave clean seams for them.

## Goal & scope (SP1)

Ship a **World Map** view in the web app: the real MKW map rendered as a polished, restrained content surface,
with all **30 courses individually interactive** (hover lifts/grows/glows the icon) and **correctly labeled**
to their canonical course slugs. It must sit cohesively inside the existing pbenguin "Neutral Graphite" UI.

**In scope:** the production asset pipeline, the committed map assets + manifest, the new view + routing, the
`WorldMap.svelte` component with calm-at-rest + living-icon hover, and the layer seams for SP2/SP3.

**Out of scope (deferred):** any server data, territory color (SP2), the GIF/leaderboard popup (SP3), the
timeline (SP4). Click on a course is a no-op in SP1.

## Design decisions already validated (via the visual-companion mockups)

- **Hybrid map:** the user wants "living icons" (the icon itself lifts on hover) but on the *pretty* hi-res
  art, not the flat stage-less base. Solution: cut each icon **natively from the hi-res map** and overlay it in
  place; on hover it grows + lifts.
- **No inpaint needed.** The hover is **grow-dominant** (scale 1.18 with only a -4% rise), so the enlarged
  sprite always fully covers its own original footprint — the baked icon underneath never peeks. The base is
  therefore the *unmodified* hi-res map. (Inpainting the icons out remains a documented fallback, and SP2 may
  later want a cleaned base for a tidier color field.)
- **Calm at rest.** Base + icons sit slightly muted (de-saturated/dimmed); the hovered course pops to full
  color. Chosen because the territory colors (SP2) become the primary visual layer, so the base should stay
  quiet. (A "vivid" mode is not shipped; calm is the single look.)
- **Framed like the OBS "feed."** The colorful map is treated as *content*: a near-black bezel
  (`--feed-bg #0b0c0e`), hairline `--bd` border, 4px radius, soft inner vignette. The surrounding chrome stays
  graphite. This is how a bright Nintendo map coheres with the restrained UI.
- **Rainbow Road** is absent from every base layer; it was added from the supplied icon and placed at
  normalized center **(0.500, 0.734)**, width **0.089** of the map.
- **Labeling is done.** All 30 icons are bound to canonical slugs (`server/courses.py:CANONICAL_COURSES`),
  validated as 30 unique slugs covering the full set.

## Source material & terminology

`temp/map/` holds the working sources (to be promoted into the repo, see Pipeline):

| File | What it is | Note |
|------|-----------|------|
| `MarioKartWorld_World_Map_Inner.webp` | Stage-less base map | actually **AVIF**; decode with ffmpeg |
| `MarioKartWorld_World_Map_Stages.webp` | Course icons on black, pixel-aligned to Inner | actually **AVIF** |
| `highresmap.jpeg` | Pretty baked map (icons included) — the production base | 3135×2350 |
| `MKWorld_Icon_Rainbow_Road.webp` | Rainbow Road icon (real WebP, has alpha) | 450×350 |
| `messymap.jpg` | Labeled fan map (names every course by position) | **labeling reference only** |

- **30 courses** = 29 auto-detected on the Stages layer + Rainbow Road.
- **slug** = the stable cross-system key (e.g. `bowsers_castle`), matching `images/courses/<lang>/<slug>.png`
  and the server's `courses.slug`. Display names carry curly apostrophes ("Wario's Galleon").

## Asset pipeline

A single committed script regenerates everything deterministically. Consolidates the throwaway
`temp/map/prep_mockup.py` + `prep2.py` exploration into one production builder.

**Location:** `scripts/map/build_map_assets.py`. **Sources committed** under `scripts/map/sources/` (the two
AVIF layers, `highresmap.jpeg`, the RR WebP — all small) plus `scripts/map/labels.json` (the icon→slug map as
`[{slug, cx, cy}]` normalized centers, exported from the completed labeling).

**Steps:**
1. Decode the AVIF layers → PNG via ffmpeg (documented build-time dependency; only needed to *regenerate*
   assets, not to build the site).
2. Detect icon boxes on the Stages layer (grayscale > threshold → morphological close → connected components;
   drop the full-frame noise blob and sub-threshold specks). Expect 29.
3. Estimate a similarity transform Inner→hi-res (ORB + `estimateAffinePartial2D`), then **per-icon snap**:
   slide each stage-icon crop (masked) in a small window around its predicted hi-res position to get a precise
   center. (Validated: tight, every icon centered.)
4. **Cut each sprite natively from the hi-res map** at the snapped box, with a soft alpha matte derived from a
   smoothstep on the stage-icon brightness. Crisp, perfectly color-matched.
5. **Rainbow Road:** take the supplied icon, place at the locked normalized rect.
6. **Assign slugs** by nearest labeled center (`labels.json`) — robust to any change in detection order.
7. Emit at **~2× display resolution** (base ≈ 2200px wide so it stays crisp up to ~1100px CSS width on retina;
   sprites cut at native hi-res, not downscaled below 2×).

**Outputs → `web/public/map/`:**
- `base.jpg` — the hi-res base (unmodified; quality ~88).
- `sprites/<slug>.png` — 30 transparent icon sprites, named by slug.
- `manifest.json` — `{ base:{w,h}, courses:[ {slug, name, hit:{x,y,w,h}, spr:{x,y,w,h}} ] }`, all rects
  normalized 0–1 to the base. `hit` = the pointer target (tight box); `spr` = the (padded) sprite placement
  rect. Both let the frontend position with pure percentages, resolution-independently.

**Validation (run in the build script + a test):** exactly 30 courses; 30 unique slugs == the canonical set;
every `sprites/<slug>.png` exists; every rect inside [0,1].

## Web integration

The web app is currently a single page (`web/src/App.svelte` = header + `CardWall`). It already imports
`../../src/theme.css`, so all pbenguin tokens are available globally.

**Routing.** Add a lightweight hash-based view switch in `App.svelte` (`#/` = Live, `#/map` = World Map) with a
small nav in the header (`Live` | `World Map`). Default = Live (the card wall) — the map is additive. No router
library; a `hashchange` listener mapping to a `view` variable is enough for two views.

**Components.**
- `web/src/WorldMap.svelte` — the view. Renders the feed frame and, inside it, a stack of layers:
  1. `<img class="base">` (the base map),
  2. **territory layer** — an empty positioned `<div class="territory">` placeholder (SP2 fills it),
  3. **icon layer** — 30 positioned course elements,
  4. **popup layer** — an empty positioned `<div class="popups">` placeholder (SP3 fills it).
- `web/src/lib/map.js` — imports/holds the manifest and small helpers (rect→CSS%, relative sprite rect).

**Each course element:** an absolutely-positioned hit `<div data-slug>` at `hit` (%), containing the sprite
`<img>` placed at `spr` relative to the hit box (so a CSS `:hover` scales the child). `transform-origin`
bottom-ish; hover applies `scale(1.18) translateY(-4%)` + drop-shadow + brightness/saturate. `z-index` raises
on hover. Click: no-op in SP1 (handler stub, `data-slug` ready for SP3).

**Calm-at-rest:** the icon layer + base carry a muted filter at rest (`saturate(~.82) brightness(~.9)`); the
hovered course returns to full color (and a touch brighter) as it lifts. Exact values lifted from the
validated mockup, tuned live.

**Responsive:** the map scales by width inside the feed frame (% positioning handles all icons); the header nav
+ chrome stack on narrow screens. The map has a sensible max display width (~1100px) and centers.

## Data flow

SP1 is **static**: `manifest.json` (+ committed images) → `WorldMap.svelte` renders. No server calls, no
stores. (SP2–SP4 introduce token-gated reads from the Pi API: `/v1/leaderboard`, `/v1/roster`, WR, PB history.)

## Error handling

- Manifest/image fetch failure → the frame shows a quiet "map unavailable" message; the rest of the site is
  unaffected.
- A missing sprite for a slug → that icon is skipped (logged once), map still renders.
- These are committed static assets, so failures are effectively build-time; the build-script validation is the
  real guard.

## Testing

- **Build script:** a Python test (or an in-script `--check`) asserting the manifest invariants above against a
  freshly built manifest (30 / unique / canonical / files exist / rects in range).
- **Frontend (vitest):** unit-test `lib/map.js` — rect→`%` formatting, the relative-sprite-rect math, and that
  the manifest yields 30 courses with the expected slugs; test the hash→view mapping. A component test that
  `WorldMap` renders 30 `[data-slug]` hit elements and toggles the hover class.
- **svelte-check:** 0 errors / 0 warnings.
- **Build:** `npm run build` (web) green.
- **Manual:** the user live-tests the view in the browser (hover feel, calm-at-rest, cohesion, label
  correctness).

## Open items / notes for review

- **Source/regeneration dependency:** regenerating assets needs ffmpeg (AVIF decode). Acceptable, since only
  asset regeneration needs it — shipping/building the site does not. Alternative: commit pre-decoded PNG layers
  (larger repo). *Recommendation: keep the small AVIF/WebP sources + ffmpeg.*
- **Sprite naming** by slug (not `hi_NN`) — chosen for legibility and because labels are final.
- **Inpaint** intentionally omitted (grow-dominant hover). Re-evaluate in SP2 if a cleaned base reads better
  under the color field.
- **Hover vs. popup (SP3):** SP1 hover = lift only. When SP3 lands, decide whether the popup opens on
  hover-dwell or click; SP1's click stub + `data-slug` keep that open.
- The live card wall stays the default landing view; whether the map later becomes the primary landing page is
  a future call, not SP1's.
