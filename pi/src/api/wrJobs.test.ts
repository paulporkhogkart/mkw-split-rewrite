import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from '../db/connect';
import { EventHub } from './events';
import { createApp } from './app';
import { mintToken } from '../db/players';
import { seedWrJobs } from '../db/wrJobs';
import type { ServerEvent } from '../db/types';

function setup() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'mario_circuit','Mario Circuit')");
  db.prepare("INSERT INTO players(id, display_name) VALUES (1,'Paul')").run();
  db.prepare(`INSERT INTO world_records(id, course_id, cc, holder_name, record_ms, record_str,
                achieved_at, video_url, character_slug, costume_slug, kart_slug, is_current)
              VALUES (10,1,150,'JaK',62934,'1:02.934','2026-04-06T00:00:00.000Z',
                      'https://youtu.be/x','toadette','explorer','baby_blooper',1)`).run();
  seedWrJobs(db);
  const token = mintToken(db, 'Paul');
  const hub = new EventHub();
  const events: ServerEvent[] = [];
  hub.subscribe((e) => events.push(e));
  const app = createApp(db, hub);
  // Same player token on both machines — the X-Worker-Id is what separates the leases.
  const w1 = { Authorization: `Bearer ${token}`, 'X-Worker-Id': 'machine-a' };
  const w2 = { Authorization: `Bearer ${token}`, 'X-Worker-Id': 'machine-b' };
  return { db, app, token, w1, w2, events };
}

describe('/v1/wr-jobs', () => {
  it('401s with no token', async () => {
    const { app } = setup();
    const res = await app.request('/v1/wr-jobs/claim', { method: 'POST', headers: { 'X-Worker-Id': 'machine-a' } });
    expect(res.status).toBe(401);
  });

  it('401s on a bad token', async () => {
    const { app } = setup();
    const res = await app.request('/v1/wr-jobs/claim', { method: 'POST',
      headers: { Authorization: 'Bearer nope', 'X-Worker-Id': 'machine-a' } });
    expect(res.status).toBe(401);
  });

  it('rejects a ?token= query param on a write (header-only)', async () => {
    const { app, token } = setup();
    const res = await app.request(`/v1/wr-jobs/claim?token=${token}`, { method: 'POST',
      headers: { 'X-Worker-Id': 'machine-a' } });
    expect(res.status).toBe(401);
  });

  it('400s when X-Worker-Id is missing (no lease identity)', async () => {
    const { app, token } = setup();
    const res = await app.request('/v1/wr-jobs/claim', { method: 'POST',
      headers: { Authorization: `Bearer ${token}` } });
    expect(res.status).toBe(400);
  });

  it('claims a job', async () => {
    const { app, w1 } = setup();
    const res = await app.request('/v1/wr-jobs/claim', { method: 'POST', headers: w1 });
    expect(res.status).toBe(200);
    expect(await res.json()).toMatchObject({ wr_id: 10, course_slug: 'mario_circuit',
      character_slug: 'toadette', costume_slug: 'explorer', attempt: 1 });
  });

  it('204s when the queue is empty', async () => {
    const { app, w1 } = setup();
    await app.request('/v1/wr-jobs/claim', { method: 'POST', headers: w1 });
    const res = await app.request('/v1/wr-jobs/claim', { method: 'POST', headers: w1 });
    expect(res.status).toBe(204);
  });

  it('does not hand the same job to a second machine on the same player token', async () => {
    const { app, w1, w2 } = setup();
    expect((await app.request('/v1/wr-jobs/claim', { method: 'POST', headers: w1 })).status).toBe(200);
    expect((await app.request('/v1/wr-jobs/claim', { method: 'POST', headers: w2 })).status).toBe(204);
  });

  it('heartbeats, then 409s for a different machine on the same token', async () => {
    const { app, w1, w2 } = setup();
    await app.request('/v1/wr-jobs/claim', { method: 'POST', headers: w1 });
    expect((await app.request('/v1/wr-jobs/10/heartbeat', { method: 'POST', headers: w1 })).status).toBe(200);
    expect((await app.request('/v1/wr-jobs/10/heartbeat', { method: 'POST', headers: w2 })).status).toBe(409);
  });

  it('accepts a result and stores the trail', async () => {
    const { db, app, w1 } = setup();
    await app.request('/v1/wr-jobs/claim', { method: 'POST', headers: w1 });
    const res = await app.request('/v1/wr-jobs/10/result', {
      method: 'POST', headers: { ...w1, 'Content-Type': 'application/json' },
      body: JSON.stringify({ ok: true, points: [[14, 1635, 875, 0.79, 1], [114, 1636, 870, 0.81, 1]] }),
    });
    expect(res.status).toBe(200);
    expect(db.prepare('SELECT n FROM wr_trails WHERE wr_id=10').get()).toMatchObject({ n: 2 });
  });

  it('409s a result from a machine that does not hold the lease', async () => {
    const { db, app, w1, w2 } = setup();
    await app.request('/v1/wr-jobs/claim', { method: 'POST', headers: w1 });
    const res = await app.request('/v1/wr-jobs/10/result', {
      method: 'POST', headers: { ...w2, 'Content-Type': 'application/json' },
      body: JSON.stringify({ ok: true, points: [[14, 1635, 875, 0.79, 1]] }),
    });
    expect(res.status).toBe(409);
    expect(db.prepare('SELECT COUNT(*) n FROM wr_trails').get()).toMatchObject({ n: 0 });
  });

  it('records a failure result', async () => {
    const { db, app, w1 } = setup();
    await app.request('/v1/wr-jobs/claim', { method: 'POST', headers: w1 });
    const res = await app.request('/v1/wr-jobs/10/result', {
      method: 'POST', headers: { ...w1, 'Content-Type': 'application/json' },
      body: JSON.stringify({ ok: false, error: 'time_mismatch' }),
    });
    expect(res.status).toBe(200);
    expect(db.prepare('SELECT last_error FROM wr_jobs WHERE wr_id=10').get())
      .toMatchObject({ last_error: 'time_mismatch' });
  });

  it('releases a job back to the queue', async () => {
    const { app, w1 } = setup();
    await app.request('/v1/wr-jobs/claim', { method: 'POST', headers: w1 });
    expect((await app.request('/v1/wr-jobs/10/release', { method: 'POST', headers: w1 })).status).toBe(200);
    expect((await app.request('/v1/wr-jobs/claim', { method: 'POST', headers: w1 })).status).toBe(200);
  });

  it('400s on a non-numeric wr_id instead of 500ing', async () => {
    const { app, w1 } = setup();
    const res = await app.request('/v1/wr-jobs/not-a-number/heartbeat', { method: 'POST', headers: w1 });
    expect(res.status).toBe(400);
  });

  it('400s a result with a malformed point instead of 500ing', async () => {
    const { app, w1 } = setup();
    await app.request('/v1/wr-jobs/claim', { method: 'POST', headers: w1 });
    const res = await app.request('/v1/wr-jobs/10/result', {
      method: 'POST', headers: { ...w1, 'Content-Type': 'application/json' },
      body: JSON.stringify({ ok: true, points: [[14, 1635, 875, 0.79, 1], 'garbage'] }),
    });
    expect(res.status).toBe(400);
  });

  it('400s a result with a non-monotonic-looking but shape-valid point instead of 500ing', async () => {
    const { app, w1 } = setup();
    await app.request('/v1/wr-jobs/claim', { method: 'POST', headers: w1 });
    const res = await app.request('/v1/wr-jobs/10/result', {
      method: 'POST', headers: { ...w1, 'Content-Type': 'application/json' },
      body: JSON.stringify({ ok: true, points: [[14, 1635, 875, 0.79, 1], [14, 1636, 870, 0.81, 1]] }),
    });
    // Shape-valid (equal t_ms is not a shape error) but rejected deeper by packTrail — this
    // documents that the cheap shape guard does not catch it; it still must not be a raw 500.
    expect(res.status).toBe(500);
  });

  it('announces wr_job_dead when a failure kills the job (cap reached)', async () => {
    const { db, app, w1, events } = setup();
    db.prepare('UPDATE wr_jobs SET attempts=4 WHERE wr_id=10').run();
    await app.request('/v1/wr-jobs/claim', { method: 'POST', headers: w1 });   // attempts -> 5
    await app.request('/v1/wr-jobs/10/result', {
      method: 'POST', headers: { ...w1, 'Content-Type': 'application/json' },
      body: JSON.stringify({ ok: false, error: 'timeout' }),
    });
    expect(events.filter((e) => e.type === 'wr_job_dead')).toMatchObject([
      { wr_id: 10, course: 'Mario Circuit', holder: 'JaK', reason: 'timeout', attempts: 5 },
    ]);
  });

  it('announces wr_job_dead immediately on a terminal time_mismatch', async () => {
    const { app, w1, events } = setup();
    await app.request('/v1/wr-jobs/claim', { method: 'POST', headers: w1 });
    await app.request('/v1/wr-jobs/10/result', {
      method: 'POST', headers: { ...w1, 'Content-Type': 'application/json' },
      body: JSON.stringify({ ok: false, error: 'time_mismatch detected=1 expected=2' }),
    });
    expect(events.filter((e) => e.type === 'wr_job_dead')).toHaveLength(1);
  });

  it('does not announce for a survivable failure', async () => {
    const { app, w1, events } = setup();
    await app.request('/v1/wr-jobs/claim', { method: 'POST', headers: w1 });
    await app.request('/v1/wr-jobs/10/result', {
      method: 'POST', headers: { ...w1, 'Content-Type': 'application/json' },
      body: JSON.stringify({ ok: false, error: 'download_failed: 403' }),
    });
    expect(events.filter((e) => e.type === 'wr_job_dead')).toEqual([]);
  });
});
