import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from '../db/connect';
import { mintToken } from '../db/players';
import { EventHub } from './events';
import { ActivityHub } from '../activity/hub';
import { SessionTracker, type SessionView } from '../activity/sessionTracker';
import { createApp } from './app';
import type { ActivityEvent } from '../activity/types';

const last = <T,>(a: T[]): T => a[a.length - 1];

function ctx() {
  const db = openDb(':memory:'); applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
  db.exec("INSERT INTO players(id,display_name,color) VALUES (1,'Gub','#38bdf8'),(2,'Paul','#a78bfa')");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'crown_city','Crown City')");
  db.exec("INSERT INTO season_rosters(season_id,player_id) VALUES (1,1),(1,2)");
  const hub = new EventHub(); const act = new ActivityHub();
  // Milestone events (kind:'event') only; sessions are asserted via the tracker captures.
  const seen: ActivityEvent[] = [];
  act.subscribe(m => { if (m.kind === 'event') seen.push(m.event); });
  const sOpens: SessionView[] = []; const sFinals: SessionView[] = [];
  const sessionTracker = new SessionTracker({
    now: () => Date.now(),
    emitOpen: v => sOpens.push(v), emitFinal: v => sFinals.push(v), emitDrop: () => {},
  });
  return { db, app: createApp(db, hub, undefined, { activity: act, sessionTracker }), act, seen,
           sOpens, sFinals, sessionTracker, gub: mintToken(db, 'Gub'), paul: mintToken(db, 'Paul') };
}
const post = (app: any, token: string, body: unknown) => app.request('/v1/runs', {
  method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` },
  body: JSON.stringify(body) });

describe('run -> activity cascade', () => {
  it('a PB that takes #1 writes pb + rank + turf_claim (ascending stream), no attempts row', async () => {
    const { app, db, seen, gub, paul } = ctx();
    await post(app, paul, { attempt_id: 'p1', course: 'Crown City', status: 'finished', total_time: '1:48.221' });
    seen.length = 0;
    await post(app, gub, { attempt_id: 'g1', course: 'Crown City', status: 'finished', total_time: '1:47.980' });
    expect(seen.map(e => e.type)).toEqual(['pb', 'rank', 'turf_claim']);
    expect(seen[0].player!.name).toBe('Gub');                 // pb actor
    expect((seen[1].payload as any).place).toBe(1);           // rank: took 1st
    expect((seen[2].payload as any).rival.name).toBe('Paul'); // turf_claim dethroned Paul
    // Paul's PB (1 row) + Gub's pb+rank+turf_claim (3) = 4 milestone rows; none of type 'attempts'.
    expect((db.prepare('SELECT COUNT(*) c FROM activity_events').get() as any).c).toBe(4);
    expect((db.prepare("SELECT COUNT(*) c FROM activity_events WHERE type='attempts'").get() as any).c).toBe(0);
  });

  it('resets feed the racing session attempt count, not a cascade attempts row', async () => {
    const { app, seen, sOpens, sFinals, sessionTracker, paul } = ctx();
    await post(app, paul, { attempt_id: 'r1', course: 'Crown City', status: 'reset' });
    await post(app, paul, { attempt_id: 'r2', course: 'Crown City', status: 'reset' });
    expect(seen.length).toBe(0);                                 // resets emit no milestones
    expect(last(sOpens)).toMatchObject({ cls: 'racing', course_id: 1, attempts: 2 });
    await post(app, paul, { attempt_id: 'p1', course: 'Crown City', status: 'finished', total_time: '1:48.221' });
    expect(seen.map(e => e.type)).not.toContain('attempts');     // attempts lives on the session now
    expect(seen.map(e => e.type)[0]).toBe('pb');
    expect(last(sOpens)).toMatchObject({ attempts: 3 });         // the finish is the 3rd attempt
    // pbs is a finalise-time outcome ("no PB" / "new PB"): finalise Paul's session and check it banked the PB.
    sessionTracker.onOffline(2);                                 // Paul's player_id
    expect(last(sFinals)).toMatchObject({ cls: 'racing', attempts: 3, pbs: 1 });
  });
});
