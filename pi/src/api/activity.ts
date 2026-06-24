import { Hono } from 'hono';
import type { DatabaseSync } from 'node:sqlite';
import type { Env } from './app';
import { recentActivity } from '../db/activity';
import { activeSeasonId } from '../db/seasons';

const num = (v: string | undefined, d: number) => { const n = Number(v); return Number.isFinite(n) ? n : d; };

export function activityRoutes(db: DatabaseSync): Hono<Env> {
  const r = new Hono<Env>();
  r.get('/v1/activity', (c) => {
    const seasonId = num(c.req.query('season'), activeSeasonId(db));
    const before = c.req.query('before') ? num(c.req.query('before'), 0) : undefined;
    return c.json(recentActivity(db, { seasonId, before, limit: num(c.req.query('limit'), 100) }));
  });
  return r;
}
