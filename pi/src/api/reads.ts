import { Hono } from 'hono';
import type { DatabaseSync } from 'node:sqlite';
import type { Env } from './app';
import { activeSeasonId, listSeasons, courseIdBySlug } from '../db/seasons';
import { slugify } from '../db/slug';
import { courseLeaderboard, overallLeaderboard, friendsPbs, playerPbs, currentWr } from '../db/reads';

const num = (v: string | undefined, d: number) => (v ? Number(v) : d);

export function readsRoutes(db: DatabaseSync): Hono<Env> {
  const r = new Hono<Env>();
  const season = (c: any) => num(c.req.query('season'), activeSeasonId(db));
  const course = (c: any) => courseIdBySlug(db, slugify(c.req.query('course') ?? ''));

  r.get('/v1/leaderboard', (c) => {
    const cid = course(c); if (cid === null) return c.json({ error: 'unknown course' }, 400);
    return c.json(courseLeaderboard(db, season(c), cid, num(c.req.query('cc'), 150)));
  });
  r.get('/v1/leaderboard/overall', (c) => c.json(overallLeaderboard(db, season(c), num(c.req.query('cc'), 150))));
  r.get('/v1/friends-pbs', (c) => {
    const cid = course(c); if (cid === null) return c.json({ error: 'unknown course' }, 400);
    return c.json(friendsPbs(db, season(c), cid, num(c.req.query('cc'), 150)));
  });
  r.get('/v1/players/:id/pbs', (c) => c.json(playerPbs(db, season(c), Number(c.req.param('id')), num(c.req.query('cc'), 150))));
  r.get('/v1/world-records', (c) => {
    const cid = course(c); if (cid === null) return c.json({ error: 'unknown course' }, 400);
    return c.json(currentWr(db, cid, num(c.req.query('cc'), 150)));
  });
  r.get('/v1/seasons', (c) => c.json(listSeasons(db)));
  return r;
}
