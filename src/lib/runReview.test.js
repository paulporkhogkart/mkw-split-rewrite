import { describe, it, expect } from "vitest";
import { isValidTime, parseTimeMs, isValidInt, isValidCount, buildLaps, isPbTime, lapsComplete } from "./runReview.js";

describe("runReview validators", () => {
  it("validates M:SS.mmm time strings", () => {
    expect(isValidTime("1:50.123")).toBe(true);
    expect(isValidTime("0:36.400")).toBe(true);
    expect(isValidTime(" 1:50.123 ")).toBe(true);
    expect(isValidTime("1:50")).toBe(false);
    expect(isValidTime("")).toBe(false);
    expect(isValidTime(null)).toBe(false);
  });

  it("parses time to ms (lossless, mirrors server timeToMs)", () => {
    expect(parseTimeMs("1:50.123")).toBe(110123);
    expect(parseTimeMs("0:36.400")).toBe(36400);
    expect(parseTimeMs("nope")).toBe(null);
  });

  it("coins accept any integer incl. negative and zero", () => {
    expect(isValidInt("0")).toBe(true);
    expect(isValidInt("-1")).toBe(true);
    expect(isValidInt("12")).toBe(true);
    expect(isValidInt("")).toBe(false);
    expect(isValidInt("1.5")).toBe(false);
    expect(isValidInt("x")).toBe(false);
  });

  it("mushrooms accept non-negative integers only", () => {
    expect(isValidCount("0")).toBe(true);
    expect(isValidCount("3")).toBe(true);
    expect(isValidCount("-1")).toBe(false);
    expect(isValidCount("")).toBe(false);
  });

  it("isPbTime: any time beats no record; otherwise must be strictly faster", () => {
    expect(isPbTime(110000, null)).toBe(true);       // no prior PB on record
    expect(isPbTime(110000, undefined)).toBe(true);
    expect(isPbTime(109000, 110000)).toBe(true);     // faster
    expect(isPbTime(110000, 110000)).toBe(false);    // tie is not a PB
    expect(isPbTime(111000, 110000)).toBe(false);    // slower
    expect(isPbTime(null, 110000)).toBe(false);      // no time entered yet
  });

  it("lapsComplete is all-or-nothing across total_laps", () => {
    const full = [
      { lap: 1, time_str: "0:30.000", coins: 5, shrooms: 2 },
      { lap: 2, time_str: "0:30.000", coins: -1, shrooms: 0 },
    ];
    expect(lapsComplete(full, 2)).toBe(true);
    expect(lapsComplete(full, 3)).toBe(false);                 // a lap missing
    const gap = [
      { lap: 1, time_str: "0:30.000", coins: 5, shrooms: 2 },
      { lap: 2, time_str: "0:30.000", coins: null, shrooms: 0 },
    ];
    expect(lapsComplete(gap, 2)).toBe(false);                  // null field
    expect(lapsComplete([], 3)).toBe(false);
    expect(lapsComplete(undefined, 3)).toBe(false);
    expect(lapsComplete(full, 0)).toBe(false);
  });

  it("buildLaps derives time_ms and parses coins/shrooms", () => {
    const laps = [
      { lap: 1, time: "0:30.000", coins: "5", shrooms: "2" },
      { lap: 2, time: "0:31.500", coins: "-1", shrooms: "0" },
    ];
    expect(buildLaps(laps)).toEqual([
      { lap: 1, time_str: "0:30.000", time_ms: 30000, coins: 5, shrooms: 2 },
      { lap: 2, time_str: "0:31.500", time_ms: 31500, coins: -1, shrooms: 0 },
    ]);
  });
});
