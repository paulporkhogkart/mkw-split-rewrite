// pi/src/db/activity.test.ts
import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';
import { insertActivityEvents, recentActivity } from './activity';

describe('activity_events schema', () => {
  it('exists with the expected columns after applySchema', () => {
    const db = openDb(':memory:');
    applySchema(db);
    const cols = (db.prepare("PRAGMA table_info(activity_events)").all() as { name: string }[]).map(c => c.name);
    expect(cols).toEqual(expect.arrayContaining(['id', 'ts', 'type', 'season_id', 'player_id', 'course_id', 'cc', 'payload']));
  });
});

function seed() {
  const db = openDb(':memory:'); applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
  db.exec("INSERT INTO players(id,display_name,color) VALUES (1,'Gub','#38bdf8'),(2,'Paul','#a78bfa')");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'crown_city','Crown City')");
  return db;
}

describe('activity persist/read', () => {
  it('inserts then reads newest-first, resolving names/colours + rival', () => {
    const db = seed();
    insertActivityEvents(db, [
      { ts: 1000, type: 'pb', season_id: 1, player_id: 1, course_id: 1, cc: 150, payload: { time_ms: 107980, time_str: '1:47.980', delta_ms: -430 } },
      { ts: 1000, type: 'turf_claim', season_id: 1, player_id: 1, course_id: 1, cc: 150, payload: { rival_id: 2 } },
    ]);
    const out = recentActivity(db, { seasonId: 1, limit: 10 });
    expect(out.map(e => e.type)).toEqual(['turf_claim', 'pb']); // newest (highest id) first
    expect(out[0].player).toEqual({ id: 1, name: 'Gub', color: '#38bdf8' });
    expect(out[0].course).toEqual({ slug: 'crown_city', name: 'Crown City' });
    expect((out[0].payload as any).rival).toEqual({ id: 2, name: 'Paul', color: '#a78bfa' });
    expect(out[1].payload).toMatchObject({ time_str: '1:47.980', delta_ms: -430 });
  });

  it('paginates with `before`', () => {
    const db = seed();
    const ids = insertActivityEvents(db, [
      { ts: 1, type: 'pb', season_id: 1, player_id: 1, course_id: 1, cc: 150, payload: {} },
      { ts: 2, type: 'pb', season_id: 1, player_id: 1, course_id: 1, cc: 150, payload: {} },
    ]);
    const page = recentActivity(db, { seasonId: 1, before: ids[1], limit: 10 });
    expect(page.map(e => e.id)).toEqual([ids[0]]);
  });
});
