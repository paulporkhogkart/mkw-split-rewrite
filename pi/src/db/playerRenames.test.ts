import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';
import { migratePlayerRenames } from './playerRenames';

describe('migratePlayerRenames', () => {
  it('lowercases Paul to "paul pork", keeps id, leaves others, idempotent', () => {
    const db = openDb(':memory:'); applySchema(db);
    db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul'),(2,'Gub')");
    migratePlayerRenames(db);
    expect((db.prepare('SELECT display_name FROM players WHERE id=1').get() as { display_name: string }).display_name).toBe('paul pork');
    expect((db.prepare('SELECT display_name FROM players WHERE id=2').get() as { display_name: string }).display_name).toBe('Gub');
    migratePlayerRenames(db);   // no-op the second time
    expect((db.prepare("SELECT COUNT(*) c FROM players WHERE display_name IN ('Paul','Paul Pork')").get() as { c: number }).c).toBe(0);
  });

  it('also fixes an interim proper-case "Paul Pork" to "paul pork"', () => {
    const db = openDb(':memory:'); applySchema(db);
    db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul Pork')");
    migratePlayerRenames(db);
    expect((db.prepare('SELECT display_name FROM players WHERE id=1').get() as { display_name: string }).display_name).toBe('paul pork');
  });
});
