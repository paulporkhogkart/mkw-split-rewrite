import { Hono } from 'hono';
import type { DatabaseSync } from 'node:sqlite';
import type { Env } from './app';
import { activeSeasonId, listSeasons, courseIdBySlug } from '../db/seasons';
import { slugify } from '../db/slug';
import { courseLeaderboard, overallLeaderboard, friendsPbs, playerPbs, currentWr, myPbs, myPbSplits, courseTrails, roster, playerTrails } from '../db/reads';
import type { TrailMode } from '../db/reads';
import { requireToken } from './auth';
import { playerByToken } from '../db/players';

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
  r.get('/v1/me/pbs', requireToken(db), (c) => c.json(myPbs(db, activeSeasonId(db), c.get('playerId'))));
  r.get('/v1/me/pb-splits', requireToken(db), (c) => {
    const cid = course(c); if (cid === null) return c.json({ error: 'unknown course' }, 400);
    return c.json(myPbSplits(db, season(c), c.get('playerId'), cid, num(c.req.query('cc'), 150)));
  });
  r.get('/v1/trails', (c) => {
    const cid = course(c); if (cid === null) return c.json({ error: 'unknown course' }, 400);
    const m = /^Bearer (.+)$/.exec(c.req.header('authorization') ?? '');
    const me = m ? playerByToken(db, m[1]) : null;
    return c.json(courseTrails(db, season(c), cid, num(c.req.query('cc'), 150), me ? me.id : null));
  });
  r.get('/v1/roster', (c) => {
    const m = /^Bearer (.+)$/.exec(c.req.header('authorization') ?? '');
    const me = m ? playerByToken(db, m[1]) : null;
    return c.json(roster(db, season(c)).map((p) => ({ ...p, is_me: me ? p.player_id === me.id : false })));
  });
  r.get('/v1/players/:id/trails', (c) => {
    const cid = course(c); if (cid === null) return c.json({ error: 'unknown course' }, 400);
    const q = c.req.query('mode') ?? 'pbs';
    const mode = (['none', 'pbs', 'best', 'last', 'all'].includes(q) ? q : 'pbs') as TrailMode;
    return c.json(playerTrails(db, season(c), Number(c.req.param('id')), cid, num(c.req.query('cc'), 150), mode, num(c.req.query('n'), 1)));
  });
  return r;
}
