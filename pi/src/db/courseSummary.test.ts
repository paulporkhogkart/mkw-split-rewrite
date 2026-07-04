import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';
import { courseSplits, courseSummary } from './courseSummary';

function seededLaps() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
  db.exec("INSERT INTO players(id,display_name,color) VALUES (1,'Paul','#f00'),(2,'Luke','#0f0')");
  db.exec("INSERT INTO season_rosters(season_id,player_id) VALUES (1,1),(1,2)");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','Rainbow Road')");
  // Two finished runs by Paul (ids 10,11) and one by Luke (id 12), 3 laps each.
  db.exec(`INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb) VALUES
    (10,1,1,1,150,'finished','live',110000,0),
    (11,1,1,1,150,'finished','live',108000,1),
    (12,1,2,1,150,'finished','live',109000,1)`);
  db.exec(`INSERT INTO run_laps(run_id,lap_index,lap_time_ms) VALUES
    (10,1,40000),(10,2,35000),(10,3,35000),
    (11,1,39000),(11,2,34000),(11,3,35000),
    (12,1,38000),(12,2,36000),(12,3,35000)`);
  return db;
}

describe('courseSplits', () => {
  it('takes each player\'s fastest lap per index and the per-lap field ideal', () => {
    const s = courseSplits(seededLaps(), 1, 150);
    expect(s.laps).toBe(3);
    const paul = s.perPlayer.find(p => p.display_name === 'Paul')!;
    expect(paul.color).toBe('#f00');
    expect(paul.best).toEqual([39000, 34000, 35000]); // min across runs 10 & 11
    const luke = s.perPlayer.find(p => p.display_name === 'Luke')!;
    expect(luke.best).toEqual([38000, 36000, 35000]);
    expect(s.fieldIdeal).toEqual([38000, 34000, 35000]); // min across players per lap
  });

  it('returns empty structure when the course has no lap data', () => {
    const db = openDb(':memory:');
    applySchema(db);
    db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','Rainbow Road')");
    expect(courseSplits(db, 1, 150)).toEqual({ laps: 0, perPlayer: [], fieldIdeal: [] });
  });
});

describe('courseSummary', () => {
  function seeded() {
    const db = openDb(':memory:');
    applySchema(db);
    db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
    db.exec("INSERT INTO players(id,display_name,color) VALUES (1,'Paul','#f00'),(2,'Luke','#0f0')");
    db.exec("INSERT INTO season_rosters(season_id,player_id) VALUES (1,1),(1,2)");
    db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','Rainbow Road')");
    db.exec(`INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb,ended_at) VALUES
      (1,1,2,1,150,'finished','live',108000,1,'2026-01-02T00:00:00Z'),
      (2,1,1,1,150,'finished','live',110000,1,'2026-01-03T00:00:00Z')`);
    db.exec("INSERT INTO run_laps(run_id,lap_index,lap_time_ms) VALUES (1,1,40000),(1,2,34000),(2,1,41000),(2,2,35000)");
    db.exec("INSERT INTO world_records(course_id,cc,holder_name,record_ms,record_str,video_url,is_current,achieved_at) VALUES (1,150,'WR',100000,'1:40','http://v',1,'2025-12-01T00:00:00Z')");
    return db;
  }

  it('assembles profile, wr, coloured leaderboard, splits, and history', () => {
    const s = courseSummary(seeded(), 150, 'rr')!;
    expect(s.profile.display_name).toBe('Rainbow Road');
    expect(s.wr!.record_ms).toBe(100000);
    expect(s.wr!.video_url).toBe('http://v');
    expect(s.leaderboard.map(r => [r.display_name, r.rank, r.color])).toEqual([['Luke', 1, '#0f0'], ['Paul', 2, '#f00']]);
    expect(s.splits.laps).toBe(2);
    expect(s.history.recordProgression.length).toBeGreaterThan(0);
    expect(s.history.wrHistory[0].holder_name).toBe('WR');
  });

  it('returns null for an unknown slug', () => {
    expect(courseSummary(seeded(), 150, 'nope')).toBeNull();
  });

  it('leaderboard is all-time: a player\'s best from a second season counts', () => {
    const db = seeded();
    db.exec("INSERT INTO seasons(id,name,is_active) VALUES (2,'S2',0)");
    // Luke's S1 best is 108000; a faster S2 run should surface here, proving the board spans
    // seasons rather than being scoped to the active one (which is still S1).
    db.exec(`INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb,ended_at) VALUES
      (3,2,2,1,150,'finished','live',95000,0,'2026-02-01T00:00:00Z')`);
    const s = courseSummary(db, 150, 'rr')!;
    expect(s.leaderboard.map(r => [r.display_name, r.total_time_ms, r.rank])).toEqual([['Luke', 95000, 1], ['Paul', 110000, 2]]);
  });

  it('excludes provenance=carryover runs from the all-time leaderboard', () => {
    const db = seeded();
    // A carryover run duplicates a prior best at the original time; it's faster than anyone's
    // real run here, so if it leaked in Paul would rank #1 at 50000 instead of #2 at 110000.
    db.exec(`INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb,ended_at) VALUES
      (3,1,1,1,150,'finished','carryover',50000,0,'2026-01-01T00:00:00Z')`);
    const s = courseSummary(db, 150, 'rr')!;
    expect(s.leaderboard.map(r => [r.display_name, r.total_time_ms, r.rank])).toEqual([['Luke', 108000, 1], ['Paul', 110000, 2]]);
  });
});
