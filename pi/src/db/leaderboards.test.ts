import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';
import { overallStandings, wrAggregate, nemesisRows } from './leaderboards';

/** Seed: 2 courses, 2 players.
 *  Paul:  course 1 = 100_000 ms, course 2 = 90_000 ms  → total 190_000, rank 1 on both → points 2
 *  Luke:  course 1 = 110_000 ms, course 2 = 95_000 ms  → total 205_000, rank 2 on both → points 4
 *  WRs: course 1 = 80_000, course 2 = 75_000 (both is_current=1)
 */
function seeded() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 1',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul'),(2,'Luke')");
  db.exec("INSERT INTO season_rosters(season_id,player_id) VALUES (1,1),(1,2)");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'course_a','Course A'),(2,'course_b','Course B')");
  // Paul PBs
  db.exec("INSERT INTO runs(season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb) VALUES (1,1,1,150,'finished','live',100000,1)");
  db.exec("INSERT INTO runs(season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb) VALUES (1,1,2,150,'finished','live',90000,1)");
  // Luke PBs
  db.exec("INSERT INTO runs(season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb) VALUES (1,2,1,150,'finished','live',110000,1)");
  db.exec("INSERT INTO runs(season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb) VALUES (1,2,2,150,'finished','live',95000,1)");
  // Current WRs on both courses
  db.exec("INSERT INTO world_records(course_id,cc,holder_name,record_ms,record_str,is_current) VALUES (1,150,'WRHolder',80000,'1:20.000',1)");
  db.exec("INSERT INTO world_records(course_id,cc,holder_name,record_ms,record_str,is_current) VALUES (2,150,'WRHolder',75000,'1:15.000',1)");
  return db;
}

describe('overallStandings', () => {
  it('ranks players by total_ms with golf points (sum of per-course ranks)', () => {
    const standings = overallStandings(seeded(), 1, 150);

    expect(standings).toHaveLength(2);

    const paul = standings[0];
    const luke = standings[1];

    // Paul is faster on both courses → lower total → comes first
    expect(paul.display_name).toBe('Paul');
    expect(paul.total_ms).toBe(190000);   // 100_000 + 90_000
    expect(paul.tracks).toBe(2);
    expect(paul.points).toBe(2);          // rank 1 on course A + rank 1 on course B

    expect(luke.display_name).toBe('Luke');
    expect(luke.total_ms).toBe(205000);   // 110_000 + 95_000
    expect(luke.tracks).toBe(2);
    expect(luke.points).toBe(4);          // rank 2 on course A + rank 2 on course B
  });

  it('includes player_id on each row', () => {
    const standings = overallStandings(seeded(), 1, 150);
    expect(standings[0].player_id).toBe(1);   // Paul
    expect(standings[1].player_id).toBe(2);   // Luke
  });

  it('returns empty array when no PBs exist', () => {
    const db = openDb(':memory:');
    applySchema(db);
    db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S',1)");
    expect(overallStandings(db, 1, 150)).toEqual([]);
  });
});

describe('wrAggregate', () => {
  it('sums all is_current WR record_ms and returns the count', () => {
    const agg = wrAggregate(seeded(), 150);
    expect(agg.total_ms).toBe(155000);   // 80_000 + 75_000
    expect(agg.count).toBe(2);
  });

  it('returns total_ms=0, count=0 when there are no current WRs', () => {
    const db = openDb(':memory:');
    applySchema(db);
    const agg = wrAggregate(db, 150);
    expect(agg.total_ms).toBe(0);
    expect(agg.count).toBe(0);
  });

  it('ignores non-current WR rows', () => {
    const db = seeded();
    // add a non-current row on course 1
    db.exec("UPDATE world_records SET is_current=0 WHERE course_id=1 AND cc=150");
    db.exec("INSERT INTO world_records(course_id,cc,holder_name,record_ms,record_str,is_current) VALUES (1,150,'NewHolder',78000,'1:18.000',1)");
    const agg = wrAggregate(db, 150);
    // course 1 now contributes 78_000 (new current), course 2 still 75_000
    expect(agg.total_ms).toBe(153000);
    expect(agg.count).toBe(2);
  });
});

/** Seed for nemesisRows:
 *  Players: Paul (id=1), Luke (id=2), Sam (id=3)
 *  Course A (id=1): Paul=100_000, Luke=90_000 (Luke leads), Sam=no PB
 *  Course B (id=2): Paul=85_000, Luke=95_000 (Paul leads, Luke 2nd)
 *  Course C (id=3): Paul=no PB, Luke=80_000
 *
 *  Untargeted from Paul's perspective:
 *    Course A: Paul (100_000) vs Luke leader (90_000) → diff = +10_000
 *    Course B: Paul leads → compare to 2nd (Luke 95_000) → Paul (85_000) is AHEAD → diff = -10_000 (not behind)
 *  So Paul is only behind on Course A (diff=10_000).
 *
 *  For "player leads, compare to 2nd" branch we need a case where Paul is behind:
 *    We'll seed Course D (id=4): Paul=110_000, Luke=100_000 (Luke leads), Sam=105_000 (2nd)
 *    Untargeted Paul vs Course D: leader is Luke (100_000), diff = 110_000 - 100_000 = +10_000
 *    This tests standard "player behind leader" case.
 *
 *  For "player leads → compare to 2nd" branch:
 *    Course E (id=5): Paul=80_000 (Paul leads), Luke=90_000 (2nd), Sam=95_000 (3rd)
 *    Untargeted Paul vs Course E: compare to 2nd (Luke 90_000), diff = 80_000 - 90_000 = -10_000 → Paul ahead → skip
 *
 *  Let's use a clean seed with exactly the branches we need:
 *    Course 1: Luke leads (90k), Paul behind (100k) → diff = +10_000 (Paul is behind Luke)
 *    Course 2: Paul leads (85k), Luke 2nd (95k) → diff = 85k - 95k = -10_000 (Paul is ahead) → skip
 *    Course 3: Paul leads (70k), Luke 2nd (80k) → diff = 70k - 80k = -10_000 → skip
 *    Course 4: Luke leads (60k), Paul behind (75k) → diff = +15_000 (Paul is behind Luke)
 *  Untargeted sorted by largest gap first: Course 4 (15_000) > Course 1 (10_000)
 *
 *  Targeted (Paul vs Luke):
 *    Course 1: Paul (100k) vs Luke (90k) → diff = +10_000 (behind)
 *    Course 2: Paul (85k) vs Luke (95k) → diff = -10_000 (ahead, negative → include but sorted last)
 *    Course 4: Paul (75k) vs Luke (60k) → diff = +15_000 (behind)
 *  Sorted largest gap first: 15_000, 10_000, -10_000
 */
function seededNemesis() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 1',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul'),(2,'Luke')");
  db.exec("INSERT INTO season_rosters(season_id,player_id) VALUES (1,1),(1,2)");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'course_a','Course A'),(2,'course_b','Course B'),(4,'course_d','Course D')");
  // Course A: Luke leads (90k), Paul behind (100k)
  db.exec("INSERT INTO runs(season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb) VALUES (1,1,1,150,'finished','live',100000,1)");
  db.exec("INSERT INTO runs(season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb) VALUES (1,2,1,150,'finished','live',90000,1)");
  // Course B: Paul leads (85k), Luke 2nd (95k)
  db.exec("INSERT INTO runs(season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb) VALUES (1,1,2,150,'finished','live',85000,1)");
  db.exec("INSERT INTO runs(season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb) VALUES (1,2,2,150,'finished','live',95000,1)");
  // Course D: Luke leads (60k), Paul behind (75k)
  db.exec("INSERT INTO runs(season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb) VALUES (1,1,4,150,'finished','live',75000,1)");
  db.exec("INSERT INTO runs(season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb) VALUES (1,2,4,150,'finished','live',60000,1)");
  return db;
}

describe('nemesisRows', () => {
  it('untargeted: returns all courses with a valid comparison, sorted largest gap first', () => {
    const rows = nemesisRows(seededNemesis(), 1, 150, 1 /* Paul */, null);
    // Course A: Paul (100k) vs Luke leader (90k) → diff = +10_000
    // Course B: Paul leads (85k) → compare to 2nd (Luke 95k) → diff = -10_000
    // Course D: Paul (75k) vs Luke leader (60k) → diff = +15_000
    // Sorted largest gap first: Course D (15k), Course A (10k), Course B (-10k)
    expect(rows).toHaveLength(3);
    expect(rows[0].track_name).toBe('Course D');
    expect(rows[0].diff_ms).toBe(15000);
    expect(rows[0].ahead_player).toBe('Luke');
    expect(rows[1].track_name).toBe('Course A');
    expect(rows[1].diff_ms).toBe(10000);
    expect(rows[1].ahead_player).toBe('Luke');
    expect(rows[2].track_name).toBe('Course B');
    expect(rows[2].diff_ms).toBe(-10000);
    expect(rows[2].ahead_player).toBe('Luke');
  });

  it('untargeted: player leads → compares to 2nd place (not self)', () => {
    const rows = nemesisRows(seededNemesis(), 1, 150, 1 /* Paul */, null);
    // Course B: Paul leads (85k), so it compares to Luke (2nd, 95k); ahead_player is Luke, not Paul
    const courseB = rows.find(r => r.track_name === 'Course B');
    expect(courseB).toBeDefined();
    expect(courseB!.ahead_player).toBe('Luke');
    expect(courseB!.diff_ms).toBe(-10000);  // Paul is 10k ahead of Luke
  });

  it('targeted: returns courses where player is behind target, sorted largest gap first (includes negative diffs)', () => {
    const rows = nemesisRows(seededNemesis(), 1, 150, 1 /* Paul */, 2 /* Luke */);
    // Course A: Paul (100k) vs Luke (90k) → diff = +10_000
    // Course B: Paul (85k) vs Luke (95k) → diff = -10_000
    // Course D: Paul (75k) vs Luke (60k) → diff = +15_000
    // Sorted largest gap first: 15k, 10k, -10k
    expect(rows).toHaveLength(3);
    expect(rows[0].track_name).toBe('Course D');
    expect(rows[0].diff_ms).toBe(15000);
    expect(rows[1].track_name).toBe('Course A');
    expect(rows[1].diff_ms).toBe(10000);
    expect(rows[2].track_name).toBe('Course B');
    expect(rows[2].diff_ms).toBe(-10000);
    // ahead_player is always the target (Luke) in targeted mode
    expect(rows.every(r => r.ahead_player === 'Luke')).toBe(true);
  });

  it('targeted: skips courses where the target has no PB', () => {
    const db = seededNemesis();
    // Add a course where Paul has a PB but Luke does not
    db.exec("INSERT INTO courses(id,slug,display_name) VALUES (5,'course_e','Course E')");
    db.exec("INSERT INTO runs(season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb) VALUES (1,1,5,150,'finished','live',120000,1)");
    const rows = nemesisRows(db, 1, 150, 1 /* Paul */, 2 /* Luke */);
    const courseE = rows.find(r => r.track_name === 'Course E');
    expect(courseE).toBeUndefined();
  });

  it('untargeted: skips course when player is the sole entry (no 2nd place to compare to)', () => {
    const db = openDb(':memory:');
    applySchema(db);
    db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S',1)");
    db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
    db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'solo','Solo Course')");
    db.exec("INSERT INTO runs(season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb) VALUES (1,1,1,150,'finished','live',100000,1)");
    const rows = nemesisRows(db, 1, 150, 1 /* Paul */, null);
    expect(rows).toHaveLength(0);
  });

  it('returns empty array when player has no PBs', () => {
    const db = seededNemesis();
    // Use a player id that doesn't exist in runs
    const rows = nemesisRows(db, 1, 150, 99 /* non-existent */, null);
    expect(rows).toHaveLength(0);
  });
});
