# SP3 — Territory Map Hover Popup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hovering a course on the World Map (`web/`, `#/map`) opens a popup showing that course's leader (their posted GIF) + the track leaderboard, with dominant leaders shown "on fire."

**Architecture:** Two pure JS modules (`fireModel`, `courseData`) are TDD'd with vitest. A Svelte component (`CoursePopup`) + hover wiring in the existing `WorldMap.svelte` are verified visually with headless Edge (the project's UI ground truth — never OpenCV; see `docs/.../map-icon-haze-fix` notes). A Python script bundles the per-player GIFs into `web/public/players/`. Fire detection is **stateless** (computed live from leaderboard + WR), so no server work.

**Tech Stack:** Svelte 4 + Vite 5 + Vitest 4 (web/), Pillow (bundling script). Reuses desktop `../src/components/Fire.svelte`. Data from existing open reads on `api.thekartoff.com`.

**Design spec:** `docs/superpowers/specs/2026-06-18-sp3-hover-popup-design.md`. The validated visual/interaction reference is `tools/popup-prototype.html` (port its CSS/markup; the differences are: strip narrowed to a ~56px card-style figure column, glance-tooltip interaction, real `Fire.svelte`).

---

## File Structure

- `web/src/lib/fireModel.js` (new) — pure `isOnFire`/`fireBarPct`/`snuffLeadMs` + constants `E0`, `K`. Reused later by SP2 territory strength.
- `web/src/lib/fireModel.test.js` (new) — vitest.
- `web/src/lib/courseData.js` (new) — fetch leaderboard/WR/roster (injectable fetch) + pure `buildCourseView()` assembling the popup view-model.
- `web/src/lib/courseData.test.js` (new) — vitest for `buildCourseView`.
- `web/src/lib/api.js` (new) — exports `API_BASE`; `web/src/main.js` refactored to use it.
- `web/src/CoursePopup.svelte` (new) — the card (strip + leaderboard + fire), driven by a view-model prop.
- `web/src/WorldMap.svelte` (modify) — hover open/close, spring, anchoring, mount `CoursePopup` in the existing `.popups` layer.
- `scripts/bundle_web_player_gifs.py` (new) — copies posted/on-pace GIFs into `web/public/players/`.
- `web/public/players/<player>.gif` + `<player>__fire.gif` (generated, committed).

---

## Task 1: API base helper

**Files:**
- Create: `web/src/lib/api.js`
- Modify: `web/src/main.js:7-8`

- [ ] **Step 1: Create the helper**

```js
// web/src/lib/api.js
// The season server origin. Override for local dev via VITE_API_BASE (e.g. http://localhost:8787).
export const API_BASE =
  (typeof import.meta !== "undefined" && import.meta.env && import.meta.env.VITE_API_BASE) ||
  "https://api.thekartoff.com";
```

- [ ] **Step 2: Use it in main.js**

Replace `web/src/main.js` lines 6-8 (the inline `const API_BASE = ...` + `startPresence`) with:

```js
import { API_BASE } from "./lib/api.js";
startPresence(API_BASE);          // read-only presence socket -> shared stores
```

- [ ] **Step 3: Verify build**

Run: `npm --prefix web run build`
Expected: builds with no errors (outputs to `web/dist`).

- [ ] **Step 4: Commit**

```bash
git add web/src/lib/api.js web/src/main.js
git commit -m "refactor(web): extract API_BASE into lib/api.js"
```

---

## Task 2: Fire model (pure, TDD)

The locked rule, in **percent of WR**: a course is on fire while `lead% >= E0 * e^(off%/K)`, `E0=0.2`, `K=4`, where `lead% = (t2-t1)/wr*100` and `off% = (t1-wr)/wr*100` (t1 = leader ms, t2 = #2 ms). Needs both a #2 and a WR.

**Files:**
- Create: `web/src/lib/fireModel.js`, `web/src/lib/fireModel.test.js`

- [ ] **Step 1: Write the failing test**

```js
// web/src/lib/fireModel.test.js
import { describe, it, expect } from "vitest";
import { isOnFire, fireBarPct, snuffLeadMs, E0, K } from "./fireModel.js";

describe("fireModel", () => {
  it("exposes the locked constants", () => {
    expect(E0).toBe(0.2); expect(K).toBe(4);
  });

  // real Season-1 / 150cc cases (t1 = leader, t2 = #2, wr = current WR), all ms
  it("lights a dominant, near-WR leader (Mario Bros. Circuit)", () => {
    expect(isOnFire({ t1: 110579, t2: 114914, wr: 107414 })).toBe(true);
  });
  it("lights a huge lead even mid-off-WR (Salty Salty Speedway)", () => {
    expect(isOnFire({ t1: 125337, t2: 131168, wr: 114534 })).toBe(true);
  });
  it("stays calm for a tiny lead near WR (Koopa Troopa Beach)", () => {
    expect(isOnFire({ t1: 90953, t2: 91025, wr: 86477 })).toBe(false);
  });
  it("stays calm for a big lead far off WR (Bowser's Castle)", () => {
    expect(isOnFire({ t1: 151846, t2: 155063, wr: 129887 })).toBe(false);
  });
  it("is false without a #2 or without a WR", () => {
    expect(isOnFire({ t1: 110579, t2: null, wr: 107414 })).toBe(false);
    expect(isOnFire({ t1: 110579, t2: 114914, wr: null })).toBe(false);
  });

  it("fireBarPct grows exponentially off the WR", () => {
    expect(fireBarPct(0)).toBeCloseTo(0.2, 6);          // floor = E0
    expect(fireBarPct(4)).toBeCloseTo(0.2 * Math.E, 6); // one K off
  });
  it("snuffLeadMs is the lead in ms a rival must beat to snuff (Mario Bros.)", () => {
    // bar% = 0.2*e^(off/4); off=(110579-107414)/107414*100=2.946% -> bar≈0.4181% -> *wr/100
    expect(snuffLeadMs({ t1: 110579, t2: 114914, wr: 107414 })).toBeGreaterThan(400);
    expect(snuffLeadMs({ t1: 110579, t2: 114914, wr: 107414 })).toBeLessThan(500);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix web test -- fireModel`
Expected: FAIL ("Failed to resolve import ./fireModel.js" / functions undefined).

- [ ] **Step 3: Write the implementation**

```js
// web/src/lib/fireModel.js
// Stateless "on fire" model for the territory map. A course burns while the leader's
// margin over #2 clears an exponential bar that rises the further the PB sits off the WR.
// lead% and off% are percent-of-WR; bar% = E0 * e^(off%/K). Same metric SP2 will reuse.
export const E0 = 0.2;   // floor: min lead (% of WR) at WR pace
export const K = 4;      // steepness: bar climbs by factor e every K% off the WR

/** The fire bar (min lead, % of WR) at a given % off the WR. */
export function fireBarPct(offPct) {
  return E0 * Math.exp(offPct / K);
}

/** Leader's required lead in ms to stay lit on this course (NaN if no WR). */
export function snuffLeadMs({ t1, wr }) {
  if (!wr || t1 == null) return NaN;
  const offPct = ((t1 - wr) / wr) * 100;
  return (fireBarPct(offPct) / 100) * wr;
}

/** True when the course's leader is "on fire". Needs a real #2 and a current WR. */
export function isOnFire({ t1, t2, wr }) {
  if (!wr || t1 == null || t2 == null || t2 < t1) return false;
  const leadPct = ((t2 - t1) / wr) * 100;
  const offPct = ((t1 - wr) / wr) * 100;
  return leadPct >= fireBarPct(offPct);
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix web test -- fireModel`
Expected: PASS (all cases).

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/fireModel.js web/src/lib/fireModel.test.js
git commit -m "feat(web): stateless exponential on-fire model (reused by SP2)"
```

---

## Task 3: Course view-model assembly (pure, TDD)

`buildCourseView` turns a raw leaderboard + WR + roster-colour map into the popup's view-model: leader, per-row colour bars, gap-to-#1, WR, on-fire flag, and the leader's GIF URLs.

**Files:**
- Create: `web/src/lib/courseData.js`, `web/src/lib/courseData.test.js`

- [ ] **Step 1: Write the failing test**

```js
// web/src/lib/courseData.test.js
import { describe, it, expect } from "vitest";
import { buildCourseView } from "./courseData.js";

const lb = [
  { player_id: 1, display_name: "Gub",   total_time_ms: 110579, total_time_str: "1:50.579", rank: 1 },
  { player_id: 2, display_name: "Paul",  total_time_ms: 114914, total_time_str: "1:54.914", rank: 2 },
];
const colorById = { 1: "#2dd4bf", 2: "#a78bfa" };

describe("buildCourseView", () => {
  it("assembles leader, rows, gap-to-#1 and gif urls", () => {
    const v = buildCourseView({ rows: lb, wr: { record_ms: 107414 }, colorById, courseName: "Mario Bros. Circuit" });
    expect(v.name).toBe("Mario Bros. Circuit");
    expect(v.wr_ms).toBe(107414);
    expect(v.leader).toEqual({ name: "Gub", color: "#2dd4bf" });
    expect(v.onFire).toBe(true);
    expect(v.gifUrl).toBe("/players/gub.gif");
    expect(v.fireGifUrl).toBe("/players/gub__fire.gif");
    expect(v.rows[0]).toMatchObject({ rank: 1, name: "Gub", color: "#2dd4bf", time_str: "1:50.579", gap_ms: null });
    expect(v.rows[1]).toMatchObject({ rank: 2, name: "Paul", color: "#a78bfa", gap_ms: 4335 });
  });

  it("is calm and gif-only when there is no WR or no #2", () => {
    const v = buildCourseView({ rows: [lb[0]], wr: null, colorById, courseName: "X" });
    expect(v.onFire).toBe(false);
    expect(v.wr_ms).toBe(null);
    expect(v.rows).toHaveLength(1);
  });

  it("falls back to a neutral colour when the roster has none", () => {
    const v = buildCourseView({ rows: lb, wr: null, colorById: {}, courseName: "X" });
    expect(v.leader.color).toBe("#888");
    expect(v.rows[0].color).toBe("#888");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix web test -- courseData`
Expected: FAIL (import unresolved).

- [ ] **Step 3: Write the implementation**

```js
// web/src/lib/courseData.js
// Assembles + fetches the hover-popup view-model for a course: leaderboard rows
// (with per-row colour + gap-to-#1), WR, on-fire flag, and the leader's GIF urls.
import { isOnFire } from "./fireModel.js";

const NEUTRAL = "#888";
const gifBase = (name) => `/players/${(name || "").toLowerCase()}`;

/** Pure: raw rows + wr + colour map -> popup view-model. */
export function buildCourseView({ rows, wr, colorById, courseName }) {
  const sorted = [...rows].sort((a, b) => a.total_time_ms - b.total_time_ms);
  const leadMs = sorted.length ? sorted[0].total_time_ms : null;
  const viewRows = sorted.map((r, i) => ({
    rank: i + 1,
    name: r.display_name,
    color: colorById[r.player_id] || NEUTRAL,
    time_ms: r.total_time_ms,
    time_str: r.total_time_str,
    gap_ms: i === 0 ? null : r.total_time_ms - leadMs,
  }));
  const leader = sorted[0];
  const wrMs = wr && wr.record_ms != null ? wr.record_ms : null;
  const onFire = isOnFire({ t1: leadMs, t2: sorted[1] ? sorted[1].total_time_ms : null, wr: wrMs });
  return {
    name: courseName,
    wr_ms: wrMs,
    leader: leader ? { name: leader.display_name, color: colorById[leader.player_id] || NEUTRAL } : null,
    onFire,
    gifUrl: leader ? `${gifBase(leader.display_name)}.gif` : null,
    fireGifUrl: leader ? `${gifBase(leader.display_name)}__fire.gif` : null,
    rows: viewRows,
  };
}

const j = async (fetchImpl, url) => { const r = await fetchImpl(url); return r.ok ? r.json() : null; };

/** Roster colour map {player_id: color}, fetched once and cached on the returned fn. */
export async function fetchColorById(apiBase, { fetchImpl = fetch } = {}) {
  if (fetchColorById._cache) return fetchColorById._cache;
  const roster = (await j(fetchImpl, `${apiBase}/v1/roster`)) || [];
  const map = {};
  for (const p of roster) if (p.color) map[p.player_id] = p.color;
  fetchColorById._cache = map;
  return map;
}

const viewCache = new Map();

/** Fetch + assemble a course's view-model (cached per slug for the session). */
export async function fetchCourseView(apiBase, course, { fetchImpl = fetch } = {}) {
  if (viewCache.has(course.slug)) return viewCache.get(course.slug);
  const q = `course=${encodeURIComponent(course.slug)}&cc=150`;
  const [rows, wr, colorById] = await Promise.all([
    j(fetchImpl, `${apiBase}/v1/leaderboard?${q}`),
    j(fetchImpl, `${apiBase}/v1/world-records?${q}`),
    fetchColorById(apiBase, { fetchImpl }),
  ]);
  const view = buildCourseView({ rows: Array.isArray(rows) ? rows : [], wr, colorById, courseName: course.name });
  viewCache.set(course.slug, view);
  return view;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix web test -- courseData`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/courseData.js web/src/lib/courseData.test.js
git commit -m "feat(web): course-popup view-model assembly + fetch"
```

---

## Task 4: Bundle the per-player GIFs into web/public

Normal strip = the player's **online** GIF (the posted one), re-encoded to **play once**. Fire strip = the player's **on-pace** source GIF (`paulSitDown`/`gubChoke`/`lukePoint`/`aliiasBird`; alex borrows gub), kept **looping**. Source of truth: `assets/player_figures.json`.

**Files:**
- Create: `scripts/bundle_web_player_gifs.py`
- Generate: `web/public/players/<player>.gif`, `web/public/players/<player>__fire.gif`

- [ ] **Step 1: Write the script**

```python
# scripts/bundle_web_player_gifs.py
"""Bundle per-player popup GIFs into web/public/players/ from assets/player_figures.json.
  <player>.gif       = the 'online' source gif, re-encoded to PLAY ONCE (no loop).
  <player>__fire.gif = the 'onpace' source gif, kept LOOPING (the on-fire strip).
Run with the system python (has Pillow):  python scripts/bundle_web_player_gifs.py
"""
import os, json
from PIL import Image, ImageSequence

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(ROOT, "assets", "player_gifs")
OUT = os.path.join(ROOT, "web", "public", "players")
MANIFEST = os.path.join(ROOT, "assets", "player_figures.json")

def reencode(src_gif, out_path, loop):
    """Copy a gif's frames to out_path. loop=None -> play once (no NETSCAPE loop); loop=0 -> forever."""
    im = Image.open(os.path.join(SRC, src_gif))
    frames = [f.convert("RGBA") for f in ImageSequence.Iterator(im)]
    durs = [f.info.get("duration", im.info.get("duration", 60)) for f in ImageSequence.Iterator(Image.open(os.path.join(SRC, src_gif)))]
    save_kw = dict(save_all=True, append_images=frames[1:], duration=durs, disposal=2, optimize=False)
    if loop is not None:
        save_kw["loop"] = loop
    frames[0].save(out_path, "GIF", **save_kw)

def main():
    os.makedirs(OUT, exist_ok=True)
    man = json.load(open(MANIFEST, encoding="utf-8"))
    for name, states in man.items():
        online = states["online"][0]
        onpace = states.get("onpace", states["online"])[0]
        reencode(online, os.path.join(OUT, f"{name}.gif"), loop=None)        # play once
        reencode(onpace, os.path.join(OUT, f"{name}__fire.gif"), loop=0)     # loop
        print("bundled", name, "<-", online, "/", onpace)

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it**

Run: `python scripts/bundle_web_player_gifs.py`
Expected: prints `bundled paul/aliias/luke/gub/alex ...`; creates 10 files in `web/public/players/`.

- [ ] **Step 3: Verify play-once vs loop in a browser**

Run (PowerShell): `Start-Process msedge "file:///C:/development/mkw-split-rewrite/web/public/players/gub.gif"` and `...gub__fire.gif`.
Expected: `gub.gif` runs through once and freezes on its last frame; `gub__fire.gif` loops. If `gub.gif` still loops (Pillow carried the loop block), re-run the bundle after installing gifsicle and replacing the play-once line with `os.system(f'gifsicle --no-loopcount "{src}" -o "{out_path}"')`, or `pip install pillow` ≥ 10 (which honours absent `loop`). Confirm before committing.

- [ ] **Step 4: Commit**

```bash
git add scripts/bundle_web_player_gifs.py web/public/players
git commit -m "build(web): bundle per-player posted (play-once) + on-pace (loop) popup GIFs"
```

---

## Task 5: CoursePopup.svelte (card component)

Port the validated card from `tools/popup-prototype.html` (the `#popup .card` markup + `.strip/.lb/.row` CSS), with two changes: the **strip is a ~56px card-style figure column** (3px spine + 56px figure, matching `Fire.svelte`'s geometry) and the fire state mounts real **`Fire.svelte`** with the looping on-pace GIF.

**Files:**
- Create: `web/src/CoursePopup.svelte`

- [ ] **Step 1: Write the component**

```svelte
<!-- web/src/CoursePopup.svelte -->
<script>
  import Fire from "../../src/components/Fire.svelte";
  export let view = null;            // the courseData view-model, or null while loading
  const fmt = (ms) => { if (ms == null) return "—"; const s = ms/1000, m = Math.floor(s/60); return `${m}:${(s-m*60 < 10 ? "0" : "")}${(s-m*60).toFixed(3)}`; };
  const gap = (ms) => (ms == null ? "-.---" : "+" + (ms/1000).toFixed(3));
</script>

{#if view}
<div class="card" class:firing={view.onFire}>
  <div class="strip">
    <div class="spine" style="background:{view.leader?.color || '#888'}"></div>
    <div class="figcol">
      <img class="fig" src={view.onFire ? view.fireGifUrl : view.gifUrl} alt="" draggable="false" />
      {#if view.onFire}<Fire color={view.leader?.color || '#888'} active={true} />{/if}
    </div>
  </div>
  <div class="lb">
    <div class="head"><span class="title">{view.name}</span>
      <span class="wr"><i>WR</i>{fmt(view.wr_ms)}</span></div>
    <div class="rule"></div>
    <div class="rows">
      <div class="hrow"><span class="bar"></span><span class="rk">#</span><span class="nm">Player</span><span class="tm">Time</span><span class="gp">Gap</span></div>
      {#each view.rows as r (r.rank)}
        <div class="row" class:lead={r.rank === 1}>
          <span class="bar" style="background:{r.color}"></span>
          <span class="rk">{r.rank}</span><span class="nm">{r.name}</span>
          <span class="tm">{r.time_str || fmt(r.time_ms)}</span>
          <span class="gp" class:none={r.rank === 1}>{gap(r.gap_ms)}</span>
        </div>
      {/each}
    </div>
  </div>
</div>
{/if}

<style>
  .card{ display:flex; width:344px; background:#121419; border:1px solid #2a2d33; border-radius:6px;
         box-shadow:0 18px 40px rgba(0,0,0,.6),0 0 0 1px rgba(61,124,194,.10); overflow:hidden; }
  /* strip = card-style figure column: 3px spine + 56px figure (Fire.svelte expects left:5,width:56) */
  .strip{ position:relative; width:64px; flex:0 0 64px; background:#0e1014; border-right:1px solid #23262b; overflow:hidden; }
  .spine{ position:absolute; left:0; top:0; bottom:0; width:3px; z-index:4; }
  .figcol{ position:absolute; left:5px; top:0; width:56px; height:100%; }
  .fig{ position:absolute; left:50%; bottom:0; transform:translateX(-50%); height:100%; width:auto; max-width:none; z-index:2; }
  .lb{ flex:1 1 auto; padding:10px 12px 7px; min-width:0; }
  .head{ display:flex; align-items:baseline; justify-content:space-between; gap:8px; }
  .title{ color:#e8eaed; font-size:14px; font-weight:600; }
  .wr{ font-size:10.5px; color:#9aa3ad; font-variant-numeric:tabular-nums; white-space:nowrap; }
  .wr i{ font-style:normal; color:#5f656e; letter-spacing:.08em; font-size:9px; margin-right:3px; }
  .rule{ height:1px; margin:8px 0 3px; background:linear-gradient(90deg,transparent,#2c313a 8%,#2c313a 92%,transparent); }
  .rows{ margin-top:2px; }
  .row{ display:flex; align-items:center; gap:11px; padding:3px 7px 3px 0; border-top:1px solid #1c1f24; }
  .hrow{ display:flex; align-items:center; gap:11px; padding:1px 7px 4px 0; }
  .bar{ flex:0 0 3px; width:3px; height:14px; border-radius:2px; }
  .rk{ flex:0 0 13px; text-align:right; font-size:12px; color:#6f7782; font-variant-numeric:tabular-nums; }
  .nm{ flex:1 1 auto; min-width:0; font-size:12.5px; color:#d4d8dd; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .tm{ flex:0 0 auto; font-family:ui-monospace,Menlo,monospace; font-size:12.5px; color:#d4d8dd; font-variant-numeric:tabular-nums; }
  .gp{ flex:0 0 58px; text-align:right; font-family:ui-monospace,Menlo,monospace; font-size:12.5px; color:#828791; font-variant-numeric:tabular-nums; }
  .gp.none{ color:#54595f; }
  .hrow .rk,.hrow .nm,.hrow .tm,.hrow .gp{ font-size:9.5px; letter-spacing:.08em; text-transform:uppercase; color:#5f656e; font-weight:500; }
  .row.lead{ background:rgba(255,255,255,.04); } .row.lead .nm,.row.lead .tm{ color:#fff; }
</style>
```

- [ ] **Step 2: Verify it compiles**

Run: `npm --prefix web run build`
Expected: builds clean (Svelte compiles `CoursePopup` + the `Fire` import resolves via `fs.allow:['..']`).

- [ ] **Step 3: Commit**

```bash
git add web/src/CoursePopup.svelte
git commit -m "feat(web): CoursePopup card (strip + leaderboard + Fire)"
```

(Visual verification happens in Task 6, mounted on the real map.)

---

## Task 6: Wire hover into WorldMap.svelte

Add glance-tooltip behaviour: open on a course-icon `mouseenter` (lazy-fetch its view-model, cache), close on `mouseleave` (NOT kept open over the popup), spring from the icon, flip near edges. Drive visibility purely via a class — **never inline `opacity`** (inline overrides the `.show` rule; this bit the prototype).

**Files:**
- Modify: `web/src/WorldMap.svelte` (the `<script>`, the `.popups` div, and `<style>`)

- [ ] **Step 1: Extend the script**

In `web/src/WorldMap.svelte`, add to the existing `<script>`:

```js
  import CoursePopup from "./CoursePopup.svelte";
  import { fetchCourseView } from "./lib/courseData.js";
  import { API_BASE } from "./lib/api.js";

  let view = null;            // current popup view-model
  let shown = false;          // drives the .show class (fade/scale)
  let popupEl, frameEl;       // bound DOM
  let style = "";             // left/top/transform-origin for the popup
  let closeTimer = 0;

  async function openCourse(course, hitEl) {
    clearTimeout(closeTimer);
    view = await fetchCourseView(API_BASE, course).catch(() => null);
    if (!view) return;
    await tick();                                   // CoursePopup renders -> measurable
    place(hitEl);
    requestAnimationFrame(() => (shown = true));    // class drives opacity/scale
  }
  function scheduleClose() { clearTimeout(closeTimer); closeTimer = setTimeout(() => (shown = false), 90); }

  function place(hitEl) {
    const fr = frameEl.getBoundingClientRect(), hr = hitEl.getBoundingClientRect();
    const cx = hr.left - fr.left + hr.width / 2, cy = hr.top - fr.top + hr.height / 2;
    const pw = popupEl.offsetWidth, ph = popupEl.offsetHeight, off = Math.max(hr.width, hr.height) * 0.55 + 8;
    const right = cx < fr.width * 0.5, below = cy < fr.height * 0.42;
    let left = right ? cx + off : cx - off - pw, top = below ? cy + off : cy - off - ph;
    left = Math.max(6, Math.min(left, fr.width - pw - 6));
    top = Math.max(6, Math.min(top, fr.height - ph - 6));
    style = `left:${left}px;top:${top}px;transform-origin:${right ? "left" : "right"} ${below ? "top" : "bottom"}`;
  }
```

Add `tick` to the existing svelte import: `import { onMount, tick } from "svelte";`

- [ ] **Step 2: Bind the frame + wire the icon hits**

On the `.frame` element add `bind:this={frameEl}`. On each `.hit` element (the `{#each manifest.courses as c}` loop) add:

```svelte
        on:mouseenter={(e) => openCourse(c, e.currentTarget)}
        on:mouseleave={scheduleClose}
```

- [ ] **Step 3: Render the popup in the `.popups` layer**

Replace the existing `<!-- SP3 (hover popup) mounts here --> <div class="popups" ...></div>` with:

```svelte
        <div class="popups">
          <div class="popup" class:show={shown} bind:this={popupEl} style={style} aria-hidden={!shown}>
            <CoursePopup {view} />
          </div>
        </div>
```

- [ ] **Step 4: Add popup styles**

Add to `WorldMap.svelte` `<style>`:

```css
  .popups { position:absolute; inset:0; pointer-events:none; }
  .popup { position:absolute; display:none; opacity:0; transform:scale(.92); z-index:80; pointer-events:none;
           transition:opacity .14s ease, transform .14s cubic-bezier(.2,.9,.3,1.2); }
  .popup.show { display:block; opacity:1; transform:scale(1); }
```

(The `.popups` rule may already exist from SP1 — replace it with the above so the inner `.popup` can size to content. Note `display:none` until `.show`; we never set inline opacity.)

- [ ] **Step 5: Visual verification — fire, calm, and an edge course**

Run (PowerShell, dev server in the background):

```powershell
Start-Job { npm --prefix web run dev } | Out-Null
Start-Sleep 4
$edge = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
& $edge --headless=new --disable-gpu --virtual-time-budget=4000 --window-size=1200,1000 --screenshot="tools/_sp3_live.png" "http://localhost:1430/#/map"
```

Then open `http://localhost:1430/#/map` in a real browser and hover: a fire course (Mario Bros. Circuit — Gub's `gubChoke` GIF looping under flames), a calm course (posted GIF plays once), and an edge course (popup flips inward). Confirm the strip is the thin card-style column, colour bars read cleanly, WR sits in the corner, and the popup closes when you leave the icon (does not stay open over the card).

- [ ] **Step 6: Commit**

```bash
git add web/src/WorldMap.svelte
git commit -m "feat(web): hover-open course popup on the World Map (glance tooltip + spring + edge-flip)"
```

---

## Task 7: Anchor polish + touch + full green

- [ ] **Step 1: Refine anchoring for centre courses**

If a centre course clamps hard to the frame edge (the prototype's rough spot), bias `place()` so the popup projects from the icon with the `off` gap preserved before clamping: prefer the side with more room — replace the `right`/`below` decisions with `const right = cx < fr.width - pw - off - 6 ? cx < fr.width*0.5 : false;` style room checks, falling back to whichever side fits. Re-run the Step 5 visual check until centre courses (e.g. Mario Circuit, Moo Moo Meadows) open cleanly beside the icon.

- [ ] **Step 2: Touch support**

Add to `WorldMap.svelte` `<script>`: a document `pointerdown` handler (registered in `onMount`, cleaned up on destroy) that closes the popup when a tap lands outside the active hit; and on `.hit` add `on:click={(e) => openCourse(c, e.currentTarget)}` so a tap opens it. Verify on a narrow window (DevTools device mode) that tap-open / tap-away-close works.

- [ ] **Step 3: Run the full web suite + build**

Run: `npm --prefix web test`
Expected: PASS (existing 13 + new fireModel/courseData specs).
Run: `npm --prefix web run build`
Expected: clean build.

- [ ] **Step 4: Clean up scratch + commit**

```bash
rm -f tools/_sp3_live.png
git add -A
git commit -m "feat(web): SP3 popup anchor polish + touch; suite green"
```

---

## Self-review notes (already folded in)

- **Spec coverage:** card (T5) · interaction/spring/edge-flip/touch (T6/T7) · fire model + reuse (T2) · data from existing reads (T3) · GIF bundling incl. play-once + on-pace loop (T4) · colours via roster (T3) · vitest + headless-Edge verification (T2/T3/T6). 
- **Out of scope:** SP2 territory colour, SP4 timeline. `fireModel.js` is written standalone for SP2 to import.
- **Naming consistency:** `buildCourseView` / `fetchCourseView` / `isOnFire` / `fireBarPct` / `snuffLeadMs` used identically across tasks.
