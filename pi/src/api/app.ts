import { Hono } from 'hono';
import type { DatabaseSync } from 'node:sqlite';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { EventHub } from './events';
import { runsRoutes } from './runs';
import { readsRoutes } from './reads';
import { createStatsApp } from './stats';
import { screenRoutes } from './screen';
import { presenceHandlers } from './presence';
import { playerByToken } from '../db/players';
import { requireTokenAny } from './auth';
import type { PresenceHub } from '../presence/hub';

export type Env = { Variables: { playerId: number; playerName: string } };

/** The self-contained stat-explorer page (pi/stat-explorer.html), served same-origin. */
const EXPLORER_HTML = fileURLToPath(new URL('../../stat-explorer.html', import.meta.url));

export function createApp(db: DatabaseSync, hub: EventHub,
                          invalidateModel?: (courseId: number) => void): Hono<Env> {
  const app = new Hono<Env>();
  app.get('/health', (c) => c.json({ status: 'ok' }));
  // Every HTTP route except /health and the two WebSocket streams needs a token (read or write).
  // /v1/events stays open: the on-Pi bot subscribes to it over localhost with no token, and it
  // only carries PB/WR events that are already announced publicly. /v1/presence keeps its own
  // optional-token (receive-only) model.
  const OPEN = new Set(['/health', '/v1/events', '/v1/presence']);
  app.use('*', (c, next) => (OPEN.has(c.req.path) ? next() : requireTokenAny(db)(c, next)));
  app.route('/', runsRoutes(db, hub, invalidateModel));
  app.route('/', readsRoutes(db));
  app.route('/', createStatsApp(db, { porkerPath: process.env.STATS_PORKER_DB ?? 'porker.db' }));
  app.route('/', screenRoutes(db));
  app.get('/explorer', (c) => {
    try { return c.html(readFileSync(EXPLORER_HTML, 'utf8')); }
    catch { return c.text('stat-explorer.html not found', 404); }
  });
  return app;
}

import { createNodeWebSocket } from '@hono/node-ws';

/** Attach the /v1/events + /v1/presence WebSocket routes. Returns { injectWebSocket }. */
export function makeWs(app: Hono<Env>, hub: EventHub, presence: PresenceHub, db: DatabaseSync) {
  const { injectWebSocket, upgradeWebSocket } = createNodeWebSocket({ app });
  app.get('/v1/events', upgradeWebSocket(() => {
    let unsub = () => {};
    return {
      onOpen(_e: unknown, ws: { send: (data: string) => void }) {
        unsub = hub.subscribe((evt) => ws.send(JSON.stringify(evt)));
      },
      onClose() { unsub(); },
    };
  }));
  // Live presence: a token (query param - a browser WS can't set headers) attributes the
  // sender's frames to their player; a token-less socket is receive-only.
  app.get('/v1/presence', upgradeWebSocket((c) => {
    const token = c.req.query('token');
    const player = token ? playerByToken(db, token) : null;
    return presenceHandlers(presence, player ? player.id : null);
  }));
  return { injectWebSocket };
}
