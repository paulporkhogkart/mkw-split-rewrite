import { describe, it, expect } from 'vitest';
import { DatabaseSync } from 'node:sqlite';
import { applySchema } from '../db/connect';
import { resolveRace } from './resolve';
import { resolvePeriod } from './period';
import { DateTime } from 'luxon';

function db(): DatabaseSync {
  const d = new DatabaseSync(':memory:');
  applySchema(d);
  d.exec(`INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1);
          INSERT INTO players(id,display_name) VALUES (1,'Paul'),(2,'Luke');
          INSERT INTO courses(id,slug,display_name) VALUES (1,'bc','Bowsers Castle'),(2,'mbc','Mario Bros Circuit');`);
  const run = (id:number,p:number,c:number,st:string,ms:number|null,when:string,ch:string) =>
    d.prepare(`INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,ended_at,total_time_ms,character)
               VALUES (?,1,?,?,150,?,'live',?,?,?)`).run(id,p,c,st,when,ms,ch);
  run(1,2,1,'finished',160000,'2026-06-10T03:00:00+00:00','Mario');
  run(2,2,1,'reset',    null,  '2026-06-10T04:00:00+00:00','Mario');
  run(3,2,2,'finished',120000,'2026-06-10T05:00:00+00:00','Peach');
  run(4,1,1,'finished',158000,'2026-06-10T06:00:00+00:00','Mario');
  const lap = (rid:number,idx:number,coins:number)=>d.prepare(
    'INSERT INTO run_laps(run_id,lap_index,lap_time_ms,coins,shrooms) VALUES (?,?,1000,?,1)').run(rid,idx,coins);
  lap(1,0,5); lap(1,1,4); lap(2,0,3); // reset still collected 3
  return d;
}
const week = () => resolvePeriod('this_week','Australia/Melbourne',
  { now: DateTime.fromISO('2026-06-10T20:00:00',{zone:'Australia/Melbourne'}) });

describe('resolveRace', () => {
  it('counts resets (status-filtered)', () => {
    const r = resolveRace(db(), { metric: 'resets', period: week(), filters: {}, seasonId: 1 });
    expect(r.total).toBe(1);
  });

  it('sums coins (gained, run-level) across all statuses incl. the reset', () => {
    const d = db();
    d.prepare('UPDATE runs SET coins_gained=? WHERE id=?').run(9, 1);  // Luke/bc finished
    d.prepare('UPDATE runs SET coins_gained=? WHERE id=?').run(3, 2);  // Luke/bc reset still gained 3
    const r = resolveRace(d, { metric: 'coins', period: week(), filters: { player: 'Luke' }, seasonId: 1 });
    expect(r.total).toBe(12);
  });

  it('breaks coins down by course', () => {
    const d = db();
    d.prepare('UPDATE runs SET coins_gained=? WHERE id=?').run(9, 1);
    d.prepare('UPDATE runs SET coins_gained=? WHERE id=?').run(3, 2);
    const r = resolveRace(d, { metric: 'coins', period: week(), filters: {}, groupBy: 'course', seasonId: 1 });
    expect(Object.fromEntries(r.rows.map((x) => [x.key, x.value]))['Bowsers Castle']).toBe(12);
  });

  it('sums coins_lost across all statuses', () => {
    const d = db();
    d.prepare('UPDATE runs SET coins_lost=? WHERE id=?').run(6, 1);
    d.prepare('UPDATE runs SET coins_lost=? WHERE id=?').run(2, 2);  // reset's lost coins count too
    const r = resolveRace(d, { metric: 'coins_lost', period: week(), filters: { player: 'Luke' }, seasonId: 1 });
    expect(r.total).toBe(8);
  });

  it('pb_count uses the stored flag', () => {
    const d = db();
    d.prepare('UPDATE runs SET was_pb=1 WHERE id IN (1,4)').run();
    const r = resolveRace(d, { metric: 'pb_count', period: week(), filters: {}, seasonId: 1 });
    expect(r.total).toBe(2);
  });

  it('time_improvement = slowest minus fastest PB in the window (ms shaved)', () => {
    const d = db();
    const add = (id: number, ms: number, when: string) => d.prepare(
      `INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,ended_at,total_time_ms,was_pb,character)
       VALUES(?,1,2,1,150,'finished','live',?,?,1,'Mario')`).run(id, when, ms);
    d.prepare('UPDATE runs SET was_pb=1 WHERE id=1').run();        // Luke/bc 160000
    add(10, 155000, '2026-06-10T07:00:00+00:00');
    add(11, 150000, '2026-06-10T08:00:00+00:00');
    const r = resolveRace(d, { metric: 'time_improvement', period: week(), filters: { player: 'Luke', course: 'bc' }, seasonId: 1 });
    expect(r.total).toBe(10000);                                   // 160000 - 150000
  });
});
