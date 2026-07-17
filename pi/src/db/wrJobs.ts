import type { DatabaseSync } from 'node:sqlite';
import type { Point } from './types';
import { insertWrTrail } from './wrTrails';
import type { TrailPoint } from './trailCodec';

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

export type WrJob = {
  wr_id: number; cc: number; course_slug: string; course_name: string;
  video_url: string; record_ms: number; lap_splits_ms: number[] | null;
  character_slug: string; costume_slug: string | null; kart_slug: string | null;
  attempt: number;             // 1-based; drives the worker's retry tiers (spec §6.4)
  lease_until: string;
};

export const DEFAULT_LEASE_SEC = 600;   // 10 min: ~10s download + ~100s processing, wide margin
export const MAX_ATTEMPTS = 5;          // claims per job before it is abandoned as poison

type ClaimRow = {
  wr_id: number; cc: number; course_slug: string; course_name: string;
  video_url: string; record_ms: number; lap_splits_ms: string | null;
  character_slug: string; costume_slug: string | null; kart_slug: string | null;
};

/**
 * Atomically lease the next processable job, or null if there is none.
 *
 * Claimable = enqueued, not removed, has a video, HAS A RESOLVED CHARACTER SLUG (an unslugged
 * WR cannot be turned into a set_selection, so it is unprocessable), has no trail yet, is not
 * under a live lease, and is under the attempts cap.
 *
 * Deliberately NOT filtered on is_current: supersession changes priority, not eligibility — a
 * WR that fell before we got to it is still valid data for that wr_id.
 *
 * attempts increments on CLAIM, not on failure, so a worker that crashes without reporting
 * still burns an attempt and a poison job cannot retry forever. releaseJob() un-does it for a
 * voluntary pause.
 *
 * Two HTTP claims cannot interleave today because server.ts holds ONE DatabaseSync and this
 * function is synchronous (no await), so Node runs it to completion. BEGIN IMMEDIATE is what
 * guards the case that model does not cover: a second connection/process (e.g. the bot) claiming
 * against the same file concurrently. Nothing fallible may follow COMMIT — a throw after it would
 * strand the job leased with an attempt burned, and mask the real error behind a ROLLBACK error.
 */
export function claimJob(db: DatabaseSync, owner: string, leaseSec = DEFAULT_LEASE_SEC): WrJob | null {
  db.exec('BEGIN IMMEDIATE');
  try {
    const row = db.prepare(
      `SELECT j.wr_id, w.cc, w.video_url, w.record_ms, w.lap_splits_ms,
              w.character_slug, w.costume_slug, w.kart_slug,
              c.slug AS course_slug, c.display_name AS course_name
       FROM wr_jobs j
       JOIN world_records w ON w.id = j.wr_id
       JOIN courses c ON c.id = w.course_id
       WHERE w.removed_at IS NULL
         AND w.video_url IS NOT NULL
         AND w.character_slug IS NOT NULL
         AND NOT EXISTS (SELECT 1 FROM wr_trails t WHERE t.wr_id = j.wr_id)
         AND (j.lease_until IS NULL OR j.lease_until < datetime('now'))
         AND j.attempts < ?
         -- time_mismatch is TERMINAL for claiming: the video itself is wrong for this
         -- record, and re-downloading it cannot change that verdict. It needs a human —
         -- or a new link: reconcile's backfill() clears it when video_url changes.
         AND (j.last_error IS NULL OR j.last_error NOT LIKE 'time_mismatch%')
       -- achieved_at DESC: SQLite sorts NULL smallest, so NULL-dated rows sort LAST within their
       -- current/superseded tier here — intended, not a bug (an undated WR has no basis to jump
       -- the queue over a dated one).
       ORDER BY w.is_current DESC, w.achieved_at DESC, j.enqueued_at ASC
       LIMIT 1`
    ).get(MAX_ATTEMPTS) as ClaimRow | undefined;

    if (!row) { db.exec('COMMIT'); return null; }

    const upd = db.prepare(
      `UPDATE wr_jobs
       SET lease_owner=?, lease_until=datetime('now', ?), attempts=attempts+1, updated_at=datetime('now')
       WHERE wr_id=?
       RETURNING attempts, lease_until`
    ).get(owner, `+${leaseSec} seconds`, row.wr_id) as { attempts: number; lease_until: string };

    // Built BEFORE COMMIT: JSON.parse can throw on a malformed lap_splits_ms, and it must roll
    // back cleanly and surface that error rather than strand a leased job with a burnt attempt.
    const job: WrJob = {
      wr_id: row.wr_id, cc: row.cc, course_slug: row.course_slug, course_name: row.course_name,
      video_url: row.video_url, record_ms: row.record_ms,
      lap_splits_ms: row.lap_splits_ms ? (JSON.parse(row.lap_splits_ms) as number[]) : null,
      character_slug: row.character_slug, costume_slug: row.costume_slug, kart_slug: row.kart_slug,
      attempt: upd.attempts, lease_until: upd.lease_until,
    };

    db.exec('COMMIT');
    return job;
  } catch (e) { db.exec('ROLLBACK'); throw e; }
}

/** Wire 4/5-tuple -> TrailPoint. Legacy 4-tuples have no lap; null is the codec's sentinel. */
const toTrailPoints = (pts: Point[]): TrailPoint[] =>
  pts.map((p) => ({ t_ms: p[0], cx: p[1], cy: p[2], score: p[3], lap: p[4] ?? null }));

/** Extend a live lease. Only the current owner of an UNEXPIRED lease may extend — once it has
 *  lapsed the job is fair game and a zombie worker must not reclaim it by heartbeat. */
export function heartbeatJob(db: DatabaseSync, wrId: number, owner: string,
                             leaseSec = DEFAULT_LEASE_SEC): boolean {
  const info = db.prepare(
    `UPDATE wr_jobs SET lease_until=datetime('now', ?), updated_at=datetime('now')
     WHERE wr_id=? AND lease_owner=? AND lease_until >= datetime('now')`
  ).run(`+${leaseSec} seconds`, wrId, owner);
  return Number(info.changes) > 0;
}

/** Voluntarily give a job back (pause mid-processing). Refunds the attempt claimJob charged —
 *  a deliberate pause must not count against the cap, whereas a crash (lease expiry) does. The
 *  lease_until >= datetime('now') guard (same as heartbeatJob) means a lapsed lease can no
 *  longer be "released": once it has expired the job is fair game for reclaim and the attempt
 *  stays burned, matching the crash-recovery path in claimJob. */
export function releaseJob(db: DatabaseSync, wrId: number, owner: string): boolean {
  const info = db.prepare(
    `UPDATE wr_jobs SET lease_owner=NULL, lease_until=NULL,
       attempts=MAX(0, attempts-1), updated_at=datetime('now')
     WHERE wr_id=? AND lease_owner=? AND lease_until >= datetime('now')`
  ).run(wrId, owner);
  return Number(info.changes) > 0;
}

/** Store the extracted trail and close the job. The wr_trails row is what marks it done.
 *  Ownership is enforced by the UPDATE's WHERE inside the transaction (same shape as
 *  heartbeat/release/fail) so the check cannot drift from the mutation. The UPDATE runs BEFORE
 *  insertWrTrail so a non-owner never gets a trail written; a throw from the insert rolls both
 *  back. Nothing fallible may follow COMMIT — see claimJob's docstring.
 *
 *  Does not special-case an empty `pts`: the route rejects that with 400 before calling in here,
 *  and insertWrTrail's packTrail already throws "empty trail" for any other caller that skips
 *  that guard — that throw rolls back the UPDATE too, so a lapsed check here would only have
 *  returned false and been mistaken by the route for "not the lease owner" (409), a lie. */
export function completeJob(db: DatabaseSync, wrId: number, owner: string, pts: Point[]): boolean {
  db.exec('BEGIN IMMEDIATE');
  try {
    const info = db.prepare(
      `UPDATE wr_jobs SET lease_owner=NULL, lease_until=NULL, last_error=NULL,
         updated_at=datetime('now')
       WHERE wr_id=? AND lease_owner=?`
    ).run(wrId, owner);
    if (Number(info.changes) === 0) { db.exec('ROLLBACK'); return false; }
    insertWrTrail(db, wrId, toTrailPoints(pts));
    db.exec('COMMIT');
    return true;
  } catch (e) { db.exec('ROLLBACK'); throw e; }
}

/** Record a failure and free the lease. The attempt claimJob charged stays burned, so repeated
 *  failures walk the job to the cap and stop it. */
export function failJob(db: DatabaseSync, wrId: number, owner: string, error: string): boolean {
  const info = db.prepare(
    `UPDATE wr_jobs SET lease_owner=NULL, lease_until=NULL, last_error=?, updated_at=datetime('now')
     WHERE wr_id=? AND lease_owner=?`
  ).run(error.slice(0, 500), wrId, owner);
  return Number(info.changes) > 0;
}

export type DeadJob = { wr_id: number; course: string; holder_name: string | null;
  record_str: string; attempts: number; last_error: string | null };

/** Jobs that will never be claimed again without a human: at the attempts cap, or
 *  terminally time_mismatched — and still trail-less. Spec §6.4's "cap reached; flag for
 *  Paul": this is what `npm run wr-flags` prints and what the wr_job_dead alert announces. */
export function deadJobs(db: DatabaseSync): DeadJob[] {
  return db.prepare(
    `SELECT j.wr_id, c.display_name AS course, w.holder_name, w.record_str,
            j.attempts, j.last_error
     FROM wr_jobs j
     JOIN world_records w ON w.id = j.wr_id
     JOIN courses c ON c.id = w.course_id
     WHERE NOT EXISTS (SELECT 1 FROM wr_trails t WHERE t.wr_id = j.wr_id)
       AND w.removed_at IS NULL
       AND (j.attempts >= ? OR j.last_error LIKE 'time_mismatch%')
     ORDER BY j.updated_at DESC`
  ).all(MAX_ATTEMPTS) as DeadJob[];
}
