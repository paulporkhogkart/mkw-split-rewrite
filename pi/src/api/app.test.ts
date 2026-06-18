import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from '../db/connect';
import { mintToken } from '../db/players';
import { EventHub } from './events';
import { createApp } from './app';

function appWith() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 1',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rainbow_road','Rainbow Road')");
  const token = mintToken(db, 'Paul');
  return { app: createApp(db, new EventHub()), token };
}

describe('app skeleton', () => {
  it('GET /health is public + ok', async () => {
    const { app } = appWith();
    const res = await app.request('/health');
    expect(res.status).toBe(200);
    expect((await res.json()).status).toBe('ok');
  });
  it('a token-gated write 401s without a token', async () => {
    const { app } = appWith();
    const res = await app.request('/v1/runs', { method: 'POST', body: '{}', headers: { 'content-type': 'application/json' } });
    expect(res.status).toBe(401);
  });
  it('accepts a valid token (not 401)', async () => {
    const { app, token } = appWith();
    const res = await app.request('/v1/runs', {
      method: 'POST',
      headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` },
      body: JSON.stringify({ attempt_id: 'x', course: 'Rainbow Road', status: 'reset' }),
    });
    expect(res.status).not.toBe(401);
  });
});

describe('reads need a token', () => {
  it('a read 401s with no token, 200s with a header or a ?token= query', async () => {
    const { app, token } = appWith();
    expect((await app.request('/v1/seasons')).status).toBe(401);
    expect((await app.request('/v1/seasons', { headers: { authorization: `Bearer ${token}` } })).status).toBe(200);
    expect((await app.request(`/v1/seasons?token=${token}`)).status).toBe(200);
  });
  it('GET /health stays public', async () => {
    expect((await appWith().app.request('/health')).status).toBe(200);
  });
});

describe('public website reads (open + CORS)', () => {
  it('leaderboard / world-records / roster are open without a token and send CORS', async () => {
    const { app } = appWith();
    for (const path of ['/v1/leaderboard?course=rainbow_road&cc=150', '/v1/world-records?course=rainbow_road&cc=150', '/v1/roster']) {
      const res = await app.request(path, { headers: { origin: 'http://localhost:1430' } });
      expect(res.status).not.toBe(401);
      expect(res.headers.get('access-control-allow-origin')).toBe('*');
    }
  });
  it('leaves the other reads + writes token-gated', async () => {
    const { app } = appWith();
    expect((await app.request('/v1/seasons')).status).toBe(401);
    expect((await app.request('/v1/runs', { method: 'POST', body: '{}', headers: { 'content-type': 'application/json' } })).status).toBe(401);
  });
});
