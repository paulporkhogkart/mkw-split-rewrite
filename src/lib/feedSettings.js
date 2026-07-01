// Feed display preferences, persisted in localStorage (client-side only — the Python
// engine never reads these). Same pattern as syncSettings.js / trailSettings.js.
import { writable } from "svelte/store";

const KEYS = {
  feedVolume: "mkw.feedVolume",
  feedMuted:  "mkw.feedMuted",
  feedHidden: "mkw.feedHidden",
  roiHidden:  "mkw.roiHidden",
};
const DEFAULTS = { feedVolume: 0.5, feedMuted: false, feedHidden: false, roiHidden: false };

// localStorage is absent under Node (tests). Probe it; fall back to a no-op so the
// module imports cleanly either way.
function safeStorage() {
  try {
    if (typeof localStorage !== "undefined" && typeof localStorage.getItem === "function") return localStorage;
  } catch { /* accessing the experimental global can throw */ }
  return { getItem: () => null, setItem: () => {} };
}

/** Parse a 0..1 volume from storage, clamping and falling back to the default. */
export function parseVolume(raw, fallback = DEFAULTS.feedVolume) {
  const n = parseFloat(raw);
  if (!isFinite(n)) return fallback;
  return Math.max(0, Math.min(1, n));
}

/** Parse a persisted boolean ("true"/"false") with a fallback. */
export function parseBool(raw, fallback = false) {
  if (raw === "true") return true;
  if (raw === "false") return false;
  return fallback;
}

/** Load all four feed prefs from a storage object (defaults when absent/invalid). */
export function loadFeedPrefs(store) {
  return {
    feedVolume: parseVolume(store.getItem(KEYS.feedVolume)),
    feedMuted:  parseBool(store.getItem(KEYS.feedMuted),  DEFAULTS.feedMuted),
    feedHidden: parseBool(store.getItem(KEYS.feedHidden), DEFAULTS.feedHidden),
    roiHidden:  parseBool(store.getItem(KEYS.roiHidden),  DEFAULTS.roiHidden),
  };
}

const ls = safeStorage();
const initial = loadFeedPrefs(ls);

export const feedVolume = writable(initial.feedVolume);
export const feedMuted  = writable(initial.feedMuted);
export const feedHidden = writable(initial.feedHidden);
export const roiHidden  = writable(initial.roiHidden);

feedVolume.subscribe((v) => ls.setItem(KEYS.feedVolume, String(parseVolume(v))));
feedMuted.subscribe((v)  => ls.setItem(KEYS.feedMuted,  v ? "true" : "false"));
feedHidden.subscribe((v) => ls.setItem(KEYS.feedHidden, v ? "true" : "false"));
roiHidden.subscribe((v)  => ls.setItem(KEYS.roiHidden,  v ? "true" : "false"));
