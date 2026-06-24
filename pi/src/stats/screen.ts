import type { DatabaseSync, SQLInputValue } from 'node:sqlite';
import type { Dimension, Period, StatResult, StatRow } from './types';
import { getMetric } from './metrics';
import { toEpochSeconds } from './period';

export interface ScreenInterval { screen: string; started_ms: number; ended_ms: number; }

/** Insert intervals for one player. Idempotent on (player_id, started_ms); skips
 *  non-positive-length / unnamed intervals. Returns the rows actually inserted. */
export function insertScreenIntervals(db: DatabaseSync, seasonId: number, playerId: number, intervals: ScreenInterval[]): ScreenInterval[] {
  const stmt = db.prepare(
    `INSERT OR IGNORE INTO screen_intervals(season_id, player_id, screen, started_ms, ended_ms)
     VALUES (?,?,?,?,?)`);
  const inserted: ScreenInterval[] = [];
  for (const iv of intervals) {
    if (!iv.screen || !(iv.ended_ms > iv.started_ms)) continue;
    if (Number(stmt.run(seasonId, playerId, iv.screen, iv.started_ms, iv.ended_ms).changes) > 0) {
      inserted.push(iv);
    }
  }
  return inserted;
}

export interface ScreenQuery {
  metric: string; period: Period; filters: Partial<Record<Dimension, string>>; groupBy?: Dimension; seasonId: number;
}

function playerId(db: DatabaseSync, name: string): number | null {
  const r = db.prepare('SELECT id FROM players WHERE display_name=? COLLATE NOCASE').get(name) as { id: number } | undefined;
  return r?.id ?? null;
}

export function resolveScreen(db: DatabaseSync, q: ScreenQuery): StatResult {
  const m = getMetric(q.metric);
  if (!m || m.kind !== 'screen') throw new Error(`not a screen metric: ${q.metric}`);

  const where = ['season_id=?'];
  const params: unknown[] = [q.seasonId];
  if (q.filters.player) { where.push('player_id=?'); params.push(playerId(db, q.filters.player) ?? -1); }
  if (q.filters.screen && q.groupBy !== 'screen') { where.push('screen=?'); params.push(q.filters.screen); }
  if (q.period.startUtc) { where.push('started_ms >= ?'); params.push(toEpochSeconds(q.period.startUtc) * 1000); }
  if (q.period.endUtc) { where.push('started_ms < ?'); params.push(toEpochSeconds(q.period.endUtc) * 1000); }

  const dur = 'SUM(ended_ms - started_ms)';
  let rows: StatRow[]; let total: number | null;
  if (q.groupBy === 'screen' || q.groupBy === 'player') {
    const sel = q.groupBy === 'screen' ? 'screen' : '(SELECT display_name FROM players WHERE id=player_id)';
    rows = db.prepare(`SELECT ${sel} AS key, ${dur} AS value FROM screen_intervals WHERE ${where.join(' AND ')} GROUP BY key ORDER BY value DESC`).all(...(params as SQLInputValue[])) as unknown as StatRow[];
    total = rows.reduce((s, x) => s + (x.value ?? 0), 0);
  } else {
    const r = db.prepare(`SELECT ${dur} AS value FROM screen_intervals WHERE ${where.join(' AND ')}`).get(...(params as SQLInputValue[])) as { value: number | null } | undefined;
    total = r?.value ?? 0;
    rows = [{ key: q.metric, value: total }];
  }

  return {
    metric: q.metric,
    period: { key: q.period.key, tz: q.period.tz, start: q.period.startIso, end: q.period.endIso },
    filters: q.filters as Record<string, string>,
    group_by: q.groupBy, rows, total,
  };
}
