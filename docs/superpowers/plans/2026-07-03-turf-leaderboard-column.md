# Turf Leaderboard Column — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the stylised Persona/TWEWY turf-ownership leaderboard column on the `web/` Turf page, re-ranking live and ticking in sync with the map's territory playback.

**Architecture:** A pure helper (`web/src/lib/turf.js`) computes standings + deterministic "jank" from the ownership data `WorldMap.svelte` already holds. A new `TurfLeaderboard.svelte` renders one card per roster player in a FIXED DOM order and animates them **imperatively** (positions/ticks/side-swaps via `transform` + Web Animations API) driven by two reactive props: the shown frame index and a live animation-progress object. `WorldMap.svelte` restructures its layout (full-width scrubber on top; column + map side by side) and exposes the play animation's `tau` so the numbers tick in lockstep with the territory front.

**Tech Stack:** Svelte 4, Vite 5, Vitest 4 (colocated `*.test.js`), Web Animations API, CSS `clip-path`.

## Global Constraints

- **The two committed references are the exact source of truth** — read them before/while building:
  - `docs/design/turf-leaderboard/turf-card-design.html` (static look).
  - `docs/design/turf-leaderboard/turf-animation-prototype.html` (the animation model + the exact adapted CSS + the vanilla engine this component ports).
- **Deterministic jank only** — shapes, rotations, offsets, digit transforms are pure functions of (player key / roster index / digit index). **Never `Math.random()`** (it would shimmer every frame).
- **Uniform cards** — 160×110px, gap 10px, `STEP=120`, right-aligned in a 172px column. Rank = order + the number, never size.
- **% = `Math.round(coursesOwned / totalCourses * 100)`**, `totalCourses` = `manifest.courses.length` (30).
- **Roster = `Object.keys(colors)`**, sorted, stable order in the DOM. Show **all** roster players; 0-course players render **muted** (`filter:saturate(.32) brightness(.72)`) at the bottom.
- **Figures** via `figureFor(name, true)` (import from `../../src/lib/playerFigures.js`); display the full name verbatim (so `paul pork` stays lowercase); figure key via `playerKey(name)`.
- **Animate only during play.** Scrub / live = hard snap (no ticking, no slide).
- Web commands: tests `npm --prefix web test`; types `npm --prefix web run check`; build `npm --prefix web run build`.

---

## File structure

- `web/src/lib/turf.js` (new) — pure: `courseCounts`, `turfStandings`, `cardConfig`, `digitJank`.
- `web/src/lib/turf.test.js` (new) — unit tests for the above.
- `web/src/TurfLeaderboard.svelte` (new) — the column: template + committed CSS + imperative animation engine.
- `web/src/WorldMap.svelte` (modify) — layout restructure; compute + pass props; expose the play `tau`.

---

## Task 1: `turf.js` — pure standings + deterministic jank

**Files:**
- Create: `web/src/lib/turf.js`
- Test: `web/src/lib/turf.test.js`

**Interfaces:**
- Consumes: `playerKey` from `../../../src/lib/playerKey.js`.
- Produces:
  - `courseCounts(snapshot) -> { [player]: number }`
  - `turfStandings(snapshot, colors, totalCourses=30) -> [{ player, color, courses, pct, rank }]` (sorted courses desc, tie-break player name asc)
  - `cardConfig(key, i) -> { shape:1..5, rot:number, ox:number, oy:number, fx:number }`
  - `digitJank(i) -> { rot:number, ty:number }`

- [ ] **Step 1: Write the failing tests**

Create `web/src/lib/turf.test.js`:

```js
import { describe, it, expect } from "vitest";
import { courseCounts, turfStandings, cardConfig, digitJank } from "./turf.js";

const C = { Gub: "#38bdf8", Aliias: "#4ade80", Alex: "#fbbf24" };
const snap = (owners) => ({ owners });

describe("courseCounts", () => {
  it("tallies owned courses per player, ignoring unowned", () => {
    const s = snap({ mc: { player: "Gub" }, dk: { player: "Gub" }, bc: { player: "Aliias" } });
    expect(courseCounts(s)).toEqual({ Gub: 2, Aliias: 1 });
  });
  it("is empty for a null/blank snapshot", () => {
    expect(courseCounts(null)).toEqual({});
    expect(courseCounts({ owners: {} })).toEqual({});
  });
});

describe("turfStandings", () => {
  it("includes every roster player (0 courses too), sorted desc, rank + pct", () => {
    const s = snap({ mc: { player: "Gub" }, dk: { player: "Gub" }, bc: { player: "Aliias" } });
    const st = turfStandings(s, C, 30);
    expect(st.map((r) => r.player)).toEqual(["Gub", "Aliias", "Alex"]); // Alex 0 at the bottom
    expect(st.map((r) => r.courses)).toEqual([2, 1, 0]);
    expect(st.map((r) => r.rank)).toEqual([1, 2, 3]);
    expect(st[0].pct).toBe(Math.round((2 / 30) * 100)); // 7
    expect(st[2].pct).toBe(0);
    expect(st[0].color).toBe("#38bdf8");
  });
  it("breaks ties by player name ascending (stable)", () => {
    const s = snap({ mc: { player: "Gub" }, bc: { player: "Aliias" } }); // 1 each
    expect(turfStandings(s, C, 30).map((r) => r.player)).toEqual(["Aliias", "Gub", "Alex"]);
  });
});

describe("deterministic jank", () => {
  it("cardConfig is stable and cycles shapes 1..5", () => {
    expect(cardConfig("gub", 0)).toEqual(cardConfig("gub", 0));
    expect(cardConfig("x", 0).shape).toBe(1);
    expect(cardConfig("x", 5).shape).toBe(1);
    expect(cardConfig("x", 4).shape).toBe(5);
  });
  it("cardConfig gives aliias the edge push, others none", () => {
    expect(cardConfig("aliias", 1).fx).toBe(12);
    expect(cardConfig("gub", 0).fx).toBe(0);
  });
  it("digitJank is stable per index", () => {
    expect(digitJank(0)).toEqual(digitJank(0));
    expect(digitJank(0)).not.toEqual(digitJank(1));
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npm --prefix web test -- turf`
Expected: FAIL (`turf.js` does not exist / exports undefined).

- [ ] **Step 3: Write `web/src/lib/turf.js`**

```js
// Pure turf-leaderboard helpers: standings from an ownership snapshot + the
// deterministic "jank" (shapes/rotations/offsets/digit transforms) the column
// draws with. No DOM, no randomness (stable across animation frames).
import { playerKey } from "../../../src/lib/playerKey.js";

/** Courses owned per player in a snapshot ({slug:{player}} -> {player:count}). */
export function courseCounts(snapshot) {
  const out = {};
  const owners = snapshot?.owners || {};
  for (const slug in owners) {
    const p = owners[slug]?.player;
    if (p) out[p] = (out[p] || 0) + 1;
  }
  return out;
}

/** Standings for a snapshot: every roster player (keys of `colors`), sorted by
 *  courses desc, tie-break player name asc. pct = round(courses/total*100). */
export function turfStandings(snapshot, colors, totalCourses = 30) {
  const counts = courseCounts(snapshot);
  return Object.keys(colors || {})
    .map((player) => ({
      player,
      color: colors[player],
      courses: counts[player] || 0,
      pct: Math.round(((counts[player] || 0) / totalCourses) * 100),
    }))
    .sort((a, b) => b.courses - a.courses || (a.player < b.player ? -1 : a.player > b.player ? 1 : 0))
    .map((r, i) => ({ ...r, rank: i + 1 }));
}

// --- deterministic jank (stable per player / index; NEVER Math.random) ---
const ROT = [-1.6, 1.4, -1.9, 1.5, -1.2];   // card tilt by roster index
const OY = [5, 4, 4, 3, 3];                  // colour-border vertical offset
const FX = { aliias: 12 };                   // per-figure edge push (px) — wide angle-of-photo figure

/** Fixed card styling for a player at stable roster index i. */
export function cardConfig(key, i) {
  return { shape: (i % 5) + 1, rot: ROT[i % 5], ox: 5, oy: OY[i % 5], fx: FX[key] || 0 };
}

const DIG_ROT = [-4, 5, -3, 4, -5];
const DIG_TY = [0, -5, 3, -4, 2];
/** Per-digit ransom transform, stable by digit position. */
export function digitJank(i) {
  return { rot: DIG_ROT[i % 5], ty: DIG_TY[i % 5] };
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `npm --prefix web test -- turf`
Expected: PASS (all `turf` tests green).

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/turf.js web/src/lib/turf.test.js
git commit -m "feat(web): turf standings + deterministic jank helpers"
```

---

## Task 2: `TurfLeaderboard.svelte` — the column + imperative animation engine

**Files:**
- Create: `web/src/TurfLeaderboard.svelte`
- Reference (read, copy CSS verbatim): `docs/design/turf-leaderboard/turf-animation-prototype.html`

**Interfaces:**
- Consumes: `turfStandings`, `cardConfig`, `digitJank` from `./lib/turf.js`; `figureFor` from `../../src/lib/playerFigures.js`; `playerKey` from `../../src/lib/playerKey.js`.
- Produces (props consumed by Task 3):
  - `snapshots: Array` — the ownership snapshots (`WorldMap`'s `snapshots`).
  - `colors: {name:hex}` — `WorldMap`'s `tlColors`.
  - `courseCount: number` — `manifest.courses.length`.
  - `frameIndex: number` — `WorldMap`'s `tlIndex` (the shown frame; used when not animating).
  - `anim: { active:boolean, from:number, to:number, tau:number }` — live play progress; when `active`, counts interpolate `snapshots[from]→snapshots[to]` by `tau`.

- [ ] **Step 1: Create the component with template + committed CSS + engine**

Create `web/src/TurfLeaderboard.svelte`. The `<style>` block is the **card CSS copied verbatim** from `docs/design/turf-leaderboard/turf-animation-prototype.html` — copy every rule from `.turfcol` down through `.m5` (i.e. `.turfcol, .stack, .rp, .rp.zero, .inner, .ck, .cf, .cf.dot::after, .figmask, .figmask img, .rp.L .figmask img, .rp.R .figmask img, .num, .num.L, .num.R, .num .d, .num .pc, .name, .name.L, .name.R, .header (add: see below), .streak, .streak b, .p1..p5, .m1..m5`). Then apply these adaptations (the prototype uses a `.header` wrapper that the static design didn't):

- Ensure a `.header` rule exists (the prototype has it): `.header{position:absolute;top:9px;z-index:6;width:auto;max-width:130px} .rp.L .header{left:11px;text-align:left} .rp.R .header{right:11px;text-align:right}` and `.num{display:block;...}` and `.name{display:inline-block;margin-top:4px;...}` — copy these from the prototype exactly.
- Add nothing else. Colours come from `--c` (set per card inline).

The `<script>` + template (the ported, prop-driven engine):

```svelte
<script>
  import { onMount } from "svelte";
  import { figureFor } from "../../src/lib/playerFigures.js";
  import { playerKey } from "../../src/lib/playerKey.js";
  import { turfStandings, cardConfig, digitJank } from "./lib/turf.js";

  export let snapshots = [];
  export let colors = {};
  export let courseCount = 30;
  export let frameIndex = 0;
  export let anim = { active: false, from: 0, to: 0, tau: 0 };

  const STEP = 120, SWAP = 420;

  $: roster = Object.keys(colors).sort();                 // stable DOM order
  $: cfg = roster.map((n, i) => cardConfig(playerKey(n), i));

  let cardEls = [], mounted = false;
  const elOf = (name) => cardEls[roster.indexOf(name)];
  let curSlot = {}, curSide = {}, curPct = {};

  function setNum(name, pct) {
    const card = elOf(name); if (!card) return;
    const num = card.querySelector(".num");
    const s = String(pct);
    let h = "";
    for (let i = 0; i < s.length; i++) {
      const j = digitJank(i);
      h += `<span class="d" style="transform:rotate(${j.rot}deg) translateY(${j.ty}px)">${s[i]}</span>`;
    }
    h += `<span class="pc" style="transform:rotate(2deg)">%</span>`;
    num.innerHTML = h;
    num.animate([{ transform: "scale(1.16)" }, { transform: "scale(1)" }],
      { duration: 230, easing: "cubic-bezier(.3,1.6,.4,1)" });
  }

  function slide(node, dx) {
    if (Math.abs(dx) < 1) return;
    node.animate([{ transform: `translateX(${dx}px)` }, { transform: "translateX(0)" }],
      { duration: SWAP, easing: "cubic-bezier(.3,1.55,.35,1)" });
  }

  // colour border sits on the figure's side; mirrors (and slides) on a side-swap
  function setBorder(card, c, side, animate) {
    const ck = card.querySelector(".ck"), ax = Math.abs(c.ox);
    const nx = side === "L" ? ax : -ax, nt = `translate(${nx}px,${c.oy}px)`;
    if (animate) {
      const ot = ck.style.transform || nt;
      ck.animate([{ transform: ot }, { transform: nt }],
        { duration: SWAP, easing: "cubic-bezier(.3,1.55,.35,1)" });
    }
    ck.style.transform = nt;
  }

  // kinetic side-swap: header + figure slam across (FLIP) + a colour streak. No fade.
  function doSwap(card, side) {
    const hdr = card.querySelector(".header"), img = card.querySelector(".figmask img");
    const h0 = hdr.getBoundingClientRect(), i0 = img.getBoundingClientRect();
    card.classList.remove("L", "R"); card.classList.add(side);
    const h1 = hdr.getBoundingClientRect(), i1 = img.getBoundingClientRect();
    slide(hdr, h0.left - h1.left); slide(img, i0.left - i1.left);
    card.querySelector(".streak b").animate(
      [{ transform: "translateX(-160%) skewX(-14deg)", opacity: .9 },
       { transform: "translateX(360%) skewX(-14deg)", opacity: 0 }],
      { duration: SWAP, easing: "ease-out" });
  }

  function place(counts, animated) {
    const order = roster.slice().sort((a, b) =>
      counts[b] - counts[a] || (a < b ? -1 : a > b ? 1 : 0));
    order.forEach((name, slot) => {
      const card = elOf(name); if (!card) return;
      const c = cfg[roster.indexOf(name)], side = slot % 2 === 0 ? "L" : "R";
      if (curSlot[name] !== slot) {
        const first = curSlot[name] === undefined;
        if (!animated) card.style.transition = "none";
        card.style.transform = `translateY(${slot * STEP}px) rotate(${c.rot}deg)`;
        if (!animated) { void card.offsetWidth; card.style.transition = ""; } // flush, re-enable
        curSlot[name] = slot;
        // z by slot (lower player on top); on a live reorder flip z at the slide midpoint (unseen)
        if (animated && !first) setTimeout(() => { if (curSlot[name] === slot) card.style.zIndex = 10 + slot; }, 220);
        else card.style.zIndex = 10 + slot;
      }
      if (curSide[name] !== side) {
        const swap = animated && curSide[name] !== undefined;
        if (swap) doSwap(card, side); else { card.classList.remove("L", "R"); card.classList.add(side); }
        setBorder(card, c, side, swap);
        curSide[name] = side;
      }
      card.classList.toggle("zero", Math.round(counts[name]) <= 0);
    });
  }

  function countsAt(i) {
    const out = {};
    const idx = Math.max(0, Math.min(i, snapshots.length - 1));
    turfStandings(snapshots[idx], colors, courseCount).forEach((r) => (out[r.player] = r.courses));
    return out;
  }

  function drive(fi, a) {
    if (!mounted || !snapshots.length) return;
    let counts;
    if (a && a.active) {
      const f = countsAt(a.from), t = countsAt(a.to), tau = a.tau;
      counts = {};
      roster.forEach((n) => (counts[n] = (f[n] || 0) + ((t[n] || 0) - (f[n] || 0)) * tau));
    } else {
      counts = countsAt(fi);
    }
    roster.forEach((n) => {
      const pct = Math.round((counts[n] / courseCount) * 100);
      if (pct !== curPct[n]) { setNum(n, pct); curPct[n] = pct; }
    });
    place(counts, !!(a && a.active));
  }

  onMount(() => { mounted = true; drive(frameIndex, anim); });
  // re-run on any input change (frame scrub, play tau, or a late data arrival)
  $: (mounted, snapshots, colors, frameIndex, anim, drive(frameIndex, anim));
</script>

<div class="turfcol">
  <div class="stack" style="height:{roster.length * STEP}px">
    {#each roster as name, i (name)}
      <div class="rp" data-key={name} bind:this={cardEls[i]}
           style="--c:{colors[name]};--fx:{cfg[i].fx}px">
        <div class="inner">
          <div class="ck p{cfg[i].shape}"></div>
          <div class="cf dot p{cfg[i].shape}"></div>
          <div class="figmask m{cfg[i].shape}"><img src={figureFor(name, true)} alt="" /></div>
          <div class="header"><span class="num"></span><span class="name">{name}</span></div>
          <div class="streak p{cfg[i].shape}"><b></b></div>
        </div>
      </div>
    {/each}
  </div>
</div>

<style>
  /* PASTE the card CSS verbatim from docs/design/turf-leaderboard/turf-animation-prototype.html
     (all rules from `.turfcol` through `.m5`, incl. `.header/.num/.name/.streak` and `.p1..p5`).
     Add at the top so the column sizes itself inside WorldMap's flex row: */
  .turfcol { flex: 0 0 172px; position: relative; }
  /* ...rest pasted verbatim... */
</style>
```

- [ ] **Step 2: Type-check**

Run: `npm --prefix web run check`
Expected: `svelte-check` reports 0 errors, 0 warnings for `TurfLeaderboard.svelte` (fix any import-path typos).

- [ ] **Step 3: Commit**

```bash
git add web/src/TurfLeaderboard.svelte
git commit -m "feat(web): TurfLeaderboard column component + animation engine"
```

---

## Task 3: Wire `TurfLeaderboard` into `WorldMap.svelte` (layout + play `tau`)

**Files:**
- Modify: `web/src/WorldMap.svelte`

**Interfaces:**
- Consumes: everything Task 2 produces (`snapshots`, `colors`, `courseCount`, `frameIndex`, `anim`).
- Produces: the finished Turf page.

- [ ] **Step 1: Import + animation state**

In the `<script>` of `web/src/WorldMap.svelte`:
- Add import (with the other component imports): `import TurfLeaderboard from "./TurfLeaderboard.svelte";`
- Add state near the other `let` declarations (e.g. by `let playing = false;`): `let turfAnim = { active: false, from: 0, to: 0, tau: 0 };`

- [ ] **Step 2: Expose the play `tau` from `animateTransition`**

In `animateTransition(from, to)`: **immediately before** `const t0 = performance.now();` add `turfAnim = { active: true, from, to, tau: 0 };`. Inside the `tick` closure, **after** `const tau = easeFlow(...)`, add `turfAnim = { active: true, from, to, tau };`. In its `else` branch (the `done()` call at `tau >= 1`), set the column to settle: change `else done();` to `else { turfAnim = { active: false, from, to, tau: 1 }; done(); }`.

In `cancelAnim()` add as the first line: `turfAnim = { ...turfAnim, active: false };` (so pause + scrub snap the column to the shown frame).

- [ ] **Step 3: Layout restructure — full-width console; column + map row**

Replace the markup body of `.map-view` (the `.console` block + the `.frame` block) so the console stays full-width on top and a new `.appbody` flex row holds the column + the existing `.frame`:

```svelte
<div class="map-view" bind:this={mapViewEl} style="height:calc(100dvh - {headerH}px)">
  {#if timelineReady && snapshots.length}
    <div class="console" bind:this={consoleEl} style="width:100%">
      <TimelineScrubber {snapshots} index={tlIndex} {playing}
        on:scrub={(e) => onScrub(e.detail.index)} on:toggle={togglePlay} />
    </div>
  {/if}

  <div class="appbody">
    {#if timelineReady && snapshots.length}
      <TurfLeaderboard {snapshots} colors={tlColors}
        courseCount={manifest?.courses?.length ?? 30}
        frameIndex={tlIndex} anim={turfAnim} />
    {/if}

    <div class="frame" style={mapW ? `width:${mapW}px;height:${mapH}px` : ""}>
      <!-- ...existing .frame contents unchanged (error / stage / msg)... -->
    </div>
  </div>
</div>
```

Keep everything **inside** `.frame` exactly as it is. Only the wrapper structure changes.

- [ ] **Step 4: CSS — the row + reserve column width in `fitMap`**

In `<style>`: keep `.map-view` as the flex column it already is. Add:

```css
.appbody { display: flex; flex: 1; min-height: 0; gap: 14px; width: 100%; justify-content: center; }
@media (max-width: 760px) { .appbody { flex-direction: column; align-items: center; } }
```

In `fitMap()`, reserve the column's width when it is showing. Change the `availW` line to:

```js
const colW = (timelineReady && snapshots.length && mapViewEl.clientWidth > 760) ? 172 + 14 : 0;
const availW = mapViewEl.clientWidth - padH - colW;
```

(`padH` and the rest of `fitMap` stay as they are.)

- [ ] **Step 5: Type-check**

Run: `npm --prefix web run check`
Expected: 0 errors / 0 warnings.

- [ ] **Step 6: Build**

Run: `npm --prefix web run build`
Expected: build succeeds (no import/compile errors).

- [ ] **Step 7: Visual verification**

Run the web app (`npm --prefix web run dev`) against a season server (or the existing local data path) and open `#/turf`. Confirm against the references:
- Static look matches `turf-card-design.html` (thin column left, map right, uniform janky cards, % top / name beneath / alternating L↔R, masked figures with the head popping, 2-sided colour border, muted 0% card at the bottom).
- Press play: numbers tick up/down in sync with the sweep, cards slide on reorder, side-swaps slam across with the streak + mirrored border, z head-overlap reads right and its flip is unseen — matches `turf-animation-prototype.html`.
- Scrub: standings snap (no ticking). Fits the viewport with no page scroll.

- [ ] **Step 8: Commit**

```bash
git add web/src/WorldMap.svelte
git commit -m "feat(web): mount turf leaderboard beside the map, sync ticks to territory playback"
```

---

## Self-review

**Spec coverage** (each spec §):
- §2 Layout — Task 3 (full-width console, `.appbody` row, `fitMap` reserves column width). ✓
- §3 Card anatomy (uniform, shapes, 2-sided border, masked figure/head-pop/feet-hidden, % top inset + per-digit jank, name beneath no-rank natural-case, muted 0%, z-by-slot, `--fx`) — CSS from the committed reference (Task 2) + `cardConfig`/`digitJank` (Task 1). ✓
- §4 Data (`turfStandings`, all roster, 0% bottom, pct round, tie-break) — Task 1, tested. ✓
- §5 Animation (tick synced to `tau`, reorder slide, z-midpoint, side-swap slide+streak, border mirror, no fade, 0%→mute) — Task 2 engine + Task 3 `tau` exposure. ✓
- §6 Assets (Luke crop baked; plain `__on` via `figureFor`; Aliias `--fx` not squished) — `figureFor` + `cardConfig` FX. Luke crop already done. ✓
- §7 Components/files — Tasks 1–3 match. ✓
- §8 Testing — Task 1 unit tests; Tasks 2–3 svelte-check + build + visual vs references. ✓

**Placeholder scan:** the only "paste verbatim" is the CSS, and it points at a committed exact file (not a vague TODO). All JS is complete.

**Type/name consistency:** `turfStandings`/`courseCounts`/`cardConfig`/`digitJank` names match across Task 1 (defined) and Task 2 (consumed); prop names (`snapshots/colors/courseCount/frameIndex/anim`) match across Task 2 (declared) and Task 3 (passed); `turfAnim` shape `{active,from,to,tau}` matches the `anim` prop contract. ✓

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-03-turf-leaderboard-column.md`. Executing inline (user pre-approved implementation), committing per task.
