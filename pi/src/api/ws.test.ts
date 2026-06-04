import { describe, it, expect } from 'vitest';
import { serve } from '@hono/node-server';
import { openDb, applySchema } from '../db/connect';
import { mintToken } from '../db/players';
import { EventHub } from './events';
import { createApp, makeWs } from './app';

function ctx() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 1',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rainbow_road','Rainbow Road')");
  db.exec("INSERT INTO season_rosters(season_id,player_id) VALUES (1,1)");
  const hub = new EventHub();
  return { db, hub, app: createApp(db, hub), token: mintToken(db, 'Paul') };
}

describe('WS /v1/events', () => {
  it('delivers a derived event to a connected subscriber', async () => {
    const c = ctx();
    const { injectWebSocket } = makeWs(c.app, c.hub);
    const server = serve({ fetch: c.app.fetch, port: 0 });
    injectWebSocket(server);
    const addr = server.address() as { port: number };

    let wsClient!: WebSocket;
    const evt = await new Promise<any>((resolve) => {
      wsClient = new WebSocket(`ws://127.0.0.1:${addr.port}/v1/events`);
      wsClient.onmessage = (ev) => resolve(JSON.parse((ev as MessageEvent).data.toString()));
      wsClient.onopen = () => {
        fetch(`http://127.0.0.1:${addr.port}/v1/runs`, {
          method: 'POST',
          headers: { 'content-type': 'application/json', authorization: `Bearer ${c.token}` },
          body: JSON.stringify({ attempt_id: 'a1', course: 'Rainbow Road', status: 'finished', total_time: '1:50.000' }),
        });
      };
    });

    expect(['run_finished', 'pb_achieved']).toContain(evt.type);
    // Close the WS client and connections before server.close() so its callback fires.
    wsClient.close();
    server.closeAllConnections();
    await new Promise<void>((r) => server.close(() => r()));
  });
});
