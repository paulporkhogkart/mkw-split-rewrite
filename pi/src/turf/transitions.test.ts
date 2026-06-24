import { describe, it, expect } from 'vitest';
import { turfTransitions } from './transitions';
import type { LeaderRow } from '../db/reads';

const row = (id: number, ms: number): LeaderRow =>
  ({ player_id: id, display_name: `P${id}`, total_time_ms: ms, total_time_str: null, rank: 0 });

describe('turfTransitions', () => {
  it('emits a claim when #1 changes', () => {
    const before = { board: [row(2, 100200), row(1, 100400)], wr: 100000 };
    const after = { board: [row(1, 100100), row(2, 100200)], wr: 100000 };
    const t = turfTransitions(before, after);
    expect(t).toContainEqual({ kind: 'claim', leaderId: 1, rivalId: 2 });
  });
  it('emits fire when the new leader is on fire (claim then fire order)', () => {
    const before = { board: [row(2, 100500), row(1, 100600)], wr: 100000 };
    const after = { board: [row(1, 100100), row(2, 100500)], wr: 100000 }; // big lead, near WR
    const t = turfTransitions(before, after);
    expect(t[0]).toEqual({ kind: 'claim', leaderId: 1, rivalId: 2 });
    expect(t[1]).toEqual({ kind: 'fire', leaderId: 1 });
  });
  it('emits waver when the same leader loses fire (e.g. WR raised)', () => {
    const board = [row(1, 100100), row(2, 100400)];
    const before = { board, wr: 100050 }; // lead ~0.30% vs bar ~0.20% -> on fire
    const after = { board, wr: 98000 };    // faster WR -> bar ~0.34% > lead -> snuffed
    expect(turfTransitions(before, after)).toEqual([{ kind: 'waver', leaderId: 1 }]);
  });
  it('no transition when nothing changes', () => {
    const board = [row(1, 100100), row(2, 100400)];
    expect(turfTransitions({ board, wr: 100000 }, { board, wr: 100000 })).toEqual([]);
  });
});
