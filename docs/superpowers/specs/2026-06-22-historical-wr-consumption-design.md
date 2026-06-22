# Historical-WR Territory Consumption (popups + fire/heat) — Design

**Date:** 2026-06-22
**Status:** Approved (brainstorming); pending spec review → implementation plan.
**Topic:** Make the territory map's course popups and the "on fire" / heat calc use the
World Record **as of the scrubbed timeline date**, instead of always using the current WR.

## Problem / Goal

The WR full-history capture ([[wr-full-history-done]]) mirrored each course's complete WR
progression into `world_records` (`achieved_at`, `record_ms`, `removed_at`, ...). Nothing
consumes that history yet: the territory page still treats the WR as a single frozen
"current" value at every frame of the timeline.

Two deliverables:

1. **Popups** — when the timeline is scrubbed to a past date, a course's hover popup shows
   the WR **in effect at that date**, not the current WR.
2. **Fire / heat** — the "on fire" verdict (and the `#/heat` page) judges each pausable
   timeline frame against the **as-of-date WR** at that frame, not the current WR.

At the LIVE frame both must be byte-for-byte identical to today (as-of-newest == current).

## Background (current state)

Data flow on the territory page (`web/src/WorldMap.svelte`):

- The server (`territoryTimeline` in `pi/src/db/reads.ts`) returns
  `{ events, colors, wrs }`, where:
  - `events` = every finished run as `{ t: Date.parse(ended_at), player, slug, ms }`,
    sorted ascending. The raw stream; **the server has no notion of "frames".**
  - `wrs` = **current WR only**, `{ slug → record_ms }`
    (`is_current = 1 AND removed_at IS NULL`, cc-filtered).
- The client derives everything else **as of the scrubbed frame** from the raw stream:
  - `buildSnapshots(events, colors)` → the ≤197 ownership snapshots (the scrubber's frames).
    Frames are an emergent client-side artifact, not known to the server.
  - `leaderboardAt(events, slug, t)` → historic standings (`web/src/lib/timeline.js`).
  - `frameTime = atLive ? Infinity : snapshots[tlIndex].t` (LIVE must be `Infinity`).
- The **WR is the only value still frozen at "current"**, at exactly two call sites:
  - **Popup** (`WorldMap.svelte` ~L348): `buildCourseView({ ..., wr: tlWrs[course.slug] })`.
  - **Fire** (`WorldMap.svelte` ~L67): `fireListAt({ ..., wrs: tlWrs, ..., t: frameTime })`
    → `onFire.js` → `heat.js:courseRowAt({ ..., wrs, ..., t })` does `wrs[course.slug]`.
- The `#/heat` page (`HeatGraph.svelte`) calls `heatRows({ ..., wrs, ..., t: Infinity })`
  through the **same** `courseRowAt`, so map-flames and heat-lit agree by construction
  (asserted by a no-drift parity test).
- `world_records` already has everything needed: `record_ms`, `achieved_at`
  (ISO `…T00:00:00.000Z`, or the pre-release sentinel `2025-06-04T00:00:00.000Z`, or
  `null` on parse failure), `removed_at`, `cc`, `course_id`.

## Chosen approach

**Ship the WR history once in the timeline payload; resolve "WR as of `t`" on the client**,
symmetric to how `leaderboardAt` already reconstructs standings as-of-`t` from the raw
stream. `courseRowAt` already receives `t`, so the WR resolution drops in beside its own
`leaderboardAt(events, slug, t)` call.

Rejected alternatives:

- **Server computes per-frame WRs and embeds them** — the server has no notion of frames;
  the snapshot times are a client-side artifact of `buildSnapshots`. The server would have
  to duplicate that logic (drift risk — the exact thing the shared `courseRowAt` derivation
  was built to avoid), and the result (≤197 frames × 30 courses) is a *larger* payload than
  the raw history.
- **Per-hover fetch from a new endpoint** — explicitly the model the territory-unify work
  removed ("no per-hover fetches; popups are instant"). Reintroducing it regresses that.

Payload cost of the chosen approach is negligible: ~30 courses × ~5–40 history rows sent as
`[achievedMs, recordMs]` pairs ≈ ~15 KB pre-gzip, vs the thousands of run events already in
the stream.

## Design

### 1. Server — `territoryTimeline` payload (`pi/src/db/reads.ts`)

Replace the current-only `wrs` with the full per-course progression:

```
wrHistory: { [slug]: [ [achievedMs, recordMs], … ] }   // each slug's array sorted ascending by achievedMs
```

- Query: select `c.slug`, `w.record_ms`, `w.achieved_at` from `world_records` joined to
  `courses`, `WHERE w.cc = ? AND w.removed_at IS NULL AND w.achieved_at IS NOT NULL`.
  **Drop `is_current = 1`** (we want the whole progression).
- Build entries as `[Date.parse(achieved_at), record_ms]`, `.filter` to finite `t`
  (consistent with how `events` filters non-finite `t`), and sort each slug's array
  ascending by `achievedMs`.
- Return type becomes
  `{ events: TimelineEvent[]; colors: Record<string,string>; wrHistory: Record<string, [number, number][]> }`.
  The `wrs` key is **dropped** — its only consumers (map fire, popup, heat page) are all
  updated in this change. Same route, `GET /v1/territory/timeline`; no new endpoint.

### 2. Client core — `wrAsOf` (`web/src/lib/timeline.js`)

New pure resolver, sibling to `leaderboardAt`:

```js
// The WR in effect for `slug` at time `t`: the minimum record_ms among that course's
// history entries achieved by `t` (achievedMs <= t). null when none exist yet. Entries are
// pre-sorted ascending, but we take the running min so a stray out-of-order/legacy row can
// never report a slower record than one already achieved. At t = Infinity → the newest =
// the current WR, so the LIVE frame is unchanged.
export function wrAsOf(wrHistory, slug, t) { … }   // wrHistory = { slug: [[achievedMs, recordMs], …] }
```

Returns a `record_ms` number or `null`.

### 3. Client wiring (mechanical)

- **`web/src/lib/heat.js`** — `courseRowAt` and `heatRows`: rename the `wrs` param to
  `wrHistory`, and resolve `const wr = wrAsOf(wrHistory, course.slug, t)` (replacing
  `wrs[course.slug]`). Single chokepoint — flames and heat both flow through here. The
  `board.length < 2 || !wr` guard is unchanged (a null as-of WR → row skipped → no fire).
- **`web/src/lib/onFire.js`** — `fireListAt`: rename `wrs` → `wrHistory`, pass through to
  `courseRowAt`.
- **`web/src/WorldMap.svelte`** — rename the `tlWrs` state to `tlWrHistory`; destructure
  `wrHistory` from the payload (`let tlEvents = [], tlColors = {}, tlWrHistory = {}`); the
  fire-list call passes `wrHistory: tlWrHistory`; the popup path resolves
  `wr: wrAsOf(tlWrHistory, course.slug, frameTime)`. `buildCourseView` and `CoursePopup`
  are **untouched** — they still receive a resolved `record_ms` number.
- **`web/src/HeatGraph.svelte`** — destructure `wrHistory` from the payload, pass it to
  `heatRows`.

## Behaviour & edge cases

- **LIVE frame (`t = Infinity`):** `wrAsOf` returns the newest history entry == the current
  WR. Popup, fire, and heat are identical to today.
- **Before a course's first dated WR** (`wrAsOf` → null): popup WR shows "—" (existing
  `fmt(null)`), and `courseRowAt`'s `!wr` guard skips the row so `isOnFire` never fires.
  Graceful; rare in practice since pre-release WRs sentinel to `2025-06-04`, before friend
  runs began.
- **`removed_at` rows / null `achieved_at`:** excluded server-side — consistent with the
  `0333c1b` rule that no consumer counts DQ'd WRs, and with the event stream's finite-`t`
  filter.
- **Duplicate rows** (same course/cc/date, different holder or ms): harmless — `wrAsOf`
  takes the min ms over `≤ t`.

## Testing (TDD)

- **New `wrAsOf` unit tests** (extend the existing `web/src/lib/timeline.test.js`): picks
  the record in effect at a mid-history `t`; takes the min over entries `≤ t`; returns
  `null` before the first entry; equals the newest entry at `t = Infinity`; unknown slug
  → `null`.
- **Server `territoryTimeline` test** (`pi/src/db/reads.test.ts`): payload carries
  `wrHistory`; each slug's array is ascending by `achievedMs`; `removed_at` rows and
  null-`achieved_at` rows are excluded; cc-filtered; the newest entry equals the prior
  current WR for a course.
- **Update `web/src/lib/heat.test.js` and `onFire.test.js`:** the `wrs` number-map fixtures
  become `wrHistory` (`{ slug: [[0, ms]] }` — a single entry at t=0 resolves at any frame).
  The existing no-drift parity assertion (heat-lit == map-lit) is unchanged and still holds
  by construction.
- **`pi/src/api/app.test.ts`** only smoke-tests `GET /v1/territory/timeline` for status
  (no `wrs`-shape assertion), so no change is needed there — just confirm it still passes.

## Files touched

- `pi/src/db/reads.ts` — `territoryTimeline` query + return shape.
- `pi/src/db/reads.test.ts` — server payload test.
- `web/src/lib/timeline.js` — new `wrAsOf`.
- `web/src/lib/heat.js` — `courseRowAt` / `heatRows` param + resolution.
- `web/src/lib/onFire.js` — `fireListAt` param.
- `web/src/WorldMap.svelte` — state rename + popup/fire wiring.
- `web/src/HeatGraph.svelte` — payload destructure.
- `web/src/lib/timeline.test.js`, `web/src/lib/heat.test.js`, `web/src/lib/onFire.test.js` — tests.

## Out of scope

- Holder / date in the popup (value-only chosen).
- Any new server route or per-hover fetch.
- The `/v1/territory` no-timeline fallback (`renderTerritory`) — static present, no
  scrubber/popups/fire, so it never reads WRs.
- Server-side per-frame precompute.
