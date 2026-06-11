import { serve } from '@hono/node-server';
import { openDb, applySchema } from './db/connect';
import { EventHub } from './api/events';
import { createApp, makeWs } from './api/app';
import { PresenceHub } from './presence/hub';
import { makeLiveCompletion } from './presence/completion';
import { startWrScraper } from './wr/scheduler';

const DB_PATH = process.env.MKW_DB ?? 'mkw.db';
const PORT = Number(process.env.PORT ?? 8787);

const db = openDb(DB_PATH);
applySchema(db);
const hub = new EventHub();
const live = makeLiveCompletion(db);
const presence = new PresenceHub(db, live);
const app = createApp(db, hub, live.invalidate);
const { injectWebSocket } = makeWs(app, hub, presence, db);
const server = serve({ fetch: app.fetch, port: PORT }, (info) => {
  console.log(`[pi] listening on http://127.0.0.1:${info.port}`);
});
injectWebSocket(server);
setInterval(() => presence.sweep(15000), 5000);   // flip dead/stale sockets offline
startWrScraper(db, hub, {
  url: process.env.MKWRS_URL,
  minIntervalSec: Number(process.env.MKWRS_MIN_INTERVAL_SEC ?? 900),   // 15 min
  maxIntervalSec: Number(process.env.MKWRS_MAX_INTERVAL_SEC ?? 1800),  // 30 min
});
