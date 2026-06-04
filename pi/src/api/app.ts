import { Hono } from 'hono';
import type { DatabaseSync } from 'node:sqlite';
import { EventHub } from './events';
import { requireToken } from './auth';

export type Env = { Variables: { playerId: number; playerName: string } };

export function createApp(db: DatabaseSync, hub: EventHub): Hono<Env> {
  const app = new Hono<Env>();
  app.get('/health', (c) => c.json({ status: 'ok' }));
  // Placeholder write route (replaced in Task 8) — just exercises auth.
  app.post('/v1/runs', requireToken(db), (c) => c.json({ ok: true }));
  return app;
}
