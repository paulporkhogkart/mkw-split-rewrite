# Offline-First Presence (graceful no-server state) — Design

**Status:** design locked 2026-06-13. Next: implementation plan (writing-plans).

## Goal

When the app has no live connection to the season server — most importantly a **cold launch
while offline** — the monitor's player panel must not collapse to a bare "no players". Instead it
should paint the **last-known roster as dimmed offline cards** from a cached snapshot, and surface a
clear, honest **"no server connection"** indicator. The app is connection-centric (a connection is
the normal case); this work makes the *degraded* state graceful and visually consistent with the
online state. It does **not** turn the app into a full offline-first product.

## Context (what already exists)

- **Player panel is 100% live-WS.** `src/lib/presence.js` sets the `presence` store
  (`src/lib/stores.js`, `{ [player_id]: PresenceEntry }`) *only* from `/v1/presence` WebSocket
  `presence_snapshot` / `presence_update` messages. There is no persisted snapshot. No link →
  `presence = {}` → `PlayerPanel.svelte` renders a lone 9px `no players`.
- **The data tier is already cached + restart-safe** (Rust `src-tauri/src/sync.rs`): your finished
  runs queue in the `outbox` and upload on reconnect; `pb_cache` holds per-course PB totals (seeded
  from `/v1/me/pbs`, lowered locally so offline PBs fire `pb_achieved`); `course_cache` serves the
  last-good `{pb_splits, trails, friends_pbs}` per visited course offline; `screen_outbox` queues
  screen-time. **None of this changes.** This project is only about the *presence panel* surface.
- **Roster is cached** client-side in `localStorage["mkw.roster"]` (`src/lib/trailSettings.js`,
  `cacheRoster`) — but used only for trail config, never to render player cards.
- **`PresenceEntry.off_stats`** (`pi/src/presence/hub.ts`) = `{ firsts, runs_7d, pbs_30d }`:
  - `firsts` = current count of #1 leaderboard spots — a point-in-time standing, **not** clock-windowed.
  - `runs_7d` / `pbs_30d` = rolling-window counts — these **go stale** when frozen in a cache.
  - The server populates `off_stats` **only for players who were offline** at snapshot time
    (online entries carry `off_stats: null`).
- **StatusBar** (`src/components/StatusBar.svelte`) shows a single status dot — for the **engine
  sidecar IPC** (`trackerConnected`/`backendAlive`), *not* the season server. There is no
  server-connection indicator anywhere in the UI.
- **`.player-band`** (`src/App.svelte`, ~940px wide) wraps `<PlayerPanel />` with no header.

## Decisions locked (from brainstorming)

1. **Frontend-only.** Persistence lives in `presence.js` + localStorage, alongside the existing
   roster/trail caches. No Rust, no server, no schema changes. (Rust-SQLite persistence was
   considered and rejected: presence is frontend-owned + ephemeral, the snapshot is tiny, and an
   IPC round-trip + second source of truth buys nothing.)
2. **Stable stats or nothing, offline.** Cards rendered from a stale snapshot show `FIRSTS` only
   (when the cached entry has it); `RUNS·7D` / `PBS·30D` are dropped — never show a windowed number
   that is secretly older than its window.
3. **A clear "no server connection" indicator is required**, and the offline panel must look like
   the online one (same grid, same cards, dimmed) rather than a broken empty box.

## Architecture (4 small pieces)

### 1. Persist + hydrate the snapshot — `src/lib/presence.js`

- On every live `presence_snapshot` / `presence_update`, **debounce-write** (~1s) the current merged
  `presence` map plus a `syncedAt` epoch-ms to `localStorage["mkw.presence"]`:
  `{ players: { [id]: entry }, syncedAt }`. Debounce avoids hammering localStorage at the WS's 4Hz.
- On `initPresence()`, **before** the WS connects, read that blob; if present, `presence.set(players)`
  and seed `serverConnection = { connected: false, syncedAt }`. A cold launch paints cards
  immediately (rendered offline because `connected:false`). Hydration does **not** call `pushSample`
  — stale frames must never feed the race-timer buffer.
- Persisted entries are stored verbatim; live race fields are simply ignored at render time because
  the disconnected panel forces every card through the offline branch (piece 3).

### 2. `serverConnection` store — `src/lib/stores.js`

```js
export const serverConnection = writable({ connected: false, syncedAt: null });
// connected: a live presence frame has arrived and the link is up.
// syncedAt:  epoch-ms of the last live frame (for "last sync Nm ago"); survives a drop.
```

`presence.js` is the only writer:
- On a live snapshot/update message → `{ connected: true, syncedAt: Date.now() }` (so "Live" means
  *we have live data*, not merely an open socket).
- On WS `close` / `error` → `connected: false`, **keep** `syncedAt` and **keep** the `presence` map,
  so the cards stay on screen as last-known and just dim.

This is the single source of truth for "are we talking to the season server right now".

### 3. Force-offline render + stable stats — `PlayerPanel.svelte` + `src/lib/playerCard.js`

- `PlayerPanel` subscribes to `serverConnection`. When `!connected`, it maps every entry to
  `{ ...e, online: false }` so each card renders through the **existing** offline branch (no new
  card design), and passes `stale: true` to the card.
- `playerCard.js` `viewModel(e, now, delayed, opts)` gains `opts.stale`. In the offline branch:
  - `stale` → `stats = e.off_stats ? { firsts: e.off_stats.firsts } : null` (drop the windowed two).
  - not stale (server up, peer simply offline — today's behavior) → `stats = e.off_stats` unchanged.
- `PlayerCard.svelte`'s offline stats block renders **only the stat rows present** in `vm.stats`
  (so a stale card shows just `FIRSTS`, a live-offline card still shows all three, and an entry with
  no `off_stats` shows none — just identity + "last seen").
- Online behavior (server up) is otherwise completely untouched.

### 4. Connection indicator + real empty states — slim band header in `PlayerPanel.svelte`

`PlayerPanel` becomes a flex column: a slim header (flex:none) + the body (flex:1, the card grid or
the empty state). The header doubles as the long-missing panel title **and** the connection chip.

```
ONLINE          ┌─ PLAYERS ··········································· ● Live ─┐
                │ ┌──────┐┌──────┐┌──────┐┌──────┐┌──────┐                  │
                │ │ PAUL ││ ALEX ││ LUKE ││ALIIAS││ADYMER│  (live cards)    │
                │ └──────┘└──────┘└──────┘└──────┘└──────┘                  │

OFFLINE         ┌─ PLAYERS ················· ○ Offline · last sync 4m ago ─┐
(cached)        │ ┌──────┐┌──────┐┌──────┐┌──────┐┌──────┐                  │
                │ │ paul ││ alex ││ luke ││aliias││adymer│  (dimmed cards)  │
                │ └──────┘└──────┘└──────┘└──────┘└──────┘                  │

EMPTY           ┌─ PLAYERS ······························· ○ Not connected ─┐
(no cache /     │     No player data yet.                                   │
 first launch)  │     Connect a season server in Settings › Sync.           │
                └───────────────────────────────────────────────────────────┘
```

**Chip** (top-right), three tiers, derived from `serverConnection` + configured-ness
(`serverUrl`/`authToken` from `src/lib/syncSettings.js`):

| Tier | Condition | Render |
|---|---|---|
| `● Live` (green) | `connected` | "Live" |
| `○ Offline · last sync Nm ago` (amber) | `!connected && syncedAt != null` | rel-time via the exported `lastSeen()` helper, ticked by `PlayerPanel`'s existing 1s slow clock |
| `○ Not connected` (grey) | `!connected && syncedAt == null` | "Not connected" |

**Body / empty state** (only when there are zero cards):
- not configured (`!serverUrl`) → copy: **"No player data yet."** + **"Connect a season server in
  Settings › Sync."**
- configured but never synced → copy: **"No player data yet."** + **"Waiting for the season
  server…"**

**StatusBar server dot.** Add a second dot + short label to `StatusBar.svelte` (props
`serverConnected`, `serverSyncedAt`, fed from `serverConnection` in `App.svelte`), sitting beside the
engine dot, so the global truth is visible even when the panel is scrolled off. Green = Live, amber =
Offline (have cache), grey = Not connected. Reuses the existing `.hb-dot` style.

## Data flow

```
cold launch
  initPresence() → read localStorage["mkw.presence"]
     ├─ present → presence.set(players); serverConnection={connected:false, syncedAt}
     └─ absent  → presence stays {}; serverConnection={connected:false, syncedAt:null}
  PlayerPanel: !connected → cards forced offline (dimmed) OR empty state; chip = Offline/Not connected

WS connects, first live frame
  presence.set(live);  serverConnection={connected:true, syncedAt:now};  debounce-persist
  PlayerPanel: connected → live cards (today's behavior); chip = Live

WS drops
  serverConnection.connected=false (syncedAt + presence map retained)
  PlayerPanel: cards dim to offline; chip = Offline · last sync Nm ago
```

## Edge cases

- **Player online at last sync, we relaunch offline** → their cached entry has `off_stats:null`;
  card shows identity + "last seen" with no stats row (never a windowed number). Correct + honest.
- **localStorage blob corrupt / unparseable** → `try/catch`, treat as absent (empty state). Never throw.
- **Roster changed server-side while we were away** → we show the last-synced roster; it refreshes
  the instant we reconnect. Acceptable for a fixed friend competition; "last sync Nm ago" sets the
  expectation.
- **No `localStorage`** (tests / odd runtimes) → guarded like `syncSettings.js`; persistence is a
  no-op, live behavior unchanged.
- **Engine connected but server not** (and vice-versa) → the two StatusBar dots are independent;
  neither implies the other.

## Testing

- `src/lib/presence.test.js` — debounced persist writes the expected blob; `initPresence` hydrates
  `presence` + `serverConnection` from it; a WS close flips `connected:false` while retaining
  `syncedAt` + the map; hydration does not call `pushSample`.
- `src/lib/playerCard.test.js` — `stale:true` offline VM exposes `{ firsts }` only and drops
  `runs_7d`/`pbs_30d`; `stale:false` keeps all three; an entry with `off_stats:null` yields no stats.
- `PlayerPanel` render test (svelte) — the three header tiers (Live / Offline+lastsync / Not
  connected) and the empty-state copy switch on `serverConnection` + configured-ness.
- `svelte-check` 0/0; full frontend `vitest` green.

## Out of scope (YAGNI)

- Locally recomputing trails / PB splits / windowed stats from offline runs (that was the rejected
  "full offline reflection" tier — the server recomputes authoritatively on reconnect).
- Any change to the Rust `sync.rs` caches, the server, or the engine.
- Persisting the race-timer buffer or replaying a mid-race card while disconnected.
- A configurable staleness window — we always show the last roster and let "last sync Nm ago" tell
  the truth.
