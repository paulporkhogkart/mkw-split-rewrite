import { describe, it, expect } from "vitest";
import { parseVolume, parseBool, loadFeedPrefs } from "./feedSettings.js";

// A Map-backed fake of the localStorage subset we use (Node has no localStorage).
function fakeStorage(seed = {}) {
  const m = new Map(Object.entries(seed));
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)) };
}

describe("parseVolume", () => {
  it("passes valid values and clamps to [0,1]", () => {
    expect(parseVolume("0.5")).toBe(0.5);
    expect(parseVolume("0")).toBe(0);
    expect(parseVolume("1.5")).toBe(1);
    expect(parseVolume("-0.2")).toBe(0);
  });
  it("falls back on null / non-numeric", () => {
    expect(parseVolume(null)).toBe(0.5);
    expect(parseVolume("abc")).toBe(0.5);
    expect(parseVolume(null, 0.8)).toBe(0.8);
  });
});

describe("parseBool", () => {
  it("maps the persisted string form", () => {
    expect(parseBool("true")).toBe(true);
    expect(parseBool("false")).toBe(false);
  });
  it("falls back on anything else", () => {
    expect(parseBool(null)).toBe(false);
    expect(parseBool("1")).toBe(false);
    expect(parseBool(null, true)).toBe(true);
  });
});

describe("loadFeedPrefs", () => {
  it("returns defaults for empty storage", () => {
    expect(loadFeedPrefs(fakeStorage())).toEqual({
      feedVolume: 0.5, feedMuted: false, feedHidden: false, roiHidden: false,
    });
  });
  it("reads persisted values", () => {
    const store = fakeStorage({
      "mkw.feedVolume": "0.25", "mkw.feedMuted": "true",
      "mkw.feedHidden": "true", "mkw.roiHidden": "true",
    });
    expect(loadFeedPrefs(store)).toEqual({
      feedVolume: 0.25, feedMuted: true, feedHidden: true, roiHidden: true,
    });
  });
});
