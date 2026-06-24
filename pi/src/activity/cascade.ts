import type { LeaderRow } from '../db/reads';
import { rankGains } from '../turf/rank';
import { turfTransitions } from '../turf/transitions';
import type { ActivityInput } from './types';

export interface RunCascadeArgs {
  ts: number; seasonId: number; cc: number; courseId: number;
  moverId: number; moverName: string;
  before: LeaderRow[]; after: LeaderRow[];
  beforeWr: number | null; afterWr: number | null;
  prevPbMs: number | null;
}

export function buildRunCascade(a: RunCascadeArgs): ActivityInput[] {
  const out: ActivityInput[] = [];
  const mine = a.after.find(r => r.player_id === a.moverId);
  if (!mine) return out;
  const base = { ts: a.ts, season_id: a.seasonId, cc: a.cc, course_id: a.courseId };

  out.push({ ...base, type: 'pb', player_id: a.moverId,
    payload: { time_ms: mine.total_time_ms, time_str: mine.total_time_str,
               delta_ms: a.prevPbMs != null ? mine.total_time_ms - a.prevPbMs : null } });

  for (const g of rankGains(a.before, a.after, a.moverId))
    out.push({ ...base, type: 'rank', player_id: a.moverId,
      payload: { place: g.place, rival_id: g.rivalId, rival_name: g.rivalName,
                 rival_time_ms: g.rivalTimeMs, gap_ms: g.rivalTimeMs - mine.total_time_ms } });

  for (const t of turfTransitions({ board: a.before, wr: a.beforeWr }, { board: a.after, wr: a.afterWr })) {
    if (t.kind === 'claim') out.push({ ...base, type: 'turf_claim', player_id: t.leaderId, payload: { rival_id: t.rivalId } });
    else if (t.kind === 'fire') out.push({ ...base, type: 'turf_fire', player_id: t.leaderId, payload: {} });
    else out.push({ ...base, type: 'turf_waver', player_id: t.leaderId, payload: {} });
  }
  return out;
}
