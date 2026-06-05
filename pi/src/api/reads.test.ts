import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from '../db/connect';
import { EventHub } from './events';
import { createApp } from './app';
import { mintToken } from '../db/players';

function appWith() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 1',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rainbow_road','Rainbow Road')");
  db.exec("INSERT INTO runs(season_id,player_id,course_id,cc,status,provenance,total_time_ms,total_time_str,is_pb) VALUES (1,1,1,150,'finished','live',108000,'1:48.000',1)");
  return createApp(db, new EventHub());
}

describe('public reads (no token)', () => {
  it('GET /v1/leaderboard returns rows', async () => {
    const res = await appWith().request('/v1/leaderboard?course=Rainbow%20Road&cc=150');
    expect(res.status).toBe(200);
    expect((await res.json())[0].display_name).toBe('Paul');
  });
  it('GET /v1/seasons works', async () => {
    const res = await appWith().request('/v1/seasons');
    expect(res.status).toBe(200);
    expect((await res.json()).length).toBe(1);
  });
});

describe('GET /v1/me/pbs (token)', () => {
  it('401s without a token, returns the caller\'s PBs with one', async () => {
    const db = openDb(':memory:'); applySchema(db);
    db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 1',1)");
    db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
    db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rainbow_road','Rainbow Road')");
    db.exec("INSERT INTO runs(season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb) VALUES (1,1,1,150,'finished','live',108000,1)");
    const app = createApp(db, new EventHub());
    const token = mintToken(db, 'Paul');

    expect((await app.request('/v1/me/pbs')).status).toBe(401);

    const res = await app.request('/v1/me/pbs', { headers: { authorization: `Bearer ${token}` } });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual([{ course_slug: 'rainbow_road', cc: 150, total_time_ms: 108000 }]);
  });
});
