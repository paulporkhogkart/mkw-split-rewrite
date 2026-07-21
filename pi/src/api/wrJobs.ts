import { Hono } from 'hono';
import type { Context } from 'hono';
import type { DatabaseSync } from 'node:sqlite';
import type { Env } from './app';
import type { Point } from '../db/types';
import type { EventHub } from './events';
import { requireToken } from './auth';
import { claimJob, heartbeatJob, releaseJob, completeJob, failJob, stuckJobs, markJobAlerted, DEFAULT_LEASE_SEC, wrJobsStatus } from '../db/wrJobs';

type ResultBody = { ok: true; points: Point[] } | { ok: false; error: string };

/** Per-MACHINE lease identity. The player token authenticates the person; one person may run the
 *  service on several PCs, so the lease owner must be the machine or one PC could complete
 *  another's job. Generated once per install and persisted by the service. */
const workerIdOf = (c: Context): string | null => {
  const v = c.req.header('x-worker-id')?.trim();
  return v && v.length > 0 && v.length <= 64 ? v : null;
};

/** Parse the `:wr_id` path param, or null if it isn't a plain integer (route 400s). */
const parseWrId = (c: Context): number | null => {
  const n = Number(c.req.param('wr_id'));
  return Number.isInteger(n) ? n : null;
};

/** Wire shape is [t_ms, cx, cy, score, lap?] — shape/type only, no range validation (cheap). */
const isValidPoint = (p: unknown): p is Point =>
  Array.isArray(p) && (p.length === 4 || p.length === 5) &&
  p.slice(0, 4).every((v) => typeof v === 'number' && Number.isFinite(v)) &&
  (p.length === 4 || p[4] === null || (typeof p[4] === 'number' && Number.isFinite(p[4])));

/** WR-service worker API. Auth is the ordinary player token, header-only (a ?token= in a write
 *  URL would leak into logs) — the same double-gate POST /v1/runs uses: the app-level
 *  requireTokenAny runs first, then requireToken here narrows it to header-only. */
export function wrJobsRoutes(db: DatabaseSync, hub: EventHub): Hono<Env> {
  const r = new Hono<Env>();

  // Public read-only status board for the hidden site page (/wr-jobs). Token-free via
  // PUBLIC_READS (exact path — the worker POST routes below live on subpaths and stay gated).
  r.get('/v1/wr-jobs', (c) => c.json({ jobs: wrJobsStatus(db) }));

  r.post('/v1/wr-jobs/claim', requireToken(db), (c) => {
    const worker = workerIdOf(c);
    if (!worker) return c.json({ error: 'missing X-Worker-Id' }, 400);
    const job = claimJob(db, worker, DEFAULT_LEASE_SEC);
    return job ? c.json(job) : c.body(null, 204);
  });

  r.post('/v1/wr-jobs/:wr_id/heartbeat', requireToken(db), (c) => {
    const worker = workerIdOf(c);
    if (!worker) return c.json({ error: 'missing X-Worker-Id' }, 400);
    const wrId = parseWrId(c);
    if (wrId === null) return c.json({ error: 'bad wr_id' }, 400);
    const ok = heartbeatJob(db, wrId, worker, DEFAULT_LEASE_SEC);
    return ok ? c.json({ ok: true }) : c.json({ error: 'not the lease owner, or lease expired' }, 409);
  });

  r.post('/v1/wr-jobs/:wr_id/release', requireToken(db), (c) => {
    const worker = workerIdOf(c);
    if (!worker) return c.json({ error: 'missing X-Worker-Id' }, 400);
    const wrId = parseWrId(c);
    if (wrId === null) return c.json({ error: 'bad wr_id' }, 400);
    const ok = releaseJob(db, wrId, worker);
    return ok ? c.json({ ok: true }) : c.json({ error: 'not the lease owner, or lease expired' }, 409);
  });

  r.post('/v1/wr-jobs/:wr_id/result', requireToken(db), async (c) => {
    const worker = workerIdOf(c);
    if (!worker) return c.json({ error: 'missing X-Worker-Id' }, 400);
    const wrId = parseWrId(c);
    if (wrId === null) return c.json({ error: 'bad wr_id' }, 400);
    const body = (await c.req.json()) as ResultBody;
    if (typeof body?.ok !== 'boolean') return c.json({ error: 'bad payload' }, 400);

    if (body.ok) {
      if (!Array.isArray(body.points) || body.points.length === 0) {
        return c.json({ error: 'empty trail' }, 400);
      }
      const badIdx = body.points.findIndex((p) => !isValidPoint(p));
      if (badIdx !== -1) return c.json({ error: `bad point at index ${badIdx}` }, 400);
      const stored = completeJob(db, wrId, worker, body.points);
      return stored ? c.json({ ok: true, n: body.points.length })
                    : c.json({ error: 'not the lease owner' }, 409);
    }
    const recorded = failJob(db, wrId, worker, body.error ?? 'unknown');
    if (recorded) {
      // Did that failure push the job into stuck territory (retry cooldown, or a parked
      // time_mismatch)? Flag it for Paul once — informational, not terminal: everything
      // except time_mismatch keeps retrying on the claim cooldown. Same predicate
      // `npm run wr-flags` prints, so alert and listing agree.
      const stuck = stuckJobs(db).find((d) => d.wr_id === wrId);
      if (stuck) {
        hub.publish({ type: 'wr_job_stuck', wr_id: stuck.wr_id, course: stuck.course,
          holder: stuck.holder_name, record_str: stuck.record_str,
          reason: stuck.last_error ?? 'unknown', attempts: stuck.attempts });
        markJobAlerted(db, stuck.wr_id);
      }
      return c.json({ ok: true });
    }
    return c.json({ error: 'not the lease owner' }, 409);
  });

  return r;
}
