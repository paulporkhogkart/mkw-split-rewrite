# Offline-First Presence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the app has no live season-server link, paint the last-known roster as dimmed offline cards from a cached snapshot and show a clear server-connection indicator, instead of collapsing the player panel to "no players".

**Architecture:** Frontend-only. `src/lib/presence.js` persists each live presence snapshot to `localStorage` and hydrates it on launch; a new `serverConnection` store tracks the link; `PlayerPanel`/`PlayerCard` render stale snapshots through the existing offline card (stable `FIRSTS` stat only); a slim band header + a StatusBar dot surface the connection state. No server, Rust, or engine changes.

**Tech Stack:** Svelte 4, Vite, Vitest (logic-only `.js` unit tests — the repo has no `@testing-library`, so `.svelte` components are verified via `svelte-check` + build), `localStorage`.

**Spec:** `docs/superpowers/specs/2026-06-13-offline-first-presence-design.md`

**Conventions:**
- Run a single test file: `npx vitest run src/lib/<file>.test.js`. Run the whole suite: `npx vitest run`.
- Type-check: `npm run check` (expect `0 errors, 0 warnings`). Build: `npm run build`.
- All commits end with the repo's `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` trailer.
- Work happens on branch `offline-first-presence` (already created; the spec commit is its first commit).

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `src/lib/stores.js` | Add the `serverConnection` writable store | Modify |
| `src/lib/presence.js` | Snapshot persist/hydrate helpers, connection-state transitions, message handler, WS wiring | Modify |
| `src/lib/presence.test.js` | Tests for persist/hydrate/transitions/message handling | Modify |
| `src/lib/playerCard.js` | `viewModel` gains `opts.stale` → force offline + `FIRSTS`-only stats | Modify |
| `src/lib/playerCard.test.js` | Tests for the stale-stats branch | Modify |
| `src/lib/playerPanel.js` | **New** pure helpers: `connectionChip`, `emptyState` | Create |
| `src/lib/playerPanel.test.js` | **New** tests for the panel helpers | Create |
| `src/components/PlayerCard.svelte` | Accept `stale` prop; render only the stat rows present | Modify |
| `src/components/PlayerPanel.svelte` | Header chip + force-offline cards + real empty states | Modify |
| `src/components/StatusBar.svelte` | Add a second dot for the season server | Modify |
| `src/App.svelte` | Import `serverConnection`; pass server props to `StatusBar` | Modify |

**Dependency order:** Task 1 → 2 → 3 (all `presence.js`). Task 4 → 6 (card stale stats). Task 5 → 7 (panel helpers). Tasks 7 and 8 depend on Task 1's store. Task 9 verifies everything.

---

## Task 1: `serverConnection` store + snapshot persistence helpers

**Files:**
- Modify: `src/lib/stores.js` (add one store)
- Modify: `src/lib/presence.js:1-12` (imports + new helpers)
- Test: `src/lib/presence.test.js`

- [ ] **Step 1: Add the store to `stores.js`**

Add after the existing `myPlayerId` line (end of file):

```js
export const serverConnection = writable({ connected: false, syncedAt: null }); // season-server link state for the player panel + StatusBar
```

- [ ] **Step 2: Write the failing test**

Append to `src/lib/presence.test.js`:

```js
import { writeSnapshot, readSnapshot } from "./presence.js";

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
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `npx vitest run src/lib/presence.test.js`
Expected: FAIL — `writeSnapshot`/`readSnapshot` are not exported.

- [ ] **Step 4: Implement the helpers in `presence.js`**

At the top of `src/lib/presence.js`, add `serverConnection` to the stores import:

```js
import { screen, selection, race, minimap, presence, myPlayerId, serverConnection } from "./stores.js";
```

Then, immediately below the existing `import` block (after the `parseTime` import line), add:

```js
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
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `npx vitest run src/lib/presence.test.js`
Expected: PASS (all blocks, including the pre-existing `frame()` / `wsUrl()` ones).

- [ ] **Step 6: Commit**

```bash
git add src/lib/stores.js src/lib/presence.js src/lib/presence.test.js
git commit -m "feat(presence): serverConnection store + snapshot persist/read helpers"
```

---

## Task 2: Hydrate + connection-state transitions

**Files:**
- Modify: `src/lib/presence.js` (new exported functions)
- Test: `src/lib/presence.test.js`

- [ ] **Step 1: Write the failing test**

Append to `src/lib/presence.test.js`:

```js
import { hydratePresence, markServerConnected, markServerDisconnected } from "./presence.js";
import { presence, serverConnection } from "./stores.js";
import { sampleAt } from "./raceTimerBuffer.js";
import { get } from "svelte/store";

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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx vitest run src/lib/presence.test.js`
Expected: FAIL — `hydratePresence` / `markServerConnected` / `markServerDisconnected` not exported.

- [ ] **Step 3: Implement in `presence.js`**

Add below the `readSnapshot` helper from Task 1:

```js
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx vitest run src/lib/presence.test.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lib/presence.js src/lib/presence.test.js
git commit -m "feat(presence): hydratePresence + connection-state transitions"
```

---

## Task 3: Message handler + WS lifecycle wiring

**Files:**
- Modify: `src/lib/presence.js` (extract `handlePresenceMessage`; wire `connect()` + `initPresence()`)
- Test: `src/lib/presence.test.js`

- [ ] **Step 1: Write the failing test**

Append to `src/lib/presence.test.js`:

```js
import { handlePresenceMessage } from "./presence.js";
import { myPlayerId } from "./stores.js";

describe("handlePresenceMessage", () => {
  it("applies a snapshot: sets presence, you, and marks connected at `now`", () => {
    presence.set({}); myPlayerId.set(null); serverConnection.set({ connected: false, syncedAt: null });
    handlePresenceMessage(JSON.stringify({
      type: "presence_snapshot", you: 1,
      players: [{ player_id: 1, name: "Paul" }, { player_id: 2, name: "Luke" }],
    }), 5000);
    expect(get(myPlayerId)).toBe(1);
    expect(Object.keys(get(presence)).sort()).toEqual(["1", "2"]);
    expect(get(serverConnection)).toEqual({ connected: true, syncedAt: 5000 });
  });
  it("merges a presence_update into the existing map and marks connected", () => {
    presence.set({ 1: { player_id: 1, name: "Paul" } });
    handlePresenceMessage(JSON.stringify({ type: "presence_update", player: { player_id: 2, name: "Luke" } }), 6000);
    expect(Object.keys(get(presence)).sort()).toEqual(["1", "2"]);
    expect(get(serverConnection).connected).toBe(true);
  });
  it("ignores malformed input without throwing", () => {
    expect(() => handlePresenceMessage("not json", 1)).not.toThrow();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx vitest run src/lib/presence.test.js`
Expected: FAIL — `handlePresenceMessage` not exported.

- [ ] **Step 3: Add a debounced persister + the message handler**

Add below `markServerDisconnected` in `presence.js`:

```js
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
```

- [ ] **Step 4: Rewire `connect()` to use the handler + mark disconnect**

In `connect()`, replace the existing `ws.addEventListener("message", ...)` block (the whole inline parser) with:

```js
  ws.addEventListener("message", (e) => handlePresenceMessage(e.data));
```

And replace the existing `ws.addEventListener("close", ...)` block with one that marks the link down first:

```js
  ws.addEventListener("close", () => {
    markServerDisconnected();
    if (closed) return;
    setTimeout(connect, backoff); backoff = Math.min(backoff * 2, 30000);
  });
```

- [ ] **Step 5: Hydrate on init**

In `initPresence()`, make hydration the first line so cached cards paint before the socket opens:

```js
export function initPresence() {
  closed = false;
  hydratePresence();   // paint last-known cards immediately; live frames overwrite on connect
  [screen, selection, race, minimap].forEach((s) => s.subscribe(() => scheduleSend()));
  hb = setInterval(rawSend, HEARTBEAT_MS);
  connect();
}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `npx vitest run src/lib/presence.test.js`
Expected: PASS (new `handlePresenceMessage` block + all earlier blocks).

- [ ] **Step 7: Commit**

```bash
git add src/lib/presence.js src/lib/presence.test.js
git commit -m "feat(presence): extract message handler, wire hydrate + connection state into the WS"
```

---

## Task 4: `viewModel` stale stats (force offline, FIRSTS only)

**Files:**
- Modify: `src/lib/playerCard.js:91-101` (the offline branch of `viewModel`)
- Test: `src/lib/playerCard.test.js`

- [ ] **Step 1: Write the failing test**

Append to `src/lib/playerCard.test.js`:

```js
describe("viewModel offline / stale stats", () => {
  const off = { player_id: 2, name: "Luke", color: "#888", online: false, updated_at: 1000,
                off_stats: { firsts: 3, runs_7d: 5, pbs_30d: 2 } };
  it("live-offline (server up, peer offline) keeps all three stats", () => {
    const vm = viewModel(off, 5000);
    expect(vm.online).toBe(false);
    expect(vm.stats).toEqual({ firsts: 3, runs_7d: 5, pbs_30d: 2 });
  });
  it("stale shows FIRSTS only", () => {
    const vm = viewModel(off, 5000, null, { stale: true });
    expect(vm.stats).toEqual({ firsts: 3 });
  });
  it("stale forces the offline view even when the cached entry was online (no off_stats)", () => {
    const onlineEntry = { ...base, online: true, off_stats: null };
    const vm = viewModel(onlineEntry, 5000, null, { stale: true });
    expect(vm.state).toBe("offline");
    expect(vm.online).toBe(false);
    expect(vm.stats).toBeNull();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx vitest run src/lib/playerCard.test.js`
Expected: FAIL — `stale` is ignored, so the third case renders the racing/live view and `vm.state` is not `"offline"`.

- [ ] **Step 3: Implement the stale branch**

In `src/lib/playerCard.js`, replace the offline guard + return (currently `if (!e.online) { ... stats: e.off_stats ?? null };`) with:

```js
  if (opts.stale || !e.online) {
    holds.delete(e.player_id);
    const seen = e.updated_at > 0 ? lastSeen(t - e.updated_at) : null;
    // Stale = we lost the server link: show only the non-windowed FIRSTS; the rolling
    // RUNS·7D / PBS·30D would silently age past their windows. Live-offline (server up,
    // peer simply offline) keeps the server's fresh full stats.
    const stats = opts.stale
      ? (e.off_stats ? { firsts: e.off_stats.firsts } : null)
      : (e.off_stats ?? null);
    return { state: "offline", name: e.name, color, online: false, char: null, kart: null, trk: null,
      primary: { kind: "seen", text: seen ? `last seen ${seen}` : "offline" },
      resets: null, pbStr: null, delta: null, finPb: false, badge: null, bar: null,
      stats };
  }
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx vitest run src/lib/playerCard.test.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lib/playerCard.js src/lib/playerCard.test.js
git commit -m "feat(playerCard): stale viewModel forces offline + FIRSTS-only stats"
```

---

## Task 5: `playerPanel.js` pure helpers (connection chip + empty state)

**Files:**
- Create: `src/lib/playerPanel.js`
- Test: `src/lib/playerPanel.test.js`

- [ ] **Step 1: Write the failing test**

Create `src/lib/playerPanel.test.js`:

```js
import { describe, it, expect } from "vitest";
import { connectionChip, emptyState } from "./playerPanel.js";

describe("connectionChip", () => {
  it("is Live when connected", () => {
    expect(connectionChip({ connected: true, syncedAt: 1000 }, 5000)).toEqual({ tier: "live", label: "Live" });
  });
  it("is Offline with the last-sync age when disconnected but synced before", () => {
    expect(connectionChip({ connected: false, syncedAt: 1000 }, 1000 + 120000))
      .toEqual({ tier: "offline", label: "Offline · last sync 2m ago" });
  });
  it("is Not connected when never synced", () => {
    expect(connectionChip({ connected: false, syncedAt: null }, 5000)).toEqual({ tier: "none", label: "Not connected" });
  });
});

describe("emptyState", () => {
  it("guides to Settings when no server is configured", () => {
    expect(emptyState(false)).toEqual({ title: "No player data yet.", hint: "Connect a season server in Settings › Sync." });
  });
  it("waits for the server when configured", () => {
    expect(emptyState(true)).toEqual({ title: "No player data yet.", hint: "Waiting for the season server…" });
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx vitest run src/lib/playerPanel.test.js`
Expected: FAIL — `./playerPanel.js` does not exist.

- [ ] **Step 3: Implement `playerPanel.js`**

Create `src/lib/playerPanel.js`:

```js
// Pure, unit-testable helpers for PlayerPanel.svelte (no Svelte/Tauri imports), mirroring
// playerCard.js. They turn the serverConnection state into the header chip + empty-state copy.
import { lastSeen } from "./playerCard.js";

/** The header connection chip. `conn` = { connected, syncedAt }; `now` is epoch-ms.
 *  live (green) = a live link; offline (amber) = no link but we have a cached snapshot,
 *  labelled with its age; none (grey) = no link and nothing cached. */
export function connectionChip(conn, now = Date.now()) {
  if (conn && conn.connected) return { tier: "live", label: "Live" };
  if (conn && conn.syncedAt != null) return { tier: "offline", label: `Offline · last sync ${lastSeen(now - conn.syncedAt)}` };
  return { tier: "none", label: "Not connected" };
}

/** Copy for the empty panel (no live or cached players). `configured` = a season-server URL is set. */
export function emptyState(configured) {
  return configured
    ? { title: "No player data yet.", hint: "Waiting for the season server…" }
    : { title: "No player data yet.", hint: "Connect a season server in Settings › Sync." };
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx vitest run src/lib/playerPanel.test.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lib/playerPanel.js src/lib/playerPanel.test.js
git commit -m "feat(playerPanel): connectionChip + emptyState pure helpers"
```

---

## Task 6: `PlayerCard.svelte` — `stale` prop + conditional stat rows

**Files:**
- Modify: `src/components/PlayerCard.svelte:1-18` (script) and the offline stats block (lines ~24-32)

- [ ] **Step 1: Add the `stale` prop and pass it through**

In the `<script>`, after `export let now = Date.now();`, add:

```js
  export let stale = false;            // server link is down: render this card offline (FIRSTS only)
```

Change the `isRacing` reactive so a stale card never samples the live buffer:

```js
  $: isRacing = !stale && !!entry && entry.online !== false && entry.screen === "RACING" && !entry.final_time;
```

Pass `stale` into the view model:

```js
  $: vm = viewModel(entry, now, delayed, { deltaMode: $deltaMode, trend, stale });
```

- [ ] **Step 2: Render only the stat rows that are present**

Replace the offline stats block (the `{#if !vm.online && vm.stats}` `<div class="sel"> … </div>` containing the three hard-coded FIRSTS / RUNS·7D / PBS·30D rows) with:

```svelte
    {#if !vm.online && vm.stats}
    <!-- Offline: stable career stats instead of dead selection rows. Render only the
         rows present — a stale (no-server) card carries FIRSTS only; a live-offline
         card carries all three. -->
    <div class="sel">
      {#if vm.stats.firsts != null}<div class="kv"><span class="kt">FIRSTS</span><span class="v">{vm.stats.firsts}</span></div>{/if}
      {#if vm.stats.runs_7d != null}<div class="kv"><span class="kt">RUNS · 7D</span><span class="v">{vm.stats.runs_7d}</span></div>{/if}
      {#if vm.stats.pbs_30d != null}<div class="kv"><span class="kt">PBS · 30D</span><span class="v">{vm.stats.pbs_30d}</span></div>{/if}
    </div>
    {:else if vm.online}
```

(Leave the `{:else if vm.online}` selection block and everything after it unchanged.)

- [ ] **Step 3: Type-check**

Run: `npm run check`
Expected: `0 errors, 0 warnings` (Task 7 wires the new prop; an unused-export note is acceptable until then, but there should be no errors).

- [ ] **Step 4: Commit**

```bash
git add src/components/PlayerCard.svelte
git commit -m "feat(card): stale prop + render only present offline stat rows"
```

---

## Task 7: `PlayerPanel.svelte` — header chip, force-offline cards, empty states

**Files:**
- Modify: `src/components/PlayerPanel.svelte` (whole component)

- [ ] **Step 1: Replace the `<script>`**

Replace the existing `<script> … </script>` with:

```svelte
<script>
  import { onDestroy } from "svelte";
  import { presence, serverConnection } from "../lib/stores.js";
  import { serverUrl } from "../lib/syncSettings.js";
  import { connectionChip, emptyState } from "../lib/playerPanel.js";
  import { C } from "../lib/palette.js";
  import PlayerCard from "./PlayerCard.svelte";

  // presence is { [player_id]: entry }; render in stable ascending player_id order.
  $: players = Object.values($presence).sort((a, b) => a.player_id - b.player_id);
  $: connected = $serverConnection.connected;
  // No live link → every card renders stale/offline, so nobody is "racing" (also drives the clock).
  $: anyRacing = connected && players.some((p) => p.online && p.screen === "RACING" && !p.final_time);
  $: configured = !!($serverUrl || "").trim();

  // One clock for all cards + the "last sync" label: ~30fps while someone races, else a
  // cheap 1s tick. Avoids a per-card animation loop.
  let now = Date.now();
  let fast = 0, slow = 0, clockRacing = null;
  function setClock(racing) {
    if (racing === clockRacing) return;
    clockRacing = racing;
    clearInterval(fast); clearInterval(slow); fast = 0; slow = 0;
    now = Date.now();
    if (racing) fast = setInterval(() => (now = Date.now()), 33);
    else slow = setInterval(() => (now = Date.now()), 1000);
  }
  $: setClock(anyRacing);
  onDestroy(() => { clearInterval(fast); clearInterval(slow); });

  $: chip = connectionChip($serverConnection, now);
  $: chipColor = chip.tier === "live" ? C.ok : chip.tier === "offline" ? C.warn : C.idle;
  $: empty = emptyState(configured);
</script>
```

- [ ] **Step 2: Replace the markup**

Replace the existing `{#if players.length} … {:else} … {/if}` block with:

```svelte
<div class="wrap">
  <div class="head">
    <span class="title">PLAYERS</span>
    <span class="chip"><span class="dot" style="background:{chipColor}"></span>{chip.label}</span>
  </div>
  {#if players.length}
    <div class="panel" style="--n:{players.length}">
      {#each players as p (p.player_id)}<PlayerCard entry={p} {now} stale={!connected} />{/each}
    </div>
  {:else}
    <div class="empty">
      <div class="empty-title">{empty.title}</div>
      <div class="empty-hint">{empty.hint}</div>
    </div>
  {/if}
</div>
```

- [ ] **Step 3: Replace the `<style>`**

Replace the existing `<style> … </style>` with:

```svelte
<style>
  .wrap { display: flex; flex-direction: column; height: 100%; }
  .head { flex: none; display: flex; align-items: center; justify-content: space-between;
          height: 20px; padding: 0 9px; background: var(--panel); border-bottom: 1px solid var(--bd); }
  .title { font-size: .62rem; letter-spacing: .14em; text-transform: uppercase; color: var(--tx-dim); }
  .chip { display: inline-flex; align-items: center; gap: 6px; font-size: .62rem; letter-spacing: .04em;
          color: var(--tx-mut); font-variant-numeric: tabular-nums; }
  .chip .dot { width: 7px; height: 7px; border-radius: 50%; flex: none; }
  .panel { flex: 1; min-height: 0; display: grid; grid-template-columns: repeat(var(--n, 5), 1fr);
           gap: 1px; background: var(--bd); }
  .empty { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center;
           gap: 4px; text-align: center; }
  .empty-title { font-size: .8rem; color: var(--tx-mut); }
  .empty-hint { font-size: .68rem; color: var(--tx-dim); letter-spacing: .02em; }
</style>
```

- [ ] **Step 4: Type-check**

Run: `npm run check`
Expected: `0 errors, 0 warnings`.

- [ ] **Step 5: Commit**

```bash
git add src/components/PlayerPanel.svelte
git commit -m "feat(panel): connection-chip header, force-offline cards offline, real empty states"
```

---

## Task 8: StatusBar server dot + App wiring

**Files:**
- Modify: `src/components/StatusBar.svelte` (props + dot + style)
- Modify: `src/App.svelte:30-31` (import) and `:1764-1773` (StatusBar props)

- [ ] **Step 1: Add server props + colour/label to `StatusBar.svelte`**

In the `<script>`, after the `frameH` prop, add:

```js
  /** Whether the season server (presence WS) is currently connected. */
  export let serverConnected = false;
  /** Epoch-ms of the last live server frame, or null if never. */
  export let serverSyncedAt = null;
```

After the existing `$: dotColor = …` line, add:

```js
  // Independent of the engine dot: green = live link, amber = offline but we have a cached
  // snapshot, grey = never connected.
  $: srvColor = serverConnected ? C.ok : serverSyncedAt != null ? C.warn : C.idle;
  $: srvLabel = serverConnected ? "server" : serverSyncedAt != null ? "server offline" : "no server";
```

- [ ] **Step 2: Render the server segment (always visible, far right)**

Immediately before the closing `</footer>`, add:

```svelte
  <span class="sb-sep">|</span>
  <span class="sb-srv"><span class="srv-dot" style="background:{srvColor}"></span>{srvLabel}</span>
```

In the `<style>`, add:

```css
  .sb-srv { display: inline-flex; align-items: center; gap: 5px; color: var(--tx-mut); }
  .srv-dot { width: 7px; height: 7px; border-radius: 50%; flex: none; }
```

- [ ] **Step 3: Wire `App.svelte`**

Add `serverConnection` to the second `stores.js` import (line 30-31), so it reads:

```js
  import { pbSplits as pbSplitsStore, pbTotalMs as pbTotalStore, friendsPbs as friendsPbsStore,
           trailRuns as trailRunsStore, trailLegend as trailLegendStore, serverConnection } from "./lib/stores.js";
```

In the `<StatusBar … />` invocation, add two props before the closing `/>`:

```svelte
    frameW={pythonFrameW}
    frameH={pythonFrameH}
    serverConnected={$serverConnection.connected}
    serverSyncedAt={$serverConnection.syncedAt}
  />
```

- [ ] **Step 4: Type-check + build**

Run: `npm run check`
Expected: `0 errors, 0 warnings`.
Run: `npm run build`
Expected: build succeeds (Vite emits to `dist-ui/`).

- [ ] **Step 5: Commit**

```bash
git add src/components/StatusBar.svelte src/App.svelte
git commit -m "feat(statusbar): season-server dot wired from serverConnection"
```

---

## Task 9: Full verification

**Files:** none (verification only).

- [ ] **Step 1: Run the entire frontend test suite**

Run: `npx vitest run`
Expected: PASS — all suites, including the new `presence` / `playerCard` / `playerPanel` cases. (Baseline was frontend 66; this adds ~13 cases.)

- [ ] **Step 2: Type-check**

Run: `npm run check`
Expected: `0 errors, 0 warnings`.

- [ ] **Step 3: Build**

Run: `npm run build`
Expected: success.

- [ ] **Step 4: Manual smoke (record results; do NOT auto-pass)**

The automated suite can't exercise the live WS, so confirm by hand (`npm run tauri dev` with the app):
1. **Cold launch offline** (server unreachable / no token) with a prior `mkw.presence` in localStorage → the panel paints dimmed cards (FIRSTS only, no RUNS·7D/PBS·30D); header chip reads `○ Offline · last sync Nm ago`; StatusBar shows the amber `server offline` dot.
2. **Fresh install, no server configured** → panel shows "No player data yet." + "Connect a season server in Settings › Sync."; chip `○ Not connected`; StatusBar grey `no server`.
3. **Server reachable** → cards relight to live within a few seconds; chip flips to `● Live`; StatusBar green `server`; live racing/finished behaviour is unchanged from before.
4. **Drop the link mid-session** (stop the server) → cards dim to offline, chip flips to `Offline · last sync …` and ticks; reconnect → relight.

- [ ] **Step 5: Final commit (only if Step 4 surfaced fixups)**

```bash
git add -A
git commit -m "fix(offline-presence): live-smoke adjustments"
```

---

## Notes for the implementer

- **Do not touch** `src-tauri/src/sync.rs`, the `pi/` server, or the engine — this is frontend-only by design (see spec's "Out of scope").
- The `mkw.presence` localStorage blob is independent of `mkw.roster` (trail config) and the sync settings; don't merge them.
- `viewModel`'s `opts.stale` is the single signal that forces a card offline — `PlayerPanel` passes `stale={!connected}` and does **not** pre-mutate entries to `online:false`.
- Leave the pre-existing unrelated `src-tauri/Cargo.toml` modification alone; never stage it with these commits.
