import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';

function cols(db: any, table: string): Set<string> {
  return new Set((db.prepare(`PRAGMA table_info(${table})`).all() as any[]).map((r) => r.name));
}

describe('history schema migration', () => {
  const db = openDb(':memory:');
  applySchema(db);

  it('adds the history columns to world_records', () => {
    const c = cols(db, 'world_records');
    for (const name of ['nation', 'character_slug', 'kart_slug', 'costume_slug',
      'lap_splits_ms', 'coins', 'mushrooms', 'date_precision', 'removed_at', 'source_raw']) {
      expect(c.has(name)).toBe(true);
    }
  });

  it('creates wr_name_flags with a unique (category, raw_value)', () => {
    db.exec(`INSERT INTO wr_name_flags(category, raw_value) VALUES ('kart','X')`);
    expect(() => db.exec(`INSERT INTO wr_name_flags(category, raw_value) VALUES ('kart','X')`)).toThrow();
  });

  it('creates wr_meta key/value', () => {
    db.exec(`INSERT INTO wr_meta(key, value) VALUES ('history_cursor','3')`);
    const row = db.prepare(`SELECT value FROM wr_meta WHERE key='history_cursor'`).get() as any;
    expect(row.value).toBe('3');
  });
});
