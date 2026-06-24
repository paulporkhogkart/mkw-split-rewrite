import { describe, it, expect } from 'vitest';
import { rankGains } from './rank';
import type { LeaderRow } from '../db/reads';

const row = (id: number, rank: number): LeaderRow =>
  ({ player_id: id, display_name: `P${id}`, total_time_ms: 100000 + rank, total_time_str: null, rank });

describe('rankGains', () => {
  it('lists each place gained, first-gained first, rival = prior holder', () => {
    // before: 1st P9, 2nd P2(Aliias), 3rd P3(Luke), 4th P4(Alex), 5th P5(mover)
    const before = [row(9, 1), row(2, 2), row(3, 3), row(4, 4), row(5, 5)];
    // after: mover P5 -> 2nd; others shift down
    const after = [row(9, 1), row(5, 2), row(2, 3), row(3, 4), row(4, 5)];
    const g = rankGains(before, after, 5);
    expect(g.map(x => [x.place, x.rivalId])).toEqual([[4, 4], [3, 3], [2, 2]]);
  });
  it('returns [] when the mover did not climb', () => {
    const before = [row(1, 1), row(2, 2)];
    expect(rankGains(before, before, 2)).toEqual([]);
  });
});
