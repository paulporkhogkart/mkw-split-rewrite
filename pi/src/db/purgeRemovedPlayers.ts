import type { DatabaseSync } from 'node:sqlite';

// Players removed from the kart-off entirely. Idempotent: once a player's rows are gone, re-running
// is a no-op. Deletes ALL data for the player (runs, laps, trails, activity, alignment, roster) and
// the players row itself — which also invalidates their auth token. Applied on every boot.
const REMOVED_PLAYERS = ['Alex'];   // display_name, matched COLLATE NOCASE

export function purgeRemovedPlayers(db: DatabaseSync): void {
  for (const name of REMOVED_PLAYERS) {
    const row = db.prepare('SELECT id FROM players WHERE display_name = ? COLLATE NOCASE')
      .get(name) as { id: number } | undefined;
    if (!row) continue;                       // idempotent: already gone
    const id = row.id;
    db.exec('BEGIN');
    try {
      // FK-safe order (foreign_keys=ON): children first, then runs (cascades laps/trails),
      // then the remaining player-referencing tables, then the players row. Each statement is
      // guarded so a table absent on an older/fresh DB never blocks boot.
      const del = (sql: string) => { try { db.prepare(sql).run(id); } catch { /* table may not exist */ } };
      del('DELETE FROM ghost_imports    WHERE player_id = ?');   // references runs(id) w/o cascade — precede runs
      del('DELETE FROM run_points       WHERE run_id IN (SELECT id FROM runs WHERE player_id = ?)'); // retired table; may persist on prod
      del('DELETE FROM runs             WHERE player_id = ?');   // cascades run_laps, run_trails
      del('DELETE FROM screen_intervals WHERE player_id = ?');
      del('DELETE FROM activity_events  WHERE player_id = ?');
      del('DELETE FROM player_alignment WHERE player_id = ?');
      del('DELETE FROM season_rosters   WHERE player_id = ?');
      del('DELETE FROM players          WHERE id = ?');
      db.exec('COMMIT');
    } catch {
      db.exec('ROLLBACK');   // non-fatal: never block boot on the purge
    }
  }
}
