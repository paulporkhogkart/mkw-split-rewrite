import { describe, it, expect } from "vitest";
import { interpolateAt } from "./raceTimerBuffer.js";

const S = [
  { t: 1000, elapsed_ms: 0, completion: 0 },
  { t: 2000, elapsed_ms: 1000, completion: 0.5 },
  { t: 3000, elapsed_ms: 2000, completion: 1 },
];

describe("interpolateAt", () => {
  it("interpolates between bracketing samples", () => {
    expect(interpolateAt(S, 2500)).toEqual({ elapsed_ms: 1500, completion: 0.75 });
  });
  it("holds the newest sample past the end", () => {
    expect(interpolateAt(S, 9999)).toEqual({ elapsed_ms: 2000, completion: 1 });
  });
  it("is null before the oldest sample and for empty", () => {
    expect(interpolateAt(S, 500)).toBeNull();
    expect(interpolateAt([], 2500)).toBeNull();
  });
});
