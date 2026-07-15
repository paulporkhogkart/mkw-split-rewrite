import { Hono } from 'hono';
import type { Context } from 'hono';
import type { DatabaseSync } from 'node:sqlite';
import type { Env } from './app';
import type { Point } from '../db/types';
import { requireToken } from './auth';
import { claimJob, heartbeatJob, releaseJob, completeJob, failJob, DEFAULT_LEASE_SEC } from '../db/wrJobs';

type ResultBody = { ok: true; points: Point[] } | { ok: false; error: string };

/** Per-MACHINE lease identity. The player token authenticates the person; one person may run the
 *  service on several PCs, so the lease owner must be the machine or one PC could complete
 *  another's job. Generated once per install and persisted by the service. */
const workerIdOf = (c: Context): string | null => {
  const v = c.req.header('x-worker-id')?.trim();
  return v && v.length > 0 && v.length <= 64 ? v : null;
};

/** WR-service worker API. Auth is the ordinary player token, header-only (a ?token= in a write
 *  URL would leak into logs) — the same double-gate POST /v1/runs uses: the app-level
 *  requireTokenAny runs first, then requireToken here narrows it to header-only. */
export function wrJobsRoutes(db: DatabaseSync): Hono<Env> {
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
    const ok = heartbeatJob(db, Number(c.req.param('wr_id')), worker, DEFAULT_LEASE_SEC);
    return ok ? c.json({ ok: true }) : c.json({ error: 'not the lease owner, or lease expired' }, 409);
  });

  r.post('/v1/wr-jobs/:wr_id/release', requireToken(db), (c) => {
    const worker = workerIdOf(c);
    if (!worker) return c.json({ error: 'missing X-Worker-Id' }, 400);
    const ok = releaseJob(db, Number(c.req.param('wr_id')), worker);
    return ok ? c.json({ ok: true }) : c.json({ error: 'not the lease owner' }, 409);
  });

  r.post('/v1/wr-jobs/:wr_id/result', requireToken(db), async (c) => {
    const worker = workerIdOf(c);
    if (!worker) return c.json({ error: 'missing X-Worker-Id' }, 400);
    const wrId = Number(c.req.param('wr_id'));
    const body = (await c.req.json()) as ResultBody;
    if (typeof body?.ok !== 'boolean') return c.json({ error: 'bad payload' }, 400);

    if (body.ok) {
      if (!Array.isArray(body.points) || body.points.length === 0) {
        return c.json({ error: 'empty trail' }, 400);
      }
      const stored = completeJob(db, wrId, worker, body.points);
      return stored ? c.json({ ok: true, n: body.points.length })
                    : c.json({ error: 'not the lease owner' }, 409);
    }
    const recorded = failJob(db, wrId, worker, body.error ?? 'unknown');
    return recorded ? c.json({ ok: true }) : c.json({ error: 'not the lease owner' }, 409);
  });

  return r;
}
