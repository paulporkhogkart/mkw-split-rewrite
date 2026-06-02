// Pure helpers for Discord presence text. No Svelte/Tauri imports — unit-testable.

export function courseSlug(name) {
  if (!name) return null;
  // Strip apostrophes first ("Wario's" -> "warios"), then non-alphanumeric runs -> "_".
  return name.toLowerCase().replace(/['’]/g, "").replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
}

export function parseTime(str) {
  if (!str) return null;
  const m = /^(\d+):(\d{1,2})\.(\d{1,3})$/.exec(str);
  if (!m) return null;
  return (+m[1]) * 60000 + (+m[2]) * 1000 + (+m[3].padEnd(3, "0"));
}

function magnitude(absMs) {
  if (absMs >= 60000) {
    const m = Math.floor(absMs / 60000);
    const s = Math.floor((absMs % 60000) / 1000);
    const ms = absMs % 1000;
    return `${m}:${String(s).padStart(2, "0")}.${String(ms).padStart(3, "0")}`;
  }
  return (absMs / 1000).toFixed(3) + "s";
}

// deltaMs < 0 means faster than PB (ahead).
export function formatDelta(deltaMs) {
  const ahead = deltaMs < 0;
  return `${magnitude(Math.abs(Math.round(deltaMs)))} ${ahead ? "ahead of PB" : "behind PB"}`;
}
