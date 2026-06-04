import { Hono } from 'hono';
import type { DatabaseSync } from 'node:sqlite';
import { EventHub } from './events';
import { runsRoutes } from './runs';
import { readsRoutes } from './reads';

export type Env = { Variables: { playerId: number; playerName: string } };

export function createApp(db: DatabaseSync, hub: EventHub): Hono<Env> {
  const app = new Hono<Env>();
  app.get('/health', (c) => c.json({ status: 'ok' }));
  app.route('/', runsRoutes(db, hub));
  app.route('/', readsRoutes(db));
  return app;
}
