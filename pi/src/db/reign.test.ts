import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';
import { wrReign } from './reign';

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
