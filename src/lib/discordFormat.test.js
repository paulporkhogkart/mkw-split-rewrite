import { describe, it, expect } from "vitest";
import { courseSlug, parseTime, formatDelta } from "./discordFormat.js";

describe("courseSlug", () => {
  it("slugifies display names to image stems", () => {
    expect(courseSlug("Wario's Galleon")).toBe("warios_galleon");
    expect(courseSlug("Great ? Block Ruins")).toBe("great_block_ruins");
    expect(courseSlug("Mario Bros. Circuit")).toBe("mario_bros_circuit");
    expect(courseSlug("Sky-High Sundae")).toBe("sky_high_sundae");
    expect(courseSlug("Rainbow Road")).toBe("rainbow_road");
  });
  it("returns null for empty", () => expect(courseSlug(null)).toBe(null));
});

describe("parseTime", () => {
  it("parses m:ss.mmm to ms", () => {
    expect(parseTime("1:57.812")).toBe(117812);
    expect(parseTime("0:41.000")).toBe(41000);
  });
});

describe("formatDelta", () => {
  it("keeps 3 decimals, trailing zeros, ahead/behind", () => {
    expect(formatDelta(-420)).toBe("0.420s ahead of PB");
    expect(formatDelta(73)).toBe("0.073s behind PB");
    expect(formatDelta(-1500)).toBe("1.500s ahead of PB");
  });
  it("uses m:ss.mmm beyond a minute", () => {
    expect(formatDelta(-61234)).toBe("1:01.234 ahead of PB");
  });
});
