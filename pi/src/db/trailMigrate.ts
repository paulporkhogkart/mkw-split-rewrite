import type { DatabaseSync } from 'node:sqlite';
import { CODEC_BROTLI_V1, assertTrailsIdentical, decodeTrail, encodeTrail, type TrailPoint } from './trailCodec';

export type TrailMigration = { migrated: number; failed: number; orphaned: number; dropped: boolean };

/** One-time, resumable run_points → run_trails migration (runs at boot, before listen).
 *  Per run: read rows in t order → encode → decode → BIT-VERIFY against the rows → insert
 *  blob + delete rows in one transaction. A verify failure keeps that run's rows. Orphan
 *  rows (run_id not in runs) are never deleted. The table is dropped only when empty —
 *  space is reclaimed by a later manual VACUUM (see docs/pi-deploy.md). */
export function migrateTrails(db: DatabaseSync): TrailMigration {
  const res: TrailMigration = { migrated: 0, failed: 0, orphaned: 0, dropped: false };
  if (!db.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name='run_points'").get()) return res;
  const ids = db.prepare(
    `SELECT DISTINCT rp.run_id FROM run_points rp
     JOIN runs r ON r.id = rp.run_id
     WHERE NOT EXISTS (SELECT 1 FROM run_trails rt WHERE rt.run_id = rp.run_id)`
  ).all() as { run_id: number }[];
  const rowsStmt = db.prepare('SELECT t_ms, cx, cy, score, lap FROM run_points WHERE run_id=? ORDER BY t_ms');
  for (const { run_id } of ids) {
    const rows = rowsStmt.all(run_id) as TrailPoint[];
    try {
      const blob = encodeTrail(rows);
      assertTrailsIdentical(rows, decodeTrail(blob));
      db.exec('BEGIN');
      db.prepare('INSERT INTO run_trails(run_id, codec, n, max_t_ms, data) VALUES (?,?,?,?,?)')
        .run(run_id, CODEC_BROTLI_V1, rows.length, rows[rows.length - 1].t_ms, blob);
      db.prepare('DELETE FROM run_points WHERE run_id=?').run(run_id);
      db.exec('COMMIT');
      res.migrated++;
    } catch (e) {
      try { db.exec('ROLLBACK'); } catch { /* encode/verify threw before BEGIN */ }
      res.failed++;
      console.error(`[trails] migration verify FAILED for run ${run_id} — rows kept:`, e);
    }
  }
  res.orphaned = (db.prepare(
    'SELECT COUNT(DISTINCT run_id) c FROM run_points WHERE run_id NOT IN (SELECT id FROM runs)'
  ).get() as { c: number }).c;
  if (!(db.prepare('SELECT EXISTS(SELECT 1 FROM run_points) e').get() as { e: number }).e) {
    db.exec('DROP TABLE run_points');
    res.dropped = true;
  }
  if (res.migrated || res.failed || res.orphaned)
    console.log(`[trails] migration: ${res.migrated} migrated, ${res.failed} failed, ${res.orphaned} orphaned run_ids; `
      + (res.dropped ? 'run_points dropped — run VACUUM to reclaim space.' : 'run_points KEPT.'));
  return res;
}
