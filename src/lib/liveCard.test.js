import { describe, it, expect } from "vitest";
import { JANK, digitSpans, zigzag, sessTags } from "./liveCard.js";

describe("digitSpans", () => {
  it("cycles the locked jank transforms with 0.07s wave steps", () => {
    const s = digitSpans("1:50.517");
    expect(s.length).toBe(8);
    expect(s[0]).toEqual({ ch: "1", tj: JANK[0], wd: 0 });
    expect(s[5].tj).toBe(JANK[0]);          // cycle of 5
    expect(s[3].wd).toBeCloseTo(0.21);
  });
});

describe("zigzag", () => {
  it("one segment per lap, gapped, inside the well", () => {
    const z = zigzag(3, 0, 128);
    expect(z.done.length + z.future.length + (z.current ? 1 : 0)).toBe(3);
    expect(z.done.length).toBe(0);
    expect(z.current).not.toBeNull();
    const xs = [...(z.current.d + z.future.join(" ")).matchAll(/([\d.]+),/g)].map((m) => +m[1]);
    expect(Math.min(...xs)).toBeGreaterThanOrEqual(2);
    expect(Math.max(...xs)).toBeLessThanOrEqual(126);
  });
  it("fill walks laps from done to future", () => {
    const z = zigzag(3, 2.5 / 3, 128);   // in lap 3, half done
    expect(z.done.length).toBe(2);
    expect(z.current.offset).toBeCloseTo(50, 0);
    expect(z.future.length).toBe(0);
  });
  it("finished = all done", () => {
    const z = zigzag(3, 1, 128);
    expect(z.done.length).toBe(3);
    expect(z.current).toBeNull();
  });
  it("degenerate lap counts fall back to one segment", () => {
    const z = zigzag(0, 0.4, 128);
    expect(z.done.length + (z.current ? 1 : 0) + z.future.length).toBe(1);
  });
});

describe("sessTags", () => {
  it("counts + m:ss elapsed", () => {
    expect(sessTags({ count: 15, label: null, sinceMs: 1_000_000 }, 1_000_000 + 84_000))
      .toEqual({ att: 15, racing: "1:24" });
    expect(sessTags(null, 5)).toEqual({ att: null, racing: null });
  });
});
