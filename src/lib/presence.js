// Live-presence driver: streams this player's status to the server's /v1/presence WS and
// feeds the broadcast into the `presence` store. Mirrors discord.js (store-driven push) +
// the bot's ws.ts (reconnect). Presence is ephemeral - a dropped frame self-corrects.
import { get } from "svelte/store";
import { screen, selection, race, minimap, presence, myPlayerId } from "./stores.js";
import { resets } from "./resets.js";
import { serverUrl, authToken } from "./syncSettings.js";
import { pushSample } from "./raceTimerBuffer.js";
import { parseTime } from "./discordFormat.js";

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
  ws.addEventListener("message", (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.type === "presence_snapshot") {
        if (msg.you != null) myPlayerId.set(msg.you);   // which entry is this client
        const map = {}, t = Date.now();
        for (const p of msg.players) {
          p._rxAt = t; map[p.player_id] = p;
          pushSample(p.player_id, { t, elapsed_ms: p.elapsed_ms, completion: p.completion, pb_delta_ms: p.pb_delta_ms });
        }
        presence.set(map);
      } else if (msg.type === "presence_update") {
        const t = Date.now(), p = { ...msg.player, _rxAt: t };
        pushSample(p.player_id, { t, elapsed_ms: p.elapsed_ms, completion: p.completion, pb_delta_ms: p.pb_delta_ms });
        presence.update((m) => ({ ...m, [p.player_id]: p }));
      }
    } catch { /* ignore malformed */ }
  });
  ws.addEventListener("close", () => {
    if (closed) return;
    setTimeout(connect, backoff); backoff = Math.min(backoff * 2, 30000);
  });
  ws.addEventListener("error", () => { try { ws.close(); } catch { /* ignore */ } });
}

export function initPresence() {
  closed = false;
  [screen, selection, race, minimap].forEach((s) => s.subscribe(() => scheduleSend()));
  hb = setInterval(rawSend, HEARTBEAT_MS);
  connect();
}
export function stopPresence() { closed = true; clearInterval(hb); try { ws?.close(); } catch { /* ignore */ } }
