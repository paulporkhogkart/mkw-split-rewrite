import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from '../db/connect';
import { EventHub } from './events';
import { createApp } from './app';

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
