import { C } from "./palette.js";

// Confidence/health → functional color. Mirrors the spec's scoreColor thresholds,
// with the idle-dim nuance preserved: negligible signal reads idle, not alarm-red.
export function scoreColor(v) {
  if (v < 0.005) return C.txDim;   // no/negligible signal — idle, not alarm-red
  if (v >= 0.8)  return C.ok;
  if (v >= 0.5)  return C.warn;
  return C.err;
}

// Backend Screen enum name → human-readable label (e.g. "RACING" → "Racing",
// "CHARACTER_SELECT" → "Character Select"). Leaves the em-dash placeholder as-is.
export function screenLabel(name) {
  if (!name || name === "—") return name;
  return name.toLowerCase().split(/[_\s]+/)
    .map(w => (w ? w[0].toUpperCase() + w.slice(1) : w)).join(" ");
}

// Confidence score → fixed 2-decimal string for the readout (e.g. 0.918 → "0.92").
export function fmtScore(v) {
  return (v == null || Number.isNaN(v)) ? "0.00" : Number(v).toFixed(2);
}

// Lap split → display string. Splits are stored as preformatted time strings;
// an unrun lap shows a dash placeholder.
export function fmtSplit(s) {
  return s == null || s === "" ? "– –" : s;
}
