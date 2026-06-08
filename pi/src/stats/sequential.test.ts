import { describe, it, expect } from 'vitest';
import { DatabaseSync } from 'node:sqlite';
import { applySchema } from '../db/connect';
import { resolveSequential } from './sequential';
import { resolvePeriod } from './period';

const allTime = () => resolvePeriod('all_time', 'Australia/Melbourne');

function base(): DatabaseSync {
  const d = new DatabaseSync(':memory:');
  applySchema(d);
  d.exec(`INSERT INTO seasons(id,name,is_active) VALUES(1,'S1',1);
          INSERT INTO players(id,display_name) VALUES(1,'Luke');
          INSERT INTO courses(id,slug,display_name) VALUES(1,'bc','BC');`);
  return d;
}

/** Insert a time-ordered sequence of [status, was_pb] runs for (Luke, bc, 150). */
function seed(d: DatabaseSync, seq: [string, number][]) {
  seq.forEach(([status, wasPb], i) => {
    const n = i + 1;
    d.prepare(`INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,ended_at,was_pb,total_time_ms)
               VALUES(?,1,1,1,150,?,'live',?,?,?)`)
      .run(n, status, `2026-06-${String(n).padStart(2, '0')}T00:00:00+00:00`, wasPb, status === 'finished' ? 100000 : null);
  });
}

const val = (d: DatabaseSync, metric: string) =>
  resolveSequential(d, { metric, period: allTime(), filters: { player: 'Luke', course: 'bc' }, seasonId: 1 }).total;

describe('resolveSequential', () => {
  it('computes the worked example (reset, PB, reset, reset, PB, reset)', () => {
    const d = base();
    seed(d, [['reset', 0], ['finished', 1], ['reset', 0], ['reset', 0], ['finished', 1], ['reset', 0]]);
    expect(val(d, 'resets_since_pb')).toBe(1);
    expect(val(d, 'avg_resets_until_pb')).toBe(1.5);
    expect(val(d, 'current_reset_streak')).toBe(1);
  });

  it('resets_since_pb counts all resets when there is no PB', () => {
    const d = base();
    seed(d, [['reset', 0], ['reset', 0], ['finished', 0]]); // a finish, but not a PB
    expect(val(d, 'resets_since_pb')).toBe(2);
  });

  it('avg_resets_until_pb is null with no PBs', () => {
    const d = base();
    seed(d, [['reset', 0], ['reset', 0]]);
    expect(val(d, 'avg_resets_until_pb')).toBeNull();
  });

  it('current_reset_streak is 0 when the last run finished', () => {
    const d = base();
    seed(d, [['reset', 0], ['finished', 1]]);
    expect(val(d, 'current_reset_streak')).toBe(0);
  });

  it('value throws without player + course', () => {
    const d = base();
    seed(d, [['reset', 0]]);
    expect(() => resolveSequential(d, { metric: 'resets_since_pb', period: allTime(), filters: { player: 'Luke' }, seasonId: 1 })).toThrow(/needs player/);
  });

  it('breaks down by course for a player', () => {
    const d = base();
    d.exec(`INSERT INTO courses(id,slug,display_name) VALUES(2,'mc','MC')`);
    seed(d, [['reset', 0], ['reset', 0]]); // bc: 2 resets, no PB
    d.prepare(`INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,ended_at,was_pb)
               VALUES(10,1,1,2,150,'reset','live','2026-06-10T00:00:00+00:00',0)`).run(); // mc: 1 reset
    const r = resolveSequential(d, { metric: 'resets_since_pb', period: allTime(), filters: { player: 'Luke' }, groupBy: 'course', seasonId: 1 });
    expect(Object.fromEntries(r.rows.map((x) => [x.key, x.value]))).toEqual({ BC: 2, MC: 1 });
  });
});
