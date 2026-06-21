# Live Heat-Graph Page (`#/heat`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a URL-only page at `thekartoff.com/#/heat` that renders the live "on fire" heat-graph (how a PB is judged hot or not), auto-updating from current PBs and WRs and never drifting from the territory map's flames.

**Architecture:** A hidden Svelte route in the `web/` SPA reuses the same public endpoint (`/v1/territory/timeline`), course manifest, timeline reducer (`leaderboardAt`), and locked fire model (`fireModel.js`) at the same live frame (`t = Infinity`) as the territory map. A new pure module (`heat.js`) turns the timeline into per-course rows; the map's existing `onFire.js` is refactored to share that derivation so the two cannot disagree.

**Tech Stack:** Svelte 4, Vite 5, Vitest 4 (web app); data from the existing Hono server endpoint `/v1/territory/timeline`.

## Global Constraints

- **No new server route.** Reuse the existing public `GET /v1/territory/timeline` → `{ events, colors, wrs }`. Do not add `/v1/fire` or touch `pi/`.
- **One source of truth for the math.** The page imports `fireModel.js` (`E0 = 0.2`, `K = 4`, `fireBarPct`, `isOnFire`, `snuffLeadMs`). No private copy of the formula.
- **Live frame only:** `t = Infinity` (matches the map's live flames). No historical scrubbing on this page.
- **URL-only / unlisted:** add the route to `view.js` and render it in `App.svelte`, but **do not** add a navbar tab.
- **Preserve `onFire.js`'s public shape:** `fireListAt(...)` must keep returning `{ slug, hit, color, t1, t2, wr }` (consumed by `WorldMap.svelte` → `MapFireLayer.svelte`).
- **Pure modules stay pure:** `heat.js` has no DOM/fetch; all I/O lives in the Svelte component.
- All commits include the repo's standard trailers (`Co-Authored-By:` + `Claude-Session:`).

---

### Task 1: Route recognition for `#/heat`

**Files:**
- Modify: `web/src/lib/view.js`
- Test: `web/src/lib/view.test.js` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `viewFromHash(hash: string) -> "live" | "territory" | "heat"`.

- [ ] **Step 1: Write the failing test**

Create `web/src/lib/view.test.js`:

```js
import { describe, it, expect } from "vitest";
import { viewFromHash } from "./view.js";

describe("viewFromHash", () => {
  it("maps territory and heat, and falls back to live", () => {
    expect(viewFromHash("#/territory")).toBe("territory");
    expect(viewFromHash("#/heat")).toBe("heat");
    expect(viewFromHash("#/")).toBe("live");
    expect(viewFromHash("")).toBe("live");
    expect(viewFromHash("#/nope")).toBe("live");
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm --prefix web test -- view`
Expected: FAIL — the `"#/heat"` case returns `"live"` (current code only knows `territory`).

- [ ] **Step 3: Update `view.js`**

Replace the whole file `web/src/lib/view.js` with:

```js
// Views selected by the location hash. Unknown hashes fall back to "live".
// `heat` is intentionally unlisted (no navbar tab) — reachable by URL only.
export function viewFromHash(hash) {
  const h = (hash || "").replace(/^#\/?/, "");
  if (h === "territory") return "territory";
  if (h === "heat") return "heat";
  return "live";
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm --prefix web test -- view`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/view.js web/src/lib/view.test.js
git commit -m "feat(web): recognize the unlisted #/heat route"
```

---

### Task 2: `heat.js` per-course rows + share the derivation with `onFire.js`

**Files:**
- Create: `web/src/lib/heat.js`
- Create: `web/src/lib/heat.test.js`
- Modify: `web/src/lib/onFire.js`
- Existing test that must still pass: `web/src/lib/onFire.test.js`

**Interfaces:**
- Consumes: `leaderboardAt(events, slug, t)` from `./timeline.js`; `fireBarPct`, `isOnFire`, `snuffLeadMs` from `./fireModel.js`.
- Produces:
  - `courseRowAt({ course, events, wrs, colors, t }) -> Row | null` where
    `Row = { slug, name, leader, color, t1, t2, wr, leadPct, offPct, barPct, fire, snuffMs }`.
    Returns `null` when the course lacks a real #2 **or** a current WR.
  - `heatRows({ courses, events, wrs, colors, t }) -> Row[]` (one row per qualifying course).
  - `onFire.js` keeps `fireListAt({ courses, events, wrs, colors, t }) -> { slug, hit, color, t1, t2, wr }[]` and `onFireCourses(entries)` unchanged in shape.

- [ ] **Step 1: Write the failing test**

Create `web/src/lib/heat.test.js`:

```js
import { describe, it, expect } from "vitest";
import { heatRows } from "./heat.js";
import { fireListAt } from "./onFire.js";

const courses = [
  { slug: "mc", name: "Mario Circuit" },
  { slug: "pb", name: "Peach Beach" },
  { slug: "rr", name: "Rainbow Road" }, // single competitor -> excluded
  { slug: "nw", name: "No WR Course" },  // has a #2 but no WR -> excluded
];
const events = [
  { t: 1000, player: "Gub",  slug: "mc", ms: 110579 },
  { t: 1000, player: "Paul", slug: "mc", ms: 114914 }, // 4.0% lead -> on fire
  { t: 1000, player: "Gub",  slug: "pb", ms: 104887 },
  { t: 1000, player: "Paul", slug: "pb", ms: 104917 }, // 30ms lead -> marginal, not on fire
  { t: 1000, player: "Gub",  slug: "rr", ms: 256426 },
  { t: 1000, player: "Gub",  slug: "nw", ms: 100000 },
  { t: 1000, player: "Paul", slug: "nw", ms: 100500 },
];
const wrs = { mc: 107414, pb: 100139, rr: 233693 }; // nw absent on purpose
const colors = { Gub: "#2dd4bf", Paul: "#a78bfa" };

describe("heatRows", () => {
  it("emits one row per course with both a real #2 and a current WR", () => {
    const rows = heatRows({ courses, events, wrs, colors, t: Infinity });
    expect(rows.map((r) => r.slug).sort()).toEqual(["mc", "pb"]);
  });

  it("computes leader, colour, name, lead% and off% of WR", () => {
    const mc = heatRows({ courses, events, wrs, colors, t: Infinity }).find((r) => r.slug === "mc");
    expect(mc).toMatchObject({ name: "Mario Circuit", leader: "Gub", color: "#2dd4bf", t1: 110579, t2: 114914, wr: 107414 });
    expect(mc.leadPct).toBeCloseTo(4.0349, 3);
    expect(mc.offPct).toBeCloseTo(2.9466, 3);
    expect(mc.fire).toBe(true);
  });

  it("flags a marginal lead as not on fire under the locked model", () => {
    const pb = heatRows({ courses, events, wrs, colors, t: Infinity }).find((r) => r.slug === "pb");
    expect(pb.fire).toBe(false);
  });

  it("excludes courses before their runner-up exists at t", () => {
    expect(heatRows({ courses, events, wrs, colors, t: 999 })).toEqual([]);
  });
});

describe("heat <-> map parity (no-drift guarantee)", () => {
  it("the lit slug set from heatRows equals the map's fireListAt set", () => {
    const lit = heatRows({ courses, events, wrs, colors, t: Infinity }).filter((r) => r.fire).map((r) => r.slug).sort();
    const mapLit = fireListAt({ courses, events, wrs, colors, t: Infinity }).map((e) => e.slug).sort();
    expect(lit).toEqual(mapLit);
    expect(lit).toEqual(["mc"]);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm --prefix web test -- heat`
Expected: FAIL — `Failed to resolve import "./heat.js"` (module does not exist yet).

- [ ] **Step 3: Create `heat.js`**

Create `web/src/lib/heat.js`:

```js
// Per-course competitive standing + "on fire" metrics, derived live from the timeline event
// stream. Uses the same inputs as the territory map's flames (leaderboardAt + fireModel), so the
// heat page and the map cannot disagree on which courses are lit. Pure: no DOM, no fetch.
import { fireBarPct, isOnFire, snuffLeadMs } from "./fireModel.js";
import { leaderboardAt } from "./timeline.js";

const NEUTRAL = "#888";

/** Standing + fire metrics for one course AS OF `t`, or null when the course lacks a real #2 or
 *  a current WR (both are required to judge "on fire", matching the explorer's regen). */
export function courseRowAt({ course, events, wrs, colors, t }) {
  const board = leaderboardAt(events, course.slug, t);
  const wr = wrs[course.slug] ?? null;
  if (board.length < 2 || !wr) return null;
  const t1 = board[0].ms;
  const t2 = board[1].ms;
  const leader = board[0].player;
  const offPct = ((t1 - wr) / wr) * 100;
  return {
    slug: course.slug,
    name: course.name,
    leader,
    color: colors[leader] || NEUTRAL,
    t1,
    t2,
    wr,
    leadPct: ((t2 - t1) / wr) * 100, // lead over #2, % of WR (x axis)
    offPct,                          // how far the PB sits off the WR, % (y axis)
    barPct: fireBarPct(offPct),      // locked fire bar at this off%
    fire: isOnFire({ t1, t2, wr }),  // locked-model verdict (map parity / no-drift)
    snuffMs: snuffLeadMs({ t1, wr }),// lead in ms a rival must beat to snuff
  };
}

/** One row per qualifying course (real #2 + current WR) as of `t`. */
export function heatRows({ courses, events, wrs, colors, t }) {
  const rows = [];
  for (const c of courses) {
    const row = courseRowAt({ course: c, events, wrs, colors, t });
    if (row) rows.push(row);
  }
  return rows;
}
```

- [ ] **Step 4: Refactor `onFire.js` to reuse `courseRowAt`**

Replace the whole file `web/src/lib/onFire.js` with:

```js
// Which courses are "on fire" for a given frame, and the render list for the flame layer.
// Pure: reuses the shared per-course derivation (heat.courseRowAt) so the map's flames and the
// heat page agree by construction. fireListAt keeps its { slug, hit, color, t1, t2, wr } shape
// (consumed by WorldMap -> MapFireLayer).
import { isOnFire } from "./fireModel.js";
import { courseRowAt } from "./heat.js";

/** Subset of `entries` ({ slug, t1, t2, wr, ...passthrough }) that is on fire; entries returned
 *  unchanged so render fields (hit, color) ride along. */
export function onFireCourses(entries) {
  return entries.filter((e) => isOnFire({ t1: e.t1, t2: e.t2, wr: e.wr }));
}

/** On-fire render list for the shown frame: each lit course's standing AS OF `t` (shared
 *  derivation), the leader's colour, and the course's hit box. */
export function fireListAt({ courses, events, wrs, colors, t }) {
  const out = [];
  for (const c of courses) {
    const row = courseRowAt({ course: c, events, wrs, colors, t });
    if (row && row.fire) out.push({ slug: row.slug, hit: c.hit, color: row.color, t1: row.t1, t2: row.t2, wr: row.wr });
  }
  return out;
}
```

- [ ] **Step 5: Run the full web suite to verify everything passes**

Run: `npm --prefix web test`
Expected: PASS — `heat.test.js` (incl. the parity test), the unchanged `onFire.test.js`, and all existing web tests are green.

- [ ] **Step 6: Commit**

```bash
git add web/src/lib/heat.js web/src/lib/heat.test.js web/src/lib/onFire.js
git commit -m "feat(web): heat.js per-course fire rows; share derivation with onFire"
```

---

### Task 3: `HeatGraph.svelte` page + wire it into `App.svelte`

**Files:**
- Create: `web/src/HeatGraph.svelte`
- Modify: `web/src/App.svelte`

**Interfaces:**
- Consumes: `heatRows` from `./lib/heat.js`; `E0`, `K` from `./lib/fireModel.js`; `territoryTimelineUrl` from `./lib/api.js`; `manifestUrl` from `./lib/map.js`.
- Produces: a self-contained page component rendered when `view === "heat"`. No exports.

- [ ] **Step 1: Create `HeatGraph.svelte`**

Create `web/src/HeatGraph.svelte`:

```svelte
<script>
  import { onMount } from "svelte";
  import { territoryTimelineUrl } from "./lib/api.js";
  import { manifestUrl } from "./lib/map.js";
  import { heatRows } from "./lib/heat.js";
  import { E0 as E0_LOCKED, K as K_LOCKED } from "./lib/fireModel.js";

  // Geometry, ported from tools/fire-model-explorer.html.
  const LEADX = 8.0;  // x-axis span: lead over #2, % of WR
  const OFFY = 20.0;  // y-axis span: how far off the WR, %
  const GUT = 172;    // right gutter (px) for the course-label column
  const H = 440;      // plot height (px)
  const COLS = 24, ROWS = 20;

  let rows = [];               // heatRows(): one entry per qualifying course (locked metrics)
  let loaded = false, error = false;
  let sceneW = 720;            // measured plot width (bind:clientWidth)

  // Live, slider-driven tuning knobs, initialised to the LOCKED model so the first paint matches
  // the territory map's flames; dragging is a local what-if (never touches the map or the model).
  let E0 = E0_LOCKED;
  let K = K_LOCKED;

  const clamp01 = (x) => Math.max(0, Math.min(1, x));
  const lerp = (a, b, t) => a + (b - a) * t;
  const bar = (off) => E0 * Math.exp(off / K); // slider-driven fire bar at a given off%

  // Warm -> hot colour ramp for the lit region of the heatmap.
  const STOPS = [[0,[20,22,26]],[0.25,[64,22,16]],[0.45,[150,52,20]],[0.65,[222,110,30]],[0.85,[245,182,44]],[1,[253,230,162]]];
  function ramp(d) {
    d = clamp01(d);
    for (let i = 1; i < STOPS.length; i++) {
      if (d <= STOPS[i][0]) {
        const d0 = STOPS[i - 1][0], c0 = STOPS[i - 1][1], d1 = STOPS[i][0], c1 = STOPS[i][1], t = (d - d0) / (d1 - d0);
        return `rgb(${Math.round(lerp(c0[0],c1[0],t))},${Math.round(lerp(c0[1],c1[1],t))},${Math.round(lerp(c0[2],c1[2],t))})`;
      }
    }
    return "rgb(253,230,162)";
  }
  const fmt = (ms) => { const s = ms/1000, m = Math.floor(s/60), r = s - m*60; return `${m}:${r<10?"0":""}${r.toFixed(3)}`; };

  // Heatmap cells: lit where lead clears the bar at that row's off%.
  $: cells = (() => {
    const out = [];
    for (let r = 0; r < ROWS; r++) {
      const off = OFFY * (r + 0.5) / ROWS, b = bar(off);
      for (let c = 0; c < COLS; c++) {
        const lead = LEADX * (c + 0.5) / COLS;
        out.push(lead >= b ? ramp(0.35 + 0.65 * Math.exp(-off / K)) : null);
      }
    }
    return out;
  })();

  $: cw = Math.max(80, sceneW - GUT); // plot width minus the label gutter

  // Per-course plotted points; `lit` is recomputed from the LIVE sliders (not the locked row.fire).
  $: pts = rows.map((r) => ({
    ...r,
    lit: r.leadPct >= bar(r.offPct),
    x: clamp01(r.leadPct / LEADX) * cw,
    y: clamp01(r.offPct / OFFY) * H,
  }));

  // Label column: stack top-down, push each down to avoid overlap, lift back if it overflows the
  // bottom (ported from the explorer's de-collision).
  $: labels = (() => {
    const ls = pts.map((p) => ({ ...p })).sort((a, b) => a.y - b.y);
    const minG = 14; let last = -1e9;
    ls.forEach((s) => { s.ly = Math.max(s.y, last + minG); last = s.ly; });
    const over = last - (H - 6); if (over > 0) ls.forEach((s) => (s.ly -= over));
    return ls;
  })();

  // The exponential bar as an SVG polyline across the plot.
  $: barPts = (() => {
    const p = [];
    for (let o = 0; o <= OFFY; o += 0.4) {
      p.push(`${clamp01(bar(o) / LEADX) * cw},${(o / OFFY) * H}`);
      if (bar(o) > LEADX) break;
    }
    return p.join(" ");
  })();

  $: lit = pts.filter((p) => p.lit).sort((a, b) => a.offPct - b.offPct);

  // Floor readout: the lead a rival must beat (at WR pace) on the shortest vs longest WR.
  $: floor = (() => {
    if (!rows.length) return null;
    const wrSorted = rows.map((r) => r.wr).sort((a, b) => a - b);
    const sec = (wr) => (E0 / 100 * wr / 1000).toFixed(2);
    return { lo: sec(wrSorted[0]), hi: sec(wrSorted[wrSorted.length - 1]) };
  })();

  onMount(async () => {
    try {
      const [mf, tl] = await Promise.all([
        fetch(manifestUrl(), { cache: "no-store" }).then((r) => { if (!r.ok) throw new Error(`manifest ${r.status}`); return r.json(); }),
        fetch(territoryTimelineUrl(150)).then((r) => { if (!r.ok) throw new Error(`timeline ${r.status}`); return r.json(); }),
      ]);
      const { events, colors, wrs } = tl;
      rows = heatRows({ courses: mf.courses, events: events || [], wrs: wrs || {}, colors: colors || {}, t: Infinity });
      loaded = true;
    } catch (e) {
      console.error("heat graph load failed", e);
      error = true;
    }
  });
</script>

<section class="heat">
  <h2>"on fire" model — live</h2>
  <p class="sub">A course burns while its leader's margin over #2 clears the exponential bar
    <b>fireBar(off) = E₀ · e^(off/K)</b> (lead &amp; bar in % of WR). Live Season PBs vs current WRs.</p>

  <div class="knobs">
    <label>floor E₀ <b>{E0.toFixed(2)}</b>% of WR
      <input type="range" min="0.05" max="1.0" step="0.05" bind:value={E0} /></label>
    <label>steepness K <b>{K.toFixed(1)}</b>
      <input type="range" min="3" max="10" step="0.5" bind:value={K} /></label>
    <button class="reset" on:click={() => { E0 = E0_LOCKED; K = K_LOCKED; }}>reset to locked</button>
  </div>

  {#if error}
    <p class="msg">Couldn't load live data.</p>
  {:else if !loaded}
    <p class="msg">Loading…</p>
  {:else if !rows.length}
    <p class="msg">No courses have a #1, a #2, and a current WR yet.</p>
  {:else}
    <div class="chartrow">
      <div class="yax"><b>≈ WR pace</b><span>off the WR ↓</span><b>{OFFY}% off</b></div>
      <div class="scene" bind:clientWidth={sceneW}>
        <div class="chart" style="right:{GUT}px">
          <div class="cells" style="grid-template-columns:repeat({COLS},1fr);grid-template-rows:repeat({ROWS},1fr)">
            {#each cells as c}<div style={c ? `background:${c}` : "background:#15171b;opacity:.5"}></div>{/each}
          </div>
        </div>
        <svg class="svg" viewBox="0 0 {sceneW} {H}" preserveAspectRatio="none">
          <polyline points={barPts} fill="none" stroke="#f5b62c" stroke-width="2" opacity="0.9" />
          {#each labels as s}
            <line x1={s.x} y1={s.y} x2={cw + 6} y2={s.ly} stroke={s.lit ? "#7a4a1e" : "#26292f"} stroke-width="1" />
          {/each}
        </svg>
        {#each pts as s}
          <div class="dot" class:fire={s.lit} style="left:{s.x}px;top:{s.y}px;background:{s.color}"
               title="{s.name} — {s.leader}, lead {((s.t2 - s.t1)/1000).toFixed(2)}s ({s.leadPct.toFixed(2)}% WR), {s.offPct.toFixed(1)}% off WR{s.lit ? '  ON FIRE' : ''}"></div>
        {/each}
        {#each labels as s}
          <div class="lab" class:fire={s.lit} style="left:{cw + 10}px;top:{s.ly}px">
            {s.name} <span class="d">{s.offPct.toFixed(1)}% off</span>
          </div>
        {/each}
      </div>
    </div>
    <div class="xax" style="padding-right:{GUT}px"><span>0%</span><span>2%</span><span>4%</span><span>6%</span><span>8%</span></div>
    <div class="xtitle" style="padding-right:{GUT}px">lead over #2, as % of WR →</div>

    {#if floor}
      <div class="floor">At WR pace, a rival must get within <b>{E0.toFixed(2)}% of WR</b> to snuff —
        <span class="mono">{floor.lo}s</span> on the shortest track, <span class="mono">{floor.hi}s</span> on the longest.</div>
    {/if}

    <div class="firelist">
      <div class="hd">{lit.length} on fire — sorted by closeness to WR:</div>
      {#if !lit.length}<div class="frow dim">Nothing lit.</div>{/if}
      {#each lit as s}
        <div class="frow">🔥 <b>{s.name}</b> — {s.leader} <span class="mono">{fmt(s.t1)}</span>
          ({s.offPct.toFixed(1)}% off), leads <span class="mono">{((s.t2 - s.t1)/1000).toFixed(2)}s</span>.
          Snuffed only if a rival is within <span class="mono out">{(bar(s.offPct)/100*s.wr/1000).toFixed(2)}s</span>
          (under <span class="mono out">{fmt(s.t1 + bar(s.offPct)/100*s.wr)}</span>).</div>
      {/each}
    </div>
  {/if}
</section>

<style>
  .heat{max-width:1040px;margin:0 auto;padding:22px 24px;color:#c7ccd2;
        font-family:'Inter',system-ui,-apple-system,"Segoe UI",sans-serif;}
  h2{color:#e8eaed;font-size:18px;margin:0 0 4px;}
  .sub{color:#8a8f98;font-size:13px;margin:0 0 12px;max-width:760px;}
  .sub b{color:#cfd3d8;font-weight:600;}
  .knobs{display:flex;gap:22px;flex-wrap:wrap;align-items:flex-end;margin-bottom:14px;font-size:11.5px;}
  .knobs label{display:flex;flex-direction:column;gap:3px;min-width:220px;color:#8a8f98;}
  .knobs b{color:#f5b62c;}
  .knobs input{width:100%;accent-color:#3d7cc2;}
  .reset{background:#16181d;color:#aeb4bc;border:1px solid #2a2e35;border-radius:4px;padding:5px 9px;font-size:11px;cursor:pointer;}
  .reset:hover{color:#f3f4f6;border-color:#3a3f48;}
  .msg{color:#8a8f98;font-size:13px;padding:24px 0;}
  .chartrow{display:flex;gap:10px;align-items:stretch;}
  .yax{width:62px;display:flex;flex-direction:column;justify-content:space-between;text-align:right;
       font-size:10px;color:#7a818b;padding:1px 0;line-height:1.2;}
  .yax b{color:#cfd3d8;font-weight:600;}
  .scene{position:relative;flex:1 1 auto;min-width:0;height:440px;}
  .chart{position:absolute;left:0;top:0;bottom:0;border:1px solid #23262b;border-radius:5px;overflow:hidden;}
  .cells{position:absolute;inset:0;display:grid;}
  .cells > div{min-width:0;}
  .svg{position:absolute;inset:0;width:100%;height:100%;pointer-events:none;}
  .dot{position:absolute;width:9px;height:9px;border-radius:50%;transform:translate(-50%,-50%);
       border:1.5px solid #0b0c0e;box-shadow:0 0 0 1px #000;z-index:4;}
  .dot.fire{box-shadow:0 0 0 2px #fff,0 0 11px 3px #f5912c;z-index:6;}
  .lab{position:absolute;transform:translateY(-50%);font-size:9.5px;white-space:nowrap;color:#aeb4bc;z-index:5;}
  .lab.fire{color:#ffce8a;font-weight:600;}
  .lab .d{color:#6f7782;font-variant-numeric:tabular-nums;}
  .xax{display:flex;justify-content:space-between;font-size:10px;color:#7a818b;margin-top:5px;padding-left:72px;}
  .xtitle{text-align:center;font-size:10.5px;color:#8a8f98;margin-top:2px;padding-left:72px;}
  .floor{margin-top:12px;font-size:12px;color:#cdd3da;background:#0e1014;border:1px solid #23262b;border-radius:5px;padding:8px 11px;}
  .floor b{color:#f5b62c;}
  .mono{font-family:ui-monospace,Menlo,monospace;font-variant-numeric:tabular-nums;}
  .firelist{margin-top:13px;font-size:12px;line-height:1.7;}
  .firelist .hd{color:#f7a13a;font-weight:600;margin-bottom:4px;}
  .frow b{color:#fff;}
  .frow .out{color:#f7a13a;}
  .frow.dim{color:#7a818b;}
</style>
```

- [ ] **Step 2: Wire it into `App.svelte`**

In `web/src/App.svelte`, add the import after the `WorldMap` import (line 8):

```js
  import HeatGraph from "./HeatGraph.svelte";
```

Then replace the `<main>` body:

```svelte
<main>
  {#if view === "territory"}<WorldMap />{:else}<CardWall />{/if}
</main>
```

with:

```svelte
<main>
  {#if view === "territory"}<WorldMap />
  {:else if view === "heat"}<HeatGraph />
  {:else}<CardWall />{/if}
</main>
```

(Do **not** add a `<a class="tab">` for heat — the page stays unlisted. The nav marker correctly shows nothing when no tab is active.)

- [ ] **Step 3: Type/lint check**

Run: `npm --prefix web run check`
Expected: `svelte-check` reports 0 errors and 0 warnings.

- [ ] **Step 4: Production build smoke**

Run: `npm --prefix web run build`
Expected: build succeeds (catches any bad import or template/syntax error).

- [ ] **Step 5: Manual visual check**

Start the web dev server (the season server must be reachable — either run `npm --prefix pi run dev` so `localhost:8787` serves data, or build with `VITE_API_BASE=https://api.thekartoff.com`):

```bash
npm --prefix web run dev
```

Then in a browser:
- Open `http://localhost:5173/#/heat` → the heat-graph renders with live dots/labels.
- Open `http://localhost:5173/#/territory` → note which course icons are on fire.
- Confirm the heat page's lit (white-haloed) dots are the **same courses** as the map's flames.
- Confirm there is **no "Heat" tab** in the navbar, and the page is reachable only by typing the URL.
- Drag the E₀/K sliders → the lit set and heatmap update; "reset to locked" restores the map-matching state.

- [ ] **Step 6: Commit**

```bash
git add web/src/HeatGraph.svelte web/src/App.svelte
git commit -m "feat(web): live #/heat fire-model page (url-only, not in nav)"
```

---

## Self-Review

**1. Spec coverage**
- URL-only page, no navbar tab → Task 1 (route) + Task 3 (render without a tab). ✓
- Auto-updates from current PBs/WRs via the existing endpoint → Task 3 `onMount` fetch of `/v1/territory/timeline`; no new server route. ✓
- Never drifts from the map's flames → Task 2 shared `courseRowAt` + the parity test. ✓
- Reuse `fireModel.js` (no private formula copy) → `heat.js` and the component import it. ✓
- Live frame only (`t = Infinity`) → Task 3. ✓
- Keep sliders defaulting to locked → Task 3 (`E0_LOCKED`/`K_LOCKED` + reset). ✓
- Testing: `heat.test.js`, parity test, `svelte-check`, manual map comparison → Tasks 2 + 3. ✓
- Preserve `fireListAt` shape for `MapFireLayer` → Task 2 returns `{ slug, hit, color, t1, t2, wr }`. ✓

**2. Placeholder scan:** No TBD/TODO/"handle edge cases"; every code step shows complete code. ✓

**3. Type consistency:** `courseRowAt`/`heatRows` row fields (`slug, name, leader, color, t1, t2, wr, leadPct, offPct, barPct, fire, snuffMs`) are produced in Task 2 and consumed verbatim in Task 3 (`r.leadPct`, `r.offPct`, `r.wr`, `r.t1`, `r.t2`, `r.name`, `r.leader`, `r.color`). `viewFromHash` returns `"heat"` (Task 1) which `App.svelte` branches on (Task 3). `fireListAt` keeps `{ slug, hit, color, t1, t2, wr }`. ✓
