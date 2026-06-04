import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from '../db/connect';
import { mintToken } from '../db/players';
import { EventHub } from './events';
import { createApp } from './app';
import type { ServerEvent } from '../db/types';

function ctx() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 1',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rainbow_road','Rainbow Road')");
  db.exec("INSERT INTO season_rosters(season_id,player_id) VALUES (1,1)");
  const hub = new EventHub();
  const events: ServerEvent[] = [];
  hub.subscribe(e => events.push(e));
  return { app: createApp(db, hub), token: mintToken(db, 'Paul'), db, events };
}

function post(app: any, token: string, body: unknown) {
  return app.request('/v1/runs', {
    method: 'POST',
    headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` },
    body: JSON.stringify(body),
  });
}

describe('POST /v1/runs', () => {
  it('ingests a finished run, marks PB, returns result, emits events', async () => {
    const { app, token, db, events } = ctx();
    const res = await post(app, token, { attempt_id: 'a1', course: 'Rainbow Road', cc: 150, status: 'finished', total_time: '1:50.000' });
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body).toMatchObject({ is_pb: true, rank: 1 });
    expect((db.prepare("SELECT COUNT(*) c FROM runs WHERE provenance='live'").get() as any).c).toBe(1);
    expect(events.some(e => e.type === 'pb_achieved')).toBe(true);
    expect(events.some(e => e.type === 'run_finished')).toBe(true);
  });

  it('400s on an unknown course', async () => {
    const { app, token } = ctx();
    const res = await post(app, token, { attempt_id: 'b1', course: 'Nonexistent Track', status: 'finished', total_time: '1:00.000' });
    expect(res.status).toBe(400);
  });

  it('reset uploads silently (no events, stored)', async () => {
    const { app, token, events, db } = ctx();
    const res = await post(app, token, { attempt_id: 'r1', course: 'Rainbow Road', status: 'reset' });
    expect(res.status).toBe(200);
    expect(events.length).toBe(0);
    expect((db.prepare("SELECT status FROM runs WHERE attempt_id='r1'").get() as any).status).toBe('reset');
  });
});
