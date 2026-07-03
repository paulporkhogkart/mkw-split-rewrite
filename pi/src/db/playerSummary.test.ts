import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';
import { playerPbRows } from './playerSummary';

function seeded() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul'),(2,'Luke'),(3,'Max')");
  db.exec("INSERT INTO season_rosters(season_id,player_id) VALUES (1,1),(1,2),(1,3)");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','Rainbow Road'),(2,'mc','Mario Circuit')");
  // Rainbow Road: Luke 108000 (#1), Paul 110000 (#2), Max 111000 (#3)
  // Mario Circuit: Paul 90000 (#1), Max 92000 (#2)
  db.exec(`INSERT INTO runs(season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb) VALUES
    (1,2,1,150,'finished','live',108000,1),
    (1,1,1,150,'finished','live',110000,1),
    (1,3,1,150,'finished','live',111000,1),
    (1,1,2,150,'finished','live',90000,1),
    (1,3,2,150,'finished','live',92000,1)`);
  db.exec("INSERT INTO world_records(course_id,cc,holder_name,record_ms,record_str,is_current) VALUES (1,150,'WR',100000,'1:40',1),(2,150,'WR',80000,'1:20',1)");
  return db;
}

describe('playerPbRows', () => {
  it('builds a row per course with rank, gap-to-next, WR gap, and leader softness', () => {
    const rows = playerPbRows(seeded(), 1, 150, 1); // Paul
    const rr = rows.find(r => r.slug === 'rr')!;
    expect(rr.your_rank).toBe(2);
    expect(rr.field_size).toBe(3);
    expect(rr.leads).toBe(false);
    expect(rr.next_rank_ms).toBe(108000);         // Luke directly above
    expect(rr.gap_to_next_ms).toBe(2000);         // 110000 - 108000
    expect(rr.wr_ms).toBe(100000);
    expect(rr.off_wr_pct).toBeCloseTo(10, 6);     // (110000-100000)/100000
    expect(rr.leader_ms).toBe(108000);
    expect(rr.leader_off_wr_pct).toBeCloseTo(8, 6);

    const mc = rows.find(r => r.slug === 'mc')!;
    expect(mc.your_rank).toBe(1);
    expect(mc.leads).toBe(true);
    expect(mc.next_rank_ms).toBeNull();
    expect(mc.gap_to_next_ms).toBeNull();
    expect(mc.leader_ms).toBe(90000);
  });

  it('sets WR fields null when the course has no current WR', () => {
    const db = seeded();
    db.exec("UPDATE world_records SET is_current=0 WHERE course_id=1");
    const rr = playerPbRows(db, 1, 150, 1).find(r => r.slug === 'rr')!;
    expect(rr.wr_ms).toBeNull();
    expect(rr.off_wr_pct).toBeNull();
    expect(rr.leader_off_wr_pct).toBeNull();
  });
});
