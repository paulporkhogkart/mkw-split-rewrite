// Screenshot preferences, persisted in localStorage (client-side only — the Python
// engine never reads these). Same safeStorage + pure-loader pattern as feedSettings.js.
import { writable } from "svelte/store";

const KEYS = {
  keybind:   "screenshot_keybind",
  saveFile:  "screenshot_save_file",
  clipboard: "screenshot_clipboard",
  dir:       "screenshot_dir",
};
const DEFAULTS = { keybind: "F12", saveFile: true, clipboard: true, dir: "" };

function safeStorage() {
  try {
    if (typeof localStorage !== "undefined" && typeof localStorage.getItem === "function") return localStorage;
  } catch { /* accessing the experimental global can throw */ }
  return { getItem: () => null, setItem: () => {} };
}

const parseBool = (raw, fallback) => (raw === "true" ? true : raw === "false" ? false : fallback);

/** Load all screenshot prefs from a storage object (defaults when absent). */
export function loadScreenshotPrefs(store) {
  return {
    keybind:   store.getItem(KEYS.keybind)   || DEFAULTS.keybind,
    saveFile:  parseBool(store.getItem(KEYS.saveFile),  DEFAULTS.saveFile),
    clipboard: parseBool(store.getItem(KEYS.clipboard), DEFAULTS.clipboard),
    dir:       store.getItem(KEYS.dir)       || DEFAULTS.dir,
  };
}

const ls = safeStorage();
const initial = loadScreenshotPrefs(ls);

export const screenshotKeybind   = writable(initial.keybind);
export const screenshotSaveFile  = writable(initial.saveFile);
export const screenshotClipboard = writable(initial.clipboard);
export const screenshotDir       = writable(initial.dir);

screenshotKeybind.subscribe((v)   => ls.setItem(KEYS.keybind, v || ""));
screenshotSaveFile.subscribe((v)  => ls.setItem(KEYS.saveFile, v ? "true" : "false"));
screenshotClipboard.subscribe((v) => ls.setItem(KEYS.clipboard, v ? "true" : "false"));
screenshotDir.subscribe((v)       => ls.setItem(KEYS.dir, v || ""));
