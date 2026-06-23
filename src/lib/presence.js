// Live-presence driver: streams this player's status to the server's /v1/presence WS and
// feeds the broadcast into the `presence` store. Mirrors discord.js (store-driven push) +
// the bot's ws.ts (reconnect). Presence is ephemeral - a dropped frame self-corrects.
import { get } from "svelte/store";
import { screen, selection, race, minimap, presence, myPlayerId, serverConnection, pbSplits, pbTotalMs, appVersion } from "./stores.js";
import { resets } from "./resets.js";
import { roster, playerColor } from "./trailSettings.js";
import { buildSelfEntry } from "./localSelf.js";
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

/** Merge one player entry into the presence map + feed the timer buffer. Free of any
 *  connection/persistence side-effects, so both server frames and the local-self echo use it. */
function applyPlayerEntry(p, now) {
  pushSample(p.player_id, { t: now, elapsed_ms: p.elapsed_ms, completion: p.completion, pb_delta_ms: p.pb_delta_ms });
  const merged = { ...get(presence), [p.player_id]: p };
  presence.set(merged);
  return merged;
}

/** This client's roster id: the live presence `you`, else the cached roster's is_me entry
 *  (so we know which card is ours offline, before any server snapshot). null if unknown. */
function meId() {
  const id = get(myPlayerId);
  if (id != null) return id;
  const r = get(roster).find((p) => p.is_me);
  return r ? r.player_id : null;
}

/** Identity (name + locked colour) for our own card: prefer the cached roster, else the last
 *  presence entry. */
function identityFor(id) {
  const r = get(roster).find((p) => p.player_id === id);
  if (r) return { player_id: id, name: r.display_name, color: playerColor(r) };
  const cur = get(presence)[id];
  return { player_id: id, name: cur?.name ?? "You", color: cur?.color ?? null };
}

/** Drive our OWN card + the race rail from the local engine stores + the locally-cached PB,
 *  while the server is unreachable. No-op if we can't identify ourselves yet. Touches neither
 *  connection state nor persistence (the cached snapshot keeps the last *server* truth). */
export function pushLocalSelf(now = Date.now()) {
  const id = meId();
  if (id == null) return;
  applyPlayerEntry(buildSelfEntry({
    identity: identityFor(id),
    screen: get(screen), selection: get(selection), race: get(race), minimap: get(minimap),
    resets: get(resets), pbTotalMs: get(pbTotalMs), pbCum: get(pbSplits), now,
  }), now);
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
    const merged = applyPlayerEntry({ ...msg.player, _rxAt: now }, now);
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
    splits_ms: splits_ms.length ? splits_ms : null, dnf: !!r.dnf,
    invalidated: !!r.invalidated, invalid_reason: r.invalidReason ?? null,
    app_version: get(appVersion) || null,
  };
}

/** ws(s)://<server>/v1/presence?token=<token>, or null when the URL or token is missing.
 *  The desktop must send its token so the server attributes frames to this player: a
 *  token-less /v1/presence socket is receive-only, so we'd connect (green dot) yet stay
 *  "offline" AND suppress the local-self echo. No token yet -> no socket, and the
 *  local-self echo keeps our own card live until one is set. */
export function wsUrl() {
  const base = get(serverUrl).trim().replace(/\/+$/, "");
  const token = get(authToken).trim();
  if (!base || !token) return null;
  return `${base.replace(/^http/, "ws")}/v1/presence?token=${encodeURIComponent(token)}`;
}

let ws = null, closed = false, backoff = 1000, pending = false, hb = 0;
let openUrl = null, reconnectTimer = 0, cfgTimer = 0;

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
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = 0; }
  const url = wsUrl();
  openUrl = url;   // what this socket targets, so reconcile() can detect a token/url edit
  if (!url) { reconnectTimer = setTimeout(connect, 2000); return; }   // no server/token yet - retry
  ws = new WebSocket(url);
  ws.addEventListener("open", () => { backoff = 1000; rawSend(); });
  ws.addEventListener("message", (e) => handlePresenceMessage(e.data));
  ws.addEventListener("close", () => {
    markServerDisconnected();
    pushLocalSelf();   // immediately take our own card live from local state
    if (closed) return;
    reconnectTimer = setTimeout(connect, backoff); backoff = Math.min(backoff * 2, 30000);
  });
  ws.addEventListener("error", () => { try { ws.close(); } catch { /* ignore */ } });
}

/** The server URL or token changed (token entered during first-time setup, or edited later
 *  in Settings): if the live socket no longer targets the desired url, drop it and reconnect
 *  with the new config instead of staying on a stale/token-less socket until the next launch.
 *  Mirrors sync.js re-pushing its config on change. */
function reconcile() {
  if (closed || wsUrl() === openUrl) return;
  if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) {
    try { ws.close(); } catch { /* ignore */ }   // close handler reconnects with the fresh url
  } else {
    connect();   // idle in the retry loop: reconnect now instead of waiting out the timer
  }
}

export function initPresence() {
  closed = false;
  hydratePresence();   // paint last-known cards immediately; live frames overwrite on connect
  // When the server is unreachable, keep OUR OWN card + the race rail live from local engine
  // state; the four selection/race stores also drive the outbound presence frame.
  const selfTick = () => { if (!get(serverConnection).connected) pushLocalSelf(); };
  [screen, selection, race, minimap].forEach((s) => s.subscribe(() => { scheduleSend(); selfTick(); }));
  [resets, pbSplits, pbTotalMs].forEach((s) => s.subscribe(selfTick));
  // Reconnect when the server URL or token changes so the token entered during first-time
  // setup (or edited later in Settings) re-authenticates the socket. Debounced so typing
  // the token doesn't thrash the connection.
  const reconcileSoon = () => { if (cfgTimer) clearTimeout(cfgTimer); cfgTimer = setTimeout(reconcile, 300); };
  [serverUrl, authToken].forEach((s) => s.subscribe(reconcileSoon));
  hb = setInterval(rawSend, HEARTBEAT_MS);
  connect();
}
export function stopPresence() {
  closed = true;
  clearInterval(hb);
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = 0; }
  if (cfgTimer) { clearTimeout(cfgTimer); cfgTimer = 0; }
  try { ws?.close(); } catch { /* ignore */ }
}
