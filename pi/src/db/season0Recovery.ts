import type { DatabaseSync } from 'node:sqlite';
import { recomputeIsPb, recomputeWasPb } from './pb';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

export type RecoveredEvent = {
  attempt_id: string; player: string; course_slug: string; cc: number;
  total_time_ms: number; total_time_str: string; ended_at: string;
};

/** Replace Season 0's synthetic legacy_import runs with the recovered real PB progression
 *  (Discord embeds). Only events before the S0/S1 cutover (seasons.ended_at) are taken;
 *  Season 1 (carryover + live) is left untouched. Idempotent: a no-op once S0 holds
 *  discord-sourced runs. */
export function applyRecoveredSeason0(db: DatabaseSync, events: RecoveredEvent[]): void {
  const s0 = db.prepare("SELECT id, ended_at FROM seasons WHERE name='Season 0'")
    .get() as { id: number; ended_at: string | null } | undefined;
  if (!s0) return;
  if (db.prepare("SELECT 1 FROM runs WHERE season_id=? AND source='discord' LIMIT 1").get(s0.id)) return;
  const cutoverMs = s0.ended_at ? Date.parse(s0.ended_at) : Infinity;

  const players = new Map<string, number>();
  for (const p of db.prepare('SELECT id, display_name FROM players').all() as any[]) players.set(p.display_name, p.id);
  const courses = new Map<string, number>();
  for (const c of db.prepare('SELECT id, slug FROM courses').all() as any[]) courses.set(c.slug, c.id);

  db.exec('BEGIN');
  try {
    db.prepare("DELETE FROM runs WHERE season_id=? AND provenance='legacy_import'").run(s0.id);
    const ins = db.prepare(
      `INSERT OR IGNORE INTO runs(attempt_id, season_id, player_id, course_id, cc, status, provenance,
         ended_at, total_time_ms, total_time_str, source)
       VALUES (?,?,?,?,?,'finished','legacy_import',?,?,?,'discord')`
    );
    const scopes = new Set<string>();
    for (const e of events) {
      if (Date.parse(e.ended_at) >= cutoverMs) continue;
      const pid = players.get(e.player), cid = courses.get(e.course_slug);
      if (pid === undefined || cid === undefined) continue;
      ins.run(e.attempt_id, s0.id, pid, cid, e.cc, e.ended_at, e.total_time_ms, e.total_time_str);
      scopes.add(`${pid}|${cid}|${e.cc}`);
    }
    for (const s of scopes) {
      const [pid, cid, cc] = s.split('|').map(Number);
      recomputeIsPb(db, s0.id, pid, cid, cc);
      recomputeWasPb(db, s0.id, pid, cid, cc);
    }
    db.exec('COMMIT');
  } catch (err) { db.exec('ROLLBACK'); throw err; }
}

const DATA_PATH = fileURLToPath(new URL('../../../server/data/season0_recovery.json', import.meta.url));

/** Read the committed recovered-PB data file and apply it to Season 0. Boot-safe:
 *  a missing/corrupt file or any failure logs and returns rather than crashing startup. */
export function migrateSeason0Recovered(db: DatabaseSync, dataPath: string = DATA_PATH): void {
  try {
    const events = JSON.parse(readFileSync(dataPath, 'utf8')) as RecoveredEvent[];
    applyRecoveredSeason0(db, events);
  } catch (err) {
    console.error('[migrate] season0 recovery skipped:', (err as Error).message);
  }
}
