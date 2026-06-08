import { Hono } from 'hono';
import type { DatabaseSync } from 'node:sqlite';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { EventHub } from './events';
import { runsRoutes } from './runs';
import { readsRoutes } from './reads';
import { createStatsApp } from './stats';
import { screenRoutes } from './screen';

export type Env = { Variables: { playerId: number; playerName: string } };

/** The self-contained stat-explorer page (pi/stat-explorer.html), served same-origin. */
const EXPLORER_HTML = fileURLToPath(new URL('../../stat-explorer.html', import.meta.url));

export function createApp(db: DatabaseSync, hub: EventHub): Hono<Env> {
  const app = new Hono<Env>();
  app.get('/health', (c) => c.json({ status: 'ok' }));
  app.route('/', runsRoutes(db, hub));
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

/** Attach the /v1/events WebSocket route. Returns { injectWebSocket } to call on the Node server. */
export function makeWs(app: Hono<Env>, hub: EventHub) {
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
  return { injectWebSocket };
}
