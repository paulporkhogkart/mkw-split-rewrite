import type { LeaderRow } from '../db/reads';

export interface RankGain { place: number; rivalId: number; rivalName: string; rivalTimeMs: number }

export function rankGains(before: LeaderRow[], after: LeaderRow[], moverId: number): RankGain[] {
  const oldPlace = before.find(r => r.player_id === moverId)?.rank ?? before.length + 1;
  const newPlace = after.find(r => r.player_id === moverId)?.rank;
  if (newPlace == null || newPlace >= oldPlace) return [];
  const gains: RankGain[] = [];
  for (let place = oldPlace - 1; place >= newPlace; place--) {
    const rival = before[place - 1]; // 0-indexed: who held `place` before
    if (rival) gains.push({ place, rivalId: rival.player_id, rivalName: rival.display_name, rivalTimeMs: rival.total_time_ms });
  }
  return gains;
}
