import { describe, it, expect } from "vitest";
import { formatKeybind, matchesKeybind, prettyKeybind } from "./keybind.js";

const ev = (o) => ({ ctrlKey: false, altKey: false, shiftKey: false, metaKey: false, ...o });

describe("formatKeybind", () => {
  it("formats a plain function key", () => {
    expect(formatKeybind(ev({ key: "F12" }))).toBe("F12");
  });
  it("upper-cases single letters and orders modifiers Ctrl/Alt/Shift/Meta", () => {
    expect(formatKeybind(ev({ key: "s", ctrlKey: true, shiftKey: true }))).toBe("Ctrl+Shift+S");
    expect(formatKeybind(ev({ key: "a", ctrlKey: true, altKey: true, shiftKey: true, metaKey: true })))
      .toBe("Ctrl+Alt+Shift+Meta+A");
  });
  it("maps space to Space", () => {
    expect(formatKeybind(ev({ key: " " }))).toBe("Space");
  });
  it("returns null for a bare modifier key", () => {
    expect(formatKeybind(ev({ key: "Control", ctrlKey: true }))).toBeNull();
    expect(formatKeybind(ev({ key: "Shift", shiftKey: true }))).toBeNull();
  });
});

describe("matchesKeybind", () => {
  it("matches case-insensitively", () => {
    expect(matchesKeybind(ev({ key: "F12" }), "F12")).toBe(true);
    expect(matchesKeybind(ev({ key: "s", ctrlKey: true }), "ctrl+s")).toBe(true);
  });
  it("rejects mismatches, empty combos, and modifier-only events", () => {
    expect(matchesKeybind(ev({ key: "a" }), "F12")).toBe(false);
    expect(matchesKeybind(ev({ key: "a" }), "")).toBe(false);
    expect(matchesKeybind(ev({ key: "Control", ctrlKey: true }), "Ctrl")).toBe(false);
  });
});

describe("prettyKeybind", () => {
  it("shows the combo or empty string", () => {
    expect(prettyKeybind("Ctrl+Shift+S")).toBe("Ctrl+Shift+S");
    expect(prettyKeybind("")).toBe("");
  });
});
