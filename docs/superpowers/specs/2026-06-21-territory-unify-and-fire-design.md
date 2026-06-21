# Territory map: timeline-unified data + on-fire flames

**Date:** 2026-06-21
**Scope:** `web/` frontend + one small `pi/` server field.

Two coupled goals, built in two phases on one branch:

- **Phase A — timeline-unified data.** Drive the Territory page's LIVE frame, hover
  popups, and (Phase B) fire from the *one* run-history stream the page already loads,
  instead of per-hover `/v1/leaderboard` + `/v1/world-records` fetches. Popups become
  instant; the only new server data is current WRs, folded into the existing timeline
  payload.
- **Phase B — on-fire flames.** Substantial-lead ("on fire") courses visibly burn on the
  map at every frame (live and scrubbed-back), reusing the flame engine
  (`Fire.svelte` / `WordmarkFire.svelte`) and the on-fire formula (`fireModel.js`).

This **supersedes the LIVE-popup data path** from `2026-06-21-territory-history-and-polish-design.md`
(unmerged, same branch `territory-history-polish`). That branch's route rename, playhead, and
`leaderboardAt` stand unchanged; its LIVE-popup fetchers are removed here (see A4).

## Background

- **Already loaded on page load.** `/v1/territory/timeline` returns `{ events, colors }`:
  `events` is the full S0+S1 finished-run stream (`{ t, player, slug, ms }`, carryover
  excluded; `player` = display name), `colors` is `{ display_name -> hex }`. `WorldMap.svelte`
  retains these as `tlEvents` / `tlColors` and builds ownership snapshots from them. From
  `tlEvents`, `leaderboardAt(events, slug, t)` (already added, pure, tested) gives any course's
  board as of any time `t`.
- **Why LIVE moves to the timeline.** `courseLeaderboard` (today's LIVE popup, `/v1/leaderboard`)
  is **current-season** (`WHERE season_id=? AND is_pb=1`); `/v1/territory` (today's LIVE map
  colours) is also current-season. The loaded history is **all-time**. To make popups + fire
  derive from the loaded history AND stay consistent with the painted region, the LIVE frame is
  unified onto the timeline (all-time), matching this map's cross-season (Aliias->Gub) premise.
- **The flame engine.** `src/components/Fire.svelte` (cards) and `web/src/lib/WordmarkFire.svelte`
  (logo) share one technique: metaball ellipses animated by one rAF, blurred + thresholded
  through an SVG goo filter, hued from a brand colour via a 4-stop HSL palette, BACK layer
  (behind) + screen-blended FRONT + glow. Per-instance, geometry authored per use-case, both
  honour `prefers-reduced-motion`.
- **The on-fire formula.** `web/src/lib/fireModel.js` `isOnFire({ t1, t2, wr })` — true when the
  leader's margin over #2 clears an exponential bar that rises the further the leader sits off
  the WR. Needs a real #2 and a WR. Single source of truth; reused verbatim.
- **The map's two renderables.** Course **icons** are DOM `<img class="spr">` sprites in
  absolutely-positioned `.hit` divs (`map.js` `hitStyle(hit)`; `hit.x/y/w/h` are fractions of
  the frame). **Territory regions** are pixels painted on `<canvas class="territory">` by a Web
  Worker on scrub/change — not a per-frame loop.

## Frame time (correctness)

`buildSnapshots` emits a snapshot only when *ownership* changes, so `snapshots[last].t` can
predate the newest runs. The shown-frame cutoff is therefore:

```
atLive    = !timelineReady || tlIndex >= snapshots.length - 1
frameTime = atLive ? Infinity : snapshots[tlIndex].t
```

At LIVE, `Infinity` includes every event (the true current board). The leader (and thus the
territory colour, taken from the last snapshot's owners) is identical either way — only
non-leading improvements differ — so popup #1 still matches the region colour.

## Phase A — Timeline-unified data

### A1. Server: add `wrs` to the timeline payload

`territoryTimeline(db, cc)` (`pi/src/db/reads.ts`) returns `{ events, colors, wrs }` where
`wrs: Record<string, number>` maps `slug -> current WR record_ms` for that `cc`:

```sql
SELECT c.slug, w.record_ms
FROM world_records w JOIN courses c ON c.id = w.course_id
WHERE w.cc = ? AND w.is_current = 1
```

Build `wrs[slug] = record_ms`. `events`/`colors` unchanged. This is the entire server change —
no `/v1/territory` enrichment, no new endpoint.

### A2. Client: retain `tlWrs`

`WorldMap.loadTimeline` already destructures `{ events, colors }`; add `wrs` and retain
`tlWrs = wrs || {}` alongside `tlEvents` / `tlColors`.

### A3. LIVE renders the timeline's last frame

Drop the separate `/v1/territory` present render from the normal path: the last snapshot **is**
LIVE. Concretely:

- `onMount` calls `loadTimeline()` first. On success, the LIVE view is the last snapshot,
  rendered through the existing snapshot path (`showSnapshot(last)` / `ensureBitmap(last)`); the
  `presentBitmap`/`atLive`-uses-present special-casing in `showSnapshot` / `drawBaseFrame` is
  removed (the last snapshot renders like any other frame).
- `renderTerritory()` (the `/v1/territory` one-shot) is kept **only as a fallback** when
  `loadTimeline()` fails: a static map, no scrubber, no popups, no fire (degraded, not blank).
- Final pixels are unchanged in the normal case: the present was already downscaled to the
  same backing size the snapshot path renders at.

### A4. Popups: one instant path; remove dead code

`openCourse` collapses to a single branch for all frames:

```
const t = atLive ? Infinity : snapshots[tlIndex].t;
const standings = leaderboardAt(tlEvents, course.slug, t);   // [] -> no popup
const view = buildCourseView({ standings, colorByName: tlColors,
                               courseName: course.name, wr: tlWrs[course.slug] ?? null });
```

No fetch — instant, identical mechanism at LIVE and historical. The `token` guard and the
empty-standings -> no-popup behaviour are kept. `figUrl` handling unchanged.

Removed from `courseData.js` as now-dead (used only by the old popup path; confirmed no other
consumers): `fetchCourseView`, `fetchCourseWr`, `fetchColorById`, the player_id-keyed
`buildCourseView`, `viewCache`, `wrCache`, and their tests. The name-keyed builder
(`buildHistoricalCourseView`) is **renamed to `buildCourseView`** — it is now the sole
course-view builder; signature stays `{ standings, colorByName, courseName, wr }`. Kept:
`assembleCourseView`, `NEUTRAL`, `gifBase`, `preloadPlayerGifs`, `freshGifUrl`, the roster
fetch (GIF warming only), and the `j` helper.

## Phase B — On-fire flames

### B1. Pure helper (`web/src/lib/`, unit-tested)

`onFireCourses(entries)` -> the subset of `entries` where `isOnFire({ t1, t2, wr })` is true,
each entry returned unchanged so render fields ride along. `entries` is
`Array<{ slug, t1, t2, wr, hit, color }>` (`t1/t2/wr` feed the formula; `slug/hit/color` are
passthrough). Reuses `isOnFire`.

### B2. Client derives the on-fire set per shown frame

In `WorldMap`, reactive to `tlIndex` (and the initial load), build `entries` for every course:
- `t1`/`t2` = the two smallest times in `leaderboardAt(tlEvents, slug, frameTime)` (`[0].ms`,
  `[1].ms`; absent -> the course can't be on fire).
- `wr` = `tlWrs[slug] ?? null`.
- `hit` = `manifest.courses` entry's hit box; `color` = `tlColors[leader]` (the `[0].player`).
- `onFire = onFireCourses(entries)` -> the list handed to the flame layer.

A course with fewer than two competitors by `frameTime`, or no WR, is not on fire. Historical
fire uses the current `tlWrs` (no historical WR yet); the `wr` input is a single per-course
value, so a time-indexed historical-WR lookup drops in later with no structural change.

### B3. `MapFireLayer.svelte` (one shared rAF)

A new overlay placed in the stage **between `.territory` and `.icons`** (flame behind the
sprites; icons stay legible), `position:absolute; inset:0; pointer-events:none`.

- **Input:** the `onFireCourses(...)` list — items `{ slug, hit, color, ... }`.
- **Rendering:** reuses the metaball + goo-filter + HSL-palette technique from `Fire.svelte`,
  consolidated so ONE rAF loop animates the blobs for ALL on-fire courses (not one component
  per icon). Each course gets a compact leader-coloured flame column anchored to its `hit` box
  (rising a little above it) + a soft per-course glow. BACK-style column only (no heavy FRONT
  lick), to keep many simultaneous flames from cluttering the map.
- **Binary on/off** per course; ignite/extinguish via a short opacity fade (like the card's
  ~0.45s), so scrub/frame changes are not hard pops.
- **`prefers-reduced-motion`:** no rAF; a static low-flame/glow (as WordmarkFire does).
- Exact flame shape, density, height, glow are **tuned live during the build** (temp tuner /
  visual companion), the way the card and logo flames were; the component exposes its layer
  constants.

### B4. Reactivity and the play loop

The on-fire list is reactive to `tlIndex` (+ initial load): scrubbing ignites/extinguishes
regions to reflect that moment. During playback `tlIndex` steps frame to frame and the layer
updates per step; if that reads as too busy, the fade timing is the tuning lever (a live call,
not structural).

## Data flow (end state)

```
page load:
  GET /v1/territory/timeline -> { events, colors, wrs }
  retain tlEvents, tlColors, tlWrs ; buildSnapshots(events,colors) -> snapshots
  GET /v1/roster -> preload GIFs (names only)
  render last snapshot = LIVE   (fallback: GET /v1/territory if timeline failed)

per shown frame (tlIndex):
  frameTime = atLive ? Infinity : snapshots[tlIndex].t
  fire:  for each course -> leaderboardAt(tlEvents,slug,frameTime) -> {t1,t2}, tlWrs[slug] -> wr
         onFireCourses(entries) -> MapFireLayer
  hover: leaderboardAt(tlEvents,slug,frameTime) + tlWrs[slug] + tlColors -> buildCourseView -> CoursePopup
```

## Performance

- One shared rAF for all flames; on-fire recompute only on frame change, not per animation frame.
- The per-course derivation needs only the top two times; a full `leaderboardAt` per course is
  acceptable (sub-ms over ~2.5k events x ~30 courses), early-stop is the optimization if ever needed.
- Fades use CSS opacity (no extra loops). Payload grows by one small `wrs` map (~30 entries).

## Testing

- Server: extend the timeline read test to assert `wrs` (slug -> record_ms; absent course -> no key).
- `buildCourseView` (renamed): existing name-keyed tests carry over; the removed player_id-keyed
  builder's tests are deleted.
- `onFireCourses(entries)` — pure: on-fire vs marginal, missing #2 -> false, missing WR -> false,
  empty input; returned entries retain `slug`/`hit`/`color`.
- The LIVE/historical entry derivation (events + tlWrs + frameTime -> entries) — unit test the
  top-two + cutoff behaviour (leans on `leaderboardAt`).
- `MapFireLayer` + the WorldMap render-orchestration change: svelte-check + build + full web
  suite (no regressions) + manual.
- Manual: popups open instantly with no network on hover; at LIVE, dominated courses burn in the
  leader's colour and the popup #1 matches the region colour; scrub back and the on-fire set +
  boards change to that moment; a single-competitor course never burns; timeline-fetch failure
  falls back to a static map; reduced-motion shows a static glow.

## Out of scope

- Intensity-scaling (flame height by lead margin) — v1 is binary on/off; `fireBarPct` gives the
  metric for a future pass.
- Region-fill fire painted into the territory canvas (rejected: worker-rendered, not a per-frame loop).
- Historical WR (current WR used; single swap-in point noted in B2).
- Changes to `Fire.svelte` / `WordmarkFire.svelte` (left untouched so cards/logo can't regress)
  and to the snapshot/animation model.
- The `/v1/territory` endpoint stays (other consumers); the map page just stops using it except
  as the no-timeline fallback.

## Future work

- Swap historical fire's WR input from current `tlWrs` to a time-indexed historical-WR lookup
  once that data exists.
- Optional flame intensity-scaling by lead margin.
