import { describe, it, expect } from "vitest";
import { buildSnapshots, flippedCourses, leaderboardAt, wrAsOf } from "./timeline.js";

const C = { Aliias: "#4ade80", Gub: "#38bdf8" };

describe("buildSnapshots", () => {
  it("emits a snapshot only when a course leader changes; owner = running-min", () => {
    const events = [
      { t: 1000, player: "Aliias", slug: "mc", ms: 90000 }, // Aliias leads mc
      { t: 2000, player: "Aliias", slug: "mc", ms: 88000 }, // still Aliias (no leader change) -> no new snapshot
      { t: 3000, player: "Gub", slug: "mc", ms: 80000 }, // Gub takes mc
    ];
    const s = buildSnapshots(events, C);
    expect(s.map((x) => x.owners.mc.player)).toEqual(["Aliias", "Gub"]);
    expect(s.map((x) => x.t)).toEqual([1000, 3000]);
    expect(s[1].owners.mc.color).toBe("#38bdf8");
  });
});

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

describe("leaderboardAt", () => {
  it("takes each player's running-minimum up to t, sorted ascending", () => {
    const events = [
      { t: 1000, player: "Aliias", slug: "mc", ms: 90000 },
      { t: 2000, player: "Aliias", slug: "mc", ms: 88000 }, // improves own time
      { t: 3000, player: "Gub", slug: "mc", ms: 80000 },
      { t: 5000, player: "Aliias", slug: "mc", ms: 70000 }, // after the cutoff -> ignored
    ];
    expect(leaderboardAt(events, "mc", 3000)).toEqual([
      { player: "Gub", ms: 80000 },
      { player: "Aliias", ms: 88000 },
    ]);
  });

  it("ignores other courses and is empty for an unknown slug or no events", () => {
    const events = [{ t: 1000, player: "Gub", slug: "mc", ms: 80000 }];
    expect(leaderboardAt(events, "dk", 9999)).toEqual([]);
    expect(leaderboardAt([], "mc", 9999)).toEqual([]);
  });
});

describe("wrAsOf", () => {
  // achievedMs intentionally NOT ascending, to prove order-independence (min over <= t).
  const hist = { rr: [[100, 95000], [300, 99000], [200, 91000]] };

  it("returns the min record achieved by t (latest effective WR for clean data)", () => {
    expect(wrAsOf(hist, "rr", 250)).toBe(91000); // 95000@100 and 91000@200 are <= 250
  });
  it("ignores a stray slower-but-later row (min over entries <= t)", () => {
    expect(wrAsOf(hist, "rr", 350)).toBe(91000); // all three <= 350; 99000@300 ignored
  });
  it("returns null before the first achieved entry", () => {
    expect(wrAsOf(hist, "rr", 50)).toBe(null);
  });
  it("includes an entry achieved exactly at t", () => {
    expect(wrAsOf(hist, "rr", 100)).toBe(95000);
  });
  it("equals the current (best-ever) WR at t = Infinity", () => {
    expect(wrAsOf(hist, "rr", Infinity)).toBe(91000);
  });
  it("returns null for an unknown slug", () => {
    expect(wrAsOf(hist, "nope", Infinity)).toBe(null);
  });
});
