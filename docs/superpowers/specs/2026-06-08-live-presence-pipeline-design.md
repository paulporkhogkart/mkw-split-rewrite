# Live Presence Pipeline (Sub-project #2) — Design

**Date:** 2026-06-08
**Status:** Approved (design), ready for planning.
**Part of:** the 3-part monitor redesign — (1) layout [done], **(2) this presence pipeline**, (3) player panel.

## Goal

Stream each player's live status — online + screen/selection/lap + live coins/mushrooms + course completion % — from their app to the server, which **fans it out to every monitor over WebSocket**, so each client's player panel (#3) shows what everyone is doing in real time. **Pushed, not polled.**

## Architecture (approach A — confirmed)

A **direct frontend ↔ server presence WebSocket**, bidirectional: each app/monitor *sends* its own presence and *receives* everyone's. Presence is **ephemeral** (a missed frame self-corrects), so it deliberately bypasses the Rust run-outbox (that's for durable uploads).

## Data contract

**Presence frame (app → server)** — sent on store-change, throttled to ~4 Hz; the sender's identity comes from the token, not the frame:
```
{ screen, course, character, kart, costume, cur_lap, tot_lap, coins, mushrooms, pos:[cx,cy]|null, final_time }
```
`coins`/`mushrooms`/`pos` already exist in the stores (engine coin/mush/minimap updates) — just not sent anywhere yet.

**Broadcast entry (server → monitors)** — one per active-season roster player:
```
{ player_id, name, color, online, screen, course, character, kart, costume,
  cur_lap, tot_lap, coins, mushrooms, completion, final_time, updated_at }
```
Offline players carry identity only (`online:false`, live fields null). A monitor gets a **full roster snapshot on connect**, then live deltas.

## Server (pi)

- **`pi/src/api/presence.ts`** — a new `/v1/presence` WebSocket route (via `@hono/node-ws`). A bearer token attributes the sender's frames to their `player_id`; token-less sockets are receive-only.
- **`pi/src/presence/hub.ts`** — `PresenceHub`: in-memory map keyed by the active-season roster. `online` = a live socket (+ a heartbeat so a stale/dropped socket flips to offline). On any change / on-/off-line it broadcasts the delta to all sockets; on a new socket it sends the full snapshot.
- **`pi/src/presence/completion.ts`** — live completion %: caches the per-course reference path (reusing increment-#3 `buildReference`/`lapBoundaries`/`completionFraction`) and projects the frame's `pos`, lap-gated by `cur_lap`. The frontend stays dumb; the bar is accurate.

## Frontend (src)

- **`src/lib/presence.js`** — a driver mirroring `discord.js`: snapshots the stores, throttles (~4 Hz), and streams frames over a WS to `<server>/v1/presence` (token from `syncSettings`), reconnecting like the bot's `ws.ts`.
- **`src/lib/stores.js`** — a new `presence` writable store, fed by the broadcast.
- Wired in `App.svelte`: init the driver when the server is configured; the #3 panel renders from the `presence` store.

## Online/offline + completion feed #3

The panel renders cards from the `presence` store — online = vivid card, offline = muted/identity-only — and `completion` drives the bar (your own card is the accuracy check you wanted).

## Testing

- **Server (vitest):** `PresenceHub` (a sender connects/updates → broadcast; disconnect → offline; snapshot on connect; roster keying; non-roster sender ignored or tagged). Live completion projection (reuse #3 fixtures: a `pos` near a known reference vertex → expected %). The WS route (authed frame → attributed broadcast; token-less socket receives but can't attribute).
- **Frontend:** `presence.js` throttle/snapshot logic unit-tested where pure; `svelte-check` + build.

## Out of scope

The card visuals (#3). Live race **time** (deferred). Durability (presence is ephemeral by design).
