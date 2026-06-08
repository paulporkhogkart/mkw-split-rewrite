import { describe, it, expect } from 'vitest';
import { DatabaseSync } from 'node:sqlite';
import { applySchema } from '../db/connect';
import { insertScreenIntervals, resolveScreen } from './screen';
import { resolvePeriod } from './period';

const allTime = () => resolvePeriod('all_time', 'Australia/Melbourne');

function base(): DatabaseSync {
  const d = new DatabaseSync(':memory:');
  applySchema(d);
  d.exec(`INSERT INTO seasons(id,name,is_active) VALUES(1,'S1',1);
          INSERT INTO players(id,display_name) VALUES(1,'Luke');`);
  return d;
}

describe('insertScreenIntervals', () => {
  it('inserts, is idempotent on (player, started_ms), and skips non-positive', () => {
    const d = base();
    expect(insertScreenIntervals(d, 1, 1, [{ screen: 'MAIN_MENU', started_ms: 1000, ended_ms: 4000 }])).toBe(1);
    expect(insertScreenIntervals(d, 1, 1, [{ screen: 'MAIN_MENU', started_ms: 1000, ended_ms: 4000 }])).toBe(0); // dup
    expect(insertScreenIntervals(d, 1, 1, [{ screen: 'X', started_ms: 5000, ended_ms: 5000 }])).toBe(0);          // zero-length
  });
});

describe('resolveScreen', () => {
  it('sums duration, filters by screen, and breaks down by screen', () => {
    const d = base();
    insertScreenIntervals(d, 1, 1, [
      { screen: 'MAIN_MENU', started_ms: 0, ended_ms: 3000 },
      { screen: 'MAIN_MENU', started_ms: 10000, ended_ms: 12000 },
      { screen: 'RACING', started_ms: 20000, ended_ms: 25000 },
    ]);
    const q = (filters: Record<string, string>, groupBy?: 'screen') =>
      resolveScreen(d, { metric: 'screen_time', period: allTime(), filters, groupBy, seasonId: 1 });
    expect(q({}).total).toBe(10000);             // 3000 + 2000 + 5000
    expect(q({ screen: 'MAIN_MENU' }).total).toBe(5000);
    const bd = q({}, 'screen');
    expect(Object.fromEntries(bd.rows.map((r) => [r.key, r.value]))).toEqual({ MAIN_MENU: 5000, RACING: 5000 });
  });
});
