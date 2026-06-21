# Territory Unify + On-Fire Flames Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Drive the Territory page's LIVE frame, hover popups, and a new on-fire flame layer from the one run-history stream the page already loads (instant popups, one consistent source), then make substantial-lead courses burn on the map at every frame.

**Architecture:** Phase A unifies data: the server folds current WRs into the timeline payload; the client derives every frame (colours, popups, fire) from the in-memory event stream; the per-hover leaderboard/WR fetches and the `/v1/territory` present render are removed (the latter kept only as a no-timeline fallback). Phase B adds `MapFireLayer` (one shared rAF, reusing the metaball flame technique) driven by a pure `onFireCourses`/`fireListAt` helper.

**Tech Stack:** Svelte 4, Vite 5, Vitest 4 (web); Hono + node:sqlite + Vitest (pi server).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-06-21-territory-unify-and-fire-design.md`.
- `web/` frontend + ONE `pi/` server field (`wrs` on the timeline payload). No other server changes.
- Run web `npm` commands from `web/`; run pi tests from `pi/`.
- `isOnFire` (`web/src/lib/fireModel.js`) is the single on-fire formula — reuse it, never reimplement.
- WR is a bare number (`record_ms`) or `null` everywhere post-unification (payload `wrs[slug]`, `buildCourseView`'s `wr`, `onFireCourses` entry `wr`).
- Frame cutoff: `atLive = !timelineReady || tlIndex >= snapshots.length - 1`; `frameTime = atLive ? Infinity : snapshots[tlIndex].t`.
- Do NOT modify `src/components/Fire.svelte` or `web/src/lib/WordmarkFire.svelte` (cards/logo must not regress). `MapFireLayer.svelte` is a self-contained sibling with its own inline colour helpers — matching WordmarkFire's established "duplicate, don't share" precedent for these flame components.
- The `/v1/territory` endpoint stays (other consumers); the map page uses it only as the no-timeline fallback.
- Comment/prose style: hyphens not em-dashes; British "colour".

---

## Phase A — Timeline-unified data

### Task A1: Server adds `wrs` to the timeline payload

**Files:**
- Modify: `pi/src/db/reads.ts` (`territoryTimeline`)
- Test: `pi/src/db/reads.test.ts` (`territoryTimeline` describe block)

**Interfaces:**
- Produces: `territoryTimeline(db, cc)` -> `{ events, colors, wrs }` where `wrs: Record<string, number>` maps course `slug` -> current WR `record_ms` for that `cc`.

- [ ] **Step 1: Extend the failing test**

In `pi/src/db/reads.test.ts`, the `territoryTimeline` test seeds course id 1 (`mario_circuit`). Add a current-WR row to that test's db, just after its `courses` insert line:

```ts
    db.exec("INSERT INTO world_records(course_id,cc,holder_name,record_ms,record_str,is_current) VALUES (1,150,'WR',60000,'1:00.000',1)");
```

and add this assertion at the end of that `it(...)`, after the `r.colors` assertion:

```ts
    expect(r.wrs).toEqual({ mario_circuit: 60000 });
```

- [ ] **Step 2: Run the test to verify it fails**

From `pi/`: `npx vitest run src/db/reads.test.ts -t territoryTimeline`
Expected: FAIL — `r.wrs` is `undefined`.

- [ ] **Step 3: Implement `wrs`**

In `pi/src/db/reads.ts`, change the `territoryTimeline` return-type annotation:

```ts
export function territoryTimeline(db: DatabaseSync, cc: number):
    { events: TimelineEvent[]; colors: Record<string, string>; wrs: Record<string, number> } {
```

and replace its final `return { events, colors };` with the WR query + return:

```ts
  const wrs: Record<string, number> = {};
  for (const w of db.prepare(
    `SELECT c.slug AS slug, w.record_ms AS ms
     FROM world_records w JOIN courses c ON c.id = w.course_id
     WHERE w.cc = ? AND w.is_current = 1`
  ).all(cc) as { slug: string; ms: number }[]) wrs[w.slug] = w.ms;
  return { events, colors, wrs };
```

- [ ] **Step 4: Run the test to verify it passes**

From `pi/`: `npx vitest run src/db/reads.test.ts -t territoryTimeline`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pi/src/db/reads.ts pi/src/db/reads.test.ts
git commit -m "feat(server): include current WRs (wrs) in the territory timeline payload"
```

---

### Task A2: Unify the popup data path (courseData + WorldMap)

This task is atomic — `courseData.js`, its test, and `WorldMap.svelte` change together so the build stays green.

**Files:**
- Modify: `web/src/lib/courseData.js` (rewrite: WR-as-number, sole `buildCourseView`, remove dead fetchers/caches)
- Modify: `web/src/lib/courseData.test.js` (sole `buildCourseView` tests)
- Modify: `web/src/WorldMap.svelte` (imports; `tlWrs`; reactive `atLive`/`frameTime`; single-path `openCourse`)

**Interfaces:**
- Consumes: `leaderboardAt(events, slug, t)` (timeline.js); the `wrs` payload field (Task A1).
- Produces: `buildCourseView({ standings, colorByName, courseName, wr })` — `standings` is `[{player, ms}]`, `wr` is `record_ms` number|null; returns the existing popup view shape (`{ name, wr_ms, leader, onFire, gifUrl, fireGifUrl, rows }`). Removed exports: `fetchCourseView`, `fetchCourseWr`, `fetchColorById`, the old player_id-keyed `buildCourseView`, `buildHistoricalCourseView`.

- [ ] **Step 1: Rewrite the courseData test to the unified API**

Replace the entire contents of `web/src/lib/courseData.test.js` with:

```js
import { describe, it, expect } from "vitest";
import { buildCourseView } from "./courseData.js";

describe("buildCourseView", () => {
  const colorByName = { Gub: "#38bdf8", Paul: "#a78bfa" };

  it("builds rows/leader/gap/gifs/on-fire from name-keyed standings", () => {
    const standings = [
      { player: "Paul", ms: 114914 },
      { player: "Gub", ms: 110579 }, // out of order on input -> must be sorted to #1
    ];
    const v = buildCourseView({ standings, colorByName, courseName: "Mario Bros. Circuit", wr: 107414 });
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
    const v = buildCourseView({ standings: [{ player: "Nobody", ms: 100000 }], colorByName: {}, courseName: "X", wr: null });
    expect(v.leader.color).toBe("#888");
    expect(v.rows[0].color).toBe("#888");
    expect(v.onFire).toBe(false);
    expect(v.wr_ms).toBe(null);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

From `web/`: `npx vitest run src/lib/courseData.test.js`
Expected: FAIL — `buildCourseView` does not accept `{ standings, ... }` / `wr` as a number yet (old export is player_id-keyed; the name-keyed builder is still `buildHistoricalCourseView`).

- [ ] **Step 3: Rewrite `courseData.js`**

Replace the entire contents of `web/src/lib/courseData.js` with:

```js
// Assembles the hover-popup view-model for a course: leaderboard rows (with per-row colour +
// gap-to-#1), WR, on-fire flag, and the leader's GIF urls. Plus GIF preloading helpers. The
// territory page derives every board from the in-memory timeline event stream (see WorldMap),
// so there are no per-hover leaderboard/WR fetches here.
import { isOnFire } from "./fireModel.js";

const NEUTRAL = "#888";
const gifBase = (name) => `/players/${(name || "").toLowerCase()}`;

/** Internal: normalized entries [{name, color, time_ms, time_str?}] + wr (record_ms number|null)
 *  -> popup view-model. Sorts by time, ranks, computes gap-to-#1, on-fire, leader gif urls. */
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
  const wrMs = wr ?? null;
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

/** Pure: standings ([{player, ms}] from leaderboardAt) + name-keyed colours + wr (record_ms
 *  number|null) -> popup view-model. CoursePopup formats time_ms itself when time_str is absent. */
export function buildCourseView({ standings, colorByName, courseName, wr }) {
  const entries = standings.map((s) => ({
    name: s.player,
    color: colorByName[s.player] || NEUTRAL,
    time_ms: s.ms,
  }));
  return assembleCourseView({ entries, wr, courseName });
}

const j = async (fetchImpl, url) => { const r = await fetchImpl(url); return r.ok ? r.json() : null; };

const gifBlobs = new Map();   // /players/<name>.gif -> Blob, preloaded once and reused across opens

/** Preload every roster player's popup GIFs as in-memory blobs. A blob lets each open spin up
 *  a fresh object URL, which restarts the GIF from frame 1 - a cached <img> with the same src
 *  stays frozen on its last decoded frame. Done once; fire-and-forget. */
export async function preloadPlayerGifs(apiBase, { fetchImpl = fetch } = {}) {
  const roster = (await j(fetchImpl, `${apiBase}/v1/roster`)) || [];
  const urls = [];
  for (const p of roster) { const b = gifBase(p.display_name); urls.push(`${b}.gif`, `${b}__fire.gif`); }
  await Promise.all(urls.map(async (u) => {
    if (gifBlobs.has(u)) return;
    try { const r = await fetchImpl(u); if (r.ok) gifBlobs.set(u, await r.blob()); } catch { /* ignore */ }
  }));
}

/** A fresh object URL for a (preloaded) GIF so it replays from the start; falls back to the
 *  plain path when it isn't preloaded yet. The caller revokes the previous object URL. */
export function freshGifUrl(path) {
  const blob = gifBlobs.get(path);
  return blob && typeof URL !== "undefined" && URL.createObjectURL ? URL.createObjectURL(blob) : path;
}
```

- [ ] **Step 4: Run the courseData test to verify it passes**

From `web/`: `npx vitest run src/lib/courseData.test.js`
Expected: PASS (both `buildCourseView` tests).

- [ ] **Step 5: Update `WorldMap.svelte` imports**

Change the courseData import (currently line 5):

```js
  import { fetchCourseView, buildHistoricalCourseView, fetchCourseWr, preloadPlayerGifs, freshGifUrl } from "./lib/courseData.js";
```

to:

```js
  import { buildCourseView, preloadPlayerGifs, freshGifUrl } from "./lib/courseData.js";
```

- [ ] **Step 6: Retain `tlWrs` from the payload**

In `WorldMap.svelte`, change the retained-stream state line (currently `let tlEvents = [], tlColors = {};`) to:

```js
  let tlEvents = [], tlColors = {}, tlWrs = {};   // retained run stream + colour + current-WR maps
```

In `loadTimeline`, change the payload destructure + retention. Currently:

```js
      const { events, colors } = await res.json();
      const snaps = buildSnapshots(events, colors);
      if (!snaps.length) return;
      tlEvents = events; tlColors = colors;   // kept for the historical hover popup
```

to:

```js
      const { events, colors, wrs } = await res.json();
      const snaps = buildSnapshots(events, colors);
      if (!snaps.length) return;
      tlEvents = events; tlColors = colors; tlWrs = wrs || {};   // kept for the unified board + fire
```

- [ ] **Step 7: Add the reactive frame cutoff**

In `WorldMap.svelte`, add these reactive statements just after the `$: stamp = ...` line (around line 58):

```js
  // The shown frame's cutoff time. buildSnapshots only emits on ownership change, so the last
  // snapshot's t can predate recent runs - at LIVE use Infinity to include every event.
  $: atLive = !timelineReady || tlIndex >= snapshots.length - 1;
  $: frameTime = atLive ? Infinity : (snapshots[tlIndex]?.t ?? Infinity);
```

- [ ] **Step 8: Collapse `openCourse` to the single unified path**

Replace the whole `openCourse` function body down to (and including) the `view = v;` line. Currently:

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

Replace with:

```js
  async function openCourse(course, hitEl) {
    clearTimeout(closeTimer);
    activeHit = hitEl;
    const my = ++token;
    // One path for every frame: the board AS OF the shown moment, reconstructed from the
    // in-memory event stream (matches the territory colours). WR is the current WR (tlWrs).
    // A course with no runs by then has no board -> no popup opens.
    const standings = leaderboardAt(tlEvents, course.slug, frameTime);
    const v = standings.length
      ? buildCourseView({ standings, colorByName: tlColors, courseName: course.name, wr: tlWrs[course.slug] ?? null })
      : null;
    if (!v || my !== token) return;                 // empty board, or a newer hover superseded us
    view = v;
```

(Leave the rest of `openCourse` — `figUrl` handling, `tick()`, `place()`, `shown` toggle — unchanged.)

- [ ] **Step 9: Verify web suite, check, build**

From `web/`: `npm test && npm run check && npm run build`
Expected: all tests pass; svelte-check 0/0; build succeeds. (`API_BASE` is still imported and used by `preloadPlayerGifs`/`renderTerritory`; `territoryUrl` still used by `renderTerritory`. No unused-import errors.)

- [ ] **Step 10: Commit**

```bash
git add web/src/lib/courseData.js web/src/lib/courseData.test.js web/src/WorldMap.svelte
git commit -m "refactor(web): unify the course popup on the in-memory timeline + WRs"
```

---

### Task A3: LIVE renders the timeline's last frame

**Files:**
- Modify: `web/src/WorldMap.svelte` (`onMount` order; `loadTimeline` returns success; drop the present-bitmap shortcut in `showSnapshot`/`drawBaseFrame`)

**Interfaces:**
- Consumes: the existing snapshot render path (`ensureBitmap`, `showSnapshot`).
- Produces: `loadTimeline()` -> `Promise<boolean>` (true when the timeline loaded). `renderTerritory()` becomes a fallback only.

- [ ] **Step 1: Make `loadTimeline` report success**

In `loadTimeline`, change the two early non-success exits and add a success return. The `if (!snaps.length) return;` becomes `if (!snaps.length) return false;`. The end of the `try` (currently `refit();`) gains a following `return true;`. The `catch` block gains `return false;` after its `console.error(...)`. Concretely the tail of the function becomes:

```js
      await tick();               // the transport row mounts -> the console grew, so re-fit the map
      refit();                    // re-fit + size canvas + build the animation source buffers + repaint
      return true;
    } catch (e) {
      console.error("timeline load failed (keeping live territory):", e);
      return false;
    }
  }
```

and earlier in the same function: `if (!snaps.length) return;` -> `if (!snaps.length) return false;`

- [ ] **Step 2: Reorder `onMount` — timeline first, present render as fallback**

In `onMount`, replace these two lines (currently ~410-411):

```js
      renderTerritory();       // canonical present (high-res) = default view + fallback
      loadTimeline();          // SP4: fetch history + prepare lazy scrub-frame cache (additive)
```

with:

```js
      const ok = await loadTimeline();   // primary: history drives every frame incl. LIVE (last snapshot)
      if (!ok) renderTerritory();        // fallback only: static present, no scrubber/popups/fire
```

- [ ] **Step 3: Drop the present-bitmap shortcut at LIVE**

In `showSnapshot`, the present-at-LIVE shortcut is replaced by the normal snapshot render. Currently:

```js
        const atLive = target === snapshots.length - 1;
        let bmp;
        try { bmp = atLive && presentBitmap ? presentBitmap : await ensureBitmap(target); }
        catch { continue; }
```

becomes:

```js
        let bmp;
        try { bmp = await ensureBitmap(target); }
        catch { continue; }
```

In `drawBaseFrame`, likewise. Currently:

```js
  async function drawBaseFrame(i) {
    i = Math.max(0, Math.min(i, snapshots.length - 1));
    const atLive = i === snapshots.length - 1;
    let bmp;
    try { bmp = atLive && presentBitmap ? presentBitmap : await ensureBitmap(i); }
    catch { return; }
    paintBitmap(bmp);
  }
```

becomes:

```js
  async function drawBaseFrame(i) {
    i = Math.max(0, Math.min(i, snapshots.length - 1));
    let bmp;
    try { bmp = await ensureBitmap(i); }
    catch { return; }
    paintBitmap(bmp);
  }
```

(`presentBitmap`, `renderTerritory`, and the `refit()` `else if (presentBitmap)` branch stay — they serve the no-timeline fallback.)

- [ ] **Step 4: Verify check, build, suite**

From `web/`: `npm run check && npm run build && npm test`
Expected: check 0/0; build succeeds; all tests pass.

- [ ] **Step 5: Manual smoke (deferred to controller/human)**

With the season server running, `npm run dev`, open the Territory page: LIVE shows the territory (last snapshot), the scrubber works, and hovering a course opens its board instantly with no network request. (Automated steps above are the gate; this is a human confirmation.)

- [ ] **Step 6: Commit**

```bash
git add web/src/WorldMap.svelte
git commit -m "refactor(web): LIVE territory renders the timeline's last frame (/v1/territory now fallback-only)"
```

---

## Phase B — On-fire flames

### Task B1: `onFireCourses` + `fireListAt` helpers

**Files:**
- Create: `web/src/lib/onFire.js`
- Test: `web/src/lib/onFire.test.js`

**Interfaces:**
- Consumes: `isOnFire` (fireModel.js), `leaderboardAt` (timeline.js).
- Produces:
  - `onFireCourses(entries)` -> subset of `entries` (each `{ slug, t1, t2, wr, hit?, color? }`) where `isOnFire({t1,t2,wr})`; entries returned unchanged.
  - `fireListAt({ courses, events, wrs, colors, t })` -> the on-fire render list `[{ slug, hit, color, t1, t2, wr }]`. `courses` is `[{ slug, hit, ... }]` (manifest courses); `wrs`/`colors` are slug->ms and name->hex maps.

- [ ] **Step 1: Write the failing tests**

Create `web/src/lib/onFire.test.js`:

```js
import { describe, it, expect } from "vitest";
import { onFireCourses, fireListAt } from "./onFire.js";

// leader 110579, #2 114914, wr 107414 -> clearly on fire (same numbers as the popup test).
// marginal: a 21ms lead over #2 off the same WR -> not on fire.
describe("onFireCourses", () => {
  it("keeps only entries whose lead clears the bar, preserving passthrough fields", () => {
    const entries = [
      { slug: "a", t1: 110579, t2: 114914, wr: 107414, hit: { x: 1 }, color: "#38bdf8" }, // on fire
      { slug: "b", t1: 110579, t2: 110600, wr: 107414, hit: { x: 2 }, color: "#a78bfa" }, // marginal
      { slug: "c", t1: 110579, t2: null,   wr: 107414, hit: { x: 3 }, color: "#fff" },     // no #2
      { slug: "d", t1: 110579, t2: 114914, wr: null,   hit: { x: 4 }, color: "#fff" },     // no WR
    ];
    const out = onFireCourses(entries);
    expect(out.map((e) => e.slug)).toEqual(["a"]);
    expect(out[0]).toMatchObject({ slug: "a", hit: { x: 1 }, color: "#38bdf8" });
  });

  it("is empty for empty input", () => {
    expect(onFireCourses([])).toEqual([]);
  });
});

describe("fireListAt", () => {
  const courses = [
    { slug: "mc", hit: { x: 0.1 } },
    { slug: "rr", hit: { x: 0.2 } }, // single competitor -> never on fire
  ];
  const events = [
    { t: 1000, player: "Gub",  slug: "mc", ms: 110579 },
    { t: 1000, player: "Paul", slug: "mc", ms: 114914 },
    { t: 1000, player: "Gub",  slug: "rr", ms: 90000 },
  ];
  const wrs = { mc: 107414, rr: 80000 };
  const colors = { Gub: "#38bdf8", Paul: "#a78bfa" };

  it("returns on-fire courses with leader colour + hit + the formula inputs", () => {
    const out = fireListAt({ courses, events, wrs, colors, t: Infinity });
    expect(out).toHaveLength(1);
    expect(out[0]).toMatchObject({ slug: "mc", color: "#38bdf8", t1: 110579, t2: 114914, wr: 107414, hit: { x: 0.1 } });
  });

  it("excludes a course whose runner-up does not exist yet at t", () => {
    const out = fireListAt({ courses, events, wrs, colors, t: 999 }); // before any event
    expect(out).toEqual([]);
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

From `web/`: `npx vitest run src/lib/onFire.test.js`
Expected: FAIL — `./onFire.js` does not exist.

- [ ] **Step 3: Implement `onFire.js`**

Create `web/src/lib/onFire.js`:

```js
// Which courses are "on fire" for a given frame, and the render list for the flame layer.
// Pure: reuses the single on-fire formula (fireModel.isOnFire) and the timeline board reducer
// (leaderboardAt). The map renderer (MapFireLayer) consumes fireListAt's output.
import { isOnFire } from "./fireModel.js";
import { leaderboardAt } from "./timeline.js";

const NEUTRAL = "#888";

/** Subset of `entries` ({ slug, t1, t2, wr, ...passthrough }) that is on fire; entries returned
 *  unchanged so render fields (hit, color) ride along. */
export function onFireCourses(entries) {
  return entries.filter((e) => isOnFire({ t1: e.t1, t2: e.t2, wr: e.wr }));
}

/** Build the on-fire render list for the shown frame: for each course, the top-two times AS OF
 *  `t` (from the event stream), the current WR, the leader's colour, and the course's hit box;
 *  then filter to the on-fire subset. */
export function fireListAt({ courses, events, wrs, colors, t }) {
  const entries = [];
  for (const c of courses) {
    const board = leaderboardAt(events, c.slug, t);
    if (board.length < 2) continue;            // need a real #2 to be on fire
    entries.push({
      slug: c.slug,
      hit: c.hit,
      color: colors[board[0].player] || NEUTRAL,
      t1: board[0].ms,
      t2: board[1].ms,
      wr: wrs[c.slug] ?? null,
    });
  }
  return onFireCourses(entries);
}
```

- [ ] **Step 4: Run the tests to verify they pass**

From `web/`: `npx vitest run src/lib/onFire.test.js`
Expected: PASS (all four).

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/onFire.js web/src/lib/onFire.test.js
git commit -m "feat(web): onFireCourses + fireListAt - the on-fire set for a frame"
```

---

### Task B2: `MapFireLayer.svelte` flame component

**Files:**
- Create: `web/src/MapFireLayer.svelte`

**Interfaces:**
- Consumes: `courses` prop = `fireListAt(...)` output `[{ slug, hit:{x,y,w,h}, color }]` (extra fields ignored).
- Produces: a `<MapFireLayer {courses} />` overlay; renders one fading flame box per course, all animated by one shared rAF.

No unit test (animated Svelte component). Verification: svelte-check + build; the flame look is then tuned live with the user (as the card/logo flames were). The constants below (`VW/VH`, `LAYERS`, `boxStyle`) are starting values.

- [ ] **Step 1: Create the component**

Create `web/src/MapFireLayer.svelte`:

```svelte
<script>
  // Shared-rAF "on fire" flames for dominated course icons on the territory map. ONE rAF loop
  // animates compact metaball flame columns for ALL on-fire courses (reusing the card/logo Fire
  // technique: ellipses through an SVG goo filter, hued per leader colour). Each flame is a
  // box anchored over its icon, behind the sprite. Self-contained sibling of Fire.svelte /
  // WordmarkFire.svelte (own inline colour helpers, by that established pattern). Shape/density
  // are tuned live. Binary on/off via Svelte fade transitions; honours reduced-motion.
  import { onDestroy, tick } from "svelte";
  import { fade } from "svelte/transition";

  export let courses = [];   // [{ slug, hit:{x,y,w,h}, color }] - the on-fire set

  const NS = "http://www.w3.org/2000/svg";
  const SPEED = 0.7;
  const reduced = typeof matchMedia !== "undefined" && matchMedia("(prefers-reduced-motion: reduce)").matches;
  const uid = "mfgoo-" + Math.random().toString(36).slice(2, 8);

  function hexToHsl(hex) {
    hex = (hex || "#888888").replace("#", "");
    if (hex.length === 3) hex = hex.split("").map((c) => c + c).join("");
    const r = parseInt(hex.substr(0, 2), 16) / 255, g = parseInt(hex.substr(2, 2), 16) / 255, b = parseInt(hex.substr(4, 2), 16) / 255;
    const mx = Math.max(r, g, b), mn = Math.min(r, g, b); let h, s, l = (mx + mn) / 2;
    if (mx === mn) { h = s = 0; } else {
      const d = mx - mn; s = l > 0.5 ? d / (2 - mx - mn) : d / (mx + mn);
      h = mx === r ? (g - b) / d + (g < b ? 6 : 0) : mx === g ? (b - r) / d + 2 : (r - g) / d + 4; h *= 60;
    }
    return { h: Math.round(h), s: Math.round(s * 100), l: Math.round(l * 100) };
  }
  const css = (o, a = 1) => `hsl(${o.h} ${o.s}% ${o.l}% / ${a})`;
  function palette(hex) {
    const { h, s, l } = hexToHsl(hex);
    return {
      outer: { h, s: Math.min(100, s + 14), l: Math.max(24, l - 20) },
      mid:   { h, s: Math.min(100, s + 8),  l: Math.max(44, l - 2) },
      inner: { h, s: Math.min(100, s),      l: Math.min(76, l + 16) },
      core:  { h, s: Math.max(20, s - 42),  l: Math.min(95, l + 38) },
    };
  }

  // per-course flame viewBox (narrow column) + its layers (a compact subset of Fire's BACK).
  const VW = 56, VH = 96, CX = VW / 2;
  const LAYERS = (P) => [
    { color: P.outer, n: 10, rise: [0.5, 0.95],  r0: [9, 14], spread: 14, sway: 4, top: 0.10, stretch: 1.9, thin: 0.50, baseY: [0.70, 1.00] },
    { color: P.mid,   n: 8,  rise: [0.55, 1.0],  r0: [7, 11], spread: 12, sway: 4, top: 0.16, stretch: 2.0, thin: 0.54, baseY: [0.64, 0.94] },
    { color: P.inner, n: 6,  rise: [0.6, 1.05],  r0: [5, 8],  spread: 9,  sway: 3, top: 0.24, stretch: 2.0, thin: 0.58, baseY: [0.58, 0.86] },
    { color: P.core,  n: 4,  rise: [0.6, 1.05],  r0: [3, 6],  spread: 6,  sway: 3, top: 0.32, stretch: 2.0, thin: 0.60, baseY: [0.52, 0.78] },
  ];

  // flame box over the icon: a column a touch wider than the icon, ~2.2x tall, rising from its
  // foot. hit is fractions of the stage; output is a % style. Tuned live.
  function boxStyle(hit) {
    const w = hit.w * 1.4, h = hit.h * 2.2;
    const left = hit.x + hit.w / 2 - w / 2;
    const top = hit.y + hit.h - h + hit.h * 0.18;
    const p = (v) => (v * 100).toFixed(3) + "%";
    return `left:${p(left)};top:${p(top)};width:${p(w)};height:${p(h)}`;
  }

  let groups = {};       // slug -> <g> element (bound per course)
  let blobs = [];        // { el, slug, ... } across all courses
  let raf = 0, running = false, t = 0;

  function resetBlob(b, L, scatter) {
    b.base = CX + (Math.random() * 2 - 1) * L.spread;
    b.vy = (L.rise[0] + Math.random() * (L.rise[1] - L.rise[0])) * SPEED;
    b.r0 = L.r0[0] + Math.random() * (L.r0[1] - L.r0[0]);
    b.phase = Math.random() * 6.28; b.sway = L.sway * (0.5 + Math.random() * 0.9);
    b.stretch = L.stretch; b.thin = L.thin; b.L = L; b.topY = VH * L.top;
    const baseLo = VH * L.baseY[0], baseHi = VH * L.baseY[1];
    b.y = scatter ? (b.topY + Math.random() * (baseHi - b.topY)) : baseLo + Math.random() * (baseHi - baseLo);
    b.spawnY = b.y;
  }
  function applyBlob(b, pr, x) {
    const rx = Math.max(0, b.r0 * (1 - pr * b.thin)), ry = b.r0 * (0.7 + pr * b.stretch);
    b.el.setAttribute("cx", x.toFixed(1)); b.el.setAttribute("cy", b.y.toFixed(1));
    b.el.setAttribute("rx", rx.toFixed(1)); b.el.setAttribute("ry", ry.toFixed(1));
  }
  function buildBlobs() {
    blobs = [];
    for (const c of courses) {                  // only current on-fire courses (leaving ones keep their frozen <g> during fade-out)
      const g = groups[c.slug]; if (!g) continue;
      while (g.firstChild) g.removeChild(g.firstChild);
      const P = palette(c.color);
      for (const L of LAYERS(P)) {
        for (let i = 0; i < L.n; i++) {
          const e = document.createElementNS(NS, "ellipse"); e.setAttribute("fill", css(L.color)); g.appendChild(e);
          const b = { el: e, slug: c.slug }; resetBlob(b, L, true); blobs.push(b);
          if (reduced) applyBlob(b, 0.5, b.base);   // static mid-rise frame
        }
      }
    }
  }
  function frame() {
    t += 0.016 * SPEED;
    for (const b of blobs) {
      b.y -= b.vy;
      const span = Math.max(6, b.spawnY - b.topY), pr = Math.min(1, Math.max(0, (b.spawnY - b.y) / span));
      const x = b.base + (CX - b.base) * pr * 0.2 + Math.sin(t * 2.4 + b.phase) * b.sway * (0.3 + pr);
      if (b.y < b.topY || (b.r0 * (1 - pr * b.thin)) < 0.4) { resetBlob(b, b.L, false); continue; }
      applyBlob(b, pr, x);
    }
    raf = requestAnimationFrame(frame);
  }
  function start() { if (running || reduced) return; running = true; raf = requestAnimationFrame(frame); }
  function stop() { running = false; if (raf) cancelAnimationFrame(raf); raf = 0; }

  // Rebuild blobs whenever the on-fire set (slug or colour) changes, after the new <g> nodes mount.
  let prevKey = "";
  $: {
    const key = courses.map((c) => c.slug + ":" + c.color).join(",");
    if (key !== prevKey) {
      prevKey = key;
      tick().then(() => {
        buildBlobs();
        if (reduced) stop();
        else if (blobs.length) start();
        else stop();
      });
    }
  }
  onDestroy(stop);
</script>

{#each courses as c (c.slug)}
  <div class="flame" style={boxStyle(c.hit)} transition:fade={{ duration: 260 }} aria-hidden="true">
    <div class="glow" style="background: radial-gradient(60% 60% at 50% 78%, {css(palette(c.color).mid, 0.5)}, {css(palette(c.color).outer, 0.12)} 60%, transparent 82%)"></div>
    <svg class="svg" viewBox="0 0 {VW} {VH}" preserveAspectRatio="none">
      <defs>
        <filter id="{uid}-{c.slug}" x="-60%" y="-30%" width="220%" height="170%" color-interpolation-filters="sRGB">
          <feGaussianBlur stdDeviation="2.6" result="b" />
          <feColorMatrix in="b" type="matrix" values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 17 -6" />
        </filter>
      </defs>
      <g bind:this={groups[c.slug]} filter="url(#{uid}-{c.slug})"></g>
    </svg>
  </div>
{/each}

<style>
  .flame { position: absolute; pointer-events: none; }
  .glow { position: absolute; inset: 0; mix-blend-mode: screen; filter: blur(6px); animation: mf-flick 3.4s ease-in-out infinite; }
  @keyframes mf-flick { 0%, 100% { opacity: 0.5; } 50% { opacity: 0.85; } }
  .svg { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; }
  @media (prefers-reduced-motion: reduce) { .glow { animation: none; } }
</style>
```

- [ ] **Step 2: Verify it type-checks and builds**

From `web/`: `npm run check && npm run build`
Expected: check 0/0; build succeeds. (The component isn't mounted yet — Task B3 wires it in. This step confirms it compiles.)

- [ ] **Step 3: Commit**

```bash
git add web/src/MapFireLayer.svelte
git commit -m "feat(web): MapFireLayer - shared-rAF flame overlay for on-fire course icons"
```

---

### Task B3: Wire the flames into the map

**Files:**
- Modify: `web/src/WorldMap.svelte` (import + reactive `fireList` + place `<MapFireLayer>` in the stage)

**Interfaces:**
- Consumes: `fireListAt` (B1), `MapFireLayer` (B2), the reactive `frameTime` (A2), `tlEvents`/`tlWrs`/`tlColors`, `manifest.courses`.

No unit test (component wiring). Verification: svelte-check + build + full suite + manual.

- [ ] **Step 1: Add imports**

In `WorldMap.svelte`, after the `TimelineScrubber` import (line 9), add:

```js
  import MapFireLayer from "./MapFireLayer.svelte";
  import { fireListAt } from "./lib/onFire.js";
```

- [ ] **Step 2: Derive the on-fire list reactively**

In `WorldMap.svelte`, add this reactive statement just after the `$: frameTime = ...` line (added in A2):

```js
  // On-fire courses for the shown frame (live or scrubbed-back), from the in-memory data.
  $: fireList = (timelineReady && manifest)
    ? fireListAt({ courses: manifest.courses, events: tlEvents, wrs: tlWrs, colors: tlColors, t: frameTime })
    : [];
```

- [ ] **Step 3: Place the flame layer between the territory canvas and the icons**

In the stage markup, the territory canvas is followed by `<div class="icons">`. Insert `<MapFireLayer>` between them. Currently:

```svelte
        <canvas class="territory" bind:this={terr} aria-hidden="true"></canvas>
        <div class="icons">
```

becomes:

```svelte
        <canvas class="territory" bind:this={terr} aria-hidden="true"></canvas>
        <!-- on-fire flames sit behind the icons, above the territory paint -->
        <MapFireLayer courses={fireList} />
        <div class="icons">
```

- [ ] **Step 4: Verify check, build, suite**

From `web/`: `npm run check && npm run build && npm test`
Expected: check 0/0; build succeeds; all tests pass.

- [ ] **Step 5: Manual smoke (deferred to controller/human)**

With the season server running, `npm run dev`, Territory page: at LIVE, dominated courses show a leader-coloured flame behind their icon; scrubbing changes which courses burn to match that moment; a single-competitor course never burns; reduced-motion shows a static glow. (This is where the flame look gets tuned live.)

- [ ] **Step 6: Commit**

```bash
git add web/src/WorldMap.svelte
git commit -m "feat(web): render on-fire flames on the territory map every frame"
```

---

## Self-Review

**1. Spec coverage:**
- A1 server `wrs` on timeline payload -> Task A1. [covered]
- A2 retain `tlWrs` -> A2 Step 6. [covered]
- LIVE = last snapshot; `/v1/territory` fallback only -> Task A3. [covered]
- Popups one instant path; dead code removed; `buildCourseView` sole builder; WR-as-number -> Task A2. [covered]
- `frameTime = atLive ? Infinity : snapshots[tlIndex].t` -> A2 Step 7, used in A2 Step 8 + B3 Step 2. [covered]
- `onFireCourses` / `fireListAt` pure + tested -> Task B1. [covered]
- `MapFireLayer` shared rAF, behind icons, fade, reduced-motion, self-contained -> Task B2 + B3 Step 3. [covered]
- Fire on every frame, reactive to `tlIndex` -> B3 Step 2. [covered]
- Don't touch Fire.svelte / WordmarkFire.svelte -> Global Constraints; B2 is a new file. [covered]
- Historical WR = current `tlWrs`, single swap-in point -> A2 Step 8 + B1 (wr input is one value). [covered]

**2. Placeholder scan:** No TBD/TODO/"handle edge cases". Every code step shows full code. The MapFireLayer constants are explicitly "starting values, tuned live" — complete and runnable, not placeholders. [clean]

**3. Type consistency:** `wrs: Record<string,number>` (A1) -> `tlWrs` slug->number (A2) -> `wr: tlWrs[slug] ?? null` into `buildCourseView({...wr})` where `wr` is number|null (A2 courseData) and into `fireListAt` -> entry `wr` number|null -> `isOnFire({wr})` (number). `buildCourseView({ standings, colorByName, courseName, wr })` signature matches its call site (A2 Step 8). `fireListAt({ courses, events, wrs, colors, t })` matches its call site (B3 Step 2). `MapFireLayer` `courses` prop = `fireListAt` output, uses `.slug/.hit/.color` (B2). `frameTime` defined A2 Step 7, consumed A2 Step 8 + B3 Step 2. [consistent]
