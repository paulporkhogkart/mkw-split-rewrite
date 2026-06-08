import { Hono } from 'hono';
import type { DatabaseSync } from 'node:sqlite';
import type { Env } from './app';
import { requireToken } from './auth';
import { activeSeasonId } from '../db/seasons';
import { insertScreenIntervals, type ScreenInterval } from '../stats/screen';

/** Authed ingest of screen-time intervals forwarded by the app. */
export function screenRoutes(db: DatabaseSync): Hono<Env> {
  const r = new Hono<Env>();
  r.post('/v1/screen-intervals', requireToken(db), async (c) => {
    const playerId = c.get('playerId');
    const body = (await c.req.json().catch(() => null)) as { intervals?: ScreenInterval[] } | null;
    if (!body || !Array.isArray(body.intervals)) return c.json({ error: 'bad payload' }, 400);
    const inserted = insertScreenIntervals(db, activeSeasonId(db), playerId, body.intervals);
    return c.json({ inserted });
  });
  return r;
}
