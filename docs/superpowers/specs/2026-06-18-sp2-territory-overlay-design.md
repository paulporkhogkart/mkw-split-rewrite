# SP2 — Territory Overlay (World Map) — Design

**Status:** design locked via visual brainstorm (companion session `5473-1781762885`, mockup `lens-focus-v4.html`). Ready for implementation plan.
**Part of:** the thekartoff.com territory map (SP1 map foundation = done; SP3 hover popup = done). This is SP2.
**Depends on:** SP1 `web/src/WorldMap.svelte` (`.base` img → `.territory` slot → `.icons`).

## 1. Goal

Paint each competitor's PB **territory** on the World Map: every land area is coloured by the player who holds the #1 PB on the nearest course, as a gap-free "gooey Voronoi" partition rendered in the **Strategy lens** style (chosen by the user from a 12-finish gallery). It must read as part of the map, not an overlay slapped on top.

## 2. Visual design (LOCKED)

The look is the "#12 Strategy lens" finish, tuned live by the user. Reference implementation: `lens-focus-v4.html` (the `paintLens` function + substrate build).

- **Partition:** every land pixel → nearest course (by centre) → that course's **owner** (#1 PB holder). Same-owner areas merge into one region. Boundaries are softened into organic curves with a **per-owner blur-argmax** of the owner masks (area-preserving "gooey" — NOT per-cell smoothing, which leaves gaps at triple points).
- **Fill (per land pixel):** the map terrain, dimmed, tinted toward the owner colour, with the terrain texture surviving as shading (so the land reads as *owned*, not covered). `colour = mix(terrain × DIM, ownerColour, tint)`, the tint leaning stronger toward the border.
- **Rim:** a bright owner-light rim along **every** region boundary, including the coastline. `colour = mix(fill, ownerLight, core·rimBright + halo·0.22)` where `core`/`halo` are smoothstep falloffs of the distance-to-border field.
- **Anti-aliasing (non-negotiable):** render the territory at **2× the display resolution** and high-quality-downscale in-canvas — this AAs the inner rims *and* the coast uniformly. The coast additionally uses a **feathered alpha** (see below) so it matches the inner rims exactly.
- **Ocean visible:** the territory is a **transparent** canvas layer over the real (muted) base map image; ocean shows the base map. The territory is full-strength on the island and feathers out at the coast.
- **Calm at rest:** base image muted (`saturate(.82) brightness(.82)`); territory + hovered icon lead.
- **No animation.** Static.
- **Icons:** unchanged from SP1 — the DOM `.icons` layer (live drop-shadow silhouette + lift/scale/pop on hover) sits on top of the territory.

### Locked constants (resolution-independent; fractions of map width W unless noted)

| Param | Value | Meaning |
|---|---|---|
| `DIM` | 0.40 | terrain brightness under the tint |
| `tint` | 0.40 | interior owner-colour tint (leans up near borders) |
| `rimBright` | 0.74 | rim mix toward owner-light |
| `rimWidth` | 0.0020·W | rim core falloff distance |
| `halo` | 0.0093·W | soft halo beyond the rim |
| `borderLean` | 0.0293·W | how far inward the tint leans toward the border |
| `gooeyRadius` | 0.014·W | per-owner blur-argmax radius |
| `coastSmoothing` | 0.0080·W | coastline shape rounding (baked into the asset, see §5) |
| `coastFeather` | 0.0020·W | coastline AA feather width |
| `ownerLight` | `mix(owner, white, 0.55)` | rim colour |

These are the user's dialled-in values (smoothing 12, edge-AA 3, brightness 74, width 3.0, halo 14, tint 40, border-lean 44, dim 40 at the mockup's 1500px internal scale). Treat them as the shipping defaults; expose only as code constants (no UI sliders in production).

## 3. Rendering pipeline (client)

The partition depends on **live** owners (PBs change), so it is computed on the client when the map view loads and the owner data arrives. There are no sliders in production, so this is a **one-time render** (no per-frame cost).

1. Load: the baked **island coverage** PNG (§5), course centres (`manifest.json`, already loaded by WorldMap), owners-per-course (§4), player colours (`/v1/roster`).
2. Compute (at 2×W internal res):
   a. `land` = coverage > 0.5; `coastCov` = coverage (0..1, the AA feather).
   b. nearest-course owner per pixel (land-independent), gated by `land`.
   c. gooey `ownerSm` via per-owner blur-argmax.
   d. border distance field `dB` via two-pass chamfer.
3. Paint the lens RGBA (alpha = `coastCov`, so ocean is transparent and the coast is AA); the ~2px ocean-side feather borrows the nearest owner so the rim AA-bleeds into the water.
4. High-quality downscale into the `.territory` `<canvas>` (2× → display).

**Off the main thread:** the compute+paint (~1s at 2×) runs in a **Web Worker with OffscreenCanvas**; the result is transferred back as an `ImageBitmap` and drawn into `.territory`. The map shows its existing loading state until ready. (Fallback: main-thread compute behind a `requestIdleCallback` if Workers/OffscreenCanvas are unavailable.)

`web/src/lib/territory.js` holds the pure compute (testable); the Worker wraps it.

## 4. Data model — owners per course

Each course's owner = the #1 PB holder for the **active season, cc 150** (matches `temp/mk_territory_json.py`). The existing reads only do per-course (`/v1/leaderboard`) or overall (`/v1/leaderboard/overall`) — there is **no bulk per-course-owner read**.

**Add one read** (server-authoritative, one SQL query):

- `pi/src/db/reads.ts: territoryOwners(db, seasonId, cc)` → for every course: `{ course_id, slug, owner_player_id, owner_name, color, t1_ms, t2_ms, wr_ms }` (owner = min `total_time_ms` where `is_pb=1`; null when unclaimed).
- `pi/src/api/reads.ts: GET /v1/territory?cc=150` (season via the usual `season(c)` helper).
- **Must be public (token-free) + CORS**, like the other 3 public reads (`/v1/leaderboard`, `/v1/world-records`, `/v1/roster`) — see the SP3 data-access note. Add `/v1/territory` to the same open list in `pi/src/api/app.ts`.
- Colours: prefer `players.color` (server wins), as `/v1/roster` already does.

Unclaimed courses (no PB) are **not seeds** — their area falls to the nearest claimed course (gap-free partition over claimed owners only). With current S1 data this yields a Gub-dominant map (24/30) + Paul (6); correct and expected.

## 5. Island coverage asset (build-time)

The island shape is the user's hand-traced mask (`island_mask3.png`, currently only in the brainstorm/temp dirs). Ship a **baked coverage** so the client does no mask smoothing:

- Add `scripts/map/build_island_coverage.py`: take the traced mask/polygon → **shape-smooth** (double box-blur radius `coastSmoothing`·W then threshold 0.5) → **feather** (box-blur radius `coastFeather`·W) → write grayscale **`web/public/map/island.png`** (same 2200×1775 frame as `base.jpg`).
- Client reads it once: `land = px > 127`, `coastCov = px / 255`. No runtime smoothing.
- Re-running the script with a different radius is how the coastline smoothing is re-tuned later (it was a slider during design; it is an art constant now).

## 6. Files

**New**
- `web/src/lib/territory.js` — pure partition + lens paint (nearest-owner → gooey blur-argmax → chamfer dB → lens RGBA). No DOM.
- `web/src/lib/territoryWorker.js` — Worker wrapper (OffscreenCanvas, ImageBitmap transfer).
- `scripts/map/build_island_coverage.py` — bakes `web/public/map/island.png`.
- `web/public/map/island.png` — baked coverage asset (committed).

**Changed**
- `web/src/WorldMap.svelte` — fetch `/v1/territory` + `/v1/roster`, kick the worker on mount, draw the result into the existing `.territory` canvas; loading/empty states.
- `web/src/lib/api.js` — add `territoryUrl()` (or fetch helper), defaulting like the other reads.
- `pi/src/db/reads.ts` — `territoryOwners()`.
- `pi/src/api/reads.ts` — `GET /v1/territory`.
- `pi/src/api/app.ts` — add `/v1/territory` to the open + CORS read list.

## 7. Testing

- `web` vitest on `territory.js`: gap-free (every land pixel assigned), owner = nearest claimed course, deterministic output for a fixed fixture, unclaimed-course fallback. Use a small synthetic coverage + a 5-owner fixture (the illustrative split).
- `pi` vitest on `territoryOwners()`: correct #1 per course, colour resolution, unclaimed → null, season/cc filtering.
- Visual: **headless-Edge screenshot, never OpenCV** (browser is ground truth for compositing/scaling). Confirm AA coast + inner rims, ocean visible, icons hover.

## 8. Deferred / out of scope

- **Strength / dominance** ("no strength for now"): later, modulate `tint`/`rimBright` by `fireModel.dominance` (the SP3 model — define once, reuse). Not in this build.
- **SP4 timeline scrubber** (replay territory over PB history): separate sub-project; depends on this.
- Contested/mixed colours: deferred with strength.

## 9. Open decisions (recommended defaults chosen)

1. **Owner data source** → *new `/v1/territory` bulk read* (recommended; one query, server-authoritative, matches existing read style). Alternative was 30 client-side `/v1/leaderboard` fetches — rejected (chatty, assembles on the client).
2. **Coastline smoothing** → *baked into `island.png` at build* (recommended; zero runtime mask work). Alternative was runtime smoothing — rejected (heavier client compute, re-derives an art constant every load).
3. **Compute location** → *Web Worker + OffscreenCanvas* (recommended; keeps the ~1s build off the main thread). Main-thread fallback documented.
