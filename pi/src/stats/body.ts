import { DatabaseSync } from 'node:sqlite';
import type { Period, StatResult, StatRow } from './types';
import type { BodyAgg } from './metrics';
import { getMetric } from './metrics';
import { toEpochSeconds } from './period';

/** porker table -> kart-player display name. Blu/Cbri excluded (non-participants). */
export const PORKER_MAP: { table: string; player: string }[] = [
  { table: 'Measurements', player: 'Paul' },
  { table: 'AddymerMeasurements', player: 'Adymer' },
  { table: 'AlexMeasurements', player: 'Alex' },
  { table: 'EunoraMeasurements', player: 'Luke' },
  { table: 'BraydenMeasurements', player: 'Aliias' },
];

/** Normalized metric id -> porker original column name. Shared with align.ts. */
export const BODY_SOURCE_COLUMNS: Record<string, string> = {
  weight: 'Weight', bmi: 'BodyMassIndex', body_fat: 'BodyFat', fat_free_weight: 'FatFreeBodyWeight',
  subcutaneous_fat: 'SubcutaneousFat', visceral_fat: 'VisceralFat', body_water: 'BodyWater',
  skeletal_muscle: 'SkeletalMuscle', muscle_mass: 'MuscleMass', bone_mass: 'BoneMass',
  protein: 'Protein', bmr: 'BasalMetabolicRate', metabolic_age: 'MetabolicAge',
};

/** Open porker.db read-only (coexists with the pork bot's writer). */
export function openPorker(path: string): DatabaseSync {
  const db = new DatabaseSync(path, { readOnly: true });
  db.exec('PRAGMA busy_timeout=2000');
  return db;
}

/** Which porker tables actually exist in a given schema ('main' for a standalone porker
 *  connection, 'porker' when ATTACHed). A fixture/partial DB may omit some. */
export function presentPorkerTables(db: DatabaseSync, schema = 'main'): { table: string; player: string }[] {
  const names = new Set((db.prepare(`SELECT name FROM ${schema}.sqlite_master WHERE type='table'`).all() as { name: string }[]).map((r) => r.name));
  return PORKER_MAP.filter((m) => names.has(m.table));
}

export interface BodyQuery {
  metric: string; agg: BodyAgg; period: Period; filters: { player?: string };
}

/** One player's column value under an aggregation, scoped to the period window (epoch bounds).
 *  `column` is the porker ORIGINAL column name (e.g. BodyFat), not the normalized metric id. */
function valueFor(db: DatabaseSync, table: string, column: string, agg: BodyAgg, lo: number | null, hi: number | null): number | null {
  const win: string[] = []; const p: number[] = [];
  if (lo != null) { win.push('"Timestamp" >= ?'); p.push(lo); }
  if (hi != null) { win.push('"Timestamp" < ?'); p.push(hi); }
  const w = win.length ? `WHERE ${win.join(' AND ')}` : '';
  if (agg === 'min' || agg === 'max') {
    const row = db.prepare(`SELECT ${agg.toUpperCase()}("${column}") AS v FROM "${table}" ${w}`).get(...p) as { v: number | null };
    return row?.v ?? null;
  }
  if (agg === 'current') {
    // latest on-or-before the window end (hi); ignore lo so "current" is the standing value
    const hw = hi != null ? 'WHERE "Timestamp" < ?' : '';
    const hp = hi != null ? [hi] : [];
    const row = db.prepare(`SELECT "${column}" AS v FROM "${table}" ${hw} ORDER BY "Timestamp" DESC LIMIT 1`).get(...hp) as { v: number | null };
    return row?.v ?? null;
  }
  // change = last - first within the window
  const first = db.prepare(`SELECT "${column}" AS v FROM "${table}" ${w} ORDER BY "Timestamp" ASC LIMIT 1`).get(...p) as { v: number | null };
  const last = db.prepare(`SELECT "${column}" AS v FROM "${table}" ${w} ORDER BY "Timestamp" DESC LIMIT 1`).get(...p) as { v: number | null };
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

  const tables = presentPorkerTables(db).filter((t) => !q.filters.player || t.player.toLowerCase() === q.filters.player.toLowerCase());
  const rows: StatRow[] = [];
  for (const t of tables) {
    const v = valueFor(db, t.table, col, q.agg, lo, hi);
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
