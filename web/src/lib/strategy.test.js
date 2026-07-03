import { describe, it, expect } from "vitest";
import { golfList, turfList, timeList } from "./strategy.js";

// Minimal PbRow shapes (only the fields the sorts read).
const row = (o) => ({
  slug: o.slug, leads: !!o.leads, your_ms: o.your_ms, wr_ms: o.wr_ms,
  next_rank_ms: o.next_rank_ms ?? null, leader_ms: o.leader_ms ?? null,
  leader_off_wr_pct: o.leader_off_wr_pct ?? null, off_wr_pct: o.off_wr_pct ?? null,
});

describe("strategy", () => {
  it("golfList ranks a same-ms gap easier when the PB sits further off WR", () => {
    const near = row({ slug: "near", your_ms: 101000, wr_ms: 100000, next_rank_ms: 100800, off_wr_pct: 1 }); // 200ms gap, ~1% off
    const far  = row({ slug: "far",  your_ms: 110000, wr_ms: 100000, next_rank_ms: 109800, off_wr_pct: 10 }); // 200ms gap, ~10% off
    const out = golfList([near, far]);
    expect(out.map((r) => r.slug)).toEqual(["far", "near"]); // far is easier
    expect(out[0].ease).toBeLessThan(out[1].ease);
  });

  it("golfList excludes courses you lead or that lack a rival above / a WR", () => {
    const lead = row({ slug: "lead", leads: true, your_ms: 90000, wr_ms: 80000, off_wr_pct: 12.5 });
    const nowr = row({ slug: "nowr", your_ms: 90000, wr_ms: null, next_rank_ms: 89000 });
    expect(golfList([lead, nowr])).toEqual([]);
  });

  it("turfList prefers a soft leader (further off WR) over a tight one at equal gap", () => {
    const soft  = row({ slug: "soft",  your_ms: 110000, wr_ms: 100000, leader_ms: 109000, leader_off_wr_pct: 9, off_wr_pct: 10 });
    const tight = row({ slug: "tight", your_ms: 110000, wr_ms: 100000, leader_ms: 109000, leader_off_wr_pct: 1, off_wr_pct: 10 });
    expect(turfList([soft, tight]).map((r) => r.slug)).toEqual(["soft", "tight"]);
  });

  it("timeList sorts your PBs by largest % off WR first and drops WR-less rows", () => {
    const a = row({ slug: "a", your_ms: 1, wr_ms: 1, off_wr_pct: 3 });
    const b = row({ slug: "b", your_ms: 1, wr_ms: 1, off_wr_pct: 8 });
    const c = row({ slug: "c", your_ms: 1, wr_ms: null, off_wr_pct: null });
    expect(timeList([a, b, c]).map((r) => r.slug)).toEqual(["b", "a"]);
  });
});
