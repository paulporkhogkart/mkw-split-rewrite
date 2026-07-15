import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from '../db/connect';
import { EventHub } from './events';
import { createApp } from './app';
import { insertWrTrail } from '../db/wrTrails';

function setup() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'mario_circuit','Mario Circuit')");
  db.prepare(`INSERT INTO world_records(id, course_id, cc, holder_name, record_ms, record_str,
                achieved_at, video_url, is_current)
              VALUES (10,1,150,'JaK',62934,'1:02.934','2026-04-06T00:00:00.000Z','https://youtu.be/x',1)`).run();
  insertWrTrail(db, 10, [{ t_ms: 14, cx: 1635, cy: 875, score: 0.79, lap: 1 }]);
  return createApp(db, new EventHub());
}

describe('GET /v1/wr-trails', () => {
  it('serves trails with NO token (public read)', async () => {
    const res = await setup().request('/v1/wr-trails?course=mario_circuit');
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body).toHaveLength(1);
    expect(body[0]).toMatchObject({ wr_id: 10, holder_name: 'JaK', record_ms: 62934, is_current: 1 });
    expect(body[0].points[0]).toEqual([14, 1635, 875, 0.79]);
  });

  it('sets permissive CORS for the cross-origin website', async () => {
    const res = await setup().request('/v1/wr-trails?course=mario_circuit');
    expect(res.headers.get('access-control-allow-origin')).toBe('*');
  });

  it('400s on an unknown course', async () => {
    const res = await setup().request('/v1/wr-trails?course=not_a_course');
    expect(res.status).toBe(400);
  });
});
