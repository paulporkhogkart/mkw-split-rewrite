import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';

const TABLES = ['seasons','players','season_rosters','courses','runs','run_laps','run_points','world_records'];

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
});
