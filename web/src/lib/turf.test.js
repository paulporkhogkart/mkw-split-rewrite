import { describe, it, expect } from "vitest";
import { courseCounts, turfStandings, cardConfig, digitJank } from "./turf.js";

const C = { Gub: "#38bdf8", Aliias: "#4ade80", Alex: "#fbbf24" };
const snap = (owners) => ({ owners });

describe("courseCounts", () => {
  it("tallies owned courses per player, ignoring unowned", () => {
    const s = snap({ mc: { player: "Gub" }, dk: { player: "Gub" }, bc: { player: "Aliias" } });
    expect(courseCounts(s)).toEqual({ Gub: 2, Aliias: 1 });
  });
  it("is empty for a null/blank snapshot", () => {
    expect(courseCounts(null)).toEqual({});
    expect(courseCounts({ owners: {} })).toEqual({});
  });
});

describe("turfStandings", () => {
  it("includes every roster player (0 courses too), sorted desc, rank + pct", () => {
    const s = snap({ mc: { player: "Gub" }, dk: { player: "Gub" }, bc: { player: "Aliias" } });
    const st = turfStandings(s, C, 30);
    expect(st.map((r) => r.player)).toEqual(["Gub", "Aliias", "Alex"]); // Alex 0 at the bottom
    expect(st.map((r) => r.courses)).toEqual([2, 1, 0]);
    expect(st.map((r) => r.rank)).toEqual([1, 2, 3]);
    expect(st[0].pct).toBe(Math.round((2 / 30) * 100)); // 7
    expect(st[2].pct).toBe(0);
    expect(st[0].color).toBe("#38bdf8");
  });
  it("breaks ties by player name ascending (stable)", () => {
    const s = snap({ mc: { player: "Gub" }, bc: { player: "Aliias" } }); // 1 each
    expect(turfStandings(s, C, 30).map((r) => r.player)).toEqual(["Aliias", "Gub", "Alex"]);
  });
});

describe("deterministic jank", () => {
  it("cardConfig is stable and cycles shapes 1..5", () => {
    expect(cardConfig("gub", 0)).toEqual(cardConfig("gub", 0));
    expect(cardConfig("x", 0).shape).toBe(1);
    expect(cardConfig("x", 5).shape).toBe(1);
    expect(cardConfig("x", 4).shape).toBe(5);
  });
  it("cardConfig gives aliias the edge push, others none", () => {
    expect(cardConfig("aliias", 1).fx).toBe(12);
    expect(cardConfig("gub", 0).fx).toBe(0);
  });
  it("digitJank is stable per index", () => {
    expect(digitJank(0)).toEqual(digitJank(0));
    expect(digitJank(0)).not.toEqual(digitJank(1));
  });
});
