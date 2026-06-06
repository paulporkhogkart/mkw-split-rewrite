# Run Review — Phase B2: Rust gating + review commands — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hold incomplete finished/reset runs in the outbox as `pending_review` (emitting a `run_needs_review` event) instead of uploading them, and add commands to resolve (fill + release), discard, or list pending runs.

**Architecture:** The outbox gains a `status` column (`ready`|`pending_review`); the drain loop only uploads `ready`. `sync::on_line` computes the run's PB status once (from the existing `pb_cache`) and the missing required fields (the completeness matrix), then routes: complete → `ready` (+ `pb_achieved` if a PB), incomplete → `pending_review` (+ `run_needs_review`). Three Tauri commands let the UI resolve/discard/list held runs.

**Tech Stack:** Rust + rusqlite (`src-tauri/src/sync.rs`), `cargo test`.

**Scope note:** Spec `docs/superpowers/specs/2026-06-06-run-review-gated-upload-design.md`. The completeness matrix: every run needs course/character/kart; finished also needs total_time; finished **PB** also needs, per lap (1..`total_laps`), `time_str` + `coins` + `shrooms`. `0`/negative are valid; only `null`/empty is missing. `total_laps`/`coins`/`shrooms`/`time_str` come from B1 (already merged). PB is the app's local cache determination (`is_new_pb`). B3 (frontend) consumes `run_needs_review` + the commands. `default_laps` is NOT needed here (Rust uses the payload's `total_laps`); it's a deferred server-side validation aid.

---

## File structure
- `src-tauri/src/sync.rs` — outbox `status` + helpers; `missing_fields` completeness; `on_line` routing; `is_finished_new_pb`; `sync_resolve_pending`/`sync_discard_pending`/`sync_list_pending`.
- `src-tauri/src/lib.rs` — register the three new commands.

---

## Task 1: outbox `status` column + helpers

**Files:** Modify `src-tauri/src/sync.rs`

- [ ] **Step 1: Write failing tests** (add into `mod tests`)

```rust
#[test]
fn outbox_status_routes_ready_vs_pending() {
    let conn = Connection::open_in_memory().unwrap();
    ensure_outbox(&conn);
    outbox_insert(&conn, "a1", LINE, "ready");
    outbox_insert(&conn, "a2", LINE, "pending_review");
    // drain (outbox_pending) sees only ready rows
    let pend: Vec<_> = outbox_pending(&conn).into_iter().map(|(id, _)| id).collect();
    assert_eq!(pend, vec!["a1"]);
    // list_pending sees only pending_review rows
    let review: Vec<_> = outbox_list_pending(&conn).into_iter().map(|(id, _)| id).collect();
    assert_eq!(review, vec!["a2"]);
}

#[test]
fn outbox_update_ready_merges_body_and_flips_status() {
    let conn = Connection::open_in_memory().unwrap();
    ensure_outbox(&conn);
    outbox_insert(&conn, "a2", LINE, "pending_review");
    outbox_update_ready(&conn, "a2", r#"{"type":"run_finalized","attempt_id":"a2","course":"X"}"#);
    // now visible to the drain (ready), gone from pending
    assert_eq!(outbox_pending(&conn).len(), 1);
    assert_eq!(outbox_list_pending(&conn).len(), 0);
    assert_eq!(outbox_get_body(&conn, "a2").unwrap(), r#"{"type":"run_finalized","attempt_id":"a2","course":"X"}"#);
}
```

- [ ] **Step 2: Run to verify they fail**

Run: `cargo test --manifest-path src-tauri/Cargo.toml outbox_status outbox_update_ready`
Expected: FAIL — `outbox_insert` arity / `outbox_list_pending` / `outbox_update_ready` / `outbox_get_body` not found.

- [ ] **Step 3: Implement the column + helpers** (`src-tauri/src/sync.rs`)

Replace `ensure_outbox` with a version that creates the `status` column and migrates an existing table:
```rust
fn ensure_outbox(conn: &Connection) {
    conn.execute(
        "CREATE TABLE IF NOT EXISTS outbox (
            attempt_id TEXT PRIMARY KEY,
            body       TEXT NOT NULL,
            status     TEXT NOT NULL DEFAULT 'ready',
            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        )",
        [],
    ).expect("create outbox");
    // Migrate a pre-existing table (Phase A had no status column). Errors if the
    // column already exists — ignored.
    conn.execute("ALTER TABLE outbox ADD COLUMN status TEXT NOT NULL DEFAULT 'ready'", []).ok();
}
```

Replace `outbox_insert` to take a status:
```rust
fn outbox_insert(conn: &Connection, attempt_id: &str, body: &str, status: &str) {
    conn.execute(
        "INSERT OR REPLACE INTO outbox(attempt_id, body, status) VALUES (?1, ?2, ?3)",
        rusqlite::params![attempt_id, body, status],
    ).ok();
}
```

Make `outbox_pending` only return `ready` rows:
```rust
fn outbox_pending(conn: &Connection) -> Vec<(String, String)> {
    let mut stmt = conn.prepare(
        "SELECT attempt_id, body FROM outbox WHERE status='ready' ORDER BY created_at"
    ).unwrap();
    let rows = stmt.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?))).unwrap();
    rows.filter_map(|r| r.ok()).collect()
}
```

Add the new helpers (next to the other outbox helpers):
```rust
fn outbox_list_pending(conn: &Connection) -> Vec<(String, String)> {
    let mut stmt = conn.prepare(
        "SELECT attempt_id, body FROM outbox WHERE status='pending_review' ORDER BY created_at"
    ).unwrap();
    let rows = stmt.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?))).unwrap();
    rows.filter_map(|r| r.ok()).collect()
}

fn outbox_get_body(conn: &Connection, attempt_id: &str) -> Option<String> {
    conn.query_row(
        "SELECT body FROM outbox WHERE attempt_id=?1",
        rusqlite::params![attempt_id],
        |r| r.get::<_, String>(0),
    ).ok()
}

fn outbox_update_ready(conn: &Connection, attempt_id: &str, body: &str) {
    conn.execute(
        "UPDATE outbox SET body=?2, status='ready' WHERE attempt_id=?1",
        rusqlite::params![attempt_id, body],
    ).ok();
}
```

Keep `on_line` compiling: its current call is `outbox_insert(conn, &id, line)`. Change it to `outbox_insert(conn, &id, line, "ready")` for now (Task 3 replaces the whole body with real routing).

Also fix the existing test `outbox_is_idempotent_by_attempt_id`: its two `outbox_insert(&conn, "a1", LINE)` calls now need the status arg — change both to `outbox_insert(&conn, "a1", LINE, "ready")`.

- [ ] **Step 4: Run to verify all pass**

Run: `cargo test --manifest-path src-tauri/Cargo.toml`
Expected: builds; all sync tests PASS (new + existing).

- [ ] **Step 5: Commit**

```bash
git add src-tauri/src/sync.rs
git commit -m "feat(sync): outbox status column (ready|pending_review) + review helpers"
```
Append via a second `-m`: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

## Task 2: completeness check (`missing_fields`)

**Files:** Modify `src-tauri/src/sync.rs`

- [ ] **Step 1: Write failing tests** (add into `mod tests`)

```rust
fn _v(s: &str) -> serde_json::Value { serde_json::from_str(s).unwrap() }

#[test]
fn missing_fields_matrix() {
    // complete finished, non-PB → nothing missing (laps not required)
    let complete = _v(r#"{"course":"X","character":"Mario","kart":"K","status":"finished","total_time":"1:00.000"}"#);
    assert!(missing_fields(&complete, false).is_empty());

    // reset missing character → ["character"]
    let reset = _v(r#"{"course":"X","character":"","kart":"K","status":"reset"}"#);
    assert_eq!(missing_fields(&reset, false), vec!["character"]);

    // finished missing total_time → ["total_time"]
    let no_total = _v(r#"{"course":"X","character":"M","kart":"K","status":"finished"}"#);
    assert_eq!(missing_fields(&no_total, false), vec!["total_time"]);

    // finished PB, all laps complete → nothing missing
    let pb_ok = _v(r#"{"course":"X","character":"M","kart":"K","status":"finished","total_time":"1:00.000","total_laps":2,
        "laps":[{"lap":1,"time_str":"0:30.000","coins":5,"shrooms":2},{"lap":2,"time_str":"0:30.000","coins":-1,"shrooms":0}]}"#);
    assert!(missing_fields(&pb_ok, true).is_empty());

    // finished PB, lap 2 coins null → ["lap_2_coins"]
    let pb_gap = _v(r#"{"course":"X","character":"M","kart":"K","status":"finished","total_time":"1:00.000","total_laps":2,
        "laps":[{"lap":1,"time_str":"0:30.000","coins":5,"shrooms":2},{"lap":2,"time_str":"0:30.000","coins":null,"shrooms":0}]}"#);
    assert_eq!(missing_fields(&pb_gap, true), vec!["lap_2_coins"]);

    // finished PB but total_laps unknown → splits not checkable, so only identity/total apply (none missing here)
    let pb_no_n = _v(r#"{"course":"X","character":"M","kart":"K","status":"finished","total_time":"1:00.000"}"#);
    assert!(missing_fields(&pb_no_n, true).is_empty());
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cargo test --manifest-path src-tauri/Cargo.toml missing_fields_matrix`
Expected: FAIL — `missing_fields` not found.

- [ ] **Step 3: Implement `missing_fields`** (`src-tauri/src/sync.rs`)

```rust
/// The gating matrix. Returns the ids of missing required fields; empty = complete
/// enough to upload. `0`/negative are valid; only null/empty counts as missing.
fn missing_fields(v: &serde_json::Value, is_pb: bool) -> Vec<String> {
    let mut missing = Vec::new();
    let str_present = |key: &str| -> bool {
        v.get(key).and_then(|x| x.as_str()).map(|s| !s.trim().is_empty()).unwrap_or(false)
    };
    for key in ["course", "character", "kart"] {
        if !str_present(key) { missing.push(key.to_string()); }
    }
    let status = v.get("status").and_then(|x| x.as_str()).unwrap_or("");
    if status == "finished" {
        let total_ok = v.get("total_time").and_then(|x| x.as_str())
            .and_then(parse_time_ms).is_some();
        if !total_ok { missing.push("total_time".to_string()); }
        if is_pb {
            if let Some(n) = v.get("total_laps").and_then(|x| x.as_i64()) {
                let laps = v.get("laps").and_then(|x| x.as_array());
                for lap_no in 1..=n {
                    let entry = laps.and_then(|arr| arr.iter().find(|e|
                        e.get("lap").and_then(|x| x.as_i64()) == Some(lap_no)));
                    let time_ok = entry.and_then(|e| e.get("time_str")).and_then(|x| x.as_str())
                        .map(|s| !s.is_empty()).unwrap_or(false);
                    let coins_ok = entry.and_then(|e| e.get("coins")).map(|x| !x.is_null()).unwrap_or(false);
                    let shrooms_ok = entry.and_then(|e| e.get("shrooms")).map(|x| !x.is_null()).unwrap_or(false);
                    if !time_ok { missing.push(format!("lap_{lap_no}_time")); }
                    if !coins_ok { missing.push(format!("lap_{lap_no}_coins")); }
                    if !shrooms_ok { missing.push(format!("lap_{lap_no}_shrooms")); }
                }
            }
            // total_laps absent → can't verify splits; don't gate on them.
        }
    }
    missing
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cargo test --manifest-path src-tauri/Cargo.toml missing_fields_matrix`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src-tauri/src/sync.rs
git commit -m "feat(sync): missing_fields - the run-completeness gating matrix"
```
Append via a second `-m`: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

## Task 3: `on_line` routing (ready vs pending_review)

**Files:** Modify `src-tauri/src/sync.rs`

- [ ] **Step 1: Write failing tests** (add into `mod tests`; also DELETE the old `pb_event_for_finished_run_with_empty_cache` test — `pb_event_for` is replaced below)

```rust
#[test]
fn on_line_routes_complete_pb_to_ready_with_pb_achieved() {
    let conn = Connection::open_in_memory().unwrap();
    ensure_outbox(&conn); ensure_pb_cache(&conn);
    *OUTBOX.lock().unwrap() = Some(conn);
    let line = r#"{"type":"run_finalized","attempt_id":"a1","course":"Rainbow Road","character":"Mario","kart":"K","status":"finished","total_time":"1:50.000","total_laps":1,"laps":[{"lap":1,"time_str":"1:50.000","coins":3,"shrooms":1}]}"#;
    let ev: serde_json::Value = serde_json::from_str(&on_line(line).unwrap()).unwrap();
    assert_eq!(ev["type"], "pb_achieved");
    let g = OUTBOX.lock().unwrap();
    assert_eq!(outbox_pending(g.as_ref().unwrap()).len(), 1);          // ready → drains
    assert_eq!(outbox_list_pending(g.as_ref().unwrap()).len(), 0);
    *OUTBOX.lock().unwrap() = None;
}

#[test]
fn on_line_holds_incomplete_run_for_review() {
    let conn = Connection::open_in_memory().unwrap();
    ensure_outbox(&conn); ensure_pb_cache(&conn);
    *OUTBOX.lock().unwrap() = Some(conn);
    // missing kart → held
    let line = r#"{"type":"run_finalized","attempt_id":"b1","course":"Rainbow Road","character":"Mario","kart":"","status":"finished","total_time":"1:50.000"}"#;
    let ev: serde_json::Value = serde_json::from_str(&on_line(line).unwrap()).unwrap();
    assert_eq!(ev["type"], "run_needs_review");
    assert_eq!(ev["attempt_id"], "b1");
    assert!(ev["missing"].as_array().unwrap().iter().any(|m| m == "kart"));
    assert!(ev["run"].get("type").is_none());                          // stripped
    let g = OUTBOX.lock().unwrap();
    assert_eq!(outbox_pending(g.as_ref().unwrap()).len(), 0);          // NOT uploaded
    assert_eq!(outbox_list_pending(g.as_ref().unwrap()).len(), 1);     // held
    *OUTBOX.lock().unwrap() = None;
}
```
(These tests use the real `OUTBOX` static, so set + clear it; run them serially — they already are, single-threaded by default for these.)

- [ ] **Step 2: Run to verify they fail**

Run: `cargo test --manifest-path src-tauri/Cargo.toml on_line_`
Expected: FAIL — `on_line` doesn't return these shapes yet.

- [ ] **Step 3: Replace `pb_event_for` with `is_finished_new_pb` and rewrite `on_line`** (`src-tauri/src/sync.rs`)

Replace the `pb_event_for` function with:
```rust
/// True if this is a finished run whose time beats the cached best (lowers the cache).
fn is_finished_new_pb(conn: &Connection, v: &serde_json::Value) -> bool {
    if v.get("status").and_then(|x| x.as_str()) != Some("finished") { return false; }
    let Some(course) = v.get("course").and_then(|x| x.as_str()) else { return false; };
    let Some(ms) = v.get("total_time").and_then(|x| x.as_str()).and_then(parse_time_ms) else { return false; };
    is_new_pb(conn, &slugify(course), 150, ms)
}
```

Rewrite `on_line`:
```rust
/// Called by lib.rs for every sidecar stdout line. Routes run_finalized events:
/// complete → outbox `ready` (+ `pb_achieved` if a PB); incomplete → `pending_review`
/// (+ `run_needs_review` for the UI). Returns the event to emit, if any.
pub fn on_line(line: &str) -> Option<String> {
    if !is_run_finalized(line) { return None; }
    let id = attempt_id_of(line)?;
    let v: serde_json::Value = serde_json::from_str(line).ok()?;
    let guard = OUTBOX.lock().ok()?;
    let conn = guard.as_ref()?;
    let is_pb = is_finished_new_pb(conn, &v);
    let missing = missing_fields(&v, is_pb);
    if missing.is_empty() {
        outbox_insert(conn, &id, line, "ready");
        if is_pb {
            let course = v.get("course").and_then(|x| x.as_str()).unwrap_or("");
            let time = v.get("total_time").and_then(|x| x.as_str()).unwrap_or("");
            return Some(serde_json::json!({"type":"pb_achieved","course":course,"time":time}).to_string());
        }
        None
    } else {
        outbox_insert(conn, &id, line, "pending_review");
        let mut run = v.clone();
        if let Some(o) = run.as_object_mut() { o.remove("type"); }
        Some(serde_json::json!({
            "type": "run_needs_review", "attempt_id": id, "is_pb": is_pb,
            "missing": missing, "run": run,
        }).to_string())
    }
}
```

- [ ] **Step 4: Run to verify all pass**

Run: `cargo test --manifest-path src-tauri/Cargo.toml`
Expected: builds; all PASS (the two new on_line tests + the rest; the deleted `pb_event_for` test is gone).

- [ ] **Step 5: Commit**

```bash
git add src-tauri/src/sync.rs
git commit -m "feat(sync): on_line gates incomplete runs to pending_review + run_needs_review"
```
Append via a second `-m`: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

## Task 4: resolve / discard / list commands

**Files:** Modify `src-tauri/src/sync.rs`, `src-tauri/src/lib.rs`

- [ ] **Step 1: Write a failing test** (add into `mod tests`)

```rust
#[test]
fn resolve_merges_filled_fields_and_releases() {
    let conn = Connection::open_in_memory().unwrap();
    ensure_outbox(&conn);
    outbox_insert(&conn, "b1",
        r#"{"type":"run_finalized","attempt_id":"b1","course":"Rainbow Road","character":"Mario","kart":null,"status":"finished"}"#,
        "pending_review");
    // merge the corrected fields (the pure helper the command wraps)
    let filled: serde_json::Value = serde_json::from_str(r#"{"kart":"Standard Kart"}"#).unwrap();
    resolve_in_outbox(&conn, "b1", &filled);
    let body = outbox_get_body(&conn, "b1").unwrap();
    let v: serde_json::Value = serde_json::from_str(&body).unwrap();
    assert_eq!(v["kart"], "Standard Kart");
    assert_eq!(outbox_pending(&conn).len(), 1);            // now ready
    assert_eq!(outbox_list_pending(&conn).len(), 0);
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cargo test --manifest-path src-tauri/Cargo.toml resolve_merges`
Expected: FAIL — `resolve_in_outbox` not found.

- [ ] **Step 3: Implement the pure helper + the three commands** (`src-tauri/src/sync.rs`)

```rust
/// Merge `filled` (a JSON object of corrected fields) into the stored body and flip the
/// row to `ready`. Pure (takes the conn) so it's unit-testable.
fn resolve_in_outbox(conn: &Connection, attempt_id: &str, filled: &serde_json::Value) {
    let Some(body) = outbox_get_body(conn, attempt_id) else { return };
    let Ok(mut v) = serde_json::from_str::<serde_json::Value>(&body) else { return };
    if let (Some(obj), Some(f)) = (v.as_object_mut(), filled.as_object()) {
        for (k, val) in f { obj.insert(k.clone(), val.clone()); }
    }
    if let Ok(merged) = serde_json::to_string(&v) {
        outbox_update_ready(conn, attempt_id, &merged);
    }
}

#[tauri::command]
pub fn sync_resolve_pending(attempt_id: String, filled: serde_json::Value) {
    if let Ok(guard) = OUTBOX.lock() {
        if let Some(conn) = guard.as_ref() { resolve_in_outbox(conn, &attempt_id, &filled); }
    }
}

#[tauri::command]
pub fn sync_discard_pending(attempt_id: String) {
    if let Ok(guard) = OUTBOX.lock() {
        if let Some(conn) = guard.as_ref() { outbox_delete(conn, &attempt_id); }
    }
}

/// JSON array of `{attempt_id, run}` for every held (pending_review) run, for the UI to
/// resurface on launch. `type` is stripped from each run.
#[tauri::command]
pub fn sync_list_pending() -> String {
    if let Ok(guard) = OUTBOX.lock() {
        if let Some(conn) = guard.as_ref() {
            let arr: Vec<serde_json::Value> = outbox_list_pending(conn).iter().filter_map(|(id, body)| {
                let mut v: serde_json::Value = serde_json::from_str(body).ok()?;
                if let Some(o) = v.as_object_mut() { o.remove("type"); }
                Some(serde_json::json!({ "attempt_id": id, "run": v }))
            }).collect();
            return serde_json::to_string(&arr).unwrap_or_else(|_| "[]".into());
        }
    }
    "[]".into()
}
```

- [ ] **Step 4: Register the commands** (`src-tauri/src/lib.rs`)

In the `invoke_handler![ ... ]` list, append after `sync::sync_test_connection`:
```rust
, sync::sync_resolve_pending, sync::sync_discard_pending, sync::sync_list_pending
```

- [ ] **Step 5: Run tests + build**

Run: `cargo test --manifest-path src-tauri/Cargo.toml`
Expected: builds (lib.rs compiles with the new commands registered); all PASS.

- [ ] **Step 6: Commit**

```bash
git add src-tauri/src/sync.rs src-tauri/src/lib.rs
git commit -m "feat(sync): sync_resolve_pending / discard / list commands for held runs"
```
Append via a second `-m`: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

## Self-review
- **Spec coverage:** outbox `status` + drain filters ready (Task 1); completeness matrix incl. PB per-lap coins/shrooms, `0`/negative valid (Task 2); `on_line` routes ready/pending_review + `pb_achieved`/`run_needs_review`, PB computed once (Task 3); resolve/discard/list commands (Task 4). `default_laps` intentionally not used (Rust uses payload `total_laps`).
- **Type/name consistency:** `outbox_insert(conn, id, body, status)`, `outbox_pending` (ready), `outbox_list_pending` (pending_review), `outbox_get_body`, `outbox_update_ready`, `resolve_in_outbox`, `missing_fields(v, is_pb)`, `is_finished_new_pb` used consistently; `on_line` still returns `Option<String>` (lib.rs caller unchanged). Commands: `sync_resolve_pending(attempt_id, filled)` (JS sends camelCase `attemptId` + `filled`), `sync_discard_pending(attempt_id)`, `sync_list_pending()`.
- **Placeholders:** none.
