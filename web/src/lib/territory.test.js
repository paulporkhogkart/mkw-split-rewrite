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
