// Read-only activity client for the public website: loads recent history via REST then streams
// live messages from /v1/activity/stream (token-less, receive-only). Milestone events + persisted
// sessions merge by `evt:<id>`; live sessions upsert by `sess:<id>` (open -> final in place) and
// `session_drop` removes them; a connect snapshot seeds the in-flight open sessions. Order is by
// feed timestamp, so arrival order never matters. WebSocket/timers/fetch are injectable for tests.
import { activity } from "../../src/lib/stores.js";
import { rowFromEvent, rowFromSession, upsertRows, dropRow, replaceSessions } from "./lib/activityMerge.js";

const MAX_BACKOFF_MS = 30000;

/** Merge milestone + persisted-session events (a REST list, or a live {kind:'event'}). */
export function pushEvents(events) {
  activity.update((cur) => upsertRows(cur, events.map(rowFromEvent)));
}
function applySession(wire) { activity.update((cur) => upsertRows(cur, [rowFromSession(wire)])); }
function dropSession(id) { activity.update((cur) => dropRow(cur, `sess:${id}`)); }
function applySnapshot(sessions) { activity.update((cur) => replaceSessions(cur, sessions.map(rowFromSession))); }

/** Route one decoded stream message into the store. */
export function applyStreamMsg(msg) {
  switch (msg && msg.kind) {
    case "event": pushEvents([msg.event]); break;
    case "session": applySession(msg.session); break;
    case "session_drop": dropSession(msg.session_id); break;
    case "sessions_snapshot": applySnapshot(msg.sessions || []); break;
    default: break;   // unknown/legacy frame: ignore
  }
}

export async function loadActivityHistory(apiBase, { fetchImpl = fetch, limit = 100 } = {}) {
  try {
    const res = await fetchImpl(`${apiBase}/v1/activity?limit=${limit}`);
    const list = await res.json();
    if (Array.isArray(list)) pushEvents(list);
  } catch { /* offline / server down: leave the store as-is */ }
}

export function startActivityStream(apiBase, {
  WebSocketImpl = WebSocket, setTimeoutImpl = setTimeout, clearTimeoutImpl = clearTimeout,
} = {}) {
  const url = `${apiBase.replace(/\/+$/, "").replace(/^http/, "ws")}/v1/activity/stream`;
  let ws = null, closed = false, backoff = 1000, timer = 0;

  function connect() {
    if (closed) return;
    ws = new WebSocketImpl(url);
    ws.addEventListener("open", () => { backoff = 1000; });
    ws.addEventListener("message", (ev) => {
      try { applyStreamMsg(JSON.parse(ev.data)); } catch { /* ignore malformed frame */ }
    });
    ws.addEventListener("close", () => {
      if (closed) return;
      if (timer) clearTimeoutImpl(timer);
      timer = setTimeoutImpl(connect, backoff);
      backoff = Math.min(backoff * 2, MAX_BACKOFF_MS);
    });
    ws.addEventListener("error", () => { try { ws.close(); } catch { /* ignore */ } });
  }
  connect();

  return function stop() {
    closed = true;
    if (timer) clearTimeoutImpl(timer);
    try { ws?.close(); } catch { /* ignore */ }
  };
}
