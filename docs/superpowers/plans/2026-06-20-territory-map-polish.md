# Territory Map Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the World Map territory read correctly per-cell, animate ownership changes as sliding borders (no flash), render crisply on any display, and fit/compose the page without scrolling.

**Architecture:** Pure render/partition logic lives in `web/src/lib/*.js` (unit-tested, DOM-free); `WorldMap.svelte` owns the canvas, the per-frame animation loop, and the layout. The territory partition seeds all 30 courses and paints only claimed cells. Playback drops the dual-canvas opacity crossfade for a single canvas whose changed cells are re-rendered per frame so the gooey border physically slides; everything else stays static (no flash).

**Tech Stack:** Svelte 4, Vite 5, Vitest 4, Web Workers + OffscreenCanvas, Canvas2D.

## Global Constraints

- Scope is `web/` only — **no** server, endpoint, or data-model changes.
- Anti-aliasing rule (non-negotiable): render hi-res → downscale; **never** upscale a smaller canvas.
- Locked lens constants stay as `LENS` in `territory.js` (no UI sliders).
- Length params (rim/halo/gooey radius) are fractions of the **full backing width W**, regardless of the array size a function operates on.
- Verify visuals with **headless Edge / CDP screenshots, never OpenCV** (wait on a DOM predicate before capture). Vite dev serves the SPA on **:1430**; pi dev on **:8787**.
- Test/check/build: `npm --prefix web test` · `npm --prefix web run check` · `npm --prefix web run build`.
- Commit messages end with the repo's required `Co-Authored-By:` / `Claude-Session:` trailers.

---

## File Structure

- `web/src/lib/territory.js` (modify) — partition + paint. All-courses seeding + `paintable` mask; `paintLens` skips non-paintable owners (back-compatible default).
- `web/src/lib/timeline.js` (modify) — add pure `flippedCourses(snapA, snapB)`.
- `web/src/lib/territoryAnim.js` (create) — pure transition math: unified owners across two snapshots, nearest-course field, change bbox, per-frame interpolated patch.
- `web/src/lib/territoryAnim.test.js` (create) — unit tests for the above.
- `web/src/lib/territory.test.js` (modify) — rewrite `prepareOwners` tests; add claim-only-your-cell tests.
- `web/src/lib/timeline.test.js` (modify) — add `flippedCourses` tests.
- `web/src/lib/territoryWorker.js` (modify) — render the base frame at a caller-supplied target size.
- `web/src/WorldMap.svelte` (modify) — single DPR-aware canvas; backing-res source buffers; the per-frame animation loop; controls-on-top fit-to-viewport layout.
- `web/src/TimelineScrubber.svelte` (modify) — relocated above the map, restyled into the composition.

---

## Task 1: Partition — a claim owns only its cell

**Files:**
- Modify: `web/src/lib/territory.js` (`prepareOwners`, `paintLens`, `buildTerritory`)
- Test: `web/src/lib/territory.test.js`

**Interfaces:**
- Produces: `prepareOwners(manifestCourses, territoryRows) → { centers:[ [x,y] ], ownerOf:number[], ownerRgb:[r,g,b][], paintable:boolean[] }` — **all** courses are seeded; unclaimed courses collapse to one non-paintable owner index.
- Produces: `paintLens(o)` now reads optional `o.paintable` (skips non-paintable owners; absent ⇒ all paintable).
- `buildTerritory(...)` signature unchanged; returns empty layer when there are **no paintable owners**.

- [ ] **Step 1: Rewrite the `prepareOwners` tests for all-courses seeding**

In `web/src/lib/territory.test.js` replace the entire `describe("prepareOwners", …)` block with:

```js
describe("prepareOwners", () => {
  it("seeds ALL courses; claimed dedupe by colour, unclaimed -> one non-paintable owner", () => {
    const rows = [
      { slug:"a", color:"#ff0000" },
      { slug:"b", color:"#ff0000" },   // same colour -> same owner index
      { slug:"c", color:null },        // unclaimed -> the single non-paintable index
    ];
    const r = prepareOwners(courses, rows);
    expect(r.centers).toEqual([[0.1,0.5],[0.9,0.5],[0.5,0.5]]); // every course is a seed
    expect(r.ownerOf).toEqual([0,0,1]);                          // a,b -> 0 (red); c -> 1 (unclaimed)
    expect(r.ownerRgb[0]).toEqual([255,0,0]);
    expect(r.paintable).toEqual([true,false]);
  });

  it("treats malformed colours as unclaimed (non-paintable), still seeded", () => {
    const rows = [
      { slug:"a", color:"#ff0000" },
      { slug:"b", color:"notacolor" },  // malformed -> unclaimed
      { slug:"c", color:"#abc" },       // 3-digit hex unsupported -> unclaimed
    ];
    const r = prepareOwners(courses, rows);
    expect(r.ownerOf).toEqual([0,1,1]);
    expect(r.paintable).toEqual([true,false]);
  });
});
```

- [ ] **Step 2: Add claim-only-your-cell tests to `buildTerritory`**

Append inside the existing `describe("buildTerritory", …)` block:

```js
  it("a single claim paints only its own course cell, not the whole island", () => {
    const W=12,H=1, coverage=new Uint8Array(W*H).fill(255);
    const terr=new Uint8ClampedArray(W*H*4).fill(180);
    const rows=[{slug:"c",color:"#00ff00"}]; // only middle course c claimed; a,b unclaimed
    const rgba=buildTerritory({ coverage, W, H, terr, manifestCourses:courses, territoryRows:rows });
    expect(rgba[6*4+3]).toBeGreaterThan(0);  // c's cell (centre) painted
    expect(rgba[0*4+3]).toBe(0);             // a's cell (left) untouched
    expect(rgba[11*4+3]).toBe(0);            // b's cell (right) untouched
  });

  it("same-owner claims do NOT merge across an unclaimed cell between them", () => {
    const W=12,H=1, coverage=new Uint8Array(W*H).fill(255);
    const terr=new Uint8ClampedArray(W*H*4).fill(180);
    const rows=[{slug:"a",color:"#ff0000"},{slug:"b",color:"#ff0000"}]; // a,b same colour; c unclaimed between
    const rgba=buildTerritory({ coverage, W, H, terr, manifestCourses:courses, territoryRows:rows });
    expect(rgba[0*4+3]).toBeGreaterThan(0);  // a painted
    expect(rgba[11*4+3]).toBeGreaterThan(0); // b painted
    expect(rgba[6*4+3]).toBe(0);             // c (between) stays unclaimed -> not merged
  });
```

- [ ] **Step 3: Run the tests, verify the new ones FAIL**

Run: `npm --prefix web test -- src/lib/territory.test.js`
Expected: FAIL — `prepareOwners` still drops unclaimed courses (`centers` length 2, no `paintable`).

- [ ] **Step 4: Implement the partition change in `territory.js`**

Replace `prepareOwners`:

```js
export function prepareOwners(manifestCourses, territoryRows) {
  const colorBySlug = Object.fromEntries(territoryRows.map((r) => [r.slug, r.color]));
  const idxOf = {}, ownerRgb = [], paintable = [], centers = [], ownerOf = [];
  let unclaimedIdx = -1;
  for (const c of manifestCourses) {
    const color = colorBySlug[c.slug];
    const claimed = color && /^#[0-9a-f]{6}$/i.test(color);
    let oi;
    if (claimed) {
      if (!(color in idxOf)) { idxOf[color] = ownerRgb.length; ownerRgb.push(hexRgb(color)); paintable.push(true); }
      oi = idxOf[color];
    } else {
      if (unclaimedIdx < 0) { unclaimedIdx = ownerRgb.length; ownerRgb.push([0, 0, 0]); paintable.push(false); }
      oi = unclaimedIdx;
    }
    centers.push([c.hit.x + c.hit.w / 2, c.hit.y + c.hit.h / 2]);
    ownerOf.push(oi);
  }
  return { centers, ownerOf, ownerRgb, paintable };
}
```

In `paintLens`, destructure `paintable` and skip non-paintable owners. Change the header + the owner-resolve block:

```js
export function paintLens(o) {
  const { W, H, terr, ownerSm, dB, coastCov, near, ownerRgb, px, paintable } = o;
  const out = new Uint8ClampedArray(W * H * 4);
  const light = ownerRgb.map(ownerLightOf);
  for (let p = 0; p < W * H; p++) {
    const cov = coastCov[p]; if (cov <= 0.004) continue;
    let oi = ownerSm[p], dist;
    if (oi < 0) { oi = near[p]; if (oi < 0) continue; dist = 0; } else dist = dB[p];
    if (paintable && !paintable[oi]) continue;   // unclaimed -> plain terrain shows through
    const O = ownerRgb[oi], Ol = light[oi], q = p * 4;
    // …unchanged from here…
```

In `buildTerritory`, capture `paintable`, guard on paintable owners, and pass `paintable` to `paintLens`:

```js
export function buildTerritory({ coverage, W, H, terr, manifestCourses, territoryRows }) {
  const { centers, ownerOf, ownerRgb, paintable } = prepareOwners(manifestCourses, territoryRows);
  if (!paintable.some(Boolean)) return new Uint8ClampedArray(W * H * 4);   // nobody claims anything
  const land = new Uint8Array(W * H), coastCov = new Float32Array(W * H);
  for (let p = 0; p < W * H; p++) { land[p] = coverage[p] > 127 ? 1 : 0; coastCov[p] = coverage[p] / 255; }
  const centersPx = centers.map((c) => [c[0] * W, c[1] * H]);
  const near = nearestOwner(W, H, centersPx, ownerOf);
  const ownerSm = gooeyPartition(near, land, W, H, ownerRgb.length, Math.round(LENS.gooeyF * W));
  const dB = borderDistance(ownerSm, W, H);
  return paintLens({ ownerSm, dB, coastCov, near, W, H, ownerRgb, terr, paintable,
    px: { rimW: LENS.rimWidthF * W, halo: LENS.haloF * W, borderLean: LENS.borderLeanF * W } });
}
```

- [ ] **Step 5: Run the whole web suite, verify green**

Run: `npm --prefix web test`
Expected: PASS (all territory tests, including the existing `paintLens`/`buildTerritory` ones — `paintLens` defaults to all-paintable when `paintable` is absent).

- [ ] **Step 6: Commit**

```bash
git add web/src/lib/territory.js web/src/lib/territory.test.js
git commit -m "feat(web): territory partition seeds all courses, paints only claimed cells"
```

---

## Task 2: `flippedCourses` snapshot diff helper

**Files:**
- Modify: `web/src/lib/timeline.js`
- Test: `web/src/lib/timeline.test.js`

**Interfaces:**
- Produces: `flippedCourses(snapA, snapB) → string[]` — slugs whose owning **player** differs between two snapshots (covers capture, first-claim, and loss). `snapA` may be `null` (treat all of `snapB`'s owners as flipped). Order: ascending slug.

- [ ] **Step 1: Write the failing tests**

Append to `web/src/lib/timeline.test.js`:

```js
import { buildSnapshots, flippedCourses } from "./timeline.js";

describe("flippedCourses", () => {
  const snap = (owners) => ({ owners });
  it("returns slugs whose owner player changed", () => {
    const a = snap({ mc:{player:"Aliias"}, dk:{player:"Gub"} });
    const b = snap({ mc:{player:"Gub"},    dk:{player:"Gub"} });
    expect(flippedCourses(a, b)).toEqual(["mc"]);
  });
  it("counts a brand-new first claim as flipped", () => {
    const a = snap({ mc:{player:"Gub"} });
    const b = snap({ mc:{player:"Gub"}, dk:{player:"Paul"} });
    expect(flippedCourses(a, b)).toEqual(["dk"]);
  });
  it("treats a null prior snapshot as everything flipped", () => {
    const b = snap({ dk:{player:"Paul"}, mc:{player:"Gub"} });
    expect(flippedCourses(null, b)).toEqual(["dk","mc"]);
  });
});
```

- [ ] **Step 2: Run, verify FAIL**

Run: `npm --prefix web test -- src/lib/timeline.test.js`
Expected: FAIL — `flippedCourses is not a function`.

- [ ] **Step 3: Implement**

Append to `web/src/lib/timeline.js`:

```js
// Slugs whose owning player differs between two ownership snapshots (capture, first
// claim, or loss). snapA may be null (everything in snapB counts as flipped). Pure.
export function flippedCourses(snapA, snapB) {
  const a = snapA ? snapA.owners : {};
  const b = snapB.owners;
  const slugs = new Set([...Object.keys(a), ...Object.keys(b)]);
  const out = [];
  for (const slug of slugs) {
    if ((a[slug]?.player ?? null) !== (b[slug]?.player ?? null)) out.push(slug);
  }
  return out.sort();
}
```

- [ ] **Step 4: Run, verify PASS**

Run: `npm --prefix web test -- src/lib/timeline.test.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/timeline.js web/src/lib/timeline.test.js
git commit -m "feat(web): flippedCourses snapshot diff helper"
```

---

## Task 3: Transition math — `territoryAnim.js`

**Files:**
- Create: `web/src/lib/territoryAnim.js`
- Test: `web/src/lib/territoryAnim.test.js`

**Interfaces:**
- Consumes: from `territory.js` — `boxBlur`, `borderDistance`, `paintLens`, `hexRgb`, `LENS`.
- Produces:
  - `prepareTransition({ coverage, terr, W, H, manifestCourses, rowsA, rowsB }) → prep | null` — precomputes everything constant across the transition (unified owners, nearest-course field, per-owner blurred A/B masks over a padded change region, near fields, slices). `null` when no course flips. `coverage` = `Uint8Array(W*H)`; `terr` = `Uint8ClampedArray(W*H*4)`; both at the **backing** resolution.
  - `interpolatePatch(prep, tau) → { x, y, w, h, rgba:Uint8ClampedArray }` — the territory RGBA for the composite sub-region at `tau∈[0,1]`, ready to `putImageData` at `(x,y)` in backing pixels.
- Invariant: `interpolatePatch(prep,0)` equals `buildTerritory(rowsA)` over the patch region; `interpolatePatch(prep,1)` equals `buildTerritory(rowsB)` (±1/255).

- [ ] **Step 1: Write the failing tests**

Create `web/src/lib/territoryAnim.test.js`:

```js
import { describe, it, expect } from "vitest";
import { prepareTransition, interpolatePatch } from "./territoryAnim.js";
import { buildTerritory } from "./territory.js";

// 1-D map: 3 courses, radius collapses to 0 at this width so the partition is exact Voronoi.
const courses = [
  { slug:"a", hit:{x:0.10,y:0.5,w:0,h:0} },
  { slug:"b", hit:{x:0.50,y:0.5,w:0,h:0} },
  { slug:"c", hit:{x:0.90,y:0.5,w:0,h:0} },
];
const W=12, H=1;
const coverage = new Uint8Array(W*H).fill(255);
const terr = new Uint8ClampedArray(W*H*4).fill(160);
const RED="#ff0000", BLUE="#0000ff";

const rowsA = [{slug:"a",color:RED},{slug:"b",color:RED},{slug:"c",color:BLUE}];
const rowsB = [{slug:"a",color:RED},{slug:"b",color:BLUE},{slug:"c",color:BLUE}]; // b flips RED->BLUE

function cropOf(full, region) {                       // pull region rows out of a full-frame RGBA
  const out = new Uint8ClampedArray(region.w*region.h*4);
  for (let yy=0; yy<region.h; yy++)
    for (let xx=0; xx<region.w; xx++) {
      const s=((region.y+yy)*W+(region.x+xx))*4, d=(yy*region.w+xx)*4;
      out[d]=full[s]; out[d+1]=full[s+1]; out[d+2]=full[s+2]; out[d+3]=full[s+3];
    }
  return out;
}

describe("prepareTransition", () => {
  it("returns null when nothing flips", () => {
    expect(prepareTransition({ coverage, terr, W, H, manifestCourses:courses, rowsA, rowsB:rowsA })).toBeNull();
  });
});

describe("interpolatePatch endpoints match the static partition", () => {
  const prep = prepareTransition({ coverage, terr, W, H, manifestCourses:courses, rowsA, rowsB });
  it("tau=0 equals buildTerritory(rowsA) over the patch", () => {
    const p = interpolatePatch(prep, 0);
    const want = cropOf(buildTerritory({ coverage, W, H, terr, manifestCourses:courses, territoryRows:rowsA }), p);
    for (let i=0;i<want.length;i++) expect(Math.abs(p.rgba[i]-want[i])).toBeLessThanOrEqual(1);
  });
  it("tau=1 equals buildTerritory(rowsB) over the patch", () => {
    const p = interpolatePatch(prep, 1);
    const want = cropOf(buildTerritory({ coverage, W, H, terr, manifestCourses:courses, territoryRows:rowsB }), p);
    for (let i=0;i<want.length;i++) expect(Math.abs(p.rgba[i]-want[i])).toBeLessThanOrEqual(1);
  });
  it("the patch covers b's cell (the flipped course)", () => {
    const p = interpolatePatch(prep, 0.5);
    expect(p.x).toBeLessThanOrEqual(6); expect(p.x+p.w).toBeGreaterThanOrEqual(6); // b centre = 0.5*12 = 6
  });
});
```

- [ ] **Step 2: Run, verify FAIL**

Run: `npm --prefix web test -- src/lib/territoryAnim.test.js`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `territoryAnim.js`**

Create `web/src/lib/territoryAnim.js`:

```js
// Pure transition math for the territory border-push animation. No DOM.
// Heavy work (unified owners, nearest-course field, per-owner blurred masks for the
// before/after snapshots) is precomputed once per transition; interpolatePatch is the
// cheap per-frame call. Lerping the BLURRED owner masks then argmaxing makes the gooey
// border slide continuously from the A-partition to the B-partition (a real border push;
// an isolated first claim with no adjacent same-owner mass instead grows from its course).
import { boxBlur, borderDistance, paintLens, hexRgb, LENS } from "./territory.js";

const clamp = (v,a,b) => (v<a?a:v>b?b:v);
const isHex = (c) => c && /^#[0-9a-f]{6}$/i.test(c);

// Unified owner palette across both snapshots; every course is a seed. Unclaimed -> one
// non-paintable index. Returns per-course owner index for A and B in the shared space.
function unifiedOwners(manifestCourses, rowsA, rowsB) {
  const colA = Object.fromEntries(rowsA.map((r)=>[r.slug,r.color]));
  const colB = Object.fromEntries(rowsB.map((r)=>[r.slug,r.color]));
  const idxOf = {}, ownerRgb = [], paintable = [];
  let unclaimed = -1;
  const ensure = (color) => {
    if (!isHex(color)) { if (unclaimed<0){ unclaimed=ownerRgb.length; ownerRgb.push([0,0,0]); paintable.push(false); } return unclaimed; }
    if (!(color in idxOf)) { idxOf[color]=ownerRgb.length; ownerRgb.push(hexRgb(color)); paintable.push(true); }
    return idxOf[color];
  };
  const centers=[], ownerOfA=[], ownerOfB=[];
  for (const c of manifestCourses) {
    centers.push([c.hit.x + c.hit.w/2, c.hit.y + c.hit.h/2]);
    ownerOfA.push(ensure(colA[c.slug]));
    ownerOfB.push(ensure(colB[c.slug]));
  }
  return { centers, ownerOfA, ownerOfB, ownerRgb, paintable };
}

// Index of the nearest course centre for every pixel (Voronoi seed id).
function nearestCourse(W, H, centersPx) {
  const nc = new Int16Array(W*H);
  for (let y=0;y<H;y++) for (let x=0;x<W;x++) {
    let bi=0, bd=1e18;
    for (let i=0;i<centersPx.length;i++){ const dx=centersPx[i][0]-x, dy=centersPx[i][1]-y, dd=dx*dx+dy*dy; if(dd<bd){bd=dd;bi=i;} }
    nc[y*W+x]=bi;
  }
  return nc;
}

export function prepareTransition({ coverage, terr, W, H, manifestCourses, rowsA, rowsB }) {
  const { centers, ownerOfA, ownerOfB, ownerRgb, paintable } = unifiedOwners(manifestCourses, rowsA, rowsB);
  const flipped = new Set();
  for (let i=0;i<manifestCourses.length;i++) if (ownerOfA[i]!==ownerOfB[i]) flipped.add(i);
  if (flipped.size === 0) return null;

  const centersPx = centers.map((c)=>[c[0]*W, c[1]*H]);
  const nc = nearestCourse(W, H, centersPx);

  // Tight bbox of the flipped cells, then pad: haloPx (rim bleed into neighbours) is kept in
  // the composite output; blurR (gooey reach) extends the COMPUTE region and is discarded so
  // borderDistance's artificial frame-edge rim never reaches a painted pixel.
  const blurR = Math.round(LENS.gooeyF * W);
  const haloPx = Math.ceil(LENS.haloF * W) + 2;
  let minx=W, miny=H, maxx=-1, maxy=-1;
  for (let y=0;y<H;y++) for (let x=0;x<W;x++) if (flipped.has(nc[y*W+x])) {
    if(x<minx)minx=x; if(x>maxx)maxx=x; if(y<miny)miny=y; if(y>maxy)maxy=y;
  }
  // composite (output) region = tight + halo; compute region = composite + blurR.
  const cx0 = clamp(minx-haloPx,0,W-1), cy0 = clamp(miny-haloPx,0,H-1);
  const cx1 = clamp(maxx+haloPx,0,W-1), cy1 = clamp(maxy+haloPx,0,H-1);
  const gx0 = clamp(cx0-blurR,0,W-1), gy0 = clamp(cy0-blurR,0,H-1);
  const gx1 = clamp(cx1+blurR,0,W-1), gy1 = clamp(cy1+blurR,0,H-1);
  const gw = gx1-gx0+1, gh = gy1-gy0+1;                 // compute-region dims

  // Slice land/coverage/terr + build per-owner binary masks (A and B) over the compute region.
  const nOwners = ownerRgb.length;
  const land = new Uint8Array(gw*gh), covSlice = new Float32Array(gw*gh), terrSlice = new Uint8ClampedArray(gw*gh*4);
  const maskA = Array.from({length:nOwners}, ()=>new Float32Array(gw*gh));
  const maskB = Array.from({length:nOwners}, ()=>new Float32Array(gw*gh));
  const nearA = new Int16Array(gw*gh), nearB = new Int16Array(gw*gh);
  for (let yy=0; yy<gh; yy++) for (let xx=0; xx<gw; xx++) {
    const gp=(gy0+yy)*W+(gx0+xx), lp=yy*gw+xx;
    const isLand = coverage[gp] > 127 ? 1 : 0;
    land[lp]=isLand; covSlice[lp]=coverage[gp]/255;
    terrSlice[lp*4]=terr[gp*4]; terrSlice[lp*4+1]=terr[gp*4+1]; terrSlice[lp*4+2]=terr[gp*4+2]; terrSlice[lp*4+3]=terr[gp*4+3];
    const course = nc[gp], oa=ownerOfA[course], ob=ownerOfB[course];
    nearA[lp]=oa; nearB[lp]=ob;
    if (isLand) { maskA[oa][lp]=1; maskB[ob][lp]=1; }
  }
  const blurA = maskA.map((m)=>boxBlur(m, blurR, gw, gh));
  const blurB = maskB.map((m)=>boxBlur(m, blurR, gw, gh));

  return {
    gw, gh, gx0, gy0, nOwners, ownerRgb, paintable, land, covSlice, terrSlice, blurA, blurB, nearA, nearB,
    // composite sub-rect inside the compute region (drops the blurR ring):
    out: { x: cx0, y: cy0, w: cx1-cx0+1, h: cy1-cy0+1, ox: cx0-gx0, oy: cy0-gy0 },
    px: { rimW: LENS.rimWidthF * W, halo: LENS.haloF * W, borderLean: LENS.borderLeanF * W },
  };
}

export function interpolatePatch(prep, tau) {
  const { gw, gh, nOwners, ownerRgb, paintable, land, covSlice, terrSlice, blurA, blurB, nearA, nearB, out, px } = prep;
  const t = clamp(tau,0,1);
  // argmax of the lerped blurred masks -> the border position at this tau.
  const ownerSm = new Int16Array(gw*gh).fill(-1);
  for (let p=0;p<gw*gh;p++) {
    if (!land[p]) continue;
    let best=-Infinity, bi=-1;
    for (let o=0;o<nOwners;o++){ const v=(1-t)*blurA[o][p]+t*blurB[o][p]; if (v>best){best=v;bi=o;} }
    ownerSm[p]=bi;
  }
  const near = t<0.5 ? nearA : nearB;                  // coast feather owner (exact at the endpoints)
  const dB = borderDistance(ownerSm, gw, gh);
  const full = paintLens({ W:gw, H:gh, terr:terrSlice, ownerRgb, paintable, ownerSm, dB, near, coastCov:covSlice, px });

  // Crop the composite sub-rect (drop the discarded blurR ring) into a tight patch.
  const rgba = new Uint8ClampedArray(out.w*out.h*4);
  for (let yy=0; yy<out.h; yy++) for (let xx=0; xx<out.w; xx++) {
    const s=((out.oy+yy)*gw+(out.ox+xx))*4, d=(yy*out.w+xx)*4;
    rgba[d]=full[s]; rgba[d+1]=full[s+1]; rgba[d+2]=full[s+2]; rgba[d+3]=full[s+3];
  }
  return { x: out.x, y: out.y, w: out.w, h: out.h, rgba };
}
```

- [ ] **Step 4: Run, verify PASS**

Run: `npm --prefix web test -- src/lib/territoryAnim.test.js`
Expected: PASS (endpoint-equality invariants + bbox coverage).

- [ ] **Step 5: Run the whole suite + commit**

Run: `npm --prefix web test`
Expected: PASS.

```bash
git add web/src/lib/territoryAnim.js web/src/lib/territoryAnim.test.js
git commit -m "feat(web): territoryAnim — per-frame interpolated border-push patch"
```

---

## Task 4: Single DPR-aware canvas (crispness + remove the crossfade flash)

**Files:**
- Modify: `web/src/lib/territoryWorker.js`
- Modify: `web/src/WorldMap.svelte`

**Interfaces:**
- The worker accepts an optional target size and renders the base frame at it.
- `WorldMap` keeps **one** `.territory` canvas sized to `displayCssWidth × devicePixelRatio` (capped at the 2200 asset). Present renders at 2200 → downscales in; timeline base frames render at the backing size. Playback hard-cuts (no opacity crossfade) until Task 5.

This is a rendering refactor verified by `svelte-check` + CDP screenshots (no per-unit test).

- [ ] **Step 1: Let the worker render at a target size**

In `web/src/lib/territoryWorker.js`, read an optional `targetW`/`targetH` and build at it (the partition is resolution-independent; constants scale by W). Replace the handler:

```js
self.onmessage = async (e) => {
  const { coverageBitmap, baseBitmap, W, H, targetW, targetH, manifestCourses, territoryRows } = e.data;
  const rw = targetW || W, rh = targetH || H;
  const cd = readRGBA(coverageBitmap, rw, rh);
  const coverage = new Uint8Array(rw * rh);
  for (let p = 0; p < rw * rh; p++) coverage[p] = cd[p * 4];
  const terr = readRGBA(baseBitmap, rw, rh);
  const rgba = buildTerritory({ coverage, W: rw, H: rh, terr, manifestCourses, territoryRows });
  const bitmap = await createImageBitmap(new ImageData(rgba, rw, rh));
  self.postMessage({ bitmap }, [bitmap]);
};
```

(`readRGBA(bitmap, W, H)` already draws the source scaled into a `W×H` OffscreenCanvas, so passing `rw/rh` renders at the target size.)

- [ ] **Step 2: Collapse to a single DPR-aware canvas in `WorldMap.svelte`**

Markup — replace the two `.territory` canvases with one:

```svelte
<canvas class="territory" bind:this={terr} aria-hidden="true"></canvas>
```

Script — remove `terrA, terrB, activeLayer, crossfade, presentBitmap` dual-layer state; add a single `terr` ref + a backing-size helper. Replace the layer/paint helpers:

```js
let terr;                          // the single territory canvas
let backW = 1100, backH = 888;     // backing-store pixels (device px), set by sizeCanvas
const ASSET_W = 2200;              // cap: the island/base asset native width

function sizeCanvas() {
  if (!terr || !stageEl) return;
  const cssW = stageEl.clientWidth || 1100;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  backW = Math.min(ASSET_W, Math.round(cssW * dpr));
  backH = Math.round(backW * (1775 / 2200));
  terr.width = backW; terr.height = backH;
}

function paintBitmap(bitmap) {     // draw a full-frame territory bitmap, AA-downscaled into the backing store
  if (!terr || !bitmap) return;
  const ctx = terr.getContext("2d");
  ctx.clearRect(0, 0, backW, backH);
  ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = "high";
  ctx.drawImage(bitmap, 0, 0, bitmap.width, bitmap.height, 0, 0, backW, backH);
}
```

CSS — drop the `.layer/.on/.cross` rules; keep the single canvas:

```css
.territory { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }
```

- [ ] **Step 3: Repoint present + timeline rendering at the single canvas**

- `renderTerritory()` (present): keep the 2200 worker render; in `worker.onmessage`, store `presentBitmap = e.data.bitmap` and `paintBitmap(presentBitmap)`.
- Timeline base frames (`ensureBitmap` / `tlRenderViaWorker`): pass `targetW: backW, targetH: backH` so historical frames render at the backing size (supersampled vs the old fixed 1100). Update the cache cap comment; keep `TL_CACHE_CAP` but recompute the ceiling against `backW`.
- `showSnapshot(i)`: paint the chosen bitmap via `paintBitmap(...)` directly (hard cut). Remove the `crossfade`/`activeLayer` flip entirely.
- `onMount`: call `sizeCanvas()` after `await tick()` and before `renderTerritory()`. Add a `window`-`resize` listener that calls `sizeCanvas()` then re-paints the current frame; remove it in `onDestroy`.

- [ ] **Step 4: svelte-check + visual smoke**

Run: `npm --prefix web run check`
Expected: 0 errors / 0 warnings.

Then visually (CDP, per Global Constraints): load `#/map` at `devicePixelRatio=2`; confirm the present territory rims are **crisp** (not soft) and play steps **hard-cut with no brightness flash** between snapshots.

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/territoryWorker.js web/src/WorldMap.svelte
git commit -m "feat(web): single DPR-aware territory canvas; drop the crossfade flash"
```

---

## Task 5: Border-push animation on playback

**Files:**
- Modify: `web/src/WorldMap.svelte`

**Interfaces:**
- Consumes: `flippedCourses` (Task 2), `prepareTransition`/`interpolatePatch` (Task 3), `paintBitmap`/backing buffers (Task 4).
- On **play**, each step animates the transition (sliding border); on **scrub**, hard-cut.

- [ ] **Step 1: Build backing-resolution source buffers once (for the per-frame patch)**

The patch composites at backing pixels, so the main thread needs `coverage`+`terr` at backing res. After `sizeCanvas()` (and on resize), build them from the source bitmaps via an offscreen canvas:

```js
let bkCoverage = null, bkTerr = null;   // backing-res source buffers for the animation
let srcCov = null, srcBase = null;      // ImageBitmaps of island.png + base.jpg (kept from load)

function buildBackingBuffers() {
  if (!srcCov || !srcBase) return;
  const c = document.createElement("canvas"); c.width = backW; c.height = backH;
  const x = c.getContext("2d", { willReadFrequently: true });
  x.drawImage(srcCov, 0, 0, backW, backH);
  const cd = x.getImageData(0, 0, backW, backH).data;
  bkCoverage = new Uint8Array(backW * backH);
  for (let p = 0; p < bkCoverage.length; p++) bkCoverage[p] = cd[p * 4];
  x.clearRect(0, 0, backW, backH);
  x.drawImage(srcBase, 0, 0, backW, backH);
  bkTerr = new Uint8ClampedArray(x.getImageData(0, 0, backW, backH).data);
}
```

Keep `srcCov`/`srcBase` when loading (in `renderTerritory`/`loadTimeline` fetch the bitmaps once and retain them); call `buildBackingBuffers()` after `sizeCanvas()`.

- [ ] **Step 2: Add the transition runner**

```js
import { flippedCourses } from "./lib/timeline.js";
import { prepareTransition, interpolatePatch } from "./lib/territoryAnim.js";

let animRaf = 0;
const ANIM_MS = 520;                       // per-transition beat (tunable)
const easeInOut = (t) => t*t*(3-2*t);

function rowsOf(i) {
  return Object.entries(snapshots[i].owners).map(([slug,o]) => ({ slug, color: o.color }));
}

// Animate the border-push from snapshot `from` to `to`; resolves when settled.
function animateTransition(from, to) {
  return new Promise((resolve) => {
    cancelAnimationFrame(animRaf);
    if (!bkCoverage || !bkTerr || flippedCourses(snapshots[from], snapshots[to]).length === 0) {
      drawBaseFrame(to); resolve(); return;       // nothing to animate -> hard set
    }
    const prep = prepareTransition({
      coverage: bkCoverage, terr: bkTerr, W: backW, H: backH,
      manifestCourses: manifest.courses, rowsA: rowsOf(from), rowsB: rowsOf(to),
    });
    if (!prep) { drawBaseFrame(to); resolve(); return; }
    const ctx = terr.getContext("2d");
    const t0 = performance.now();
    const tick = (now) => {
      const tau = easeInOut(Math.min(1, (now - t0) / ANIM_MS));
      const patch = interpolatePatch(prep, tau);
      ctx.putImageData(new ImageData(patch.rgba, patch.w, patch.h), patch.x, patch.y);
      if (tau < 1) { animRaf = requestAnimationFrame(tick); }
      else { drawBaseFrame(to); resolve(); }       // settle on the canonical AA base frame
    };
    // Ensure the static base = `from` is on the canvas before patching over it.
    drawBaseFrame(from);
    animRaf = requestAnimationFrame(tick);
  });
}
```

`drawBaseFrame(i)` = the Task-4 hard-paint (present bitmap at LIVE, else the cached/await-rendered timeline bitmap via `paintBitmap`). Factor `showSnapshot`'s paint into `drawBaseFrame(i)`.

- [ ] **Step 2b: Tune the unclaimed→owned growth (isolated first-claim)**

Verify visually that a first claim with no same-owner neighbour **grows from its course** rather than snapping at `tau≈0.5`. If it snaps, raise the new owner's influence near its course centre: in `interpolatePatch`, bias `blurB[o]` for flipped-only pixels by a centre-weighted term (a tuning lever; keep the endpoint invariants intact). Confirm `npm --prefix web test` still passes.

- [ ] **Step 3: Drive play through the animation; keep scrub instant**

- `step()`: replace `showSnapshot(next, true)` with `await animateTransition(tlIndex, next)` then schedule the next step (gate the timer on `playing`). Same-timestamp dense runs already collapse into single snapshots via `buildSnapshots`, so each step is one animated beat.
- Scrub handler (`on:scrub`): `cancelAnimationFrame(animRaf)` + `drawBaseFrame(targetIndex)` (hard cut, no animation).
- `onDestroy`: `cancelAnimationFrame(animRaf)`.

- [ ] **Step 4: Visual verification (CDP — the no-flash + sliding-border gate)**

Per Global Constraints, drive `#/map`, press play, and capture a **mid-transition** frame plus the frame just before it. Confirm:
- the contested border has **moved partway** (not a full-region dissolve), with a visible owner rim on the moving front;
- the rest of the map is **pixel-identical** between the two frames (no global brightness dip = no flash);
- early history: a single first claim animates **only its cell**.

- [ ] **Step 5: Commit**

```bash
git add web/src/WorldMap.svelte
git commit -m "feat(web): border-push playback animation (sliding gooey borders, no flash)"
```

---

## Task 6: Layout — controls on top, fit-to-viewport, composed (frontend-design)

**Files:**
- Modify: `web/src/WorldMap.svelte` (layout + legend)
- Modify: `web/src/TimelineScrubber.svelte` (relocated above the map; restyle into the composition)

**Interfaces:** no new public interfaces — this is composition + CSS. Use the **frontend-design skill** for the visual treatment; the deterministic part (fit-to-viewport sizing) is specified below. Verified by `svelte-check` + CDP screenshots + a vite build smoke.

- [ ] **Step 1: Invoke the frontend-design skill**

Before writing layout CSS, invoke `frontend-design:frontend-design` and apply it to: the title row, the transport strip, the player legend/standings, and how the map frame sits in the page. Honour the project's restraint (graphite tokens in `web/src/theme.css`; calm-at-rest base). Acceptance criteria the design must meet are Steps 2–4.

- [ ] **Step 2: Restructure `.map-view` — controls on top, map fills the rest**

Target DOM order under the existing site header: **title row → transport (TimelineScrubber) → legend/standings → map frame**. The map is sized to consume the remaining viewport height so nothing scrolls on a 1080p screen:

```css
.map-view {
  height: calc(100vh - var(--header-h, 52px));
  display: flex; flex-direction: column; gap: 10px;
  padding: 12px 16px; box-sizing: border-box; overflow: hidden;
}
.controls { flex: none; }                 /* title + transport + legend */
.frame { flex: 1 1 auto; min-height: 0;   /* map fills remaining height */
  max-width: none; margin: 0 auto; aspect-ratio: 2200 / 1775; }
```

Drive the canvas/stage to fit by height (width follows the 2200:1775 aspect), centred, capped so it never exceeds the asset:

```js
// in sizeCanvas(), cap cssW so the frame fits the available height too:
const availH = frameEl.clientHeight;
const cssW = Math.min(stageEl.clientWidth || 1100, availH * (2200/1775));
```

After resize, `sizeCanvas()` → `buildBackingBuffers()` → re-`drawBaseFrame(tlIndex)`.

- [ ] **Step 3: Move the scrubber up + add the legend/standings strip**

- Relocate `<TimelineScrubber/>` from below the frame into the top `.controls` block; restyle (frontend-design) so it reads as the view's transport, not a detached strip.
- Add a compact legend: for each player in the current snapshot's owners, a colour swatch + name + PB-count (count of courses they own in the LIVE snapshot). Keep it to one restrained row. Source colours/owners from `snapshots[snapshots.length-1].owners` (LIVE) or the present `territoryRows`.

- [ ] **Step 4: Verify fit + composition (CDP) + svelte-check**

Run: `npm --prefix web run check`  → 0/0.
CDP at 1920×1080: the **entire** view (header, title, transport, legend, full map) fits with **no scrollbar**; the map is centred and uncramped; the composition reads as a deliberate panel.

- [ ] **Step 5: Build smoke (module-worker bundling)**

Run: `npm --prefix web run build`
Expected: builds clean (the territory + animation workers bundle under `vite build`, not just dev).

- [ ] **Step 6: Commit**

```bash
git add web/src/WorldMap.svelte web/src/TimelineScrubber.svelte
git commit -m "feat(web): controls-on-top fit-to-viewport map layout + player legend"
```

---

## Self-Review

**1. Spec coverage:**
- §1 partition (claim-only-cell) → Task 1. ✓
- §2 animation (border-push, no flash, single canvas, isolated-claim growth, simultaneous flips) → Tasks 3 (math) + 4 (single canvas) + 5 (playback). ✓ (simultaneous same-`t` flips collapse in `buildSnapshots`; one animated beat per snapshot.)
- §3 render crispness (DPR backing store, supersampled timeline frames, cache rebalance) → Task 4. ✓
- §4 layout (controls on top, fit-to-viewport, legend, frontend-design) → Task 6. ✓
- §5 module boundaries → file structure + `territoryAnim.js`. ✓
- §6 testing (territory/timeline/anim units; CDP visual; build smoke) → Tasks 1–6 steps. ✓
- §0 gub colour → already done, out of build scope. ✓

**2. Placeholder scan:** No "TBD/handle edge cases/similar to". The two non-code judgement points are explicit and bounded: Task 5 Step 2b (isolated-claim growth tuning lever, with the invariant to preserve) and Task 6 Step 1 (frontend-design for visual treatment, with Steps 2–4 as hard acceptance criteria). Both are real iterative-visual work, not vague hand-waving.

**3. Type consistency:** `prepareOwners` returns `{centers, ownerOf, ownerRgb, paintable}` (Task 1) and `paintLens` reads `paintable` consistently (Tasks 1 + 3 via the same `territory.js`). `prepareTransition`→`interpolatePatch` share the `prep` shape (Task 3); `interpolatePatch` returns `{x,y,w,h,rgba}` consumed verbatim by `putImageData` in Task 5. `flippedCourses(snapA, snapB)` (Task 2) is consumed with the same signature in Task 5. `paintBitmap`/`drawBaseFrame`/`sizeCanvas`/`backW`/`backH`/`bkCoverage`/`bkTerr` defined in Task 4 and reused in Tasks 5–6. Consistent.

## Risks (carried from the spec)

- **Patch/base seam** at the composite region edge — guarded by computing patch and base at the same backing resolution; the `blurR` ring is discarded to kill the false frame-edge rim. Verify at the seam in CDP.
- **Large simultaneous-flip bbox** — if a single snapshot flips far-apart cells the patch region is large; acceptable for the (clustered) real history, otherwise drop to per-cell patches.
- **Moving-border AA** — the patch computes at backing res 1:1 (mild aliasing in motion only); the settled frame is the AA base. Lever: 2× the compute region if the user wants the moving edge crisper.
