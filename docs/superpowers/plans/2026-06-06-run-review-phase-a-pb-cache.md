# Run Review — Phase A: PB cache + correct PB signal — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Determine "is this run a PB?" from a server-authoritative, offline-safe local cache (not the engine's stale local `replays` store), and emit a correct app-side `pb_achieved` signal.

**Architecture:** A new token-gated `GET /v1/me/pbs` returns the caller's current bests for the active season. The Tauri app keeps a `pb_cache(course_slug, cc, best_ms)` table beside the outbox, seeded from that endpoint and updated optimistically on each local PB. `sync::on_line` computes `is_pb` from the cache and returns a `pb_achieved` event for `lib.rs` to emit; the engine stops emitting its own (wrong) `pb_achieved`.

**Tech Stack:** Node + Hono + `node:sqlite` (server, vitest); Rust + rusqlite + reqwest (Tauri app, `cargo test`); Python engine (pytest).

**Scope note:** This is the foundation the Phase B gating consumes. It also fixes the false-PB *celebration* signal. It does **not** fix Discord's live in-race delta (`pbSplits`/`pbTotalMs` in `src/lib/discord.js`), which still sources from the engine's local store — that needs a server PB-*splits* endpoint and is a separate follow-up. Known unrelated issue left as-is: course display-names that don't slugify to a canonical slug (e.g. `Wario Shipyard`→`warios_galleon`, aliased only in the importer) won't match the cache or the server's `/v1/runs` lookup.

---

## File structure

- `pi/src/db/reads.ts` — add `myPbs()` query (caller's PBs for a season, joined to course slug).
- `pi/src/api/reads.ts` — add token-gated `GET /v1/me/pbs` route.
- `src-tauri/src/sync.rs` — add `slugify`, `parse_time_ms`, `pb_cache` table + `is_new_pb`, `parse_me_pbs`, `pb_event_for`; change `on_line` to return an optional event; seed/reconcile wiring in `init`/drain loop.
- `src-tauri/src/lib.rs` — emit the event `on_line` now returns.
- `mkw_tracker/lifecycle/race.py` + `mkw_tracker/main.py` — stop emitting the local-store `pb_achieved`.

---

## Task 1: Server query — `myPbs`

**Files:**
- Modify: `pi/src/db/reads.ts`
- Test: `pi/src/db/reads.test.ts`

- [ ] **Step 1: Write the failing test** (append to `pi/src/db/reads.test.ts`)

```ts
import { myPbs } from './reads';

describe('myPbs', () => {
  it('returns the player\'s PB rows as {course_slug, cc, total_time_ms}', () => {
    const db = openDb(':memory:'); applySchema(db);
    db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 1',1)");
    db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
    db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rainbow_road','Rainbow Road'),(2,'mario_circuit','Mario Circuit')");
    db.exec("INSERT INTO runs(season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb) VALUES (1,1,1,150,'finished','live',108000,1),(1,1,2,150,'finished','live',95000,0)");
    expect(myPbs(db, 1, 1)).toEqual([{ course_slug: 'rainbow_road', cc: 150, total_time_ms: 108000 }]);
  });
});
```

(If `openDb`/`applySchema` aren't already imported at the top of the file, add `import { openDb, applySchema } from './connect';`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd pi && npx vitest run src/db/reads.test.ts`
Expected: FAIL — `myPbs is not a function`.

- [ ] **Step 3: Implement `myPbs`** (append to `pi/src/db/reads.ts`)

```ts
export function myPbs(db: DatabaseSync, seasonId: number, playerId: number) {
  return db.prepare(
    `SELECT c.slug AS course_slug, r.cc, r.total_time_ms
     FROM runs r JOIN courses c ON c.id = r.course_id
     WHERE r.season_id=? AND r.player_id=? AND r.is_pb=1 AND r.total_time_ms IS NOT NULL
     ORDER BY c.slug`
  ).all(seasonId, playerId);
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd pi && npx vitest run src/db/reads.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pi/src/db/reads.ts pi/src/db/reads.test.ts
git commit -m "feat(pi): myPbs query — caller's PBs as {course_slug, cc, total_time_ms}"
```

---

## Task 2: Server route — `GET /v1/me/pbs` (token-gated)

**Files:**
- Modify: `pi/src/api/reads.ts`
- Test: `pi/src/api/reads.test.ts`

- [ ] **Step 1: Write the failing test** (append to `pi/src/api/reads.test.ts`)

```ts
import { mintToken } from '../db/players';

describe('GET /v1/me/pbs (token)', () => {
  it('401s without a token, returns the caller\'s PBs with one', async () => {
    const db = openDb(':memory:'); applySchema(db);
    db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'Season 1',1)");
    db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
    db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rainbow_road','Rainbow Road')");
    db.exec("INSERT INTO runs(season_id,player_id,course_id,cc,status,provenance,total_time_ms,is_pb) VALUES (1,1,1,150,'finished','live',108000,1)");
    const app = createApp(db, new EventHub());
    const token = mintToken(db, 'Paul');

    expect((await app.request('/v1/me/pbs')).status).toBe(401);

    const res = await app.request('/v1/me/pbs', { headers: { authorization: `Bearer ${token}` } });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual([{ course_slug: 'rainbow_road', cc: 150, total_time_ms: 108000 }]);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd pi && npx vitest run src/api/reads.test.ts`
Expected: FAIL — route returns 404 (not registered).

- [ ] **Step 3: Implement the route** (`pi/src/api/reads.ts`)

Add imports at the top:
```ts
import { requireToken } from './auth';
import { courseLeaderboard, overallLeaderboard, friendsPbs, playerPbs, currentWr, myPbs } from '../db/reads';
```
(extend the existing `../db/reads` import with `myPbs`; add the `./auth` import).

Add the route inside `readsRoutes`, before `return r;`:
```ts
  r.get('/v1/me/pbs', requireToken(db), (c) => c.json(myPbs(db, activeSeasonId(db), c.get('playerId'))));
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd pi && npx vitest run src/api/reads.test.ts`
Expected: PASS.

- [ ] **Step 5: Run the full server suite (no regressions)**

Run: `cd pi && npm test`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add pi/src/api/reads.ts pi/src/api/reads.test.ts
git commit -m "feat(pi): GET /v1/me/pbs — token-gated current bests for the active season"
```

---

## Task 3: Rust helpers — `slugify` + `parse_time_ms`

**Files:**
- Modify: `src-tauri/src/sync.rs` (add fns + unit tests in the existing `#[cfg(test)] mod tests`)

- [ ] **Step 1: Write failing tests** (add into `mod tests` in `src-tauri/src/sync.rs`)

```rust
#[test]
fn slugify_matches_server_rule() {
    assert_eq!(slugify("Rainbow Road"), "rainbow_road");
    assert_eq!(slugify("Bowser's Castle"), "bowsers_castle");   // apostrophe dropped
    assert_eq!(slugify("  Mario  Circuit "), "mario_circuit");  // collapse + trim
}

#[test]
fn parse_time_ms_handles_mmss() {
    assert_eq!(parse_time_ms("1:50.123"), Some(110123));
    assert_eq!(parse_time_ms("0:36.400"), Some(36400));
    assert_eq!(parse_time_ms("nope"), None);
}
```

- [ ] **Step 2: Run to verify they fail**

Run: `cargo test --manifest-path src-tauri/Cargo.toml slugify parse_time_ms`
Expected: FAIL — functions not found.

- [ ] **Step 3: Implement the helpers** (add near the top of `src-tauri/src/sync.rs`)

```rust
/// Mirror of the server's `slugify` (pi/src/db/slug.ts): lowercase, drop apostrophes,
/// collapse any other non-alphanumeric run to a single underscore, trim underscores.
fn slugify(name: &str) -> String {
    let mut out = String::new();
    let mut prev_us = false;
    for ch in name.to_lowercase().chars() {
        if ch == '\'' || ch == '\u{2019}' { continue; }      // straight + curly apostrophe
        if ch.is_ascii_alphanumeric() {
            out.push(ch);
            prev_us = false;
        } else if !prev_us {
            out.push('_');
            prev_us = true;
        }
    }
    out.trim_matches('_').to_string()
}

/// Parse "M:SS.mmm" → milliseconds. Returns None on any other shape.
fn parse_time_ms(t: &str) -> Option<i64> {
    let (m, rest) = t.trim().split_once(':')?;
    let (s, ms) = rest.split_once('.')?;
    if s.len() != 2 || ms.len() != 3 { return None; }
    let m: i64 = m.parse().ok()?;
    let s: i64 = s.parse().ok()?;
    let ms: i64 = ms.parse().ok()?;
    Some(m * 60_000 + s * 1_000 + ms)
}
```

- [ ] **Step 4: Run to verify they pass**

Run: `cargo test --manifest-path src-tauri/Cargo.toml slugify parse_time_ms`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src-tauri/src/sync.rs
git commit -m "feat(sync): slugify (server-matching) + M:SS.mmm time parser"
```

---

## Task 4: Rust — `pb_cache` table + `is_new_pb` (optimistic)

**Files:**
- Modify: `src-tauri/src/sync.rs`

- [ ] **Step 1: Write failing test** (add into `mod tests`)

```rust
#[test]
fn is_new_pb_optimistically_lowers_the_cache() {
    let conn = Connection::open_in_memory().unwrap();
    ensure_pb_cache(&conn);
    // No entry → it's a PB, and the cache now holds it.
    assert!(is_new_pb(&conn, "rainbow_road", 150, 110000));
    assert_eq!(pb_cache_best(&conn, "rainbow_road", 150), Some(110000));
    // Slower → not a PB, cache unchanged.
    assert!(!is_new_pb(&conn, "rainbow_road", 150, 111000));
    assert_eq!(pb_cache_best(&conn, "rainbow_road", 150), Some(110000));
    // Faster → PB, cache lowers (handles back-to-back offline PBs).
    assert!(is_new_pb(&conn, "rainbow_road", 150, 108500));
    assert_eq!(pb_cache_best(&conn, "rainbow_road", 150), Some(108500));
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cargo test --manifest-path src-tauri/Cargo.toml is_new_pb`
Expected: FAIL — items not found.

- [ ] **Step 3: Implement the cache** (add to `src-tauri/src/sync.rs`)

```rust
fn ensure_pb_cache(conn: &Connection) {
    conn.execute(
        "CREATE TABLE IF NOT EXISTS pb_cache (
            course_slug TEXT NOT NULL,
            cc          INTEGER NOT NULL,
            best_ms     INTEGER NOT NULL,
            PRIMARY KEY (course_slug, cc)
        )",
        [],
    ).expect("create pb_cache");
}

fn pb_cache_best(conn: &Connection, slug: &str, cc: i64) -> Option<i64> {
    conn.query_row(
        "SELECT best_ms FROM pb_cache WHERE course_slug=?1 AND cc=?2",
        rusqlite::params![slug, cc],
        |r| r.get::<_, i64>(0),
    ).ok()
}

fn pb_cache_put(conn: &Connection, slug: &str, cc: i64, ms: i64) {
    conn.execute(
        "INSERT INTO pb_cache(course_slug, cc, best_ms) VALUES (?1,?2,?3)
         ON CONFLICT(course_slug, cc) DO UPDATE SET best_ms=excluded.best_ms",
        rusqlite::params![slug, cc, ms],
    ).ok();
}

/// True if `ms` beats the cached best (or there is none). On true, lowers the cache
/// immediately so consecutive offline PBs are each detected.
fn is_new_pb(conn: &Connection, slug: &str, cc: i64, ms: i64) -> bool {
    match pb_cache_best(conn, slug, cc) {
        Some(best) if ms >= best => false,
        _ => { pb_cache_put(conn, slug, cc, ms); true }
    }
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cargo test --manifest-path src-tauri/Cargo.toml is_new_pb`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src-tauri/src/sync.rs
git commit -m "feat(sync): pb_cache table + is_new_pb optimistic determination"
```

---

## Task 5: Rust — seed the cache from `/v1/me/pbs`

**Files:**
- Modify: `src-tauri/src/sync.rs`

- [ ] **Step 1: Write failing test** (add into `mod tests`)

```rust
#[test]
fn parse_me_pbs_reads_rows() {
    let body = r#"[{"course_slug":"rainbow_road","cc":150,"total_time_ms":110000},
                   {"course_slug":"mario_circuit","cc":150,"total_time_ms":95000}]"#;
    let rows = parse_me_pbs(body);
    assert_eq!(rows.len(), 2);
    assert_eq!(rows[0], ("rainbow_road".to_string(), 150, 110000));
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cargo test --manifest-path src-tauri/Cargo.toml parse_me_pbs`
Expected: FAIL — not found.

- [ ] **Step 3: Implement the parser + a seed helper** (`src-tauri/src/sync.rs`)

```rust
/// Parse a `/v1/me/pbs` JSON body into (course_slug, cc, best_ms) rows. Tolerant: skips
/// malformed entries.
fn parse_me_pbs(body: &str) -> Vec<(String, i64, i64)> {
    let v: serde_json::Value = match serde_json::from_str(body) { Ok(v) => v, Err(_) => return Vec::new() };
    let arr = match v.as_array() { Some(a) => a, None => return Vec::new() };
    arr.iter().filter_map(|e| {
        Some((
            e.get("course_slug")?.as_str()?.to_string(),
            e.get("cc")?.as_i64()?,
            e.get("total_time_ms")?.as_i64()?,
        ))
    }).collect()
}

/// Replace the cache contents with the server's authoritative bests.
fn seed_pb_cache(conn: &Connection, rows: &[(String, i64, i64)]) {
    conn.execute("DELETE FROM pb_cache", []).ok();
    for (slug, cc, ms) in rows { pb_cache_put(conn, slug, *cc, *ms); }
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cargo test --manifest-path src-tauri/Cargo.toml parse_me_pbs`
Expected: PASS.

- [ ] **Step 5: Wire seeding into `init` + the drain loop** (`src-tauri/src/sync.rs`)

In `init`, after `ensure_outbox(&conn);` add `ensure_pb_cache(&conn);`.

In the drain-loop thread, fetch `/v1/me/pbs` once the config is present and re-fetch after each successful upload. Add a `seeded` flag in the loop scope and, where the config is read each cycle:
```rust
            // (inside the loop, after `let cfg = CONFIG.lock().unwrap().clone();`
            //  and the empty-config `continue`)
            if !seeded {
                let url = format!("{}/v1/me/pbs", cfg.server_url.trim_end_matches('/'));
                if let Ok(resp) = client.get(&url).bearer_auth(&cfg.token).send() {
                    if resp.status().is_success() {
                        if let Ok(body) = resp.text() {
                            let rows = parse_me_pbs(&body);
                            if let Some(c) = OUTBOX.lock().unwrap().as_ref() { seed_pb_cache(c, &rows); }
                            seeded = true;
                        }
                    }
                }
            }
```
Declare `let mut seeded = false;` just before the `loop {`. After the block that deletes a row on a successful upload, set `seeded = false;` so the next cycle reconciles the cache against the server's authoritative bests.

- [ ] **Step 6: Verify it compiles + tests pass**

Run: `cargo test --manifest-path src-tauri/Cargo.toml`
Expected: builds; all sync tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src-tauri/src/sync.rs
git commit -m "feat(sync): seed/reconcile pb_cache from GET /v1/me/pbs"
```

---

## Task 6: Rust — `on_line` computes `is_pb` and returns a `pb_achieved` event

**Files:**
- Modify: `src-tauri/src/sync.rs` (add `pb_event_for`, change `on_line` signature)
- Modify: `src-tauri/src/lib.rs` (emit the returned event)

- [ ] **Step 1: Write failing test** (add into `mod tests`)

```rust
#[test]
fn pb_event_for_finished_run_with_empty_cache() {
    let conn = Connection::open_in_memory().unwrap();
    ensure_pb_cache(&conn);
    let line = r#"{"type":"run_finalized","attempt_id":"a1","course":"Rainbow Road","status":"finished","total_time":"1:50.000"}"#;
    let ev = pb_event_for(&conn, line).unwrap();
    let v: serde_json::Value = serde_json::from_str(&ev).unwrap();
    assert_eq!(v["type"], "pb_achieved");
    assert_eq!(v["course"], "Rainbow Road");
    assert_eq!(v["time"], "1:50.000");
    // A reset, or a second slower finish, yields no event.
    assert!(pb_event_for(&conn, r#"{"type":"run_finalized","attempt_id":"r","course":"Rainbow Road","status":"reset"}"#).is_none());
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cargo test --manifest-path src-tauri/Cargo.toml pb_event_for`
Expected: FAIL — not found.

- [ ] **Step 3: Implement `pb_event_for` and rewrite `on_line`** (`src-tauri/src/sync.rs`)

```rust
/// If `line` is a finished run that beats the cached best for its course, returns a
/// `pb_achieved` JSON line (and optimistically lowers the cache). cc is 150 until the
/// engine reports it (server default matches).
fn pb_event_for(conn: &Connection, line: &str) -> Option<String> {
    let v: serde_json::Value = serde_json::from_str(line).ok()?;
    if v.get("status")?.as_str()? != "finished" { return None; }
    let course = v.get("course")?.as_str()?;
    let time = v.get("total_time")?.as_str()?;
    let ms = parse_time_ms(time)?;
    if is_new_pb(conn, &slugify(course), 150, ms) {
        Some(serde_json::json!({ "type": "pb_achieved", "course": course, "time": time }).to_string())
    } else {
        None
    }
}
```

Replace the body of `on_line` so it returns the optional event:
```rust
/// Called by lib.rs for every sidecar stdout line. Enqueues run_finalized events and
/// returns a `pb_achieved` event to emit when the run is a new PB.
pub fn on_line(line: &str) -> Option<String> {
    if !is_run_finalized(line) {
        return None;
    }
    let id = attempt_id_of(line)?;
    let guard = OUTBOX.lock().ok()?;
    let conn = guard.as_ref()?;
    outbox_insert(conn, &id, line);
    pb_event_for(conn, line)
}
```

- [ ] **Step 4: Emit the event in `lib.rs`** (`src-tauri/src/lib.rs`, the `CommandEvent::Stdout` arm)

Replace:
```rust
                            let _ = handle.emit("tracker-event", msg.as_ref());
                            sync::on_line(msg.as_ref());
```
with:
```rust
                            let _ = handle.emit("tracker-event", msg.as_ref());
                            if let Some(ev) = sync::on_line(msg.as_ref()) {
                                let _ = handle.emit("tracker-event", &ev);
                            }
```

- [ ] **Step 5: Run tests + build**

Run: `cargo test --manifest-path src-tauri/Cargo.toml`
Expected: builds; all PASS (the existing `detects_run_finalized_*` / outbox tests still pass).

- [ ] **Step 6: Commit**

```bash
git add src-tauri/src/sync.rs src-tauri/src/lib.rs
git commit -m "feat(sync): app-side pb_achieved from the PB cache (on_line)"
```

---

## Task 7: Engine — stop emitting the stale local-store `pb_achieved`

**Files:**
- Modify: `mkw_tracker/lifecycle/race.py` (drop the `pending_pb_event` assignment)
- Modify: `mkw_tracker/main.py` (drop the `pending_pb_event` emit block)
- Test: `tests/test_run_finalized.py` (unaffected; run to confirm)

- [ ] **Step 1: Remove the PB-event assignment in `race.py`**

In `_finalize_recording`, delete the trailing PB block:
```python
        if completed and best_total_time and course and replay_id is not None:
            from ..database.replay_repo import get_pb
            pb = get_pb(course)
            if pb and pb.get("id") == replay_id:
                self.pending_pb_event = (course, best_total_time)
```
Also remove the now-unused attribute init `self.pending_pb_event = None` (and its comment) in `__init__`.

- [ ] **Step 2: Remove the emit block in `main.py`**

Delete:
```python
        if getattr(lifecycle, "pending_pb_event", None) is not None:
            _pb_course, _pb_time = lifecycle.pending_pb_event
            ipc.emit(emit_pb_achieved(_pb_course, _pb_time))
            lifecycle.pending_pb_event = None
```
If `emit_pb_achieved` is now unused in `main.py`, remove it from its import line (leave `emit_pb_achieved` defined in `protocol.py` — Phase B/Discord follow-up may reuse the shape).

- [ ] **Step 3: Run the engine tests**

Run: `python -m pytest tests/test_run_finalized.py tests/test_pb_splits.py -q`
Expected: PASS (no test asserts `pending_pb_event`; if one does, delete that assertion — the signal moved to the app).

- [ ] **Step 4: Full engine suite (no regressions)**

Run: `python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mkw_tracker/lifecycle/race.py mkw_tracker/main.py
git commit -m "refactor(engine): retire local-store pb_achieved (PB now app-side)"
```

---

## Self-review

- **Spec coverage:** Phase A items from the spec — `/v1/me/pbs` (Task 2), slug-keyed `pb_cache` seeded + optimistic (Tasks 3–5), app-side `is_pb`/`pb_achieved` replacing the engine's local-store signal (Tasks 6–7). Covered. The Discord live-delta and the gating/popup are explicitly Phase B/follow-up.
- **Type/name consistency:** `slugify`, `parse_time_ms`, `pb_cache_best/put`, `is_new_pb`, `parse_me_pbs`, `seed_pb_cache`, `pb_event_for`, `on_line(&str) -> Option<String>` used consistently across tasks; `course_slug/cc/total_time_ms` match between `myPbs` (Task 1), the route (Task 2), and `parse_me_pbs` (Task 5).
- **Placeholders:** none — every code/step is concrete.
