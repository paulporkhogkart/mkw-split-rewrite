// Read-only activity-log client for the public website: loads recent history via REST then
// streams live events from /v1/activity/stream (token-less, receive-only). Both paths merge
// into the shared `activity` store by id, so order-of-arrival never matters. Mirrors the
// reconnect/backoff of presenceClient.js. WebSocket/timers/fetch are injectable for tests.
import { activity } from "../../src/lib/stores.js";
import { mergeActivity } from "./lib/activityMerge.js";
import { activityUrl, activityStreamWsUrl } from "./lib/api.js";

const MAX_BACKOFF_MS = 30000;

export function pushActivity(events) {
  activity.update((cur) => mergeActivity(cur, events));
}

export async function loadActivityHistory(apiBase, { fetchImpl = fetch, limit = 100 } = {}) {
  try {
    const res = await fetchImpl(`${apiBase}/v1/activity?limit=${limit}`);
    const list = await res.json();
    if (Array.isArray(list)) pushActivity(list);
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
      try { pushActivity([JSON.parse(ev.data)]); } catch { /* ignore malformed frame */ }
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
