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

describe('PresenceHub', () => {
  it('seeds the roster offline and snapshots it to a new sink', () => {
    const hub = new PresenceHub(db(), noCompletion, noPace, () => 1000);
    const got: any[] = [];
    hub.addSink((m) => got.push(m));
    expect(got[0].type).toBe('presence_snapshot');
    expect(got[0].players.map((p: any) => [p.name, p.online])).toEqual([['Paul', false], ['Luke', false]]);
  });

  it('a frame marks the player online + broadcasts a delta with enriched completion', () => {
    const hub = new PresenceHub(db(), (_c, _l, pos) => ({ completion: pos ? 0.5 : null, dividers: [] }), noPace, () => 2000);
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
    const hub = new PresenceHub(db(), () => ({ completion: null, dividers: [] }), noPace, () => t);
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
    const hub = new PresenceHub(d, () => ({ completion: null, dividers: [] }), noPace, () => 5000);
    const got: any[] = [];
    hub.addSink((m) => got.push(m));
    hub.update(1, { screen: 'RACING', course: 'Rainbow Road', resets: 3 });
    expect(got.at(-1).player).toMatchObject({ player_id: 1, resets: 3, pb_ms: 79880 });
  });

  it('passes elapsed_ms through (present -> value, absent -> null)', () => {
    const hub = new PresenceHub(db(), noCompletion, noPace, () => 2000);
    const got: any[] = [];
    hub.addSink((m) => got.push(m));
    hub.update(1, { screen: 'RACING', elapsed_ms: 12345 });
    expect(got.at(-1).player).toMatchObject({ elapsed_ms: 12345 });
    hub.update(2, { screen: 'RACING' });
    expect(got.at(-1).player).toMatchObject({ elapsed_ms: null });
  });

  it('seeds offline entries with updated_at 0 (never seen)', () => {
    const hub = new PresenceHub(db(), noCompletion, noPace, () => 1000);
    const got: any[] = [];
    hub.addSink((m) => got.push(m));
    expect(got[0].players[0].updated_at).toBe(0);
  });

  it('maps a non-fresh track_state to a held (stale) completion', () => {
    const seen: boolean[] = [];
    const hub = new PresenceHub(db(), (_c, _l, _p, _pid, _t, stale) => { seen.push(!!stale); return { completion: stale ? 0.9 : 0.1, dividers: [] }; }, noPace, () => 3000);
    hub.addSink(() => {});
    hub.update(1, { screen: 'RACING', course: 'bc', cur_lap: 2, pos: [1, 2], track_state: 'reacquire' });
    expect(seen).toEqual([true]);
  });

  it('attaches pb_delta_ms while racing; clears it once finished or off the track', () => {
    const calls: unknown[] = [];
    const pace = (pid: number, course: unknown, completion: unknown, elapsed: unknown) => {
      calls.push([pid, course, completion, elapsed]); return 1234;
    };
    const hub = new PresenceHub(db(), () => ({ completion: 0.5, dividers: [] }), pace, () => 1000);
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

  it('passes the model-derived dividers from the completion provider straight through', () => {
    const seen: PresenceEntry[] = [];
    // The provider returns the model's known interior boundaries (constant per course); the hub
    // just forwards them — they appear on the very first frame, not only as laps complete.
    const hub = new PresenceHub(db(), () => ({ completion: 0.3, dividers: [0.33, 0.66] }), noPace, () => 1000);
    hub.addSink((m: any) => { if (m.type === 'presence_update') seen.push(m.player); });
    hub.update(1, { screen: 'RACING', course: 'bc', cur_lap: 1, tot_lap: 3, pos: [1, 2] });
    expect(seen.at(-1)!.dividers).toEqual([0.33, 0.66]);   // present from the first frame (lap 1)
  });
});
