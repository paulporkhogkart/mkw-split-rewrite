# Tracks Page — Design Spec

**Date:** 2026-07-04
**Surface:** `web/` (thekartoff.com) + `pi/` (new public endpoints)
**Status:** approved for v1; visual design deferred to implementation.

## Overview

A per-track section of thekartoff.com, mirroring the players page. A single **TRACKS**
nav item opens a grid of all 30 tracks; each links to that track's page at `/tracks/:slug`.
The track page is a **hub** for one course: current standings, world record, the time
needed to go "on fire", a reconstructed track map with four visualization modes, lap-split
analysis, and historical views.

Like the players page, this is **public read-only** and the first pass fixes **structure +
data, not final look** — "all the information present, styled later." The map (drawing the
server's course model, everyone's PB lines/dots, and a run-density heatmap) is the novel part.

## Naming: "Tracks" (public) / "course" (internal)

**Only the URL path (`/tracks`) and visible UI text (nav "TRACKS", page headings) read
"Tracks."** Every code identifier keeps **"course"** — the DB table `courses`, the API
(`/v1/courses/...`), the `view.js` view key (`"courses"`), the new components
(`CoursesIndex`/`CourseProfile`/`CourseMap`, paralleling `PlayersIndex`/`PlayerProfile`), the
`api.js` helpers (`courseSummaryUrl` …), and existing helpers (`courseIdBySlug`,
`courseData.js`, `course_models`). This public-noun / internal-noun split is deliberate:
Nintendo's official term is "course", but "track" is the colloquial term the user prefers, and
renaming the whole backend/DB/desktop surface buys nothing. Switching the public label to
"Courses" later is a one-line change (the route string + a few labels).

## Decisions locked

- **Structure:** mirror players — one `TRACKS` nav item → `/tracks` index grid →
  `/tracks/:slug` detail hub. Course slugs are already canonical in the DB (`courses.slug`),
  so — unlike players — **no client slug-mirror is needed**; the slug resolves directly via
  `courseIdBySlug`.
- **v1 scope = "Hub + map + lap splits":** the ready-now hub (leaderboard, WR, on-fire
  target, history) + the 4-mode track map + the lap-splits suite. (User-selected boundary.)
- **Global leaderboard is not duplicated here** — it already lives on `/turf`. This surface
  is *per-track*.
- **Visual design deferred to implementation** (frontend-design skill; OBS-plain / functional
  colour house standards). Verify canvas visuals in a **real browser**, never OpenCV.

## Routing & navigation

- Add `TRACKS` to the navbar in `App.svelte` (between existing tabs).
- Extend `lib/view.js` History-API routing (same mechanism as `/players`): `/tracks` and
  `/tracks/:slug` both map to view key `"courses"`. Add `courseSlugFromPath` (clone of
  `playerSlugFromPath`, regex `^tracks/([^/]+)`). Wire `view` + `courseSlug` in `App.svelte`'s
  `navigate` and `popstate` sync; render `{#if courseSlug}<CourseProfile>{:else}<CoursesIndex>`.

## Index page (`/tracks`)

**Leaderboard-first — the index is a wall of track leaderboards, not a thumbnail picker.**
The most important information (who is fastest on each track) is visible immediately, with **no
hover interaction**.

A grid of **persistent leaderboard cards** in canonical order (sort by `course_id`, which
follows the `CANONICAL_COURSES` insert order — better than the alphabetical `/v1/territory`
default), one per track. **Each card *is* the turf `CoursePopup` rendered inline** (not on
hover): track name + WR + the leader's figure strip + the **full leaderboard** (rank · player ·
time · gap, with on-fire flames). The whole card is an `<a href="/tracks/:slug">` to the detail
hub (App's `navigate` intercepts the click). The track sprite (`spriteUrl(slug)`) may sit as a
small marker in the card header, but the leaderboard is the substance.

**Overall-time card (pinned, non-linking).** Lead the page with one extra leaderboard card
titled **Overall** — each player's summed PBs (total time across all tracks, ranked, with a
track-count), the #2 axis of the site's leaderboard hierarchy. Same leaderboard-card styling,
but with **no WR/fire and it links nowhere** (it is a summary, not a track). Derived from the
same timeline data (sum each player's current per-track best), mirroring the pi
`overallLeaderboard` semantics (sum of available PBs). Not affected by the search.

**Track search.** A text input filters the track cards by name client-side (substring match) so
finding a track is quick. It filters only the track grid; the Overall card stays pinned.

**Data (no new endpoint).** Fetch `/v1/territory/timeline` **once** — the same public source
the turf map uses — and derive all 30 tracks' standings + colours + current WR client-side
(`lib/timeline.js` → `courseData.js buildCourseView`). No per-card fetch, no popups. The
Overall card and the search filter read this same data — no extra request. (The
timeline is a biggish payload, but the turf map already downloads it; a dedicated "all current
boards" endpoint is a possible later optimization, not needed for v1.)

`CoursePopup` is currently styled as a floating popup; used as a persistent grid card it needs
only **light style adaptation** (drop the shadow/float, fit the grid cell), not structural change.

## Detail page (`/tracks/:slug`) — the track hub

Sections, top to bottom:

1. **Header + WR.** Track thumbnail/splash + the current **world record**: holder, time,
   character/kart **chips** (via `chips.js` `chipUrl` — assets 404-hide, so a text label
   fallback shows until the matte PNGs land), and a **video link** (`world_records.video_url`).
2. **Leaderboard.** The full board — every player's PB, gap-to-#1, rank, on-fire flame —
   assembled by `courseData.js` (same as the turf popup), not truncated.
3. **On-fire target.** "Run **X:XX.XXX** to seize #1 and light this track." Computed
   client-side from the current board + WR via a new pure `lib/fireTarget.js` (see §Math).
   Graceful states when undefined (no WR / no second time / you're already lit).
4. **Track map.** The canvas with four modes (see §Track map).
5. **Lap splits.** A per-lap table: each player's **best split per lap**, their **theoretical
   best** (Σ of their own best individual laps), and the **field ideal** (Σ of the fastest
   lap by anyone). Adapts to the track's lap count (MKW has variable lap counts — derive from
   the data). Theoretical best only shown when a player has a best for *every* lap; else "—".
6. **History.** **Record progression** (the local record falling over time — who set it, when),
   **ownership reigns** (who held #1 and for how long), and **WR history** (past world records
   + their video links). Server-computed per-track (see §Backend), reusing `pi/src/db/reign.ts`.

## Track map (four modes, one canvas)

A new `CourseMap.svelte` hosting a `<canvas>` + a mode toggle, reusing pbenguin's pure drawing
module `src/lib/overlay.js` (the website already imports from root `src/`).

- **Outline** — the server's course model polylines (the "mental model" of the track shape).
- **PB lines** — each player's PB drawn as a coloured path (their trail points as a polyline).
- **▶ Replay** — animated PB dots racing together on the race clock (play / scrub), reusing
  `overlay.js` `interpolateXY` + the trail-dot rendering (PB pulse, per-player colour,
  z-ordering) and the `buildTrailRuns` assembly logic from `src/lib/trailSettings.js`.
- **Heatmap** — all finished runs' density (the run cloud that forms the track), from a compact
  server-rasterized grid.

**Coordinate handling.** All geometry (course model, trail points) is in full-frame **1080p
common-frame pixels**; the minimap occupies a per-course sub-rectangle. That per-course ROI
lives only in the desktop DB, **not the Pi** — so the web **derives the viewport bbox from the
model/point data itself** (union of polyline/point extents, padded) and fits it to the canvas
via `overlay.js` `computeDisplayRect`/`pointToScreen`. Self-adapts per track; no ROI needed
server-side.

## Backend — new public endpoints

Only the map's canvas data and the splits/history are new; the current leaderboard
(`/v1/leaderboard?course=`), WR (`/v1/world-records?course=`), and course list
(`/v1/territory`) are **already public**. New routes, all public:

1. **`GET /v1/courses/:slug?cc=150`** — the hub summary in one payload:
   ```
   { profile: { slug, display_name },
     wr:        { holder_name, record_ms, record_str, video_url, character, vehicle } | null,
     leaderboard: [ { player_id, display_name, color, total_time_ms, rank } ],
     splits:    { laps: N,
                  perPlayer: [ { player_id, display_name, color, best: [ms|null, …N] } ],
                  fieldIdeal: [ms|null, …N] },
     history:   { recordProgression: [ { t, player, ms } ],   // local record drops
                  reigns:            [ { player, from, to|null, ms } ],
                  wrHistory:         [ { t, holder_name, record_ms, video_url } ] } }
   ```
   Assembled by a new `pi/src/db/courseSummary.ts` (resolves slug via `courseIdBySlug`, 404s
   on unknown), reusing `courseLeaderboard`, `currentWr`, `reign.ts`, and a new `run_laps`
   aggregation for `splits` (`MIN(lap_time_ms)` per player per `lap_index` over finished runs).
   `perPlayer.best` feeds client-side theoretical-best; `fieldIdeal` is per-lap min across all.
   *Fallback:* if server-side reign/progression proves fiddly, the client can derive history
   from the already-public `/v1/territory/timeline` sliced to this slug (heavier download).

2. **`GET /v1/courses/:slug/model?cc=150`** — the course model JSON (`loadCourseModel`), or
   `{ model: null }` when none is built yet. Lazy (fetched when the map opens).

3. **`GET /v1/courses/:slug/trails?cc=150`** — every PB's trail points for the course
   (`courseTrails`), **with the stored per-player `player_alignment` applied server-side** so
   points land in the model's common frame (alignment is near-identity; this new public route
   leaves the existing gated `/v1/trails` untouched). Feeds PB-lines + Replay. Lazy.

4. **`GET /v1/courses/:slug/heatmap?cc=150`** — a compact density grid over all finished runs,
   rasterized server-side (reusing the splat primitive in `pi/src/progress/lapGraphCV`) and
   **cached** (recompute on cache miss; the run set changes only on new uploads). Returns a
   small typed grid `{ w, h, bbox:[x0,y0,x1,y1], cells:[…] }`, not raw points. Lazy.

**Gating (`pi/src/api/app.ts`)** — mirror the player-summary exception for the courses paths:
- CORS: `app.use('/v1/courses/*', readCors)` (covers `:slug` and the three sub-paths).
- Open regex: `const COURSE = /^\/v1\/courses\/[^/]+(\/(model|trails|heatmap))?$/;` added to
  `isOpen`. Single- and known-two-segment courses reads become token-free; nothing else opens.

## Math modules (pure, unit-tested)

**`lib/fireTarget.js`** — the on-fire target time. To become the leader at time `T` *and* be
lit, `T` must clear the fire bar at your own off-WR%:
```
lead% = (t1 − T)/wr × 100 ≥ fireBarPct((T − wr)/wr × 100)     // t1 = current best, wr = WR
```
`fireBarPct` (from `lib/fireModel.js`, reused not duplicated) is monotonic, so there is one
crossing; solve for the largest qualifying `T` by bisection/Newton. Undefined (return null +
a reason) when: no WR, empty board, or the required `T ≤ wr` (impossible). If you're already
the leader, the target is the time to ignite over the current #2.

**`lib/courseSplits.js`** — from `summary.splits`: theoretical best per player
(`Σ perPlayer.best` when all N laps present, else null) and it already receives `fieldIdeal`.
Handles missing laps, single-lap tracks, and players with no laps.

## Files

New (web):
- `web/src/CoursesIndex.svelte`, `web/src/CourseProfile.svelte`, `web/src/CourseMap.svelte`
- `web/src/lib/fireTarget.js` + `.test.js`, `web/src/lib/courseSplits.js` + `.test.js`

New (pi):
- `pi/src/db/courseSummary.ts` + colocated test (leaderboard + wr + splits + history)
- `pi/src/db/courseHeatmap.ts` (rasterize + cache) or inline in the route

Edited:
- `web/src/App.svelte` (nav + view routing), `web/src/lib/view.js` (routes + `courseSlugFromPath`)
- `web/src/lib/api.js` (+ `courseSummaryUrl`, `courseModelUrl`, `courseTrailsUrl`, `courseHeatmapUrl`)
- `pi/src/api/reads.ts` (+ the four `/v1/courses/...` routes), `pi/src/api/app.ts` (CORS + regex)

Reused: `src/lib/overlay.js`, `src/lib/trailSettings.js`, `web/src/lib/courseData.js`,
`web/src/lib/timeline.js`, `web/src/lib/chips.js`, `web/src/lib/map.js`, `web/src/lib/fireModel.js`
(all unchanged); `web/src/CoursePopup.svelte` reused with **light restyle** (float → grid card).

## Testing

- **Pure math** — `fireTarget.js` (crossing solve; undefined cases: no WR, empty board,
  impossible target, already-leader) and `courseSplits.js` (theoretical best, missing laps,
  field ideal) fully unit-tested with hand-built inputs.
- **Index derivations** — the timeline → overall-standings sum (sum of per-track bests +
  track-count) and the client-side name filter are pure helpers, unit-tested.
- **`courseSummary.ts`** — seeded in-memory DB (mirroring `playerSummary.test.ts`): leaderboard
  order, WR fields, per-lap bests + field ideal, reigns/record-progression, and slug 404.
- **HTTP + gate/CORS** — mirror `players.test.ts`: `/v1/courses/:slug` (+ `/model`, `/trails`,
  `/heatmap`) return 200 + `access-control-allow-origin: *` with no token; unknown slug 404s;
  the existing gated `/v1/trails` stays 401.
- **Routing** — extend `view.test.js` with a `tracks` describe block.
- **Svelte / canvas** — verified in a **real browser** (headless Edge + CDP), never OpenCV.

## Non-goals (explicit, deferred)

- **Recommended loadout** for a track (needs the v2 public stats passthrough the players page
  also deferred).
- **"Death" heatmap toggle** (where abandoned/reset/DNF runs stop — a data proxy, needs an
  abandoned-run endpoint; later).
- **Chip PNG assets** themselves — the render path (`chipUrl`) ships now with a text fallback;
  chips light up when the asset-matting work lands.
- **cc selector** — v1 is `cc=150` only, like the players page.
- **Recent-runs log** — dropped from the default (user unsure); trivial to add from the timeline
  if wanted. Naturally mirrors to a per-player log on the players page later.
