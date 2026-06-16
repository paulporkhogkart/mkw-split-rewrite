// Per-player ghost-trail playback settings + the pure helpers that turn a course's
// server reads into render-ready trails. Persisted in localStorage (like syncSettings).
import { writable } from "svelte/store";

// Locked palette — a player's colour is fixed + the same on every client (deterministic
// by player_id), so "your dots" look the same to everyone. Not client-configurable.
export const TRAIL_PRESETS = ["#3d7cc2", "#d98a3e", "#5aa86a", "#cf5b4e", "#9b6bd0", "#46b0c8", "#d56aa8", "#c9b03e"];
export const TRAIL_MODES = ["none", "pbs", "best", "last", "last_pb", "all"];
const FADE_FLOOR = 0.2;

const SKEY = "mkw.trailSettings";
const RKEY = "mkw.roster";
const DEFAULTS = { fadeByRank: false, players: {} };   // players: { [playerId]: {mode, n} }

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
  alex:   "#3d7cc2",   // blue
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
      });
    });
  }
  // Global paint order = z-order (last = on top), intermingled across players by importance:
  //   1. every PB sits above every non-PB dot (no faded ghost ever covers a PB);
  //   2. within a band, fainter runs sit lower (the fade level is the priority);
  //   3. faster runs sit higher - so the fastest PB tops the whole stack.
  out.sort((a, b) => {
    if (a.is_pb !== b.is_pb) return a.is_pb ? 1 : -1;
    if (a.opacity !== b.opacity) return a.opacity - b.opacity;
    const at = a.total_ms ?? Infinity, bt = b.total_ms ?? Infinity;
    return at === bt ? 0 : bt - at;
  });
  return out;
}

/** Legend rows (active players) for the overlay: {name, color, mode, n}. */
export function trailLegendRows(settings, rosterList) {
  return (rosterList ?? [])
    .map((p) => ({ name: p.display_name, color: playerColor(p), ...playerCfg(settings, p) }))
    .filter((r) => r.mode !== "none");
}
