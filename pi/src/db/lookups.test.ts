import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';
import { listCourses, listPlayers } from './lookups';

function seeded() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 1',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul'),(2,'Luke'),(3,'Zoe')");
  // Only Paul and Luke are on the season roster; Zoe is not.
  db.exec("INSERT INTO season_rosters(season_id,player_id) VALUES (1,1),(1,2)");
  // Two courses: inserted out of alphabetical order to verify ORDER BY display_name.
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rainbow_road','Rainbow Road'),(2,'dk_pass','DK Pass')");
  return db;
}

describe('listCourses', () => {
  it('returns slug + display_name ordered by display_name', () => {
    const courses = listCourses(seeded());
    expect(courses).toEqual([
      { slug: 'dk_pass', display_name: 'DK Pass' },
      { slug: 'rainbow_road', display_name: 'Rainbow Road' },
    ]);
  });

  it('returns empty array when no courses', () => {
    const db = openDb(':memory:');
    applySchema(db);
    expect(listCourses(db)).toEqual([]);
  });
});

describe('listPlayers', () => {
  it('returns display_name for season roster members, ordered by display_name', () => {
    const players = listPlayers(seeded(), 1);
    expect(players).toEqual([
      { display_name: 'Luke' },
      { display_name: 'Paul' },
    ]);
  });

  it('excludes players not in the season roster', () => {
    const players = listPlayers(seeded(), 1);
    const names = players.map((p) => p.display_name);
    expect(names).not.toContain('Zoe');
  });

  it('returns empty array when no roster members for the season', () => {
    const db = openDb(':memory:');
    applySchema(db);
    db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 1',1)");
    expect(listPlayers(db, 1)).toEqual([]);
  });
});
