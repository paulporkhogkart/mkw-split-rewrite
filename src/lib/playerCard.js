// Pure mapping: a presence entry -> the player-card view model, plus formatters.
// No Svelte/Tauri imports, so it's unit-testable. See docs/superpowers/specs/2026-06-09-player-panel-design.md.
import { parseTime } from "./discordFormat.js";

const SETUP = { CHARACTER_SELECT: "Choosing character", KART_SELECT: "Choosing kart", COURSE_SELECT: "Choosing track" };

/** ms -> "m:ss.SSS" (always shows minutes), or null. */
export function fmtTimeMs(ms) {
  if (ms == null || Number.isNaN(ms)) return null;
  const m = Math.floor(ms / 60000), s = Math.floor((ms % 60000) / 1000), msec = ms % 1000;
  return `${m}:${String(s).padStart(2, "0")}.${String(msec).padStart(3, "0")}`;
}

/** elapsed ms since last seen -> coarse relative label, or null. */
export function lastSeen(deltaMs) {
  if (deltaMs == null) return null;
  const s = Math.floor(deltaMs / 1000);
  if (s < 60) return "just now";
  const m = Math.floor(s / 60); if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60); if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

/** Signed delta ms -> { text:"+0.432"|"-1.260", cls:"slow"|"fast" } - full ms
 *  precision, matching the m:ss.SSS timer next to it. */
function signedDelta(d) {
  const ahead = d < 0;
  return { text: `${ahead ? "-" : "+"}${(Math.abs(d) / 1000).toFixed(3)}`, cls: ahead ? "fast" : "slow" };
}

/** Live pace delta ms (server pb_delta_ms, signed) -> signedDelta or null. */
export function liveDelta(deltaMs) {
  return deltaMs == null ? null : signedDelta(deltaMs);
}

/** final time string vs PB ms -> signedDelta or null. */
export function pbDelta(finalStr, pbMs) {
  if (!finalStr || pbMs == null) return null;
  const f = parseTime(finalStr);
  return f == null ? null : signedDelta(f - pbMs);
}

/** A presence entry -> the card view model. `now` is a fn (Date.now) or a number
 *  for testability. `delayed` is the interpolated { elapsed_ms, completion } from
 *  the delay buffer (or null) - the racing timer + bar render from it so the
 *  display lags real time and lines up at the finish. */
export function viewModel(e, now = Date.now, delayed = null) {
  const t = typeof now === "function" ? now() : now;
  const color = e.color || "#888";
  if (!e.online) {
    const seen = e.updated_at > 0 ? lastSeen(t - e.updated_at) : null;
    return { state: "offline", name: e.name, color, online: false, char: null, kart: null, trk: null,
      primary: { kind: "seen", text: seen ? `last seen ${seen}` : "offline" },
      resets: null, pbStr: null, delta: null, bar: null };
  }
  const racing = e.screen === "RACING" && !e.final_time;
  const finished = (e.screen === "RACING" && e.final_time) || e.screen === "POST_TIME_TRIAL";
  let state, primary;
  if (SETUP[e.screen]) { state = "setup"; primary = { kind: "activity", text: SETUP[e.screen] }; }
  else if (racing) {
    state = "racing";
    const ms = delayed && delayed.elapsed_ms != null ? delayed.elapsed_ms : null;
    primary = { kind: "time", text: ms != null ? fmtTimeMs(Math.round(ms)) : "—" };
  }
  else if (finished) { state = "finished"; primary = { kind: "time", text: e.final_time }; }
  else { state = "menus"; primary = { kind: "activity", text: "In the menus" }; }
  const race = state === "racing" || state === "finished";
  const clamp01 = (x) => Math.max(0, Math.min(1, x));
  let fill = 0;
  if (state === "finished") fill = e.completion == null ? 1 : clamp01(e.completion);
  else if (state === "racing") fill = delayed && delayed.completion != null ? clamp01(delayed.completion) : 0;
  // No stored model for this course yet (first finished run builds it): show the
  // bar shell with evenly-spaced placeholder dividers and a "calibrating" label.
  // Real dividers sit at measured distance proportions, so these are stand-ins.
  const evenDividers = (n) =>
    Number.isInteger(n) && n >= 2 ? Array.from({ length: n - 1 }, (_, i) => (i + 1) / n) : [];
  let bar = null;
  if (race) {
    bar = e.has_model === false
      ? { fill: state === "finished" ? 1 : 0, dividers: evenDividers(e.tot_lap), calibrating: true }
      : { fill, dividers: Array.isArray(e.dividers) ? e.dividers : [] };
  }
  return {
    state, name: e.name, color, online: true,
    char: e.character || null, kart: e.kart || null, trk: e.course || null, primary,
    resets: race ? (e.resets ?? 0) : null,
    pbStr: race && e.pb_ms != null ? fmtTimeMs(e.pb_ms) : null,
    delta: state === "finished" ? pbDelta(e.final_time, e.pb_ms)
      : state === "racing" ? liveDelta(e.pb_delta_ms) : null,
    bar,
  };
}
