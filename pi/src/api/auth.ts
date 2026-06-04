import type { Context, Next } from 'hono';
import type { DatabaseSync } from 'node:sqlite';
import { playerByToken } from '../db/players';

export function requireToken(db: DatabaseSync) {
  return async (c: Context, next: Next) => {
    const auth = c.req.header('authorization') ?? '';
    const m = /^Bearer (.+)$/.exec(auth);
    const player = m ? playerByToken(db, m[1]) : null;
    if (!player) return c.json({ error: 'unauthorized' }, 401);
    c.set('playerId', player.id);
    c.set('playerName', player.display_name);
    await next();
  };
}
