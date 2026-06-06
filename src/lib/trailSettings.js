// Per-player ghost-trail playback settings + the pure helpers that turn a course's
// server reads into render-ready trails. Persisted in localStorage (like syncSettings).
import { writable } from "svelte/store";

// Preset palette — distinct + legible on the dark minimap.
export const TRAIL_PRESETS = ["#3d7cc2", "#d98a3e", "#5aa86a", "#cf5b4e", "#9b6bd0", "#46b0c8", "#d56aa8", "#c9b03e"];
export const TRAIL_MODES = ["none", "pbs", "best", "last", "all"];
const FADE_FLOOR = 0.2;

const SKEY = "mkw.trailSettings";
const RKEY = "mkw.roster";
const DEFAULTS = { fadeByRank: true, players: {} };   // players: { [playerId]: {mode, n, color} }

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

/** A player's effective config, with defaults: PBs, n=100, a preset colour by roster index. */
export function playerCfg(settings, playerId, idx) {
  const p = settings.players?.[playerId];
  return {
    mode:  p?.mode  ?? "pbs",
    n:     p?.n     ?? 100,
    color: p?.color ?? TRAIL_PRESETS[idx % TRAIL_PRESETS.length],
  };
}

/** The trail config to send to sync_course_reads: players with mode != none. */
export function activeConfig(settings, rosterList) {
  return (rosterList ?? [])
    .map((p, idx) => ({ player_id: p.player_id, ...playerCfg(settings, p.player_id, idx) }))
    .filter((c) => c.mode !== "none")
    .map((c) => ({ player_id: c.player_id, mode: c.mode, n: Math.max(1, Number(c.n) || 1) }));
}

/** Opacity for the run at rank `i` of a set of `count`, given the fade toggle. */
export function rankOpacity(i, count, fade) {
  if (!fade || count <= 1) return 1;
  return +(1 - (i / (count - 1)) * (1 - FADE_FLOOR)).toFixed(3);
}

/** Group the combined course-reads `trails` (each tagged player_id, rank-ordered within a
 *  player) into render-ready runs with the player's colour + per-run fade opacity.
 *  Returns [{points, color, opacity}]. */
export function buildTrailRuns(courseReads, settings, rosterList) {
  const idxById = new Map((rosterList ?? []).map((p, idx) => [p.player_id, idx]));
  const byPlayer = new Map();
  for (const t of (courseReads?.trails ?? [])) {
    if (!byPlayer.has(t.player_id)) byPlayer.set(t.player_id, []);
    byPlayer.get(t.player_id).push(t);
  }
  const out = [];
  for (const [pid, runs] of byPlayer) {
    const { color } = playerCfg(settings, pid, idxById.get(pid) ?? 0);
    runs.forEach((run, i) => {
      out.push({
        points: run.points ?? [], color,
        opacity: rankOpacity(i, runs.length, settings.fadeByRank),
        abandoned: run.status !== "finished",   // reset/dnf runs draw an X at their end
      });
    });
  }
  return out;
}

/** Legend rows (active players) for the overlay: {name, color, mode, n}. */
export function trailLegendRows(settings, rosterList) {
  return (rosterList ?? [])
    .map((p, idx) => ({ name: p.display_name, ...playerCfg(settings, p.player_id, idx) }))
    .filter((r) => r.mode !== "none");
}
