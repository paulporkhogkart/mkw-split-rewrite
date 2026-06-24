import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from '../db/connect';
import { EventHub } from './events';
import { ActivityHub } from '../activity/hub';
import { insertActivityEvents } from '../db/activity';
import { createApp } from './app';

function ctx() {
  const db = openDb(':memory:'); applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
  db.exec("INSERT INTO players(id,display_name,color) VALUES (1,'Gub','#38bdf8')");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'crown_city','Crown City')");
  return { db, app: createApp(db, new EventHub(), undefined, { activity: new ActivityHub() }) };
}

describe('GET /v1/activity', () => {
  it('returns newest-first, no auth required', async () => {
    const { db, app } = ctx();
    insertActivityEvents(db, [
      { ts: 1, type: 'pb', season_id: 1, player_id: 1, course_id: 1, cc: 150, payload: { time_str: '1:48.000' } },
      { ts: 2, type: 'pb', season_id: 1, player_id: 1, course_id: 1, cc: 150, payload: { time_str: '1:47.000' } },
    ]);
    const res = await app.request('/v1/activity?limit=10');
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.map((e: any) => e.payload.time_str)).toEqual(['1:47.000', '1:48.000']);
  });
});
