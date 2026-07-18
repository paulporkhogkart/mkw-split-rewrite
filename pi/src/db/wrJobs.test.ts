import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';
import { enqueueJob, seedWrJobs, claimJob, MAX_ATTEMPTS,
  heartbeatJob, releaseJob, completeJob, failJob, deadJobs,
  sweepDeadJobAlerts, markJobAlerted } from './wrJobs';
import { insertWrTrail, getWrTrail } from './wrTrails';
import { EventHub } from '../api/events';
import type { ServerEvent } from './types';

function setup() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'mario_circuit','Mario Circuit')");
  return db;
}

const addWr = (db: any, id: number, opts: { current?: number; video?: string | null } = {}) =>
  db.prepare(`INSERT INTO world_records(id, course_id, cc, holder_name, record_ms, record_str,
                achieved_at, video_url, character_slug, is_current)
              VALUES (?,1,150,'JaK',62934,'1:02.934','2026-04-06T00:00:00.000Z',?, 'toadette', ?)`)
    .run(id, opts.video === undefined ? 'https://youtu.be/x' : opts.video, opts.current ?? 1);

describe('enqueueJob', () => {
  it('inserts once and is idempotent', () => {
    const db = setup(); addWr(db, 10);
    enqueueJob(db, 10);
    enqueueJob(db, 10);
    expect(db.prepare('SELECT COUNT(*) n FROM wr_jobs').get()).toMatchObject({ n: 1 });
  });

  it('does not reset attempts on a repeat enqueue', () => {
    const db = setup(); addWr(db, 10);
    enqueueJob(db, 10);
    db.prepare('UPDATE wr_jobs SET attempts=3 WHERE wr_id=10').run();
    enqueueJob(db, 10);
    expect(db.prepare('SELECT attempts FROM wr_jobs WHERE wr_id=10').get()).toMatchObject({ attempts: 3 });
  });
});

describe('seedWrJobs', () => {
  it('seeds current WRs that have a video and no trail', () => {
    const db = setup(); addWr(db, 10);
    expect(seedWrJobs(db)).toBe(1);
    expect(seedWrJobs(db)).toBe(0);            // idempotent
  });

  // NOTE: each skip case gets its OWN fresh db. `idx_wr_current` (connect.ts:61) is a partial
  // unique index allowing ONE is_current=1 row per (course_id, cc), so two current WRs cannot
  // coexist on the same course in one db.
  it('skips a WR with no video', () => {
    const db = setup(); addWr(db, 10, { video: null });
    expect(seedWrJobs(db)).toBe(0);
  });

  it('skips a non-current WR', () => {
    const db = setup(); addWr(db, 11, { current: 0 });
    expect(seedWrJobs(db)).toBe(0);
  });

  it('skips an already-trailed WR', () => {
    const db = setup(); addWr(db, 12);
    insertWrTrail(db, 12, [{ t_ms: 1, cx: 1, cy: 1, score: 0.9, lap: 1 }]);
    expect(seedWrJobs(db)).toBe(0);
  });

  it('skips soft-removed WRs', () => {
    const db = setup(); addWr(db, 10);
    db.prepare("UPDATE world_records SET removed_at = datetime('now') WHERE id=10").run();
    expect(seedWrJobs(db)).toBe(0);
  });
});

describe('claimJob', () => {
  it('returns null when nothing is queued', () => {
    expect(claimJob(setup(), 'w1')).toBeNull();
  });

  it('claims a queued job, stamps the lease, and counts the attempt', () => {
    const db = setup(); addWr(db, 10); seedWrJobs(db);
    const job = claimJob(db, 'w1');
    expect(job).toMatchObject({
      wr_id: 10, cc: 150, course_slug: 'mario_circuit', course_name: 'Mario Circuit',
      video_url: 'https://youtu.be/x', record_ms: 62934, character_slug: 'toadette', attempt: 1,
    });
    const row = db.prepare('SELECT lease_owner, attempts FROM wr_jobs WHERE wr_id=10').get() as any;
    expect(row).toMatchObject({ lease_owner: 'w1', attempts: 1 });
  });

  it('does not hand the same job to a second worker while leased', () => {
    const db = setup(); addWr(db, 10); seedWrJobs(db);
    expect(claimJob(db, 'w1')).not.toBeNull();
    expect(claimJob(db, 'w2')).toBeNull();
  });

  it('re-offers a job whose lease expired, and burns the attempt (crash recovery)', () => {
    const db = setup(); addWr(db, 10); seedWrJobs(db);
    claimJob(db, 'w1');
    db.prepare("UPDATE wr_jobs SET lease_until = datetime('now','-1 minute') WHERE wr_id=10").run();
    const again = claimJob(db, 'w2');
    expect(again).toMatchObject({ wr_id: 10, attempt: 2 });
  });

  it('skips a WR whose character_slug is unresolved (unprocessable)', () => {
    const db = setup(); addWr(db, 10);
    db.prepare('UPDATE world_records SET character_slug=NULL WHERE id=10').run();
    seedWrJobs(db);
    expect(claimJob(db, 'w1')).toBeNull();
  });

  it('skips a job at the attempts cap', () => {
    const db = setup(); addWr(db, 10); seedWrJobs(db);
    db.prepare('UPDATE wr_jobs SET attempts=? WHERE wr_id=10').run(MAX_ATTEMPTS);
    expect(claimJob(db, 'w1')).toBeNull();
  });

  it('rolls back and surfaces the real error when the WR row is malformed', () => {
    const db = setup(); addWr(db, 10); seedWrJobs(db);
    db.prepare("UPDATE world_records SET lap_splits_ms='not json' WHERE id=10").run();
    // The real JSON error must reach the caller — not a "no transaction is active" ROLLBACK error
    // masking it, which is what happens if anything fallible runs after COMMIT.
    expect(() => claimJob(db, 'w1')).toThrow(/JSON/i);
    // ...and the job must NOT be stranded: no lease stamped, no attempt burned.
    expect(db.prepare('SELECT lease_owner, attempts FROM wr_jobs WHERE wr_id=10').get())
      .toMatchObject({ lease_owner: null, attempts: 0 });
    // Once the data is repaired the job is claimable again, proving the rollback left it intact.
    db.prepare("UPDATE world_records SET lap_splits_ms='[31000,62934]' WHERE id=10").run();
    expect(claimJob(db, 'w2')).toMatchObject({ wr_id: 10, attempt: 1, lap_splits_ms: [31000, 62934] });
  });

  it('skips an already-trailed WR', () => {
    const db = setup(); addWr(db, 10); enqueueJob(db, 10);
    insertWrTrail(db, 10, [{ t_ms: 1, cx: 1, cy: 1, score: 0.9, lap: 1 }]);
    expect(claimJob(db, 'w1')).toBeNull();
  });

  it('prioritises current over superseded, then newest first', () => {
    const db = setup();
    addWr(db, 10, { current: 0 });   // superseded
    addWr(db, 11, { current: 1 });   // current
    enqueueJob(db, 10); enqueueJob(db, 11);
    expect(claimJob(db, 'w1')!.wr_id).toBe(11);   // current wins
    expect(claimJob(db, 'w2')!.wr_id).toBe(10);   // superseded still processed
  });
});

describe('lease lifecycle', () => {
  const queued = () => { const db = setup(); addWr(db, 10); seedWrJobs(db); return db; };

  it('heartbeat extends only for the lease owner', () => {
    const db = queued(); claimJob(db, 'w1', 60);
    expect(heartbeatJob(db, 10, 'w1', 600)).toBe(true);
    expect(heartbeatJob(db, 10, 'w2', 600)).toBe(false);   // not the owner
  });

  it('heartbeat fails once the lease has expired', () => {
    const db = queued(); claimJob(db, 'w1');
    db.prepare("UPDATE wr_jobs SET lease_until = datetime('now','-1 minute') WHERE wr_id=10").run();
    expect(heartbeatJob(db, 10, 'w1', 600)).toBe(false);
  });

  it('release clears the lease AND refunds the attempt (a pause must not burn one)', () => {
    const db = queued(); claimJob(db, 'w1');
    expect(releaseJob(db, 10, 'w1')).toBe(true);
    const row = db.prepare('SELECT lease_owner, lease_until, attempts FROM wr_jobs WHERE wr_id=10').get() as any;
    expect(row).toMatchObject({ lease_owner: null, lease_until: null, attempts: 0 });
    expect(claimJob(db, 'w2')).not.toBeNull();             // immediately re-claimable
  });

  it('release by a non-owner does nothing', () => {
    const db = queued(); claimJob(db, 'w1');
    expect(releaseJob(db, 10, 'w2')).toBe(false);
  });

  it('release on an already-lapsed lease fails and does not refund the attempt', () => {
    const db = queued(); claimJob(db, 'w1');
    db.prepare("UPDATE wr_jobs SET lease_until = datetime('now','-1 minute') WHERE wr_id=10").run();
    expect(releaseJob(db, 10, 'w1')).toBe(false);
    const row = db.prepare('SELECT lease_owner, attempts FROM wr_jobs WHERE wr_id=10').get() as any;
    expect(row).toMatchObject({ lease_owner: 'w1', attempts: 1 });   // untouched: attempt stays burned
  });

  it('complete stores the trail and clears the lease', () => {
    const db = queued(); claimJob(db, 'w1');
    expect(completeJob(db, 10, 'w1', [[14, 1635, 875, 0.79, 1], [114, 1636, 870, 0.81, 1]])).toBe(true);
    expect(getWrTrail(db, 10)).toHaveLength(2);
    expect(db.prepare('SELECT lease_owner FROM wr_jobs WHERE wr_id=10').get()).toMatchObject({ lease_owner: null });
    expect(claimJob(db, 'w2')).toBeNull();                 // done: trail exists
  });

  it('complete accepts a legacy 4-tuple point (lap omitted)', () => {
    const db = queued(); claimJob(db, 'w1');
    expect(completeJob(db, 10, 'w1', [[14, 1635, 875, 0.79]] as any)).toBe(true);
    expect(getWrTrail(db, 10)[0].lap).toBeNull();
  });

  it('complete by a non-owner is rejected and stores nothing', () => {
    const db = queued(); claimJob(db, 'w1');
    expect(completeJob(db, 10, 'w2', [[14, 1635, 875, 0.79, 1]])).toBe(false);
    expect(getWrTrail(db, 10)).toEqual([]);
  });

  it('fail records the reason and clears the lease, keeping the attempt burned', () => {
    const db = queued(); claimJob(db, 'w1');
    expect(failJob(db, 10, 'w1', 'time_mismatch')).toBe(true);
    const row = db.prepare('SELECT lease_owner, last_error, attempts FROM wr_jobs WHERE wr_id=10').get() as any;
    expect(row).toMatchObject({ lease_owner: null, last_error: 'time_mismatch', attempts: 1 });
  });

  it('fail by a non-owner is rejected and does not clear the lease', () => {
    const db = queued(); claimJob(db, 'w1');
    expect(failJob(db, 10, 'w2', 'time_mismatch')).toBe(false);
    expect(db.prepare('SELECT lease_owner, last_error FROM wr_jobs WHERE wr_id=10').get())
      .toMatchObject({ lease_owner: 'w1', last_error: null });   // w1 still holds it
  });

  it('a time_mismatch failure makes the job unclaimable — terminal, not retryable', () => {
    // Re-downloading the same wrong/mislinked video cannot change the verdict; without
    // terminality it burns all 5 attempts (~10 min + ~275MB on Rainbow Road) to reach
    // the same dead end. Spec §6.4's known gap, closed here.
    const db = queued(); claimJob(db, 'w1');
    expect(failJob(db, 10, 'w1', 'time_mismatch detected=62934 expected=62000')).toBe(true);
    expect(claimJob(db, 'w2')).toBeNull();
  });

  it('other failures stay retryable up to the attempts cap', () => {
    const db = queued(); claimJob(db, 'w1');
    failJob(db, 10, 'w1', 'download_failed: HTTP 403');
    expect(claimJob(db, 'w2')).not.toBeNull();
  });
});

describe('deadJobs', () => {
  const dead = () => { const db = setup(); addWr(db, 10); seedWrJobs(db); return db; };

  it('lists a job at the attempts cap', () => {
    const db = dead();
    db.prepare('UPDATE wr_jobs SET attempts=5 WHERE wr_id=10').run();
    expect(deadJobs(db)).toMatchObject([{ wr_id: 10, course: 'Mario Circuit', attempts: 5 }]);
  });

  it('lists a terminal time_mismatch even below the cap', () => {
    const db = dead(); claimJob(db, 'w1');
    failJob(db, 10, 'w1', 'time_mismatch detected=1 expected=2');
    expect(deadJobs(db)).toMatchObject([{ wr_id: 10, attempts: 1 }]);
  });

  it('does not list healthy or already-trailed jobs', () => {
    const db = dead();
    expect(deadJobs(db)).toEqual([]);                            // healthy
    db.prepare('UPDATE wr_jobs SET attempts=5 WHERE wr_id=10').run();
    insertWrTrail(db, 10, [{ t_ms: 1, cx: 1, cy: 1, score: 0.9, lap: 1 }]);
    expect(deadJobs(db)).toEqual([]);                            // done is not dead
  });

  it('sweepDeadJobAlerts announces each dead job exactly once', () => {
    const db = dead();
    db.prepare('UPDATE wr_jobs SET attempts=5 WHERE wr_id=10').run();
    const hub = new EventHub();
    const events: ServerEvent[] = [];
    hub.subscribe((e) => events.push(e));
    expect(sweepDeadJobAlerts(db, hub)).toBe(1);       // silent death found -> alert
    expect(sweepDeadJobAlerts(db, hub)).toBe(0);       // second sweep: already alerted
    expect(events.filter((e) => e.type === 'wr_job_dead')).toMatchObject([
      { wr_id: 10, course: 'Mario Circuit', attempts: 5 },
    ]);
  });

  it('markJobAlerted keeps the route-alerted job out of the sweep', () => {
    const db = dead(); claimJob(db, 'w1');
    failJob(db, 10, 'w1', 'time_mismatch detected=1 expected=2');
    markJobAlerted(db, 10);                            // the /result route alerted already
    const hub = new EventHub();
    expect(sweepDeadJobAlerts(db, hub)).toBe(0);
  });
});
