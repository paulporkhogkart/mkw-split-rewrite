import { describe, it, expect } from 'vitest';
import { DatabaseSync } from 'node:sqlite';
import { applySchema } from '../db/connect';
import { PresenceHub, type PresenceEntry } from './hub';
import { makeLapDelta } from './lapDelta';
import type { LiveCompletion } from './completion';

function db() {
  const d = new DatabaseSync(':memory:'); applySchema(d);
  d.exec(`INSERT INTO seasons(id,name,is_active) VALUES(1,'S1',1);
          INSERT INTO players(id,display_name,color) VALUES(1,'Paul','#a78bfa'),(2,'Luke','#f87171');
          INSERT INTO season_rosters(season_id,player_id) VALUES(1,1),(1,2);`);
  return d;
}
const noCompletion: LiveCompletion = Object.assign(() => ({ completion: null, dividers: [], model: false }), { invalidate() {} });
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
    const hub = new PresenceHub(db(), (_c, _l, pos) => ({ completion: pos ? 0.5 : null, dividers: [], model: true }), noPace, noLaps, () =>2000);
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
    const hub = new PresenceHub(db(), noCompletion, noPace, noLaps, () =>t);
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

  it('stamps screen_since_ms when the activity class changes, holding it while it persists', () => {
    let t = 1000;
    const hub = new PresenceHub(db(), noCompletion, noPace, noLaps, () => t);
    const got: any[] = [];
    hub.addSink((m) => got.push(m));
    const since = () => got.at(-1).player.screen_since_ms;

    hub.update(1, { screen: 'CHARACTER_SELECT' });   // enters character-select @1000
    expect(since()).toBe(1000);
    t = 1500;
    hub.update(1, { screen: 'CHARACTER_SELECT' });   // same class -> unchanged
    expect(since()).toBe(1000);
    t = 2000;
    hub.update(1, { screen: 'KART_SELECT' });         // new class -> restamped
    expect(since()).toBe(2000);
    t = 3000;
    hub.update(1, { screen: 'RACING', course: 'bc' }); // starts the grind @3000
    expect(since()).toBe(3000);
    t = 3500;
    hub.update(1, { screen: 'RESET' });                // held screen continues racing -> unchanged
    expect(since()).toBe(3000);
    t = 4000;
    hub.update(1, { screen: 'RACING', course: 'bc' }); // back on track, same grind -> unchanged
    expect(since()).toBe(3000);
  });

  it('nulls screen_since_ms offline and restamps on the next login', () => {
    let t = 1000;
    const hub = new PresenceHub(db(), noCompletion, noPace, noLaps, () => t);
    const got: any[] = [];
    hub.addSink((m) => got.push(m));
    hub.update(1, { screen: 'RACING', course: 'bc' });
    expect(got.at(-1).player.screen_since_ms).toBe(1000);
    t = 2000;
    hub.setOffline(1);
    expect(got.at(-1).player.screen_since_ms).toBeNull();
    t = 3000;
    hub.update(1, { screen: 'RACING', course: 'bc' });   // fresh login -> new stamp
    expect(got.at(-1).player.screen_since_ms).toBe(3000);
  });

  it('fires onLogin on the first frame and onLogout on going offline (idempotently)', () => {
    const logins: number[] = []; const logouts: number[] = [];
    const sink = { onFrame() {}, onOffline() {},
                   onLogin: (id: number) => logins.push(id), onLogout: (id: number) => logouts.push(id) };
    const hub = new PresenceHub(db(), noCompletion, noPace, noLaps, () => 1000, sink);
    hub.update(1, { screen: 'MAIN_MENU' });   // first frame -> login
    hub.update(1, { screen: 'RACING' });      // still online -> no second login
    expect(logins).toEqual([1]);
    hub.setOffline(1);                         // -> logout
    hub.setOffline(1);                         // already offline -> no second logout
    expect(logouts).toEqual([1]);
  });

  it('passes resets through and enriches pb_ms for the current course', () => {
    const d = db();
    d.exec(`INSERT INTO courses(id,slug,display_name) VALUES(7,'rainbow_road','Rainbow Road');
            INSERT INTO runs(season_id,player_id,course_id,cc,status,total_time_ms,is_pb,provenance)
              VALUES(1,1,7,150,'finished',79880,1,'live');`);
    const hub = new PresenceHub(d, noCompletion, noPace, noLaps, () =>5000);
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

  it('passes the dnf (timeout) flag through and stops treating the card as racing', () => {
    const pace = () => 1234;                                  // a non-null pace == "thinks it is racing"
    const hub = new PresenceHub(db(), noCompletion, pace, noLaps, () => 2000);
    const got: any[] = [];
    hub.addSink((m) => got.push(m));
    hub.update(1, { screen: 'RACING', course: 'Rainbow Road', final_time: null, dnf: true });
    expect(got.at(-1).player).toMatchObject({ player_id: 1, dnf: true });
    expect(got.at(-1).player.pb_delta_ms).toBe(null);         // timed out -> not racing -> no live pace
    hub.update(2, { screen: 'RACING', course: 'Rainbow Road', final_time: null });
    expect(got.at(-1).player).toMatchObject({ dnf: false, pb_delta_ms: 1234 });
  });

  it('career stats ride offline + idle-online entries; a racing card drops them', () => {
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
    // Online but idle (no race to show): the pre-selection card carries the same stats.
    expect(got.at(-1).player.off_stats).toEqual({ firsts: 1, runs_7d: 2, pbs_30d: 1 });
    hub.update(1, { screen: 'RACING', course: 'Rainbow Road' });
    expect(got.at(-1).player.off_stats).toBeNull();                          // racing: the live readout replaces them
    hub.update(1, { screen: 'POST_TIME_TRIAL', course: 'Rainbow Road' });
    expect(got.at(-1).player.off_stats).toBeNull();                          // results screen too
    hub.setOffline(1);
    expect(got.at(-1).player.off_stats).toEqual({ firsts: 1, runs_7d: 2, pbs_30d: 1 });
  });

  it('refreshOffStats rebroadcasts offline entries whose standings changed', () => {
    const d = db();
    d.exec(`INSERT INTO courses(id,slug,display_name) VALUES(7,'rr','RR');
            INSERT INTO runs(season_id,player_id,course_id,cc,status,total_time_ms,is_pb,was_pb,provenance,ended_at)
              VALUES(1,1,7,150,'finished',80000,1,1,'live',datetime('now'));`);
    const hub = new PresenceHub(d, noCompletion, noPace, noLaps, () => 1000);
    const got: any[] = [];
    hub.addSink((m) => got.push(m));
    expect(got[0].players.find((p: any) => p.player_id === 1).off_stats.firsts).toBe(1);
    // Luke uploads a faster run while Paul is offline: Paul loses the #1.
    d.exec(`UPDATE runs SET is_pb=0;
            INSERT INTO runs(season_id,player_id,course_id,cc,status,total_time_ms,is_pb,was_pb,provenance,ended_at)
              VALUES(1,2,7,150,'finished',79000,1,1,'live',datetime('now'));`);
    hub.refreshOffStats();
    const paul = got.filter((m: any) => m.type === 'presence_update' && m.player.player_id === 1);
    expect(paul.at(-1).player.off_stats.firsts).toBe(0);
    const n = got.length;
    hub.refreshOffStats();                       // nothing changed -> no rebroadcast
    expect(got.length).toBe(n);
  });

  it('seeds offline entries with updated_at 0 (never seen)', () => {
    const hub = new PresenceHub(db(), noCompletion, noPace, noLaps, () =>1000);
    const got: any[] = [];
    hub.addSink((m) => got.push(m));
    expect(got[0].players[0].updated_at).toBe(0);
  });

  it('seeds offline updated_at from the stored last_seen_at (restores after a restart)', () => {
    const d = db();
    d.prepare('UPDATE players SET last_seen_at=? WHERE id=?').run(1717000000000, 1);
    const hub = new PresenceHub(d, noCompletion, noPace, noLaps, () => 9999999999999);
    const got: any[] = [];
    hub.addSink((m) => got.push(m));
    const paul = got[0].players.find((p: any) => p.player_id === 1);
    const luke = got[0].players.find((p: any) => p.player_id === 2);
    expect(paul.updated_at).toBe(1717000000000);   // restored from the db
    expect(luke.updated_at).toBe(0);               // NULL last_seen_at -> 0 (never seen)
  });

  it('maps a non-fresh track_state to a held (stale) completion', () => {
    const seen: boolean[] = [];
    const hub = new PresenceHub(db(), (_c, _l, _p, _pid, _t, stale) => { seen.push(!!stale); return { completion: stale ? 0.9 : 0.1, dividers: [], model: true }; }, noPace, noLaps, () =>3000);
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
    const hub = new PresenceHub(db(), () => ({ completion: 0.5, dividers: [], model: true }), pace, noLaps, () => 1000);
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
        { lap: 1, delta_ms: 300, seg_delta_ms: 300, gained: false, gold: false },
        { lap: 2, delta_ms: -800, seg_delta_ms: -1100, gained: true, gold: false }] };
    };
    const hub = new PresenceHub(db(), noCompletion, noPace, laps, () => 1000);
    const got: any[] = [];
    hub.addSink((m) => got.push(m));
    hub.update(1, { screen: 'RACING', course: 'bc', splits_ms: [51294, 50764] });
    expect(got.at(-1).player.lap_delta).toEqual({ lap: 2, delta_ms: -800, seg_delta_ms: -1100, gained: true, gold: false });
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

  it('pins the lap gold across the finish upload, refreshing only at the next race', () => {
    const d = db();
    d.exec(`INSERT INTO courses(id,slug,display_name) VALUES(7,'rainbow_road','Rainbow Road');
            INSERT INTO runs(id,season_id,player_id,course_id,cc,status,total_time_ms,is_pb,provenance) VALUES
              (40,1,1,7,150,'finished',62000,0,'live'),   -- holds the lap-1 gold (20000)
              (41,1,1,7,150,'finished',60500,1,'live');   -- the PB run
            INSERT INTO run_laps(run_id,lap_index,lap_time_ms) VALUES
              (40,1,20000),(40,2,21000),(40,3,21000),
              (41,1,20500),(41,2,20000),(41,3,20000);`);
    const laps = makeLapDelta(d);
    const hub = new PresenceHub(d, noCompletion, noPace, laps, () => 1000);
    const got: any[] = [];
    hub.addSink((m) => got.push(m));
    // Racing: live lap 1 (19000) beats the pre-race best-ever (20000) -> gold.
    hub.update(1, { screen: 'RACING', course: 'Rainbow Road', splits_ms: [19000] });
    expect(got.at(-1).player.lap_deltas[0].gold).toBe(true);
    // The finish uploads a run whose lap 1 (19000) becomes the db best, and the
    // model-rebuild hook drops the lap cache.
    d.exec(`UPDATE runs SET is_pb=0 WHERE id=41;
            INSERT INTO runs(id,season_id,player_id,course_id,cc,status,total_time_ms,is_pb,provenance)
              VALUES(42,1,1,7,150,'finished',58000,1,'live');
            INSERT INTO run_laps(run_id,lap_index,lap_time_ms) VALUES (42,1,19000),(42,2,20000),(42,3,19000);`);
    laps.invalidateCourse(7);
    // Still on the results screen (in race context): the gold must hold.
    hub.update(1, { screen: 'POST_TIME_TRIAL', course: 'Rainbow Road', final_time: '0:58.000', splits_ms: [19000] });
    expect(got.at(-1).player.lap_deltas[0].gold).toBe(true);
    // Back to the menus, then a fresh race: the baseline now includes the uploaded
    // run, so re-running 19000 only ties the db best -> no longer a fresh gold.
    hub.update(1, { screen: 'MAIN_MENU', course: 'Rainbow Road' });
    hub.update(1, { screen: 'RACING', course: 'Rainbow Road', splits_ms: [19000] });
    expect(got.at(-1).player.lap_deltas[0].gold).toBe(false);
  });

  it('passes the model-derived dividers from the completion provider straight through', () => {
    const seen: PresenceEntry[] = [];
    // The provider returns the model's known interior boundaries (constant per course); the hub
    // just forwards them — they appear on the very first frame, not only as laps complete.
    const hub = new PresenceHub(db(), () => ({ completion: 0.3, dividers: [0.33, 0.66], model: true }), noPace, noLaps, () =>1000);
    hub.addSink((m: any) => { if (m.type === 'presence_update') seen.push(m.player); });
    hub.update(1, { screen: 'RACING', course: 'bc', cur_lap: 1, tot_lap: 3, pos: [1, 2] });
    expect(seen.at(-1)!.dividers).toEqual([0.33, 0.66]);   // present from the first frame (lap 1)
  });

  it('persists last_seen_at to the db on disconnect', () => {
    const d = db();
    const hub = new PresenceHub(d, noCompletion, noPace, noLaps, () => 4242);
    hub.addSink(() => {});
    hub.update(1, { screen: 'MAIN_MENU' });   // Paul online
    hub.setOffline(1);                        // -> persists 4242
    expect((d.prepare('SELECT last_seen_at FROM players WHERE id=1').get() as { last_seen_at: number }).last_seen_at)
      .toBe(4242);
  });

  it('persists last_seen_at on the offline->online transition only', () => {
    const d = db();
    let t = 1000;
    const hub = new PresenceHub(d, noCompletion, noPace, noLaps, () => t);
    hub.addSink(() => {});
    hub.update(1, { screen: 'MAIN_MENU' });    // offline -> online: persists 1000
    expect((d.prepare('SELECT last_seen_at FROM players WHERE id=1').get() as { last_seen_at: number }).last_seen_at)
      .toBe(1000);
    t = 2000;
    hub.update(1, { screen: 'RACING' });       // already online: NO db write
    expect((d.prepare('SELECT last_seen_at FROM players WHERE id=1').get() as { last_seen_at: number }).last_seen_at)
      .toBe(1000);                             // still the connect value
  });

  it('round-trips last-seen across a restart (persist on offline, restore on a new hub)', () => {
    const d = db();
    const hub1 = new PresenceHub(d, noCompletion, noPace, noLaps, () => 555000);
    hub1.addSink(() => {});
    hub1.update(1, { screen: 'MAIN_MENU' });   // online
    hub1.setOffline(1);                        // persists 555000
    const hub2 = new PresenceHub(d, noCompletion, noPace, noLaps, () => 999999);   // "restart"
    const got: any[] = [];
    hub2.addSink((m) => got.push(m));
    expect(got[0].players.find((p: any) => p.player_id === 1).updated_at).toBe(555000);
  });

  it('persistLastSeen flushes online entries only', () => {
    const d = db();
    let t = 7000;
    const hub = new PresenceHub(d, noCompletion, noPace, noLaps, () => t);
    hub.addSink(() => {});
    hub.update(1, { screen: 'RACING' });   // Paul online @7000 (connect write = 7000)
    t = 8000;
    hub.update(1, { screen: 'RACING' });   // still online @8000 (updated_at advances; no db write)
    hub.persistLastSeen();                 // flush: Paul -> 8000; Luke (never connected) untouched
    expect((d.prepare('SELECT last_seen_at FROM players WHERE id=1').get() as { last_seen_at: number }).last_seen_at)
      .toBe(8000);
    expect((d.prepare('SELECT last_seen_at FROM players WHERE id=2').get() as { last_seen_at: number | null }).last_seen_at)
      .toBe(null);
  });

  it('persists app_version to players on change; absent never clobbers', () => {
    const d = db();
    const hub = new PresenceHub(d, noCompletion, noPace, noLaps, () => 1000);
    hub.addSink(() => {});
    const ver = () => (d.prepare('SELECT app_version FROM players WHERE id=1').get() as { app_version: string | null }).app_version;
    hub.update(1, { screen: 'MAIN_MENU', app_version: '2.1.0' });
    expect(ver()).toBe('2.1.0');
    hub.update(1, { screen: 'MAIN_MENU', app_version: '2.2.0' });   // changed -> rewritten
    expect(ver()).toBe('2.2.0');
    hub.update(1, { screen: 'RACING' });                            // absent -> unchanged
    expect(ver()).toBe('2.2.0');
  });
});
