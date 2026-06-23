# Persist "last seen" across server restarts — Design

**Status:** design locked 2026-06-23. Next: implementation plan (writing-plans).

## Goal

A player's offline card shows **"last seen 8m ago"** (and the website's cards do too). That timestamp
currently lives only in the server's RAM, so every Pi **restart / auto-update wipes it** — after a
reboot every offline card collapses to a bare **"offline"** with no history. This work persists the
last-seen moment to the database so it **survives restarts**, and seeds it back on boot so the
existing "last seen X ago" rendering just works. It is a server-side durability fix only; it does
**not** change how cards look or what "last seen" means.

## Context (what already exists)

- **Presence is 100% in-memory.** `PresenceHub` (`pi/src/presence/hub.ts`) holds
  `private map = new Map<player_id, PresenceEntry>()`. Nothing about presence is persisted.
- **`PresenceEntry.updated_at` (epoch ms) is the last-seen anchor.** It is set to `now` on every
  applied frame (`update()`) and to `now` when a player goes offline (`setOffline()`).
- **The frontend already renders last-seen from it** — unchanged by this work. `playerCard.js`
  `viewModel()` offline branch:
  ```js
  const seen = e.updated_at > 0 ? lastSeen(t - e.updated_at) : null;
  primary: { kind: "seen", text: seen ? `last seen ${seen}` : "offline" }
  ```
  So `updated_at > 0` → "last seen X ago"; `updated_at === 0` → bare "offline". `lastSeen()`
  (`src/lib/playerCard.js`) buckets to just now / Nm / Nh / Nd ago.
- **On boot, the anchor is zero.** `seedRoster()` seeds every roster player via
  `offlineEntry(id, name, color, 0, …)` — i.e. `updated_at: 0`. **This is the bug:** after a restart
  the only state we have is "offline, no history".
- **Both clients read one source.** The Tauri monitor and thekartoff.com both consume the same
  `/v1/presence` snapshot produced by this single hub, so a fix in the hub reaches both with no
  client changes.
- **`players` table** (`server/schema.sql`) is `id, display_name, auth_token_hash, color,
  created_at`. No last-seen column today.
- **Additive migrations** for existing DBs are a settled pattern in
  `pi/src/db/connect.ts:applySchema()` — `try { db.exec('ALTER TABLE … ADD COLUMN …'); } catch {}`.
- **The hub already reads the DB inline** (`seedRoster`, `offStats`, `pbForCourse` each call
  `this.db.prepare(...).…`) and takes an injectable `now: () => number` for deterministic tests.
- **`setInterval` is the server's loop idiom** — `server.ts` already runs
  `setInterval(() => presence.sweep(15000), 5000)` plus the WR scrapers.

## Decisions locked (from brainstorming)

1. **Persistence cadence: periodic + on every contact change.** Write last-seen on connect, on
   disconnect, and refresh it every ~30s while a player stays online. (Chosen over "on-disconnect
   only", which is weakest in exactly the restart-while-online case this fixes.)
2. **Column on `players`, not a new table.** Last-seen is one scalar per person; a dedicated table
   would be over-engineering. It is a property of the player (global), not season-scoped.
3. **Epoch-ms `INTEGER`, not ISO `TEXT`.** It round-trips directly with the hub's `updated_at` /
   `Date.now()` clock and is purely machine-consumed (rendered into a relative label). `created_at`
   stays `TEXT`; this column intentionally differs.
4. **No frontend / IPC / website changes.** The fix is entirely in the hub + schema; the existing
   render path is the consumer.

## Architecture (4 small pieces)

### 1. Schema — `last_seen_at` column

- `server/schema.sql`: add `last_seen_at INTEGER` to the `players` table (fresh DBs).
- `pi/src/db/connect.ts:applySchema()`: additive
  `try { db.exec('ALTER TABLE players ADD COLUMN last_seen_at INTEGER'); } catch { /* present */ }`
  alongside the existing `color` migration.
- Nullable. `NULL` = never seen.

### 2. Write path — `PresenceHub` (`pi/src/presence/hub.ts`)

A single private best-effort helper, called from the three contact points:

```ts
/** Best-effort durable last-seen stamp (epoch ms). A DB hiccup must never break presence. */
private writeLastSeen(playerId: number, ts: number): void {
  try { this.db.prepare('UPDATE players SET last_seen_at=? WHERE id=?').run(ts, playerId); }
  catch { /* non-fatal */ }
}
```
(Prepared inline per call, matching `offStats` / `pbForCourse`. A cached prepared-statement *field*
is **not** an option here: under native class-field semantics — which `tsconfig` `target: ES2023`
selects — instance field initializers run before the `private db` parameter-property assignment, so
`this.db` would be undefined at field-init time. Inline prepare sidesteps that and the write volume is
trivial.)

- **On (re)connect** — in `update()`, when the entry was offline and this frame brings it online
  (`const wasOnline = cur.online;` … `if (!wasOnline) this.writeLastSeen(playerId, now);`). Stamps
  the moment contact resumes; closes the gap where a short session ends in a hard kill before the
  first periodic flush (relevant because frequent restarts cause frequent reconnects).
- **On disconnect** — in `setOffline()`, capture `const now = this.now()` once and use it for both
  the offline entry's `updated_at` and `this.writeLastSeen(playerId, now)`, so the in-memory anchor
  and the persisted value match exactly. Captures the exact drop moment.
- **Periodic, while online** — a new public method:
  ```ts
  /** Flush every online entry's last-seen to the DB (durability backstop for crashes/auto-updates,
   *  bounding post-restart staleness to the call interval). Offline entries don't change. */
  persistLastSeen(): void {
    for (const e of this.map.values()) if (e.online) this.writeLastSeen(e.player_id, e.updated_at);
  }
  ```

### 3. Boot / seed path — `seedRoster()`

- Add `p.last_seen_at` to the roster SELECT.
- Seed the offline entry with it as the anchor:
  `offlineEntry(r.id, r.display_name, r.color, r.last_seen_at ?? 0, this.cachedOffStats(r.id))`.
- After a restart, offline cards immediately show the accurate "last seen X ago"; `NULL → 0` keeps
  today's bare "offline" for never-seen players. `offlineEntry`'s 4th param is already the
  `updated_at` slot, so a backdated value is semantically correct with no signature change.

### 4. Periodic flush wiring — `server.ts`

Add beside the existing sweep:
```ts
setInterval(() => presence.persistLastSeen(), 30000);   // durable last-seen (survives restarts)
```

## Data flow

```
boot
  seedRoster() reads players.last_seen_at
     ├─ value → offlineEntry(updated_at = last_seen_at)  → card: "last seen X ago"
     └─ NULL  → offlineEntry(updated_at = 0)             → card: "offline"

player connects (first frame, offline→online)
  update(): wasOnline=false → writeLastSeen(now); entry online (today's behavior)

while online
  every 30s: persistLastSeen() → UPDATE players SET last_seen_at = entry.updated_at

player disconnects (onClose / sweep)
  setOffline(): entry.updated_at = now; writeLastSeen(now)

server restart  → back to "boot": last value reloaded, history intact
```

## Edge cases

- **Never-seen player** (`last_seen_at NULL`) → anchor 0 → bare "offline". Today's behavior, preserved.
- **Backdated anchor vs. dead-socket sweep.** `sweep()` only flips *online* entries; seeded entries
  are offline, so their past `updated_at` is never mistaken for a stale live socket.
- **Short session ending in a hard kill** (online < one flush interval, no clean `onClose`) → not
  captured by the periodic flush; the **on-connect stamp covers the common case**, and the worst case
  is an *older* last-seen, **never** a wrong "online" or a future time.
- **Client/server clock skew.** The card computes `clientNow - updated_at`; server writes server-ms.
  This skew already exists for live sessions today and is harmless at minute-bucket granularity.
- **DB write failure / locked.** `writeLastSeen` swallows errors — presence broadcasting must never
  break for a durability write. (WAL + single process makes this near-impossible anyway.)
- **Restart-while-online, then reconnect.** The app reconnects within seconds; the card shows the
  accurate gap ("last seen just now" / "1m ago") during downtime, then flips live on reconnect.

## Testing

Unit tests in `pi/src/presence/hub.test.ts` (its in-memory `db()` helper + injected `now`):

- **Seed reads the column** — insert a player with `last_seen_at = T`; a fresh hub's snapshot entry
  has `updated_at === T`. A `NULL` column → `updated_at === 0`.
- **Disconnect persists** — `setOffline(id)` writes `now` to `players.last_seen_at` (assert via a DB
  read).
- **(Re)connect persists** — an `update()` that takes an offline entry online writes `now`; a
  subsequent in-session frame does **not** re-stamp on the DB (only the transition does).
- **Periodic flush persists online only** — after frames make a player online, `persistLastSeen()`
  writes their `updated_at`; an offline player's stored value is untouched by the flush.
- **Round-trip** — write via `setOffline`, build a new hub on the same DB, assert the seeded
  `updated_at` matches (the restart scenario end-to-end).

`pi` vitest green; no frontend or svelte-check impact (no client files change).

## Out of scope (YAGNI)

- Any change to the "last seen X ago" format or the `lastSeen()` buckets (e.g. an absolute date for
  very stale entries). This persists the existing string; it does not redesign it.
- Per-frame DB writes (the 30s flush + transition stamps already bound staleness).
- A separate presence/last-seen table, or persisting any other presence field (screen, course, race
  state stay ephemeral — only last-seen needs to outlive a restart).
- Frontend, IPC, Rust `sync.rs`, or website code. A graceful SIGTERM flush (considered, deferred —
  the periodic flush already covers planned restarts within its interval).
