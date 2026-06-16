import { describe, it, expect, beforeEach } from "vitest";
import { get } from "svelte/store";
import { presence, serverConnection, myPlayerId } from "../../src/lib/stores.js";
import { presenceWsUrl, startPresence } from "./presenceClient.js";

// Minimal fake WebSocket: captures listeners, lets the test drive open/message/close.
class FakeWS {
  constructor(url) { this.url = url; this.listeners = {}; this.closed = false; FakeWS.last = this; }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  emit(type, ev) { (this.listeners[type] || []).forEach((fn) => fn(ev)); }
  close() { this.closed = true; }
}

beforeEach(() => {
  presence.set({}); myPlayerId.set(null); serverConnection.set({ connected: false, syncedAt: null });
  FakeWS.last = null;
});

describe("presenceWsUrl", () => {
  it("derives the token-less ws(s) URL and strips a trailing slash", () => {
    expect(presenceWsUrl("https://api.thekartoff.com/")).toBe("wss://api.thekartoff.com/v1/presence");
    expect(presenceWsUrl("http://localhost:8787")).toBe("ws://localhost:8787/v1/presence");
  });
});

describe("startPresence", () => {
  it("opens a receive-only socket and applies a snapshot to the shared stores", () => {
    startPresence("http://localhost:8787", { WebSocketImpl: FakeWS });
    expect(FakeWS.last.url).toBe("ws://localhost:8787/v1/presence");
    FakeWS.last.emit("open");
    FakeWS.last.emit("message", { data: JSON.stringify({
      type: "presence_snapshot", you: null,
      players: [{ player_id: 1, name: "Paul", online: true }, { player_id: 2, name: "Luke", online: false }],
    }) });
    expect(Object.keys(get(presence)).sort()).toEqual(["1", "2"]);
    expect(get(serverConnection).connected).toBe(true);
  });

  it("marks disconnected and schedules a reconnect on close", () => {
    let scheduled = null;
    const setTimeoutImpl = (fn, ms) => { scheduled = { fn, ms }; return 1; };
    startPresence("http://x", { WebSocketImpl: FakeWS, setTimeoutImpl });
    const first = FakeWS.last;
    first.emit("message", { data: JSON.stringify({ type: "presence_snapshot", players: [] }) });
    expect(get(serverConnection).connected).toBe(true);
    first.emit("close");
    expect(get(serverConnection).connected).toBe(false);
    expect(scheduled.ms).toBe(1000);
    scheduled.fn();                          // run the scheduled reconnect
    expect(FakeWS.last).not.toBe(first);     // a fresh socket was opened
  });

  it("stop() prevents reconnects after close", () => {
    let scheduled = null;
    const setTimeoutImpl = (fn, ms) => { scheduled = { fn, ms }; return 1; };
    const stop = startPresence("http://x", { WebSocketImpl: FakeWS, setTimeoutImpl });
    stop();
    FakeWS.last.emit("close");
    expect(scheduled).toBeNull();            // closed first -> never schedules
  });
});
