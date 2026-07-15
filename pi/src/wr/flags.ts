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
 *
 *  Two call sites write flags (the current-WR reconciler, which has an EventHub and can alert,
 *  and the history reconciler, which cannot), so "should we alert" can't be inferred from a
 *  sighting count — whichever call site runs first would win the count and the other could never
 *  alert. Instead this returns `{shouldAlert: true}` whenever the flag is unresolved and no alert
 *  has been stamped for it yet (`alerted_at IS NULL`); the caller must actually publish before
 *  calling `markFlagAlerted`, which is the only thing that stamps `alerted_at`. Callers that
 *  cannot alert (history reconciler) just ignore the return — the flag stays owed until a caller
 *  that can alert sees it.
 *
 *  Re-alert on regression: if the flag had previously been resolved (`resolved_at` was non-null)
 *  and breaks again, `alerted_at` is cleared along with `resolved_at` so it alerts again. */
export function upsertFlag(db: DatabaseSync, f: FlagInput): { shouldAlert: boolean } {
  const row = db.prepare(
    `INSERT INTO wr_name_flags(category, raw_value, slug_guess, example_course_id, example_wr_id, occurrences)
     VALUES (?,?,?,?,?,1)
     ON CONFLICT(category, raw_value) DO UPDATE SET
       occurrences = occurrences + 1,
       slug_guess = excluded.slug_guess,
       alerted_at = CASE WHEN wr_name_flags.resolved_at IS NOT NULL THEN NULL ELSE wr_name_flags.alerted_at END,
       resolved_at = NULL
     RETURNING alerted_at`
  ).get(f.category, f.rawValue, f.slugGuess ?? null, f.exampleCourseId ?? null, f.exampleWrId ?? null) as
    { alerted_at: string | null };
  // resolved_at is always NULL immediately after this statement (set unconditionally above, since
  // the row is unresolved again right now), so "shouldAlert" reduces to "no alert stamped yet".
  return { shouldAlert: row.alerted_at === null };
}

/** Stamp that an alert was actually published for this (category, raw_value). Only the caller
 *  that publishes may call this — see `upsertFlag`'s doc comment. */
export function markFlagAlerted(db: DatabaseSync, category: FlagInput['category'], rawValue: string): void {
  db.prepare(
    `UPDATE wr_name_flags SET alerted_at = datetime('now') WHERE category=? AND raw_value=?`
  ).run(category, rawValue);
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
