// Mapping: a presence entry -> the player-card view model, plus formatters.
// No Svelte/Tauri imports, so it's unit-testable. The only state is the per-player
// hold (the last race readout, kept on screen through pause/reset loaders).
// See docs/superpowers/specs/2026-06-09-player-panel-design.md.
import { parseTime } from "./discordFormat.js";

const SETUP = { CHARACTER_SELECT: "Choosing character", KART_SELECT: "Choosing kart", COURSE_SELECT: "Choosing track",
                START_TIME_TRIAL: "Starting time trial" };
const PAUSE_SCREENS = new Set(["RACE_MENU", "HOME"]);
const RESET_SCREENS = new Set(["RESET", "GHOST_RESET", "UNKNOWN_RESET"]);
// Screens that sit BETWEEN race contexts (pause menus, reset loaders, mid-race
// detection blips): the card keeps the last race readout instead of flashing
// "In the menus". A real menu or the next race drops it.
const HOLD_SCREENS = new Set(["RACE_MENU", "HOME", "RESET", "GHOST_RESET", "UNKNOWN_RESET", "UNKNOWN_RACE_ACTIVE"]);

const holds = new Map();   // player_id -> last race display payload
export function clearHolds() { holds.clear(); }

/** Character display name with the costume leading ("Burger Bud Toad"); a bare
 *  character ("Base" or no costume detected) is just the character. */
export function charName(character, costume) {
  if (!character) return null;
  return costume && costume !== "Base" ? `${costume} ${character}` : character;
}

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

/** Signed delta ms -> "+0.432"|"-1.260" - full ms precision, matching the
 *  m:ss.SSS timer next to it. */
const signedText = (d) => `${d < 0 ? "-" : "+"}${(Math.abs(d) / 1000).toFixed(3)}`;

/** Live pace delta ms (server pb_delta_ms, signed) + trend ("gain"|"loss"|null)
 *  -> { text, cls } in LiveSplit shades: ahead/behind from the sign, light shade
 *  when the trend opposes it (gaining-but-behind / losing-but-ahead), sharp shade
 *  otherwise (steady counts as sharp). */
export function liveDelta(deltaMs, trend) {
  if (deltaMs == null) return null;
  const cls = deltaMs < 0
    ? (trend === "loss" ? "ahead-loss" : "ahead-gain")
    : (trend === "gain" ? "behind-gain" : "behind-loss");
  return { text: signedText(deltaMs), cls };
}

/** Server lap_delta ({ lap, delta_ms, gained, gold }) -> { text, cls } per the
 *  LiveSplit conventions; gold (best-ever segment) overrides the shade. */
export function lapDeltaVm(ld) {
  if (!ld || ld.delta_ms == null) return null;
  const cls = ld.gold ? "gold"
    : ld.delta_ms < 0 ? (ld.gained ? "ahead-gain" : "ahead-loss")
    : (ld.gained ? "behind-gain" : "behind-loss");
  return { text: signedText(ld.delta_ms), cls };
}

/** final time string vs PB ms -> { text, cls } in the sharp LiveSplit shades
 *  (the finish is a settled ahead/behind - no trend), or null. */
export function pbDelta(finalStr, pbMs) {
  if (!finalStr || pbMs == null) return null;
  const f = parseTime(finalStr);
  if (f == null) return null;
  const d = f - pbMs;
  return { text: signedText(d), cls: d < 0 ? "ahead-gain" : "behind-loss" };
}

/** A presence entry -> the card view model. `now` is a fn (Date.now) or a number
 *  for testability. `delayed` is the interpolated { elapsed_ms, completion,
 *  pb_delta_ms } from the delay buffer (or null) - the racing timer, bar and
 *  pace delta all render from it so the three move on one display clock (the
 *  lerp sweeps the delta smoothly between 4Hz server values) and line up at
 *  the finish. `opts`: { deltaMode: "pace"|"laps", trend } - "laps" renders the
 *  server's per-lap LiveSplit delta (held between lap lines) instead. */
export function viewModel(e, now = Date.now, delayed = null, opts = {}) {
  const t = typeof now === "function" ? now() : now;
  const color = e.color || "#888";
  if (!e.online) {
    holds.delete(e.player_id);
    const seen = e.updated_at > 0 ? lastSeen(t - e.updated_at) : null;
    return { state: "offline", name: e.name, color, online: false, char: null, kart: null, trk: null,
      primary: { kind: "seen", text: seen ? `last seen ${seen}` : "offline" },
      resets: null, pbStr: null, delta: null, finPb: false, badge: null, bar: null,
      stats: e.off_stats ?? null };
  }
  const racing = e.screen === "RACING" && !e.final_time;
  const finished = (e.screen === "RACING" && e.final_time) || e.screen === "POST_TIME_TRIAL";
  const ident = { name: e.name, color, online: true,
    char: charName(e.character, e.costume), kart: e.kart || null, trk: e.course || null };

  if (racing || finished) {
    const state = racing ? "racing" : "finished";
    // No sample yet / countdown (race_cleared nulls the clock): a timer reads 0:00.000,
    // never a dash - the race clock IS zero until GO.
    const primary = racing
      ? { kind: "time", text: fmtTimeMs(Math.round(delayed && delayed.elapsed_ms != null ? delayed.elapsed_ms : 0)) }
      : { kind: "time", text: e.final_time };
    const clamp01 = (x) => Math.max(0, Math.min(1, x));
    const fill = finished ? (e.completion == null ? 1 : clamp01(e.completion))
      : (delayed && delayed.completion != null ? clamp01(delayed.completion) : 0);
    // No stored model for this course yet (first finished run builds it): show the
    // bar shell with evenly-spaced placeholder dividers and a "calibrating" label.
    // Real dividers sit at measured distance proportions, so these are stand-ins.
    const evenDividers = (n) =>
      Number.isInteger(n) && n >= 2 ? Array.from({ length: n - 1 }, (_, i) => (i + 1) / n) : [];
    const bar = e.has_model === false
      ? { fill: finished ? 1 : 0, dividers: evenDividers(e.tot_lap), calibrating: true }
      : { fill, dividers: Array.isArray(e.dividers) ? e.dividers : [] };
    const delta = finished ? pbDelta(e.final_time, e.pb_ms)
      : opts.deltaMode === "laps" ? lapDeltaVm(e.lap_delta)
      : liveDelta(delayed ? delayed.pb_delta_ms : null, opts.trend);
    const vm = {
      state, ...ident, primary,
      resets: e.resets ?? 0,
      pbStr: e.pb_ms != null ? fmtTimeMs(e.pb_ms) : null,
      delta,
      // Finished colour: green when the final beat the (pre-race) PB; a first-ever
      // finish (no PB to compare) is a PB by definition.
      finPb: finished && (delta == null || delta.cls.startsWith("ahead")),
      badge: finished ? "fin" : null,
      bar,
    };
    // Remember the readout: pause menus + reset loaders keep showing it.
    holds.set(e.player_id, { primary, delta, bar, pbStr: vm.pbStr, resets: vm.resets,
                             finPb: vm.finPb, finished });
    return vm;
  }

  // Between race contexts (pause / reset loaders / detection blips): replay the
  // held readout instead of flashing "In the menus". FIN stays on a finished
  // readout; the pause screens get the pause badge.
  const held = holds.get(e.player_id);
  if (held && HOLD_SCREENS.has(e.screen)) {
    return { state: held.finished ? "finished" : "held", ...ident,
      primary: held.primary, resets: held.resets, pbStr: held.pbStr, delta: held.delta,
      finPb: held.finPb,
      badge: held.finished ? "fin"
        : PAUSE_SCREENS.has(e.screen) ? "pause"
        : RESET_SCREENS.has(e.screen) ? "reset" : null,
      bar: held.bar };
  }

  holds.delete(e.player_id);   // a real menu: the race readout is over
  const primary = SETUP[e.screen]
    ? { kind: "activity", text: SETUP[e.screen] }
    : { kind: "activity", text: "In the menus" };
  return { state: SETUP[e.screen] ? "setup" : "menus", ...ident, primary,
    resets: null, pbStr: null, delta: null, finPb: false, badge: null, bar: null };
}
