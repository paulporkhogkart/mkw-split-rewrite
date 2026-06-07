import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';
import { overallStandings, wrAggregate } from './leaderboards';

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
