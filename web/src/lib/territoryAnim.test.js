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
