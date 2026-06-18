import { describe, it, expect } from "vitest";
import { boxBlur, nearestOwner, gooeyPartition, borderDistance, LENS, hexRgb, paintLens, prepareOwners, buildTerritory } from "./territory.js";

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

  it("seeds the coast: land touching off-land (-1) is 0, interior grows inward", () => {
    const W=5,H=5; const sm=new Int16Array(W*H).fill(-1);
    for (let y=1;y<=3;y++) for (let x=1;x<=3;x++) sm[y*W+x]=0;  // 3x3 land island of owner 0
    const dB = borderDistance(sm, W, H);
    expect(dB[1*W+1]).toBe(0);             // corner land pixel touches ocean -> coast
    expect(dB[2*W+2]).toBeGreaterThan(0);  // interior centre grows inward
  });
});

describe("LENS constants", () => {
  it("are the locked values", () => {
    expect(LENS).toMatchObject({ DIM:0.40, tint:0.40, rimBright:0.74, rimWidthF:0.0020, haloF:0.0093, borderLeanF:0.0293, gooeyF:0.014, lightF:0.55 });
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

  it("drops courses whose colour is malformed (treats as unclaimed)", () => {
    const rows = [
      { slug:"a", color:"#ff0000" },
      { slug:"b", color:"notacolor" },  // malformed -> dropped
      { slug:"c", color:"#abc" },       // 3-digit hex, unsupported -> dropped
    ];
    const r = prepareOwners(courses, rows);
    expect(r.ownerRgb).toEqual([[255,0,0]]);
    expect(r.ownerOf).toEqual([0]);
    expect(r.centers).toEqual([[0.1,0.5]]);
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
