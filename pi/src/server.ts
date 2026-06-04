import { serve } from '@hono/node-server';
import { openDb, applySchema } from './db/connect';
import { EventHub } from './api/events';
import { createApp, makeWs } from './api/app';

const DB_PATH = process.env.MKW_DB ?? 'mkw.db';
const PORT = Number(process.env.PORT ?? 8787);

const db = openDb(DB_PATH);
applySchema(db);
const hub = new EventHub();
const app = createApp(db, hub);
const { injectWebSocket } = makeWs(app, hub);
const server = serve({ fetch: app.fetch, port: PORT }, (info) => {
  console.log(`[pi] listening on http://127.0.0.1:${info.port}`);
});
injectWebSocket(server);
