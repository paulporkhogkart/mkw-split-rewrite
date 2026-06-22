import type { DatabaseSync } from 'node:sqlite';
import { resolveItem, type ItemCategory } from './roster';

export type FlagInput = {
  category: ItemCategory | 'course';
  rawValue: string;
  slugGuess?: string;
  exampleCourseId?: number;
  exampleWrId?: number;
};

/** Record an unresolved name. Idempotent on (category, raw_value): increments occurrences and
 *  clears any stale resolved_at (it is unresolved again right now). */
export function upsertFlag(db: DatabaseSync, f: FlagInput): void {
  db.prepare(
    `INSERT INTO wr_name_flags(category, raw_value, slug_guess, example_course_id, example_wr_id, occurrences)
     VALUES (?,?,?,?,?,1)
     ON CONFLICT(category, raw_value) DO UPDATE SET
       occurrences = occurrences + 1,
       slug_guess = excluded.slug_guess,
       resolved_at = NULL`
  ).run(f.category, f.rawValue, f.slugGuess ?? null, f.exampleCourseId ?? null, f.exampleWrId ?? null);
}

/** Re-check every unresolved flag (non-course categories) against the current roster/aliases and
 *  stamp resolved_at on any that now resolve. Returns the count resolved. */
export function resolveFlags(db: DatabaseSync): number {
  const rows = db.prepare(
    `SELECT id, category, raw_value FROM wr_name_flags WHERE resolved_at IS NULL`
  ).all() as { id: number; category: string; raw_value: string }[];
  let n = 0;
  for (const r of rows) {
    if (r.category === 'course') continue;
    if (resolveItem(r.category as ItemCategory, r.raw_value).slug !== null) {
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
