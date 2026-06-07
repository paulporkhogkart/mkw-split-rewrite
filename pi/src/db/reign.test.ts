import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';
import { wrReign, trackReign } from './reign';

function wrDb() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','RR')");
  // History oldest->newest. Luke held 2 records, then Paul takes it (current).
  db.exec("INSERT INTO world_records(course_id,cc,holder_name,record_ms,record_str,achieved_at,is_current) VALUES " +
    "(1,150,'Luke',110000,'1:50.000','2026-01-01T00:00:00.000Z',0)," +
    "(1,150,'Luke',108000,'1:48.000','2026-01-10T00:00:00.000Z',0)," +
    "(1,150,'Paul',107000,'1:47.000','2026-02-01T00:00:00.000Z',1)");
  return db;
}

describe('wrReign', () => {
  it('measures the dethroned holder reign from their first contiguous record', () => {
    const r = wrReign(wrDb(), 1, 150, 'Luke', 'Paul');
    expect(r?.previous_holder).toBe('Luke');
    expect(r?.is_same_person).toBe(false);
    // reign started 2026-01-01, so a positive duration
    expect(r!.reign_ms!).toBeGreaterThan(0);
  });
  it('flags is_same_person when the holder re-breaks their own WR', () => {
    const db = wrDb();
    db.exec("UPDATE world_records SET is_current=0");
    db.exec("INSERT INTO world_records(course_id,cc,holder_name,record_ms,record_str,achieved_at,is_current) VALUES (1,150,'Paul',106000,'1:46.000','2026-03-01T00:00:00.000Z',1)");
    const r = wrReign(db, 1, 150, 'Paul', 'Paul');
    expect(r?.is_same_person).toBe(true);
    expect(r?.previous_holder).toBe('Paul');
  });
  it('returns null when there is no previous holder', () => {
    expect(wrReign(wrDb(), 1, 150, null, 'Paul')).toBeNull();
  });
});

function trackDb() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul'),(2,'Luke')");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','RR')");
  // Luke leads from Jan (108000). Paul's new PB (run 99, 106000) just dethroned him.
  db.exec("INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,ended_at,is_pb) VALUES " +
    "(10,1,2,1,150,'finished','live',112000,'2026-01-01T00:00:00.000Z',0)," +
    "(11,1,2,1,150,'finished','live',108000,'2026-01-05T00:00:00.000Z',1)," +
    "(20,1,1,1,150,'finished','live',110000,'2026-01-10T00:00:00.000Z',0)," +
    "(99,1,1,1,150,'finished','live',106000,'2026-03-01T00:00:00.000Z',1)");
  return db;
}

describe('trackReign', () => {
  it('finds the dethroned leader and a positive reign, excluding the new PB run', () => {
    const r = trackReign(trackDb(), 1, 1, 150, 'Paul', 99);
    expect(r?.previous_holder).toBe('Luke');
    expect(r?.is_same_person).toBe(false);
    expect(r!.reign_ms!).toBeGreaterThan(0);
  });
  it('reports is_same_person when the leader improves their own best', () => {
    const db = trackDb();
    // Paul already led from Jan (107000, run 5); run 99 is his improvement.
    db.exec("INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,ended_at,is_pb) VALUES (5,1,1,1,150,'finished','live',107000,'2026-01-02T00:00:00.000Z',0)");
    const r = trackReign(db, 1, 1, 150, 'Paul', 99);
    expect(r?.previous_holder).toBe('Paul');
    expect(r?.is_same_person).toBe(true);
  });
  it('returns nulls when there is no prior finished run', () => {
    const db = openDb(':memory:'); applySchema(db);
    db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
    db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
    db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','RR')");
    db.exec("INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,ended_at,is_pb) VALUES (99,1,1,1,150,'finished','live',106000,'2026-03-01T00:00:00.000Z',1)");
    expect(trackReign(db, 1, 1, 150, 'Paul', 99)).toEqual({ previous_holder: null, reign_ms: null, is_same_person: false });
  });
});
