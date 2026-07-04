import { DatabaseSync } from 'node:sqlite';
import type { Period, StatResult, StatRow } from './types';
import type { BodyAgg } from './metrics';
import { getMetric } from './metrics';
import { toEpochSeconds } from './period';

/** porker `person` key -> kart-player display name. Blu/Cbri excluded (non-participants).
 *  The new porker.db uses one `measurements` table keyed by `person` (mr-porker 2.0);
 *  these keys match the participant `key`s in mr-porker's config.json and the labels the
 *  migration wrote for the historical per-person tables. */
export const PORKER_MAP: { person: string; player: string }[] = [
  { person: 'paul', player: 'paul pork' },   // must match players.display_name (renamed from 'Paul')
  { person: 'addymer', player: 'Gub' },
  { person: 'alex', player: 'Alex' },
  { person: 'eunora', player: 'Luke' },
  { person: 'brayden', player: 'Aliias' },
];

/** Normalized metric id -> porker.db column name (mr-porker 2.0 snake_case). Shared with align.ts. */
export const BODY_SOURCE_COLUMNS: Record<string, string> = {
  weight: 'weight', bmi: 'body_mass_index', body_fat: 'body_fat', fat_free_weight: 'fat_free_body_weight',
  subcutaneous_fat: 'subcutaneous_fat', visceral_fat: 'visceral_fat', body_water: 'body_water',
  skeletal_muscle: 'skeletal_muscle', muscle_mass: 'muscle_mass', bone_mass: 'bone_mass',
  protein: 'protein', bmr: 'basal_metabolic_rate', metabolic_age: 'metabolic_age',
};

/** Open porker.db read-only (coexists with the pork bot's writer). */
export function openPorker(path: string): DatabaseSync {
  const db = new DatabaseSync(path, { readOnly: true });
  db.exec('PRAGMA busy_timeout=2000');
  return db;
}

/** Which mapped porker people actually have rows in a given schema ('main' for a standalone
 *  porker connection, 'porker' when ATTACHed). Returns [] if the `measurements` table is
 *  absent (empty fixture) or no mapped person has data. */
export function presentPorkerPeople(db: DatabaseSync, schema = 'main'): { person: string; player: string }[] {
  const hasTable = db.prepare(`SELECT 1 FROM ${schema}.sqlite_master WHERE type='table' AND name='measurements'`).get() != null;
  if (!hasTable) return [];
  const people = new Set((db.prepare(`SELECT DISTINCT person FROM ${schema}.measurements`).all() as { person: string }[]).map((r) => r.person));
  return PORKER_MAP.filter((m) => people.has(m.person));
}

export interface BodyQuery {
  metric: string; agg: BodyAgg; period: Period; filters: { player?: string };
}

/** One person's column value under an aggregation, scoped to the period window (epoch bounds).
 *  `column` is the porker.db column name (e.g. body_fat), not the normalized metric id. */
function valueFor(db: DatabaseSync, person: string, column: string, agg: BodyAgg, lo: number | null, hi: number | null): number | null {
  const win: string[] = ['person = ?']; const p: (string | number)[] = [person];
  if (lo != null) { win.push('"timestamp" >= ?'); p.push(lo); }
  if (hi != null) { win.push('"timestamp" < ?'); p.push(hi); }
  const w = `WHERE ${win.join(' AND ')}`;
  if (agg === 'min' || agg === 'max') {
    const row = db.prepare(`SELECT ${agg.toUpperCase()}("${column}") AS v FROM measurements ${w}`).get(...p) as { v: number | null };
    return row?.v ?? null;
  }
  if (agg === 'current') {
    // latest on-or-before the window end (hi); ignore lo so "current" is the standing value
    const hw: string[] = ['person = ?']; const hp: (string | number)[] = [person];
    if (hi != null) { hw.push('"timestamp" < ?'); hp.push(hi); }
    const row = db.prepare(`SELECT "${column}" AS v FROM measurements WHERE ${hw.join(' AND ')} ORDER BY "timestamp" DESC LIMIT 1`).get(...hp) as { v: number | null };
    return row?.v ?? null;
  }
  // change = last - first within the window
  const first = db.prepare(`SELECT "${column}" AS v FROM measurements ${w} ORDER BY "timestamp" ASC LIMIT 1`).get(...p) as { v: number | null };
  const last = db.prepare(`SELECT "${column}" AS v FROM measurements ${w} ORDER BY "timestamp" DESC LIMIT 1`).get(...p) as { v: number | null };
  return first?.v != null && last?.v != null ? last.v - first.v : null;
}

export function resolveBody(db: DatabaseSync, q: BodyQuery): StatResult {
  const m = getMetric(q.metric);
  if (!m || m.kind !== 'body') throw new Error(`not a body metric: ${q.metric}`);
  if (!m.aggs.includes(q.agg)) throw new Error(`agg ${q.agg} not allowed for ${q.metric}`);
  const col = BODY_SOURCE_COLUMNS[m.column];
  if (!col) throw new Error(`no porker column for ${q.metric}`);

  const lo = q.period.startUtc ? toEpochSeconds(q.period.startUtc) : null;
  const hi = q.period.endUtc ? toEpochSeconds(q.period.endUtc) : null;

  const people = presentPorkerPeople(db).filter((t) => !q.filters.player || t.player.toLowerCase() === q.filters.player.toLowerCase());
  const rows: StatRow[] = [];
  for (const t of people) {
    const v = valueFor(db, t.person, col, q.agg, lo, hi);
    if (v != null) rows.push({ key: t.player, value: v });
  }
  rows.sort((a, b) => (b.value ?? 0) - (a.value ?? 0));
  const total = rows.length ? rows.reduce((s, r) => s + (r.value ?? 0), 0) : null;

  return {
    metric: q.metric,
    period: { key: q.period.key, tz: q.period.tz, start: q.period.startIso, end: q.period.endIso },
    filters: q.filters as Record<string, string>,
    rows, total,
  };
}
