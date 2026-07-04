import { describe, it, expect } from "vitest";
import { fireTargetMs } from "./fireTarget.js";
import { fireBarPct } from "./fireModel.js";

describe("fireTargetMs", () => {
  it("returns a target strictly between WR and the current leader, on the fire bar", () => {
    const wr = 100000, leaderMs = 110000;
    const { ms, reason } = fireTargetMs({ leaderMs, wr });
    expect(reason).toBe("ok");
    expect(ms).toBeGreaterThan(wr);
    expect(ms).toBeLessThan(leaderMs);
    // at the target, lead% ~= bar% (the crossing)
    const leadPct = ((leaderMs - ms) / wr) * 100;
    const barPct = fireBarPct(((ms - wr) / wr) * 100);
    expect(leadPct).toBeCloseTo(barPct, 2);
  });

  it("is null when there is no WR", () => {
    expect(fireTargetMs({ leaderMs: 110000, wr: null }).ms).toBeNull();
    expect(fireTargetMs({ leaderMs: null, wr: 100000 }).ms).toBeNull();
  });

  it("is wr-tight when even WR pace cannot clear the bar", () => {
    const r = fireTargetMs({ leaderMs: 100100, wr: 100000 }); // leader only 0.1% off WR (< E0=0.2)
    expect(r.ms).toBeNull();
    expect(r.reason).toBe("wr-tight");
  });
});
