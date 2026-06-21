import { describe, it, expect } from "vitest";
import { onFireCourses, fireListAt } from "./onFire.js";

// leader 110579, #2 114914, wr 107414 -> clearly on fire (same numbers as the popup test).
// marginal: a 21ms lead over #2 off the same WR -> not on fire.
describe("onFireCourses", () => {
  it("keeps only entries whose lead clears the bar, preserving passthrough fields", () => {
    const entries = [
      { slug: "a", t1: 110579, t2: 114914, wr: 107414, hit: { x: 1 }, color: "#38bdf8" }, // on fire
      { slug: "b", t1: 110579, t2: 110600, wr: 107414, hit: { x: 2 }, color: "#a78bfa" }, // marginal
      { slug: "c", t1: 110579, t2: null,   wr: 107414, hit: { x: 3 }, color: "#fff" },     // no #2
      { slug: "d", t1: 110579, t2: 114914, wr: null,   hit: { x: 4 }, color: "#fff" },     // no WR
    ];
    const out = onFireCourses(entries);
    expect(out.map((e) => e.slug)).toEqual(["a"]);
    expect(out[0]).toMatchObject({ slug: "a", hit: { x: 1 }, color: "#38bdf8" });
  });

  it("is empty for empty input", () => {
    expect(onFireCourses([])).toEqual([]);
  });
});

describe("fireListAt", () => {
  const courses = [
    { slug: "mc", hit: { x: 0.1 } },
    { slug: "rr", hit: { x: 0.2 } }, // single competitor -> never on fire
  ];
  const events = [
    { t: 1000, player: "Gub",  slug: "mc", ms: 110579 },
    { t: 1000, player: "Paul", slug: "mc", ms: 114914 },
    { t: 1000, player: "Gub",  slug: "rr", ms: 90000 },
  ];
  const wrs = { mc: 107414, rr: 80000 };
  const colors = { Gub: "#38bdf8", Paul: "#a78bfa" };

  it("returns on-fire courses with leader colour + hit + the formula inputs", () => {
    const out = fireListAt({ courses, events, wrs, colors, t: Infinity });
    expect(out).toHaveLength(1);
    expect(out[0]).toMatchObject({ slug: "mc", color: "#38bdf8", t1: 110579, t2: 114914, wr: 107414, hit: { x: 0.1 } });
  });

  it("excludes a course whose runner-up does not exist yet at t", () => {
    const out = fireListAt({ courses, events, wrs, colors, t: 999 }); // before any event
    expect(out).toEqual([]);
  });
});
