import type { DatabaseSync } from 'node:sqlite';

// One-time, idempotent display-name corrections, applied on every boot (here + on deploy). Each
// rename is a no-op once applied (the old name no longer exists). player_id is unchanged, so runs,
// tokens, colours and every foreign-key reference are unaffected - only the displayed name moves.
const RENAMES: Array<[from: string, to: string]> = [
  ['Paul', 'paul pork'],
  ['Paul Pork', 'paul pork'],   // also fix the interim proper-case rename (local DBs / a prior boot)
];

export function migratePlayerRenames(db: DatabaseSync): void {
  const stmt = db.prepare('UPDATE players SET display_name=? WHERE display_name=?');
  for (const [from, to] of RENAMES) {
    try { stmt.run(to, from); } catch { /* non-fatal: never block boot on a rename */ }
  }
}
