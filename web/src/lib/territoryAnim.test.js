import { describe, it, expect } from "vitest";
import { prepareTransition, interpolatePatch } from "./territoryAnim.js";
import { buildTerritory } from "./territory.js";

// 1-D map: 3 courses. At this width the gooey radius rounds to 0, so the partition is an
// exact Voronoi and endpoints are checkable. (The sliding gradient only shows at real
// scale where radius > 0; that is verified visually via CDP, not here.)
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
const allRed  = courses.map((c)=>({slug:c.slug,color:RED}));
const allBlue = courses.map((c)=>({slug:c.slug,color:BLUE}));

describe("prepareTransition", () => {
  it("returns null when nothing flips", () => {
    expect(prepareTransition({ coverage, terr, W, H, manifestCourses:courses, rowsA, rowsB:rowsA })).toBeNull();
  });
});

describe("interpolatePatch endpoints (all courses flip -> compute region == full frame)", () => {
  const prep = prepareTransition({ coverage, terr, W, H, manifestCourses:courses, rowsA:allRed, rowsB:allBlue });
  it("tau=0 equals buildTerritory(rowsA) exactly", () => {
    const p = interpolatePatch(prep, 0);
    expect(p.w).toBe(W);                                 // whole frame is the patch
    const want = buildTerritory({ coverage, W, H, terr, manifestCourses:courses, territoryRows:allRed });
    for (let i=0;i<want.length;i++) expect(Math.abs(p.rgba[i]-want[i])).toBeLessThanOrEqual(1);
  });
  it("tau=1 equals buildTerritory(rowsB) exactly", () => {
    const p = interpolatePatch(prep, 1);
    const want = buildTerritory({ coverage, W, H, terr, manifestCourses:courses, territoryRows:allBlue });
    for (let i=0;i<want.length;i++) expect(Math.abs(p.rgba[i]-want[i])).toBeLessThanOrEqual(1);
  });
});

describe("partial flip (b: RED->BLUE)", () => {
  const prep = prepareTransition({ coverage, terr, W, H, manifestCourses:courses, rowsA, rowsB });
  it("the patch covers b's cell (the only flipped course)", () => {
    const p = interpolatePatch(prep, 0.5);
    expect(p.x).toBeLessThanOrEqual(6); expect(p.x+p.w).toBeGreaterThan(6); // b centre = 0.5*12 = 6
  });
  it("the flipped cell moves RED->BLUE while unchanged cells stay put", () => {
    const p0 = interpolatePatch(prep, 0), p1 = interpolatePatch(prep, 1);
    const at = (p, gx) => { const i = (gx - p.x) * 4; return { r: p.rgba[i], b: p.rgba[i+2] }; };
    // b (x=6) flips: red-leaning at tau=0, blue-leaning at tau=1
    expect(at(p0,6).r).toBeGreaterThan(at(p0,6).b);
    expect(at(p1,6).b).toBeGreaterThan(at(p1,6).r);
    // a (x=2) stays red at both ends; c (x=10) stays blue at both ends (no flash on unchanged land)
    expect(at(p0,2).r).toBeGreaterThan(at(p0,2).b);
    expect(at(p1,2).r).toBeGreaterThan(at(p1,2).b);
    expect(at(p0,10).b).toBeGreaterThan(at(p0,10).r);
    expect(at(p1,10).b).toBeGreaterThan(at(p1,10).r);
  });

});

// Regression for the "square glow": over a small cropped window, borderDistance treats the window
// EDGE as a false border, whose tint-lean (borderLean, the widest paintLens falloff) bled past the
// old discard ring into the kept region. Needs a 2D grid at a real scale to manifest (H=1 is
// degenerate: every pixel is a frame edge).
describe("no window-edge artefact (2D)", () => {
  const N = 160;
  const cov = new Uint8Array(N * N).fill(255);
  const tr = new Uint8ClampedArray(N * N * 4).fill(160);
  const crs = [{ slug: "L", hit: { x: 0.25, y: 0.5, w: 0, h: 0 } }, { slug: "R", hit: { x: 0.75, y: 0.5, w: 0, h: 0 } }];
  const aAll = [{ slug: "L", color: "#ff0000" }, { slug: "R", color: "#ff0000" }];   // whole island one owner
  const bFlip = [{ slug: "L", color: "#ff0000" }, { slug: "R", color: "#0000ff" }];  // R flips to blue
  it("the kept patch equals the full-frame base at the endpoints", () => {
    const prep = prepareTransition({ coverage: cov, terr: tr, W: N, H: N, manifestCourses: crs, rowsA: aAll, rowsB: bFlip });
    for (const [tau, rows] of [[0, aAll], [1, bFlip]]) {
      const p = interpolatePatch(prep, tau);
      const full = buildTerritory({ coverage: cov, W: N, H: N, terr: tr, manifestCourses: crs, territoryRows: rows });
      let maxd = 0;
      for (let yy = 0; yy < p.h; yy++) for (let xx = 0; xx < p.w; xx++) {
        const s = ((p.y + yy) * N + (p.x + xx)) * 4, d = (yy * p.w + xx) * 4;
        for (let c = 0; c < 4; c++) maxd = Math.max(maxd, Math.abs(p.rgba[d + c] - full[s + c]));
      }
      expect(maxd, `tau=${tau}`).toBeLessThanOrEqual(1);
    }
  });
});

// The invasion front must sweep FROM the new owner's existing territory across the captured cell.
describe("invasion front direction", () => {
  const N = 120;
  const cov = new Uint8Array(N * N).fill(255);
  const tr = new Uint8ClampedArray(N * N * 4).fill(160);
  const crs = [
    { slug: "L", hit: { x: 0.15, y: 0.5, w: 0, h: 0 } },  // attacker (blue), left
    { slug: "M", hit: { x: 0.50, y: 0.5, w: 0, h: 0 } },  // captured (red -> blue), middle
    { slug: "R", hit: { x: 0.85, y: 0.5, w: 0, h: 0 } },  // defender (red), right
  ];
  const a = [{ slug: "L", color: "#0000ff" }, { slug: "M", color: "#ff0000" }, { slug: "R", color: "#ff0000" }];
  const b = [{ slug: "L", color: "#0000ff" }, { slug: "M", color: "#0000ff" }, { slug: "R", color: "#ff0000" }];
  it("mid-slide: the side nearest the attacker is converted, the far side not yet", () => {
    const prep = prepareTransition({ coverage: cov, terr: tr, W: N, H: N, manifestCourses: crs, rowsA: a, rowsB: b });
    const p = interpolatePatch(prep, 0.5);
    const at = (gx, gy) => { const i = ((gy - p.y) * p.w + (gx - p.x)) * 4; return { r: p.rgba[i], b: p.rgba[i + 2] }; };
    const y = Math.round(0.5 * N);
    const near = at(Math.round(0.42 * N), y);   // M-cell, near the attacker (left)
    const far = at(Math.round(0.58 * N), y);    // M-cell, near the defender (right)
    expect(near.b).toBeGreaterThan(near.r);     // already blue (front passed)
    expect(far.r).toBeGreaterThan(far.b);       // still red (front not arrived)
  });
});

// A run of adjoining captures by one owner (X, Y, Z all taken) must reveal as ONE continuous front
// sweeping near->far, not every cell animating simultaneously.
describe("adjoining run sweeps as one continuous front", () => {
  const NW = 400, NH = 80;
  const cov = new Uint8Array(NW * NH).fill(255), tr = new Uint8ClampedArray(NW * NH * 4).fill(160);
  const crs = [
    { slug: "A", hit: { x: 0.10, y: 0.5, w: 0, h: 0 } },  // attacker (blue)
    { slug: "X", hit: { x: 0.35, y: 0.5, w: 0, h: 0 } },
    { slug: "Y", hit: { x: 0.55, y: 0.5, w: 0, h: 0 } },
    { slug: "Z", hit: { x: 0.75, y: 0.5, w: 0, h: 0 } },
    { slug: "D", hit: { x: 0.95, y: 0.5, w: 0, h: 0 } },  // defender (red)
  ];
  const B = "#0000ff", R = "#ff0000";
  const aRows = crs.map((c) => ({ slug: c.slug, color: c.slug === "A" ? B : R }));
  const bRows = crs.map((c) => ({ slug: c.slug, color: c.slug === "D" ? R : B }));   // A,X,Y,Z blue; D red
  it("mid-sweep the near cell is taken but the far cell is not", () => {
    const prep = prepareTransition({ coverage: cov, terr: tr, W: NW, H: NH, manifestCourses: crs, rowsA: aRows, rowsB: bRows });
    const p = interpolatePatch(prep, 0.5);
    const isB = (gx, gy) => { const i = ((gy - p.y) * p.w + (gx - p.x)) * 4; return p.rgba[i + 2] > p.rgba[i]; };
    expect(isB(140, 40)).toBe(true);    // X (nearest the attacker) already taken
    expect(isB(300, 40)).toBe(false);   // Z (far end of the run) not yet -> one front sweeping through
  });
});

// A big window uses the precomputed-endpoint BLEND path; its endpoints must still equal the base.
describe("blend path (big window)", () => {
  const N = 650;
  const cov = new Uint8Array(N * N).fill(255), tr = new Uint8ClampedArray(N * N * 4).fill(160);
  const crs = [{ slug: "L", hit: { x: 0.3, y: 0.5, w: 0, h: 0 } }, { slug: "R", hit: { x: 0.7, y: 0.5, w: 0, h: 0 } }];
  const aAll = [{ slug: "L", color: "#ff0000" }, { slug: "R", color: "#ff0000" }];
  const bFlip = [{ slug: "L", color: "#ff0000" }, { slug: "R", color: "#0000ff" }];
  it("selects blend and the endpoints equal the full-frame base", () => {
    const prep = prepareTransition({ coverage: cov, terr: tr, W: N, H: N, manifestCourses: crs, rowsA: aAll, rowsB: bFlip });
    expect(prep.live).toBe(false);      // window > 130k -> blend
    for (const [t, rows] of [[0, aAll], [1, bFlip]]) {
      const p = interpolatePatch(prep, t);
      const full = buildTerritory({ coverage: cov, W: N, H: N, terr: tr, manifestCourses: crs, territoryRows: rows });
      let maxd = 0;
      for (let yy = 0; yy < p.h; yy++) for (let xx = 0; xx < p.w; xx++) { const s = ((p.y + yy) * N + (p.x + xx)) * 4, d = (yy * p.w + xx) * 4; for (let c = 0; c < 4; c++) maxd = Math.max(maxd, Math.abs(p.rgba[d + c] - full[s + c])); }
      expect(maxd, `t=${t}`).toBeLessThanOrEqual(1);
    }
  });
});

// The blend (big-window) glow must light ONLY the captured cells, like the live path. The reveal
// field carries mid/high values on far, NON-flipped neighbours; an unguarded glow there shimmers
// across land that never changes hands (the "shimmer from the opposite side" bug). Pixels identical
// at both endpoints (non-flipped, off the moving rim) must stay EXACTLY the base at every progress.
describe("blend glow does not leak onto non-flipped territory", () => {
  const N = 650;
  const cov = new Uint8Array(N * N).fill(255), tr = new Uint8ClampedArray(N * N * 4).fill(130);
  const crs = [{ slug: "L", hit: { x: 0.3, y: 0.5, w: 0, h: 0 } }, { slug: "R", hit: { x: 0.7, y: 0.5, w: 0, h: 0 } }];
  const aAll = [{ slug: "L", color: "#ff0000" }, { slug: "R", color: "#ff0000" }];
  const bFlip = [{ slug: "L", color: "#ff0000" }, { slug: "R", color: "#0000ff" }];
  it("stable non-flipped pixels never brighten across the sweep", () => {
    const prep = prepareTransition({ coverage: cov, terr: tr, W: N, H: N, manifestCourses: crs, rowsA: aAll, rowsB: bFlip });
    expect(prep.live).toBe(false);                          // big window -> blend path
    const P0 = interpolatePatch(prep, 0), P1 = interpolatePatch(prep, 1), w = P0.w;
    const stable = [];                                      // identical at both endpoints = non-flipped, off the rim
    for (let i = 0; i < w * P0.h; i++) { const d = i * 4;
      if (P0.rgba[d] === P1.rgba[d] && P0.rgba[d + 1] === P1.rgba[d + 1] && P0.rgba[d + 2] === P1.rgba[d + 2]) stable.push(d); }
    expect(stable.length).toBeGreaterThan(1000);            // non-vacuous: such pixels exist in the window
    let maxOver = 0;
    for (const tau of [0.2, 0.4, 0.6, 0.8, 0.9, 0.95]) { const P = interpolatePatch(prep, tau);
      for (const d of stable) maxOver = Math.max(maxOver, P.rgba[d] - P0.rgba[d], P.rgba[d + 1] - P0.rgba[d + 1], P.rgba[d + 2] - P0.rgba[d + 2]); }
    expect(maxOver).toBeLessThanOrEqual(1);                 // no glow brightening on land that never changes hands
  });
});

// A capture whose new owner has territory NEARBY (inside the patch padding) but NOT edge-adjacent
// must erupt radially from the course, not sweep in from the disconnected blob (the Dry Bones bug).
describe("non-adjacent capture erupts radially", () => {
  const NW = 400, NH = 80;
  const cov = new Uint8Array(NW * NH).fill(255);
  const tr = new Uint8ClampedArray(NW * NH * 4).fill(160);
  const crs = []; for (let i = 0; i < 12; i++) crs.push({ slug: "c" + i, hit: { x: (i + 1) * 0.075, y: 0.5, w: 0, h: 0 } });
  const R = "#ff0000", B = "#0000ff";
  const aRows = crs.map((c, i) => ({ slug: c.slug, color: i === 3 ? B : R }));            // c3 = the attacker's existing (non-adjacent) land
  const bRows = crs.map((c, i) => ({ slug: c.slug, color: (i === 3 || i === 5) ? B : R })); // c5 (between c4 and c6) is captured
  it("mid-slide the captured cell converts symmetrically (centre-out), not from one side", () => {
    const prep = prepareTransition({ coverage: cov, terr: tr, W: NW, H: NH, manifestCourses: crs, rowsA: aRows, rowsB: bRows });
    const p = interpolatePatch(prep, 0.7);
    const at = (gx, gy) => { const i = ((gy - p.y) * p.w + (gx - p.x)) * 4; return p.rgba[i + 2] > p.rgba[i]; }; // true = blue
    const y = 40;
    expect(at(180, y)).toBe(true);          // centre converted (radial reaches it first)
    expect(at(168, y)).toBe(at(192, y));    // both edges the same -> symmetric eruption, not a one-sided front
  });
});

// The new owner's land that is NOT edge-adjacent to the captured cell must not seed the front. A
// DISCONNECTED ob blob sitting inside the patch window (separated from the cell by enemy land) used
// to pull a SECOND front in from the far side (the user's "shimmer from the side we don't own"). The
// captured cell's reveal must depend only on the adjoining contact, so adding the far blob changes
// nothing. Collinear F .. E .. C .. R: E is a thin enemy wedge between far-ob F and captured C.
describe("far non-adjoining land of the new owner does not seed a second front", () => {
  const W = 600, H = 200;
  const cov = new Uint8Array(W * H).fill(255), tr = new Uint8ClampedArray(W * H * 4).fill(150);
  const crs = [
    { slug: "F", hit: { x: 290 / W, y: 0.5, w: 0, h: 0 } },   // far ob blob (left)
    { slug: "E", hit: { x: 300 / W, y: 0.5, w: 0, h: 0 } },   // thin enemy wedge
    { slug: "C", hit: { x: 310 / W, y: 0.5, w: 0, h: 0 } },   // captured
    { slug: "R", hit: { x: 410 / W, y: 0.5, w: 0, h: 0 } },   // adjoining ob (right)
  ];
  const RED = "#ff0000", BLUE = "#0000ff";
  const aF = [{ slug: "F", color: BLUE }, { slug: "E", color: RED }, { slug: "C", color: RED }, { slug: "R", color: BLUE }];
  const bF = [{ slug: "F", color: BLUE }, { slug: "E", color: RED }, { slug: "C", color: BLUE }, { slug: "R", color: BLUE }];
  const aNo = [{ slug: "F", color: RED }, { slug: "E", color: RED }, { slug: "C", color: RED }, { slug: "R", color: BLUE }];   // no far blob
  const bNo = [{ slug: "F", color: RED }, { slug: "E", color: RED }, { slug: "C", color: BLUE }, { slug: "R", color: BLUE }];
  it("the captured cell's reveal is one-sided and unchanged by the far blob", () => {
    const withF = prepareTransition({ coverage: cov, terr: tr, W, H, manifestCourses: crs, rowsA: aF, rowsB: bF });
    const noF = prepareTransition({ coverage: cov, terr: tr, W, H, manifestCourses: crs, rowsA: aNo, rowsB: bNo });
    const gw = withF.gw, gh = withF.gh;
    let ob = -1, minx = gw, maxx = -1;
    for (let p = 0; p < gw * gh; p++) if (withF.oaField[p] !== withF.obField[p]) { ob = withF.obField[p]; const x = p % gw; if (x < minx) minx = x; if (x > maxx) maxx = x; }
    const mid = (minx + maxx) / 2;
    let farPx = 0, lSum = 0, lN = 0, rSum = 0, rN = 0;
    for (let p = 0; p < gw * gh; p++) {
      const x = p % gw, flip = withF.oaField[p] !== withF.obField[p];
      if (!flip && withF.oaField[p] === ob && x < minx) farPx++;                       // our land LEFT of the cell = the far blob
      if (flip && withF.reveal[p] <= 1.001) { if (x < mid) { lSum += withF.reveal[p]; lN++; } else { rSum += withF.reveal[p]; rN++; } }
    }
    expect(farPx).toBeGreaterThan(300);                  // non-vacuous: the far blob really is in the window
    expect(lN).toBeGreaterThan(50); expect(rN).toBeGreaterThan(50);
    expect(rSum / rN).toBeLessThan(lSum / lN);            // front from the RIGHT (the adjoining side) reveals first
    let maxDiff = 0;
    for (let p = 0; p < gw * gh; p++) if (withF.oaField[p] !== withF.obField[p]) maxDiff = Math.max(maxDiff, Math.abs(withF.reveal[p] - noF.reveal[p]));
    expect(maxDiff).toBeLessThanOrEqual(0.01);            // the far blob does not change the captured cell's reveal at all
  });
});
