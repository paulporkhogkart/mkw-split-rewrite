import type { DatabaseSync } from 'node:sqlite';

/** Enqueue a WR for trail extraction. Idempotent and non-destructive: a repeat enqueue must not
 *  reset attempts, or a poison job would retry forever. */
export function enqueueJob(db: DatabaseSync, wrId: number): void {
  db.prepare('INSERT INTO wr_jobs(wr_id) VALUES (?) ON CONFLICT(wr_id) DO NOTHING').run(wrId);
}

/** Seed jobs for every current, non-removed, videoed WR that has no trail yet. Runs on every
 *  boot; idempotent (ON CONFLICT DO NOTHING preserves an existing row's attempts).
 *  Returns the number of jobs added. */
export function seedWrJobs(db: DatabaseSync): number {
  const info = db.prepare(
    `INSERT INTO wr_jobs(wr_id)
     SELECT w.id FROM world_records w
     WHERE w.is_current = 1
       AND w.removed_at IS NULL
       AND w.video_url IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM wr_trails t WHERE t.wr_id = w.id)
     ON CONFLICT(wr_id) DO NOTHING`
  ).run();
  return Number(info.changes);
}
