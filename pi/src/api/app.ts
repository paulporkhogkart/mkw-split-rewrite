import { Hono } from 'hono';
import type { DatabaseSync } from 'node:sqlite';
import { EventHub } from './events';
import { runsRoutes } from './runs';
import { readsRoutes } from './reads';
import { createStatsApp } from './stats';

export type Env = { Variables: { playerId: number; playerName: string } };

export function createApp(db: DatabaseSync, hub: EventHub): Hono<Env> {
  const app = new Hono<Env>();
  app.get('/health', (c) => c.json({ status: 'ok' }));
  app.route('/', runsRoutes(db, hub));
  app.route('/', readsRoutes(db));
  app.route('/', createStatsApp(db, { porkerPath: process.env.STATS_PORKER_DB ?? 'porker.db' }));
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
