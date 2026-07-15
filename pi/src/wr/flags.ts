import type { DatabaseSync } from 'node:sqlite';
import { resolveItem, type ItemCategory } from './roster';
import { mkwrsNameToSlug } from './courses';

export type FlagInput = {
  category: ItemCategory | 'course';
  rawValue: string;
  slugGuess?: string;
  exampleCourseId?: number;
  exampleWrId?: number;
};

/** Record an unresolved name. Idempotent on (category, raw_value): increments occurrences and
 *  clears any stale resolved_at (it is unresolved again right now).
 *  Returns `{isNew: true}` only on the very first sighting — callers alert on that alone, or a
 *  15-minute scraper would re-announce the same broken name forever. Trade-off: a name that was
 *  resolved and later breaks again does NOT re-alert; `npm run wr-flags` still lists it. */
export function upsertFlag(db: DatabaseSync, f: FlagInput): { isNew: boolean } {
  const row = db.prepare(
    `INSERT INTO wr_name_flags(category, raw_value, slug_guess, example_course_id, example_wr_id, occurrences)
     VALUES (?,?,?,?,?,1)
     ON CONFLICT(category, raw_value) DO UPDATE SET
       occurrences = occurrences + 1,
       slug_guess = excluded.slug_guess,
       resolved_at = NULL
     RETURNING occurrences`
  ).get(f.category, f.rawValue, f.slugGuess ?? null, f.exampleCourseId ?? null, f.exampleWrId ?? null) as { occurrences: number };
  return { isNew: row.occurrences === 1 };
}

/** Re-check every unresolved flag against the current roster/aliases (items) or the courses
 *  table (courses) and stamp resolved_at on any that now resolve. Returns the count resolved. */
export function resolveFlags(db: DatabaseSync): number {
  const rows = db.prepare(
    `SELECT id, category, raw_value FROM wr_name_flags WHERE resolved_at IS NULL`
  ).all() as { id: number; category: string; raw_value: string }[];
  let n = 0;
  for (const r of rows) {
    const resolved = r.category === 'course'
      ? db.prepare('SELECT 1 FROM courses WHERE slug=?').get(mkwrsNameToSlug(r.raw_value)) != null
      : resolveItem(r.category as ItemCategory, r.raw_value).slug !== null;
    if (resolved) {
      db.prepare(`UPDATE wr_name_flags SET resolved_at = datetime('now') WHERE id=?`).run(r.id);
      n++;
    }
  }
  return n;
}

/** Human-readable list of unresolved flags, grouped by category. */
export function reportFlags(db: DatabaseSync): string {
  const rows = db.prepare(
    `SELECT category, raw_value, slug_guess, occurrences FROM wr_name_flags
     WHERE resolved_at IS NULL ORDER BY category, occurrences DESC, raw_value`
  ).all() as { category: string; raw_value: string; slug_guess: string | null; occurrences: number }[];
  if (rows.length === 0) return 'No unresolved name flags.';
  const out: string[] = [`${rows.length} unresolved name flag(s):`];
  let cat = '';
  for (const r of rows) {
    if (r.category !== cat) { cat = r.category; out.push(`\n[${cat}]`); }
    out.push(`  ${r.raw_value}  (slug ${r.slug_guess ?? '?'}, x${r.occurrences})`);
  }
  return out.join('\n');
}
