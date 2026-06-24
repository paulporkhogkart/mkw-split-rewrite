// pi/src/activity/cascade.test.ts
import { describe, it, expect } from 'vitest';
import { buildRunCascade } from './cascade';
import type { LeaderRow } from '../db/reads';

const row = (id: number, ms: number, rank: number): LeaderRow =>
  ({ player_id: id, display_name: `P${id}`, total_time_ms: ms, total_time_str: null, rank });

describe('buildRunCascade', () => {
  it('PB takes #1: order is attempts, pb, rank(1st), turf_claim', () => {
    const before = [row(2, 108221, 1), row(1, 108600, 2)]; // P2 leads, mover P1 2nd
    const after = [row(1, 107980, 1), row(2, 108221, 2)];  // mover P1 -> 1st
    const out = buildRunCascade({
      ts: 1000, seasonId: 1, cc: 150, courseId: 1, moverId: 1, moverName: 'P1',
      before, after, beforeWr: null, afterWr: null, prevPbMs: 108410,
      attempts: { count: 12, durationMs: 240000 },
    });
    expect(out.map(e => e.type)).toEqual(['attempts', 'pb', 'rank', 'turf_claim']);
    expect(out[1].payload).toMatchObject({ time_ms: 107980, delta_ms: 107980 - 108410 });
    expect(out[2].payload).toMatchObject({ place: 1, rival_id: 2, gap_ms: 108221 - 107980 });
    expect(out[3]).toMatchObject({ type: 'turf_claim', player_id: 1, payload: { rival_id: 2 } });
  });

  it('PB that only climbs mid-board: pb + rank rows, no turf', () => {
    const before = [row(9, 100000, 1), row(2, 100500, 2), row(1, 100900, 3)];
    const after = [row(9, 100000, 1), row(1, 100400, 2), row(2, 100500, 3)];
    const out = buildRunCascade({
      ts: 1, seasonId: 1, cc: 150, courseId: 1, moverId: 1, moverName: 'P1',
      before, after, beforeWr: null, afterWr: null, prevPbMs: 100900, attempts: null,
    });
    expect(out.map(e => e.type)).toEqual(['pb', 'rank']);
    expect(out[1].payload).toMatchObject({ place: 2, rival_id: 2 });
  });
});
