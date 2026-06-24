import { serve } from '@hono/node-server';
import { openDb, applySchema } from './db/connect';
import { migrateSeason0Recovered } from './db/season0Recovery';
import { EventHub } from './api/events';
import { ActivityHub } from './activity/hub';
import { createApp, makeWs } from './api/app';
import { PresenceHub } from './presence/hub';
import { makeLiveCompletion } from './presence/completion';
import { makePaceDelta } from './presence/pace';
import { makeLapDelta } from './presence/lapDelta';
import { startWrScraper } from './wr/scheduler';
import { startWrHistoryScraper } from './wr/history_scheduler';

const DB_PATH = process.env.MKW_DB ?? 'mkw.db';
const PORT = Number(process.env.PORT ?? 8787);

const db = openDb(DB_PATH);
applySchema(db);
migrateSeason0Recovered(db);   // one-time: real Discord-recovered Season 0 progression
const hub = new EventHub();
const activity = new ActivityHub();
const live = makeLiveCompletion(db);
const pace = makePaceDelta(db);
const laps = makeLapDelta(db);
const presence = new PresenceHub(db, live, pace, laps);
// A model rebuild refreshes alignments too, so live projection, PB pace curves and
// lap comparisons (PB laps + golds) all reload; the rebuild fires on every finished
// trailed upload, which keeps the lap golds fresh as runs land.
const app = createApp(db, hub, (courseId) => {
  live.invalidate(courseId); pace.invalidateCourse(courseId); laps.invalidateCourse(courseId);
  presence.refreshOffStats();   // an upload can change offline players' standings
}, { activity });
const { injectWebSocket } = makeWs(app, hub, presence, db, activity);
const server = serve({ fetch: app.fetch, port: PORT }, (info) => {
  console.log(`[pi] listening on http://127.0.0.1:${info.port}`);
});
injectWebSocket(server);
setInterval(() => presence.sweep(15000), 5000);   // flip dead/stale sockets offline
setInterval(() => presence.persistLastSeen(), 30000);   // durable last-seen (survives restarts)
startWrScraper(db, hub, {
  url: process.env.MKWRS_URL,
  minIntervalSec: Number(process.env.MKWRS_MIN_INTERVAL_SEC ?? 900),   // 15 min
  maxIntervalSec: Number(process.env.MKWRS_MAX_INTERVAL_SEC ?? 1800),  // 30 min
  activity,
});
startWrHistoryScraper(db, {
  minIntervalSec: Number(process.env.MKWRS_HISTORY_MIN_INTERVAL_SEC ?? 7200),    // 2 h
  maxIntervalSec: Number(process.env.MKWRS_HISTORY_ENABLED === '0'
    ? 0
    : process.env.MKWRS_HISTORY_MAX_INTERVAL_SEC ?? 21600),                      // 6 h; 0 disables
});
