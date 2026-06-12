import { describe, it, expect, beforeEach } from "vitest";
import { interpolateAt, pushSample, sampleAt, clearBuffer, EXTRAPOLATE_CAP_MS } from "./raceTimerBuffer.js";

const S = [
  { t: 1000, elapsed_ms: 0, completion: 0 },
  { t: 2000, elapsed_ms: 1000, completion: 0.5 },
  { t: 3000, elapsed_ms: 2000, completion: 1 },
];

describe("interpolateAt", () => {
  it("interpolates between bracketing samples", () => {
    expect(interpolateAt(S, 2500)).toEqual({ elapsed_ms: 1500, completion: 0.75 });
  });
  it("extrapolates elapsed_ms past the newest sample at 1ms/ms, holding completion", () => {
    expect(interpolateAt(S, 3500)).toEqual({ elapsed_ms: 2500, completion: 1 });
  });
  it("caps extrapolation at EXTRAPOLATE_CAP_MS past the newest sample", () => {
    expect(interpolateAt(S, 3000 + EXTRAPOLATE_CAP_MS + 5000))
      .toEqual({ elapsed_ms: 2000 + EXTRAPOLATE_CAP_MS, completion: 1 });
  });
  it("does not invent elapsed_ms when the newest sample has none", () => {
    const idle = [{ t: 1000, elapsed_ms: null, completion: null }];
    expect(interpolateAt(idle, 2000)).toEqual({ elapsed_ms: null, completion: null });
  });
  it("is null before the oldest sample and for empty", () => {
    expect(interpolateAt(S, 500)).toBeNull();
    expect(interpolateAt([], 2500)).toBeNull();
  });
});

describe("sampleAt (monotonic display floor)", () => {
  beforeEach(() => { clearBuffer("p"); });

  it("never ticks backward when a late anchor lands behind the extrapolation", () => {
    pushSample("p", { t: 1000, elapsed_ms: 5000, completion: 0.5 });
    expect(sampleAt("p", 1300).elapsed_ms).toBe(5300);            // extrapolated
    pushSample("p", { t: 1310, elapsed_ms: 5250, completion: 0.5 }); // anchor ~60ms behind
    expect(sampleAt("p", 1320).elapsed_ms).toBe(5300);            // held, not 5260
    expect(sampleAt("p", 1400).elapsed_ms).toBe(5340);            // resumes once past the floor
  });

  it("accepts a big backward jump as a new race and resets the floor", () => {
    pushSample("p", { t: 1000, elapsed_ms: 90000, completion: 0.9 });
    expect(sampleAt("p", 1100).elapsed_ms).toBe(90100);
    pushSample("p", { t: 5000, elapsed_ms: 200, completion: 0 });
    expect(sampleAt("p", 5050).elapsed_ms).toBe(250);
  });

  it("passes null elapsed through without touching the floor", () => {
    pushSample("p", { t: 1000, elapsed_ms: 5000, completion: 0.5 });
    expect(sampleAt("p", 1200).elapsed_ms).toBe(5200);
    pushSample("p", { t: 1300, elapsed_ms: null, completion: null });
    expect(sampleAt("p", 1400).elapsed_ms).toBeNull();
    pushSample("p", { t: 1500, elapsed_ms: 5500, completion: 0.5 });
    expect(sampleAt("p", 1600).elapsed_ms).toBe(5600);            // floor didn't pin at 5200+
  });
});
