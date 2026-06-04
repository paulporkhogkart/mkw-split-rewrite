# Client Write Path — Tauri/Rust Sync (sub-project B, Phase 1, part 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect the engine to the server. A new `src-tauri/src/sync.rs` (decoupled module, mirroring `discord.rs`) consumes the engine's `run_finalized` events from the sidecar's stdout, persists them in a **rusqlite outbox**, and a background task uploads them to `{server_url}/v1/runs` with a bearer token (**reqwest**), surviving offline. A "Sync" settings tab (server URL + token + cc) configures it.

**Architecture:** Same decoupled pattern as the Discord plugin. Frontend: `syncSettings.js` (localStorage-backed stores) → `sync.js` (`invoke("sync_set_config", …)` on change). Rust: `sync.rs` holds the config + a rusqlite outbox; `lib.rs` routes `run_finalized` stdout lines into `sync::on_line`; a drain loop POSTs pending rows (idempotent by `attempt_id`) and deletes on `2xx`. The engine never sees the server (invariant: the Tauri app is the engine's only runtime peer).

**Tech Stack:** Rust (Tauri 2, `tokio` via `tauri::async_runtime`, new crates **reqwest** + **rusqlite**), `cargo test`/`cargo build`; Svelte + `localStorage`, `svelte-check`. Spec: `docs/superpowers/specs/2026-06-04-server-api-and-sync-design.md` (§14).

**Testing strategy (honest for this layer):** Rust **unit tests** (`#[cfg(test)]`, like `discord.rs`) cover the pure logic — `build_upload_body` (strip `type`, inject `cc`), `attempt_id_of`, and the outbox CRUD against an in-memory rusqlite. The reqwest uploader + drain loop + frontend wiring are verified by **`cargo build` + `svelte-check` (compile gates)** and a **documented manual end-to-end smoke** against the local `pi/` server (no GUI integration test). This matches how the Discord plugin is structured (pure logic unit-tested, transport smoke-tested).

Every commit ends with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Work on a new branch off `main` (Task 1). Rust commands run from `src-tauri/`; JS from the repo root.

**API caveat:** the reqwest (0.12) / rusqlite (0.32) / Tauri 2 path APIs in this plan reflect their expected shapes. If an installed version differs (e.g. `app.path().app_data_dir()`, reqwest builder, rusqlite `query_map`), make the minimal adaptation that preserves the tested behavior; if it's not obvious, STOP and report.

---

## File Structure

| Path | Change |
|---|---|
| `src-tauri/Cargo.toml` | Add `reqwest` (json) + `rusqlite` (bundled). |
| `src-tauri/src/sync.rs` | NEW. Config state, outbox (rusqlite), `build_upload_body`/`attempt_id_of`, `on_line`/`enqueue`, reqwest uploader + drain loop, `init`, `sync_set_config` command, tests. |
| `src-tauri/src/lib.rs` | `mod sync;`; route `run_finalized` in the `Stdout` arm; add `sync_set_config` to the handler; `sync::init(...)` in `setup`. |
| `src/lib/syncSettings.js` | NEW. `serverUrl` / `authToken` / `cc` stores (localStorage), mirroring `discordSettings.js`. |
| `src/lib/sync.js` | NEW. `initSync()` — subscribe to the stores, `invoke("sync_set_config", …)`. |
| `src/App.svelte` | Call `initSync()` in `onMount` (next to `initDiscordPresence()`). |
| `src/components/SettingsModal.svelte` | Add a "sync" wizard step + a `{:else if wizardStep === "sync"}` block. |

**Interfaces locked here:**
- Rust: `pub fn on_line(line: &str)`; `pub fn init(app: tauri::AppHandle)`; `#[tauri::command] pub fn sync_set_config(server_url: String, token: String, cc: i64)`; pure `fn build_upload_body(line: &str, cc: i64) -> Option<String>`, `fn attempt_id_of(line: &str) -> Option<String>`.
- JS: stores `serverUrl`, `authToken`, `cc` (default `150`); `initSync()`.

---

### Task 1: Cargo deps + sync.rs pure logic + outbox (TDD via `cargo test`)

**Files:** Modify `src-tauri/Cargo.toml`; create `src-tauri/src/sync.rs`.

- [ ] **Step 1: Create the branch**

Run (repo root): `git checkout -b client-write-path-sync`

- [ ] **Step 2: Add deps to `src-tauri/Cargo.toml`** (under `[dependencies]`)

```toml
reqwest = { version = "0.12", features = ["json", "blocking"] }
rusqlite = { version = "0.32", features = ["bundled"] }
```

- [ ] **Step 3: Write `src-tauri/src/sync.rs` with the pure logic + outbox + failing tests**

```rust
//! Decoupled client→server upload module (mirrors discord.rs).
//! Consumes engine `run_finalized` events, persists them in a rusqlite outbox,
//! and a background task uploads them to the server. The engine never sees the network.
use rusqlite::Connection;

/// Extract the `attempt_id` from a run_finalized JSON line.
fn attempt_id_of(line: &str) -> Option<String> {
    let v: serde_json::Value = serde_json::from_str(line).ok()?;
    v.get("attempt_id")?.as_str().map(|s| s.to_string())
}

/// Turn a stored run_finalized line into the POST body: drop `type`, set `cc`.
fn build_upload_body(line: &str, cc: i64) -> Option<String> {
    let mut v: serde_json::Value = serde_json::from_str(line).ok()?;
    let obj = v.as_object_mut()?;
    obj.remove("type");
    obj.insert("cc".into(), serde_json::json!(cc));
    serde_json::to_string(&v).ok()
}

fn ensure_outbox(conn: &Connection) {
    conn.execute(
        "CREATE TABLE IF NOT EXISTS outbox (
            attempt_id TEXT PRIMARY KEY,
            body       TEXT NOT NULL,
            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        )",
        [],
    ).expect("create outbox");
}

fn outbox_insert(conn: &Connection, attempt_id: &str, body: &str) {
    conn.execute(
        "INSERT OR REPLACE INTO outbox(attempt_id, body) VALUES (?1, ?2)",
        rusqlite::params![attempt_id, body],
    ).ok();
}

fn outbox_pending(conn: &Connection) -> Vec<(String, String)> {
    let mut stmt = conn.prepare("SELECT attempt_id, body FROM outbox ORDER BY created_at").unwrap();
    let rows = stmt.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?))).unwrap();
    rows.filter_map(|r| r.ok()).collect()
}

fn outbox_delete(conn: &Connection, attempt_id: &str) {
    conn.execute("DELETE FROM outbox WHERE attempt_id = ?1", rusqlite::params![attempt_id]).ok();
}

#[cfg(test)]
mod tests {
    use super::*;

    const LINE: &str = r#"{"type":"run_finalized","attempt_id":"a1","course":"Rainbow Road","status":"finished","total_time":"1:50.000"}"#;

    #[test]
    fn attempt_id_is_extracted() {
        assert_eq!(attempt_id_of(LINE).as_deref(), Some("a1"));
        assert_eq!(attempt_id_of("not json"), None);
    }

    #[test]
    fn upload_body_drops_type_and_sets_cc() {
        let body = build_upload_body(LINE, 150).unwrap();
        let v: serde_json::Value = serde_json::from_str(&body).unwrap();
        assert!(v.get("type").is_none());
        assert_eq!(v["cc"], 150);
        assert_eq!(v["attempt_id"], "a1");
        assert_eq!(v["course"], "Rainbow Road");
    }

    #[test]
    fn outbox_is_idempotent_by_attempt_id() {
        let conn = Connection::open_in_memory().unwrap();
        ensure_outbox(&conn);
        outbox_insert(&conn, "a1", LINE);
        outbox_insert(&conn, "a1", LINE);          // same id → replace, not duplicate
        assert_eq!(outbox_pending(&conn).len(), 1);
        outbox_delete(&conn, "a1");
        assert_eq!(outbox_pending(&conn).len(), 0);
    }
}
```

- [ ] **Step 4: Run the tests**

Run (from `src-tauri/`): `cargo test sync::`
Expected: PASS (3 tests). (First run compiles the new crates — may take a minute.)

- [ ] **Step 5: Commit**

```bash
git add src-tauri/Cargo.toml src-tauri/Cargo.lock src-tauri/src/sync.rs
git commit -m "feat(sync): outbox + upload-body logic (reqwest + rusqlite deps)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: sync.rs runtime — config, enqueue, uploader, drain loop, init

**Files:** Modify `src-tauri/src/sync.rs`.

- [ ] **Step 1: Add the runtime above the `#[cfg(test)]` block**

```rust
use std::sync::Mutex;

#[derive(Default, Clone)]
struct Config { server_url: String, token: String, cc: i64 }

static CONFIG: Mutex<Config> = Mutex::new(Config { server_url: String::new(), token: String::new(), cc: 150 });
static OUTBOX: Mutex<Option<Connection>> = Mutex::new(None);

/// Called by lib.rs for every sidecar stdout line. Enqueues run_finalized events; ignores the rest.
pub fn on_line(line: &str) {
    if !line.contains("\"type\":\"run_finalized\"") {
        return; // cheap pre-filter
    }
    let Some(id) = attempt_id_of(line) else { return };
    if let Ok(mut guard) = OUTBOX.lock() {
        if let Some(conn) = guard.as_ref() {
            outbox_insert(conn, &id, line);
        } else {
            log::warn!("[sync] outbox not initialised; dropping run_finalized");
        }
    }
}

#[tauri::command]
pub fn sync_set_config(server_url: String, token: String, cc: i64) {
    if let Ok(mut c) = CONFIG.lock() {
        *c = Config { server_url, token, cc };
    }
}

/// Open the outbox DB in the app data dir, then spawn the drain loop.
pub fn init(app: tauri::AppHandle) {
    use tauri::Manager;
    let dir = match app.path().app_data_dir() {
        Ok(d) => d,
        Err(e) => { log::error!("[sync] no app_data_dir: {e}"); return; }
    };
    let _ = std::fs::create_dir_all(&dir);
    let conn = match Connection::open(dir.join("sync_outbox.db")) {
        Ok(c) => c,
        Err(e) => { log::error!("[sync] open outbox failed: {e}"); return; }
    };
    ensure_outbox(&conn);
    *OUTBOX.lock().unwrap() = Some(conn);

    // A dedicated OS thread (not the tokio runtime) running a blocking loop:
    // rusqlite is sync and reqwest::blocking manages its own runtime, so this
    // avoids any dependency on Tauri's async runtime having a time driver.
    std::thread::spawn(move || {
        let client = reqwest::blocking::Client::new();
        loop {
            std::thread::sleep(std::time::Duration::from_secs(3));
            let cfg = CONFIG.lock().unwrap().clone();
            if cfg.server_url.is_empty() || cfg.token.is_empty() {
                continue;
            }
            // Snapshot pending rows WITHOUT holding the lock during the blocking POSTs.
            let pending: Vec<(String, String)> = {
                let guard = OUTBOX.lock().unwrap();
                match guard.as_ref() { Some(c) => outbox_pending(c), None => Vec::new() }
            };
            for (id, line) in pending {
                let Some(body) = build_upload_body(&line, cfg.cc) else {
                    // Unparseable row: drop it so it doesn't wedge the queue.
                    if let Some(c) = OUTBOX.lock().unwrap().as_ref() { outbox_delete(c, &id); }
                    continue;
                };
                let url = format!("{}/v1/runs", cfg.server_url.trim_end_matches('/'));
                match client.post(&url)
                    .bearer_auth(&cfg.token)
                    .header("content-type", "application/json")
                    .body(body)
                    .send()
                {
                    Ok(resp) if resp.status().is_success() => {
                        if let Some(c) = OUTBOX.lock().unwrap().as_ref() { outbox_delete(c, &id); }
                    }
                    Ok(resp) => log::debug!("[sync] {id}: server {}", resp.status()),
                    Err(e) => log::debug!("[sync] {id}: {e}"),
                }
            }
        }
    });
}
```

- [ ] **Step 2: Verify it compiles + tests still pass**

Run (from `src-tauri/`): `cargo test sync::`
Expected: PASS (3 tests; compiles the runtime too).

- [ ] **Step 3: Commit**

```bash
git add src-tauri/src/sync.rs
git commit -m "feat(sync): config + enqueue + reqwest uploader + drain loop" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Wire `sync` into `lib.rs`

**Files:** Modify `src-tauri/src/lib.rs`.

- [ ] **Step 1: Add the module declaration** — at the top of `lib.rs`, below `mod discord;`:

```rust
mod sync;
```

- [ ] **Step 2: Route `run_finalized` from the stdout arm** — in `do_spawn_sidecar`, the `CommandEvent::Stdout(line)` arm currently is:

```rust
                        CommandEvent::Stdout(line) => {
                            let msg = String::from_utf8_lossy(&line);
                            let _ = handle.emit("tracker-event", msg.as_ref());
                        }
```
Change it to also hand the line to sync:
```rust
                        CommandEvent::Stdout(line) => {
                            let msg = String::from_utf8_lossy(&line);
                            let _ = handle.emit("tracker-event", msg.as_ref());
                            sync::on_line(msg.as_ref());
                        }
```

- [ ] **Step 3: Register the command** — add `sync::sync_set_config` to the `invoke_handler` list:

```rust
        .invoke_handler(tauri::generate_handler![start_tracker, stop_tracker, restart_tracker, send_to_tracker, open_url, discord::discord_set_presence, discord::discord_clear_presence, sync::sync_set_config])
```

- [ ] **Step 4: Initialise sync in `setup`** — in the `.setup(|app| { ... })` closure, after `app.manage(...)`:

```rust
            sync::init(app.handle().clone());
```

- [ ] **Step 5: Verify the Rust app compiles**

Run (from `src-tauri/`): `cargo build`
Expected: compiles successfully (warnings OK).

- [ ] **Step 6: Commit**

```bash
git add src-tauri/src/lib.rs
git commit -m "feat(sync): route run_finalized + register command + init" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Frontend settings store + config driver

**Files:** Create `src/lib/syncSettings.js`, `src/lib/sync.js`; modify `src/App.svelte`.

- [ ] **Step 1: Create `src/lib/syncSettings.js`** (mirrors `discordSettings.js`)

```js
import { writable } from "svelte/store";

// Sync settings, persisted in localStorage (decoupled from the Python config).
const URL_KEY = "sync_server_url";
const TOKEN_KEY = "sync_auth_token";
const CC_KEY = "sync_cc";

export const serverUrl = writable(localStorage.getItem(URL_KEY) || "");
export const authToken = writable(localStorage.getItem(TOKEN_KEY) || "");
export const cc = writable(Number(localStorage.getItem(CC_KEY)) || 150);

serverUrl.subscribe((v) => localStorage.setItem(URL_KEY, v || ""));
authToken.subscribe((v) => localStorage.setItem(TOKEN_KEY, v || ""));
cc.subscribe((v) => localStorage.setItem(CC_KEY, String(v || 150)));
```

- [ ] **Step 2: Create `src/lib/sync.js`** (mirrors `discord.js`)

```js
// Pushes the sync config to the Rust uploader whenever it changes. Reads only.
import { invoke } from "@tauri-apps/api/core";
import { get } from "svelte/store";
import { serverUrl, authToken, cc } from "./syncSettings.js";

function push() {
  invoke("sync_set_config", {
    server_url: get(serverUrl),
    token: get(authToken),
    cc: get(cc),
  }).catch(() => {});
}

export function initSync() {
  [serverUrl, authToken, cc].forEach((s) => s.subscribe(() => push()));
}
```

- [ ] **Step 3: Wire `initSync()` in `src/App.svelte`** — find the `onMount` where `initDiscordPresence()` is called and add `initSync()` next to it. First add the import near the other lib imports:

```js
  import { initSync } from "./lib/sync.js";
```
Then inside `onMount` (next to `initDiscordPresence()`):
```js
    initSync();
```

- [ ] **Step 4: Verify the frontend type-checks**

Run (repo root): `npm run check`
Expected: 0 errors (same as before — new files are plain JS).

- [ ] **Step 5: Commit**

```bash
git add src/lib/syncSettings.js src/lib/sync.js src/App.svelte
git commit -m "feat(sync): localStorage settings store + config driver" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: "Sync" settings tab

**Files:** Modify `src/components/SettingsModal.svelte`.

- [ ] **Step 1: Import the sync stores** — near the existing `discordSettings` import (~line 20):

```js
  import { serverUrl, authToken, cc } from "../lib/syncSettings.js";
```

- [ ] **Step 2: Add "sync" to the re-run steps list** — find the steps array used for the returning-user tab bar (the one that includes `"discord"`; the comment near line 8 references `RERUN_STEPS`). Add `"sync"` to it so the tab appears, e.g. `["language", "camera", "discord", "sync"]` (match the existing array's exact form).

- [ ] **Step 3: Add the tab panel** — after the `{:else if wizardStep === "discord"}` block's closing, add:

```svelte
        {:else if wizardStep === "sync"}
          <div class="wiz-pane">
            <h2>Sync</h2>
            <p>Upload your runs to the competition server so they appear on the leaderboard and broadcast. Get your token from whoever runs the server.</p>

            <div class="discord-fields">
              <label class="discord-label" for="sync-url">Server URL</label>
              <input id="sync-url" class="discord-input" type="text" bind:value={$serverUrl}
                placeholder="https://your-server.example" />
              <label class="discord-label" for="sync-token">Your token</label>
              <input id="sync-token" class="discord-input" type="password" bind:value={$authToken}
                placeholder="paste your token" />
              <label class="discord-label" for="sync-cc">Engine class (cc)</label>
              <input id="sync-cc" class="discord-input" type="number" bind:value={$cc} min="50" step="50" />
            </div>
            <p class="discord-note">Runs queue locally and upload when the server is reachable, so a flaky connection is fine. Leave the URL blank to disable uploading.</p>
          </div>
```
(Reuses the existing `discord-*` / `wiz-pane` styles. If the wrapper class differs from `wiz-pane`, match whatever the `discord` block uses.)

- [ ] **Step 4: Verify it type-checks**

Run (repo root): `npm run check`
Expected: 0 errors.

- [ ] **Step 5: Commit**

```bash
git add src/components/SettingsModal.svelte
git commit -m "feat(sync): Sync settings tab (server URL + token + cc)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: End-to-end smoke + final checks

**Files:** none (verification only); create `docs/` note optional.

- [ ] **Step 1: Rust + frontend gates**

Run (from `src-tauri/`): `cargo test` (all Rust tests incl. discord + sync pass) and `cargo build` (compiles).
Run (repo root): `npm run check` (0 errors) and `python -m pytest tests/test_run_finalized.py -q` (engine emit still green).

- [ ] **Step 2: Manual end-to-end smoke** (the real integration proof — run locally)

1. Start the server: from `pi/`, `npm run dev` (listens on `:8787`).
2. Seed a player + token: ensure the server DB has a player and mint a token. With a practice DB: `MKW_DB=path npm run mint-token Paul` (or point the server at an imported DB). Note the token.
3. Launch the app (`npm run tauri dev`), open Settings → **Sync**, set Server URL `http://127.0.0.1:8787`, paste the token, cc `150`.
4. Produce a run: either play a time trial, or run the engine against a recorded video so it reaches `_finalize_recording` and emits `run_finalized`.
5. Verify the run landed: query the server DB — `SELECT attempt_id, status, total_time_ms, provenance FROM runs WHERE provenance='live'` shows the attempt; or watch a `WS /v1/events` subscriber print `run_finished`/`pb_achieved`.
6. Offline check: stop the server, do another run (it queues), restart the server, confirm the queued run uploads within a few seconds.

Record the outcome in the PR/commit description.

- [ ] **Step 3: Commit (if any verification notes/docs were added; otherwise skip)**

---

## Notes for the executor

- **Rust commands run from `src-tauri/`**; JS from the repo root. The first `cargo` build with the new crates is slow (compiles reqwest + rusqlite-bundled).
- **Don't hold the `OUTBOX` mutex during the blocking POSTs** — the drain loop (a dedicated std thread using `reqwest::blocking`) snapshots pending rows, releases the lock, then POSTs. Keep it that way.
- **Idempotency:** the outbox key + the server's `attempt_id` upsert mean re-sends never duplicate, so the simple "delete on 2xx, retry otherwise" loop is safe.
- The engine emits `run_finalized` for **every** attempt (incl. resets); they all flow to the server as `provenance='live'`.
- After this lands, **sub-project B is functionally complete** (engine → app → server). Remaining for the wider effort: Phase 2 (retire the engine's local race store + repoint monitor reads), sub-project C (overlays + website), the server-side WR scraper, Pi deployment, and the authoritative cutover import. Also deferred (not on the durable-upload path): the optional **`run_started` ping** — `sync.rs` watching the engine's `screen_change`→RACING line and POSTing `/v1/runs/start` for a "started a run" event.
```
