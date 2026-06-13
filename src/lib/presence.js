// Live-presence driver: streams this player's status to the server's /v1/presence WS and
// feeds the broadcast into the `presence` store. Mirrors discord.js (store-driven push) +
// the bot's ws.ts (reconnect). Presence is ephemeral - a dropped frame self-corrects.
import { get } from "svelte/store";
import { screen, selection, race, minimap, presence, myPlayerId, serverConnection } from "./stores.js";
import { resets } from "./resets.js";
import { serverUrl, authToken } from "./syncSettings.js";
import { pushSample } from "./raceTimerBuffer.js";
import { parseTime } from "./discordFormat.js";

const SNAPSHOT_KEY = "mkw.presence";

// localStorage is absent under Node (tests) and can be present-but-broken under Node's
// experimental Web Storage; probe like syncSettings.js and fall back to a no-op.
function safeStorage() {
  try {
    if (typeof localStorage !== "undefined" && typeof localStorage.getItem === "function") return localStorage;
  } catch { /* accessing the experimental global can throw */ }
  return { getItem: () => null, setItem: () => {} };
}
const ls = safeStorage();

/** Persist the presence map + the epoch-ms it was last synced. Storage is injectable for tests. */
export function writeSnapshot(players, syncedAt, storage = ls) {
  try { storage.setItem(SNAPSHOT_KEY, JSON.stringify({ players, syncedAt })); } catch { /* quota / serialize */ }
}

/** Read the persisted snapshot, or null when absent/corrupt. */
export function readSnapshot(storage = ls) {
  try {
    const raw = storage.getItem(SNAPSHOT_KEY);
    if (!raw) return null;
    const v = JSON.parse(raw);
    return v && typeof v === "object" && v.players ? { players: v.players, syncedAt: v.syncedAt ?? null } : null;
  } catch { return null; }
}

/** Paint last-known cards on launch: load the cached snapshot into `presence` and seed
 *  serverConnection as disconnected (so cards render stale/offline). Does NOT push race
 *  samples — a stale snapshot must never feed the live timer buffer. Returns whether a
 *  cache was found. */
export function hydratePresence(storage = ls) {
  const snap = readSnapshot(storage);
  if (!snap) { serverConnection.set({ connected: false, syncedAt: null }); return false; }
  presence.set(snap.players);
  serverConnection.set({ connected: false, syncedAt: snap.syncedAt });
  return true;
}

/** A live frame arrived: the link is up as of `syncedAt`. */
export function markServerConnected(syncedAt) {
  serverConnection.set({ connected: true, syncedAt });
}

/** The socket dropped: flip to disconnected but keep `syncedAt` so cards can show "last sync". */
export function markServerDisconnected() {
  serverConnection.update((s) => ({ ...s, connected: false }));
}

const PERSIST_DEBOUNCE_MS = 1000;
let persistTimer = 0, persistMap = null, persistAt = 0;
/** Trailing-debounce a snapshot write so the 4Hz WS doesn't hammer localStorage. */
function persistSoon(map, syncedAt) {
  persistMap = map; persistAt = syncedAt;
  if (persistTimer) return;
  persistTimer = setTimeout(() => { persistTimer = 0; writeSnapshot(persistMap, persistAt); }, PERSIST_DEBOUNCE_MS);
}

/** Apply one raw WS frame to the stores: snapshot replaces, update merges; either way feed
 *  the race-timer buffer (live), mark the link connected, and schedule a persist. `now` is
 *  injectable for tests. Malformed JSON is ignored. */
export function handlePresenceMessage(raw, now = Date.now()) {
  let msg;
  try { msg = JSON.parse(raw); } catch { return; }
  if (msg.type === "presence_snapshot") {
    if (msg.you != null) myPlayerId.set(msg.you);
    const map = {};
    for (const p of msg.players) {
      p._rxAt = now; map[p.player_id] = p;
      pushSample(p.player_id, { t: now, elapsed_ms: p.elapsed_ms, completion: p.completion, pb_delta_ms: p.pb_delta_ms });
    }
    presence.set(map);
    markServerConnected(now);
    persistSoon(map, now);
  } else if (msg.type === "presence_update") {
    const p = { ...msg.player, _rxAt: now };
    pushSample(p.player_id, { t: now, elapsed_ms: p.elapsed_ms, completion: p.completion, pb_delta_ms: p.pb_delta_ms });
    const merged = { ...get(presence), [p.player_id]: p };
    presence.set(merged);
    markServerConnected(now);
    persistSoon(merged, now);
  }
}

const THROTTLE_MS = 250;    // ~4 Hz cap on outbound frames
const HEARTBEAT_MS = 5000;  // idle keep-alive so the server's sweep keeps us online

/** Snapshot the live stores into a presence frame. */
export function frame() {
  const sel = get(selection), r = get(race), mm = get(minimap);
  // Completed laps' digit-read durations, contiguous from lap 1 (the server's
  // LiveSplit-style lap delta compares them against the PB run's run_laps).
  const splits_ms = [];
  if (r.splits) {
    for (let i = 1; ; i++) {
      const ms = parseTime(r.splits[i]);
      if (ms == null) break;
      splits_ms.push(ms);
    }
  }
  return {
    screen: get(screen),
    course: sel.course, character: sel.char, kart: sel.kart, costume: sel.costume,
    cur_lap: r.curLap, tot_lap: r.totLap, coins: r.coins, mushrooms: r.mushrooms,
    pos: mm && mm.cx != null ? [mm.cx, mm.cy] : null, final_time: r.finishTime, resets: get(resets),
    track_state: mm ? mm.trackState : null, elapsed_ms: r.elapsedMs ?? null,
    splits_ms: splits_ms.length ? splits_ms : null,
  };
}

/** ws(s)://<server>/v1/presence?token=<token>, or null when no server is configured. */
export function wsUrl() {
  const base = get(serverUrl).trim().replace(/\/+$/, "");
  if (!base) return null;
  const token = get(authToken).trim();
  return `${base.replace(/^http/, "ws")}/v1/presence${token ? `?token=${encodeURIComponent(token)}` : ""}`;
}

let ws = null, closed = false, backoff = 1000, pending = false, hb = 0;

function rawSend() {
  if (ws && ws.readyState === WebSocket.OPEN) { try { ws.send(JSON.stringify(frame())); } catch { /* drop */ } }
}
function scheduleSend() {
  if (pending) return;
  pending = true;
  setTimeout(() => { pending = false; rawSend(); }, THROTTLE_MS);
}

function connect() {
  if (closed) return;
  const url = wsUrl();
  if (!url) { setTimeout(connect, 2000); return; }   // not configured yet - retry
  ws = new WebSocket(url);
  ws.addEventListener("open", () => { backoff = 1000; rawSend(); });
  ws.addEventListener("message", (e) => handlePresenceMessage(e.data));
  ws.addEventListener("close", () => {
    markServerDisconnected();
    if (closed) return;
    setTimeout(connect, backoff); backoff = Math.min(backoff * 2, 30000);
  });
  ws.addEventListener("error", () => { try { ws.close(); } catch { /* ignore */ } });
}

export function initPresence() {
  closed = false;
  hydratePresence();   // paint last-known cards immediately; live frames overwrite on connect
  [screen, selection, race, minimap].forEach((s) => s.subscribe(() => scheduleSend()));
  hb = setInterval(rawSend, HEARTBEAT_MS);
  connect();
}
export function stopPresence() { closed = true; clearInterval(hb); try { ws?.close(); } catch { /* ignore */ } }
