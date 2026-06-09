import { describe, it, expect } from 'vitest';
import { DatabaseSync } from 'node:sqlite';
import { applySchema } from '../db/connect';
import { PresenceHub } from './hub';

function db() {
  const d = new DatabaseSync(':memory:'); applySchema(d);
  d.exec(`INSERT INTO seasons(id,name,is_active) VALUES(1,'S1',1);
          INSERT INTO players(id,display_name,color) VALUES(1,'Paul','#a78bfa'),(2,'Luke','#f87171');
          INSERT INTO season_rosters(season_id,player_id) VALUES(1,1),(1,2);`);
  return d;
}
const noCompletion = () => null;

describe('PresenceHub', () => {
  it('seeds the roster offline and snapshots it to a new sink', () => {
    const hub = new PresenceHub(db(), noCompletion, () => 1000);
    const got: any[] = [];
    hub.addSink((m) => got.push(m));
    expect(got[0].type).toBe('presence_snapshot');
    expect(got[0].players.map((p: any) => [p.name, p.online])).toEqual([['Paul', false], ['Luke', false]]);
  });

  it('a frame marks the player online + broadcasts a delta with enriched completion', () => {
    const hub = new PresenceHub(db(), (_c, _l, pos) => (pos ? 0.5 : null), () => 2000);
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
    const hub = new PresenceHub(db(), () => null, () => t);
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
    const hub = new PresenceHub(d, () => null, () => 5000);
    const got: any[] = [];
    hub.addSink((m) => got.push(m));
    hub.update(1, { screen: 'RACING', course: 'Rainbow Road', resets: 3 });
    expect(got.at(-1).player).toMatchObject({ player_id: 1, resets: 3, pb_ms: 79880 });
  });

  it('seeds offline entries with updated_at 0 (never seen)', () => {
    const hub = new PresenceHub(db(), noCompletion, () => 1000);
    const got: any[] = [];
    hub.addSink((m) => got.push(m));
    expect(got[0].players[0].updated_at).toBe(0);
  });
});
