import type { DatabaseSync } from 'node:sqlite';
import { slugify } from '../db/slug';

/** mkwrs track names whose slug does not match the canonical slug. */
export const MKWRS_ALIASES: Record<string, string> = {
  'Wario Shipyard': 'warios_galleon',
};

export function mkwrsNameToSlug(name: string): string {
  const trimmed = name.trim();
  return MKWRS_ALIASES[trimmed] ?? slugify(trimmed);
}

/** True for mkwrs "(Glitch)" category names, which have no canonical course. */
export function isGlitchCourseName(name: string): boolean {
  return /\(glitch\)/i.test(name);
}

/** Resolve a mkwrs track name to a canonical course id, or null for glitch
 *  categories and any name with no canonical course (caller skips + warns). */
export function resolveCourseId(db: DatabaseSync, name: string): number | null {
  if (isGlitchCourseName(name)) return null;
  const slug = mkwrsNameToSlug(name);
  const row = db.prepare('SELECT id FROM courses WHERE slug=?').get(slug) as { id: number } | undefined;
  return row ? row.id : null;
}
