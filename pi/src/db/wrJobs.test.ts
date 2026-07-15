import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from './connect';
import { enqueueJob, seedWrJobs, claimJob } from './wrJobs';
import { insertWrTrail } from './wrTrails';

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
    db.prepare('UPDATE wr_jobs SET attempts=5 WHERE wr_id=10').run();
    expect(claimJob(db, 'w1')).toBeNull();
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
