// Read-only live-presence client for the public website. Connects to the season
// server's /v1/presence WebSocket WITHOUT a token (a token-less socket is
// receive-only) and feeds the shared presence stores via the desktop app's
// handlePresenceMessage. It only receives - no outbound frames, no local-self echo
// (those are desktop-only concerns). Mirrors the reconnect/backoff in src/lib/presence.js.
import { handlePresenceMessage, markServerDisconnected } from "../../src/lib/presence.js";

const MAX_BACKOFF_MS = 30000;

/** ws(s)://<origin>/v1/presence - the public, receive-only stream (no token). */
export function presenceWsUrl(apiBase) {
  const base = (apiBase || "").trim().replace(/\/+$/, "");
  return `${base.replace(/^http/, "ws")}/v1/presence`;
}

/** Open a reconnecting read-only presence socket. Returns stop(). WebSocket + timers
 *  are injectable for tests. */
export function startPresence(apiBase, {
  WebSocketImpl = WebSocket, setTimeoutImpl = setTimeout, clearTimeoutImpl = clearTimeout,
} = {}) {
  const url = presenceWsUrl(apiBase);
  let ws = null, closed = false, backoff = 1000, timer = 0;

  function connect() {
    if (closed) return;
    ws = new WebSocketImpl(url);
    ws.addEventListener("open", () => { backoff = 1000; });
    ws.addEventListener("message", (e) => handlePresenceMessage(e.data));
    ws.addEventListener("close", () => {
      markServerDisconnected();
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
