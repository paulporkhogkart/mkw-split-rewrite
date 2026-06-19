import { describe, it, expect } from "vitest";
import { buildSnapshots } from "./timeline.js";

const C = { Aliias: "#4ade80", Gub: "#2dd4bf" };

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
    expect(s[1].owners.mc.color).toBe("#2dd4bf");
  });
});
