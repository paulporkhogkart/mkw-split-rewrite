# WR Service Plan 3 — Tray + Background Modes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the merged WR-service core to a settings-driven background capability: tray-only
autostart, on-demand window, an any-screen-change idle gate whose predicate doubles as the job
cancel, a polling runner with backoff, and the three hardening items (heartbeat, bounded yt-dlp,
Pi sweep alert).

**Architecture:** Spec `docs/superpowers/specs/2026-07-17-wr-service-tray-background-design.md`
(read it first; its parent is `2026-07-15-pbenguin-wr-service-design.md` §6). The main window
becomes programmatic (created on normal launch / on demand; never on `--tray-start`); the engine
lifecycle is Rust-owned; the WR loop is one OS thread behind a pure gate predicate fed by
atomics the existing engine-stdout forwarder maintains. New app-level `tray.rs`; new `wr/gate.rs`,
`wr/runner.rs`, `wr/phase.rs`. Pi side gains a sweep-based `wr_job_dead` alert with an
`alerted_at` dedup column.

**Tech Stack:** Rust (Tauri v2 + new plugins `single-instance`, `autostart`; tray-icon feature),
Svelte 4 frontend, Node/TS via tsx + vitest on the Pi.

## Global Constraints

- **Branch `wr-tray` off `main`, in the MAIN checkout — no worktree** (the gitignored fixture
  video + `mkw_tracker.db` live only here).
- **NEVER modify `mkw_tracker/`** (the Python engine). No `[dev-dependencies]` in
  `src-tauri/Cargo.toml` (regular `[dependencies]` additions named by Task 6 are fine).
- **Defaults change nothing:** all four settings default off; with all off there is NO tray icon
  and closing quits — a user who ignores the feature notices zero difference.
- **The camera must be untouchable by a hidden start**: no code path may spawn the live engine
  without a visible window having invoked `start_tracker`, except when `keep_tracking_in_tray`
  kept an already-running engine alive through a close-to-tray.
- Settings keys, exact strings: `close_to_tray`, `start_at_login`, `run_wr_service`,
  `keep_tracking_in_tray` — stored as `"0"`/`"1"` in `wr_service.db`'s `wr_local` KV.
- Idle gate: `WR_IDLE_MINUTES = 10`; open = `!tracking_running || now-last_screen_change ≥ 10min`;
  the negated predicate IS the cancel closure. Only `screen_change` events reset the clock.
- Backoff (seconds): Completed→0; Idle 204→60 doubling to 300 cap; Error→120; Failed→30;
  Released/gate-closed→30.
- Heartbeat every 120 s while a job is in flight; `Ok(false)` = lease lost → cancel;
  `Err` = network blip → keep going. yt-dlp cap `DOWNLOAD_CAP = 240 s`, cancel-aware.
- Rust suite baseline 100 passed + 1 ignored; pi baseline 605 — both only grow. `cargo build`
  and `cargo build --release` stay warning-free. Pi stays `npx tsc --noEmit`-clean.
- Every failing test must be RUN and observed failing for the RIGHT reason before its fix.
- Never run the ~77s `--ignored` fixture test except where a task explicitly says to (none do).
- Stage only named files (`git add <paths>`, never `-A`). Commit trailer on its own line:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
- Window/tray lifecycle is NOT unit-testable — those tasks gate on compile + zero warnings +
  the manual smoke checklist (spec §7), run by Paul after merge. Do not fake lifecycle tests.

## File Structure

| File | Change |
|---|---|
| `src-tauri/src/wr/state.rs` | + typed settings accessors over `wr_local` |
| `src-tauri/src/wr/mod.rs` | + `wr_get_settings` / `wr_set_setting` commands + `settings_db` helper |
| `src-tauri/src/wr/gate.rs` **(create)** | `TrackerActivity` atomics + pure `gate_open` + `ACTIVITY` static |
| `src-tauri/src/wr/phase.rs` **(create)** | in-flight phase (Downloading/Processing + course) for the tooltip |
| `src-tauri/src/wr/runner.rs` **(create)** | the loop thread: gate → `process_one` → backoff; pause/shutdown |
| `src-tauri/src/wr/service.rs` | heartbeat scope around the engine step; phase marks; cancel-aware download call |
| `src-tauri/src/wr/ytdlp.rs` | `run_download` spawn+watchdog (bounded, cancel-aware) |
| `src-tauri/src/sync.rs` | + `pub fn config_snapshot()` |
| `src-tauri/src/tray.rs` **(create)** | conditional tray, menu, tooltip/icon refresh |
| `src-tauri/src/lib.rs` | programmatic window, plugins, close-to-tray, activity hooks, runner state |
| `src-tauri/tauri.conf.json` | empty `app.windows` |
| `src-tauri/Cargo.toml` | tray-icon/image-png features; single-instance + autostart plugins |
| `src-tauri/icons/tray-16-idle.png` + `tray-16-active.png` **(create)** | hand-made 16×16 grey/red |
| `src/components/SettingsModal.svelte` | Background section |
| `pi/src/db/connect.ts` | `wr_jobs.alerted_at` ALTER |
| `pi/src/db/wrJobs.ts` (+test) | `markJobAlerted`, `sweepDeadJobAlerts` |
| `pi/src/api/wrJobs.ts` (+test) | route publish also marks alerted |
| `pi/src/wr/reconcile.ts` (+test) | revival clears `alerted_at` |
| `pi/src/wr/scheduler.ts` (+test) | sweep on tick |
| Parent spec + root `CLAUDE.md` | Task 10 docs sync |

---

### Task 1: Settings storage + the two Tauri commands

**Files:**
- Modify: `src-tauri/src/wr/state.rs`
- Modify: `src-tauri/src/wr/mod.rs`
- Modify: `src-tauri/src/lib.rs` (invoke_handler list only)

**Interfaces:**
- Produces: `state::get_flag(conn, key) -> bool`, `state::set_flag(conn, key, value)`,
  key consts `state::{SETTING_CLOSE_TO_TRAY, SETTING_START_AT_LOGIN, SETTING_RUN_WR_SERVICE, SETTING_KEEP_TRACKING_IN_TRAY}`;
  `wr::settings_db(app) -> Result<(rusqlite::Connection), String>` (opens `app_data_dir()/wr`);
  `wr::WrSettings { close_to_tray, start_at_login, run_wr_service, keep_tracking_in_tray: bool }`
  (Serialize, camelCase); commands `wr_get_settings(app) -> Result<WrSettings, String>` and
  `wr_set_setting(app, key: String, value: bool) -> Result<(), String>`.
  Tasks 3/6/7 EXTEND `wr_set_setting` with side-effects; Task 8's frontend calls both commands
  with snake_case `key` strings.

- [ ] **Step 1: Write the failing tests**

Append to `src-tauri/src/wr/state.rs`'s `mod tests`:

```rust
    #[test]
    fn flags_default_false_and_roundtrip() {
        let d = tmpdir("flags");
        let c = open(&d).unwrap();
        assert!(!get_flag(&c, SETTING_CLOSE_TO_TRAY), "unset flag must read false");
        set_flag(&c, SETTING_CLOSE_TO_TRAY, true);
        assert!(get_flag(&c, SETTING_CLOSE_TO_TRAY));
        set_flag(&c, SETTING_CLOSE_TO_TRAY, false);
        assert!(!get_flag(&c, SETTING_CLOSE_TO_TRAY));
    }

    #[test]
    fn flags_are_independent_keys() {
        let d = tmpdir("flags_indep");
        let c = open(&d).unwrap();
        set_flag(&c, SETTING_RUN_WR_SERVICE, true);
        assert!(get_flag(&c, SETTING_RUN_WR_SERVICE));
        assert!(!get_flag(&c, SETTING_KEEP_TRACKING_IN_TRAY),
            "setting one flag must not bleed into another");
    }

    #[test]
    fn flags_survive_reopen() {
        let d = tmpdir("flags_reopen");
        { let c = open(&d).unwrap(); set_flag(&c, SETTING_START_AT_LOGIN, true); }
        let c2 = open(&d).unwrap();
        assert!(get_flag(&c2, SETTING_START_AT_LOGIN), "settings must persist across restarts");
    }
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd src-tauri && cargo test wr::state`
Expected: FAIL — `cannot find function get_flag` (compile error is acceptable here: the fns are new).

- [ ] **Step 3: Implement the accessors**

Add to `src-tauri/src/wr/state.rs` above the tests (below `set_inflight`):

```rust
/// Background-mode settings (spec 2026-07-17 §3). Exact key strings — the frontend sends
/// these verbatim through wr_set_setting.
pub const SETTING_CLOSE_TO_TRAY: &str = "close_to_tray";
pub const SETTING_START_AT_LOGIN: &str = "start_at_login";
pub const SETTING_RUN_WR_SERVICE: &str = "run_wr_service";
pub const SETTING_KEEP_TRACKING_IN_TRAY: &str = "keep_tracking_in_tray";

/// Read a boolean setting; unset = false (every setting defaults to today's behaviour).
pub fn get_flag(conn: &Connection, key: &str) -> bool {
    get(conn, key).as_deref() == Some("1")
}

pub fn set_flag(conn: &Connection, key: &str, value: bool) {
    put(conn, key, Some(if value { "1" } else { "0" }));
}
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd src-tauri && cargo test wr::state`
Expected: PASS (12 tests).

- [ ] **Step 5: Add the commands**

Append to `src-tauri/src/wr/mod.rs`:

```rust
/// The four background-mode settings, camelCase for the webview (Tauri convention).
#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct WrSettings {
    pub close_to_tray: bool,
    pub start_at_login: bool,
    pub run_wr_service: bool,
    pub keep_tracking_in_tray: bool,
}

/// The WR service's data dir (worker id, scratch DB, yt-dlp, settings) — the same
/// `app_data_dir()/wr` wr_process_one uses.
pub fn wr_data_dir(app: &tauri::AppHandle) -> Result<std::path::PathBuf, String> {
    use tauri::Manager;
    Ok(app.path().app_data_dir().map_err(|e| e.to_string())?.join("wr"))
}

/// Pub: lib.rs's close-to-tray handler and tray.rs read flags through this too.
pub fn settings_db(app: &tauri::AppHandle) -> Result<rusqlite::Connection, String> {
    state::open(&wr_data_dir(app)?)
}

#[tauri::command]
pub fn wr_get_settings(app: tauri::AppHandle) -> Result<WrSettings, String> {
    let c = settings_db(&app)?;
    Ok(WrSettings {
        close_to_tray: state::get_flag(&c, state::SETTING_CLOSE_TO_TRAY),
        start_at_login: state::get_flag(&c, state::SETTING_START_AT_LOGIN),
        run_wr_service: state::get_flag(&c, state::SETTING_RUN_WR_SERVICE),
        keep_tracking_in_tray: state::get_flag(&c, state::SETTING_KEEP_TRACKING_IN_TRAY),
    })
}

/// Persist one setting. `key` is the snake_case store key (state::SETTING_*). Rejecting
/// unknown keys keeps a frontend typo from silently minting a dead setting.
/// Later tasks bolt side-effects on here (autostart registration, runner start/stop,
/// tray existence) — the persist-then-apply order is deliberate so a crash mid-apply
/// still leaves the stored intent correct for the next boot.
#[tauri::command]
pub fn wr_set_setting(app: tauri::AppHandle, key: String, value: bool) -> Result<(), String> {
    const KNOWN: [&str; 4] = [
        state::SETTING_CLOSE_TO_TRAY, state::SETTING_START_AT_LOGIN,
        state::SETTING_RUN_WR_SERVICE, state::SETTING_KEEP_TRACKING_IN_TRAY,
    ];
    if !KNOWN.contains(&key.as_str()) {
        return Err(format!("unknown setting: {key}"));
    }
    let c = settings_db(&app)?;
    state::set_flag(&c, &key, value);
    Ok(())
}
```

In `src-tauri/src/lib.rs`, extend the `invoke_handler` list (one line, after `wr::wr_process_one`):
`, wr::wr_get_settings, wr::wr_set_setting`

- [ ] **Step 6: Full suite + build gate**

Run: `cd src-tauri && cargo test && cargo build`
Expected: 103 passed + 1 ignored, zero warnings.

- [ ] **Step 7: Commit**

```bash
git add src-tauri/src/wr/state.rs src-tauri/src/wr/mod.rs src-tauri/src/lib.rs
git commit -m "feat(wr): background-mode settings store + get/set commands"
```

---

### Task 2: The idle gate — activity atomics + pure predicate

**Files:**
- Create: `src-tauri/src/wr/gate.rs`
- Modify: `src-tauri/src/wr/mod.rs` (`pub mod gate;`)
- Modify: `src-tauri/src/lib.rs` (three hook sites)

**Interfaces:**
- Produces: `gate::TrackerActivity` with `note_screen_change()`, `set_tracking(bool)`,
  `tracking_running() -> bool`, `last_change_epoch_ms() -> i64`; `gate::ACTIVITY: TrackerActivity`
  (process-wide static); pure `gate::gate_open(tracking_running: bool, last_change_epoch_ms: i64, now_epoch_ms: i64) -> bool`
  and `gate::now_epoch_ms() -> i64`; const `gate::WR_IDLE_MS: i64`. Task 3's runner calls
  `gate_open(ACTIVITY.tracking_running(), ACTIVITY.last_change_epoch_ms(), now_epoch_ms())`.

- [ ] **Step 1: Write the failing tests**

Create `src-tauri/src/wr/gate.rs`:

```rust
//! The WR idle gate (spec 2026-07-17 §4). WR work may only run while live tracking is
//! stopped, or has shown no screen_change for WR_IDLE_MS. The SAME predicate, negated,
//! is the in-flight job's cancel: any screen change closes the gate AND cancels.

use std::sync::atomic::{AtomicBool, AtomicI64, Ordering};

#[cfg(test)]
mod tests {
    use super::*;

    const T0: i64 = 1_700_000_000_000;
    const MIN: i64 = 60_000;

    #[test]
    fn gate_truth_table() {
        // Tracking stopped: always open, staleness irrelevant.
        assert!(gate_open(false, T0, T0));
        assert!(gate_open(false, T0, T0 + 1));
        // Tracking running, fresh activity: closed.
        assert!(!gate_open(true, T0, T0));
        assert!(!gate_open(true, T0, T0 + 9 * MIN));
        // Running, exactly at the threshold: open (>=, not >).
        assert!(gate_open(true, T0, T0 + WR_IDLE_MS));
        assert!(gate_open(true, T0, T0 + WR_IDLE_MS + 1));
    }

    #[test]
    fn activity_updates_move_the_clock() {
        let a = TrackerActivity::new();
        assert!(!a.tracking_running());
        a.set_tracking(true);
        assert!(a.tracking_running());
        let before = a.last_change_epoch_ms();
        assert!(before > 0, "set_tracking(true) must count as activity, or the gate \
                             would open the instant tracking starts");
        a.note_screen_change();
        assert!(a.last_change_epoch_ms() >= before);
        a.set_tracking(false);
        assert!(!a.tracking_running());
    }

    #[test]
    fn fresh_activity_state_is_open_gate() {
        // Boot state: never tracked, never saw a screen — the gate must be open.
        let a = TrackerActivity::new();
        assert!(gate_open(a.tracking_running(), a.last_change_epoch_ms(), now_epoch_ms()));
    }
}
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd src-tauri && cargo test wr::gate`
Expected: compile FAIL — `cannot find type TrackerActivity` (add `pub mod gate;` to
`src-tauri/src/wr/mod.rs` in this same step so the failure is the missing type, not a missing module).

- [ ] **Step 3: Implement**

Add above the tests in `src-tauri/src/wr/gate.rs`:

```rust
/// 10 minutes (spec §6.2 WR_IDLE_MINUTES).
pub const WR_IDLE_MS: i64 = 10 * 60 * 1000;

/// Live-tracker activity signals, maintained by lib.rs's engine-stdout forwarder and the
/// sidecar spawn/kill paths. Plain atomics: written from the forwarder's async task and
/// read from the runner thread.
pub struct TrackerActivity {
    tracking: AtomicBool,
    last_change_ms: AtomicI64,
}

impl TrackerActivity {
    pub const fn new() -> Self {
        Self { tracking: AtomicBool::new(false), last_change_ms: AtomicI64::new(0) }
    }

    /// Any screen_change event — the ONLY thing that resets the idle clock (decided
    /// 2026-07-17: navigating menus counts as activity; the engine's 0.2s heartbeats
    /// and other chatter do not).
    pub fn note_screen_change(&self) {
        self.last_change_ms.store(now_epoch_ms(), Ordering::Relaxed);
    }

    /// Turning tracking ON also counts as activity, so the gate shuts the moment the
    /// live engine starts rather than 10 minutes later.
    pub fn set_tracking(&self, on: bool) {
        if on { self.note_screen_change(); }
        self.tracking.store(on, Ordering::Relaxed);
    }

    pub fn tracking_running(&self) -> bool { self.tracking.load(Ordering::Relaxed) }
    pub fn last_change_epoch_ms(&self) -> i64 { self.last_change_ms.load(Ordering::Relaxed) }
}

/// Process-wide instance. A static (not app-managed state) because the wr runner thread
/// and lib.rs's forwarder both need it without threading an AppHandle through pure code.
pub static ACTIVITY: TrackerActivity = TrackerActivity::new();

pub fn now_epoch_ms() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0)
}

/// The gate predicate (pure — the runner and the cancel closure share it).
/// Open = tracking stopped, or no screen change for WR_IDLE_MS.
pub fn gate_open(tracking_running: bool, last_change_epoch_ms: i64, now_epoch_ms: i64) -> bool {
    !tracking_running || (now_epoch_ms - last_change_epoch_ms) >= WR_IDLE_MS
}
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd src-tauri && cargo test wr::gate`
Expected: PASS (3 tests).

- [ ] **Step 5: Hook the signals in lib.rs**

Three edits in `src-tauri/src/lib.rs`:

(a) In `do_spawn_sidecar`, in the `Ok((mut rx, child)) => {` arm, directly after
`*state.0.lock().unwrap() = Some(child);` add:

```rust
            wr::gate::ACTIVITY.set_tracking(true);
```

(b) In the same function's event loop, in the `CommandEvent::Stdout(line)` arm, directly after
`let msg = String::from_utf8_lossy(&line);` add:

```rust
                            // Idle-gate signal: only screen_change resets the WR idle clock
                            // (cheap substring check — the full JSON parse isn't needed here).
                            if msg.contains("\"type\":\"screen_change\"")
                                || msg.contains("\"type\": \"screen_change\"") {
                                wr::gate::ACTIVITY.note_screen_change();
                            }
```

and in the `CommandEvent::Terminated(status)` arm, first line:

```rust
                            wr::gate::ACTIVITY.set_tracking(false);
```

(c) In `stop_tracker` and `restart_tracker`, directly after each `let _ = child.kill();` add:

```rust
            wr::gate::ACTIVITY.set_tracking(false);
```

(In `restart_tracker` the subsequent `do_spawn_sidecar` sets it true again.)

- [ ] **Step 6: Full suite + build gate**

Run: `cd src-tauri && cargo test && cargo build`
Expected: 106 passed + 1 ignored, zero warnings. (`ACTIVITY` and the hooks are exercised for
real by Task 3's runner; here the compile + the pure tests are the gate.)

- [ ] **Step 7: Commit**

```bash
git add src-tauri/src/wr/gate.rs src-tauri/src/wr/mod.rs src-tauri/src/lib.rs
git commit -m "feat(wr): idle-gate predicate + tracker-activity signals"
```

---

### Task 3: Phase reporting + the runner loop

**Files:**
- Create: `src-tauri/src/wr/phase.rs`
- Create: `src-tauri/src/wr/runner.rs`
- Modify: `src-tauri/src/wr/mod.rs` (module decls; extend `wr_set_setting`)
- Modify: `src-tauri/src/wr/service.rs` (phase marks; `Outcome` allow(dead_code) removal)
- Modify: `src-tauri/src/sync.rs` (+ `config_snapshot`)
- Modify: `src-tauri/src/lib.rs` (RunnerState managed; start at setup when enabled)

**Interfaces:**
- Consumes: Task 1's flags + `wr_data_dir`; Task 2's `gate::{ACTIVITY, gate_open, now_epoch_ms}`.
- Produces: `phase::Phase { pub kind: PhaseKind, pub course_slug: String }`,
  `phase::PhaseKind::{Downloading, Processing}`, `phase::set(Option<Phase>)`, `phase::get() -> Option<Phase>`;
  `runner::Runner` with `Runner::start(app: tauri::AppHandle) -> Runner`, `runner.set_paused(bool)`,
  `runner.is_paused() -> bool`, `runner.stop(self)`; `runner::next_backoff(outcome: &service::Outcome, prev_idle: std::time::Duration) -> std::time::Duration`;
  `sync::config_snapshot() -> (String, String)` (server_url, token — clones of sync.rs's CONFIG);
  lib.rs `pub struct RunnerState(pub Mutex<Option<wr::runner::Runner>>)` managed on the app.
  Task 7 reads `phase::get()` + `runner.is_paused()` for the tooltip and calls
  `tray::refresh_tray_status` FROM the runner via a callback set at start (see Step 5's
  `set_refresh_hook`).

- [ ] **Step 1: Write the failing phase tests**

Create `src-tauri/src/wr/phase.rs`:

```rust
//! The in-flight phase, for the tray tooltip. service.rs writes it at the download and
//! engine boundaries; the tray reads it. A tiny global rather than a channel because the
//! producer (run_job) has no handle to the tray and must not depend on it.

use std::sync::Mutex;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn phase_roundtrips_and_clears() {
        set(Some(Phase { kind: PhaseKind::Downloading, course_slug: "dk_spaceport".into() }));
        let p = get().expect("phase must be readable while set");
        assert!(matches!(p.kind, PhaseKind::Downloading));
        assert_eq!(p.course_slug, "dk_spaceport");
        set(Some(Phase { kind: PhaseKind::Processing, course_slug: "dk_spaceport".into() }));
        assert!(matches!(get().unwrap().kind, PhaseKind::Processing));
        set(None);
        assert!(get().is_none(), "a finished job must clear the phase");
    }
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd src-tauri && cargo test wr::phase`
Expected: compile FAIL — missing types (add `pub mod phase;` to `wr/mod.rs` in this step).

- [ ] **Step 3: Implement phase**

Add above the tests in `src-tauri/src/wr/phase.rs`:

```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PhaseKind { Downloading, Processing }

#[derive(Debug, Clone)]
pub struct Phase {
    pub kind: PhaseKind,
    pub course_slug: String,
}

static PHASE: Mutex<Option<Phase>> = Mutex::new(None);

pub fn set(p: Option<Phase>) {
    *PHASE.lock().unwrap_or_else(|e| e.into_inner()) = p;
}

pub fn get() -> Option<Phase> {
    PHASE.lock().unwrap_or_else(|e| e.into_inner()).clone()
}
```

Run: `cargo test wr::phase` → PASS (1 test).

- [ ] **Step 4: Mark phases in service.rs**

In `src-tauri/src/wr/service.rs`:
- In `run_job`, directly before the `ytdlp::download(...)` call add:

```rust
    super::phase::set(Some(super::phase::Phase {
        kind: super::phase::PhaseKind::Downloading, course_slug: j.course_slug.clone() }));
```

- Directly before the `engine::run_video(` call add:

```rust
    super::phase::set(Some(super::phase::Phase {
        kind: super::phase::PhaseKind::Processing, course_slug: j.course_slug.clone() }));
```

- In `process_one`, directly after `let outcome = run_job(cfg, &client, &j, cancel);` add:

```rust
    super::phase::set(None);
```

- [ ] **Step 5: Write the failing backoff test, then the runner**

Create `src-tauri/src/wr/runner.rs`:

```rust
//! The WR service loop (spec 2026-07-17 §4): one OS thread — gate check ->
//! process_one -> outcome-driven backoff. Blocking reqwest on a plain thread is the
//! house pattern (sync.rs does the same); no tokio.

use super::service::{self, Outcome, ServiceCfg};
use super::{gate, WrError};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn backoff_schedule_per_outcome() {
        let base = Duration::from_secs(60);
        // Completed: straight back to work — there may be a queue to drain.
        assert_eq!(next_backoff(&Outcome::Completed(1), base), Duration::ZERO);
        // Idle 204: doubling from 60s, capped at 300s.
        assert_eq!(next_backoff(&Outcome::Idle, Duration::ZERO), Duration::from_secs(60));
        assert_eq!(next_backoff(&Outcome::Idle, base), Duration::from_secs(120));
        assert_eq!(next_backoff(&Outcome::Idle, Duration::from_secs(240)), Duration::from_secs(300));
        assert_eq!(next_backoff(&Outcome::Idle, Duration::from_secs(300)), Duration::from_secs(300));
        // Errors (our side / network): 2 min.
        assert_eq!(next_backoff(&Outcome::Error("x".into()), base), Duration::from_secs(120));
        // A reported failure burned an attempt server-side; modest pause.
        assert_eq!(next_backoff(&Outcome::Failed(1, WrError::NoTrail), base), Duration::from_secs(30));
        // Released (cancel/pause/gate): quick re-check — the gate logic decides the rest.
        assert_eq!(next_backoff(&Outcome::Released(1), base), Duration::from_secs(30));
    }
}
```

Run: `cd src-tauri && cargo test wr::runner`
Expected: compile FAIL — `next_backoff` missing (add `pub mod runner;` to `wr/mod.rs` now).

- [ ] **Step 6: Implement the runner**

Add above the tests in `src-tauri/src/wr/runner.rs`:

```rust
/// Next sleep before another claim attempt, from what just happened. `prev_idle` is the
/// previous Idle backoff (doubling), ignored for the other arms.
pub fn next_backoff(outcome: &Outcome, prev_idle: Duration) -> Duration {
    match outcome {
        Outcome::Completed(_) => Duration::ZERO,
        Outcome::Idle => {
            let next = if prev_idle.is_zero() { 60 } else { prev_idle.as_secs().saturating_mul(2) };
            Duration::from_secs(next.clamp(60, 300))
        }
        Outcome::Error(_) => Duration::from_secs(120),
        Outcome::Failed(_, _) => Duration::from_secs(30),
        Outcome::Released(_) => Duration::from_secs(30),
    }
}

/// Handle to the loop thread. Dropping without stop() leaves the thread running until
/// app exit; stop() is the orderly path (quit, or the run_wr_service toggle going off).
pub struct Runner {
    shutdown: Arc<AtomicBool>,
    paused: Arc<AtomicBool>,
    handle: Option<std::thread::JoinHandle<()>>,
    refresh: Arc<Mutex<Option<Box<dyn Fn() + Send>>>>,
}

impl Runner {
    /// Spawn the loop. It claims nothing until the gate is open AND sync's CONFIG has a
    /// server + token (reads the ordinary player token — no second credential store).
    pub fn start(app: tauri::AppHandle) -> Runner {
        let shutdown = Arc::new(AtomicBool::new(false));
        let paused = Arc::new(AtomicBool::new(false));
        let refresh: Arc<Mutex<Option<Box<dyn Fn() + Send>>>> = Arc::new(Mutex::new(None));
        let (sd, pd, rf) = (shutdown.clone(), paused.clone(), refresh.clone());

        let handle = std::thread::Builder::new()
            .name("wr-runner".into())
            .spawn(move || run_loop(app, sd, pd, rf))
            .expect("spawn wr-runner thread");

        Runner { shutdown, paused, handle: Some(handle), refresh }
    }

    /// Task 7 installs the tray-refresh hook here (runner -> tray, decoupled).
    pub fn set_refresh_hook(&self, hook: Box<dyn Fn() + Send>) {
        *self.refresh.lock().unwrap_or_else(|e| e.into_inner()) = Some(hook);
    }

    pub fn set_paused(&self, paused: bool) {
        self.paused.store(paused, Ordering::Relaxed);
    }
    pub fn is_paused(&self) -> bool { self.paused.load(Ordering::Relaxed) }

    /// Orderly shutdown: flags the loop (whose cancel closure aborts any in-flight job,
    /// releasing the lease) and joins. The engine watchdog polls at 250ms and the
    /// download watchdog likewise, so the join resolves within a few seconds worst-case.
    pub fn stop(mut self) {
        self.shutdown.store(true, Ordering::Relaxed);
        if let Some(h) = self.handle.take() { let _ = h.join(); }
    }
}

fn ping_refresh(refresh: &Arc<Mutex<Option<Box<dyn Fn() + Send>>>>) {
    if let Some(f) = refresh.lock().unwrap_or_else(|e| e.into_inner()).as_ref() { f(); }
}

/// Sleep `d` in 1s slices so shutdown/pause stay responsive.
fn interruptible_sleep(d: Duration, shutdown: &AtomicBool) {
    let mut left = d;
    while !left.is_zero() && !shutdown.load(Ordering::Relaxed) {
        let step = left.min(Duration::from_secs(1));
        std::thread::sleep(step);
        left = left.saturating_sub(step);
    }
}

fn run_loop(
    app: tauri::AppHandle,
    shutdown: Arc<AtomicBool>,
    paused: Arc<AtomicBool>,
    refresh: Arc<Mutex<Option<Box<dyn Fn() + Send>>>>,
) {
    let mut idle_backoff = Duration::ZERO;
    while !shutdown.load(Ordering::Relaxed) {
        let gate_now = gate::gate_open(
            gate::ACTIVITY.tracking_running(),
            gate::ACTIVITY.last_change_epoch_ms(),
            gate::now_epoch_ms(),
        );
        if paused.load(Ordering::Relaxed) || !gate_now {
            ping_refresh(&refresh);
            interruptible_sleep(Duration::from_secs(30), &shutdown);
            continue;
        }
        let (server_url, token) = crate::sync::config_snapshot();
        if server_url.trim().is_empty() || token.trim().is_empty() {
            interruptible_sleep(Duration::from_secs(60), &shutdown);
            continue;
        }
        let Ok(data_dir) = super::wr_data_dir(&app) else {
            interruptible_sleep(Duration::from_secs(120), &shutdown);
            continue;
        };
        let cfg = ServiceCfg {
            server_url, token, data_dir,
            engine: super::engine::EnginePath::resolve(),
        };
        // The cancel closure IS the negated gate (spec: one consistent rule), plus
        // pause and shutdown. Any screen change mid-job aborts -> release -> refund.
        let sd2 = shutdown.clone();
        let pd2 = paused.clone();
        let cancel = move || {
            sd2.load(Ordering::Relaxed)
                || pd2.load(Ordering::Relaxed)
                || !gate::gate_open(
                    gate::ACTIVITY.tracking_running(),
                    gate::ACTIVITY.last_change_epoch_ms(),
                    gate::now_epoch_ms(),
                )
        };
        ping_refresh(&refresh);
        // Contain a panicking job: log, long backoff, keep serving (the PROCESS_LOCK
        // already recovers from poisoning).
        let outcome = match std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            service::process_one(&cfg, &cancel)
        })) {
            Ok(o) => o,
            Err(_) => {
                log::error!("[wr] process_one panicked; backing off 5 min");
                ping_refresh(&refresh);
                interruptible_sleep(Duration::from_secs(300), &shutdown);
                continue;
            }
        };
        log::info!("[wr] runner outcome: {outcome:?}");
        idle_backoff = next_backoff(&outcome, idle_backoff);
        if !matches!(outcome, Outcome::Idle) && !matches!(outcome, Outcome::Completed(_)) {
            // Non-idle outcomes use their fixed backoff; reset the idle ladder.
            let pause = idle_backoff;
            idle_backoff = Duration::ZERO;
            ping_refresh(&refresh);
            interruptible_sleep(pause, &shutdown);
            continue;
        }
        ping_refresh(&refresh);
        interruptible_sleep(idle_backoff, &shutdown);
        if matches!(outcome, Outcome::Completed(_)) { idle_backoff = Duration::ZERO; }
    }
    ping_refresh(&refresh);
}
```

Run: `cd src-tauri && cargo test wr::runner`
Expected: PASS (1 test).

Also in `src-tauri/src/wr/service.rs`: delete the `#[allow(dead_code)]` line above
`pub enum Outcome` and its comment block (lines 18-24) — `next_backoff` now matches the
variants for real, which is exactly what that comment said would end the allow.

- [ ] **Step 7: sync accessor + lib.rs wiring**

In `src-tauri/src/sync.rs`, directly below `pub fn sync_set_config` add:

```rust
/// Snapshot of (server_url, token) for the WR runner. Same CONFIG the uploader uses —
/// the WR service deliberately has no second credential store (spec §4).
pub fn config_snapshot() -> (String, String) {
    let c = CONFIG.lock().unwrap();
    (c.server_url.clone(), c.token.clone())
}
```

In `src-tauri/src/lib.rs`:
- Below `struct SidecarState(...)` add:

```rust
/// The WR service loop, when the run_wr_service setting is on. None otherwise.
pub struct RunnerState(pub Mutex<Option<wr::runner::Runner>>);
```

- In `setup`, after `app.manage(SidecarState(Mutex::new(None)));`:

```rust
            app.manage(RunnerState(Mutex::new(None)));
            if let Ok(c) = wr::settings_db(app.handle()) {
                if wr::state::get_flag(&c, wr::state::SETTING_RUN_WR_SERVICE) {
                    let runner = wr::runner::Runner::start(app.handle().clone());
                    *app.state::<RunnerState>().0.lock().unwrap() = Some(runner);
                }
            }
```

- In the `RunEvent::Exit` handler, before the sidecar kill:

```rust
                if let Some(rs) = app_handle.try_state::<RunnerState>() {
                    if let Some(runner) = rs.0.lock().unwrap().take() { runner.stop(); }
                }
```

- In `src-tauri/src/wr/mod.rs`, extend `wr_set_setting`: after `state::set_flag(&c, &key, value);` add:

```rust
    if key == state::SETTING_RUN_WR_SERVICE {
        use tauri::Manager;
        let rs = app.state::<crate::RunnerState>();
        let mut guard = rs.0.lock().unwrap_or_else(|e| e.into_inner());
        match (value, guard.is_some()) {
            (true, false) => *guard = Some(runner::Runner::start(app.clone())),
            (false, true) => { if let Some(r) = guard.take() { r.stop(); } }
            _ => {}
        }
    }
```

(`state.rs`'s `get_flag` import path: `wr::state` is already `pub mod` — lib.rs uses full paths
as shown.)

- [ ] **Step 8: Full suite + build gate + commit**

Run: `cd src-tauri && cargo test && cargo build`
Expected: 108 passed + 1 ignored, zero warnings (the Outcome allow(dead_code) removal must not
surface new warnings — next_backoff reads every variant).

```bash
git add src-tauri/src/wr/phase.rs src-tauri/src/wr/runner.rs src-tauri/src/wr/mod.rs src-tauri/src/wr/service.rs src-tauri/src/sync.rs src-tauri/src/lib.rs
git commit -m "feat(wr): runner loop with gate-as-cancel, backoff, and phase reporting"
```

---

### Task 4: Heartbeat around the engine step

**Files:**
- Modify: `src-tauri/src/wr/service.rs` (run_job engine step; new pure fn + tests)
- Modify: `src-tauri/src/wr/job.rs` (heartbeat doc + remove its `#[allow(dead_code)]`)

**Interfaces:**
- Consumes: `job::Client::heartbeat(wr_id) -> Result<bool, String>` (exists, currently unused).
- Produces: `service::should_stop_after_heartbeat(&Result<bool, String>) -> bool` (pure).

- [ ] **Step 1: Write the failing test**

Append to `src-tauri/src/wr/service.rs` `mod tests`:

```rust
    #[test]
    fn heartbeat_verdicts_only_a_confirmed_loss_stops_work() {
        // Ok(false) = the server CONFIRMED we no longer own the lease: stop, the job is
        // someone else's now. An Err is a network blip — the lease is probably still
        // ours, and stopping on flaky wifi would abandon healthy jobs.
        assert!(should_stop_after_heartbeat(&Ok(false)));
        assert!(!should_stop_after_heartbeat(&Ok(true)));
        assert!(!should_stop_after_heartbeat(&Err("timeout".into())));
    }
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd src-tauri && cargo test wr::service`
Expected: compile FAIL — `should_stop_after_heartbeat` missing.

- [ ] **Step 3: Implement**

Add above `run_job` in `src-tauri/src/wr/service.rs`:

```rust
/// Only a CONFIRMED ownership loss (server said false) stops in-flight work; a network
/// error keeps going — the lease is probably still ours and the next beat re-checks.
fn should_stop_after_heartbeat(res: &Result<bool, String>) -> bool {
    matches!(res, Ok(false))
}

/// Beat every 120s: the lease is 600s, so even one lost beat leaves wide margin, and
/// the cadence is cheap enough to never matter.
const HEARTBEAT_EVERY: std::time::Duration = std::time::Duration::from_secs(120);
```

Then wrap the engine step. Replace the current block

```rust
    // Wall-clock bound (~the video's own length): budget from the record, not a constant
    // (Rainbow Road is 233s — a fixed 300s left it ~50s of margin, not "room to spare").
    let finalized = match engine::run_video(
        &cfg.engine, &dest, selections_for(j), engine_timeout_for(j.record_ms), cancel) {
```

with:

```rust
    // Wall-clock bound (~the video's own length): budget from the record, not a constant
    // (Rainbow Road is 233s — a fixed 300s left it ~50s of margin, not "room to spare").
    //
    // HEARTBEAT (spec 2026-07-17 §5.1): while the engine runs, a scoped thread extends
    // the lease every 120s. This decouples the engine budget from the 600s lease for
    // good, and a CONFIRMED ownership loss (heartbeat -> Ok(false): someone else claimed
    // after an overrun) cancels the run — no point finishing a job we can no longer
    // report. The composed closure means the engine watchdog polls lease_lost every
    // 250ms like everything else.
    let lease_lost = std::sync::atomic::AtomicBool::new(false);
    let cancel_or_lost = || cancel() || lease_lost.load(std::sync::atomic::Ordering::Relaxed);
    let run_result = std::thread::scope(|s| {
        let done = std::sync::atomic::AtomicBool::new(false);
        let done_ref = &done;
        let lost_ref = &lease_lost;
        s.spawn(move || {
            let mut since_beat = std::time::Duration::ZERO;
            while !done_ref.load(std::sync::atomic::Ordering::Relaxed) {
                std::thread::sleep(std::time::Duration::from_millis(250));
                since_beat += std::time::Duration::from_millis(250);
                if since_beat >= HEARTBEAT_EVERY {
                    since_beat = std::time::Duration::ZERO;
                    let res = client.heartbeat(j.wr_id);
                    if should_stop_after_heartbeat(&res) {
                        log::warn!("[wr] wr_id={} lease no longer ours; cancelling run", j.wr_id);
                        lost_ref.store(true, std::sync::atomic::Ordering::Relaxed);
                        return;
                    }
                }
            }
        });
        let r = engine::run_video(
            &cfg.engine, &dest, selections_for(j), engine_timeout_for(j.record_ms),
            &cancel_or_lost);
        done.store(true, std::sync::atomic::Ordering::Relaxed);
        r
    });
    let finalized = match run_result {
```

(The match arms below it are unchanged. Note: on a lease-lost cancel the existing
`Err(WrError::Cancelled)` arm calls `release()`, which 409s harmlessly — we are no longer
the owner — and reports `Released`; add this one-line comment above that arm:)

```rust
        // (A lease-lost cancel lands here too: release() then 409s harmlessly — fine.)
```

- [ ] **Step 4: Update job.rs's heartbeat docs**

In `src-tauri/src/wr/job.rs`: delete the `#[allow(dead_code)]` line above `pub fn heartbeat`
and replace its final doc paragraph

```rust
    /// Unused for now (`#[allow(dead_code)]` below) — today's whole-job worst case
    /// (~30s download + <=540s engine budget + 30s upload) still fits the ~600s lease,
    /// narrowly; see service::engine_timeout_for's doc. Plan 3's loop should call this
    /// defensively around the engine step.
```

with:

```rust
    /// Called every 120s by run_job's heartbeat thread while the engine runs (Plan 3),
    /// so the lease outlives any legal run regardless of the download's duration.
```

- [ ] **Step 5: Suite + build + commit**

Run: `cd src-tauri && cargo test && cargo build`
Expected: 109 passed + 1 ignored, zero warnings (the removed allow must not warn — heartbeat
now has a real caller).

```bash
git add src-tauri/src/wr/service.rs src-tauri/src/wr/job.rs
git commit -m "feat(wr): heartbeat the lease while the engine runs; confirmed loss cancels"
```

---

### Task 5: Bounded, cancel-aware yt-dlp download

**Files:**
- Modify: `src-tauri/src/wr/ytdlp.rs` (spawn+watchdog `run_download`; `download` gains `cancel`)
- Modify: `src-tauri/src/wr/service.rs` (call sites + Cancelled arm on the download path)

**Interfaces:**
- Produces: `ytdlp::DOWNLOAD_CAP: Duration` (240 s);
  `ytdlp::download(exe: &Path, url: &str, tier: Tier, dest: &Path, cancel: &(dyn Fn() -> bool + Sync)) -> Result<(), WrError>`
  (signature change — service.rs is the only caller);
  private `run_download(cmd: Command, cap: Duration, cancel) -> Result<(bool, String), WrError>`.

- [ ] **Step 1: Write the failing tests**

Append to `src-tauri/src/wr/ytdlp.rs` `mod tests` (add `use std::time::Duration;` and
`use std::process::Command;` to the test module's imports):

```rust
    /// A stand-in downloader that never finishes — the shape of a stalled transfer.
    fn wedged_cmd() -> Command {
        let mut c = Command::new("python");
        c.args(["-c", "import time; time.sleep(60)"]);
        c
    }

    #[test]
    fn a_wedged_download_is_killed_at_the_cap_not_waited_out() {
        let started = std::time::Instant::now();
        let err = run_download(wedged_cmd(), Duration::from_secs(2), &|| false)
            .expect_err("a stalled download must be killed, not waited out");
        assert!(matches!(err, WrError::DownloadFailed(_)),
            "a timeout is retryable DownloadFailed, got {err:?}");
        assert!(started.elapsed() < Duration::from_secs(15),
            "the cap must actually fire; took {:?}", started.elapsed());
    }

    #[test]
    fn cancel_aborts_a_download_promptly_and_is_not_a_failure() {
        let started = std::time::Instant::now();
        let err = run_download(wedged_cmd(), Duration::from_secs(600), &|| true)
            .expect_err("a cancelled download must abort");
        assert!(matches!(err, WrError::Cancelled),
            "cancel must stay distinct from failure (release vs fail), got {err:?}");
        assert!(started.elapsed() < Duration::from_secs(15));
    }

    #[test]
    fn a_finished_download_reports_status_and_stderr() {
        let mut ok = Command::new("python");
        ok.args(["-c", "import sys; sys.stderr.write('some warning\\n')"]);
        let (success, stderr) = run_download(ok, Duration::from_secs(10), &|| false).unwrap();
        assert!(success);
        assert!(stderr.contains("some warning"), "stderr must be captured for classify_failure");
    }
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd src-tauri && cargo test wr::ytdlp`
Expected: compile FAIL — `run_download` missing.

- [ ] **Step 3: Implement**

In `src-tauri/src/wr/ytdlp.rs`, add below `classify_failure`:

```rust
/// Hard cap on one yt-dlp run (spec 2026-07-17 §5.2). The biggest current video
/// (Rainbow Road, ~135MB) downloads in well under a minute on any sane connection;
/// 240s is generous headroom, and past it a retry beats waiting.
pub const DOWNLOAD_CAP: std::time::Duration = std::time::Duration::from_secs(240);

/// Run a downloader process with a watchdog: cancel-aware, bounded by `cap`. Returns
/// (exit_success, stderr_text). Mirrors engine.rs's run_video shell: a watchdog thread
/// polls cancel/elapsed every 250ms and kills the child (closing its pipes, which
/// unblocks the drain); .output()-style blocking had neither bound nor cancel, which
/// stalled the whole runner on a hung transfer (fix-wave review F6).
fn run_download(
    mut cmd: std::process::Command,
    cap: std::time::Duration,
    cancel: &(dyn Fn() -> bool + Sync),
) -> Result<(bool, String), WrError> {
    use std::io::Read;
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::Mutex;

    let mut child = cmd
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .map_err(|e| WrError::EngineFailed(format!("spawn yt-dlp: {e}")))?;
    let stderr = child.stderr.take().expect("piped stderr");

    let started = std::time::Instant::now();
    let child = Mutex::new(child);
    let done = AtomicBool::new(false);
    let cancelled = AtomicBool::new(false);
    let timed_out = AtomicBool::new(false);

    let stderr_text = std::thread::scope(|s| {
        s.spawn(|| {
            while !done.load(Ordering::Relaxed) {
                if cancel() {
                    cancelled.store(true, Ordering::Relaxed);
                    let _ = child.lock().unwrap().kill();
                    return;
                }
                if started.elapsed() > cap {
                    timed_out.store(true, Ordering::Relaxed);
                    let _ = child.lock().unwrap().kill();
                    return;
                }
                std::thread::sleep(std::time::Duration::from_millis(250));
            }
        });
        // Drain stderr to EOF (yt-dlp's stderr is small; no ring needed). Blocks until
        // the child exits or is killed — either closes the pipe.
        let mut buf = String::new();
        let mut rdr = stderr;
        let _ = rdr.read_to_string(&mut buf);
        done.store(true, Ordering::Relaxed);
        buf
    });

    let mut child = child.into_inner().unwrap_or_else(|e| e.into_inner());
    let status = child.wait().map_err(|e| WrError::EngineFailed(format!("wait yt-dlp: {e}")))?;

    if cancelled.load(std::sync::atomic::Ordering::Relaxed) {
        return Err(WrError::Cancelled);
    }
    if timed_out.load(std::sync::atomic::Ordering::Relaxed) {
        return Err(WrError::DownloadFailed(format!(
            "download exceeded the {}s cap and was killed", cap.as_secs())));
    }
    Ok((status.success(), stderr_text))
}

/// Download `url` to `dest`. Video only — audio is never fetched. Bounded and
/// cancel-aware via run_download.
pub fn download(
    exe: &Path,
    url: &str,
    tier: Tier,
    dest: &Path,
    cancel: &(dyn Fn() -> bool + Sync),
) -> Result<(), WrError> {
    let mut cmd = std::process::Command::new(exe);
    cmd.args([
        "-f", format_selector(tier),
        "-o", &dest.to_string_lossy(),
        "--no-playlist",
        // Observed live: the 197MB pull 403'd on defaults and only completed with
        // concurrent fragments. Do NOT reach for --extractor-args
        // player_client=web_safari as a "fix" — it trips YouTube's n-challenge and
        // needs a JS runtime. The default client works.
        "--concurrent-fragments", "4",
        "--retries", "10",
        "--fragment-retries", "10",
        "--no-progress",
        url,
    ]);
    let (success, stderr) = run_download(cmd, DOWNLOAD_CAP, cancel)?;
    if success && dest.is_file() { return Ok(()); }
    Err(classify_failure(&stderr))
}
```

DELETE the old `pub fn download(...)` (`.output()`-based) entirely. The old
`a_spawn_failure_is_engine_failed_not_download_failed` test gains the cancel arg:

```rust
        let err = download(missing, "https://example.invalid/video", Tier::Native1080p60, &dest,
                           &|| false)
            .expect_err("a nonexistent exe must fail to spawn");
```

- [ ] **Step 4: Update the service.rs call sites**

In `run_job`:
- `ytdlp::download(&exe, &j.video_url, tier, &dest)` → `ytdlp::download(&exe, &j.video_url, tier, &dest, cancel)`
- the retry line `.and_then(|exe2| ytdlp::download(&exe2, &j.video_url, tier, &dest));`
  → `.and_then(|exe2| ytdlp::download(&exe2, &j.video_url, tier, &dest, cancel));`
- ABOVE the `if is_staleness_explicable(&e) {` check, insert a Cancelled arm so a pause during
  the download releases instead of failing:

```rust
    if let Err(e) = ytdlp::download(&exe, &j.video_url, tier, &dest, cancel) {
        // A cancel mid-download is a deliberate stop: release (refund), never fail.
        if matches!(e, WrError::Cancelled) {
            let _ = client.release(j.wr_id);
            return Outcome::Released(j.wr_id);
        }
        if is_staleness_explicable(&e) {
```

(the rest of that block is unchanged; `is_staleness_explicable(Cancelled)` is already false, so
the new arm is the only behaviour change).

- [ ] **Step 5: Run to verify everything passes**

Run: `cd src-tauri && cargo test wr::ytdlp wr::service`
Expected: PASS — ytdlp 9 tests (6 old incl. updated spawn-failure + 3 new), service unchanged.
Then: `cargo test && cargo build` → 112 passed + 1 ignored, zero warnings.

- [ ] **Step 6: Commit**

```bash
git add src-tauri/src/wr/ytdlp.rs src-tauri/src/wr/service.rs
git commit -m "feat(wr): bound the yt-dlp download and make it cancel-aware"
```

---

### Task 6: Programmatic window, single-instance, autostart, close-to-tray

**Files:**
- Modify: `src-tauri/Cargo.toml`
- Modify: `src-tauri/tauri.conf.json`
- Modify: `src-tauri/src/lib.rs`
- Modify: `src-tauri/src/wr/mod.rs` (`wr_set_setting` autostart branch)

No unit tests are possible for window lifecycle (Global Constraints): this task's gate is
compile + zero warnings in BOTH profiles + `cargo test` unchanged + the manual checklist at
merge time. Do not write lifecycle tests that assert nothing.

- [ ] **Step 1: Dependencies + config**

`src-tauri/Cargo.toml`:
- `tauri = { version = "2", features = [] }` → `tauri = { version = "2", features = ["tray-icon", "image-png"] }`
  (tray-icon is used by Task 7; added here so Cargo.toml is touched once)
- Add below `tauri-plugin-dialog = "2"`:

```toml
tauri-plugin-single-instance = "2"
tauri-plugin-autostart = "2"
```

`src-tauri/tauri.conf.json`: replace the `app.windows` array with `[]` (keep the `app` object
and `security` untouched):

```json
    "windows": [],
```

- [ ] **Step 2: Window helpers + launch-mode split in lib.rs**

Add to `src-tauri/src/lib.rs` (above `pub fn run()`):

```rust
/// True when this process was started by the login autostart entry. A --tray-start
/// launch creates NO window and NO webview (spec 2026-07-17 §1): the camera cannot be
/// touched by a hidden start because App.svelte's onMount->start_tracker only ever runs
/// inside a window the user asked for.
fn is_tray_start() -> bool {
    std::env::args().any(|a| a == "--tray-start")
}

/// Create the main window (the exact shape tauri.conf.json used to declare) or focus it
/// if it already exists. Every fresh creation re-runs the frontend's onMount, so live
/// tracking starts on restore just as it does on a normal launch.
pub fn show_main_window(app: &tauri::AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
        return;
    }
    match tauri::WebviewWindowBuilder::new(app, "main", tauri::WebviewUrl::default())
        .title("pbenguin")
        .inner_size(1200.0, 760.0)
        .min_inner_size(600.0, 440.0)
        .center()
        .resizable(true)
        .decorations(false)
        .build()
    {
        Ok(_w) => {
            #[cfg(target_os = "windows")]
            if let Some(w) = app.get_webview_window("main") {
                grant_media_permissions(&w);
            }
        }
        Err(e) => log::error!("create main window: {e}"),
    }
}

/// Kill the live engine sidecar if running. Shared by stop/restart commands, app exit,
/// and close-to-tray with keep_tracking_in_tray off (camera released).
fn kill_sidecar(state: &SidecarState) {
    if let Ok(mut guard) = state.0.lock() {
        if let Some(child) = guard.take() {
            let _ = child.kill();
            wr::gate::ACTIVITY.set_tracking(false);
        }
    }
}
```

Rewrite `stop_tracker` and `restart_tracker` to use it (replacing their bodies AND Task 2's
inline `set_tracking(false)` lines, which move into `kill_sidecar`):

```rust
/// Kill the running tracker without restarting it (e.g. before applying an update).
#[tauri::command]
fn stop_tracker(state: tauri::State<SidecarState>) {
    kill_sidecar(&state);
}

/// Kill the running tracker and immediately restart it (e.g. after a device change).
#[tauri::command]
fn restart_tracker(app: tauri::AppHandle, state: tauri::State<SidecarState>) {
    kill_sidecar(&state);
    do_spawn_sidecar(app, &state);
}
```

- [ ] **Step 3: Builder wiring**

In `pub fn run()`:

(a) FIRST plugin (before the log plugin — single-instance must be registered first per its
docs), the second-launch handler:

```rust
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            // A second manual launch while trayed: surface the existing instance.
            // Load-bearing, not cosmetic — two processes would share one worker-id and
            // sweep_orphans would glob-delete each other's live download (spec §1).
            show_main_window(app);
        }))
```

(b) After `.plugin(tauri_plugin_dialog::init())`:

```rust
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            Some(vec!["--tray-start"]),
        ))
```

(c) Replace the `setup` closure's window-dependent line. Old:

```rust
            #[cfg(target_os = "windows")]
            grant_media_permissions(&app.get_webview_window("main").expect("main window"));
```

New (window creation is conditional; `grant_media_permissions` moved into `show_main_window`):

```rust
            if !is_tray_start() {
                show_main_window(app.handle());
            }
```

(d) Close-to-tray: after the `.setup(...)` call, add:

```rust
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                let app = window.app_handle();
                let Ok(c) = wr::settings_db(app) else { return };
                if !wr::state::get_flag(&c, wr::state::SETTING_CLOSE_TO_TRAY) {
                    return; // default: closing quits, exactly as today
                }
                api.prevent_close();
                if !wr::state::get_flag(&c, wr::state::SETTING_KEEP_TRACKING_IN_TRAY) {
                    // The camera light goes OFF on tray-enter unless the user opted to
                    // keep tracking (spec §3).
                    if let Some(state) = app.try_state::<SidecarState>() {
                        kill_sidecar(&state);
                    }
                }
                // Destroy (not hide): the webview is a pure viewer — run uploads flow
                // through sync::on_line in Rust — and a fresh onMount on restore is the
                // same path as a normal launch. destroy() bypasses CloseRequested, so
                // no loop.
                let _ = window.destroy();
            }
        })
```

(e) `wr_set_setting` autostart branch — in `src-tauri/src/wr/mod.rs`, after the
`SETTING_RUN_WR_SERVICE` block from Task 3:

```rust
    if key == state::SETTING_START_AT_LOGIN {
        use tauri_plugin_autostart::ManagerExt;
        let mgr = app.autolaunch();
        let res = if value { mgr.enable() } else { mgr.disable() };
        if let Err(e) = res {
            return Err(format!("autostart registration failed: {e}"));
        }
    }
```

- [ ] **Step 3f (AMENDED 2026-07-18 — review finding): stay resident after the last window dies**

Destroying the sole window fires `RunEvent::ExitRequested` and, unprevented, the app EXITS —
close-to-tray would quit the whole process (verified against tauri-runtime-wry 2.11.4's
Destroyed→ExitRequested→ControlFlow::Exit path). Last-window-destroy arrives with `code: None`;
a deliberate `app.exit(n)` carries `Some(n)`, so no quit-intent flag is needed. In `run()`'s
event callback, restructure to a match and add the guard:

```rust
        .run(|app_handle, event| {
            match event {
                // Last-window-destroyed arrives with code: None; a deliberate app.exit(n)
                // (tray Quit) carries Some(n). Stay resident only for the former, and only
                // when close-to-tray is on — i.e. exactly when our close handler just
                // destroyed the window expecting the process to live on in the tray.
                // Everything else (normal quit with the flag off, tray Quit, OS session
                // end) falls through to a real exit.
                tauri::RunEvent::ExitRequested { code, api, .. } => {
                    if code.is_none() {
                        if let Ok(c) = wr::settings_db(app_handle) {
                            if wr::state::get_flag(&c, wr::state::SETTING_CLOSE_TO_TRAY) {
                                api.prevent_exit();
                            }
                        }
                    }
                }
                tauri::RunEvent::Exit => {
                    // (existing teardown body unchanged: runner stop, sidecar kill,
                    // discord shutdown)
                }
                _ => {}
            }
        });
```

Also add a label guard as the close handler's first line (future-proofing — the logic must
never apply to a second window): `if window.label() != "main" { return; }`

- [ ] **Step 4: Gate + commit**

Run: `cd src-tauri && cargo test && cargo build && cargo build --release`
Expected: 112 passed + 1 ignored; ZERO warnings in both profiles. (`show_main_window` and the
handlers are all reachable; nothing should need an allow.)

```bash
git add src-tauri/Cargo.toml src-tauri/Cargo.lock src-tauri/tauri.conf.json src-tauri/src/lib.rs src-tauri/src/wr/mod.rs
git commit -m "feat(app): on-demand main window, single-instance, autostart, close-to-tray"
```

---

### Task 7: The tray — icons, menu, tooltip

**Files:**
- Create: `src-tauri/icons/tray-16-idle.png`, `src-tauri/icons/tray-16-active.png`
- Create: `src-tauri/src/tray.rs`
- Modify: `src-tauri/src/lib.rs` (`mod tray;`, setup + runner hook)
- Modify: `src-tauri/src/wr/mod.rs` (`wr_set_setting` calls `tray::sync_tray`)

**Interfaces:**
- Consumes: Task 1 flags; Task 3's `RunnerState`, `runner.is_paused()`, `set_refresh_hook`,
  `phase::{get, Phase, PhaseKind}`; Task 2's gate; Task 6's `show_main_window`.
- Produces: `tray::sync_tray(app: &tauri::AppHandle)` (create/destroy per settings),
  `tray::refresh_tray_status(app: &tauri::AppHandle)`; pure
  `tray::tooltip(run_wr_service: bool, paused: bool, gate_open: bool, phase: Option<&wr::phase::Phase>) -> String`
  and `tray::is_active(phase: Option<&wr::phase::Phase>) -> bool`.

- [ ] **Step 1: Produce the icons (offline, then committed)**

Run this once from the repo root (cv2 is a project dependency; writes the two PNGs):

```bash
python - <<'EOF'
import cv2, numpy as np
src = cv2.imread('src-tauri/icons/128x128.png', cv2.IMREAD_UNCHANGED)  # BGRA
small = cv2.resize(src, (16, 16), interpolation=cv2.INTER_AREA)        # hi-res -> AA downscale
b, g, r, a = cv2.split(small.astype(np.float32))
lum = 0.114 * b + 0.587 * g + 0.299 * r
# idle: neutral grey, slightly lifted so it reads on dark taskbars
idle = cv2.merge([lum, lum, lum, a]).clip(0, 255).astype(np.uint8)
# active: red-shaded — luminance pushed into the red channel, blue/green suppressed
act = cv2.merge([lum * 0.25, lum * 0.25, np.clip(lum * 1.35 + 30, 0, 255), a]).astype(np.uint8)
cv2.imwrite('src-tauri/icons/tray-16-idle.png', idle)
cv2.imwrite('src-tauri/icons/tray-16-active.png', act)
print('wrote tray-16-idle.png + tray-16-active.png')
EOF
```

Eyeball both files yourself (open them; 16×16, transparent background preserved, the active one
clearly red). They ship for Paul's review at merge — if either looks wrong at 16px, adjust the
two tint lines and regenerate; do NOT hand-edit pixels.

- [ ] **Step 2: Write the failing pure tests + tray.rs**

Create `src-tauri/src/tray.rs`:

```rust
//! The system tray (spec 2026-07-17 §2). Exists only while at least one background
//! feature is enabled; grey when idle, red while a job is in flight.

use crate::wr::{self, phase::Phase};
use tauri::Manager;

#[cfg(test)]
mod tests {
    use super::*;
    use crate::wr::phase::PhaseKind;

    fn ph(kind: PhaseKind) -> Phase {
        Phase { kind, course_slug: "dk_spaceport".into() }
    }

    #[test]
    fn tooltip_states() {
        // Priority: off > paused > working > waiting > idle.
        assert_eq!(tooltip(false, false, true, None), "WR service off");
        assert_eq!(tooltip(true, true, true, None), "Paused");
        assert_eq!(tooltip(true, false, true, Some(&ph(PhaseKind::Downloading))),
                   "Downloading dk_spaceport…");
        assert_eq!(tooltip(true, false, true, Some(&ph(PhaseKind::Processing))),
                   "Processing dk_spaceport…");
        assert_eq!(tooltip(true, false, false, None), "Waiting — tracking active");
        assert_eq!(tooltip(true, false, true, None), "Idle");
    }

    #[test]
    fn active_icon_only_while_a_job_is_in_flight() {
        assert!(!is_active(None));
        assert!(is_active(Some(&ph(PhaseKind::Downloading))));
        assert!(is_active(Some(&ph(PhaseKind::Processing))));
    }
}
```

Run: `cd src-tauri && cargo test tray`
Expected: compile FAIL — missing fns (add `mod tray;` beside `mod wr;` in lib.rs now).

- [ ] **Step 3: Implement tray.rs**

Add above the tests:

```rust
const TRAY_ID: &str = "pbenguin-tray";

/// Tooltip per spec §2. The parent spec's "Idle — 3 queued" needs a Pi endpoint that
/// doesn't exist; dropped for v1.
pub fn tooltip(run_wr_service: bool, paused: bool, gate_open: bool, phase: Option<&Phase>) -> String {
    if !run_wr_service { return "WR service off".into(); }
    if paused { return "Paused".into(); }
    if let Some(p) = phase {
        return match p.kind {
            wr::phase::PhaseKind::Downloading => format!("Downloading {}…", p.course_slug),
            wr::phase::PhaseKind::Processing => format!("Processing {}…", p.course_slug),
        };
    }
    if !gate_open { return "Waiting — tracking active".into(); }
    "Idle".into()
}

/// Red only while a job is actually in flight.
pub fn is_active(phase: Option<&Phase>) -> bool { phase.is_some() }

fn icon(active: bool) -> Result<tauri::image::Image<'static>, tauri::Error> {
    let bytes: &[u8] = if active {
        include_bytes!("../icons/tray-16-active.png")
    } else {
        include_bytes!("../icons/tray-16-idle.png")
    };
    tauri::image::Image::from_bytes(bytes)
}

fn settings(app: &tauri::AppHandle) -> (bool, bool, bool, bool) {
    match wr::settings_db(app) {
        Ok(c) => (
            wr::state::get_flag(&c, wr::state::SETTING_CLOSE_TO_TRAY),
            wr::state::get_flag(&c, wr::state::SETTING_START_AT_LOGIN),
            wr::state::get_flag(&c, wr::state::SETTING_RUN_WR_SERVICE),
            wr::state::get_flag(&c, wr::state::SETTING_KEEP_TRACKING_IN_TRAY),
        ),
        Err(_) => (false, false, false, false),
    }
}

/// Create or destroy the tray so it exists iff any background feature is on
/// (all-defaults = no tray = zero visible change; spec §2). Rebuilds the menu each call
/// so the Pause/Resume label tracks runner state.
pub fn sync_tray(app: &tauri::AppHandle) {
    let (close_to_tray, start_at_login, run_wr, _) = settings(app);
    let wanted = close_to_tray || start_at_login || run_wr;
    let existing = app.tray_by_id(TRAY_ID);

    if !wanted {
        if existing.is_some() { let _ = app.remove_tray_by_id(TRAY_ID); }
        return;
    }

    let paused = app
        .try_state::<crate::RunnerState>()
        .and_then(|rs| rs.0.lock().ok().map(|g| g.as_ref().map(|r| r.is_paused()).unwrap_or(false)))
        .unwrap_or(false);
    let pause_label = if paused { "Resume WR service" } else { "Pause WR service" };

    let menu = (|| -> tauri::Result<tauri::menu::Menu<tauri::Wry>> {
        use tauri::menu::{MenuBuilder, MenuItemBuilder};
        MenuBuilder::new(app)
            .item(&MenuItemBuilder::with_id("pause", pause_label).enabled(run_wr).build(app)?)
            .separator()
            .item(&MenuItemBuilder::with_id("open", "Open pbenguin").build(app)?)
            .item(&MenuItemBuilder::with_id("quit", "Quit").build(app)?)
            .build()
    })();
    let Ok(menu) = menu else { return };

    if let Some(tray) = existing {
        let _ = tray.set_menu(Some(menu));
        refresh_tray_status(app);
        return;
    }

    let Ok(img) = icon(false) else { return };
    let built = tauri::tray::TrayIconBuilder::with_id(TRAY_ID)
        .icon(img)
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id().as_ref() {
            "open" => crate::show_main_window(app),
            "quit" => app.exit(0),
            "pause" => {
                if let Some(rs) = app.try_state::<crate::RunnerState>() {
                    if let Ok(guard) = rs.0.lock() {
                        if let Some(r) = guard.as_ref() { r.set_paused(!r.is_paused()); }
                    }
                }
                sync_tray(app); // relabel Pause/Resume + retint
            }
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let tauri::tray::TrayIconEvent::Click {
                button: tauri::tray::MouseButton::Left,
                button_state: tauri::tray::MouseButtonState::Up, ..
            } = event
            {
                crate::show_main_window(tray.app_handle());
            }
        })
        .build(app);
    if built.is_ok() { refresh_tray_status(app); }
}

/// Retint + retooltip from live state. Called by the runner's refresh hook at phase
/// transitions and by sync_tray.
pub fn refresh_tray_status(app: &tauri::AppHandle) {
    let Some(tray) = app.tray_by_id(TRAY_ID) else { return };
    let (_, _, run_wr, _) = settings(app);
    let paused = app
        .try_state::<crate::RunnerState>()
        .and_then(|rs| rs.0.lock().ok().map(|g| g.as_ref().map(|r| r.is_paused()).unwrap_or(false)))
        .unwrap_or(false);
    let phase = wr::phase::get();
    let gate = wr::gate::gate_open(
        wr::gate::ACTIVITY.tracking_running(),
        wr::gate::ACTIVITY.last_change_epoch_ms(),
        wr::gate::now_epoch_ms(),
    );
    let _ = tray.set_tooltip(Some(tooltip(run_wr, paused, gate, phase.as_ref())));
    if let Ok(img) = icon(is_active(phase.as_ref())) {
        let _ = tray.set_icon(Some(img));
    }
}
```

- [ ] **Step 4: Wire it up**

In `src-tauri/src/lib.rs` `setup`, after the RunnerState block from Task 3, add:

```rust
            tray::sync_tray(app.handle());
            if let Some(rs) = app.try_state::<RunnerState>() {
                if let Ok(guard) = rs.0.lock() {
                    if let Some(r) = guard.as_ref() {
                        let h = app.handle().clone();
                        r.set_refresh_hook(Box::new(move || tray::refresh_tray_status(&h)));
                    }
                }
            }
```

(The hook runs on the runner thread. Tauri v2's tray setters marshal to the platform correctly
on Windows; if dev testing ever shows otherwise, wrap the hook body in
`h.run_on_main_thread(...)` — note it in your report rather than silently restructuring.)

In `src-tauri/src/wr/mod.rs`, at the END of `wr_set_setting` (after the autostart branch), add:

```rust
    crate::tray::sync_tray(&app);
    Ok(())
```

(replacing the existing trailing `Ok(())`). Also: when Task 3's `run_wr_service` branch STARTS a
runner here, install the refresh hook on it the same way as in setup:

```rust
            (true, false) => {
                let r = runner::Runner::start(app.clone());
                let h = app.clone();
                r.set_refresh_hook(Box::new(move || crate::tray::refresh_tray_status(&h)));
                *guard = Some(r);
            }
```

- [ ] **Step 5: Gate + commit**

Run: `cd src-tauri && cargo test && cargo build && cargo build --release`
Expected: 114 passed + 1 ignored; zero warnings both profiles.

```bash
git add src-tauri/icons/tray-16-idle.png src-tauri/icons/tray-16-active.png src-tauri/src/tray.rs src-tauri/src/lib.rs src-tauri/src/wr/mod.rs
git commit -m "feat(app): conditional system tray with WR status tooltip + pause/resume"
```

---

### Task 8: The Background settings section (frontend)

**Files:**
- Modify: `src/components/SettingsModal.svelte`

**Interfaces:**
- Consumes: Task 1's commands — `invoke("wr_get_settings")` returns
  `{ closeToTray, startAtLogin, runWrService, keepTrackingInTray }` (camelCase);
  `invoke("wr_set_setting", { key, value })` takes the snake_case store key.

No frontend test rig exists in this repo (nothing under `src/` has tests) — do not invent one.
The gate is: `npm run build:full` compiles clean, plus the manual checklist. Keep the styling
plain (desktop app is deliberately plain/OBS-like — see the repo's design rules).

- [ ] **Step 1: Script-side state + handlers**

In `src/components/SettingsModal.svelte`'s `<script>` (below the existing `invoke` import and
the `export let` block), add:

```js
  // ── Background modes (spec 2026-07-17 §3) ────────────────────────────────────
  // camelCase mirror of the Rust store; keys sent back are the snake_case store keys.
  let bg = { closeToTray: false, startAtLogin: false, runWrService: false, keepTrackingInTray: false };
  const BG_KEYS = {
    closeToTray: "close_to_tray",
    startAtLogin: "start_at_login",
    runWrService: "run_wr_service",
    keepTrackingInTray: "keep_tracking_in_tray",
  };
  async function loadBg() {
    try { bg = await invoke("wr_get_settings"); } catch { /* pre-first-run: defaults stand */ }
  }
  async function setBg(field, value) {
    bg = { ...bg, [field]: value };                    // optimistic; store is the truth on reopen
    try { await invoke("wr_set_setting", { key: BG_KEYS[field], value }); }
    catch (e) { console.error("wr_set_setting failed", e); loadBg(); }
  }
  loadBg();
```

- [ ] **Step 2: Markup**

In the returning-user (non-wizard) layout, directly AFTER the block that renders
`<LanguageSelectors …/>`, add:

```svelte
      <!-- Background modes: all default off = today's behaviour exactly. -->
      <div class="bg-section">
        <h3>Background</h3>
        <label class="bg-row">
          <input type="checkbox" checked={bg.closeToTray}
                 on:change={(e) => setBg("closeToTray", e.currentTarget.checked)} />
          Close to tray instead of quitting
        </label>
        <label class="bg-row">
          <input type="checkbox" checked={bg.startAtLogin}
                 on:change={(e) => setBg("startAtLogin", e.currentTarget.checked)} />
          Start pbenguin at login
        </label>
        <label class="bg-row">
          <input type="checkbox" checked={bg.runWrService}
                 on:change={(e) => setBg("runWrService", e.currentTarget.checked)} />
          Run the WR service
        </label>
        <div class="bg-hint">Processes WR videos only while tracking is stopped or idle 10 min+.</div>
        <div class="bg-subhead">When in tray:</div>
        <label class="bg-row bg-indent">
          <input type="checkbox" checked={bg.keepTrackingInTray}
                 on:change={(e) => setBg("keepTrackingInTray", e.currentTarget.checked)} />
          Keep live tracking running
        </label>
      </div>
```

And in the component's `<style>` block:

```css
  .bg-section { margin-top: 14px; padding-top: 10px; border-top: 1px solid var(--border, #333); }
  .bg-section h3 { margin: 0 0 8px; font-size: 13px; opacity: 0.85; }
  .bg-row { display: flex; align-items: center; gap: 8px; padding: 3px 0; cursor: pointer; }
  .bg-hint { font-size: 11px; opacity: 0.6; margin: 0 0 6px 24px; }
  .bg-subhead { font-size: 11px; opacity: 0.6; margin-top: 6px; }
  .bg-indent { margin-left: 16px; }
```

(If the modal already defines equivalent row/hint classes, reuse those instead of adding
duplicates — match the file's existing idiom; the copy strings above are spec-exact and must
not change.)

- [ ] **Step 3: Gate + commit**

Run: `npm run build:full` (repo root)
Expected: builds clean, no Svelte warnings about the new block.

```bash
git add src/components/SettingsModal.svelte
git commit -m "feat(ui): Background settings section (tray, autostart, WR service)"
```

---

### Task 9: Pi sweep alert for silent-crash deaths

**Files:**
- Modify: `pi/src/db/connect.ts` (additive ALTER list)
- Modify: `pi/src/db/wrJobs.ts` + `pi/src/db/wrJobs.test.ts`
- Modify: `pi/src/api/wrJobs.ts` + `pi/src/api/wrJobs.test.ts`
- Modify: `pi/src/wr/reconcile.ts` + `pi/src/wr/reconcile.test.ts`
- Modify: `pi/src/wr/scheduler.ts` + `pi/src/wr/scheduler.test.ts`
- Modify: `pi/src/server.ts` (pass hub already happens via startWrScraper — verify only)

**Interfaces:**
- Consumes: `deadJobs(db)`, `EventHub.publish`, the `wr_job_dead` ServerEvent (all exist).
- Produces: `markJobAlerted(db, wrId)`; `sweepDeadJobAlerts(db, hub) -> number` (published
  count); `SchedulerOpts.sweep?: (db, hub) => number` (injectable, defaults to
  `sweepDeadJobAlerts`).

- [ ] **Step 1: The column**

In `pi/src/db/connect.ts`, find the additive-ALTER list that follows the `db.exec(schema)`
call and add, following the existing entry format exactly:

```ts
  { table: 'wr_jobs', column: 'alerted_at', ddl: 'ALTER TABLE wr_jobs ADD COLUMN alerted_at TEXT' },
```

(If the list is plain try/catch `db.exec` lines rather than objects, mirror THAT shape —
open the file and copy the exact idiom of its last entry.)

- [ ] **Step 2: Failing db tests**

Append to `pi/src/db/wrJobs.test.ts` (inside `describe('deadJobs', ...)`; `EventHub` import:
`import { EventHub } from '../api/events';` and `import type { ServerEvent } from './types';`):

```ts
  it('sweepDeadJobAlerts announces each dead job exactly once', () => {
    const db = dead();
    db.prepare('UPDATE wr_jobs SET attempts=5 WHERE wr_id=10').run();
    const hub = new EventHub();
    const events: ServerEvent[] = [];
    hub.subscribe((e) => events.push(e));
    expect(sweepDeadJobAlerts(db, hub)).toBe(1);       // silent death found -> alert
    expect(sweepDeadJobAlerts(db, hub)).toBe(0);       // second sweep: already alerted
    expect(events.filter((e) => e.type === 'wr_job_dead')).toMatchObject([
      { wr_id: 10, course: 'Mario Circuit', attempts: 5 },
    ]);
  });

  it('markJobAlerted keeps the route-alerted job out of the sweep', () => {
    const db = dead(); claimJob(db, 'w1');
    failJob(db, 10, 'w1', 'time_mismatch detected=1 expected=2');
    markJobAlerted(db, 10);                            // the /result route alerted already
    const hub = new EventHub();
    expect(sweepDeadJobAlerts(db, hub)).toBe(0);
  });
```

Run: `cd pi && npx vitest run src/db/wrJobs.test.ts`
Expected: FAIL — `sweepDeadJobAlerts is not a function`.

- [ ] **Step 3: Implement in wrJobs.ts**

Append to `pi/src/db/wrJobs.ts` (type import at top: `import type { EventHub } from '../api/events';`):

```ts
/** Stamp a dead job as announced, whichever path announced it (the /result route or the
 *  sweep). Revival (reconcile backfill on a link change) clears it, so a revived-then-
 *  re-dead job legitimately re-alerts. */
export function markJobAlerted(db: DatabaseSync, wrId: number): void {
  db.prepare(`UPDATE wr_jobs SET alerted_at=datetime('now') WHERE wr_id=?`).run(wrId);
}

/** Announce dead jobs nobody has alerted yet. Catches the death class the /result route
 *  can't see: a job whose final attempts burned via crash + lease lapse never posts a
 *  result (spec §6.4's silent-crash note). Runs on the scraper tick; same deadJobs()
 *  predicate as the route and wr-flags, so all three can never disagree. */
export function sweepDeadJobAlerts(db: DatabaseSync, hub: EventHub): number {
  let n = 0;
  for (const d of deadJobs(db)) {
    const row = db.prepare('SELECT alerted_at FROM wr_jobs WHERE wr_id=?').get(d.wr_id) as
      { alerted_at: string | null } | undefined;
    if (!row || row.alerted_at !== null) continue;
    hub.publish({ type: 'wr_job_dead', wr_id: d.wr_id, course: d.course,
      holder: d.holder_name, record_str: d.record_str,
      reason: d.last_error ?? 'attempts exhausted (no result ever posted)', attempts: d.attempts });
    markJobAlerted(db, d.wr_id);
    n++;
  }
  return n;
}
```

Run: `npx vitest run src/db/wrJobs.test.ts` → PASS (33 tests).

- [ ] **Step 4: Route marks alerted; revival re-arms**

In `pi/src/api/wrJobs.ts`'s `/result` failure branch, inside the `if (dead) {` block after
`hub.publish({...})`, add (and add `markJobAlerted` to the wrJobs import):

```ts
        markJobAlerted(db, dead.wr_id);
```

In `pi/src/wr/reconcile.ts` `backfill()`, the revival UPDATE gains `alerted_at=NULL`:

```ts
    db.prepare(`UPDATE wr_jobs SET last_error=NULL, attempts=0,
                  lease_owner=NULL, lease_until=NULL, alerted_at=NULL, updated_at=datetime('now')
                WHERE wr_id=?`).run(row.id);
```

Failing tests first — append to `pi/src/api/wrJobs.test.ts`:

```ts
  it('a route-announced death is stamped so the sweep will not re-announce it', async () => {
    const { db, app, w1 } = setup();
    await app.request('/v1/wr-jobs/claim', { method: 'POST', headers: w1 });
    await app.request('/v1/wr-jobs/10/result', {
      method: 'POST', headers: { ...w1, 'Content-Type': 'application/json' },
      body: JSON.stringify({ ok: false, error: 'time_mismatch detected=1 expected=2' }),
    });
    expect(db.prepare('SELECT alerted_at FROM wr_jobs WHERE wr_id=10').get())
      .not.toMatchObject({ alerted_at: null });
  });
```

and to `pi/src/wr/reconcile.test.ts` (extend the existing revival test's final assertions):

```ts
    expect(db.prepare('SELECT alerted_at FROM wr_jobs WHERE wr_id=?').get(id))
      .toMatchObject({ alerted_at: null });             // revival re-arms alerting
```

(add `db.prepare("UPDATE wr_jobs SET alerted_at=datetime('now') WHERE wr_id=?").run(id);`
directly after the `failJob(...)` line in that test, so the clearing is actually observable).

Run both files, observe the route test RED (`alerted_at: null`) before the route edit; then
GREEN after.

- [ ] **Step 5: Sweep on the scraper tick**

In `pi/src/wr/scheduler.ts`:
- imports: `import { sweepDeadJobAlerts } from '../db/wrJobs';`
- `SchedulerOpts` gains: `sweep?: (db: DatabaseSync, hub: EventHub) => number;  // injectable for tests`
- in `startWrScraper`: `const sweep = opts.sweep ?? sweepDeadJobAlerts;` and in `tick`'s `try`
  block, after the `console.log` of the scrape report:

```ts
      const alerted = sweep(db, hub);
      if (alerted) console.log(`[wr] dead-job sweep alerted ${alerted} job(s)`);
```

Failing test first — append to `pi/src/wr/scheduler.test.ts` (mirror its existing
injectable-scrape test style; read the file's setup helpers and reuse them):

```ts
  it('runs the dead-job sweep after each scrape tick', async () => {
    const calls: string[] = [];
    const stop = startWrScraper(dbStub(), hubStub(), {
      minIntervalSec: 0, maxIntervalSec: 1,
      scrape: async () => { calls.push('scrape'); return emptyReport(); },
      sweep: () => { calls.push('sweep'); return 0; },
      random: () => 0,
    });
    await vi.waitFor(() => expect(calls).toContain('sweep'));
    expect(calls.indexOf('sweep')).toBeGreaterThan(calls.indexOf('scrape'));
    stop();
  });
```

(`dbStub`/`hubStub`/`emptyReport` stand for whatever the existing tests in that file use to
fake the db/hub/report — reuse the file's actual helpers verbatim; if it constructs a real
`:memory:` db and `new EventHub()`, do the same.)

Run: RED (sweep option unknown / never called) → implement → GREEN.

- [ ] **Step 6: Full Pi gate + commit**

Run: `cd pi && npx vitest run && npx tsc --noEmit`
Expected: 609+ tests pass (605 baseline +2 db +1 api +1 scheduler; the reconcile addition
extends an existing test), tsc clean.

```bash
git add pi/src/db/connect.ts pi/src/db/wrJobs.ts pi/src/db/wrJobs.test.ts pi/src/api/wrJobs.ts pi/src/api/wrJobs.test.ts pi/src/wr/reconcile.ts pi/src/wr/reconcile.test.ts pi/src/wr/scheduler.ts pi/src/wr/scheduler.test.ts
git commit -m "feat(wr): sweep-based wr_job_dead alert catches silent-crash deaths"
```

---

### Task 10: Docs sync

**Files:**
- Modify: `docs/superpowers/specs/2026-07-15-pbenguin-wr-service-design.md`
- Modify: `CLAUDE.md` (repo root)

One commit, no tests; the gate is claims-match-code (cite the line you checked for each edit
in your report).

- [ ] **Step 1: Parent spec honesty updates**

- §6.3's caveat paragraph (added 2026-07-17, beginning **Caveat found 2026-07-17:**): replace
  its last sentence (`Plan 3 must gate that call; "a hidden start touches no camera" is a
  requirement on Plan 3, not a property the app already has.`) with:
  `RESOLVED by Plan 3's tray-only architecture (2026-07-17-wr-service-tray-background-design.md
  §1): a --tray-start launch creates no window and no webview at all, so onMount never runs;
  the call needs no gating.`
- §6.4 step 3's timeout sentence: replace `Wiring the dormant heartbeat + bounding yt-dlp are
  named Plan 3 items.` with `Plan 3 wired both: a 120s heartbeat thread runs while the engine
  does (service.rs), and yt-dlp is bounded at 240s and cancel-aware (ytdlp.rs run_download).`
- §6.4 CLOSED paragraph: replace the final sentence (`One death class stays alert-less by
  design for now: … a sweep-based alert is a named Plan 3 item.`) with:
  `Silent-crash deaths (attempts burned via crash + lease lapse, no /result ever posted) are
  announced too: the scraper tick sweeps deadJobs() for unalerted rows (wr_jobs.alerted_at
  dedups; revival clears it).`
- §6.1: append one line under the settings block:
  `Implemented by Plan 3 (2026-07-17-wr-service-tray-background-design.md): keys in
  wr_service.db's wr_local, a Background section in SettingsModal, tray exists only while at
  least one checkbox is on.`

- [ ] **Step 2: Root CLAUDE.md**

In the Repo Surfaces table's desktop-app row, after `Product name is pbenguin
(src-tauri/tauri.conf.json); the npm package id is still mkw-tracker.` append:
` Opt-in background WR service (tray-only autostart, settings > Background) replays mkwrs WR
videos through a throwaway engine and uploads trails — see
docs/superpowers/specs/2026-07-17-wr-service-tray-background-design.md.`

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-07-15-pbenguin-wr-service-design.md CLAUDE.md
git commit -m "docs(wr): sync parent spec + CLAUDE.md to the shipped tray/background modes"
```

---

## Final verification (controller, after all tasks)

1. `cd src-tauri && cargo test` → 114 passed + 1 ignored; `cargo build` + `cargo build
   --release` zero warnings.
2. `cd src-tauri && cargo test wr::engine::tests::fixture -- --ignored --nocapture` → PASS,
   `1:02.934`, >1500 points, unrelaxed (the heartbeat scope wraps run_video — prove it didn't
   disturb the real path).
3. `cd pi && npx vitest run && npx tsc --noEmit` → all green, clean.
4. `npm run build:full` → clean.
5. Mutation spot-checks (break → named test fails → restore byte-identical):
   `gate_open`'s `>=` flipped to `>` → `gate_truth_table` fails; `next_backoff` Idle cap 300→600
   → backoff test fails; `run_download`'s timeout kill removed → wedged-download test fails
   (~60s); `sweepDeadJobAlerts`'s `alerted_at !== null` skip removed → exactly-once test fails.
6. Manual smoke checklist (spec 2026-07-17 §7) — run what's runnable in dev (`npm run tauri
   dev -- -- --tray-start` for the tray-only path), leave the installed-autostart items for
   Paul's post-merge eyeball, and list every unrun item explicitly in the ledger.
7. `superpowers:finishing-a-development-branch` (merge to `main`, no push, no tag).

## What this plan deliberately does not do

- No Plan 4 (client WR-dot display).
- No queue-count tooltip (needs a Pi stats endpoint that doesn't exist).
- No WS wake nudge (polling + backoff is enough at this cadence).
- No engine (`mkw_tracker/`) changes — the Sky-High Sundae seed-row rename remains a separate
  decision for Paul.
