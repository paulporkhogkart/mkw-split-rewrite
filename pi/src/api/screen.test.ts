import { describe, it, expect } from 'vitest';
import { DatabaseSync } from 'node:sqlite';
import { applySchema } from '../db/connect';
import { createApp } from './app';
import { EventHub } from './events';
import { mintToken } from '../db/players';

function db(): DatabaseSync {
  const d = new DatabaseSync(':memory:');
  applySchema(d);
  d.exec(`INSERT INTO seasons(id,name,is_active) VALUES(1,'S1',1);
          INSERT INTO players(id,display_name) VALUES(1,'Luke');`);
  return d;
}

describe('screen-intervals ingest + screen_time', () => {
  it('POST stores intervals; stats aggregate + breakdown read them', async () => {
    const d = db();
    const token = mintToken(d, 'Luke');
    const app = createApp(d, new EventHub());
    const post = await app.request('/v1/screen-intervals', {
      method: 'POST',
      headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' },
      body: JSON.stringify({ intervals: [
        { screen: 'MAIN_MENU', started_ms: 0, ended_ms: 3000 },
        { screen: 'RACING', started_ms: 3000, ended_ms: 8000 },
      ] }),
    });
    expect(post.status).toBe(200);
    expect((await post.json()).inserted).toBe(2);

    const v = await app.request('/v1/stats/value?metric=screen_time&screen=MAIN_MENU&period=all_time');
    expect((await v.json()).value).toBe(3000);

    const bd = await app.request('/v1/stats/breakdown?metric=screen_time&group_by=screen&period=all_time');
    const rows = (await bd.json()).rows as { key: string; value: number }[];
    expect(Object.fromEntries(rows.map((r) => [r.key, r.value]))).toEqual({ MAIN_MENU: 3000, RACING: 5000 });

    const cat = await (await app.request('/v1/stats/metrics')).json();
    expect(cat.find((m: { id: string }) => m.id === 'screen_time').dimensions).toEqual(['player', 'screen']);
  });

  it('rejects an unauthenticated POST', async () => {
    const app = createApp(db(), new EventHub());
    const res = await app.request('/v1/screen-intervals', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ intervals: [] }),
    });
    expect(res.status).toBe(401);
  });
});
