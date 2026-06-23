import { Hono } from 'hono';
import type { DatabaseSync } from 'node:sqlite';
import type { Env } from './app';
import { activeSeasonId } from '../db/seasons';
import { repoVersion } from '../version/repoVersion';
import { readService } from '../version/serviceStatus';
import { makeLatestFetcher, type LatestFn } from '../version/latest';

const BOOTED_AT = Date.now();   // module load ~ server boot

interface PlayerVersionRow { player_id: number; name: string; color: string | null; app_version: string | null; last_seen_at: number | null; }

/** GET /v1/version — public-but-unlisted diagnostic. Never throws: external lookups degrade to
 *  null + errors[], local DB reads always succeed. */
export function versionRoutes(db: DatabaseSync,
                              opts: { latest?: LatestFn; serverVersion?: string; bootedAt?: number } = {}): Hono<Env> {
  const serverVersion = opts.serverVersion ?? repoVersion();
  const latest = opts.latest ?? makeLatestFetcher();
  const bootedAt = opts.bootedAt ?? BOOTED_AT;
  const r = new Hono<Env>();
  r.get('/v1/version', async (c) => {
    const lv = await latest(c.req.query('fresh') === '1');
    const bot = readService(db, 'bot');
    const players = db.prepare(
      `SELECT p.id AS player_id, p.display_name AS name, p.color, p.app_version, p.last_seen_at
       FROM season_rosters sr JOIN players p ON p.id = sr.player_id
       WHERE sr.season_id = ?
       ORDER BY p.display_name`
    ).all(activeSeasonId(db)) as PlayerVersionRow[];
    return c.json({
      latest: { tag: lv.tag, app: lv.app, fetched_at: lv.fetched_at, errors: lv.errors },
      deployed: {
        server: { version: serverVersion, booted_at: bootedAt },
        bot: bot ? { version: bot.version, booted_at: bot.booted_at } : null,
      },
      players,
    });
  });
  return r;
}
