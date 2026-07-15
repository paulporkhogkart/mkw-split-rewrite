import type { DatabaseSync } from 'node:sqlite';
import { resolveLoadout } from './loadout';

type Row = {
  id: number; character: string | null; vehicle: string | null;
  character_slug: string | null; costume_slug: string | null; kart_slug: string | null;
};

/** Re-resolve slugs on rows whose raw names are present but whose slugs are unset. Run after
 *  adding an alias to roster.ts / courses.ts: resolveFlags only clears the flag, it does not
 *  rewrite the slug columns, and an unslugged WR can never be claimed for processing.
 *  Idempotent — returns the number of rows actually updated. */
export function backfillSlugs(db: DatabaseSync): number {
  // A NULL costume_slug is ambiguous (base costume vs unresolved), so candidacy keys off the
  // raw columns; the per-row diff below decides whether anything actually changes.
  const rows = db.prepare(
    `SELECT id, character, vehicle, character_slug, costume_slug, kart_slug
     FROM world_records
     WHERE (character IS NOT NULL OR vehicle IS NOT NULL)`
  ).all() as Row[];

  let n = 0;
  for (const r of rows) {
    const lo = resolveLoadout(r.character, r.vehicle);
    const sets: string[] = [];
    const vals: (string | null)[] = [];
    if (lo.characterSlug !== null && lo.characterSlug !== r.character_slug) { sets.push('character_slug=?'); vals.push(lo.characterSlug); }
    if (lo.costumeSlug !== null && lo.costumeSlug !== r.costume_slug) { sets.push('costume_slug=?'); vals.push(lo.costumeSlug); }
    if (lo.kartSlug !== null && lo.kartSlug !== r.kart_slug) { sets.push('kart_slug=?'); vals.push(lo.kartSlug); }
    if (sets.length === 0) continue;
    db.prepare(`UPDATE world_records SET ${sets.join(', ')} WHERE id=?`).run(...vals, r.id);
    n++;
  }
  return n;
}
