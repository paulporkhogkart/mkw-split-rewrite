import { Hono } from 'hono';
import { cors } from 'hono/cors';
import type { DatabaseSync } from 'node:sqlite';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { EventHub } from './events';
import { ActivityHub, type SessionWire } from '../activity/hub';
import { SessionTracker } from '../activity/sessionTracker';
import { runsRoutes } from './runs';
import { readsRoutes } from './reads';
import { activityRoutes } from './activity';
import { versionRoutes } from './version';
import type { LatestFn } from '../version/latest';
import { createStatsApp } from './stats';
import { screenRoutes } from './screen';
import { presenceHandlers } from './presence';
import { playerByToken } from '../db/players';
import { requireTokenAny } from './auth';
import type { PresenceHub } from '../presence/hub';

export type Env = { Variables: { playerId: number; playerName: string } };

/** The self-contained stat-explorer page (pi/stat-explorer.html), served same-origin. */
const EXPLORER_HTML = fileURLToPath(new URL('../../stat-explorer.html', import.meta.url));

export function createApp(db: DatabaseSync, hub: EventHub,
                          invalidateModel?: (courseId: number) => void,
                          opts?: { latest?: LatestFn; activity?: ActivityHub; sessionTracker?: SessionTracker }): Hono<Env> {
  const app = new Hono<Env>();
  app.get('/health', (c) => c.json({ status: 'ok' }));
  // Every HTTP route except /health and the two WebSocket streams needs a token (read or write).
  // /v1/events stays open: the on-Pi bot subscribes to it over localhost with no token, and it
  // only carries PB/WR events that are already announced publicly. /v1/presence keeps its own
  // optional-token (receive-only) model.
  // The public website fetches these reads cross-origin. They serve already-public data (same
  // category as the open /v1/presence stream), so they skip the token gate and get permissive
  // CORS (incl. preflight). Everything else (writes, stats, screen, other reads) stays gated.
  const PUBLIC_READS = ['/v1/leaderboard', '/v1/world-records', '/v1/roster', '/v1/territory', '/v1/territory/timeline', '/v1/version', '/v1/activity'];
  const readCors = cors({ origin: '*', allowMethods: ['GET'] });
  for (const p of PUBLIC_READS) app.use(p, readCors);
  app.use('/v1/players/:slug', readCors);   // single-segment player summary is public

  const OPEN = new Set(['/health', '/v1/events', '/v1/presence', '/v1/activity/stream', ...PUBLIC_READS]);
  const PLAYER_SUMMARY = /^\/v1\/players\/[^/]+$/;   // NOT /v1/players/:id/pbs|trails (two segments — stay gated)
  const isOpen = (path: string) => OPEN.has(path) || PLAYER_SUMMARY.test(path);
  app.use('*', (c, next) => (isOpen(c.req.path) ? next() : requireTokenAny(db)(c, next)));
  const activity = opts?.activity ?? new ActivityHub();
  // A default no-op tracker keeps runsRoutes working in tests; server.ts passes the real one
  // (shared with presence) so attempts land on the presence-opened racing session.
  const sessionTracker = opts?.sessionTracker
    ?? new SessionTracker({ now: Date.now, emitOpen() {}, emitFinal() {}, emitDrop() {} });
  app.route('/', runsRoutes(db, hub, activity, sessionTracker, invalidateModel));
  app.route('/', activityRoutes(db));
  app.route('/', readsRoutes(db));
  app.route('/', versionRoutes(db, { latest: opts?.latest }));
  app.route('/', createStatsApp(db, { porkerPath: process.env.STATS_PORKER_DB ?? 'porker.db' }));
  app.route('/', screenRoutes(db));
  app.get('/explorer', (c) => {
    try { return c.html(readFileSync(EXPLORER_HTML, 'utf8')); }
    catch { return c.text('stat-explorer.html not found', 404); }
  });
  return app;
}

import { createNodeWebSocket } from '@hono/node-ws';

/** Attach the /v1/events + /v1/presence + /v1/activity/stream WebSocket routes. Returns { injectWebSocket }. */
export function makeWs(app: Hono<Env>, hub: EventHub, presence: PresenceHub, db: DatabaseSync,
                       activity: ActivityHub, sessionsSnapshot: () => SessionWire[]) {
  const { injectWebSocket, upgradeWebSocket } = createNodeWebSocket({ app });
  app.get('/v1/events', upgradeWebSocket(() => {
    let unsub = () => {};
    return {
      onOpen(_e: unknown, ws: { send: (data: string) => void }) {
        unsub = hub.subscribe((evt) => ws.send(JSON.stringify(evt)));
      },
      onClose() { unsub(); },
    };
  }));
  // Live presence: a token (query param - a browser WS can't set headers) attributes the
  // sender's frames to their player; a token-less socket is receive-only.
  app.get('/v1/presence', upgradeWebSocket((c) => {
    const token = c.req.query('token');
    const player = token ? playerByToken(db, token) : null;
    return presenceHandlers(presence, player ? player.id : null);
  }));
  // Live activity stream: open, no token required (matches /v1/activity public reads policy).
  app.get('/v1/activity/stream', upgradeWebSocket(() => {
    let unsub = () => {};
    return {
      onOpen(_e: unknown, ws: { send: (data: string) => void }) {
        // Snapshot the in-flight open sessions first so a fresh client sees them ticking.
        ws.send(JSON.stringify({ kind: 'sessions_snapshot', sessions: sessionsSnapshot() }));
        unsub = activity.subscribe((ev) => ws.send(JSON.stringify(ev)));
      },
      onClose() { unsub(); },
    };
  }));
  return { injectWebSocket };
}
