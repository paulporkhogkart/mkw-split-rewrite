import { describe, it, expect } from 'vitest';
import { DatabaseSync } from 'node:sqlite';
import { applySchema } from '../db/connect';
import { PresenceHub, type PresenceEntry } from './hub';

function db() {
  const d = new DatabaseSync(':memory:'); applySchema(d);
  d.exec(`INSERT INTO seasons(id,name,is_active) VALUES(1,'S1',1);
          INSERT INTO players(id,display_name,color) VALUES(1,'Paul','#a78bfa'),(2,'Luke','#f87171');
          INSERT INTO season_rosters(season_id,player_id) VALUES(1,1),(1,2);`);
  return d;
}
const noCompletion = () => ({ completion: null, dividers: [] });
const noPace = () => null;
const noLaps = () => null;

describe('PresenceHub', () => {
  it('seeds the roster offline and snapshots it to a new sink', () => {
    const hub = new PresenceHub(db(), noCompletion, noPace, noLaps, () =>1000);
    const got: any[] = [];
    hub.addSink((m) => got.push(m));
    expect(got[0].type).toBe('presence_snapshot');
    expect(got[0].players.map((p: any) => [p.name, p.online])).toEqual([['Paul', false], ['Luke', false]]);
  });

  it('a frame marks the player online + broadcasts a delta with enriched completion', () => {
    const hub = new PresenceHub(db(), (_c, _l, pos) => ({ completion: pos ? 0.5 : null, dividers: [] }), noPace, noLaps, () =>2000);
    const got: any[] = [];
    hub.addSink((m) => got.push(m));                         // got[0] = snapshot
    hub.update(2, { screen: 'RACING', course: 'bc', cur_lap: 2, coins: 7, pos: [1, 2] });
    expect(got[1].type).toBe('presence_update');
    expect(got[1].player).toMatchObject({ player_id: 2, name: 'Luke', online: true, screen: 'RACING', coins: 7, completion: 0.5, updated_at: 2000 });
  });

  it('ignores frames from non-roster players', () => {
    const hub = new PresenceHub(db(), noCompletion);
    const got: any[] = [];
    hub.addSink((m) => got.push(m));
    hub.update(999, { screen: 'RACING' });
    expect(got).toHaveLength(1);                             // only the snapshot
  });

  it('setOffline / sweep flip a stale player offline (idempotently)', () => {
    let t = 1000;
    const hub = new PresenceHub(db(), () => ({ completion: null, dividers: [] }), noPace, noLaps, () =>t);
    const got: any[] = [];
    hub.addSink((m) => got.push(m));
    hub.update(1, { screen: 'MAIN_MENU' });                  // Paul online @1000
    expect(got.at(-1).player.online).toBe(true);
    t += 20000;
    hub.sweep(15000);                                        // 20s stale > 15s -> offline
    expect(got.at(-1).player).toMatchObject({ player_id: 1, online: false });
    hub.sweep(15000);                                        // already offline -> no new broadcast
    expect(got.filter((m) => m.type === 'presence_update' && m.player.player_id === 1 && !m.player.online)).toHaveLength(1);
  });

  it('passes resets through and enriches pb_ms for the current course', () => {
    const d = db();
    d.exec(`INSERT INTO courses(id,slug,display_name) VALUES(7,'rainbow_road','Rainbow Road');
            INSERT INTO runs(season_id,player_id,course_id,cc,status,total_time_ms,is_pb,provenance)
              VALUES(1,1,7,150,'finished',79880,1,'live');`);
    const hub = new PresenceHub(d, () => ({ completion: null, dividers: [] }), noPace, noLaps, () =>5000);
    const got: any[] = [];
    hub.addSink((m) => got.push(m));
    hub.update(1, { screen: 'RACING', course: 'Rainbow Road', resets: 3 });
    expect(got.at(-1).player).toMatchObject({ player_id: 1, resets: 3, pb_ms: 79880 });
  });

  it('passes elapsed_ms through (present -> value, absent -> null)', () => {
    const hub = new PresenceHub(db(), noCompletion, noPace, noLaps, () =>2000);
    const got: any[] = [];
    hub.addSink((m) => got.push(m));
    hub.update(1, { screen: 'RACING', elapsed_ms: 12345 });
    expect(got.at(-1).player).toMatchObject({ elapsed_ms: 12345 });
    hub.update(2, { screen: 'RACING' });
    expect(got.at(-1).player).toMatchObject({ elapsed_ms: null });
  });

  it('offline entries carry career stats; online ones do not', () => {
    const d = db();
    d.exec(`INSERT INTO courses(id,slug,display_name) VALUES(7,'rainbow_road','Rainbow Road');
            INSERT INTO runs(season_id,player_id,course_id,cc,status,total_time_ms,is_pb,was_pb,provenance,ended_at)
              VALUES(1,1,7,150,'finished',79880,1,1,'live',datetime('now')),
                    (1,2,7,150,'finished',81000,1,1,'live',datetime('now')),
                    (1,1,7,150,'reset',NULL,0,0,'live',datetime('now'));`);
    const hub = new PresenceHub(d, noCompletion, noPace, noLaps, () => 1000);
    const got: any[] = [];
    hub.addSink((m) => got.push(m));
    const paul = got[0].players.find((p: any) => p.player_id === 1);
    const luke = got[0].players.find((p: any) => p.player_id === 2);
    expect(paul.off_stats).toEqual({ firsts: 1, runs_7d: 2, pbs_30d: 1 });   // holds the #1
    expect(luke.off_stats).toEqual({ firsts: 0, runs_7d: 1, pbs_30d: 1 });
    hub.update(1, { screen: 'MAIN_MENU' });
    expect(got.at(-1).player.off_stats).toBeNull();                          // online: not carried
    hub.setOffline(1);
    expect(got.at(-1).player.off_stats).toEqual({ firsts: 1, runs_7d: 2, pbs_30d: 1 });
  });

  it('seeds offline entries with updated_at 0 (never seen)', () => {
    const hub = new PresenceHub(db(), noCompletion, noPace, noLaps, () =>1000);
    const got: any[] = [];
    hub.addSink((m) => got.push(m));
    expect(got[0].players[0].updated_at).toBe(0);
  });

  it('maps a non-fresh track_state to a held (stale) completion', () => {
    const seen: boolean[] = [];
    const hub = new PresenceHub(db(), (_c, _l, _p, _pid, _t, stale) => { seen.push(!!stale); return { completion: stale ? 0.9 : 0.1, dividers: [] }; }, noPace, noLaps, () =>3000);
    hub.addSink(() => {});
    hub.update(1, { screen: 'RACING', course: 'bc', cur_lap: 2, pos: [1, 2], track_state: 'reacquire' });
    expect(seen).toEqual([true]);
  });

  it('pins pb_ms for the race so the finished delta reads against the pre-race PB', () => {
    const d = db();
    d.exec(`INSERT INTO courses(id,slug,display_name) VALUES(7,'rainbow_road','Rainbow Road');
            INSERT INTO runs(id,season_id,player_id,course_id,cc,status,total_time_ms,is_pb,provenance)
              VALUES(50,1,1,7,150,'finished',79880,1,'live');`);
    const hub = new PresenceHub(d, noCompletion, noPace, noLaps, () =>5000);
    const got: any[] = [];
    hub.addSink((m) => got.push(m));
    hub.update(1, { screen: 'RACING', course: 'Rainbow Road' });
    expect(got.at(-1).player.pb_ms).toBe(79880);
    // The finish upload lands a faster PB while the player is still on the result.
    d.exec(`UPDATE runs SET is_pb=0 WHERE id=50;
            INSERT INTO runs(id,season_id,player_id,course_id,cc,status,total_time_ms,is_pb,provenance)
              VALUES(51,1,1,7,150,'finished',78000,1,'live');`);
    hub.update(1, { screen: 'RACING', course: 'Rainbow Road', final_time: '1:18.000' });
    expect(got.at(-1).player.pb_ms).toBe(79880);     // held through the finish
    hub.update(1, { screen: 'POST_TIME_TRIAL', course: 'Rainbow Road' });
    expect(got.at(-1).player.pb_ms).toBe(79880);     // and the results screen
    hub.update(1, { screen: 'MAIN_MENU', course: 'Rainbow Road' });
    expect(got.at(-1).player.pb_ms).toBe(78000);     // off-race: live again
    hub.update(1, { screen: 'RACING', course: 'Rainbow Road' });
    expect(got.at(-1).player.pb_ms).toBe(78000);     // the next start picks it up
  });

  it('attaches pb_delta_ms while racing; clears it once finished or off the track', () => {
    const calls: unknown[] = [];
    const pace = (pid: number, course: unknown, completion: unknown, elapsed: unknown) => {
      calls.push([pid, course, completion, elapsed]); return 1234;
    };
    const hub = new PresenceHub(db(), () => ({ completion: 0.5, dividers: [] }), pace, noLaps, () => 1000);
    const got: any[] = [];
    hub.addSink((m) => got.push(m));
    hub.update(1, { screen: 'RACING', course: 'bc', pos: [1, 2], elapsed_ms: 41000 });
    expect(got.at(-1).player.pb_delta_ms).toBe(1234);
    expect(calls.at(-1)).toEqual([1, 'bc', 0.5, 41000]);   // the broadcast completion + race clock
    hub.update(1, { screen: 'RACING', course: 'bc', elapsed_ms: 80000, final_time: '1:19.880' });
    expect(got.at(-1).player.pb_delta_ms).toBeNull();      // finished -> the exact delta takes over
    hub.update(1, { screen: 'MAIN_MENU' });
    expect(got.at(-1).player.pb_delta_ms).toBeNull();
  });

  it('attaches lap deltas + PB laps while racing from the frame splits', () => {
    const calls: unknown[] = [];
    const laps = (pid: number, course: unknown, splits: unknown) => {
      calls.push([pid, course, splits]);
      return { pb_laps_ms: [51000, 52000], deltas: [
        { lap: 1, delta_ms: 300, gained: false, gold: false },
        { lap: 2, delta_ms: -800, gained: true, gold: false }] };
    };
    const hub = new PresenceHub(db(), noCompletion, noPace, laps, () => 1000);
    const got: any[] = [];
    hub.addSink((m) => got.push(m));
    hub.update(1, { screen: 'RACING', course: 'bc', splits_ms: [51294, 50764] });
    expect(got.at(-1).player.lap_delta).toEqual({ lap: 2, delta_ms: -800, gained: true, gold: false });
    expect(got.at(-1).player.lap_deltas).toHaveLength(2);
    expect(got.at(-1).player.pb_laps_ms).toEqual([51000, 52000]);
    expect(calls.at(-1)).toEqual([1, 'bc', [51294, 50764]]);
    // The comparison stays up through the finished state (the rail reads it there)...
    hub.update(1, { screen: 'RACING', course: 'bc', final_time: '2:32.168', splits_ms: [51294, 50764] });
    expect(got.at(-1).player.lap_deltas).toHaveLength(2);
    // ...and drops once the player leaves the race context.
    hub.update(1, { screen: 'MAIN_MENU' });
    expect(got.at(-1).player.lap_deltas).toBeNull();
    expect(got.at(-1).player.lap_delta).toBeNull();
  });

  it('passes the model-derived dividers from the completion provider straight through', () => {
    const seen: PresenceEntry[] = [];
    // The provider returns the model's known interior boundaries (constant per course); the hub
    // just forwards them — they appear on the very first frame, not only as laps complete.
    const hub = new PresenceHub(db(), () => ({ completion: 0.3, dividers: [0.33, 0.66] }), noPace, noLaps, () =>1000);
    hub.addSink((m: any) => { if (m.type === 'presence_update') seen.push(m.player); });
    hub.update(1, { screen: 'RACING', course: 'bc', cur_lap: 1, tot_lap: 3, pos: [1, 2] });
    expect(seen.at(-1)!.dividers).toEqual([0.33, 0.66]);   // present from the first frame (lap 1)
  });
});
