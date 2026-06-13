import { describe, it, expect, beforeEach } from "vitest";
import { updateFire, clearFire, FIRE_ON_MS_PACE, FIRE_OFF_MS } from "./fireState.js";

beforeEach(() => clearFire());

const pace = (o) => updateFire(1, { mode: "pace", racing: true, ...o });
const laps = (o) => updateFire(1, { mode: "laps", racing: true, ...o });

describe("updateFire — pace mode (consistently under PB)", () => {
  it("stays off until ahead continuously for the on-window, then lights", () => {
    expect(pace({ ahead: true, now: 0 })).toBe(false);
    expect(pace({ ahead: true, now: FIRE_ON_MS_PACE - 1 })).toBe(false);
    expect(pace({ ahead: true, now: FIRE_ON_MS_PACE })).toBe(true);
  });

  it("a dip behind before lighting resets the on-timer", () => {
    expect(pace({ ahead: true, now: 0 })).toBe(false);
    expect(pace({ ahead: false, now: 1000 })).toBe(false);   // resets aheadSince
    expect(pace({ ahead: true, now: 1500 })).toBe(false);    // aheadSince = 1500
    expect(pace({ ahead: true, now: 1500 + FIRE_ON_MS_PACE - 1 })).toBe(false);
    expect(pace({ ahead: true, now: 1500 + FIRE_ON_MS_PACE })).toBe(true);
  });

  it("once lit, holds through a brief dip and only drops after the off-window", () => {
    pace({ ahead: true, now: 0 });
    expect(pace({ ahead: true, now: FIRE_ON_MS_PACE })).toBe(true);
    expect(pace({ ahead: false, now: FIRE_ON_MS_PACE + 1 })).toBe(true);          // behindSince set
    expect(pace({ ahead: false, now: FIRE_ON_MS_PACE + FIRE_OFF_MS })).toBe(true); // 1ms short
    expect(pace({ ahead: false, now: FIRE_ON_MS_PACE + 1 + FIRE_OFF_MS })).toBe(false);
  });

  it("a dip that recovers before the off-window never drops (no flicker)", () => {
    pace({ ahead: true, now: 0 });
    pace({ ahead: true, now: FIRE_ON_MS_PACE });
    expect(pace({ ahead: false, now: FIRE_ON_MS_PACE + 100 })).toBe(true);
    expect(pace({ ahead: true, now: FIRE_ON_MS_PACE + 200 })).toBe(true);   // recovered
    expect(pace({ ahead: true, now: FIRE_ON_MS_PACE + 5000 })).toBe(true);
  });
});

describe("updateFire — laps mode (last split under PB)", () => {
  it("lights immediately when the last completed split is ahead", () => {
    expect(laps({ ahead: true, now: 0 })).toBe(true);
  });

  it("drops after the off-window once the split goes behind", () => {
    laps({ ahead: true, now: 0 });
    expect(laps({ ahead: false, now: 0 })).toBe(true);             // off-timer starts here
    expect(laps({ ahead: false, now: FIRE_OFF_MS - 1 })).toBe(true);
    expect(laps({ ahead: false, now: FIRE_OFF_MS })).toBe(false);
  });
});

describe("updateFire — racing gate", () => {
  it("is off when not racing and clears the latch (must re-earn the window)", () => {
    pace({ ahead: true, now: 0 });
    expect(pace({ ahead: true, now: FIRE_ON_MS_PACE })).toBe(true);
    expect(updateFire(1, { mode: "pace", racing: false, ahead: true, now: 2100 })).toBe(false);
    expect(pace({ ahead: true, now: 2100 })).toBe(false);               // re-armed from scratch
    expect(pace({ ahead: true, now: 2100 + FIRE_ON_MS_PACE })).toBe(true);
  });

  it("never lights from a behind-only run", () => {
    expect(pace({ ahead: false, now: 0 })).toBe(false);
    expect(pace({ ahead: false, now: 10000 })).toBe(false);
  });

  it("keeps independent latches per player", () => {
    expect(updateFire(1, { mode: "laps", racing: true, ahead: true, now: 0 })).toBe(true);
    expect(updateFire(2, { mode: "laps", racing: true, ahead: false, now: 0 })).toBe(false);
  });
});
