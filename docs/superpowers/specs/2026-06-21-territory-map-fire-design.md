# Territory map: "on fire" flames for dominated courses

**Date:** 2026-06-21
**Scope:** `web/` frontend + one small `pi/` server read extension.

Substantial-lead ("on fire") courses should visibly burn on the Territory map, so a
glance shows which regions are dominated — reusing the existing flame engine
(`Fire.svelte` / `WordmarkFire.svelte`) and the existing on-fire formula
(`fireModel.js`). Fire tracks the shown frame: it follows the timeline scrubber back
through history, not just the live present.

Builds directly on the territory history work (`2026-06-21-territory-history-and-polish-design.md`):
that branch added `leaderboardAt(events, slug, t)` and retained the timeline event stream
(`tlEvents`) + colour map (`tlColors`) on `WorldMap`, both of which this feature consumes.

## Background

- **The flame engine.** `src/components/Fire.svelte` (player cards, 56x150 column) and
  `web/src/lib/WordmarkFire.svelte` (logo, wide box) are the same technique: metaball
  ellipses animated by one `requestAnimationFrame` loop, blurred + thresholded through an
  SVG goo filter, hued from a brand colour via a 4-stop HSL palette, with a BACK layer
  (behind) + a screen-blended FRONT layer + a glow. Each is a per-instance component with
  its own rAF, and the layer geometry is authored per use-case. Both honour
  `prefers-reduced-motion` (WordmarkFire explicitly).
- **The on-fire formula.** `web/src/lib/fireModel.js` `isOnFire({ t1, t2, wr })` — true when
  the leader's margin over #2 clears an exponential bar that rises the further the leader's
  time sits off the WR. Needs a real #2 and a WR; returns false otherwise. This is the single
  source of truth and is reused verbatim (no porting to the server).
- **The map's two renderables.** Course **icons** are DOM `<img class="spr">` sprites inside
  absolutely-positioned `.hit` divs (geometry via `map.js` `hitStyle(hit)`, where
  `hit.x/y/w/h` are fractions of the map frame). The **territory regions** are pixels painted
  on a `<canvas class="territory">` by a Web Worker (`territoryWorker.js`) on scrub/change —
  NOT a per-frame animation loop.
- **Frame sources (already in `WorldMap.svelte`).** LIVE territory comes from `/v1/territory`
  (`territoryOwners`, active-season PBs) rendered to `presentBitmap`. Historical frames come
  from `tlEvents` (the full S0+S1 finished-run stream, carryover excluded) via ownership
  snapshots; `snapshots[tlIndex].t` is the shown moment. The shown frame is LIVE when
  `tlIndex` is the last snapshot (or no timeline loaded), else historical.

## Approach

Flame as a **DOM overlay anchored to each on-fire course's icon**, driven by **one shared
rAF loop**, with the on-fire set **re-derived for whichever frame is shown**.

Rejected alternatives:
- Painting flame into the territory canvas — it is worker-rendered on demand, not a per-frame
  loop; adding animated fire there is a large, fragile change.
- One `Fire.svelte` instance per on-fire icon — up to ~30 independent rAF loops in a
  dominant-player season; a perf trap.

## Design

### 1. Server — on-fire *inputs* on `/v1/territory` (small)

Extend `territoryOwners` (`pi/src/db/reads.ts`) so each course row also carries:
- `leader_ms` — the rank-1 (owner) `total_time_ms` (= t1), or null
- `second_ms` — the rank-2 `total_time_ms` (= t2), or null
- `wr_ms` — the current WR `record_ms` for that course+cc (or null)

The query already partitions by course ordered by time and takes `rn=1`; extend it to also
read `rn=2`, and LEFT JOIN the current WR (`world_records WHERE is_current=1`). The existing
owner/name/colour fields are unchanged. `TerritoryOwner` gains three nullable numeric fields.
No formula on the server — just the three numbers, so `isOnFire` stays the one formula.

The `wr_ms` returned here is the **current** WR. It is the only WR source this feature has,
and it is reused for historical frames too (see scope).

### 2. Client — derive the on-fire set for the shown frame

`WorldMap.svelte` already fetches `/v1/territory`. From that response build, once,
`wrBySlug = { slug -> wr_ms }` (current WR per course), kept for the historical path.

On-fire set is recomputed whenever the shown frame changes (`tlIndex` change, or LIVE load):

- **LIVE** (`tlIndex` at last, or no timeline): per course, `isOnFire({ t1: leader_ms,
  t2: second_ms, wr: wr_ms })` straight from the `/v1/territory` rows. Uses the same
  leader source as the LIVE territory colours, so fire matches what is drawn.
- **Historical** (`tlIndex` < last): per course, `t1`/`t2` = the two smallest times in
  `leaderboardAt(tlEvents, slug, snapshots[tlIndex].t)` (`[0].ms`, `[1].ms`), `wr` =
  `wrBySlug[slug]`. Uses the same `tlEvents` leader source as the historical territory
  colours, so fire matches that frame. A course with fewer than two competitors by then has
  no `t2` -> not on fire.

### 3. Pure helper (`web/src/lib/`, unit-tested)

`onFireCourses(entries)` -> the subset of `entries` where `isOnFire({ t1, t2, wr })` is true,
each entry returned unchanged (so render fields ride along). `entries` is
`Array<{ slug, t1, t2, wr, hit, color }>` — `t1/t2/wr` feed the formula; `slug/hit/color` are
passthrough the renderer needs (returning the filtered entries, not bare slugs, avoids a
re-lookup of `hit`/`color`). Two thin call-site adapters build `entries`: from `/v1/territory`
rows (LIVE; `color` = row colour) and from `tlEvents` + `wrBySlug` at a time `t` (historical;
`color` = `tlColors[owner]`, `hit` from `manifest.courses` by slug). The historical adapter
may reuse `leaderboardAt`; it only needs the top two, so it can stop early, but a full
`leaderboardAt` call per course is also acceptable (sub-ms over ~2.5k events x ~30 courses).

### 4. Visual — `MapFireLayer.svelte` (one shared rAF)

A new overlay component placed in the stage **between `.territory` and `.icons`** (so flame
sits behind the sprites and the icons stay legible), `position:absolute; inset:0;
pointer-events:none`.

- **Input:** the current on-fire list — the `onFireCourses(...)` output, each item
  `{ slug, hit, color, ... }` (`hit` the course's normalized box; `color` the leader's colour;
  the `t1/t2/wr` fields ride along unused by the renderer).
- **Rendering:** reuses the metaball + goo-filter + HSL-palette technique from `Fire.svelte`,
  but consolidated: ONE rAF loop animates the blobs for ALL on-fire courses. Each course gets
  a compact flame column hued by its leader's colour, anchored to its `hit` box (rising a
  little above it), plus a soft per-course glow. A flame sits **behind** the icon (BACK-style
  column); no heavy FRONT lick, to avoid obscuring the sprite and to keep many simultaneous
  flames from cluttering the map.
- **Binary on/off** per course (matches the card/logo `active` model). Courses fade in when
  they ignite and fade out when they extinguish (a short opacity transition, like the card's
  0.45s ignite), so scrubbing/among-frame changes are not hard pops.
- **`prefers-reduced-motion`:** no rAF; show a static low-flame/glow (as WordmarkFire does).
- **Exact flame shape, density, height, and glow are tuned live during the build**, the same
  way the card and logo flames were (a temp HTML tuner / the visual companion). The component
  exposes its layer constants for that tuning.

### 5. Reactivity and the play loop

The on-fire list is reactive to `tlIndex` (and the initial LIVE load). As the user scrubs,
regions ignite/extinguish to reflect that moment — the intended "watch dominance build"
effect. During **playback** (the animate-through-history loop), `tlIndex` steps frame to
frame; the flame layer updates per step. If that reads as too busy, the fade timing is the
tuning lever (and, if needed, the layer can update only on settled frames) — a live-tuning
call, not a structural one.

### 6. Scope — LIVE + historical, current WR

Fire shows on every frame (live and scrubbed-back). Historical frames use the **current** WR
(`wrBySlug`) because no historical WR exists yet. The WR input is a single per-course value
at the point of the `isOnFire` call, so when historical WRs are scraped it becomes a
time-indexed lookup with no structural change — the same swap-in point already documented for
the historical leaderboard popup.

## Performance

- One shared rAF for all flames (not one per icon).
- On-fire recompute is cheap and only on frame change (not per animation frame).
- Historical adapter needs only the top two times per course; full-sort is acceptable but
  early-stop is the optimization if profiling ever calls for it.
- Fades avoid pops without extra loops (CSS opacity).

## Testing

- `onFireCourses(entries)` — pure, unit-tested: on-fire vs not (clear lead over #2 off the WR
  vs marginal), missing #2 -> not on fire, missing WR -> not on fire, empty input; returned
  entries retain their passthrough `slug`/`hit`/`color`.
- The historical adapter (events + `wrBySlug` + `t` -> entries) — unit-tested for the
  cutoff/top-two behaviour (leans on `leaderboardAt`, already tested).
- Server: extend the existing territory read test to assert the new `leader_ms` / `second_ms`
  / `wr_ms` fields (including null `second_ms` when a course has one competitor, null `wr_ms`
  when no current WR).
- `MapFireLayer` animation: svelte-check + build + manual, as with the other Fire components.
- Manual: at LIVE, dominated courses burn in their leader's colour; scrub back into a past
  reign and watch the on-fire set change to match that moment's territory; a course with a
  single competitor never burns; reduced-motion shows a static glow.

## Out of scope

- Intensity-scaling (flame height proportional to how far the lead clears the bar). v1 is
  binary on/off. Noted as a future enhancement; `fireBarPct` already gives the metric.
- Region-fill fire painted into the territory canvas (the rejected approach).
- Historical WR (current WR used; documented single swap-in point above).
- Any change to `Fire.svelte` / `WordmarkFire.svelte` (left untouched so cards/logo can't
  regress) and to the territory rendering / snapshot model.

## Future work

- Swap the historical WR input from `wrBySlug` (current) to a time-indexed historical-WR
  lookup once that data exists.
- Optional intensity-scaling of flame height by lead margin.
