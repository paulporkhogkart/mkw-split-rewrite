// Pure, unit-testable construction of THIS client's own presence entry from the local engine
// stores + the locally-cached PB. Used when the season server is unreachable so the user's own
// card + the race rail keep showing live, server-independent data. No Svelte/Tauri imports.
import { parseTime } from "./discordFormat.js";

/** Cumulative PB splits {lap: cumMs} -> per-lap durations [lap1, lap2, ...], or null. The
 *  pb-splits cache is cumulative ({1:36000,2:72000,...}); the rail + delta math want durations. */
export function pbLapDurations(pbCum) {
  if (!pbCum) return null;
  const laps = Object.keys(pbCum).map(Number).filter((n) => Number.isInteger(n) && n >= 1).sort((a, b) => a - b);
  if (!laps.length) return null;
  const out = [];
  let prev = 0;
  for (const n of laps) {
    const c = pbCum[n];
    if (c == null) break;
    out.push(c - prev);
    prev = c;
  }
  return out.length ? out : null;
}

/** Local LiveSplit lap info from live per-lap split strings ({lap: "m:ss.SSS"}) + the PB's
 *  cumulative splits, mirroring the server lapDelta — minus the `gold` best-ever-segment flag,
 *  which needs full run history the client lacks offline. Emits a delta row only for contiguous
 *  completed laps (both a live split and a PB lap present). Shape matches `me.lap_deltas`. */
export function localLapInfo(splitsObj, pbCum) {
  const pbLaps = pbLapDurations(pbCum);
  if (!pbLaps) return { pb_laps_ms: null, lap_deltas: null };
  const deltas = [];
  let live = 0, ref = 0;
  for (let i = 1; i <= pbLaps.length; i++) {
    const lv = parseTime(splitsObj?.[i]);
    const rf = pbLaps[i - 1];
    if (lv == null || rf == null) break;
    live += lv; ref += rf;
    deltas.push({ lap: i, delta_ms: live - ref, seg_delta_ms: lv - rf, gained: lv < rf, gold: false });
  }
  return { pb_laps_ms: pbLaps, lap_deltas: deltas };
}

/** Build a card/rail-renderable presence entry for THIS client from the local stores.
 *  `s`: { identity:{player_id,name,color}, screen, selection, race, minimap, resets,
 *  pbTotalMs, pbCum, now }. Tagged `_localSelf` so the panel renders it live (never stale);
 *  the progress bar (server model) is suppressed via has_model:false. */
export function buildSelfEntry(s) {
  const { identity, screen, selection, race, minimap, resets, pbTotalMs, pbCum, now } = s;
  const { pb_laps_ms, lap_deltas } = localLapInfo(race?.splits, pbCum);
  const pos = minimap && minimap.cx != null && minimap.cy != null ? [minimap.cx, minimap.cy] : null;
  return {
    player_id: identity.player_id, name: identity.name, color: identity.color,
    online: true, screen,
    course: selection?.course || null, character: selection?.char || null,
    kart: selection?.kart || null, costume: selection?.costume || null,
    cur_lap: race?.curLap ?? null, tot_lap: race?.totLap ?? null,
    coins: race?.coins ?? null, mushrooms: race?.mushrooms ?? null,
    pos, final_time: race?.finishTime ?? null, dnf: !!race?.dnf, resets: resets ?? null,
    invalidated: !!race?.invalidated, invalid_reason: race?.invalidReason ?? null,
    elapsed_ms: race?.elapsedMs ?? null,
    pb_ms: pbTotalMs ?? null, pb_laps_ms, lap_deltas,
    completion: null, dividers: [], has_model: false,
    pb_delta_ms: null, lap_delta: null, off_stats: null,
    updated_at: now, _localSelf: true,
  };
}
