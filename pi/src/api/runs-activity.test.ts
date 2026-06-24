import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from '../db/connect';
import { mintToken } from '../db/players';
import { EventHub } from './events';
import { ActivityHub } from '../activity/hub';
import { createApp } from './app';
import type { ActivityEvent } from '../activity/types';

function ctx() {
  const db = openDb(':memory:'); applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
  db.exec("INSERT INTO players(id,display_name,color) VALUES (1,'Gub','#38bdf8'),(2,'Paul','#a78bfa')");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'crown_city','Crown City')");
  db.exec("INSERT INTO season_rosters(season_id,player_id) VALUES (1,1),(1,2)");
  const hub = new EventHub(); const act = new ActivityHub();
  const seen: ActivityEvent[] = []; act.subscribe(e => seen.push(e));
  return { db, app: createApp(db, hub, undefined, { activity: act }), act, seen,
           gub: mintToken(db, 'Gub'), paul: mintToken(db, 'Paul') };
}
const post = (app: any, token: string, body: unknown) => app.request('/v1/runs', {
  method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` },
  body: JSON.stringify(body) });

describe('run -> activity cascade', () => {
  it('a PB that takes #1 writes pb + rank + turf_claim (ascending stream)', async () => {
    const { app, db, seen, gub, paul } = ctx();
    await post(app, paul, { attempt_id: 'p1', course: 'Crown City', status: 'finished', total_time: '1:48.221' });
    seen.length = 0;
    await post(app, gub, { attempt_id: 'g1', course: 'Crown City', status: 'finished', total_time: '1:47.980' });
    // clean first-try PBs emit NO attempts row; burst = pb + rank + turf_claim only
    expect(seen.map(e => e.type)).toEqual(['pb', 'rank', 'turf_claim']);
    expect(seen[0].player!.name).toBe('Gub');                 // pb actor
    expect((seen[1].payload as any).place).toBe(1);           // rank: took 1st
    expect((seen[2].payload as any).rival.name).toBe('Paul'); // turf_claim dethroned Paul
    // Paul's first run fires cascade (pb only = 1 row); Gub's adds 3 more → 4 total
    expect((db.prepare('SELECT COUNT(*) c FROM activity_events').get() as any).c).toBe(4);
    expect((db.prepare('SELECT COUNT(*) c FROM activity_events WHERE player_id=1').get() as any).c).toBe(3);
  });

  it('prior resets before a PB emit an attempts row with count = reset count', async () => {
    const { app, seen, paul } = ctx();
    // two resets then a PB — resets emit no events themselves
    await post(app, paul, { attempt_id: 'r1', course: 'Crown City', status: 'reset' });
    await post(app, paul, { attempt_id: 'r2', course: 'Crown City', status: 'reset' });
    expect(seen.length).toBe(0);
    await post(app, paul, { attempt_id: 'p1', course: 'Crown City', status: 'finished', total_time: '1:48.221' });
    // burst starts with attempts (2 prior resets), then pb
    expect(seen.map(e => e.type)[0]).toBe('attempts');
    expect(seen.map(e => e.type)[1]).toBe('pb');
    expect((seen[0].payload as any).count).toBe(2);
  });
});
