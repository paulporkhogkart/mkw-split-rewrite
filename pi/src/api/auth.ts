import type { Context, Next } from 'hono';
import type { DatabaseSync } from 'node:sqlite';
import { playerByToken } from '../db/players';

/** Token from the Authorization: Bearer header only. */
function bearerToken(c: Context): string | null {
  const m = /^Bearer (.+)$/.exec(c.req.header('authorization') ?? '');
  return m ? m[1] : null;
}

/** Token from the Bearer header, else a ?token= query param (browsers / WebSocket clients
 *  can't set an Authorization header). */
export function tokenFromRequest(c: Context): string | null {
  return bearerToken(c) ?? c.req.query('token') ?? null;
}

function gate(db: DatabaseSync, extract: (c: Context) => string | null) {
  return async (c: Context, next: Next) => {
    const tok = extract(c);
    const player = tok ? playerByToken(db, tok) : null;
    if (!player) return c.json({ error: 'unauthorized' }, 401);
    c.set('playerId', player.id);
    c.set('playerName', player.display_name);
    await next();
  };
}

/** Header-only auth — for writes (a ?token= in a write URL would leak in logs). */
export function requireToken(db: DatabaseSync) { return gate(db, bearerToken); }

/** Header-or-query auth — for reads (lets a browser / WS client pass ?token=). */
export function requireTokenAny(db: DatabaseSync) { return gate(db, tokenFromRequest); }
