import { describe, it, expect } from "vitest";
import { isOnFire, fireBarPct, snuffLeadMs, E0, K } from "./fireModel.js";

describe("fireModel", () => {
  it("exposes the locked constants", () => {
    expect(E0).toBe(0.2); expect(K).toBe(4);
  });

  // real Season-1 / 150cc cases (t1 = leader, t2 = #2, wr = current WR), all ms
  it("lights a dominant, near-WR leader (Mario Bros. Circuit)", () => {
    expect(isOnFire({ t1: 110579, t2: 114914, wr: 107414 })).toBe(true);
  });
  it("lights a huge lead even mid-off-WR (Salty Salty Speedway)", () => {
    expect(isOnFire({ t1: 125337, t2: 131168, wr: 114534 })).toBe(true);
  });
  it("stays calm for a tiny lead near WR (Koopa Troopa Beach)", () => {
    expect(isOnFire({ t1: 90953, t2: 91025, wr: 86477 })).toBe(false);
  });
  it("stays calm for a big lead far off WR (Bowser's Castle)", () => {
    expect(isOnFire({ t1: 151846, t2: 155063, wr: 129887 })).toBe(false);
  });
  it("is false without a #2 or without a WR", () => {
    expect(isOnFire({ t1: 110579, t2: null, wr: 107414 })).toBe(false);
    expect(isOnFire({ t1: 110579, t2: 114914, wr: null })).toBe(false);
  });

  it("fireBarPct grows exponentially off the WR", () => {
    expect(fireBarPct(0)).toBeCloseTo(0.2, 6);          // floor = E0
    expect(fireBarPct(4)).toBeCloseTo(0.2 * Math.E, 6); // one K off
  });
  it("snuffLeadMs is the lead in ms a rival must beat to snuff (Mario Bros.)", () => {
    expect(snuffLeadMs({ t1: 110579, t2: 114914, wr: 107414 })).toBeGreaterThan(400);
    expect(snuffLeadMs({ t1: 110579, t2: 114914, wr: 107414 })).toBeLessThan(500);
  });
});
