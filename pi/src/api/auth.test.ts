import { describe, it, expect } from 'vitest';
import { Hono } from 'hono';
import { openDb, applySchema } from '../db/connect';
import { mintToken } from '../db/players';
import { requireTokenAny } from './auth';
import type { Env } from './app';

function gated() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
  const token = mintToken(db, 'Paul');
  const app = new Hono<Env>();
  app.use('*', requireTokenAny(db));
  app.get('/x', (c) => c.json({ ok: true, me: c.get('playerName') }));
  return { app, token };
}

describe('requireTokenAny', () => {
  it('401s without a token', async () => {
    expect((await gated().app.request('/x')).status).toBe(401);
  });
  it('accepts a Bearer header and sets the player', async () => {
    const { app, token } = gated();
    const res = await app.request('/x', { headers: { authorization: `Bearer ${token}` } });
    expect(res.status).toBe(200);
    expect((await res.json()).me).toBe('Paul');
  });
  it('accepts a ?token= query param', async () => {
    const { app, token } = gated();
    expect((await app.request(`/x?token=${token}`)).status).toBe(200);
  });
  it('401s on a bad token', async () => {
    expect((await gated().app.request('/x?token=nope')).status).toBe(401);
  });
});
