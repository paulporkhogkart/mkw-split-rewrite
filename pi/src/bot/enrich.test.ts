import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from '../db/connect';
import { buildWrData } from './enrich';
import type { ServerEvent } from '../db/types';

function db1() {
  const db = openDb(':memory:'); applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'mario_bros_circuit','Mario Bros. Circuit')");
  db.exec("INSERT INTO world_records(course_id,cc,holder_name,record_ms,record_str,achieved_at,is_current) VALUES " +
    "(1,150,'Luke',100000,'1:40.000','2026-01-01T00:00:00.000Z',0)," +
    "(1,150,'Paul',99000,'1:39.000','2026-02-01T00:00:00.000Z',1)");
  return db;
}

const wrEvent: Extract<ServerEvent, { type: 'wr_update' }> = {
  type: 'wr_update', course: 'Mario Bros. Circuit', cc: 150, holder: 'Paul',
  total_time: '1:39.000', prev_holder: 'Luke', prev_time: '1:40.000',
  improvement_ms: 1000, character: null, vehicle: null, video_url: null,
};

describe('buildWrData', () => {
  it('resolves the course display name, formats the delta, and includes reign', () => {
    const d = buildWrData(db1(), wrEvent);
    expect(d.holder).toBe('Paul');
    expect(d.track).toBe('Mario Bros. Circuit');
    expect(d.record).toBe('1:39.000');
    expect(d.improvement_str).toBe('-1.000s');     // 1000ms faster, shown as a negative delta
    expect(d.reign?.previous_holder).toBe('Luke');
    expect(d.reign?.is_same_person).toBe(false);
  });
});
