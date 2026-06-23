# Persist "last seen" across server restarts — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist each player's last-seen timestamp to the database so offline player-cards still show "last seen X ago" after a Pi restart/auto-update, instead of collapsing to a bare "offline".

**Architecture:** Add a `last_seen_at` (epoch-ms) column to the `players` table. The in-memory `PresenceHub` writes it on connect, on disconnect, and via a 30s periodic flush, and seeds it back into each card's `updated_at` on boot. The existing frontend "last seen X ago" rendering is the consumer — no client changes. Both the Tauri monitor and thekartoff.com inherit the fix via the shared `/v1/presence` snapshot.

**Tech Stack:** TypeScript (Node 22 `node:sqlite` `DatabaseSync`), Vitest. Server code under `pi/`.

## Global Constraints

Every task implicitly includes these (verbatim from the spec):

- **Column type: `last_seen_at INTEGER`, nullable, epoch ms.** `NULL` = never seen. (Not ISO `TEXT`; it round-trips with the hub's `Date.now()`/`updated_at` clock.)
- **Server-side only.** No frontend, IPC, Rust `sync.rs`, or website file changes. The existing `playerCard.js` render path is the consumer.
- **Writes are best-effort.** A durability write must never throw out of the hub — wrap DB writes in `try/catch`.
- **Inline-prepared statements** (`this.db.prepare(...).run(...)` per call), matching `offStats`/`pbForCourse`. Do **not** cache a prepared statement in an instance field: under `tsconfig` `target: ES2023` native class-field semantics, field initializers run before the `private db` parameter-property assignment, so `this.db` is undefined at field-init time.
- **Migrations are additive** via the established `try { db.exec('ALTER TABLE … ADD COLUMN …'); } catch {}` pattern in `pi/src/db/connect.ts:applySchema()`.
- **Test command:** from `pi/`, full suite `npm test` (= `vitest run`); single file/test `npx vitest run <file> -t "<name>"`; typecheck `npx tsc --noEmit`.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `server/schema.sql` | Modify | Add `last_seen_at INTEGER` to the `players` table (fresh DBs). |
| `pi/src/db/connect.ts` | Modify | Additive `ALTER TABLE players ADD COLUMN last_seen_at` (existing DBs). |
| `pi/src/db/connect.test.ts` | Modify | Migration test: column added to a pre-existing players table, idempotently. |
| `pi/src/presence/hub.ts` | Modify | `writeLastSeen()` helper; seed-restore in `seedRoster()`; on-connect stamp in `update()`; on-disconnect stamp in `setOffline()`; new `persistLastSeen()`. |
| `pi/src/presence/hub.test.ts` | Modify | Seed-restore, on-connect/on-disconnect persist, round-trip, periodic-flush tests. |
| `pi/src/server.ts` | Modify | `setInterval(() => presence.persistLastSeen(), 30000)`. |

---

### Task 1: Schema column + additive migration

**Files:**
- Modify: `server/schema.sql` (the `players` `CREATE TABLE`, ~lines 10-16)
- Modify: `pi/src/db/connect.ts:19` (beside the existing `color` ALTER)
- Test: `pi/src/db/connect.test.ts` (append a new `describe`)

**Interfaces:**
- Consumes: nothing.
- Produces: a nullable `players.last_seen_at` INTEGER column, present on both fresh DBs (schema.sql) and migrated DBs (the ALTER). Later tasks read/write it.

- [ ] **Step 1: Write the failing migration test**

Append to `pi/src/db/connect.test.ts`:

```ts
describe('applySchema last_seen_at migration', () => {
  it('adds last_seen_at to a pre-existing players table, idempotently', () => {
    const db = new DatabaseSync(':memory:');
    // Legacy players shape (predates last_seen_at): applySchema's CREATE TABLE
    // IF NOT EXISTS is a no-op, so only the additive ALTER can add the column.
    db.exec(`CREATE TABLE players(
      id INTEGER PRIMARY KEY, display_name TEXT NOT NULL UNIQUE,
      auth_token_hash TEXT UNIQUE, color TEXT,
      created_at TEXT NOT NULL DEFAULT (datetime('now')));
      INSERT INTO players(id,display_name) VALUES (1,'Paul');`);
    applySchema(db);   // additive ALTER adds last_seen_at
    db.prepare('UPDATE players SET last_seen_at=? WHERE id=?').run(1717000000000, 1);
    expect((db.prepare('SELECT last_seen_at FROM players WHERE id=1').get() as { last_seen_at: number }).last_seen_at)
      .toBe(1717000000000);
    applySchema(db);   // idempotent second boot: ALTER is caught, value survives
    expect((db.prepare('SELECT last_seen_at FROM players WHERE id=1').get() as { last_seen_at: number }).last_seen_at)
      .toBe(1717000000000);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd pi && npx vitest run src/db/connect.test.ts -t "adds last_seen_at"`
Expected: FAIL — the `UPDATE players SET last_seen_at` throws `no column named last_seen_at` (the migration doesn't exist yet).

- [ ] **Step 3: Add the column to the canonical schema (fresh DBs)**

In `server/schema.sql`, add the column to the `players` table:

```sql
CREATE TABLE IF NOT EXISTS players (
    id              INTEGER PRIMARY KEY,
    display_name    TEXT NOT NULL UNIQUE,
    auth_token_hash TEXT UNIQUE,
    color           TEXT,
    last_seen_at    INTEGER,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
```

- [ ] **Step 4: Add the additive migration (existing DBs)**

In `pi/src/db/connect.ts`, immediately after the existing color ALTER (line 19):

```ts
  try { db.exec('ALTER TABLE players ADD COLUMN color TEXT'); } catch { /* already present */ }
  // Additive: durable last-seen timestamp (epoch ms). Seeded back into presence on boot
  // so offline cards survive a restart. Nullable -> never seen.
  try { db.exec('ALTER TABLE players ADD COLUMN last_seen_at INTEGER'); } catch { /* already present */ }
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd pi && npx vitest run src/db/connect.test.ts -t "adds last_seen_at"`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add server/schema.sql pi/src/db/connect.ts pi/src/db/connect.test.ts
git commit -m "feat(presence): add players.last_seen_at column + additive migration"
```

---

### Task 2: Restore last-seen on boot (`seedRoster`)

**Files:**
- Modify: `pi/src/presence/hub.ts:96-102` (`seedRoster`)
- Test: `pi/src/presence/hub.test.ts` (append inside the `describe('PresenceHub', …)` block)

**Interfaces:**
- Consumes: `players.last_seen_at` (Task 1).
- Produces: a seeded offline `PresenceEntry` whose `updated_at` equals the stored `last_seen_at` (or `0` when `NULL`). No signature change — `offlineEntry`'s 4th parameter is already the `updated_at` slot.

- [ ] **Step 1: Write the failing test**

Append inside the `describe('PresenceHub', …)` block in `pi/src/presence/hub.test.ts`:

```ts
it('seeds offline updated_at from the stored last_seen_at (restores after a restart)', () => {
  const d = db();
  d.prepare('UPDATE players SET last_seen_at=? WHERE id=?').run(1717000000000, 1);
  const hub = new PresenceHub(d, noCompletion, noPace, noLaps, () => 9999999999999);
  const got: any[] = [];
  hub.addSink((m) => got.push(m));
  const paul = got[0].players.find((p: any) => p.player_id === 1);
  const luke = got[0].players.find((p: any) => p.player_id === 2);
  expect(paul.updated_at).toBe(1717000000000);   // restored from the db
  expect(luke.updated_at).toBe(0);               // NULL last_seen_at -> 0 (never seen)
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd pi && npx vitest run src/presence/hub.test.ts -t "restores after a restart"`
Expected: FAIL — `paul.updated_at` is `0` (seedRoster currently hardcodes `offlineEntry(..., 0, ...)`), not `1717000000000`.

- [ ] **Step 3: Read last_seen_at in seedRoster and seed it as updated_at**

Replace `seedRoster()` in `pi/src/presence/hub.ts`:

```ts
  seedRoster(): void {
    const rows = this.db.prepare(
      `SELECT p.id, p.display_name, p.color, p.last_seen_at FROM season_rosters sr JOIN players p ON p.id=sr.player_id WHERE sr.season_id=?`
    ).all(activeSeasonId(this.db)) as { id: number; display_name: string; color: string | null; last_seen_at: number | null }[];
    for (const r of rows)
      if (!this.map.has(r.id))
        this.map.set(r.id, offlineEntry(r.id, r.display_name, r.color, r.last_seen_at ?? 0, this.cachedOffStats(r.id)));
  }
```

- [ ] **Step 4: Run the test (and the existing never-seen test) to verify they pass**

Run: `cd pi && npx vitest run src/presence/hub.test.ts -t "restores after a restart"`
Expected: PASS.
Run: `cd pi && npx vitest run src/presence/hub.test.ts -t "never seen"`
Expected: PASS (default `db()` has no `last_seen_at` → `NULL` → `0`, unchanged).

- [ ] **Step 5: Commit**

```bash
git add pi/src/presence/hub.ts pi/src/presence/hub.test.ts
git commit -m "feat(presence): restore last-seen into updated_at on boot"
```

---

### Task 3: Persist on connect + disconnect

**Files:**
- Modify: `pi/src/presence/hub.ts` — add `writeLastSeen()`; wire into `update()` (~line 141) and `setOffline()` (~line 203)
- Test: `pi/src/presence/hub.test.ts` (append inside the `describe('PresenceHub', …)` block)

**Interfaces:**
- Consumes: `players.last_seen_at` (Task 1); seed-restore (Task 2) for the round-trip.
- Produces: `private writeLastSeen(playerId: number, ts: number): void` — best-effort `UPDATE players SET last_seen_at=? WHERE id=?`. Called on the offline→online transition in `update()` and in `setOffline()`.

- [ ] **Step 1: Write the failing tests**

Append inside the `describe('PresenceHub', …)` block in `pi/src/presence/hub.test.ts`:

```ts
it('persists last_seen_at to the db on disconnect', () => {
  const d = db();
  const hub = new PresenceHub(d, noCompletion, noPace, noLaps, () => 4242);
  hub.addSink(() => {});
  hub.update(1, { screen: 'MAIN_MENU' });   // Paul online
  hub.setOffline(1);                        // -> persists 4242
  expect((d.prepare('SELECT last_seen_at FROM players WHERE id=1').get() as { last_seen_at: number }).last_seen_at)
    .toBe(4242);
});

it('persists last_seen_at on the offline->online transition only', () => {
  const d = db();
  let t = 1000;
  const hub = new PresenceHub(d, noCompletion, noPace, noLaps, () => t);
  hub.addSink(() => {});
  hub.update(1, { screen: 'MAIN_MENU' });    // offline -> online: persists 1000
  expect((d.prepare('SELECT last_seen_at FROM players WHERE id=1').get() as { last_seen_at: number }).last_seen_at)
    .toBe(1000);
  t = 2000;
  hub.update(1, { screen: 'RACING' });       // already online: NO db write
  expect((d.prepare('SELECT last_seen_at FROM players WHERE id=1').get() as { last_seen_at: number }).last_seen_at)
    .toBe(1000);                             // still the connect value
});

it('round-trips last-seen across a restart (persist on offline, restore on a new hub)', () => {
  const d = db();
  const hub1 = new PresenceHub(d, noCompletion, noPace, noLaps, () => 555000);
  hub1.addSink(() => {});
  hub1.update(1, { screen: 'MAIN_MENU' });   // online
  hub1.setOffline(1);                        // persists 555000
  const hub2 = new PresenceHub(d, noCompletion, noPace, noLaps, () => 999999);   // "restart"
  const got: any[] = [];
  hub2.addSink((m) => got.push(m));
  expect(got[0].players.find((p: any) => p.player_id === 1).updated_at).toBe(555000);
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd pi && npx vitest run src/presence/hub.test.ts -t "persists last_seen_at"`
Expected: FAIL — nothing writes `last_seen_at`, so the column stays `NULL` (`null !== 4242`).

- [ ] **Step 3: Add the `writeLastSeen` helper**

In `pi/src/presence/hub.ts`, add a private method (e.g. just above `private pbForCourse`):

```ts
  /** Best-effort durable last-seen stamp (epoch ms). Inline-prepared (a cached field
   *  can't read this.db at field-init time). A DB hiccup must never break presence,
   *  so failures are swallowed. */
  private writeLastSeen(playerId: number, ts: number): void {
    try { this.db.prepare('UPDATE players SET last_seen_at=? WHERE id=?').run(ts, playerId); }
    catch { /* non-fatal */ }
  }
```

- [ ] **Step 4: Stamp on the offline→online transition in `update()`**

In `update()`, capture the prior online state and write on the transition. The method already computes `const now = this.now();` near the top — reuse it. Add `const wasOnline = cur.online;` right after the `if (!cur) return;` guard, and the write right after `this.map.set(playerId, entry);`:

```ts
  update(playerId: number, frame: PresenceFrame): void {
    const cur = this.map.get(playerId);
    if (!cur) return;
    const wasOnline = cur.online;
    const now = this.now();
    // … existing body unchanged through entry construction …
    this.map.set(playerId, entry);
    if (!wasOnline) this.writeLastSeen(playerId, now);   // offline -> online: stamp the reconnect
    this.broadcast({ type: 'presence_update', player: entry });
  }
```

- [ ] **Step 5: Stamp on disconnect in `setOffline()`**

Replace `setOffline()` so a single `now` feeds both the in-memory anchor and the persisted value:

```ts
  setOffline(playerId: number): void {
    this.pbLatch.delete(playerId);
    const e = this.map.get(playerId);
    if (!e || !e.online) return;
    const now = this.now();
    const off = offlineEntry(e.player_id, e.name, e.color, now, this.cachedOffStats(playerId));
    this.map.set(playerId, off);
    this.writeLastSeen(playerId, now);
    this.broadcast({ type: 'presence_update', player: off });
  }
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd pi && npx vitest run src/presence/hub.test.ts -t "persists last_seen_at"`
Expected: PASS (both persist tests).
Run: `cd pi && npx vitest run src/presence/hub.test.ts -t "round-trips last-seen"`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add pi/src/presence/hub.ts pi/src/presence/hub.test.ts
git commit -m "feat(presence): persist last-seen on connect + disconnect"
```

---

### Task 4: Periodic flush + server wiring

**Files:**
- Modify: `pi/src/presence/hub.ts` — add `persistLastSeen()` (public)
- Modify: `pi/src/server.ts:36` (beside the existing sweep interval)
- Test: `pi/src/presence/hub.test.ts` (append inside the `describe('PresenceHub', …)` block)

**Interfaces:**
- Consumes: `writeLastSeen()` (Task 3).
- Produces: `persistLastSeen(): void` — flushes every online entry's `updated_at` to `players.last_seen_at`; offline entries are skipped. Called every 30s from `server.ts`.

- [ ] **Step 1: Write the failing test**

Append inside the `describe('PresenceHub', …)` block in `pi/src/presence/hub.test.ts`:

```ts
it('persistLastSeen flushes online entries only', () => {
  const d = db();
  let t = 7000;
  const hub = new PresenceHub(d, noCompletion, noPace, noLaps, () => t);
  hub.addSink(() => {});
  hub.update(1, { screen: 'RACING' });   // Paul online @7000 (connect write = 7000)
  t = 8000;
  hub.update(1, { screen: 'RACING' });   // still online @8000 (updated_at advances; no db write)
  hub.persistLastSeen();                 // flush: Paul -> 8000; Luke (never connected) untouched
  expect((d.prepare('SELECT last_seen_at FROM players WHERE id=1').get() as { last_seen_at: number }).last_seen_at)
    .toBe(8000);
  expect((d.prepare('SELECT last_seen_at FROM players WHERE id=2').get() as { last_seen_at: number | null }).last_seen_at)
    .toBe(null);
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd pi && npx vitest run src/presence/hub.test.ts -t "flushes online entries only"`
Expected: FAIL — `presence.persistLastSeen is not a function` (method doesn't exist yet).

- [ ] **Step 3: Add the `persistLastSeen` method**

In `pi/src/presence/hub.ts`, add a public method (e.g. just after `sweep`):

```ts
  /** Flush every online entry's last-seen to the db (durability backstop for crashes/
   *  auto-updates; bounds post-restart staleness to the call interval). Offline entries
   *  don't change — their value was persisted at the disconnect transition. */
  persistLastSeen(): void {
    for (const e of this.map.values()) if (e.online) this.writeLastSeen(e.player_id, e.updated_at);
  }
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd pi && npx vitest run src/presence/hub.test.ts -t "flushes online entries only"`
Expected: PASS.

- [ ] **Step 5: Wire the periodic flush in server.ts**

In `pi/src/server.ts`, add beside the existing sweep interval (line 36):

```ts
setInterval(() => presence.sweep(15000), 5000);   // flip dead/stale sockets offline
setInterval(() => presence.persistLastSeen(), 30000);   // durable last-seen (survives restarts)
```

- [ ] **Step 6: Typecheck + full suite**

Run: `cd pi && npx tsc --noEmit`
Expected: no errors (catches any type issue in `server.ts`, which no test imports).
Run: `cd pi && npm test`
Expected: PASS — all pi tests green (the prior baseline plus the new cases).

- [ ] **Step 7: Commit**

```bash
git add pi/src/presence/hub.ts pi/src/server.ts pi/src/presence/hub.test.ts
git commit -m "feat(presence): periodic last-seen flush + server wiring"
```

---

## Self-Review

**Spec coverage:**
- Schema `last_seen_at INTEGER` + additive migration → Task 1. ✓
- Write on connect → Task 3 Step 4. ✓
- Write on disconnect → Task 3 Step 5. ✓
- Periodic 30s flush (online only) → Task 4. ✓
- Boot restore in `seedRoster` (NULL→0 preserved) → Task 2. ✓
- Best-effort inline-prepared helper (no cached field) → Task 3 Step 3 + Global Constraints. ✓
- `server.ts` interval wiring → Task 4 Step 5. ✓
- No frontend/IPC/website change → none planned; verified by file list. ✓
- Tests: seed-read, disconnect-persist, connect-persist (transition-only), periodic-flush (online-only), round-trip → Tasks 2-4. ✓
- Edge: NULL→"offline" preserved (existing "never seen" test stays green) → Task 2 Step 4. ✓

**Placeholder scan:** none — every step has concrete code/commands.

**Type consistency:** `writeLastSeen(playerId: number, ts: number)` defined in Task 3, consumed by `persistLastSeen` in Task 4. `seedRoster` row type extended with `last_seen_at: number | null`. `offlineEntry(id, name, color, updated_at, off_stats)` 4th-arg usage matches its existing signature (`now: number`). DB reads cast to `{ last_seen_at: number | null }`. Consistent throughout.
