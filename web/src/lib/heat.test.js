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
  { t: 1000, player: "Gub", slug: "mc", ms: 110579 },
  { t: 1000, player: "Paul", slug: "mc", ms: 114914 }, // 4.0% lead -> on fire
  { t: 1000, player: "Gub", slug: "pb", ms: 104887 },
  { t: 1000, player: "Paul", slug: "pb", ms: 104917 }, // 30ms lead -> marginal, not on fire
  { t: 1000, player: "Gub", slug: "rr", ms: 256426 },
  { t: 1000, player: "Gub", slug: "nw", ms: 100000 },
  { t: 1000, player: "Paul", slug: "nw", ms: 100500 },
];
// single-entry histories (achieved at t=0) so each resolves at every frame; nw absent on purpose
const wrHistory = { mc: [[0, 107414]], pb: [[0, 100139]], rr: [[0, 233693]] };
const colors = { Gub: "#2dd4bf", Paul: "#a78bfa" };

describe("heatRows", () => {
  it("emits one row per course with both a real #2 and a current WR", () => {
    const rows = heatRows({ courses, events, wrHistory, colors, t: Infinity });
    expect(rows.map((r) => r.slug).sort()).toEqual(["mc", "pb"]);
  });

  it("computes leader, colour, name, lead% and off% of WR", () => {
    const mc = heatRows({ courses, events, wrHistory, colors, t: Infinity }).find((r) => r.slug === "mc");
    expect(mc).toMatchObject({ name: "Mario Circuit", leader: "Gub", color: "#2dd4bf", t1: 110579, t2: 114914, wr: 107414 });
    expect(mc.leadPct).toBeCloseTo(4.0358, 3); // (114914-110579)/107414*100
    expect(mc.offPct).toBeCloseTo(2.9465, 3); // (110579-107414)/107414*100
    expect(mc.fire).toBe(true);
  });

  it("flags a marginal lead as not on fire under the locked model", () => {
    const pb = heatRows({ courses, events, wrHistory, colors, t: Infinity }).find((r) => r.slug === "pb");
    expect(pb.fire).toBe(false);
  });

  it("excludes courses before their runner-up exists at t", () => {
    expect(heatRows({ courses, events, wrHistory, colors, t: 999 })).toEqual([]);
  });
});

describe("heat <-> map parity (no-drift guarantee)", () => {
  it("the lit slug set from heatRows equals the map's fireListAt set", () => {
    const lit = heatRows({ courses, events, wrHistory, colors, t: Infinity }).filter((r) => r.fire).map((r) => r.slug).sort();
    const mapLit = fireListAt({ courses, events, wrHistory, colors, t: Infinity }).map((e) => e.slug).sort();
    expect(lit).toEqual(mapLit);
    expect(lit).toEqual(["mc"]);
  });
});
