import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';

const TABLES = ['seasons','players','season_rosters','courses','runs','run_laps','run_trails','world_records','ghost_imports'];

describe('applySchema', () => {
  it('creates every canonical table + the auth column', () => {
    const db = openDb(':memory:');
    applySchema(db);
    const names = (db.prepare("SELECT name FROM sqlite_master WHERE type='table'").all() as { name: string }[])
      .map(r => r.name);
    for (const t of TABLES) expect(names).toContain(t);
    const cols = (db.prepare('PRAGMA table_info(players)').all() as { name: string }[]).map(c => c.name);
    expect(cols).toContain('auth_token_hash');
  });

  it('is idempotent', () => {
    const db = openDb(':memory:');
    applySchema(db);
    expect(() => applySchema(db)).not.toThrow();
  });

  it('has runs.source and a ghost_imports table', () => {
    const db = openDb(':memory:');
    applySchema(db);
    const cols = (db.prepare('PRAGMA table_info(runs)').all() as { name: string }[]).map(c => c.name);
    expect(cols).toContain('source');
    expect(() => db.prepare('SELECT id, run_id, player_id, course_id, cc, total_time_ms, action FROM ghost_imports').all()).not.toThrow();
  });

  it('fresh DBs no longer create the legacy run_points table', () => {
    const db = openDb(':memory:');
    applySchema(db);
    expect(db.prepare("SELECT 1 FROM sqlite_master WHERE name='run_points'").get()).toBeUndefined();
  });
});
