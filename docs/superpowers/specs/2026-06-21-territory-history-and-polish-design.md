# Territory page: historical leaderboards + route + playhead polish

**Date:** 2026-06-21
**Scope:** `web/` frontend only. No server changes.

Three issues on the Territory page (`WorldMap.svelte` + the timeline scrubber + the
course hover popup), in priority order:

1. **Route** — serve the page at `#/territory` instead of `#/map`.
2. **Playhead** — the scrubber knob/fill use the old OBS-app blue accent, which clashes
   with the site's red / per-player KART-OFF palette. Restyle as a broadcast needle.
3. **Historical leaderboards (main)** — when scrubbed back in time, hovering a course icon
   shows that course's leaderboard **as it stood at that moment**, not the current standings.

## Background (current state)

- `web/src/lib/view.js` maps the location hash to a view: `#/map` -> `"map"`, anything
  else -> `"live"`. `App.svelte` renders `WorldMap` for `"map"` and `CardWall` otherwise,
  with a nav tab linking to `#/map`.
- The `/map/island.png` and `/map/base.jpg` paths are **public map assets**, unrelated to
  the route. They do not change.
- `TimelineScrubber.svelte` renders a range input over a rail + fill + per-flip coloured
  ticks. The thumb is a 14px blue (`--accent` `#3d7cc2`) circle with a white ring + drop
  shadow; the fill is `--accent-soft` (blue); the play button hover is `--accent`.
- `WorldMap.svelte` `openCourse()` always calls `fetchCourseView(API_BASE, course)`
  (`courseData.js`), which fetches `/v1/leaderboard?course=<slug>&cc=150` (the canonical
  **current** all-time board) and caches it per slug.
- The scrubber position is `tlIndex`; `snapshots[tlIndex]` is the shown ownership snapshot,
  with `snapshots[tlIndex].t` its epoch-ms timestamp. The rightmost index is LIVE.
- `loadTimeline()` fetches `/v1/territory/timeline` -> `{ events, colors }`, builds
  ownership snapshots via `buildSnapshots(events, colors)`, then **discards** `events`.
  - `events`: the **entire** finished-run stream — `{ t, player, slug, ms }` for every
    finished run at `cc` (excluding `provenance='carryover'`), across S0 recovery + S1 live.
    `player` is the display name. `t` is epoch ms.
  - `colors`: `{ display_name -> color }`.

The events stream is the same source that drives the ownership snapshots the map already
shows, so any leaderboard reconstructed from it is **guaranteed consistent** with the
territory colours on screen.

## Issue 1 — route `/map` -> `/territory`

Rename the hash route and the internal view value (clearer than keeping a `"map"` value
behind a `/territory` URL):

- `view.js`: return `"territory"` for hash `territory`; fallback stays `"live"`.
- `App.svelte`: nav `href="#/territory"`; both `view === "map"` checks (tab `class:on`,
  the `{#if}` router) become `view === "territory"`.
- `view.test.js`: update cases (`#/territory` -> `"territory"`, unknown -> `"live"`).

No redirect from the old `#/map` is added (private site, no external inbound links to it);
an unknown hash already falls back to the Live view.

## Issue 2 — playhead as a broadcast needle (`TimelineScrubber.svelte`, CSS only)

Replace the round thumb with a thin vertical needle and de-blue the rest of the transport:

- Thumb (`::-webkit-slider-thumb`, `::-moz-range-thumb`): a ~2px-wide, full-track-height
  vertical bar in white (`#f3f4f6`) with a soft dark text-shadow/outline so it reads on any
  underlying tick colour. No fill circle, no drop-shadow ring. Keep the hit-area usable
  (the input stays full-height; only the painted thumb is the needle).
- Fill (`.fill`): from `--accent-soft` (blue) to a neutral muted tone (a faint white /
  `--tx-dim`-ish), so "played" is still indicated without the blue.
- Play button hover/focus: from `--accent` (blue) to a neutral/white treatment, so nothing
  on the transport is blue.

Rail and coloured ticks are unchanged. This is presentational only — no prop/event changes.

## Issue 3 — historical leaderboards on hover (main)

Reconstruct the per-course board client-side from the retained `events`, keyed on the
scrubber position. The popup component (`CoursePopup.svelte`) is unchanged — only the
view-model handed to it differs.

### Data flow

```
loadTimeline(): fetch /v1/territory/timeline -> { events, colors }
                buildSnapshots(events, colors) -> snapshots   (as today)
                RETAIN events + colors on the component        (new)

hover course icon (openCourse):
  atLive = timeline not ready  OR  tlIndex === snapshots.length - 1
  if atLive:  fetchCourseView(API_BASE, course)                (unchanged path)
  else:       standings = leaderboardAt(events, course.slug, snapshots[tlIndex].t)
              view = buildHistoricalCourseView({ standings,
                       colorByName: colors, courseName: course.name, wr })
  -> CoursePopup {view}
```

### New pure helper (`timeline.js`, unit-tested)

`leaderboardAt(events, slug, t)` -> `[{ player, ms }]` sorted ascending: for the given
`slug`, each player's minimum `ms` among events with `event.t <= t`. Players with no run by
`t` are absent. Pure; no DOM. Reused later for the mid-scrub live update (see Future work).

### New view-model builder (`courseData.js`, unit-tested)

`buildHistoricalCourseView({ standings, colorByName, courseName, wr })` -> the same shape
`buildCourseView` already produces and `CoursePopup` consumes:
`{ name, wr_ms, leader:{name,color}, onFire, gifUrl, fireGifUrl, rows:[{rank,name,color,time_ms,time_str,gap_ms}] }`.

- Colours come from `colorByName` (the timeline `colors` map, display-name keyed) — already
  consistent with the map ticks.
- `gap_ms` = each row's `ms` minus the leader's; rank 1 has `null`.
- `time_str` omitted; `CoursePopup` falls back to its own `fmt(time_ms)`.
- `onFire` via the existing `isOnFire({ t1, t2, wr })`.
- Leader's `gifUrl`/`fireGifUrl` derived from the leader's display name (same `gifBase`
  helper as `buildCourseView`), so the figure is whoever led at that moment.

`buildCourseView` and `buildHistoricalCourseView` share the row/leader/gif/on-fire assembly;
factor the common part into one internal function so the two entry points only differ in how
they obtain rows + colours.

### WR in historical view (current-only, for now)

The dataset has no historical WR. The popup's WR line and the on-fire calc use the **current**
WR for that slug. Fetch it the same way the live path does (`/v1/world-records?course=<slug>`,
cached per slug) and pass it into `buildHistoricalCourseView`. This is the only value in the
historical popup that is not true-to-the-moment. **Future:** when historical WRs are scraped,
swap this single `wr` input for a time-indexed lookup — no other change needed.

### Caching

Historical views are time-dependent, so they must **not** use the existing per-slug
`viewCache` (that stays for the live path). Reconstruction is cheap (~2.5k events filtered,
sub-ms), so historical views are computed on demand with no caching in v1. (A `slug+index`
memo is a trivial later add if profiling ever shows a need.)

### Hover-open semantics (and future live-update)

The popup derives its view from `tlIndex` **at hover-open time**. This matches the natural
interaction: to move the scrubber the user moves the pointer off the icon, which closes the
popup (`mouseleave` -> `scheduleClose`), so the next hover re-opens against the new position.
During playback the user is not hovering.

**Future (not in this change):** live-update an already-open popup while scrubbing/playing.
Because the view is a pure function of `(slug, tlIndex)`, this becomes a reactive statement
that re-derives the open popup's view when `tlIndex` changes — no structural change required.
The design keeps the derivation pure specifically to enable this.

## Testing

- `view.test.js` — updated route cases.
- `timeline.test.js` — `leaderboardAt`: running-minimum per player; `t` cutoff excludes
  later runs; a player improving their own time; ties; unknown slug -> empty; empty events.
- `courseData.test.js` — `buildHistoricalCourseView`: rank/gap ordering, colour-by-name
  mapping, leader gif selection, on-fire passthrough, current-WR passthrough.
- Manual: scrub back, hover a course Aliias owned during his reign -> Aliias #1 with the
  board of that moment; scrub to LIVE -> current board (unchanged); playhead reads as a
  white needle with no blue anywhere on the transport.

## Out of scope

- Server / endpoint changes.
- Historical WR (current WR used; future swap-in point identified above).
- Mid-scrub live-update of an open popup (future; derivation kept pure to enable it).
- Any change to `CoursePopup.svelte` markup/styles, the territory rendering, or the snapshot
  model.
