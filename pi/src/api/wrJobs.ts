import { Hono } from 'hono';
import type { Context } from 'hono';
import type { DatabaseSync } from 'node:sqlite';
import type { Env } from './app';
import type { Point } from '../db/types';
import type { EventHub } from './events';
import { requireToken } from './auth';
import { claimJob, heartbeatJob, releaseJob, completeJob, failJob, deadJobs, markJobAlerted, DEFAULT_LEASE_SEC } from '../db/wrJobs';

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
      // Did that failure kill the job (attempts cap, or terminal time_mismatch)? Then a
      // human is the only thing that can move it — spec §6.4 "cap reached; flag for
      // Paul". Same predicate `npm run wr-flags` prints, so alert and listing agree.
      const dead = deadJobs(db).find((d) => d.wr_id === wrId);
      if (dead) {
        hub.publish({ type: 'wr_job_dead', wr_id: dead.wr_id, course: dead.course,
          holder: dead.holder_name, record_str: dead.record_str,
          reason: dead.last_error ?? 'unknown', attempts: dead.attempts });
        markJobAlerted(db, dead.wr_id);
      }
      return c.json({ ok: true });
    }
    return c.json({ error: 'not the lease owner' }, 409);
  });

  return r;
}
