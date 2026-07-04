import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from '../db/connect';
import { EventHub } from './events';
import { createApp } from './app';

function appWithData() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
  db.exec("INSERT INTO players(id,display_name,color) VALUES (1,'Paul','#f00')");
  db.exec("INSERT INTO season_rosters(season_id,player_id) VALUES (1,1)");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','Rainbow Road')");
  db.exec("INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb,ended_at) VALUES (1,1,1,1,150,'finished','live',110000,1,'2026-01-01T00:00:00Z')");
  return createApp(db, new EventHub());
}

describe('GET /v1/courses/:slug', () => {
  it('serves a summary with no token and CORS headers', async () => {
    const res = await appWithData().request('/v1/courses/rr');
    expect(res.status).toBe(200);
    expect(res.headers.get('access-control-allow-origin')).toBe('*');
    const body = await res.json();
    expect(body.profile.display_name).toBe('Rainbow Road');
    expect(body.leaderboard[0].display_name).toBe('Paul');
  });

  it('404s an unknown slug', async () => {
    const res = await appWithData().request('/v1/courses/nope');
    expect(res.status).toBe(404);
  });

  it('keeps a two-segment course path token-gated (401 without a token)', async () => {
    const res = await appWithData().request('/v1/courses/rr/model');
    expect(res.status).toBe(401);
  });
});
