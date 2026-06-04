import type { DatabaseSync } from 'node:sqlite';

export function activeSeasonId(db: DatabaseSync): number {
  const row = db.prepare('SELECT id FROM seasons WHERE is_active = 1 ORDER BY id DESC LIMIT 1').get() as { id: number } | undefined;
  if (!row) throw new Error('no active season');
  return row.id;
}

export function listSeasons(db: DatabaseSync): { id: number; name: string; is_active: number }[] {
  return db.prepare('SELECT id, name, is_active FROM seasons ORDER BY id').all() as any;
}

export function courseIdBySlug(db: DatabaseSync, slug: string): number | null {
  const row = db.prepare('SELECT id FROM courses WHERE slug = ?').get(slug) as { id: number } | undefined;
  return row ? row.id : null;
}
