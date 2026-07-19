import { describe, it, expect } from "vitest";
import { fmtBytes, packLabel, progressFrac } from "./chipsSettings.js";

describe("fmtBytes", () => {
  it("scales units", () => {
    expect(fmtBytes(0)).toBe("0 B");
    expect(fmtBytes(6_230_000_000)).toBe("6.23 GB");
    expect(fmtBytes(512 * 1024)).toBe("512 KB");
  });
});

describe("packLabel", () => {
  const base = { packComplete: false, packWanted: false, packPaused: false, updateAvailable: false };
  it("idle offer", () => expect(packLabel(base, null)).toBe("Download full pack (6.3 GB)"));
  it("downloading with progress", () =>
    expect(packLabel({ ...base, packWanted: true }, { done: 11, total: 51, state: "downloading" }))
      .toBe("Downloading · shard 12/51"));
  it("paused", () =>
    expect(packLabel({ ...base, packWanted: true, packPaused: true }, { done: 11, total: 51 }))
      .toBe("Paused · shard 12/51"));
  it("installed", () =>
    expect(packLabel({ ...base, packComplete: true, packTag: "chips-v1" }, null)).toBe("Installed (chips-v1)"));
  it("update available", () =>
    expect(packLabel({ ...base, packComplete: true, updateAvailable: true }, null))
      .toBe("Pack update available (6.3 GB)"));
  it("interrupted (runner dead, not paused) with progress", () =>
    expect(packLabel({ ...base, packWanted: true, running: false }, { done: 11, total: 51 }))
      .toBe("Interrupted · shard 12/51"));
  it("interrupted (runner dead, not paused) with no progress", () =>
    expect(packLabel({ ...base, packWanted: true, running: false }, null))
      .toBe("Interrupted"));
});

describe("progressFrac", () => {
  it("fractions and clamps", () => {
    expect(progressFrac({ done: 17, total: 51 })).toBeCloseTo(1 / 3);
    expect(progressFrac({ done: 0, total: 0 })).toBeNull();
  });
});
