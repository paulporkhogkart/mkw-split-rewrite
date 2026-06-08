import { Hono } from 'hono';
import type { DatabaseSync } from 'node:sqlite';
import { existsSync } from 'node:fs';
import { DateTime } from 'luxon';
import { resolvePeriod } from '../stats/period';
import type { PeriodKey, Dimension, StatResult } from '../stats/types';
import { getMetric, listMetrics, allowsDimension, type BodyAgg } from '../stats/metrics';
import { resolveRace } from '../stats/resolve';
import { resolveSequential } from '../stats/sequential';
import { resolveBody, openPorker, presentPorkerTables } from '../stats/body';
import { parseBodyCondition, bodyConditionSql } from '../stats/align';
import { activeSeasonId } from '../db/seasons';

const DIMS: Dimension[] = ['player', 'course', 'character', 'kart', 'costume', 'cc'];
const PERIODS: PeriodKey[] = ['today', 'this_week', 'this_month', 'all_time', 'range'];

export interface StatsDeps { porkerPath: string | null; }

/** Minimal structural view of a request context (real Hono context + the series fake both satisfy it). */
interface Ctx { req: { query(): Record<string, string>; query(k: string): string | undefined } }

export function createStatsApp(db: DatabaseSync, deps: StatsDeps): Hono {
  const app = new Hono();

  const collectFilters = (c: Ctx): Partial<Record<Dimension, string>> => {
    const f: Partial<Record<Dimension, string>> = {};
    for (const d of DIMS) { const v = c.req.query(d); if (v != null) f[d] = v; }
    return f;
  };

  const period = (c: Ctx) => {
    const key = (c.req.query('period') ?? 'all_time') as PeriodKey;
    if (!PERIODS.includes(key)) throw { code: 400, msg: `bad period: ${key}` };
    const tz = c.req.query('tz') ?? 'Australia/Melbourne';
    return resolvePeriod(key, tz, { from: c.req.query('from'), to: c.req.query('to') });
  };

  const requirePorker = (): string => {
    if (!deps.porkerPath || !existsSync(deps.porkerPath)) throw { code: 503, msg: 'porker.db unavailable' };
    return deps.porkerPath;
  };

  const handleBody = (c: Ctx): StatResult => {
    const metric = c.req.query('metric')!;
    const m = getMetric(metric)!;
    const agg = (c.req.query('agg') ?? (m as { defaultAgg?: BodyAgg }).defaultAgg) as BodyAgg;
    const pk = openPorker(requirePorker());
    try { return resolveBody(pk, { metric, agg, period: period(c), filters: { player: c.req.query('player') ?? undefined } }); }
    finally { pk.close(); }
  };

  const handleRace = (c: Ctx, groupBy?: Dimension): StatResult => {
    const metric = c.req.query('metric')!;
    const filters = collectFilters(c);
    const seasonId = c.req.query('season') ? Number(c.req.query('season')) : activeSeasonId(db);
    const cc = c.req.query('cc') ? Number(c.req.query('cc')) : undefined;
    const bcRaw = c.req.query('body_condition');
    if (!bcRaw) return resolveRace(db, { metric, period: period(c), filters, groupBy, seasonId, cc });
    // cross-domain: ATTACH porker, build the condition over present tables, run, DETACH.
    const path = requirePorker();
    db.exec(`ATTACH DATABASE '${path.replace(/'/g, "''")}' AS porker`);
    try {
      const tables = presentPorkerTables(db, 'porker');
      const frag = bodyConditionSql(parseBodyCondition(bcRaw), tables);
      return resolveRace(db, { metric, period: period(c), filters, groupBy, seasonId, cc, bodyConditionSql: frag });
    } finally { try { db.exec('DETACH DATABASE porker'); } catch { /* ignore */ } }
  };

  const guard = (c: Ctx, groupBy?: Dimension) => {
    const id = c.req.query('metric');
    if (!id) throw { code: 400, msg: 'metric required' };
    const m = getMetric(id);
    if (!m) throw { code: 400, msg: `unknown metric: ${id}` };
    if (groupBy) {
      if (!DIMS.includes(groupBy)) throw { code: 400, msg: `bad group_by: ${groupBy}` };
      if (!allowsDimension(id, groupBy)) throw { code: 400, msg: `${id} cannot group by ${groupBy}` };
    }
    for (const d of DIMS) if (c.req.query(d) != null && !allowsDimension(id, d)) throw { code: 400, msg: `${id} cannot filter by ${d}` };
    return m;
  };

  const handleSequential = (c: Ctx, groupBy?: Dimension): StatResult => {
    const metric = c.req.query('metric')!;
    const filters = collectFilters(c);
    const seasonId = c.req.query('season') ? Number(c.req.query('season')) : activeSeasonId(db);
    return resolveSequential(db, { metric, period: period(c), filters, groupBy, seasonId });
  };

  const dispatch = (c: Ctx, groupBy?: Dimension): StatResult => {
    const m = guard(c, groupBy);
    if (m.kind === 'body') return handleBody(c);
    if (m.kind === 'sequential') return handleSequential(c, groupBy);
    return handleRace(c, groupBy);
  };

  function seriesResult(c: Ctx) {
    const m = guard(c);
    const bucket = (c.req.query('bucket') ?? 'day') as 'day' | 'week' | 'month';
    const p = period(c);
    if (!p.startIso || !p.endIso) throw { code: 400, msg: 'series requires a bounded period' };
    const tz = c.req.query('tz') ?? 'Australia/Melbourne';
    const buckets = subBuckets(p.startIso, p.endIso, bucket, tz).map(([from, to]) => {
      const sub = new URLSearchParams(c.req.query() as Record<string, string>);
      sub.set('period', 'range'); sub.set('from', from); sub.set('to', to);
      const fakeC: Ctx = { req: { query: ((k?: string) => (k === undefined ? Object.fromEntries(sub) : sub.get(k) ?? undefined)) as Ctx['req']['query'] } };
      const r = m.kind === 'body' ? handleBody(fakeC) : handleRace(fakeC);
      return { start: from, end: to, value: r.total };
    });
    return { metric: c.req.query('metric'), bucket, buckets };
  }

  const wrap = (c: { json(v: unknown, status?: number): Response }, fn: () => unknown) => {
    try { return c.json(fn()); }
    catch (e: unknown) {
      const err = e as { code?: number; msg?: string; message?: string };
      return c.json({ error: err?.msg ?? err?.message ?? String(e) }, err?.code ?? 400);
    }
  };

  app.get('/v1/stats/value', (c) => wrap(c, () => {
    const r = dispatch(c);
    return { metric: r.metric, period: r.period, filters: r.filters, value: r.total, unevaluable: r.unevaluable };
  }));
  app.get('/v1/stats/breakdown', (c) => wrap(c, () => {
    const gb = c.req.query('group_by') as Dimension | undefined;
    if (!gb) throw { code: 400, msg: 'breakdown requires group_by' };
    return dispatch(c, gb);
  }));
  app.get('/v1/stats/series', (c) => wrap(c, () => seriesResult(c)));
  app.get('/v1/stats/metrics', (c) => c.json(listMetrics().map((m) => ({
    id: m.id, kind: m.kind,
    dimensions: m.kind === 'race' ? DIMS : m.kind === 'sequential' ? (['player', 'course', 'cc'] as Dimension[]) : ['player'],
    aggs: m.kind === 'body' ? m.aggs : undefined,
  }))));

  return app;
}

/** Split [from,to) ISO bounds into per-bucket [from,to) ISO ranges in tz. */
function subBuckets(fromIso: string, toIso: string, bucket: 'day' | 'week' | 'month', tz: string): [string, string][] {
  const end = DateTime.fromISO(toIso, { zone: tz });
  let cur = DateTime.fromISO(fromIso, { zone: tz });
  const unit = bucket === 'day' ? 'days' : bucket === 'week' ? 'weeks' : 'months';
  const out: [string, string][] = [];
  while (cur < end) {
    const next = cur.plus({ [unit]: 1 });
    out.push([cur.toISO()!, (next < end ? next : end).toISO()!]);
    cur = next;
  }
  return out;
}
