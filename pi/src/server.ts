import { serve } from '@hono/node-server';
import { openDb, applySchema } from './db/connect';
import { migrateSeason0Recovered } from './db/season0Recovery';
import { migratePlayerRenames } from './db/playerRenames';
import { migrateTrails } from './db/trailMigrate';
import { purgeRemovedPlayers } from './db/purgeRemovedPlayers';
import { backfillActivity } from './activity/backfill';
import { EventHub } from './api/events';
import { ActivityHub } from './activity/hub';
import { SessionTracker } from './activity/sessionTracker';
import { commitActivity } from './activity/publish';
import { insertActivityEvents, sessionInput, sessionWire } from './db/activity';
import { activeSeasonId } from './db/seasons';
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
migratePlayerRenames(db);      // idempotent display-name corrections (e.g. Paul -> paul pork)
migrateTrails(db);             // one-time: run_points rows → run_trails blobs (bit-verified; see docs/replay-format.md)
purgeRemovedPlayers(db);       // remove ex-participants entirely (after recovery + trail migration, before activity backfill)
try { backfillActivity(db); } catch { /* guard: safe to skip if activity_events absent */ }
// Purge the legacy emit-at-end session events ('attempts'/'screen') from any pre-redesign DB;
// sessions are presence-driven now and the new model never writes these types (idempotent).
db.exec("DELETE FROM activity_events WHERE type IN ('attempts','screen')");
const hub = new EventHub();
const activity = new ActivityHub();
// Presence-driven activity sessions: presence feeds onFrame/onOffline, runs feed noteRun/notePb.
// Open/finalised sessions broadcast on the activity stream; finalised ones persist to activity_events.
// Only racing + watching-a-ghost sessions are surfaced. The menus class and the
// character/kart/track select screens are tracked (to bound the racing/ghost sessions around
// them) but not sent or stored - low-value + unbounded storage. Edit SURFACED to change.
const SURFACED = new Set(['racing', 'ghost']);
const sessionTracker = new SessionTracker({
  now: Date.now,
  emitOpen: (v) => { if (SURFACED.has(v.cls)) activity.publish({ kind: 'session', session: sessionWire(db, v) }); },
  emitFinal: (v) => {
    if (!SURFACED.has(v.cls)) return;
    insertActivityEvents(db, [sessionInput(activeSeasonId(db), v)]);
    activity.publish({ kind: 'session', session: sessionWire(db, v) });
  },
  emitDrop: (id) => activity.publish({ kind: 'session_drop', session_id: id }),
});
const live = makeLiveCompletion(db);
const pace = makePaceDelta(db);
const laps = makeLapDelta(db);
// App open/close -> a "presence" activity event. Logins within a short window of boot are skipped:
// after a (re)start every connected app reconnects at once, and that burst isn't a real round of opens.
const bootAt = Date.now();
const PRESENCE_LOGIN_GRACE_MS = 10000;
const presenceActivityEvent = (playerId: number, online: boolean) =>
  commitActivity(db, activity, [{ ts: Date.now(), type: 'presence', season_id: activeSeasonId(db),
    player_id: playerId, course_id: null, cc: null, payload: { online } }]);
const presence = new PresenceHub(db, live, pace, laps, Date.now, {
  onFrame: (pid, frame) => sessionTracker.onFrame(pid, frame),
  onOffline: (pid) => sessionTracker.onOffline(pid),
  onLogin: (pid) => { if (Date.now() - bootAt >= PRESENCE_LOGIN_GRACE_MS) presenceActivityEvent(pid, true); },
  onLogout: (pid) => presenceActivityEvent(pid, false),
});
// A model rebuild refreshes alignments too, so live projection, PB pace curves and
// lap comparisons (PB laps + golds) all reload; the rebuild fires on every finished
// trailed upload, which keeps the lap golds fresh as runs land.
const app = createApp(db, hub, (courseId) => {
  live.invalidate(courseId); pace.invalidateCourse(courseId); laps.invalidateCourse(courseId);
  presence.refreshOffStats();   // an upload can change offline players' standings
}, { activity, sessionTracker });
const sessionsSnapshot = () => sessionTracker.openSessions()
  .filter(v => SURFACED.has(v.cls)).map(v => sessionWire(db, v));
const { injectWebSocket } = makeWs(app, hub, presence, db, activity, sessionsSnapshot);
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
