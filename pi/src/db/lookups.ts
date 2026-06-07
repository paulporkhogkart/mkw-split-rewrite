import type { DatabaseSync } from 'node:sqlite';

export function listCourses(db: DatabaseSync): { slug: string; display_name: string }[] {
  return db.prepare('SELECT slug, display_name FROM courses ORDER BY display_name').all() as any;
}

export function listPlayers(db: DatabaseSync, seasonId: number): { display_name: string }[] {
  return db.prepare(
    `SELECT p.display_name FROM season_rosters sr JOIN players p ON p.id = sr.player_id
     WHERE sr.season_id=? ORDER BY p.display_name`
  ).all(seasonId) as any;
}
