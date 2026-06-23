import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from '../db/connect';
import { EventHub } from './events';
import { createApp } from './app';
import type { LatestFn } from '../version/latest';

const fakeLatest: LatestFn = async () => ({ tag: '2.1.5', app: '2.1.0', fetched_at: 1750000000000, errors: [] });

function appWith() {
  const db = openDb(':memory:'); applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 1',1)");
  db.exec(`INSERT INTO players(id,display_name,color,app_version,last_seen_at) VALUES
    (1,'Paul','#a78bfa','2.1.0',1750000000000),(2,'Gub','#38bdf8','2.0.0',1749000000000),(3,'Aliias',NULL,NULL,NULL)`);
  db.exec("INSERT INTO season_rosters(season_id,player_id) VALUES (1,1),(1,2),(1,3)");
  db.exec("INSERT INTO service_status(service,version,booted_at) VALUES ('bot','2.1.5',1750000000000)");
  return createApp(db, new EventHub(), undefined, { latest: fakeLatest });
}

describe('GET /v1/version', () => {
  it('is public (no token) and reports latest, deployed, and per-player app versions', async () => {
    const res = await appWith().request('/v1/version');
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.latest).toMatchObject({ tag: '2.1.5', app: '2.1.0', errors: [] });
    expect(body.deployed.bot).toMatchObject({ version: '2.1.5' });
    expect(typeof body.deployed.server.version).toBe('string');
    expect(body.players.map((p: any) => p.name)).toEqual(['Aliias', 'Gub', 'Paul']);   // by display_name
    expect(body.players.find((p: any) => p.name === 'Paul').app_version).toBe('2.1.0');
    expect(body.players.find((p: any) => p.name === 'Aliias').app_version).toBeNull();
  });

  it('reports bot:null when no service_status row exists', async () => {
    const db = openDb(':memory:'); applySchema(db);
    db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 1',1)");
    const app = createApp(db, new EventHub(), undefined, { latest: fakeLatest });
    const body = await (await app.request('/v1/version')).json();
    expect(body.deployed.bot).toBeNull();
    expect(body.players).toEqual([]);
  });
});
