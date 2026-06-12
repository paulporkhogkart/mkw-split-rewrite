import { describe, it, expect, beforeEach } from "vitest";
import { interpolateAt, pushSample, sampleAt, clearBuffer, deltaTrendAt, EXTRAPOLATE_CAP_MS } from "./raceTimerBuffer.js";

const S = [
  { t: 1000, elapsed_ms: 0, completion: 0, pb_delta_ms: 0 },
  { t: 2000, elapsed_ms: 1000, completion: 0.5, pb_delta_ms: 100 },
  { t: 3000, elapsed_ms: 2000, completion: 1, pb_delta_ms: -300 },
];

describe("interpolateAt", () => {
  it("interpolates between bracketing samples (delta sweeps, not steps)", () => {
    expect(interpolateAt(S, 2500)).toEqual({ elapsed_ms: 1500, completion: 0.75, pb_delta_ms: -100 });
  });
  it("extrapolates elapsed_ms past the newest sample at 1ms/ms, holding completion + delta", () => {
    expect(interpolateAt(S, 3500)).toEqual({ elapsed_ms: 2500, completion: 1, pb_delta_ms: -300 });
  });
  it("caps extrapolation at EXTRAPOLATE_CAP_MS past the newest sample", () => {
    expect(interpolateAt(S, 3000 + EXTRAPOLATE_CAP_MS + 5000))
      .toEqual({ elapsed_ms: 2000 + EXTRAPOLATE_CAP_MS, completion: 1, pb_delta_ms: -300 });
  });
  it("does not invent elapsed_ms when the newest sample has none", () => {
    const idle = [{ t: 1000, elapsed_ms: null, completion: null, pb_delta_ms: null }];
    expect(interpolateAt(idle, 2000)).toEqual({ elapsed_ms: null, completion: null, pb_delta_ms: null });
  });
  it("clamps to the oldest sample before the window; null only when empty", () => {
    expect(interpolateAt(S, 500)).toEqual({ elapsed_ms: 0, completion: 0, pb_delta_ms: 0 });
    expect(interpolateAt([], 2500)).toBeNull();
  });
  it("pause-resume seam: a lone fresh sample serves a target trailing it", () => {
    // After a pause the buffer restarts from one sample; the card's target sits
    // DELAY_MS behind it for ~200ms. It must show the frozen values, not zeros.
    const lone = [{ t: 10000, elapsed_ms: 51234, completion: 0.4, pb_delta_ms: 800 }];
    expect(interpolateAt(lone, 9800)).toEqual({ elapsed_ms: 51234, completion: 0.4, pb_delta_ms: 800 });
  });
});

describe("deltaTrendAt", () => {
  beforeEach(() => { clearBuffer("t"); });
  const push = (t, d) => pushSample("t", { t, elapsed_ms: t, completion: 0.5, pb_delta_ms: d });

  it("reports gain when the delta falls and loss when it rises", () => {
    push(1000, 500); push(1500, 400); push(2000, 300);     // falling: catching the PB
    expect(deltaTrendAt("t", 2000)).toBe("gain");
    clearBuffer("t");
    push(1000, 300); push(1500, 450); push(2000, 600);     // rising: losing time
    expect(deltaTrendAt("t", 2000)).toBe("loss");
  });
  it("is null when steady (deadband) or without enough history", () => {
    push(1000, 500); push(1500, 505); push(2000, 498);     // wiggle within 15ms
    expect(deltaTrendAt("t", 2000)).toBeNull();
    clearBuffer("t");
    push(2000, 500);                                       // no past sample to compare
    expect(deltaTrendAt("t", 2000)).toBeNull();
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
