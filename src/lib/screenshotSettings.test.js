import { describe, it, expect } from "vitest";
import { loadScreenshotPrefs } from "./screenshotSettings.js";

function fakeStorage(seed = {}) {
  const m = new Map(Object.entries(seed));
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)) };
}

describe("loadScreenshotPrefs", () => {
  it("returns defaults for empty storage", () => {
    expect(loadScreenshotPrefs(fakeStorage())).toEqual({
      keybind: "F12", saveFile: true, clipboard: true, dir: "",
    });
  });
  it("reads persisted values", () => {
    const store = fakeStorage({
      screenshot_keybind: "Ctrl+Shift+S", screenshot_save_file: "false",
      screenshot_clipboard: "false", screenshot_dir: "D:/shots",
    });
    expect(loadScreenshotPrefs(store)).toEqual({
      keybind: "Ctrl+Shift+S", saveFile: false, clipboard: false, dir: "D:/shots",
    });
  });
});
