import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';
import { courseLeaderboard, friendsPbs, currentWr, myPbs } from './reads';

function seeded() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 1',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul'),(2,'Luke')");
  db.exec("INSERT INTO season_rosters(season_id,player_id) VALUES (1,1),(1,2)");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','RR')");
  db.exec("INSERT INTO runs(season_id,player_id,course_id,cc,status,provenance,total_time_ms,total_time_str,is_pb) VALUES (1,1,1,150,'finished','live',108000,'1:48.000',1),(1,2,1,150,'finished','live',112000,'1:52.000',1)");
  db.exec("INSERT INTO world_records(course_id,cc,holder_name,record_ms,record_str) VALUES (1,150,'SuperFX',100000,'1:40.000')");
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
