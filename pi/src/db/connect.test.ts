import { describe, it, expect } from 'vitest';
import { openDb } from './connect';

describe('openDb', () => {
  it('opens an in-memory db with foreign keys on', () => {
    const db = openDb(':memory:');
    db.exec('CREATE TABLE t(x INTEGER)');
    db.prepare('INSERT INTO t(x) VALUES (?)').run(7);
    const row = db.prepare('SELECT COUNT(*) c, SUM(x) s FROM t').get() as { c: number; s: number };
    expect(row).toEqual({ c: 1, s: 7 });
    expect((db.prepare('PRAGMA foreign_keys').get() as { foreign_keys: number }).foreign_keys).toBe(1);
  });
});
