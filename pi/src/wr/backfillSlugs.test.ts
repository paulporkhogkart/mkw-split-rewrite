import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from '../db/connect';
import { backfillSlugs } from './backfillSlugs';

function setup() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rainbow_road','Rainbow Road')");
  return db;
}

const insertWr = (db: any, character: string | null, vehicle: string | null) =>
  db.prepare(`INSERT INTO world_records(course_id, cc, record_ms, record_str, character, vehicle, is_current)
              VALUES (1,150,100000,'1:40.000',?,?,1)`).run(character, vehicle);

describe('backfillSlugs', () => {
  it('fills slugs that are null but now resolvable', () => {
    const db = setup();
    insertWr(db, 'Toadette (Conductor)', 'Mach Rocket');
    expect(backfillSlugs(db)).toBe(1);
    const row = db.prepare('SELECT character_slug, costume_slug, kart_slug FROM world_records').get() as any;
    expect(row).toMatchObject({ character_slug: 'toadette', costume_slug: 'conductor', kart_slug: 'mach_rocket' });
  });

  it('is idempotent — a second run writes nothing', () => {
    const db = setup();
    insertWr(db, 'Bowser', 'Reel Racer');
    expect(backfillSlugs(db)).toBe(1);
    expect(backfillSlugs(db)).toBe(0);
  });

  it('leaves genuinely unresolvable rows alone', () => {
    const db = setup();
    insertWr(db, 'Zzz Nobody', 'Fake Kart');
    expect(backfillSlugs(db)).toBe(0);
    const row = db.prepare('SELECT character_slug FROM world_records').get() as any;
    expect(row.character_slug).toBeNull();
  });

  it('does not touch a base costume (null costume_slug is correct, not missing)', () => {
    const db = setup();
    insertWr(db, 'Bowser', 'Reel Racer');
    backfillSlugs(db);
    expect(backfillSlugs(db)).toBe(0);
    const row = db.prepare('SELECT costume_slug FROM world_records').get() as any;
    expect(row.costume_slug).toBeNull();
  });
});
