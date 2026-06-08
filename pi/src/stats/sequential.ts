import type { DatabaseSync } from 'node:sqlite';
import type { Dimension, Period, StatResult, StatRow } from './types';
import { getMetric } from './metrics';

export interface SequentialQuery {
  metric: string;
  period: Period;       // echoed only; sequential metrics describe current state, not a window
  filters: Partial<Record<Dimension, string>>;
  groupBy?: Dimension;
  seasonId: number;
}

type RunRow = { status: string; was_pb: number };

/** Compute a sequential metric over one group's time-ordered runs. */
export function computeSequential(metric: string, runs: RunRow[]): number | null {
  if (metric === 'resets_since_pb') {
    let lastPb = -1;
    runs.forEach((r, i) => { if (r.was_pb === 1) lastPb = i; });
    return runs.slice(lastPb + 1).filter((r) => r.status === 'reset').length;
  }
  if (metric === 'current_reset_streak') {
    let n = 0;
    for (let i = runs.length - 1; i >= 0; i--) { if (runs[i].status === 'reset') n++; else break; }
    return n;
  }
  if (metric === 'avg_resets_until_pb') {
    const epochs: number[] = [];
    let cur = 0;
    for (const r of runs) {
      if (r.status === 'reset') cur++;
      if (r.was_pb === 1) { epochs.push(cur); cur = 0; }
    }
    return epochs.length ? epochs.reduce((a, b) => a + b, 0) / epochs.length : null;
  }
  throw new Error(`unknown sequential metric: ${metric}`);
}

function playerId(db: DatabaseSync, name: string): number | null {
  const r = db.prepare('SELECT id FROM players WHERE display_name=? COLLATE NOCASE').get(name) as { id: number } | undefined;
  return r?.id ?? null;
}
function courseId(db: DatabaseSync, v: string): number | null {
  const r = db.prepare('SELECT id FROM courses WHERE slug=? OR display_name=? COLLATE NOCASE').get(v, v) as { id: number } | undefined;
  return r?.id ?? null;
}

export function resolveSequential(db: DatabaseSync, q: SequentialQuery): StatResult {
  const m = getMetric(q.metric);
  if (!m || m.kind !== 'sequential') throw new Error(`not a sequential metric: ${q.metric}`);

  const cc = q.filters.cc != null ? Number(q.filters.cc) : 150;
  const pid = q.filters.player ? playerId(db, q.filters.player) : null;
  const cid = q.filters.course ? courseId(db, q.filters.course) : null;

  let groups: { playerId: number; courseId: number; key: string }[];
  if (q.groupBy === 'course') {
    if (pid == null) throw new Error(`${q.metric} breakdown by course needs a player`);
    groups = (db.prepare(
      `SELECT DISTINCT r.course_id AS id, c.display_name AS name FROM runs r JOIN courses c ON c.id=r.course_id
       WHERE r.season_id=? AND r.player_id=? AND r.cc=?`).all(q.seasonId, pid, cc) as { id: number; name: string }[])
      .map((row) => ({ playerId: pid, courseId: row.id, key: row.name }));
  } else if (q.groupBy === 'player') {
    if (cid == null) throw new Error(`${q.metric} breakdown by player needs a course`);
    groups = (db.prepare(
      `SELECT DISTINCT r.player_id AS id, p.display_name AS name FROM runs r JOIN players p ON p.id=r.player_id
       WHERE r.season_id=? AND r.course_id=? AND r.cc=?`).all(q.seasonId, cid, cc) as { id: number; name: string }[])
      .map((row) => ({ playerId: row.id, courseId: cid, key: row.name }));
  } else {
    if (pid == null || cid == null) throw new Error(`${q.metric} value needs player + course`);
    groups = [{ playerId: pid, courseId: cid, key: q.metric }];
  }

  const runStmt = db.prepare(
    `SELECT status, was_pb FROM runs
     WHERE season_id=? AND player_id=? AND course_id=? AND cc=? AND status IN ('finished','reset','dnf')
     ORDER BY datetime(ended_at), id`);
  const rows: StatRow[] = groups.map((g) => ({
    key: g.key, value: computeSequential(q.metric, runStmt.all(q.seasonId, g.playerId, g.courseId, cc) as RunRow[]),
  }));

  let total: number | null;
  if (!q.groupBy) {
    total = rows[0]?.value ?? null;
  } else if (q.metric === 'avg_resets_until_pb') {
    const vals = rows.map((r) => r.value).filter((v): v is number => v != null);
    total = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
  } else {
    total = rows.reduce((s, r) => s + (r.value ?? 0), 0);
  }

  return {
    metric: q.metric,
    period: { key: q.period.key, tz: q.period.tz, start: q.period.startIso, end: q.period.endIso },
    filters: q.filters as Record<string, string>,
    group_by: q.groupBy,
    rows, total,
  };
}
