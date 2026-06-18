# SP2 Territory Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Paint each competitor's PB territory on the World Map as a gap-free "gooey Voronoi" partition in the locked "Strategy lens" style (dimmed terrain + owner tint + bright anti-aliased rim), over the visible ocean, with the existing interactive icons on top.

**Architecture:** A new public server read returns the #1 PB holder per course. The web app loads that + a baked island-coverage PNG + the base map, computes the partition and paints the lens **in a Web Worker** at 2× resolution, and draws the result (high-quality downscaled = anti-aliased) into the existing `.territory` canvas layer of `WorldMap.svelte`. One-time render on map load; no animation, no sliders.

**Tech Stack:** Pi season server (Hono + `node:sqlite`, TypeScript, vitest). Web SPA (`web/`, Vite + Svelte, vitest). Build script (Python + Pillow + OpenCV, like `scripts/map/build_map_assets.py`).

## Global Constraints

- Reference implementation for the exact algorithm + tuned look: `.superpowers/brainstorm/5473-1781762885/content/lens-focus-v4.html` (the `subFromNear` / `paintLens` functions).
- **Locked visual constants** (fractions of the internal map width `W`, unless noted): `DIM=0.40`, `tint=0.40`, `rimBright=0.74`, `rimWidthF=0.0020`, `haloF=0.0093`, `borderLeanF=0.0293`, `gooeyF=0.014`, `ownerLight = mix(owner, white, 0.55)`. Coastline `smoothFrac=0.0080` and `featherFrac=0.0020` are baked into the island asset, NOT applied at runtime.
- The new read `/v1/territory` MUST be **public (no token) + permissive CORS (GET)** — add it to `PUBLIC_READS` in `pi/src/api/app.ts` (same treatment as `/v1/leaderboard`).
- **Visual verification is headless Edge ONLY, never OpenCV** — the browser is ground truth for compositing/scaling. Command: `msedge --headless=new --disable-gpu --no-sandbox --user-data-dir=<fresh tmp> --disk-cache-size=1 --window-size=1300,1100 --virtual-time-budget=20000 --screenshot=out.png <url>`.
- **No animation.** Static render only.
- Internal render resolution = the island asset's frame (2200×1775); downscale to the display canvas with `imageSmoothingQuality='high'` for anti-aliasing (inner rims AND coast).
- Keep all suites green: `npm --prefix pi test`, `npm --prefix web test`, `npm --prefix web run check` (svelte-check 0 errors / 0 warnings).
- We are on `main`. **Branch first** (`git checkout -b sp2-territory-overlay`) before Task 1; do not commit to `main`.
- Strength/dominance is OUT of scope (deferred). Territory = owner (#1 PB holder) only.

---

### Task 0: Branch

- [ ] **Step 1: Create the working branch**

```bash
git checkout -b sp2-territory-overlay
```

---

### Task 1: `/v1/territory` public read (owner per course)

**Files:**
- Modify: `pi/src/db/reads.ts` (add `territoryOwners` + type)
- Modify: `pi/src/db/reads.test.ts` (add tests)
- Modify: `pi/src/api/reads.ts` (import + route)
- Modify: `pi/src/api/app.ts:32` (`PUBLIC_READS` array)

**Interfaces:**
- Produces: `territoryOwners(db, seasonId, cc) -> TerritoryOwner[]` where
  `TerritoryOwner = { course_id: number; slug: string; display_name: string; owner_player_id: number | null; owner_name: string | null; color: string | null }`.
  HTTP: `GET /v1/territory?cc=150` (no token), returns that array as JSON.

- [ ] **Step 1: Write the failing test** — append to `pi/src/db/reads.test.ts`:

```ts
import { territoryOwners } from './reads';

describe('territoryOwners', () => {
  it('returns each course #1 PB holder + colour, null when unclaimed', () => {
    const db = openDb(':memory:'); applySchema(db);
    db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
    db.exec("INSERT INTO players(id,display_name,color) VALUES (1,'Paul','#a78bfa'),(2,'Gub','#2dd4bf')");
    db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','RR'),(2,'mc','MC'),(3,'pb','PB')");
    // rr: Paul 108s beats Gub 112s -> Paul; mc: Gub only -> Gub; pb: no PB -> unclaimed
    db.exec("INSERT INTO runs(season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb) VALUES " +
      "(1,1,1,150,'finished','live',108000,1),(1,2,1,150,'finished','live',112000,1),(1,2,2,150,'finished','live',99000,1)");
    const by = Object.fromEntries(territoryOwners(db, 1, 150).map(r => [r.slug, r]));
    expect(by.rr.owner_name).toBe('Paul'); expect(by.rr.color).toBe('#a78bfa');
    expect(by.mc.owner_name).toBe('Gub');
    expect(by.pb.owner_player_id).toBe(null); expect(by.pb.color).toBe(null);
  });
  it('filters by season and cc', () => {
    const db = openDb(':memory:'); applySchema(db);
    db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1),(2,'S2',0)");
    db.exec("INSERT INTO players(id,display_name,color) VALUES (1,'Paul','#a78bfa')");
    db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','RR')");
    db.exec("INSERT INTO runs(season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb) VALUES " +
      "(2,1,1,150,'finished','live',108000,1),(1,1,1,200,'finished','live',90000,1)");
    expect(territoryOwners(db, 1, 150).find(r => r.slug === 'rr')?.owner_player_id).toBe(null);
  });
});
```

- [ ] **Step 2: Run it; verify it fails**

Run: `npm --prefix pi test -- reads`
Expected: FAIL — `territoryOwners is not a function`.

- [ ] **Step 3: Implement `territoryOwners`** — append to `pi/src/db/reads.ts`:

```ts
export type TerritoryOwner = {
  course_id: number; slug: string; display_name: string;
  owner_player_id: number | null; owner_name: string | null; color: string | null;
};

/** Every course with its #1 PB holder (lowest total_time_ms, is_pb) for the season+cc,
 *  plus that player's colour. owner_* / color are null for unclaimed courses. */
export function territoryOwners(db: DatabaseSync, seasonId: number, cc: number): TerritoryOwner[] {
  return db.prepare(
    `SELECT c.id AS course_id, c.slug, c.display_name,
            t.player_id AS owner_player_id, p.display_name AS owner_name, p.color
     FROM courses c
     LEFT JOIN (
       SELECT course_id, player_id,
              ROW_NUMBER() OVER (PARTITION BY course_id ORDER BY total_time_ms ASC) AS rn
       FROM runs WHERE season_id=? AND cc=? AND is_pb=1 AND total_time_ms IS NOT NULL
     ) t ON t.course_id = c.id AND t.rn = 1
     LEFT JOIN players p ON p.id = t.player_id
     ORDER BY c.slug`
  ).all(seasonId, cc) as TerritoryOwner[];
}
```

- [ ] **Step 4: Run the test; verify it passes**

Run: `npm --prefix pi test -- reads`
Expected: PASS (all `reads` tests).

- [ ] **Step 5: Add the route** — in `pi/src/api/reads.ts`, add `territoryOwners` to the import on line 6, then add this route after the `/v1/leaderboard/overall` route (line 22):

```ts
  r.get('/v1/territory', (c) => c.json(territoryOwners(db, season(c), num(c.req.query('cc'), 150))));
```

- [ ] **Step 6: Make it public + CORS** — in `pi/src/api/app.ts:32`, add `/v1/territory`:

```ts
  const PUBLIC_READS = ['/v1/leaderboard', '/v1/world-records', '/v1/roster', '/v1/territory'];
```

- [ ] **Step 7: Verify the route serves token-free + CORS**

Run (two shells): `npm --prefix pi run dev`  then  `curl -i "http://localhost:8787/v1/territory?cc=150"`
Expected: `HTTP/1.1 200`, header `access-control-allow-origin: *`, body = JSON array of `{course_id,slug,display_name,owner_player_id,owner_name,color}`. (Compare: `curl -i "http://localhost:8787/v1/seasons"` still returns `401`.)

- [ ] **Step 8: Commit**

```bash
git add pi/src/db/reads.ts pi/src/db/reads.test.ts pi/src/api/reads.ts pi/src/api/app.ts
git commit -m "feat(pi): /v1/territory public read - #1 PB holder + colour per course"
```

---

### Task 2: Bake the island coverage asset

**Files:**
- Create: `scripts/map/sources/island_mask.png` (copy of the hand-traced mask)
- Create: `scripts/map/build_island_coverage.py`
- Create: `web/public/map/island.png` (build output, committed)

**Interfaces:**
- Produces: `web/public/map/island.png` — grayscale, same frame as `base.jpg` (2200×1775). Client reads it as: `land = px > 127`, `coastCov = px / 255` (already shape-smoothed + edge-feathered).

- [ ] **Step 1: Vendor the traced mask as a committed source**

```bash
mkdir -p scripts/map/sources
cp ".superpowers/brainstorm/5473-1781762885/content/island_mask3.png" scripts/map/sources/island_mask.png
```

- [ ] **Step 2: Write the build script** — create `scripts/map/build_island_coverage.py`:

```python
#!/usr/bin/env python3
"""Bake the territory island coverage asset for the World Map (SP2).

Reads the hand-traced island mask, ROUNDS the coastline (blur -> re-threshold,
removes the faceting of the 161-segment trace) and FEATHERS the edge (small blur)
into one grayscale coverage PNG. The web client uses it for BOTH the land test
(px > 127) and the anti-aliased coast (px / 255), so it does no mask work at runtime.
Re-run with a different SMOOTH_FRAC to re-tune coastline rounding.

  python scripts/map/build_island_coverage.py
"""
from pathlib import Path
import cv2, numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
SRC = Path(__file__).resolve().parent / "sources" / "island_mask.png"
OUT = ROOT / "web" / "public" / "map" / "island.png"

W = 2200            # match base.jpg frame width
SMOOTH_FRAC = 0.0080   # coastline shape rounding radius (fraction of width)
FEATHER_FRAC = 0.0020  # anti-alias feather radius (fraction of width)

def box(a, r):
    k = 2 * r + 1
    return cv2.blur(a, (k, k), borderType=cv2.BORDER_REPLICATE)

def main():
    src = np.asarray(Image.open(SRC).convert("L"))
    H = round(W * src.shape[0] / src.shape[1])
    a = cv2.resize(src, (W, H), interpolation=cv2.INTER_AREA)
    binary = (a > 127).astype(np.float32)
    rs = max(1, round(SMOOTH_FRAC * W))
    shape = (box(box(binary, rs), rs) >= 0.5).astype(np.float32)   # rounded binary coastline
    rf = max(1, round(FEATHER_FRAC * W))
    cov = np.clip(box(shape, rf), 0.0, 1.0)                        # AA feather
    OUT.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray((cov * 255).astype(np.uint8), "L").save(OUT)
    print(f"wrote {OUT} ({W}x{H}), feather px = {((cov > 0.02) & (cov < 0.98)).sum()}")

if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run the build**

Run: `python scripts/map/build_island_coverage.py`
Expected: prints `wrote .../web/public/map/island.png (2200x1775), feather px = <a number well above 0>`.

- [ ] **Step 4: Verify the asset is a feathered grayscale coverage**

Run:
```bash
python -c "import numpy as np; from PIL import Image; a=np.array(Image.open('web/public/map/island.png')); print(Image.open('web/public/map/island.png').mode, a.shape, 'land%%=', round((a>127).mean()*100,1), 'feathered=', int(((a>5)&(a<250)).sum()))"
```
Expected: `L (1775, 2200) land%= ~28 feathered= <large nonzero>` (intermediate-alpha pixels prove the AA feather; `~28%` land matches the island footprint).

- [ ] **Step 5: Commit**

```bash
git add scripts/map/sources/island_mask.png scripts/map/build_island_coverage.py web/public/map/island.png
git commit -m "feat(map): bake smoothed+feathered island coverage asset (SP2)"
```

---

### Task 3: `territory.js` — partition core

**Files:**
- Create: `web/src/lib/territory.js`
- Create: `web/src/lib/territory.test.js`

**Interfaces:**
- Produces:
  - `boxBlur(src: Float32Array, r: number, W: number, H: number) -> Float32Array`
  - `nearestOwner(W, H, centersPx: number[][], ownerOf: number[]) -> Int16Array` (owner index per pixel for the WHOLE frame; `centersPx[i]=[x,y]`, `ownerOf[i]=owner index`)
  - `gooeyPartition(near: Int16Array, land: Uint8Array, W, H, nOwners, radius) -> Int16Array` (`ownerSm`, `-1` off land, gap-free on land)
  - `borderDistance(ownerSm: Int16Array, W, H) -> Float32Array` (`dB`, 0 on region outline incl. coast)

- [ ] **Step 1: Write the failing test** — create `web/src/lib/territory.test.js`:

```js
import { describe, it, expect } from "vitest";
import { boxBlur, nearestOwner, gooeyPartition, borderDistance } from "./territory.js";

describe("boxBlur", () => {
  it("averages a 3x3 neighbourhood", () => {
    const src = Float32Array.from([0,0,0, 0,9,0, 0,0,0]);
    expect(boxBlur(src, 1, 3, 3)[4]).toBeCloseTo(1, 5);   // 9/9
  });
});

describe("nearestOwner", () => {
  it("assigns each pixel to the nearest seed's owner", () => {
    const near = nearestOwner(4, 1, [[0,0],[3,0]], [0,1]);
    expect(Array.from(near)).toEqual([0,0,1,1]);
  });
});

describe("gooeyPartition", () => {
  it("assigns every land pixel an owner (gap-free), -1 off land", () => {
    const near = Int16Array.from([0,0,1,1]);
    const land = Uint8Array.from([1,1,1,0]);
    const sm = gooeyPartition(near, land, 4, 1, 2, 0);
    expect(Array.from(sm)).toEqual([0,0,1,-1]);
    expect(Array.from(sm).filter((v,i)=>land[i]&&v<0).length).toBe(0);
  });
});

describe("borderDistance", () => {
  it("is 0 on the owner boundary and grows inward", () => {
    const W=9,H=9; const sm=new Int16Array(W*H);
    for (let y=0;y<H;y++) for (let x=0;x<W;x++) sm[y*W+x] = x<5?0:1;  // split at x=4|5
    const dB = borderDistance(sm, W, H);
    expect(dB[4*W+4]).toBe(0);          // last col of owner 0 = boundary
    expect(dB[4*W+2]).toBeGreaterThan(1); // interior pixel, away from both frame and seam
  });
});
```

- [ ] **Step 2: Run it; verify it fails**

Run: `npm --prefix web test -- territory`
Expected: FAIL — cannot import from `./territory.js`.

- [ ] **Step 3: Implement the core** — create `web/src/lib/territory.js`:

```js
// Pure territory partition + lens paint for the World Map (SP2). No DOM.
// Algorithm ported from the locked mockup lens-focus-v4.html.

const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);

export function boxBlur(src, r, W, H) {
  if (r < 1) return src.slice();
  const d = 2 * r + 1, tmp = new Float32Array(W * H), out = new Float32Array(W * H);
  for (let y = 0; y < H; y++) {
    const row = y * W; let acc = 0;
    for (let x = -r; x <= r; x++) acc += src[row + clamp(x, 0, W - 1)];
    for (let x = 0; x < W; x++) { tmp[row + x] = acc / d; acc += src[row + clamp(x + r + 1, 0, W - 1)] - src[row + clamp(x - r, 0, W - 1)]; }
  }
  for (let x = 0; x < W; x++) {
    let acc = 0;
    for (let y = -r; y <= r; y++) acc += tmp[clamp(y, 0, H - 1) * W + x];
    for (let y = 0; y < H; y++) { out[y * W + x] = acc / d; acc += tmp[clamp(y + r + 1, 0, H - 1) * W + x] - tmp[clamp(y - r, 0, H - 1) * W + x]; }
  }
  return out;
}

export function nearestOwner(W, H, centersPx, ownerOf) {
  const near = new Int16Array(W * H).fill(-1);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    let bi = -1, bd = 1e18;
    for (let i = 0; i < centersPx.length; i++) {
      const dx = centersPx[i][0] - x, dy = centersPx[i][1] - y, dd = dx * dx + dy * dy;
      if (dd < bd) { bd = dd; bi = i; }
    }
    near[y * W + x] = bi < 0 ? -1 : ownerOf[bi];
  }
  return near;
}

export function gooeyPartition(near, land, W, H, nOwners, radius) {
  const ownerSm = new Int16Array(W * H).fill(-1);
  const best = new Float32Array(W * H), mask = new Float32Array(W * H);
  for (let o = 0; o < nOwners; o++) {
    mask.fill(0);
    for (let p = 0; p < mask.length; p++) if (land[p] && near[p] === o) mask[p] = 1;
    const b = boxBlur(mask, radius, W, H);
    for (let p = 0; p < b.length; p++) { if (!land[p]) continue; if (b[p] > best[p]) { best[p] = b[p]; ownerSm[p] = o; } }
  }
  return ownerSm;
}

export function borderDistance(ownerSm, W, H) {
  const dB = new Float32Array(W * H).fill(1e9);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const p = y * W + x, o = ownerSm[p]; if (o < 0) continue;
    let edge = (x === 0 || x === W - 1 || y === 0 || y === H - 1);
    if (!edge) edge = ownerSm[p - 1] !== o || ownerSm[p + 1] !== o || ownerSm[p - W] !== o || ownerSm[p + W] !== o;
    if (edge) dB[p] = 0;
  }
  const O = 1, D = 1.41421;
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const p = y * W + x; if (ownerSm[p] < 0) continue; let m = dB[p];
    if (x > 0) m = Math.min(m, dB[p - 1] + O); if (y > 0) m = Math.min(m, dB[p - W] + O);
    if (x > 0 && y > 0) m = Math.min(m, dB[p - W - 1] + D); if (x < W - 1 && y > 0) m = Math.min(m, dB[p - W + 1] + D); dB[p] = m;
  }
  for (let y = H - 1; y >= 0; y--) for (let x = W - 1; x >= 0; x--) {
    const p = y * W + x; if (ownerSm[p] < 0) continue; let m = dB[p];
    if (x < W - 1) m = Math.min(m, dB[p + 1] + O); if (y < H - 1) m = Math.min(m, dB[p + W] + O);
    if (x < W - 1 && y < H - 1) m = Math.min(m, dB[p + W + 1] + D); if (x > 0 && y < H - 1) m = Math.min(m, dB[p + W - 1] + D); dB[p] = m;
  }
  return dB;
}
```

- [ ] **Step 4: Run the tests; verify they pass**

Run: `npm --prefix web test -- territory`
Expected: PASS (boxBlur, nearestOwner, gooeyPartition, borderDistance).

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/territory.js web/src/lib/territory.test.js
git commit -m "feat(web): territory.js partition core (nearest-owner, gooey, border SDF)"
```

---

### Task 4: `territory.js` — lens paint

**Files:**
- Modify: `web/src/lib/territory.js` (add `LENS`, `hexRgb`, `ownerLightOf`, `paintLens`)
- Modify: `web/src/lib/territory.test.js` (add tests)

**Interfaces:**
- Consumes: `ownerSm`, `dB` (Task 3); a single-channel `coastCov` (0..1) and `near` (Task 3).
- Produces:
  - `LENS` — the locked constants object.
  - `hexRgb(hex) -> [r,g,b]`
  - `paintLens({ ownerSm, dB, coastCov, near, W, H, ownerRgb, terr, px:{ rimW, halo, borderLean } }) -> Uint8ClampedArray` (RGBA, transparent ocean).

- [ ] **Step 1: Write the failing test** — append to `web/src/lib/territory.test.js`:

```js
import { LENS, hexRgb, paintLens } from "./territory.js";

describe("LENS constants", () => {
  it("are the locked values", () => {
    expect(LENS).toMatchObject({ DIM:0.40, tint:0.40, rimBright:0.74, rimWidthF:0.0020, haloF:0.0093, borderLeanF:0.0293, gooeyF:0.014 });
  });
});

describe("paintLens", () => {
  // 5x1 strip: pixels 0..3 land (owner 0), pixel 4 ocean.
  const W=5,H=1, terr=new Uint8ClampedArray(W*H*4).fill(200);
  const base = {
    W, H, terr, ownerRgb: [[0,128,255]],
    ownerSm: Int16Array.from([0,0,0,0,-1]),
    dB:      Float32Array.from([0,3,6,9,0]),     // pixel 0 = rim, deepens inward
    near:    Int16Array.from([0,0,0,0,0]),
    coastCov:Float32Array.from([1,1,1,1,0]),     // ocean (px4) fully transparent
    px: { rimW: 2.8, halo: 14, borderLean: 44 },
  };
  it("is transparent over ocean and opaque on the island", () => {
    const out = paintLens(base);
    expect(out[4*4+3]).toBe(0);     // ocean alpha
    expect(out[0*4+3]).toBe(255);   // land alpha
  });
  it("paints a brighter rim than the interior", () => {
    const out = paintLens(base);
    const rimLum = out[0]+out[1]+out[2];        // pixel 0 (dB=0)
    const inLum  = out[3*4]+out[3*4+1]+out[3*4+2]; // pixel 3 (deep)
    expect(rimLum).toBeGreaterThan(inLum);
  });
});
```

- [ ] **Step 2: Run it; verify it fails**

Run: `npm --prefix web test -- territory`
Expected: FAIL — `paintLens` / `LENS` not exported.

- [ ] **Step 3: Implement the paint** — append to `web/src/lib/territory.js`:

```js
const smooth = (e0, e1, x) => { const t = clamp((x - e0) / (e1 - e0), 0, 1); return t * t * (3 - 2 * t); };
const mix = (a, b, t) => [a[0] + (b[0]-a[0])*t, a[1] + (b[1]-a[1])*t, a[2] + (b[2]-a[2])*t];

export const LENS = { DIM:0.40, tint:0.40, rimBright:0.74, rimWidthF:0.0020, haloF:0.0093, borderLeanF:0.0293, gooeyF:0.014, lightF:0.55 };

export const hexRgb = (h) => { h = h.replace("#",""); return [parseInt(h.slice(0,2),16), parseInt(h.slice(2,4),16), parseInt(h.slice(4,6),16)]; };
export const ownerLightOf = (rgb) => mix(rgb, [255,255,255], LENS.lightF);

export function paintLens(o) {
  const { W, H, terr, ownerSm, dB, coastCov, near, ownerRgb, px } = o;
  const out = new Uint8ClampedArray(W * H * 4);
  const light = ownerRgb.map(ownerLightOf);
  for (let p = 0; p < W * H; p++) {
    const cov = coastCov[p]; if (cov <= 0.004) continue;
    let oi = ownerSm[p], dist;
    if (oi < 0) { oi = near[p]; if (oi < 0) continue; dist = 0; } else dist = dB[p];   // ocean feather borrows nearest owner at the rim
    const O = ownerRgb[oi], Ol = light[oi], q = p * 4;
    const Dd = [terr[q] * LENS.DIM, terr[q + 1] * LENS.DIM, terr[q + 2] * LENS.DIM];   // dimmed terrain (texture survives)
    const inward = smooth(0, px.borderLean, dist);
    const tint = clamp(LENS.tint * (0.55 + 0.9 * (1 - inward)), 0, 0.9);               // subtle inside, leans into the border
    let col = mix(Dd, O, tint);
    const core = smooth(px.rimW, 0, dist), halo = smooth(px.halo, px.rimW, dist);
    col = mix(col, Ol, clamp(core * LENS.rimBright + halo * 0.22, 0, 1));              // bright owner rim + soft halo
    out[q] = col[0]; out[q + 1] = col[1]; out[q + 2] = col[2]; out[q + 3] = cov * 255;
  }
  return out;
}
```

- [ ] **Step 4: Run the tests; verify they pass**

Run: `npm --prefix web test -- territory`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/territory.js web/src/lib/territory.test.js
git commit -m "feat(web): territory.js lens paint (dimmed terrain + tint + AA rim)"
```

---

### Task 5: `territory.js` — owner prep + `buildTerritory` orchestrator

**Files:**
- Modify: `web/src/lib/territory.js` (add `prepareOwners`, `buildTerritory`)
- Modify: `web/src/lib/territory.test.js` (add tests)

**Interfaces:**
- Consumes: all of Task 3/4; `manifest.courses` (each `{ slug, hit:{x,y,w,h} }`); `/v1/territory` rows (`{ slug, color }`).
- Produces:
  - `prepareOwners(manifestCourses, territoryRows) -> { centers:number[][], ownerOf:number[], ownerRgb:number[][] }` (normalized centres of CLAIMED courses only; `ownerRgb` = unique owner colours).
  - `buildTerritory({ coverage:Uint8Array(W*H), W, H, terr:Uint8ClampedArray(W*H*4), manifestCourses, territoryRows }) -> Uint8ClampedArray(W*H*4)` (the full territory RGBA layer).

- [ ] **Step 1: Write the failing test** — append to `web/src/lib/territory.test.js`:

```js
import { prepareOwners, buildTerritory } from "./territory.js";

const courses = [
  { slug:"a", hit:{x:0.1,y:0.5,w:0.0,h:0.0} },
  { slug:"b", hit:{x:0.9,y:0.5,w:0.0,h:0.0} },
  { slug:"c", hit:{x:0.5,y:0.5,w:0.0,h:0.0} },
];

describe("prepareOwners", () => {
  it("keeps claimed courses only, dedupes colours, computes centres", () => {
    const rows = [
      { slug:"a", color:"#ff0000" },
      { slug:"b", color:"#ff0000" },   // same colour -> same owner index
      { slug:"c", color:null },        // unclaimed -> dropped (not a seed)
    ];
    const r = prepareOwners(courses, rows);
    expect(r.ownerRgb).toEqual([[255,0,0]]);
    expect(r.ownerOf).toEqual([0,0]);
    expect(r.centers).toEqual([[0.1,0.5],[0.9,0.5]]);
  });
});

describe("buildTerritory", () => {
  it("paints owner colour on land, transparent off land", () => {
    const W=8,H=1, coverage=new Uint8Array(W*H).fill(255); coverage[7]=0;  // px7 ocean
    const terr=new Uint8ClampedArray(W*H*4).fill(180);
    const rows=[{slug:"a",color:"#ff0000"},{slug:"b",color:"#0000ff"},{slug:"c",color:null}];
    const rgba=buildTerritory({ coverage, W, H, terr, manifestCourses:courses, territoryRows:rows });
    expect(rgba[0*4+3]).toBe(255);   // land painted
    expect(rgba[7*4+3]).toBe(0);     // ocean transparent
    // left half leans red, right half leans blue (owner tint visible)
    expect(rgba[0*4]).toBeGreaterThan(rgba[0*4+2]);
    expect(rgba[6*4+2]).toBeGreaterThan(rgba[6*4]);
  });
  it("returns a fully transparent layer when nothing is claimed", () => {
    const W=4,H=1, coverage=new Uint8Array(W*H).fill(255), terr=new Uint8ClampedArray(W*H*4);
    const rgba=buildTerritory({ coverage, W, H, terr, manifestCourses:courses, territoryRows:[{slug:"a",color:null}] });
    expect(Array.from(rgba).every(v=>v===0)).toBe(true);
  });
});
```

- [ ] **Step 2: Run it; verify it fails**

Run: `npm --prefix web test -- territory`
Expected: FAIL — `prepareOwners` / `buildTerritory` not exported.

- [ ] **Step 3: Implement** — append to `web/src/lib/territory.js`:

```js
export function prepareOwners(manifestCourses, territoryRows) {
  const colorBySlug = Object.fromEntries(territoryRows.map((r) => [r.slug, r.color]));
  const idxOf = {}, ownerRgb = [], centers = [], ownerOf = [];
  for (const c of manifestCourses) {
    const color = colorBySlug[c.slug];
    if (!color) continue;                                  // unclaimed -> not a seed
    if (!(color in idxOf)) { idxOf[color] = ownerRgb.length; ownerRgb.push(hexRgb(color)); }
    centers.push([c.hit.x + c.hit.w / 2, c.hit.y + c.hit.h / 2]);
    ownerOf.push(idxOf[color]);
  }
  return { centers, ownerOf, ownerRgb };
}

export function buildTerritory({ coverage, W, H, terr, manifestCourses, territoryRows }) {
  const { centers, ownerOf, ownerRgb } = prepareOwners(manifestCourses, territoryRows);
  if (ownerRgb.length === 0) return new Uint8ClampedArray(W * H * 4);   // nobody claims anything
  const land = new Uint8Array(W * H), coastCov = new Float32Array(W * H);
  for (let p = 0; p < W * H; p++) { land[p] = coverage[p] > 127 ? 1 : 0; coastCov[p] = coverage[p] / 255; }
  const centersPx = centers.map((c) => [c[0] * W, c[1] * H]);
  const near = nearestOwner(W, H, centersPx, ownerOf);
  const ownerSm = gooeyPartition(near, land, W, H, ownerRgb.length, Math.round(LENS.gooeyF * W));
  const dB = borderDistance(ownerSm, W, H);
  return paintLens({ ownerSm, dB, coastCov, near, W, H, ownerRgb, terr,
    px: { rimW: LENS.rimWidthF * W, halo: LENS.haloF * W, borderLean: LENS.borderLeanF * W } });
}
```

- [ ] **Step 4: Run the tests; verify they pass**

Run: `npm --prefix web test -- territory`
Expected: PASS (all territory tests).

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/territory.js web/src/lib/territory.test.js
git commit -m "feat(web): buildTerritory orchestrator (manifest+owners -> RGBA layer)"
```

---

### Task 6: Worker + WorldMap wiring + render

**Files:**
- Create: `web/src/lib/territoryWorker.js`
- Modify: `web/src/lib/api.js` (add `territoryUrl`)
- Modify: `web/src/WorldMap.svelte` (fetch, worker, draw into `.territory` canvas)

**Interfaces:**
- Consumes: `buildTerritory` (Task 5); `GET /v1/territory` (Task 1); `web/public/map/island.png` (Task 2).
- Produces: the territory rendered into the on-page `.territory` canvas. (Glue — verified end-to-end in headless Edge, no unit test.)

- [ ] **Step 1: Add the API helper** — append to `web/src/lib/api.js`:

```js
export const territoryUrl = (cc = 150) => `${API_BASE}/v1/territory?cc=${cc}`;
```

- [ ] **Step 2: Write the worker** — create `web/src/lib/territoryWorker.js`:

```js
// Off-main-thread territory render. Reads the coverage + base bitmaps, builds the
// RGBA layer at full asset resolution, and ships an ImageBitmap back to the page.
import { buildTerritory } from "./territory.js";

function readRGBA(bitmap, W, H) {
  const c = new OffscreenCanvas(W, H), x = c.getContext("2d");
  x.drawImage(bitmap, 0, 0, W, H);
  return x.getImageData(0, 0, W, H).data;
}

self.onmessage = async (e) => {
  const { coverageBitmap, baseBitmap, W, H, manifestCourses, territoryRows } = e.data;
  const cd = readRGBA(coverageBitmap, W, H);
  const coverage = new Uint8Array(W * H);
  for (let p = 0; p < W * H; p++) coverage[p] = cd[p * 4];     // R channel = grayscale coverage
  const terr = readRGBA(baseBitmap, W, H);
  const rgba = buildTerritory({ coverage, W, H, terr, manifestCourses, territoryRows });
  const bitmap = await createImageBitmap(new ImageData(rgba, W, H));
  self.postMessage({ bitmap }, [bitmap]);
};
```

- [ ] **Step 3: Wire WorldMap.svelte** — three edits:

(a) In the `<script>`, add imports near the existing ones (top of `WorldMap.svelte`):

```js
  import { territoryUrl } from "./lib/api.js";
```

(b) Add state + the render function inside `<script>` (after the existing `let manifest = null;` line):

```js
  let terrCanvas;   // the .territory <canvas>

  async function renderTerritory() {
    if (!terrCanvas || !manifest) return;
    try {
      const [rows, cov, base] = await Promise.all([
        fetch(territoryUrl(150)).then((r) => r.json()),
        createImageBitmap(await (await fetch(`/map/island.png`)).blob()),
        createImageBitmap(await (await fetch(`/map/base.jpg`)).blob()),
      ]);
      const W = cov.width, H = cov.height;
      const worker = new Worker(new URL("./lib/territoryWorker.js", import.meta.url), { type: "module" });
      worker.onmessage = (e) => {
        const dw = 1100, dh = Math.round((dw * H) / W);
        terrCanvas.width = dw; terrCanvas.height = dh;
        const ctx = terrCanvas.getContext("2d");
        ctx.clearRect(0, 0, dw, dh);
        ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = "high";
        ctx.drawImage(e.data.bitmap, 0, 0, W, H, 0, 0, dw, dh);   // 2x -> display = AA
        worker.terminate();
      };
      worker.postMessage({ coverageBitmap: cov, baseBitmap: base, W, H,
        manifestCourses: manifest.courses, territoryRows: rows }, [cov, base]);
    } catch (e) { console.error("territory render failed", e); }
  }
```

(c) Call it once the manifest is loaded — at the end of the existing `onMount` (after `manifest = await r.json();` succeeds, before the `preloadPlayerGifs` line), add:

```js
      renderTerritory();
```

(d) In the markup, replace the placeholder `.territory` div (`<div class="territory" aria-hidden="true"></div>`) with a canvas:

```svelte
        <canvas class="territory" bind:this={terrCanvas} aria-hidden="true"></canvas>
```

(e) In `<style>`, change the base brightness to match the approved mockup and give the canvas a size — update the `.base` rule to `brightness(.82)` and the `.territory` rule:

```css
  .base { display: block; width: 100%; height: auto; filter: saturate(.82) brightness(.82); }
  .territory { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }
```

- [ ] **Step 4: svelte-check passes**

Run: `npm --prefix web run check`
Expected: 0 errors, 0 warnings.

- [ ] **Step 5: Verify the render in headless Edge**

Run (three shells): `npm --prefix pi run dev` · `npm --prefix web run dev` · then screenshot:
```bash
msedge --headless=new --disable-gpu --no-sandbox --user-data-dir="$(mktemp -d)" --disk-cache-size=1 --window-size=1300,1100 --virtual-time-budget=20000 --screenshot=temp/sp2_check.png "http://localhost:5173/#/map"
```
Open `temp/sp2_check.png` with the Read tool. Expected: territory regions painted on the island with bright anti-aliased rims, **ocean visible** around the island, the coast and inner borders both smooth (no jaggies), icons on top. (Live S1 is Gub-dominant teal + Paul purple.) **Use the browser screenshot as ground truth — never OpenCV.**

- [ ] **Step 6: Commit**

```bash
git add web/src/lib/territoryWorker.js web/src/lib/api.js web/src/WorldMap.svelte
git commit -m "feat(web): render SP2 territory layer in WorldMap (worker + AA downscale)"
```

---

## Self-Review

**Spec coverage:**
- Visual design (lens, gooey partition, AA coast, ocean, icons) → Tasks 3–6. ✓
- Locked constants → `LENS` in Task 4 + Global Constraints. ✓
- Rendering pipeline (2× → downscale, Worker) → Tasks 5–6. ✓
- Owner data + new public read → Task 1. ✓
- Island coverage asset + build script → Task 2. ✓
- Module list (territory.js, territoryWorker.js, WorldMap, api.js, reads) → Tasks 1, 3–6. ✓
- Tests (web territory.js, pi territoryOwners) → Tasks 1, 3–5. ✓
- Deferred strength / SP4 → noted, not built. ✓

**Placeholder scan:** none — every step has full code or an exact command + expected output.

**Type consistency:** `territoryOwners` row shape (`slug`, `color`) is consumed by `prepareOwners` (reads `r.slug`, `r.color`). `buildTerritory` opts (`coverage, W, H, terr, manifestCourses, territoryRows`) match the worker's call and the Task-5 tests. `paintLens` opts (`ownerSm, dB, coastCov, near, W, H, ownerRgb, terr, px:{rimW,halo,borderLean}`) match `buildTerritory`'s call and the Task-4 tests. `nearestOwner/gooeyPartition/borderDistance` signatures match between definition (Task 3) and use (Task 5). Worker message shape (`coverageBitmap, baseBitmap, W, H, manifestCourses, territoryRows`) matches WorldMap's `postMessage` and the worker's `onmessage`. ✓
