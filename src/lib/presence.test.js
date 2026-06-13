import { describe, it, expect } from "vitest";
import { screen, selection, race, minimap, presence, serverConnection } from "./stores.js";
import { resets } from "./resets.js";
import { serverUrl, authToken } from "./syncSettings.js";
import { frame, wsUrl, writeSnapshot, readSnapshot, hydratePresence, markServerConnected, markServerDisconnected } from "./presence.js";
import { sampleAt } from "./raceTimerBuffer.js";
import { get } from "svelte/store";

describe("presence frame()", () => {
  it("maps the live stores into a frame", () => {
    screen.set("RACING");
    selection.set({ char: "Mario", costume: "Base", kart: "Std", course: "Bowsers Castle" });
    race.set({ curLap: 2, totLap: 3, coins: 7, mushrooms: 1, splits: {}, finishTime: null, elapsedMs: 5000 });
    minimap.set({ cx: 12, cy: 34, radius: 5, trackState: "tracking", roi: [0, 0, 1, 1] });
    resets.set(4);
    expect(frame()).toEqual({
      screen: "RACING", course: "Bowsers Castle", character: "Mario", kart: "Std", costume: "Base",
      cur_lap: 2, tot_lap: 3, coins: 7, mushrooms: 1, pos: [12, 34], final_time: null, resets: 4,
      track_state: "tracking", elapsed_ms: 5000, splits_ms: null,
    });
  });
  it("sends the completed laps' durations as a contiguous ms prefix", () => {
    race.set({ curLap: 3, totLap: 3, coins: 0, mushrooms: 0, finishTime: null, elapsedMs: 110000,
               splits: { 1: "0:51.294", 2: "0:50.764" } });
    expect(frame().splits_ms).toEqual([51294, 50764]);
    race.set({ curLap: 3, totLap: 3, coins: 0, mushrooms: 0, finishTime: null, elapsedMs: 110000,
               splits: { 2: "0:50.764" } });                  // lap 1 missing -> no prefix
    expect(frame().splits_ms).toBeNull();
  });
  it("pos is null with no minimap fix", () => {
    minimap.set(null);
    expect(frame().pos).toBeNull();
    minimap.set({ cx: null, cy: null, radius: 0, trackState: null, roi: null });
    expect(frame().pos).toBeNull();   // a cleared-but-present store must not send [null,null]
  });
});

describe("presence wsUrl()", () => {
  it("converts http(s) base to ws(s) and appends the token", () => {
    serverUrl.set("http://127.0.0.1:8787/");
    authToken.set("abc");
    expect(wsUrl()).toBe("ws://127.0.0.1:8787/v1/presence?token=abc");
    serverUrl.set("https://srv.example.com");
    expect(wsUrl()).toBe("wss://srv.example.com/v1/presence?token=abc");
  });
  it("is null when no server is configured", () => {
    serverUrl.set("");
    expect(wsUrl()).toBeNull();
  });
});

// A Map-backed fake of the localStorage subset we use (Node has no localStorage).
function fakeStorage(seed = {}) {
  const m = new Map(Object.entries(seed));
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)) };
}

describe("presence snapshot persistence", () => {
  it("round-trips the players map + syncedAt", () => {
    const store = fakeStorage();
    const players = { 1: { player_id: 1, name: "Paul" }, 2: { player_id: 2, name: "Luke" } };
    writeSnapshot(players, 1717000000000, store);
    expect(readSnapshot(store)).toEqual({ players, syncedAt: 1717000000000 });
  });
  it("returns null when absent or corrupt", () => {
    expect(readSnapshot(fakeStorage())).toBeNull();
    expect(readSnapshot(fakeStorage({ "mkw.presence": "not json" }))).toBeNull();
  });
});

describe("presence hydrate + connection transitions", () => {
  it("hydrates the presence map + serverConnection(connected:false) from cache", () => {
    const players = { 7: { player_id: 7, name: "Alex", online: true, elapsed_ms: 5000, completion: 0.4 } };
    const store = fakeStorage({ "mkw.presence": JSON.stringify({ players, syncedAt: 4242 }) });
    presence.set({}); serverConnection.set({ connected: false, syncedAt: null });
    expect(hydratePresence(store)).toBe(true);
    expect(get(presence)).toEqual(players);
    expect(get(serverConnection)).toEqual({ connected: false, syncedAt: 4242 });
  });
  it("does NOT feed the race-timer buffer (no live samples from a stale snapshot)", () => {
    const players = { 8: { player_id: 8, name: "Luke", elapsed_ms: 9000, completion: 0.9 } };
    const store = fakeStorage({ "mkw.presence": JSON.stringify({ players, syncedAt: 1 }) });
    hydratePresence(store);
    expect(sampleAt(8, Date.now())).toBeNull();
  });
  it("leaves defaults when there is no cache", () => {
    presence.set({}); serverConnection.set({ connected: false, syncedAt: 999 });
    expect(hydratePresence(fakeStorage())).toBe(false);
    expect(get(serverConnection)).toEqual({ connected: false, syncedAt: null });
  });
  it("marks connected (with syncedAt) and disconnected (keeping syncedAt)", () => {
    markServerConnected(5000);
    expect(get(serverConnection)).toEqual({ connected: true, syncedAt: 5000 });
    markServerDisconnected();
    expect(get(serverConnection)).toEqual({ connected: false, syncedAt: 5000 });
  });
});
