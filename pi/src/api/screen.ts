import { Hono } from 'hono';
import type { DatabaseSync } from 'node:sqlite';
import type { Env } from './app';
import { requireToken } from './auth';
import { activeSeasonId } from '../db/seasons';
import { insertScreenIntervals, type ScreenInterval } from '../stats/screen';

/** Authed ingest of screen-time intervals forwarded by the app. Feeds the screen-time stats
 *  (`/v1/stats`); the activity feed is now driven live from presence, not these batches. */
export function screenRoutes(db: DatabaseSync): Hono<Env> {
  const r = new Hono<Env>();
  r.post('/v1/screen-intervals', requireToken(db), async (c) => {
    const playerId = c.get('playerId');
    const body = (await c.req.json().catch(() => null)) as { intervals?: ScreenInterval[] } | null;
    if (!body || !Array.isArray(body.intervals)) return c.json({ error: 'bad payload' }, 400);
    const seasonId = activeSeasonId(db);
    const insertedRows = insertScreenIntervals(db, seasonId, playerId, body.intervals);
    return c.json({ inserted: insertedRows.length });
  });
  return r;
}
