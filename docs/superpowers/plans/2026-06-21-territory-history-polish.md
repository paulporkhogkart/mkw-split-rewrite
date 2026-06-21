# Territory History + Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On the Territory page, serve it at `#/territory`, restyle the scrubber playhead as a broadcast needle, and make a course's hover popup show the leaderboard as it stood at the scrubbed-to moment.

**Architecture:** `web/` frontend only, no server changes. The historical leaderboard is reconstructed entirely client-side from the run-event stream that `/v1/territory/timeline` already ships (the same stream that builds the ownership snapshots), so the board is guaranteed consistent with the territory colours on the map. A pure `leaderboardAt(events, slug, t)` helper plus a `buildHistoricalCourseView(...)` view-model feed the existing, unchanged `CoursePopup`.

**Tech Stack:** Svelte 4, Vite 5, Vitest 4. Pure-JS lib helpers are unit-tested; Svelte components and CSS are covered by `svelte-check` + build + manual verification.

## Global Constraints

- `web/` frontend only. No server / endpoint / DB changes.
- Spec: `docs/superpowers/specs/2026-06-21-territory-history-and-polish-design.md`.
- Run all `npm` commands from the `web/` directory.
- `CoursePopup.svelte` markup/styles do NOT change; only the view-model passed to it.
- Do not touch the `/map/island.png` / `/map/base.jpg` asset paths (public map assets, unrelated to the route).
- Comment style: match existing `web/` files — hyphens not em-dashes, British "colour" in prose.
- WR in historical view is the CURRENT WR (no historical WR yet); keep the WR input a single value so a time-indexed lookup can drop in later.

---

### Task 1: Route `/map` -> `/territory`

**Files:**
- Modify: `web/src/lib/view.js`
- Modify: `web/src/lib/view.test.js`
- Modify: `web/src/App.svelte` (lines 52-53, 58)

**Interfaces:**
- Produces: `viewFromHash(hash) -> "territory" | "live"` (the `"map"` value is retired).

- [ ] **Step 1: Update the failing test**

Replace the whole body of `web/src/lib/view.test.js` with:

```js
import { describe, it, expect } from "vitest";
import { viewFromHash } from "./view.js";

describe("viewFromHash", () => {
  it("defaults to the live card wall", () => {
    expect(viewFromHash("")).toBe("live");
    expect(viewFromHash("#/")).toBe("live");
    expect(viewFromHash("#/unknown")).toBe("live");
    expect(viewFromHash("#/map")).toBe("live"); // old route no longer matches
  });
  it("returns territory for the territory hash", () => {
    expect(viewFromHash("#/territory")).toBe("territory");
    expect(viewFromHash("#territory")).toBe("territory");
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

From `web/`: `npx vitest run src/lib/view.test.js`
Expected: FAIL — `#/territory` returns `"live"` (old code only matches `"map"`), and `#/map` returns `"map"` not `"live"`.

- [ ] **Step 3: Update `view.js`**

Replace the whole of `web/src/lib/view.js` with:

```js
// Two views, selected by the location hash. Unknown hashes fall back to "live".
export function viewFromHash(hash) {
  return (hash || "").replace(/^#\/?/, "") === "territory" ? "territory" : "live";
}
```

- [ ] **Step 4: Run the test to verify it passes**

From `web/`: `npx vitest run src/lib/view.test.js`
Expected: PASS (both tests).

- [ ] **Step 5: Update `App.svelte`**

In `web/src/App.svelte`, change the Territory nav tab (currently line 53):

```svelte
    <a class="tab" class:on={view === "map"} href="#/map">Territory</a>
```

to:

```svelte
    <a class="tab" class:on={view === "territory"} href="#/territory">Territory</a>
```

and change the router line (currently line 58):

```svelte
  {#if view === "map"}<WorldMap />{:else}<CardWall />{/if}
```

to:

```svelte
  {#if view === "territory"}<WorldMap />{:else}<CardWall />{/if}
```

- [ ] **Step 6: Verify the app still type-checks**

From `web/`: `npm run check`
Expected: 0 errors / 0 warnings.

- [ ] **Step 7: Commit**

```bash
git add web/src/lib/view.js web/src/lib/view.test.js web/src/App.svelte
git commit -m "feat(web): serve the territory page at #/territory"
```

---

### Task 2: Scrubber playhead -> broadcast needle (CSS only)

**Files:**
- Modify: `web/src/TimelineScrubber.svelte` (`<style>` only)

**Interfaces:** none (presentational; no prop/event changes).

No unit test applies (pure CSS). Verification is `svelte-check` + a build + visual check.

- [ ] **Step 1: Recolour the fill from blue to neutral**

In `web/src/TimelineScrubber.svelte`, the `.fill` rule currently reads:

```css
  .fill {
    position: absolute;
    left: 0;
    top: 50%;
    transform: translateY(-50%);
    height: 4px;
    border-radius: var(--r-sm);
    background: var(--accent-soft);
    transition: width 0.15s linear;
  }
```

Change only the `background` line to a neutral played-track tone:

```css
    background: rgba(255, 255, 255, 0.18);
```

- [ ] **Step 2: Replace the round thumb with a vertical needle**

The range input + track heights are 16px. Bump them to 18px so an 18px needle never clips (Firefox clips `::-moz-range-thumb` to the track height). Change these three height values:

`.range { ... height: 16px; ... }` -> `height: 18px;`
`.range::-webkit-slider-runnable-track { background: transparent; height: 16px; }` -> `height: 18px;`
`.range::-moz-range-track { background: transparent; height: 16px; }` -> `height: 18px;`

Then replace the two thumb rules. Currently:

```css
  .range::-webkit-slider-thumb {
    -webkit-appearance: none;
    appearance: none;
    width: 14px;
    height: 14px;
    border-radius: 50%;
    background: var(--accent);
    border: 2px solid #fff;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.55);
  }
  .range::-moz-range-thumb {
    width: 14px;
    height: 14px;
    border-radius: 50%;
    background: var(--accent);
    border: 2px solid #fff;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.55);
  }
```

Replace both with a thin white needle (the `0 0 0 1px` dark ring keeps it legible over any tick colour):

```css
  .range::-webkit-slider-thumb {
    -webkit-appearance: none;
    appearance: none;
    width: 2px;
    height: 18px;
    border-radius: 1px;
    background: #f3f4f6;
    border: none;
    box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.55), 0 1px 4px rgba(0, 0, 0, 0.6);
  }
  .range::-moz-range-thumb {
    width: 2px;
    height: 18px;
    border-radius: 1px;
    background: #f3f4f6;
    border: none;
    box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.55), 0 1px 4px rgba(0, 0, 0, 0.6);
  }
```

- [ ] **Step 3: De-blue the focus ring**

Currently:

```css
  .range:focus-visible::-webkit-slider-thumb { box-shadow: 0 0 0 3px var(--accent-bg); }
  .range:focus-visible::-moz-range-thumb { box-shadow: 0 0 0 3px var(--accent-bg); }
```

Replace with a neutral ring that keeps the dark outline:

```css
  .range:focus-visible::-webkit-slider-thumb { box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.55), 0 0 0 3px rgba(255, 255, 255, 0.3); }
  .range:focus-visible::-moz-range-thumb { box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.55), 0 0 0 3px rgba(255, 255, 255, 0.3); }
```

- [ ] **Step 4: De-blue the play button hover/focus**

Currently:

```css
  .play:hover { border-color: var(--accent); color: #fff; }
  .play:focus-visible { outline: none; border-color: var(--accent); }
```

Replace with a neutral treatment:

```css
  .play:hover { border-color: var(--tx-mut); color: #fff; background: var(--raised); }
  .play:focus-visible { outline: none; border-color: var(--tx-mut); }
```

- [ ] **Step 5: Verify it type-checks and builds**

From `web/`: `npm run check && npm run build`
Expected: check 0/0; build succeeds.

- [ ] **Step 6: Visual check (manual)**

From `web/`: `npm run dev`, open the app, go to the Territory page. Confirm: the playhead is a thin white vertical needle (no blue circle, no white ring), the played portion of the track is a soft neutral grey (not blue), and the play button hover is neutral. Drag the needle across coloured ticks and confirm it stays legible.

- [ ] **Step 7: Commit**

```bash
git add web/src/TimelineScrubber.svelte
git commit -m "style(web): scrubber playhead as a broadcast needle, de-blue the transport"
```

---

### Task 3: `leaderboardAt` helper

**Files:**
- Modify: `web/src/lib/timeline.js` (append the helper)
- Modify: `web/src/lib/timeline.test.js` (import + tests)

**Interfaces:**
- Produces: `leaderboardAt(events, slug, t) -> Array<{ player: string, ms: number }>` sorted ascending by `ms`. `events` are `{ t, player, slug, ms }` (the `/v1/territory/timeline` stream). `player` is the display name.

- [ ] **Step 1: Write the failing tests**

In `web/src/lib/timeline.test.js`, change the import line:

```js
import { buildSnapshots, flippedCourses } from "./timeline.js";
```

to:

```js
import { buildSnapshots, flippedCourses, leaderboardAt } from "./timeline.js";
```

and append these tests at the end of the file:

```js
describe("leaderboardAt", () => {
  it("takes each player's running-minimum up to t, sorted ascending", () => {
    const events = [
      { t: 1000, player: "Aliias", slug: "mc", ms: 90000 },
      { t: 2000, player: "Aliias", slug: "mc", ms: 88000 }, // improves own time
      { t: 3000, player: "Gub", slug: "mc", ms: 80000 },
      { t: 5000, player: "Aliias", slug: "mc", ms: 70000 }, // after the cutoff -> ignored
    ];
    expect(leaderboardAt(events, "mc", 3000)).toEqual([
      { player: "Gub", ms: 80000 },
      { player: "Aliias", ms: 88000 },
    ]);
  });

  it("ignores other courses and is empty for an unknown slug or no events", () => {
    const events = [{ t: 1000, player: "Gub", slug: "mc", ms: 80000 }];
    expect(leaderboardAt(events, "dk", 9999)).toEqual([]);
    expect(leaderboardAt([], "mc", 9999)).toEqual([]);
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

From `web/`: `npx vitest run src/lib/timeline.test.js`
Expected: FAIL — `leaderboardAt` is not exported (`leaderboardAt is not a function`).

- [ ] **Step 3: Implement `leaderboardAt`**

Append to `web/src/lib/timeline.js`:

```js
// Per-course leaderboard AS OF time `t`: each player's running-minimum ms among that course's
// events with `event.t <= t`, sorted ascending. Drives the historical hover popup off the same
// event stream that builds the ownership snapshots, so the board matches the map. Pure.
export function leaderboardAt(events, slug, t) {
  const best = {}; // player -> min ms up to t
  for (const e of events) {
    if (e.slug !== slug || e.t > t) continue;
    if (best[e.player] == null || e.ms < best[e.player]) best[e.player] = e.ms;
  }
  return Object.entries(best)
    .map(([player, ms]) => ({ player, ms }))
    .sort((a, b) => a.ms - b.ms);
}
```

- [ ] **Step 4: Run the tests to verify they pass**

From `web/`: `npx vitest run src/lib/timeline.test.js`
Expected: PASS (all `buildSnapshots`, `flippedCourses`, and `leaderboardAt` tests).

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/timeline.js web/src/lib/timeline.test.js
git commit -m "feat(web): leaderboardAt - reconstruct a course board as of time t"
```

---

### Task 4: `buildHistoricalCourseView` + shared assembly

**Files:**
- Modify: `web/src/lib/courseData.js` (extract a shared assembler; add `buildHistoricalCourseView` + `fetchCourseWr`)
- Modify: `web/src/lib/courseData.test.js` (import + tests)

**Interfaces:**
- Consumes: `leaderboardAt(...)` output as `standings` (`Array<{ player, ms }>`); `isOnFire` (already imported in this file).
- Produces:
  - `buildHistoricalCourseView({ standings, colorByName, courseName, wr }) -> view` — same shape `buildCourseView` returns and `CoursePopup` consumes: `{ name, wr_ms, leader:{name,color}|null, onFire, gifUrl, fireGifUrl, rows:[{rank,name,color,time_ms,time_str,gap_ms}] }`. `colorByName` is `{ display_name -> color }`; `wr` is `{ record_ms } | null`.
  - `fetchCourseWr(apiBase, course, { fetchImpl }) -> { record_ms } | null` — current WR, cached per slug.

- [ ] **Step 1: Write the failing tests**

In `web/src/lib/courseData.test.js`, change the import line:

```js
import { buildCourseView } from "./courseData.js";
```

to:

```js
import { buildCourseView, buildHistoricalCourseView } from "./courseData.js";
```

and append at the end of the file:

```js
describe("buildHistoricalCourseView", () => {
  const colorByName = { Gub: "#38bdf8", Paul: "#a78bfa" };

  it("builds rows/leader/gap/gifs/on-fire from name-keyed standings", () => {
    const standings = [
      { player: "Paul", ms: 114914 },
      { player: "Gub", ms: 110579 }, // out of order on input -> must be sorted to #1
    ];
    const v = buildHistoricalCourseView({ standings, colorByName, courseName: "Mario Bros. Circuit", wr: { record_ms: 107414 } });
    expect(v.name).toBe("Mario Bros. Circuit");
    expect(v.wr_ms).toBe(107414);
    expect(v.leader).toEqual({ name: "Gub", color: "#38bdf8" });
    expect(v.onFire).toBe(true);
    expect(v.gifUrl).toBe("/players/gub.gif");
    expect(v.fireGifUrl).toBe("/players/gub__fire.gif");
    expect(v.rows[0]).toMatchObject({ rank: 1, name: "Gub", color: "#38bdf8", time_ms: 110579, gap_ms: null });
    expect(v.rows[1]).toMatchObject({ rank: 2, name: "Paul", color: "#a78bfa", gap_ms: 4335 });
  });

  it("falls back to a neutral colour and is calm with no #2 / no WR", () => {
    const v = buildHistoricalCourseView({ standings: [{ player: "Nobody", ms: 100000 }], colorByName: {}, courseName: "X", wr: null });
    expect(v.leader.color).toBe("#888");
    expect(v.rows[0].color).toBe("#888");
    expect(v.onFire).toBe(false);
    expect(v.wr_ms).toBe(null);
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

From `web/`: `npx vitest run src/lib/courseData.test.js`
Expected: FAIL — `buildHistoricalCourseView` is not exported.

- [ ] **Step 3: Refactor `buildCourseView` onto a shared assembler and add the new exports**

In `web/src/lib/courseData.js`, replace the existing `buildCourseView` function (the block from `/** Pure: raw rows + wr + colour map -> popup view-model. */` through its closing `}`) with the shared assembler plus the two builders:

```js
/** Internal: normalized entries [{name, color, time_ms, time_str?}] -> popup view-model.
 *  Sorts by time, ranks, computes gap-to-#1, on-fire, and the leader's gif urls. */
function assembleCourseView({ entries, wr, courseName }) {
  const sorted = [...entries].sort((a, b) => a.time_ms - b.time_ms);
  const leadMs = sorted.length ? sorted[0].time_ms : null;
  const rows = sorted.map((e, i) => ({
    rank: i + 1,
    name: e.name,
    color: e.color || NEUTRAL,
    time_ms: e.time_ms,
    time_str: e.time_str,
    gap_ms: i === 0 ? null : e.time_ms - leadMs,
  }));
  const leader = sorted[0] || null;
  const wrMs = wr && wr.record_ms != null ? wr.record_ms : null;
  const onFire = isOnFire({ t1: leadMs, t2: sorted[1] ? sorted[1].time_ms : null, wr: wrMs });
  return {
    name: courseName,
    wr_ms: wrMs,
    leader: leader ? { name: leader.name, color: leader.color || NEUTRAL } : null,
    onFire,
    gifUrl: leader ? `${gifBase(leader.name)}.gif` : null,
    fireGifUrl: leader ? `${gifBase(leader.name)}__fire.gif` : null,
    rows,
  };
}

/** Pure: raw leaderboard rows (player_id-keyed) + wr + colour map -> popup view-model. */
export function buildCourseView({ rows, wr, colorById, courseName }) {
  const entries = rows.map((r) => ({
    name: r.display_name,
    color: colorById[r.player_id] || NEUTRAL,
    time_ms: r.total_time_ms,
    time_str: r.total_time_str,
  }));
  return assembleCourseView({ entries, wr, courseName });
}

/** Pure: historical standings ([{player, ms}] from leaderboardAt) + name-keyed colours + wr
 *  -> popup view-model. Same shape as buildCourseView; CoursePopup formats time_ms itself. */
export function buildHistoricalCourseView({ standings, colorByName, courseName, wr }) {
  const entries = standings.map((s) => ({
    name: s.player,
    color: colorByName[s.player] || NEUTRAL,
    time_ms: s.ms,
  }));
  return assembleCourseView({ entries, wr, courseName });
}
```

Then, just below the existing `fetchColorById` function, add the WR fetcher (it reuses the file-level `j` helper):

```js
const wrCache = new Map();

/** Current WR for a course (the raw { record_ms } row, or null), cached per slug. The historical
 *  hover popup has no historical WR yet, so it shows the current WR. Swap this for a time-indexed
 *  lookup once historical WRs are scraped. */
export async function fetchCourseWr(apiBase, course, { fetchImpl = fetch } = {}) {
  if (wrCache.has(course.slug)) return wrCache.get(course.slug);
  const wr = await j(fetchImpl, `${apiBase}/v1/world-records?course=${encodeURIComponent(course.slug)}&cc=150`);
  wrCache.set(course.slug, wr);
  return wr;
}
```

- [ ] **Step 4: Run the full courseData suite to verify it passes**

From `web/`: `npx vitest run src/lib/courseData.test.js`
Expected: PASS — both the pre-existing `buildCourseView` tests (unchanged behaviour after the refactor) and the new `buildHistoricalCourseView` tests.

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/courseData.js web/src/lib/courseData.test.js
git commit -m "feat(web): buildHistoricalCourseView + fetchCourseWr for the history popup"
```

---

### Task 5: Wire the historical board into the hover popup

**Files:**
- Modify: `web/src/WorldMap.svelte` (imports; retain `events`/`colors`; branch in `openCourse`)

**Interfaces:**
- Consumes: `leaderboardAt` (Task 3), `buildHistoricalCourseView` + `fetchCourseWr` (Task 4).

No unit test applies (Svelte component / integration). Verification is `svelte-check` + build + manual.

- [ ] **Step 1: Add the imports**

In `web/src/WorldMap.svelte`, change:

```js
  import { fetchCourseView, preloadPlayerGifs, freshGifUrl } from "./lib/courseData.js";
```

to:

```js
  import { fetchCourseView, buildHistoricalCourseView, fetchCourseWr, preloadPlayerGifs, freshGifUrl } from "./lib/courseData.js";
```

and change:

```js
  import { buildSnapshots, flippedCourses } from "./lib/timeline.js";
```

to:

```js
  import { buildSnapshots, flippedCourses, leaderboardAt } from "./lib/timeline.js";
```

- [ ] **Step 2: Add retained-stream state**

Find:

```js
  let snapshots = [];
  let tlIndex = 0;
```

and insert the retained stream between them:

```js
  let snapshots = [];
  let tlEvents = [], tlColors = {};   // retained run stream + colour map for historical hover boards
  let tlIndex = 0;
```

- [ ] **Step 3: Retain `events` + `colors` in `loadTimeline`**

Find:

```js
      const { events, colors } = await res.json();
      const snaps = buildSnapshots(events, colors);
      if (!snaps.length) return;
```

and add the retention line right after:

```js
      const { events, colors } = await res.json();
      const snaps = buildSnapshots(events, colors);
      if (!snaps.length) return;
      tlEvents = events; tlColors = colors;   // kept for the historical hover popup
```

- [ ] **Step 4: Branch `openCourse` on the scrubber position**

Find:

```js
  async function openCourse(course, hitEl) {
    clearTimeout(closeTimer);
    activeHit = hitEl;
    const my = ++token;
    const v = await fetchCourseView(API_BASE, course).catch(() => null);
    if (!v || my !== token) return;                 // fetch failed, or a newer hover superseded us
    view = v;
```

and replace it with:

```js
  async function openCourse(course, hitEl) {
    clearTimeout(closeTimer);
    activeHit = hitEl;
    const my = ++token;
    const atLive = !timelineReady || tlIndex >= snapshots.length - 1;
    let v;
    if (atLive) {
      v = await fetchCourseView(API_BASE, course).catch(() => null);   // canonical current board
    } else {
      // Scrubbed back: reconstruct the board AS OF the shown snapshot from the retained event
      // stream (matches the territory colours on the map). WR is the current WR (no historical
      // WR yet). A course with no runs by then has no board -> no popup opens.
      const t = snapshots[tlIndex].t;
      const wr = await fetchCourseWr(API_BASE, course).catch(() => null);
      const standings = leaderboardAt(tlEvents, course.slug, t);
      v = standings.length
        ? buildHistoricalCourseView({ standings, colorByName: tlColors, courseName: course.name, wr })
        : null;
    }
    if (!v || my !== token) return;                 // fetch failed / empty board, or a newer hover superseded us
    view = v;
```

(Leave the rest of `openCourse` — the `figUrl` handling, `tick()`, `place()`, and the `shown` toggle — unchanged.)

- [ ] **Step 5: Verify it type-checks and builds**

From `web/`: `npm run check && npm run build`
Expected: check 0/0; build succeeds.

- [ ] **Step 6: Run the whole web suite**

From `web/`: `npm test`
Expected: all tests pass (view, timeline incl. `leaderboardAt`, courseData incl. `buildHistoricalCourseView`, and the rest).

- [ ] **Step 7: Manual end-to-end check**

This needs the season server serving data. With the API reachable, from `web/`: `npm run dev`, open the Territory page.
- At LIVE (needle at the far right), hover a course -> the current leaderboard (unchanged behaviour).
- Scrub back into Aliias's reign, hover a course he owned -> the popup shows Aliias #1 with the board of that moment, and the leader figure is his. The popup's leader colour matches the course's territory colour on the map.
- Scrub to an early moment and hover a course nobody had run yet -> no popup opens (expected).
- Scrub back to LIVE, hover the same course -> current board again.

- [ ] **Step 8: Commit**

```bash
git add web/src/WorldMap.svelte
git commit -m "feat(web): course hover popup shows the leaderboard at the scrubbed moment"
```

---

## Self-Review

**1. Spec coverage:**
- Route `/map` -> `/territory` (view.js value rename, App.svelte, view.test.js) -> Task 1. [covered]
- Playhead broadcast needle + de-blue fill + de-blue play button -> Task 2. [covered]
- Retain `events` + `colors` -> Task 5 Steps 2-3. [covered]
- `leaderboardAt(events, slug, t)` pure + tested -> Task 3. [covered]
- `buildHistoricalCourseView` + shared assembler + tested -> Task 4. [covered]
- WR current-only via `fetchCourseWr`, single swap-in point -> Task 4 Step 3 + Task 5 Step 4. [covered]
- LIVE keeps `fetchCourseView`; scrubbed reconstructs; hover-open semantics -> Task 5 Step 4. [covered]
- Tests: view, timeline, courseData -> Tasks 1/3/4; full suite -> Task 5 Step 6. [covered]
- Future mid-scrub live-update: derivation kept pure (`leaderboardAt` + `buildHistoricalCourseView` are pure functions of slug + index); no task builds it (out of scope). [intentionally deferred]

**2. Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to". Every code step shows the full code. [clean]

**3. Type consistency:** `leaderboardAt -> [{player, ms}]` (Task 3) is consumed as `standings` by `buildHistoricalCourseView({ standings, colorByName, courseName, wr })` (Task 4), called with `colorByName: tlColors`, `courseName: course.name`, `wr` from `fetchCourseWr` (Task 5). View shape matches what `CoursePopup` reads (`rows[].time_str || fmt(time_ms)`, `leader.color`, `onFire`, `gifUrl`/`fireGifUrl`). [consistent]
