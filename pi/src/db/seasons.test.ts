import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';
import { activeSeasonId, listSeasons, courseIdBySlug } from './seasons';

function seeded() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 0',0),(2,'Season 1',1)");
  db.exec("INSERT INTO courses(slug,display_name) VALUES ('rainbow_road','Rainbow Road')");
  return db;
}

describe('seasons helpers', () => {
  it('activeSeasonId returns the active season', () => {
    expect(activeSeasonId(seeded())).toBe(2);
  });
  it('listSeasons returns all', () => {
    expect(listSeasons(seeded()).map(s => s.name)).toEqual(['Season 0', 'Season 1']);
  });
  it('courseIdBySlug resolves a known slug, null otherwise', () => {
    const db = seeded();
    const rr = courseIdBySlug(db, 'rainbow_road');
    expect(typeof rr).toBe('number');
    expect(courseIdBySlug(db, 'nope')).toBeNull();
  });
});
