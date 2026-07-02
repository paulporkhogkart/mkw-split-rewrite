// Pure keybind helpers: canonical string form so the recorder, storage, matcher,
// and display all agree. Takes plain keydown-like objects (testable without a DOM).

const MODIFIER_KEYS = new Set(["Control", "Alt", "Shift", "Meta"]);

function normKey(k) {
  if (k === " ") return "Space";
  return k.length === 1 ? k.toUpperCase() : k;
}

/** Canonical combo (e.g. "F12", "Ctrl+Shift+S"), or null if e.key is a bare modifier. */
export function formatKeybind(e) {
  if (MODIFIER_KEYS.has(e.key)) return null;
  const parts = [];
  if (e.ctrlKey) parts.push("Ctrl");
  if (e.altKey) parts.push("Alt");
  if (e.shiftKey) parts.push("Shift");
  if (e.metaKey) parts.push("Meta");
  parts.push(normKey(e.key));
  return parts.join("+");
}

/** True if the event's combo equals `combo` (case-insensitive). False for empty/null. */
export function matchesKeybind(e, combo) {
  if (!combo) return false;
  const f = formatKeybind(e);
  return f !== null && f.toLowerCase() === combo.toLowerCase();
}

/** Display form of a stored combo. Identity for now; centralizes future symbol mapping. */
export function prettyKeybind(combo) {
  return combo || "";
}
