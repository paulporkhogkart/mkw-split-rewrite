import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';
import { courseSplits } from './courseSummary';

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
    const s = courseSplits(seededLaps(), 1, 1, 150);
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
    expect(courseSplits(db, 1, 1, 150)).toEqual({ laps: 0, perPlayer: [], fieldIdeal: [] });
  });
});
