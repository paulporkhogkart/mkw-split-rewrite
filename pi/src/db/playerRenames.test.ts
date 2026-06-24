import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';
import { migratePlayerRenames } from './playerRenames';

describe('migratePlayerRenames', () => {
  it('renames Paul -> Paul Pork, keeps player_id, leaves others, and is idempotent', () => {
    const db = openDb(':memory:'); applySchema(db);
    db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul'),(2,'Gub')");
    migratePlayerRenames(db);
    expect((db.prepare('SELECT display_name FROM players WHERE id=1').get() as { display_name: string }).display_name).toBe('Paul Pork');
    expect((db.prepare('SELECT display_name FROM players WHERE id=2').get() as { display_name: string }).display_name).toBe('Gub');
    migratePlayerRenames(db);   // no-op the second time
    expect((db.prepare("SELECT COUNT(*) c FROM players WHERE display_name='Paul Pork'").get() as { c: number }).c).toBe(1);
    expect((db.prepare("SELECT COUNT(*) c FROM players WHERE display_name='Paul'").get() as { c: number }).c).toBe(0);
  });
});
