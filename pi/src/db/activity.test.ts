// pi/src/db/activity.test.ts
import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';

describe('activity_events schema', () => {
  it('exists with the expected columns after applySchema', () => {
    const db = openDb(':memory:');
    applySchema(db);
    const cols = (db.prepare("PRAGMA table_info(activity_events)").all() as { name: string }[]).map(c => c.name);
    expect(cols).toEqual(expect.arrayContaining(['id', 'ts', 'type', 'season_id', 'player_id', 'course_id', 'cc', 'payload']));
  });
});
