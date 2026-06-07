import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';
import { courseLeaderboard, friendsPbs, currentWr, myPbs, myPbSplits, courseTrails, roster, playerTrails } from './reads';

function seeded() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 1',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul'),(2,'Luke')");
  db.exec("INSERT INTO season_rosters(season_id,player_id) VALUES (1,1),(1,2)");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','RR')");
  db.exec("INSERT INTO runs(season_id,player_id,course_id,cc,status,provenance,total_time_ms,total_time_str,is_pb) VALUES (1,1,1,150,'finished','live',108000,'1:48.000',1),(1,2,1,150,'finished','live',112000,'1:52.000',1)");
  db.exec("INSERT INTO world_records(course_id,cc,holder_name,record_ms,record_str,is_current) VALUES (1,150,'SuperFX',100000,'1:40.000',1)");
  return db;
}

describe('reads', () => {
  it('courseLeaderboard orders by time with names + rank', () => {
    const lb = courseLeaderboard(seeded(), 1, 1, 150);
    expect(lb.map(r => [r.display_name, r.total_time_ms, r.rank])).toEqual([['Paul',108000,1],['Luke',112000,2]]);
  });
  it('friendsPbs returns the roster PBs for the course', () => {
    const pbs = friendsPbs(seeded(), 1, 1, 150);
    expect(pbs.length).toBe(2);
  });
  it('currentWr returns the latest WR', () => {
    expect(currentWr(seeded(), 1, 150)?.record_ms).toBe(100000);
  });
  it('currentWr returns the is_current row even when a faster row exists', () => {
    const db = seeded();
    // A faster, non-current row (a removed/DQ'd record) plus moving current to a slower one.
    db.exec("UPDATE world_records SET is_current=0 WHERE record_ms=100000");
    db.exec("INSERT INTO world_records(course_id,cc,holder_name,record_ms,record_str,is_current) VALUES (1,150,'Reverted',101000,'1:41.000',1)");
    db.exec("INSERT INTO world_records(course_id,cc,holder_name,record_ms,record_str,is_current) VALUES (1,150,'Ghost',95000,'1:35.000',0)");
    expect(currentWr(db, 1, 150)?.record_ms).toBe(101000);
  });
});

describe('myPbs', () => {
  it('returns the player\'s PB rows as {course_slug, cc, total_time_ms}', () => {
    const db = openDb(':memory:'); applySchema(db);
    db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 1',1)");
    db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
    db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rainbow_road','Rainbow Road'),(2,'mario_circuit','Mario Circuit')");
    db.exec("INSERT INTO runs(season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb) VALUES (1,1,1,150,'finished','live',108000,1),(1,1,2,150,'finished','live',95000,0)");
    expect(myPbs(db, 1, 1)).toEqual([{ course_slug: 'rainbow_road', cc: 150, total_time_ms: 108000 }]);
  });
});

describe('myPbSplits', () => {
  it('returns the caller PB total + per-lap cumulative splits', () => {
    const db = openDb(':memory:'); applySchema(db);
    db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
    db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
    db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','RR')");
    db.exec("INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb) VALUES (10,1,1,1,150,'finished','live',108000,1)");
    db.exec("INSERT INTO run_laps(run_id,lap_index,lap_time_ms) VALUES (10,1,36000),(10,2,72000),(10,3,108000)");
    expect(myPbSplits(db, 1, 1, 1, 150)).toEqual({ total_ms: 108000, splits: { 1: 36000, 2: 72000, 3: 108000 } });
  });
  it('returns empty splits when there is no live PB (or a legacy total-only PB)', () => {
    const db = openDb(':memory:'); applySchema(db);
    db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
    db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
    db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','RR')");
    db.exec("INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb) VALUES (11,1,1,1,150,'finished','legacy_import',95000,1)");
    expect(myPbSplits(db, 1, 1, 1, 150)).toEqual({ total_ms: 95000, splits: {} });
    expect(myPbSplits(db, 1, 1, 999, 150)).toEqual({ total_ms: null, splits: {} });
  });
});

describe('courseTrails', () => {
  function seededTrails() {
    const db = openDb(':memory:'); applySchema(db);
    db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
    db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul'),(2,'Luke'),(3,'Alex')");
    db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','RR')");
    // Paul + Luke: live PBs with points; Alex: legacy PB, no points.
    db.exec("INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb) VALUES (10,1,1,1,150,'finished','live',108000,1),(20,1,2,1,150,'finished','live',112000,1),(30,1,3,1,150,'finished','legacy_import',95000,1)");
    db.exec("INSERT INTO run_points(run_id,t_ms,cx,cy,score) VALUES (10,0,100,200,0.9),(10,16,101,201,0.95)");
    db.exec("INSERT INTO run_points(run_id,t_ms,cx,cy,score) VALUES (20,0,300,400,0.8)");
    return db;
  }
  it('returns roster PB trails with points, omitting legacy (point-less) PBs', () => {
    const t = courseTrails(seededTrails(), 1, 1, 150, null);
    expect(t.map(x => x.player)).toEqual(['Paul', 'Luke']);   // Alex (no points) omitted
    expect(t[0]).toEqual({ player_id: 1, player: 'Paul', total_ms: 108000, is_me: false,
      points: [[0,100,200,0.9],[16,101,201,0.95]] });
  });
  it('flags is_me for the matching player', () => {
    const t = courseTrails(seededTrails(), 1, 1, 150, 1);
    expect(t.find(x => x.player_id === 1)?.is_me).toBe(true);
    expect(t.find(x => x.player_id === 2)?.is_me).toBe(false);
  });
});

describe('roster', () => {
  it('returns the season roster (alphabetical) with curated colour, excluding non-members', () => {
    const db = openDb(':memory:'); applySchema(db);
    db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
    db.exec("INSERT INTO players(id,display_name,color) VALUES (1,'Paul','#9b6bd0'),(2,'Luke',NULL),(3,'Zoe',NULL)");
    db.exec("INSERT INTO season_rosters(season_id,player_id) VALUES (1,1),(1,2)");   // Zoe not in roster
    expect(roster(db, 1)).toEqual([
      { player_id: 2, display_name: 'Luke', color: null },
      { player_id: 1, display_name: 'Paul', color: '#9b6bd0' },
    ]);
  });
});

describe('playerTrails', () => {
  function db5() {
    const db = openDb(':memory:'); applySchema(db);
    db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
    db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
    db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','RR')");
    // 3 finished runs with points (one is_pb); run 40 is faster but has NO points (legacy).
    db.exec("INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,started_at,ended_at,is_pb) VALUES " +
      "(10,1,1,1,150,'finished','live',108000,'2026-01-01T00:00','2026-01-01T00:02',1)," +
      "(20,1,1,1,150,'finished','live',110000,'2026-01-02T00:00','2026-01-02T00:02',0)," +
      "(30,1,1,1,150,'finished','live',109000,'2026-01-03T00:00','2026-01-03T00:02',0)," +
      "(40,1,1,1,150,'finished','legacy_import',107000,NULL,NULL,0)");
    db.exec("INSERT INTO run_points(run_id,t_ms,cx,cy,score) VALUES (10,0,1,1,0.9),(20,0,2,2,0.9),(30,0,3,3,0.9)");
    return db;
  }
  it('pbs returns the PB run only', () => {
    expect(playerTrails(db5(), 1, 1, 1, 150, 'pbs', 1).map(r => r.run_id)).toEqual([10]);
  });
  it('best returns fastest finished WITH points first (legacy point-less excluded from the limit)', () => {
    expect(playerTrails(db5(), 1, 1, 1, 150, 'best', 2).map(r => r.run_id)).toEqual([10, 30]);
  });
  it('last returns newest first, limited', () => {
    expect(playerTrails(db5(), 1, 1, 1, 150, 'last', 2).map(r => r.run_id)).toEqual([30, 20]);
  });
  it('all returns every run with points, newest first', () => {
    expect(playerTrails(db5(), 1, 1, 1, 150, 'all', 1).map(r => r.run_id)).toEqual([30, 20, 10]);
  });
  it('none returns nothing', () => {
    expect(playerTrails(db5(), 1, 1, 1, 150, 'none', 5)).toEqual([]);
  });
  it('last_pb appends the PB when it is older than the last N; flags is_pb', () => {
    const t = playerTrails(db5(), 1, 1, 1, 150, 'last_pb', 1);
    expect(t.map((r) => r.run_id)).toEqual([30, 10]);          // newest + the PB
    expect(t.map((r) => r.is_pb)).toEqual([false, true]);
  });
  it('last_pb does not duplicate the PB when it is within the last N', () => {
    const t = playerTrails(db5(), 1, 1, 1, 150, 'last_pb', 3);
    expect(t.map((r) => r.run_id)).toEqual([30, 20, 10]);
    expect(t.filter((r) => r.is_pb).map((r) => r.run_id)).toEqual([10]);
  });
});
