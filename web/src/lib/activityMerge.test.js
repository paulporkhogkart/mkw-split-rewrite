import { describe, it, expect } from "vitest";
import { mergeActivity } from "./activityMerge.js";

const e = (id) => ({ id, ts: id, type: "pb", payload: {} });

describe("mergeActivity", () => {
  it("merges newest-first by id and dedups", () => {
    const out = mergeActivity([e(3), e(1)], [e(2), e(3)]);
    expect(out.map(x => x.id)).toEqual([3, 2, 1]);
  });
  it("caps to the most recent N", () => {
    const existing = [e(5), e(4), e(3)];
    const out = mergeActivity(existing, [e(6)], 3);
    expect(out.map(x => x.id)).toEqual([6, 5, 4]);
  });
});
