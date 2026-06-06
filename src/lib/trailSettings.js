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

function loadRoster() { try { return JSON.parse(localStorage.getItem(RKEY) || "[]"); } catch { return []; } }
export const roster = writable(loadRoster());
/** Cache the roster (from the server) so the settings list survives offline. */
export function cacheRoster(list) {
  roster.set(list);
  try { localStorage.setItem(RKEY, JSON.stringify(list)); } catch (_) { /* ignore */ }
}

/** A player's locked trail colour — deterministic by player_id, identical on every client. */
export function playerColor(playerId) {
  return TRAIL_PRESETS[((Number(playerId) % TRAIL_PRESETS.length) + TRAIL_PRESETS.length) % TRAIL_PRESETS.length];
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
 *  stay full opacity (and carry is_pb so the overlay can mark them); reset runs carry abandoned.
 *  Returns [{points, color, opacity, abandoned, is_pb}]. */
export function buildTrailRuns(courseReads, settings) {
  const byPlayer = new Map();
  for (const t of (courseReads?.trails ?? [])) {
    if (!byPlayer.has(t.player_id)) byPlayer.set(t.player_id, []);
    byPlayer.get(t.player_id).push(t);
  }
  const out = [];
  for (const [pid, runs] of byPlayer) {
    const color = playerColor(pid);
    runs.forEach((run, i) => {
      out.push({
        points: run.points ?? [],
        color,
        opacity: run.is_pb ? 1 : rankOpacity(i, runs.length, settings.fadeByRank),
        abandoned: run.status !== "finished",   // reset/dnf runs draw an X at their end
        is_pb: !!run.is_pb,                      // PB runs get a slight visual accent
      });
    });
  }
  return out;
}

/** Legend rows (active players) for the overlay: {name, color, mode, n}. */
export function trailLegendRows(settings, rosterList) {
  return (rosterList ?? [])
    .map((p) => ({ name: p.display_name, color: playerColor(p.player_id), ...playerCfg(settings, p) }))
    .filter((r) => r.mode !== "none");
}
