import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from '../db/connect';
import { EventHub } from './events';
import { createApp } from './app';

function appWithData() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul'),(2,'Luke')");
  db.exec("INSERT INTO season_rosters(season_id,player_id) VALUES (1,1),(1,2)");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','Rainbow Road')");
  db.exec(`INSERT INTO runs(season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb) VALUES
    (1,2,1,150,'finished','live',108000,1),(1,1,1,150,'finished','live',110000,1)`);
  db.exec("INSERT INTO world_records(course_id,cc,holder_name,record_ms,record_str,is_current) VALUES (1,150,'WR',100000,'1:40',1)");
  return createApp(db, new EventHub());
}

describe('GET /v1/players/:slug', () => {
  it('serves a summary with no token and CORS headers', async () => {
    const res = await appWithData().request('/v1/players/paul');
    expect(res.status).toBe(200);
    expect(res.headers.get('access-control-allow-origin')).toBe('*');
    const body = await res.json();
    expect(body.profile.display_name).toBe('Paul');
    expect(body.pbs[0].slug).toBe('rr');
  });

  it('404s an unknown slug', async () => {
    const res = await appWithData().request('/v1/players/nobody');
    expect(res.status).toBe(404);
  });

  it('leaves the token-gated /v1/players/:id/pbs untouched (401 without a token)', async () => {
    const res = await appWithData().request('/v1/players/1/pbs');
    expect(res.status).toBe(401);
  });
});
