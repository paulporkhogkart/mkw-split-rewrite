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

describe('ghost import', () => {
  it('enriches a carryover match and does NOT announce a PB', async () => {
    const { app, db, token, events } = ctx();
    db.exec("INSERT INTO runs(id,attempt_id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,total_time_str,is_pb) " +
            "VALUES (70,'cv',1,1,1,150,'finished','carryover',100000,'1:40.000',1)");
    const before = events.length;
    const res = await post(app, token, { attempt_id: 'g1', course: 'Rainbow Road', status: 'finished',
      total_time: '1:40.000', source: 'ghost', character: 'Mario', total_laps: 1,
      laps: [{ lap: 1, time_ms: 100000, time_str: '1:40.000', coins: 3, shrooms: 1 }],
      points: [[0, 1, 2, 1, 1]] });
    const body = await res.json();
    expect(body.deduped).toBe(true);
    expect(events.slice(before).some(e => e.type === 'pb_achieved')).toBe(false);
    expect(events.slice(before).some(e => e.type === 'run_finished')).toBe(false);
    expect((db.prepare("SELECT character FROM runs WHERE id=70").get() as any).character).toBeTruthy();
    expect((db.prepare("SELECT COUNT(*) c FROM runs WHERE provenance='live'").get() as any).c).toBe(0);  // no new row
    expect((db.prepare("SELECT action FROM ghost_imports").get() as any).action).toBe('enriched');
  });

  it('inserts + announces when no match exists', async () => {
    const { app, db, token, events } = ctx();
    const before = events.length;
    const res = await post(app, token, { attempt_id: 'g2', course: 'Rainbow Road', status: 'finished',
      total_time: '1:30.000', source: 'ghost', character: 'Mario', kart: 'K', total_laps: 1, laps: [], points: [] });
    const body = await res.json();
    expect(body.is_pb).toBe(true);
    expect(events.slice(before).some(e => e.type === 'pb_achieved')).toBe(true);
    expect((db.prepare("SELECT source FROM runs WHERE attempt_id='g2'").get() as any).source).toBe('ghost');
    expect((db.prepare("SELECT action FROM ghost_imports").get() as any).action).toBe('new');
  });
});

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
