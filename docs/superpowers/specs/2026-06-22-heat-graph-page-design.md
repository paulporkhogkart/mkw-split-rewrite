# Live Heat-Graph Page (`#/heat`) — Design

**Date:** 2026-06-22
**Status:** Approved (brainstorming); pending spec review → implementation plan.
**Topic:** Expose the "on fire" heat-graph (how a PB is judged hot or not) as a live,
URL-only page in the `thekartoff` web app, auto-updating from current PBs and WRs.

## Problem / Goal

The "on fire" model — a course burns while its leader's margin over #2 clears an
exponential bar that rises the further the PB sits off the WR — is currently only
inspectable through `tools/fire-model-explorer.html`, a standalone file with a **baked-in
PB snapshot** refreshed by hand via `tools/fire-model-explorer-regen.py`.

We want that same heat-graph visualization available **live** in the web app:

- reachable by **URL only** (`thekartoff.com/#/heat`), **not linked in the navbar**;
- **always current** with the live PBs and WRs (no manual snapshot regeneration);
- **never drifting** from the territory map's flames — the page and the map must agree
  on which courses are lit.

## Background (current state)

- **The model** lives in `web/src/lib/fireModel.js`: `E0 = 0.2`, `K = 4`,
  `fireBarPct(offPct)`, `isOnFire({t1,t2,wr})`, `snuffLeadMs({t1,wr})`. Single source of
  truth, already shared with the live territory-map flames.
- **The map's flame set** is computed in `web/src/WorldMap.svelte` as
  `fireListAt({ courses: manifest.courses, events: tlEvents, wrs: tlWrs, colors: tlColors, t: frameTime })`
  (`web/src/lib/onFire.js`), with `t = Infinity` at the live frame.
- **All required data is already served** by one public endpoint,
  `GET /v1/territory/timeline` → `{ events, colors, wrs }` (`pi/src/api/reads.ts` →
  `territoryTimeline` in `pi/src/db/reads.ts`). Per-course standings come from
  `leaderboardAt(events, slug, t)` (`web/src/lib/timeline.js`); WR per slug from `wrs`;
  leader colour from `colors`.
- **Course names** come from the map manifest (`manifestUrl` in `web/src/lib/map.js`),
  `manifest.courses = [{ slug, name, hit, ... }]` — the same list `WorldMap` loads.
- **The web app** (`web/`) is a hash-routed Svelte SPA. `web/src/lib/view.js` maps the
  location hash to a view (`"territory"` vs fallback `"live"`); `web/src/App.svelte`
  renders the view and the navbar tabs.

## Approach

Add a hidden SPA page that reuses the **identical** data source, course manifest, reducer,
model, and live frame as the map. Because the inputs and math are shared, the page's lit
set equals the map's flame set by construction, and it stays current because the endpoint
does.

**No new server route is required** — the existing public `/v1/territory/timeline`
already exposes the data. (A dedicated `/v1/fire` JSON contract was considered and
rejected as strictly more code with a drift risk; see Decisions.)

## Components

1. **`web/src/lib/heat.js`** *(new, pure, unit-tested)*
   - `heatRows(courses, events, wrs, colors)` → one row per course:
     `{ slug, name, leader, color, t1, t2, leadPct, offPct, fire, snuffMs }`.
   - `t1`/`t2` = current #1/#2 (`leaderboardAt(events, slug, Infinity)`); `wr = wrs[slug]`;
     `color = colors[leader]`. `fire`/bar metrics from `fireModel.js`
     (`isOnFire`, `fireBarPct`, `snuffLeadMs`). Courses without a real #2 or without a
     current WR are skipped (same gate as `fireListAt`).
   - This is `fireListAt` generalized to **all** courses (not just the lit subset). The two
     return different field shapes (`fireListAt` carries the map `hit` box + `wr`; `heatRows`
     carries `name` + the `lead/off/snuff` metrics), but share the same per-course
     derivation: `leaderboardAt` → require a real #2 → `t1/t2` → `wrs[slug]` → leader colour.
     Factor that core into one helper both build on, so the timeline-→-course-row logic
     lives in one place and the two cannot disagree on which courses qualify.

2. **`web/src/HeatGraph.svelte`** *(new)*
   - On mount, fetch `territoryTimelineUrl()` and `manifestUrl` (mirroring `WorldMap`),
     build rows via `heat.js`, render the ported explorer visualization:
     the exponential heatmap region, one colored dot per course, the bar polyline, the
     axes, and the "🔥 on fire — sorted by closeness to WR" list.
   - **Presentation** is ported from `tools/fire-model-explorer.html`; **math** comes from
     `fireModel.js`, never a private copy.
   - **Sliders:** keep the `E₀` / `K` sliders, initialized to the locked `fireModel`
     defaults (`0.2` / `4`). At the default position the lit set matches the map; dragging
     is a what-if that only affects this page (never the map or the model).

3. **`web/src/lib/view.js`** — recognize `#/heat` → `"heat"` (currently only `territory`
   vs fallback `live`).

4. **`web/src/App.svelte`** — render `<HeatGraph/>` when `view === "heat"`.
   **No navbar tab is added** → the page is URL-only / unlisted.

## Data flow

```
/v1/territory/timeline ──► { events, colors, wrs }
manifestUrl            ──► manifest.courses [{ slug, name }]
        │
        ▼
heatRows(courses, events, wrs, colors)   // leaderboardAt @ t=Infinity + fireModel
        │
        ▼
HeatGraph.svelte  ─►  heatmap region + per-course dots + bar polyline + on-fire list
```

The same `events/colors/wrs` + `manifest.courses` + `fireModel` that `WorldMap` →
`MapFireLayer` consume → identical lit set.

## Decisions

- **Reuse `/v1/territory/timeline`, no new server route.** It already exposes
  everything and is the same source the map uses, so the page cannot disagree with the
  map and needs no regeneration. A dedicated `/v1/fire` would be more code and could drift
  from the map's cross-season "current" semantics.
- **Keep the `E₀`/`K` sliders** (default locked `0.2`/`4`) so it stays a live tuning
  explorer; the default matches the map.
- **Route `#/heat`.**
- **Live frame only** (`t = Infinity`), matching the map's live flames. Historical
  scrubbing stays on the territory map, which already owns the time-scrubber.
- **Unlisted, not access-controlled** — consistent with the PB/WR data already being
  public via the territory endpoints.

## Out of scope (YAGNI)

- Auth / login gating of the page.
- A new server endpoint (`/v1/fire`).
- Mobile-specific layout.
- Historical scrubbing / per-frame replay on this page.
- Any change to the model constants or to `tools/fire-model-explorer.html`.

## Testing

- **Unit (`web/src/lib/heat.test.js`)**, mirroring `onFire.test.js`: on-fire boundary
  (lead exactly on/over/under the bar), skip rows with no current WR or no real #2,
  row ordering, and that the lit **slug set** from `heatRows(...).filter(r => r.fire)`
  matches the existing `fireListAt` set for the same inputs (the no-drift guarantee).
- **`svelte-check`** clean (0/0).
- **Manual:** load `#/heat` against a server serving live data; confirm the lit courses
  match the territory map's flames; confirm the page is not reachable from the navbar but
  loads directly by URL.
