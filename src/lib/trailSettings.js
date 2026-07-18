// Per-player ghost-trail playback settings + the pure helpers that turn a course's
// server reads into render-ready trails. Persisted in localStorage (like syncSettings).
import { writable } from "svelte/store";

// Locked palette — a player's colour is fixed + the same on every client (deterministic
// by player_id), so "your dots" look the same to everyone. Not client-configurable.
export const TRAIL_PRESETS = ["#3d7cc2", "#d98a3e", "#5aa86a", "#cf5b4e", "#9b6bd0", "#46b0c8", "#d56aa8", "#c9b03e"];
export const TRAIL_MODES = ["none", "pbs", "best", "last", "last_pb", "all"];
const FADE_FLOOR = 0.2;

// The world record renders as one more (grey) player: its "PB" is the current WR, its
// ghosts are the historic ones. Colour locked like the player palette above.
export const WR_COLOR = "#a7adb5";
export const WR_MODES = ["off", "current", "all"];

const SKEY = "mkw.trailSettings";
const RKEY = "mkw.roster";
const DEFAULTS = { fadeByRank: false, players: {}, wr: { mode: "current" } };   // players: { [playerId]: {mode, n} }

function loadSettings() {
  try { return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(SKEY) || "{}") }; }
  catch { return { ...DEFAULTS }; }
}
export const trailSettings = writable(loadSettings());
trailSettings.subscribe((v) => { try { localStorage.setItem(SKEY, JSON.stringify(v)); } catch (_) { /* ignore */ } });

/** Reset every player's mode/count + the fade toggle back to defaults (PB + Last N, fade off). */
export function resetTrailSettings() {
  trailSettings.set({ ...DEFAULTS, players: {} });
}

function loadRoster() { try { return JSON.parse(localStorage.getItem(RKEY) || "[]"); } catch { return []; } }
export const roster = writable(loadRoster());
/** Cache the roster (from the server) so the settings list survives offline. */
export function cacheRoster(list) {
  roster.set(list);
  try { localStorage.setItem(RKEY, JSON.stringify(list)); } catch (_) { /* ignore */ }
}

// Fixed colour assignments for the competition roster (keyed by display name, lowercased).
// Locked + identical on every client; anyone not listed falls back to a stable per-id preset.
const PLAYER_COLORS = {
  paul:   "#9b6bd0",   // purple
  luke:   "#cf5b4e",   // red
  aliias: "#5aa86a",   // green
  gub:    "#d98a3e",   // orange
};

/** A player's locked trail colour: their assigned colour if listed, else a stable per-id
 *  preset. Identical on every client. Takes the roster player {player_id, display_name}. */
export function playerColor(p) {
  if (p?.color) return p.color;   // server-curated colour wins (set via the pi `set-color` script)
  const named = PLAYER_COLORS[(p?.display_name ?? "").trim().toLowerCase()];
  if (named) return named;
  const L = TRAIL_PRESETS.length;
  return TRAIL_PRESETS[(((Number(p?.player_id) || 0) % L) + L) % L];
}

/** A player's effective {mode, n}. Default "PB + Last N": you = last 49 + PB, others = last 24 + PB. */
export function playerCfg(settings, rosterPlayer) {
  const p = settings.players?.[rosterPlayer.player_id];
  return {
    mode: p?.mode ?? "last_pb",
    n:    p?.n    ?? (rosterPlayer.is_me ? 49 : 24),
  };
}

/** The WR pseudo-player's effective config. Default: the current WR only, on
 *  (spec 2026-07-18). Unknown stored values fall back to the default. */
export function wrCfg(settings) {
  const m = settings?.wr?.mode;
  return { mode: WR_MODES.includes(m) ? m : "current" };
}

/** The trail config to send to sync_course_reads: players with mode != none. */
export function activeConfig(settings, rosterList) {
  return (rosterList ?? [])
    .map((p) => ({ player_id: p.player_id, ...playerCfg(settings, p) }))
    .filter((c) => c.mode !== "none")
    .map((c) => ({ player_id: c.player_id, mode: c.mode, n: Math.max(1, Number(c.n) || 1) }));
}

/** Opacity for the run at rank `i` of a set of `count`, given the fade toggle. */
export function rankOpacity(i, count, fade) {
  if (!fade || count <= 1) return 1;
  return +(1 - (i / (count - 1)) * (1 - FADE_FLOOR)).toFixed(3);
}

/** Paint band (ascending = bottom to top). Two tiers - every alive run outranks every
 *  abandoned (X-ending) one - and within a tier the WR yields to players of its rank:
 *  historic WR < player past run < current WR < player PB (decided 2026-07-18). A
 *  run's tier is its static abandoned flag, so the paint order never reshuffles
 *  mid-race when a dot visually becomes its X. */
export function bandOf(run) {
  const rank = run.wr === "historic" ? 0 : run.wr === "current" ? 2 : run.is_pb ? 3 : 1;
  return (run.abandoned ? 0 : 4) + rank;
}

/** Group the combined course-reads `trails` (each tagged player_id, rank-ordered within a
 *  player) into render-ready runs with the player's locked colour + per-run opacity. PB runs
 *  stay full opacity (and carry is_pb so the overlay can pulse them); reset runs carry abandoned.
 *  Output is in global paint order (z-order, last = on top), intermingled across players by
 *  importance: every PB sits above every non-PB dot, fainter (more faded) runs sit lower, and
 *  faster runs sit higher - so the fastest PB tops the whole stack and no colour forms a layer.
 *  Returns [{points, color, opacity, abandoned, is_pb, total_ms}]. */
export function buildTrailRuns(courseReads, settings, rosterList) {
  const byId = new Map((rosterList ?? []).map((p) => [p.player_id, p]));
  const byPlayer = new Map();
  for (const t of (courseReads?.trails ?? [])) {
    if (!byPlayer.has(t.player_id)) byPlayer.set(t.player_id, []);
    byPlayer.get(t.player_id).push(t);
  }
  const out = [];
  for (const [pid, runs] of byPlayer) {
    const color = playerColor(byId.get(pid) ?? { player_id: pid });
    runs.forEach((run, i) => {
      out.push({
        points: run.points ?? [],
        color,
        opacity: run.is_pb ? 1 : rankOpacity(i, runs.length, settings.fadeByRank),
        abandoned: run.status !== "finished",   // reset/dnf runs draw an X at their end
        is_pb: !!run.is_pb,                      // PB run: pulsing accent + top of the z-stack
        total_ms: run.total_ms ?? null,          // for the importance sort below
        wr: null,                                // player runs are not WR
      });
    });
  }
  // The WR is one more (grey) player. Same opacity rules as everyone (rankOpacity over
  // the fastest-first rows; the fade toggle applies); a stored WR trail is by
  // construction a verified finished run, so abandoned is always false.
  const wrMode = wrCfg(settings).mode;
  if (wrMode !== "off") {
    const rows = (courseReads?.wr_trails ?? []).filter((w) => wrMode === "all" || w.is_current);
    rows.forEach((w, i) => {
      out.push({
        points: w.points ?? [],
        color: WR_COLOR,
        opacity: w.is_current ? 1 : rankOpacity(i, rows.length, settings.fadeByRank),
        abandoned: false,
        is_pb: !!w.is_current,                    // the current WR breathes like a PB
        total_ms: w.record_ms ?? null,
        wr: w.is_current ? "current" : "historic",
      });
    });
  }
  // Global paint order = z-order (last = on top): the two-tier band hierarchy (bandOf),
  // then the existing tiebreaks - fainter runs lower, faster runs higher.
  out.sort((a, b) => {
    if (bandOf(a) !== bandOf(b)) return bandOf(a) - bandOf(b);
    if (a.opacity !== b.opacity) return a.opacity - b.opacity;
    const at = a.total_ms ?? Infinity, bt = b.total_ms ?? Infinity;
    return at === bt ? 0 : bt - at;
  });
  return out;
}

/** Legend rows (active players) for the overlay: {name, color, mode, n}. */
export function trailLegendRows(settings, rosterList) {
  const rows = (rosterList ?? [])
    .map((p) => ({ name: p.display_name, color: playerColor(p), ...playerCfg(settings, p) }))
    .filter((r) => r.mode !== "none");
  const wr = wrCfg(settings);
  if (wr.mode !== "off") rows.push({ name: "WR", color: WR_COLOR, mode: wr.mode, n: 1 });
  return rows;
}
