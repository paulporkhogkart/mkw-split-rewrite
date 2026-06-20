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
