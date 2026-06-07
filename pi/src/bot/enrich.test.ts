import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from '../db/connect';
import { buildWrData } from './enrich';
import type { ServerEvent } from '../db/types';

function db1() {
  const db = openDb(':memory:'); applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'mario_bros_circuit','Mario Bros. Circuit')");
  db.exec("INSERT INTO world_records(course_id,cc,holder_name,record_ms,record_str,achieved_at,is_current) VALUES " +
    "(1,150,'Luke',100000,'1:40.000','2026-01-01T00:00:00.000Z',0)," +
    "(1,150,'Paul',99000,'1:39.000','2026-02-01T00:00:00.000Z',1)");
  return db;
}

const wrEvent: Extract<ServerEvent, { type: 'wr_update' }> = {
  type: 'wr_update', course: 'Mario Bros. Circuit', cc: 150, holder: 'Paul',
  total_time: '1:39.000', prev_holder: 'Luke', prev_time: '1:40.000',
  improvement_ms: 1000, character: null, vehicle: null, video_url: null,
};

describe('buildWrData', () => {
  it('resolves the course display name, formats the delta, and includes reign', () => {
    const d = buildWrData(db1(), wrEvent);
    expect(d.holder).toBe('Paul');
    expect(d.track).toBe('Mario Bros. Circuit');
    expect(d.record).toBe('1:39.000');
    expect(d.improvement_str).toBe('-1.000s');     // 1000ms faster, shown as a negative delta
    expect(d.reign?.previous_holder).toBe('Luke');
    expect(d.reign?.is_same_person).toBe(false);
  });
});

import { buildPbData } from './enrich';

function pbDb() {
  const db = openDb(':memory:'); applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul'),(2,'Luke')");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','Rainbow Road')");
  // Post-PB leaderboard: Paul 1:46 (new PB, run 99) leads Luke 1:48. Paul's prev was 1:50.
  db.exec("INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,total_time_str,ended_at,is_pb) VALUES " +
    "(50,1,1,1,150,'finished','live',110000,'1:50.000','2026-01-01T00:00:00.000Z',0)," +   // Paul old
    "(60,1,2,1,150,'finished','live',108000,'1:48.000','2026-01-02T00:00:00.000Z',1)," +   // Luke PB
    "(99,1,1,1,150,'finished','live',106000,'1:46.000','2026-03-01T00:00:00.000Z',1)");    // Paul new PB
  db.exec("INSERT INTO world_records(course_id,cc,holder_name,record_ms,record_str,achieved_at,is_current) VALUES (1,150,'SuperFX',100000,'1:40.000','2026-01-01T00:00:00.000Z',1)");
  return db;
}

const pbEvent: Extract<ServerEvent, { type: 'pb_achieved' }> = {
  type: 'pb_achieved', player: 'Paul', course: 'rr', cc: 150,
  total_time: '1:46.000', delta_vs_prev_ms: -4000, rank: 1,
};

describe('buildPbData', () => {
  it('computes track/total positions, overtaken, still-ahead (WR), and a track record', () => {
    const d = buildPbData(pbDb(), pbEvent);
    expect(d.track).toBe('Rainbow Road');
    expect(d.time).toBe('1:46.000');
    expect(d.improvement_str).toBe('-4.000s');
    expect(d.positions.track).toEqual({ old: 2, new: 1 });   // Paul was behind Luke, now leads
    expect(d.overtaken).toEqual([{ name: 'Luke', diff_str: '+2.000s' }]);   // passed Luke (108000-106000)
    expect(d.still_ahead).toEqual({ name: 'WR', diff_str: '-6.000s' });     // WR is faster (100000-106000)
    expect(d.is_new_track_record).toBe(true);
    expect(d.reign?.previous_holder).toBe('Luke');
  });
});
