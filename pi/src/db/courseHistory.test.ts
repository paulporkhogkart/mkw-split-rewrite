import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';
import { recordProgression, courseReigns, wrHistoryRows } from './courseHistory';

function seeded() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul'),(2,'Luke')");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','Rainbow Road')");
  // Timeline: Paul 110s (owns), Luke 109s (takes #1), Paul 108s (retakes #1).
  db.exec(`INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb,ended_at) VALUES
    (1,1,1,1,150,'finished','live',110000,0,'2026-01-01T00:00:00Z'),
    (2,1,2,1,150,'finished','live',109000,0,'2026-01-02T00:00:00Z'),
    (3,1,1,1,150,'finished','live',108000,1,'2026-01-03T00:00:00Z')`);
  db.exec(`INSERT INTO world_records(course_id,cc,holder_name,record_ms,record_str,video_url,is_current,removed_at,achieved_at) VALUES
    (1,150,'WRHolderA',105000,'1:45','http://v/a',0,NULL,'2025-12-01T00:00:00Z'),
    (1,150,'WRHolderB',104000,'1:44','http://v/b',1,NULL,'2025-12-20T00:00:00Z')`);
  return db;
}

describe('recordProgression', () => {
  it('emits an entry each time the local record falls', () => {
    const p = recordProgression(seeded(), 1, 150);
    expect(p.map(e => [e.player, e.ms])).toEqual([['Paul', 110000], ['Luke', 109000], ['Paul', 108000]]);
    expect(p[0].t).toBe(Date.parse('2026-01-01T00:00:00Z'));
  });
});

describe('courseReigns', () => {
  it('spans #1 ownership, last reign ongoing', () => {
    const r = courseReigns(seeded(), 1, 150);
    expect(r.map(x => x.player)).toEqual(['Paul', 'Luke', 'Paul']);
    expect(r[0].to).toBe(Date.parse('2026-01-02T00:00:00Z'));
    expect(r[0].ms).toBe(Date.parse('2026-01-02T00:00:00Z') - Date.parse('2026-01-01T00:00:00Z'));
    expect(r[2].to).toBeNull();
    expect(r[2].ms).toBeNull();
  });
});

describe('wrHistoryRows', () => {
  it('returns WR history ascending with videos', () => {
    const w = wrHistoryRows(seeded(), 1, 150);
    expect(w.map(x => x.holder_name)).toEqual(['WRHolderA', 'WRHolderB']);
    expect(w[1].record_ms).toBe(104000);
    expect(w[1].video_url).toBe('http://v/b');
  });
});
