# WR Service Core (headless) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make pbenguin able to take one world-record job from the Pi, download its YouTube video, replay it through the detection engine, verify the result against the scraped time, and upload the trail — with no UI at all.

**Architecture:** Plan 2 of four from `docs/superpowers/specs/2026-07-15-pbenguin-wr-service-design.md`. A new `src-tauri/src/wr/` Rust module inside the existing pbenguin app (NOT a second binary — see spec §6). Every piece of *decision* logic is a pure function with unit tests; the I/O shells around them are proven once, end-to-end, against a known-good fixture video. Plan 3 adds the tray, settings and autostart; this plan is driven by a dev-only Tauri command.

**Tech Stack:** Rust (Tauri v2 app, `reqwest` blocking, `rusqlite`, `serde_json` — all already dependencies), driving the existing Python `mkw_tracker` engine over stdio JSON.

## Global Constraints

- **Never modify the Python engine (`mkw_tracker/`).** The spike proved it needs zero changes. If you think it needs one, STOP and escalate.
- **`temp/` and `*.mp4` are gitignored.** Never commit a video. The fixture `temp/wr_mario_circuit.mp4` must already exist; if absent, STOP and escalate (re-downloading it is a documented step, not something to improvise).
- **Rust tests are inline** `#[cfg(test)] mod tests` in the same file, matching `src-tauri/src/discord.rs:183`. Run with `cd src-tauri && cargo test`.
- **The crate has no `[dev-dependencies]`** and must not gain any: no mock-HTTP crates, no `tokio-test`. Test pure functions; prove I/O with the fixture.
- **Use `std::process::Command` for the WR engine, NOT `tauri-plugin-shell`.** The shell plugin needs an `AppHandle` and yields an async event stream, which would make `engine.rs` untestable and drag Tauri into a module that has no reason to know about it. `lib.rs:48` uses the shell plugin for the *live* engine; that stays as-is. This is a deliberate divergence — say so in the code comment.
- **All HTTP is `reqwest::blocking` on a dedicated OS thread**, matching `sync.rs:686`'s comment: rusqlite is sync and blocking reqwest manages its own runtime, so this avoids depending on Tauri's async runtime having a time driver.
- **Auth:** `Authorization: Bearer <player token>` **header only**, plus `X-Worker-Id: <per-install id>`. Read the token from `sync.rs`'s `CONFIG` — do NOT add a second credential store.
- **1080p60 is non-negotiable** — every engine ROI is a 1080p pixel coord. No 1080p60 stream → fail the job, don't process it badly.
- **Never point at production by accident.** The dev command must take an explicit server URL/job id; it must not silently claim from the real Pi during a unit test.
- Commit trailer, on its own line: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
- Stage only your own files with explicit `git add <paths>`. NEVER `git add -A` — the repo has a huge gitignored `temp/`.

---

## What the Pi already gives us (Plan 1, merged @ `d749002`)

`POST /v1/wr-jobs/claim` returns 200 with this exact shape, or 204 when nothing is claimable:

```json
{ "wr_id": 6, "cc": 150, "course_slug": "shy_guy_bazaar", "course_name": "Shy Guy Bazaar",
  "video_url": "https://www.youtube.com/watch?v=wTZXUMhimbw", "record_ms": 110449,
  "lap_splits_ms": null, "character_slug": "swoop", "costume_slug": null,
  "kart_slug": "rob_hog", "attempt": 1, "lease_until": "2026-07-15 04:38:40" }
```

`costume_slug: null` is **normal** — it means the base costume (24 of 30 live WRs). Never treat it as an error.

Also: `POST /v1/wr-jobs/:wr_id/heartbeat` · `/release` · `/result`. All take the same two headers. `/result` body is `{"ok":true,"points":[[t_ms,cx,cy,score,lap],...]}` or `{"ok":false,"error":"<reason>"}`. Non-owner → 409. Missing `X-Worker-Id` → 400.

## File Structure

| File | Responsibility |
|---|---|
| `src-tauri/src/wr/mod.rs` **(create)** | Module root; re-exports; the `WrError` reason enum shared by all of it. |
| `src-tauri/src/wr/state.rs` **(create)** | Stable per-install worker id + local SQLite (in-flight job, for orphan sweep). |
| `src-tauri/src/wr/job.rs` **(create)** | `WrJob` type, its JSON parsing, and the four HTTP calls. |
| `src-tauri/src/wr/ytdlp.rs` **(create)** | Self-updating `yt-dlp.exe`; format selector; download to a path. |
| `src-tauri/src/wr/engine.rs` **(create)** | `EngineDriver` — the PURE stdio decision logic — plus the process shell around it. |
| `src-tauri/src/wr/verify.rs` **(create)** | Pure verification gate: detected vs expected, and the retry tier for an attempt. |
| `src-tauri/src/wr/service.rs` **(create)** | The work loop that composes the above; pause/discard. |
| `src-tauri/src/lib.rs` **(modify)** | Register the `wr` module + the dev-only command. |

`gate.rs` is deliberately absent: the idle gate is Plan 3's (it needs the live tracker's screen state, which this plan has no access to). Plan 2's loop exposes a `can_claim: bool` input that Plan 3 supplies.

---

### Task 1: Worker identity and local state

**Files:**
- Create: `src-tauri/src/wr/mod.rs`
- Create: `src-tauri/src/wr/state.rs`
- Modify: `src-tauri/src/lib.rs`

**Interfaces:**
- Produces: `wr::state::worker_id(dir: &Path) -> String` (stable across restarts); `wr::state::open(dir: &Path) -> Result<Connection, String>`; `wr::state::set_inflight(&Connection, Option<i64>)` / `inflight(&Connection) -> Option<i64>`; `wr::WrError`.

**Why:** the lease is per-MACHINE, not per-person (one player token may run on several PCs), so the service needs an id that survives restarts. The in-flight record is how a crashed run gets its orphaned video file swept on next boot.

- [ ] **Step 1: Write the failing test**

Create `src-tauri/src/wr/state.rs`:

```rust
//! Per-install identity + local scratch state for the WR service.
//!
//! The worker id is the LEASE identity on the Pi. The player token authenticates the
//! person; this identifies the machine, so two of Paul's PCs on one token can hold
//! separate leases (spec §6, X-Worker-Id).

use rusqlite::Connection;
use std::path::Path;

#[cfg(test)]
mod tests {
    use super::*;

    fn tmpdir(tag: &str) -> std::path::PathBuf {
        let d = std::env::temp_dir().join(format!("wr_state_test_{tag}"));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    #[test]
    fn worker_id_is_stable_across_calls() {
        let d = tmpdir("stable");
        let a = worker_id(&d);
        let b = worker_id(&d);
        assert_eq!(a, b, "worker id must persist — it is the lease identity");
        assert!(!a.is_empty());
    }

    #[test]
    fn worker_id_differs_per_install() {
        let a = worker_id(&tmpdir("inst_a"));
        let b = worker_id(&tmpdir("inst_b"));
        assert_ne!(a, b, "two installs must not share a lease identity");
    }

    #[test]
    fn worker_id_is_url_and_header_safe() {
        let id = worker_id(&tmpdir("safe"));
        assert!(id.len() <= 64, "server rejects >64 chars");
        assert!(id.chars().all(|c| c.is_ascii_alphanumeric() || c == '-'),
                "must be a legal HTTP header value: {id}");
    }

    #[test]
    fn inflight_roundtrips_and_clears() {
        let d = tmpdir("inflight");
        let c = open(&d).unwrap();
        assert_eq!(inflight(&c), None);
        set_inflight(&c, Some(42));
        assert_eq!(inflight(&c), Some(42));
        set_inflight(&c, None);
        assert_eq!(inflight(&c), None, "a finished job must leave no in-flight record");
    }

    #[test]
    fn inflight_survives_reopen() {
        let d = tmpdir("reopen");
        { let c = open(&d).unwrap(); set_inflight(&c, Some(7)); }
        let c2 = open(&d).unwrap();
        assert_eq!(inflight(&c2), Some(7), "a crash must leave the orphan discoverable");
    }
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd src-tauri && cargo test wr::state`
Expected: FAIL — `cannot find function worker_id in this scope` (and `open`, `set_inflight`, `inflight`).

- [ ] **Step 3: Implement**

Add above the `#[cfg(test)]` block in `src-tauri/src/wr/state.rs`:

```rust
/// Open (creating if needed) the WR service's local scratch DB.
/// Separate file from sync_outbox.db: different lifecycle, and a corrupt WR scratch
/// file must never take the run uploader down with it.
pub fn open(dir: &Path) -> Result<Connection, String> {
    std::fs::create_dir_all(dir).map_err(|e| format!("create {dir:?}: {e}"))?;
    let conn = Connection::open(dir.join("wr_service.db")).map_err(|e| e.to_string())?;
    conn.execute(
        "CREATE TABLE IF NOT EXISTS wr_local (key TEXT PRIMARY KEY, value TEXT)",
        [],
    ).map_err(|e| e.to_string())?;
    Ok(conn)
}

fn get(conn: &Connection, key: &str) -> Option<String> {
    conn.query_row("SELECT value FROM wr_local WHERE key=?1", [key], |r| r.get(0)).ok()
}

fn put(conn: &Connection, key: &str, value: Option<&str>) {
    match value {
        Some(v) => { let _ = conn.execute(
            "INSERT INTO wr_local(key,value) VALUES(?1,?2)
             ON CONFLICT(key) DO UPDATE SET value=excluded.value", [key, v]); }
        None => { let _ = conn.execute("DELETE FROM wr_local WHERE key=?1", [key]); }
    }
}

/// The wr_id currently being worked, if any. Present after a crash => its video file
/// is an orphan to sweep.
pub fn inflight(conn: &Connection) -> Option<i64> {
    get(conn, "inflight_wr_id").and_then(|s| s.parse().ok())
}

pub fn set_inflight(conn: &Connection, wr_id: Option<i64>) {
    put(conn, "inflight_wr_id", wr_id.map(|v| v.to_string()).as_deref());
}

/// Stable per-install lease identity. Generated once into `worker-id` beside the DB and
/// reused forever; a plain file (not the DB) so a scratch-DB reset can't silently change
/// our identity and orphan a live lease.
pub fn worker_id(dir: &Path) -> String {
    let _ = std::fs::create_dir_all(dir);
    let path = dir.join("worker-id");
    if let Ok(s) = std::fs::read_to_string(&path) {
        let s = s.trim().to_string();
        if !s.is_empty() { return s; }
    }
    let id = generate_worker_id();
    let _ = std::fs::write(&path, &id);
    id
}

/// 32 hex chars of OS entropy, with no new dependency.
///
/// `RandomState` is std's HashMap hasher seeder, and std seeds it from the OS RNG
/// (that is the whole point — it exists to make HashDoS attacks infeasible). Two
/// independently-constructed hashers therefore give 128 bits of OS-derived entropy.
/// This is a real entropy source, not a clock-derived PRNG.
///
/// Not crypto: nothing here is a secret. A collision would only mean two machines
/// contend for one lease, which is recoverable, not data loss.
fn generate_worker_id() -> String {
    use std::collections::hash_map::RandomState;
    use std::hash::{BuildHasher, Hasher};
    let a = RandomState::new().build_hasher().finish();
    let b = RandomState::new().build_hasher().finish();
    format!("{a:016x}{b:016x}")
}
```

Create `src-tauri/src/wr/mod.rs`:

```rust
//! The WR trail-extraction service: claim a world record from the Pi, download its
//! YouTube video, replay it through the detection engine, verify, upload the trail.
//!
//! Lives INSIDE pbenguin rather than as a second binary (spec §6): the app already
//! ships, updates, bundles the engine exe and holds the player token.

pub mod state;

/// Terminal outcomes reported to the Pi as `{"ok":false,"error":...}`. The Pi's
/// `attempts` counter walks a repeatedly-failing job to its cap and stops it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum WrError {
    /// No 1080p60 stream on this video — every engine ROI is a 1080p pixel coord.
    No1080p60,
    DownloadFailed(String),
    VideoUnavailable,
    /// The engine produced no trail: the minimap never locked.
    NoTrail,
    /// Detected time != the mkwrs record. Wrong/mislinked/truncated video.
    TimeMismatch { detected_ms: i64, expected_ms: i64 },
    /// The engine could not be spawned or died. OUR fault, not the video's.
    EngineFailed(String),
    Timeout,
    /// Aborted by a pause or the idle gate closing. NEVER reported to the Pi — the caller
    /// `release`s instead, which refunds the attempt. A deliberate stop must not look
    /// like a failure, or five pauses would exhaust the job's attempts.
    Cancelled,
}

impl WrError {
    /// The stable string the Pi stores in `wr_jobs.last_error`.
    pub fn reason(&self) -> String {
        match self {
            WrError::No1080p60 => "no_1080p60".into(),
            WrError::DownloadFailed(_) => "download_failed".into(),
            WrError::VideoUnavailable => "video_unavailable".into(),
            WrError::NoTrail => "no_trail".into(),
            WrError::TimeMismatch { detected_ms, expected_ms } =>
                format!("time_mismatch detected={detected_ms} expected={expected_ms}"),
            WrError::EngineFailed(m) => format!("engine_failed {m}"),
            WrError::Timeout => "timeout".into(),
            // Defensive: the caller must release() rather than fail() on a cancel. If this
            // string ever reaches the Pi, that contract was broken.
            WrError::Cancelled => "cancelled".into(),
        }
    }
}
```

In `src-tauri/src/lib.rs`, add beside the other `mod` declarations at the top of the file:

```rust
mod wr;
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd src-tauri && cargo test wr::state`
Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add src-tauri/src/wr/mod.rs src-tauri/src/wr/state.rs src-tauri/src/lib.rs
git commit -m "feat(wr): worker identity + local scratch state"
```

---

### Task 2: The engine driver — pure stdio decision logic

**Files:**
- Create: `src-tauri/src/wr/engine.rs`
- Modify: `src-tauri/src/wr/mod.rs`

**Interfaces:**
- Consumes: nothing.
- Produces: `wr::engine::Selections { course, character, costume, kart }`; `wr::engine::EngineDriver::new(Selections)`; `EngineDriver::on_line(&mut self, line: &str) -> Vec<String>` (JSON commands to write to the engine's stdin); `EngineDriver::finalized(&self) -> Option<&Finalized>`; `wr::engine::Finalized { total_time: Option<String>, points: Vec<[f64; 5]> }`; `wr::engine::time_to_ms(&str) -> Option<i64>`.

**This is the crux of the whole feature.** It is pure — no process, no I/O — so it is fully unit-testable. Task 3 wraps it in a process.

**Everything below was proven by a spike against a real WR video** (JaK, Mario Circuit, `1'02"934`). Do not redesign it:
- `set_selection` takes **top-level keys**, not `{field,value}`.
- WR videos cut from a menu straight into the countdown, so the detector parks in `UNKNOWN_RACE_ACTIVE` and **every tracker stays gated off** — the first spike run produced 0 minimap updates.
- **Do NOT `force_screen RACING` directly from there** — that path invalidates the run and *clears the recorded points* (finishes with `points: 0`). Force `RESET` (a legitimate start screen) **then** `RACING`.
- Force **only** on `UNKNOWN_RACE_ACTIVE`, so a video that does contain a real loading screen starts normally and is never forced.
- The engine prints non-JSON diagnostics to stdout, interleaved with the JSON.

- [ ] **Step 1: Write the failing test**

Create `src-tauri/src/wr/engine.rs`:

```rust
//! Drives the Python engine over stdio to extract a trail from a WR video.
//!
//! `EngineDriver` is PURE: feed it stdout lines, it returns stdin commands. All the
//! sequencing knowledge proven by the 2026-07-15 spike lives here and is unit-tested;
//! `run_video` (Task 3) is the thin process shell around it.

#[cfg(test)]
mod tests {
    use super::*;

    fn sel() -> Selections {
        Selections {
            course: "Mario Circuit".into(), character: "Toadette".into(),
            costume: Some("Explorer".into()), kart: Some("Baby Blooper".into()),
        }
    }

    #[test]
    fn injects_selections_on_ready_with_top_level_keys() {
        let mut d = EngineDriver::new(sel());
        let out = d.on_line(r#"{"type":"ready","version":"2.7.0"}"#);
        assert_eq!(out.len(), 1);
        let v: serde_json::Value = serde_json::from_str(&out[0]).unwrap();
        assert_eq!(v["type"], "set_selection");
        // TOP-LEVEL keys — main.py:179 reads msg.get("course"), NOT {field,value}.
        assert_eq!(v["course"], "Mario Circuit");
        assert_eq!(v["character"], "Toadette");
        assert_eq!(v["costume"], "Explorer");
        assert_eq!(v["kart"], "Baby Blooper");
    }

    #[test]
    fn omits_a_base_costume_rather_than_sending_null() {
        let mut d = EngineDriver::new(Selections {
            course: "Choco Mountain".into(), character: "Bowser".into(),
            costume: None, kart: Some("Reel Racer".into()),
        });
        let out = d.on_line(r#"{"type":"ready"}"#);
        let v: serde_json::Value = serde_json::from_str(&out[0]).unwrap();
        assert!(v.get("costume").is_none(), "base costume must be omitted, not null");
        assert_eq!(v["character"], "Bowser");
    }

    #[test]
    fn injects_only_once() {
        let mut d = EngineDriver::new(sel());
        assert_eq!(d.on_line(r#"{"type":"ready"}"#).len(), 1);
        assert_eq!(d.on_line(r#"{"type":"ready"}"#).len(), 0, "re-injecting would fight the tracker");
    }

    #[test]
    fn forces_reset_then_racing_on_unknown_race_active() {
        let mut d = EngineDriver::new(sel());
        d.on_line(r#"{"type":"ready"}"#);
        let out = d.on_line(r#"{"type":"screen_change","from":"UNKNOWN","to":"UNKNOWN_RACE_ACTIVE"}"#);
        assert_eq!(out.len(), 2, "must force RESET then RACING");
        let a: serde_json::Value = serde_json::from_str(&out[0]).unwrap();
        let b: serde_json::Value = serde_json::from_str(&out[1]).unwrap();
        assert_eq!(a["type"], "force_screen");
        // RESET first: it is in _RACE_START_SCREENS, so RESET->RACING is a genuine fresh
        // start. Forcing RACING directly from UNKNOWN_RACE_ACTIVE hits race.py:182 ->
        // _invalidate() -> _mm_rec.stop() -> the points are CLEARED.
        assert_eq!(a["screen"], "RESET");
        assert_eq!(b["screen"], "RACING");
    }

    #[test]
    fn forces_only_once_even_if_the_screen_flaps() {
        let mut d = EngineDriver::new(sel());
        d.on_line(r#"{"type":"ready"}"#);
        assert_eq!(d.on_line(r#"{"type":"screen_change","to":"UNKNOWN_RACE_ACTIVE"}"#).len(), 2);
        assert_eq!(d.on_line(r#"{"type":"screen_change","to":"UNKNOWN_RACE_ACTIVE"}"#).len(), 0);
    }

    #[test]
    fn never_forces_when_the_video_has_a_real_loading_screen() {
        let mut d = EngineDriver::new(sel());
        d.on_line(r#"{"type":"ready"}"#);
        let out = d.on_line(r#"{"type":"screen_change","from":"RESET","to":"RACING"}"#);
        assert!(out.is_empty(), "a genuine start must never be forced — forcing would invalidate it");
    }

    #[test]
    fn tolerates_the_engines_non_json_stdout_diagnostics() {
        let mut d = EngineDriver::new(sel());
        // The engine print()s these alongside the JPC stream (tracker.py:166 etc).
        let out = d.on_line("  [MinimapTracker] Seeded (1636,876) r=20 conf_thr=0.65");
        assert!(out.is_empty());
        assert!(d.finalized().is_none());
    }

    #[test]
    fn captures_run_finalized_with_its_trail() {
        let mut d = EngineDriver::new(sel());
        d.on_line(r#"{"type":"run_finalized","status":"finished","total_time":"1:02.934",
                      "points":[[14,1635,875,0.79,1],[114,1636,870,0.81,1]]}"#);
        let f = d.finalized().expect("must capture run_finalized");
        assert_eq!(f.total_time.as_deref(), Some("1:02.934"));
        assert_eq!(f.points.len(), 2);
        assert_eq!(f.points[0][0], 14.0);
        assert_eq!(f.points[0][1], 1635.0);
    }

    #[test]
    fn accepts_a_legacy_four_tuple_point() {
        let mut d = EngineDriver::new(sel());
        d.on_line(r#"{"type":"run_finalized","status":"finished","total_time":"1:02.934",
                      "points":[[14,1635,875,0.79]]}"#);
        let f = d.finalized().unwrap();
        assert_eq!(f.points[0][4], -1.0, "missing lap becomes the -1 sentinel");
    }

    #[test]
    fn time_to_ms_parses_the_engines_format() {
        assert_eq!(time_to_ms("1:02.934"), Some(62934));
        assert_eq!(time_to_ms("2:09.606"), Some(129606));
        assert_eq!(time_to_ms("0:18.213"), Some(18213));
        assert_eq!(time_to_ms(""), None);
        assert_eq!(time_to_ms("nonsense"), None);
    }
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd src-tauri && cargo test wr::engine`
Expected: FAIL — `cannot find type Selections in this scope`.

- [ ] **Step 3: Implement**

Add above the `#[cfg(test)]` block in `src-tauri/src/wr/engine.rs`:

```rust
use serde_json::json;

/// Engine DISPLAY names (not slugs). The caller maps slug -> display before constructing.
#[derive(Debug, Clone)]
pub struct Selections {
    pub course: String,
    pub character: String,
    /// None = base costume. Must be OMITTED from set_selection, not sent as null.
    pub costume: Option<String>,
    pub kart: Option<String>,
}

/// What the engine reported at the end of the run.
#[derive(Debug, Clone)]
pub struct Finalized {
    pub total_time: Option<String>,
    /// [t_ms, cx, cy, score, lap]; lap = -1.0 when the engine omitted it (legacy 4-tuple).
    pub points: Vec<[f64; 5]>,
}

/// Pure sequencer: stdout line in, stdin commands out.
pub struct EngineDriver {
    selections: Selections,
    injected: bool,
    forced: bool,
    finalized: Option<Finalized>,
}

impl EngineDriver {
    pub fn new(selections: Selections) -> Self {
        Self { selections, injected: false, forced: false, finalized: None }
    }

    pub fn finalized(&self) -> Option<&Finalized> { self.finalized.as_ref() }

    /// Feed one engine stdout line; returns JSON commands to write to its stdin.
    /// Non-JSON lines (the engine's print() diagnostics) yield nothing.
    pub fn on_line(&mut self, line: &str) -> Vec<String> {
        let v: serde_json::Value = match serde_json::from_str(line.trim()) {
            Ok(v) => v,
            Err(_) => return vec![],
        };
        match v.get("type").and_then(|t| t.as_str()) {
            Some("ready") if !self.injected => {
                self.injected = true;
                let mut m = serde_json::Map::new();
                m.insert("type".into(), json!("set_selection"));
                m.insert("course".into(), json!(self.selections.course));
                m.insert("character".into(), json!(self.selections.character));
                if let Some(c) = &self.selections.costume { m.insert("costume".into(), json!(c)); }
                if let Some(k) = &self.selections.kart { m.insert("kart".into(), json!(k)); }
                vec![serde_json::Value::Object(m).to_string()]
            }
            Some("screen_change")
                if !self.forced
                    && v.get("to").and_then(|t| t.as_str()) == Some("UNKNOWN_RACE_ACTIVE") =>
            {
                self.forced = true;
                // WR uploads cut from a menu straight into the countdown, so the detector
                // parks in UNKNOWN_RACE_ACTIVE and every tracker stays gated off.
                // RESET first (it is in _RACE_START_SCREENS) so RESET->RACING reads as a
                // genuine fresh start. Forcing RACING directly hits race.py:182 ->
                // _invalidate() -> _mm_rec.stop(), which CLEARS the points.
                vec![
                    json!({"type":"force_screen","screen":"RESET"}).to_string(),
                    json!({"type":"force_screen","screen":"RACING"}).to_string(),
                ]
            }
            Some("run_finalized") => {
                let points = v.get("points").and_then(|p| p.as_array()).map(|arr| {
                    arr.iter().filter_map(|p| {
                        let a = p.as_array()?;
                        if a.len() < 4 { return None; }
                        let n = |i: usize| a.get(i).and_then(|x| x.as_f64());
                        Some([n(0)?, n(1)?, n(2)?, n(3)?, n(4).unwrap_or(-1.0)])
                    }).collect()
                }).unwrap_or_default();
                self.finalized = Some(Finalized {
                    total_time: v.get("total_time").and_then(|t| t.as_str()).map(str::to_string),
                    points,
                });
                vec![]
            }
            _ => vec![],
        }
    }
}

/// Parse the engine's `M:SS.mmm` into milliseconds.
pub fn time_to_ms(s: &str) -> Option<i64> {
    let (m, rest) = s.split_once(':')?;
    let (sec, ms) = rest.split_once('.')?;
    let m: i64 = m.trim().parse().ok()?;
    let sec: i64 = sec.parse().ok()?;
    let ms: i64 = ms.parse().ok()?;
    if sec >= 60 || ms >= 1000 { return None; }
    Some(m * 60_000 + sec * 1_000 + ms)
}
```

In `src-tauri/src/wr/mod.rs`, add beside `pub mod state;`:

```rust
pub mod engine;
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd src-tauri && cargo test wr::engine`
Expected: PASS, 10 tests.

- [ ] **Step 5: Commit**

```bash
git add src-tauri/src/wr/engine.rs src-tauri/src/wr/mod.rs
git commit -m "feat(wr): pure engine stdio driver (set_selection + RESET->RACING bypass)"
```

---

### Task 3: Verification gate and retry tier

**Files:**
- Create: `src-tauri/src/wr/verify.rs`
- Modify: `src-tauri/src/wr/mod.rs`

**Interfaces:**
- Consumes: `wr::WrError` (Task 1); `wr::engine::{Finalized, time_to_ms}` (Task 2).
- Produces: `wr::verify::verify(&Finalized, expected_ms: i64) -> Result<Vec<[f64;5]>, WrError>`; `wr::verify::Tier` and `wr::verify::tier_for(attempt: i64) -> Tier`.

**Why this is the whole robustness story:** the engine reads the time off the video *independently of mkwrs*, so comparing them costs nothing and catches mislinked videos, mkwrs typos, re-uploads of the wrong run, and truncated videos — the exact failures the brief worried about.

- [ ] **Step 1: Write the failing test**

Create `src-tauri/src/wr/verify.rs`:

```rust
//! The free correctness gate (spec §6.6) + the retry escalation tiers (§6.4).

use super::engine::{time_to_ms, Finalized};
use super::WrError;

#[cfg(test)]
mod tests {
    use super::*;

    fn fin(total: Option<&str>, n: usize) -> Finalized {
        Finalized {
            total_time: total.map(str::to_string),
            points: (0..n).map(|i| [i as f64, 1635.0, 875.0, 0.79, 1.0]).collect(),
        }
    }

    #[test]
    fn accepts_an_exact_match_and_returns_the_trail() {
        // The real spike numbers: JaK, Mario Circuit, 1:02.934 == mkwrs 1'02"934.
        let pts = verify(&fin(Some("1:02.934"), 1732), 62934).expect("exact match must pass");
        assert_eq!(pts.len(), 1732);
    }

    #[test]
    fn rejects_a_mismatched_time_and_reports_both_values() {
        let err = verify(&fin(Some("1:02.934"), 100), 62000).unwrap_err();
        assert_eq!(err, WrError::TimeMismatch { detected_ms: 62934, expected_ms: 62000 });
        assert!(err.reason().starts_with("time_mismatch"));
    }

    #[test]
    fn rejects_an_empty_trail_as_no_trail_not_a_mismatch() {
        // The minimap never locked. Distinct from a wrong video: this one is worth retrying
        // at higher quality (tier 3), a mismatch never is.
        assert_eq!(verify(&fin(Some("1:02.934"), 0), 62934).unwrap_err(), WrError::NoTrail);
    }

    #[test]
    fn rejects_a_run_with_no_total_time() {
        // e.g. an invalidated run: the engine emits status finished with total_time null.
        assert_eq!(verify(&fin(None, 500), 62934).unwrap_err(), WrError::NoTrail);
    }

    #[test]
    fn checks_no_trail_before_the_time_so_the_reason_is_actionable() {
        assert_eq!(verify(&fin(Some("9:99.999"), 0), 62934).unwrap_err(), WrError::NoTrail);
    }

    #[test]
    fn tier_1_and_2_are_native_1080p_and_3_escalates_to_4k() {
        assert_eq!(tier_for(1), Tier::Native1080p60);
        assert_eq!(tier_for(2), Tier::Native1080p60, "attempt 2 = a plain re-download (throttling/403)");
        assert_eq!(tier_for(3), Tier::Downscaled4k);
        assert_eq!(tier_for(4), Tier::Native1080p60, "past the escalation, back off rather than re-pay 197MB");
    }

    #[test]
    fn cancelled_is_never_a_verification_outcome() {
        // verify() must only ever report on the CONTENT. Cancellation is the caller's
        // concern and is handled by release(), which refunds the attempt.
        let e = verify(&fin(Some("1:02.934"), 0), 62934).unwrap_err();
        assert_ne!(e, WrError::Cancelled);
        assert_eq!(e, WrError::NoTrail);
    }
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd src-tauri && cargo test wr::verify`
Expected: FAIL — `cannot find function verify in this scope`.

- [ ] **Step 3: Implement**

Add above the `#[cfg(test)]` block in `src-tauri/src/wr/verify.rs`:

```rust
/// Which source to download for this attempt (spec §6.4 retry tiers).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Tier {
    /// YouTube's native 1080p60. The default and almost always right.
    Native1080p60,
    /// 2160p60 downscaled to 1080p with ffmpeg lanczos. Measured to raise the median
    /// badge NCC 0.796 -> 0.829 but to produce an IDENTICAL trail, because
    /// calibrate_from_race scales its margin by (1 - median) so a better image tightens
    /// its own threshold. Costs 3.6x the bandwidth + a ~58s transcode, so it is worth it
    /// ONLY as a last resort for a video that produced no trail at all.
    Downscaled4k,
}

/// Escalate only for a genuinely marginal video. A time_mismatch must never escalate —
/// a wrong video is wrong at any bitrate; it needs a human, not more pixels.
pub fn tier_for(attempt: i64) -> Tier {
    if attempt == 3 { Tier::Downscaled4k } else { Tier::Native1080p60 }
}

/// The free correctness gate: the engine read the time off the video without consulting
/// mkwrs, so an exact match is strong evidence we processed the right video in full.
/// Returns the trail on success.
pub fn verify(f: &Finalized, expected_ms: i64) -> Result<Vec<[f64; 5]>, WrError> {
    // Order matters: "no trail" is the actionable reason and is retryable at a higher
    // tier; a bogus time on an empty run would otherwise mask it as a mismatch.
    if f.points.is_empty() { return Err(WrError::NoTrail); }
    let detected_ms = match f.total_time.as_deref().and_then(time_to_ms) {
        Some(ms) => ms,
        None => return Err(WrError::NoTrail),
    };
    if detected_ms != expected_ms {
        return Err(WrError::TimeMismatch { detected_ms, expected_ms });
    }
    Ok(f.points.clone())
}
```

In `src-tauri/src/wr/mod.rs`, add:

```rust
pub mod verify;
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd src-tauri && cargo test wr::verify`
Expected: PASS, 6 tests.

- [ ] **Step 5: Commit**

```bash
git add src-tauri/src/wr/verify.rs src-tauri/src/wr/mod.rs
git commit -m "feat(wr): verification gate + retry tiers"
```

---

### Task 4: Job types and the Pi HTTP client

**Files:**
- Create: `src-tauri/src/wr/job.rs`
- Modify: `src-tauri/src/wr/mod.rs`

**Interfaces:**
- Consumes: `wr::WrError` (Task 1).
- Produces: `wr::job::WrJob` (public fields per the claim JSON); `wr::job::parse_job(&str) -> Option<WrJob>`; `wr::job::slug_to_display(&str) -> String`; `wr::job::Client::new(server_url, token, worker_id)` with `claim() -> Result<Option<WrJob>, String>`, `heartbeat(wr_id)`, `release(wr_id)`, `complete(wr_id, &[[f64;5]])`, `fail(wr_id, &WrError)`.

**The slug → display-name mapping is load-bearing:** the Pi sends slugs (`baby_blooper`), the engine wants display names (`Baby Blooper`), because engine template keys are built from filenames via `_`→space + `.title()`.

- [ ] **Step 1: Write the failing test**

Create `src-tauri/src/wr/job.rs`:

```rust
//! The Pi's WR job API + the job payload.
//!
//! Auth is the ordinary PLAYER token (header only — a ?token= in a write URL would leak
//! into logs), plus X-Worker-Id for the per-MACHINE lease: a player token identifies a
//! person, and one person may run this on several PCs (spec §6).

use super::WrError;

#[cfg(test)]
mod tests {
    use super::*;

    // Verbatim from a live claim during the Plan 1 end-to-end verification.
    const LIVE: &str = r#"{"wr_id":6,"cc":150,"course_slug":"shy_guy_bazaar",
      "course_name":"Shy Guy Bazaar","video_url":"https://www.youtube.com/watch?v=wTZXUMhimbw",
      "record_ms":110449,"lap_splits_ms":null,"character_slug":"swoop","costume_slug":null,
      "kart_slug":"rob_hog","attempt":1,"lease_until":"2026-07-15 04:38:40"}"#;

    #[test]
    fn parses_a_real_claim_response() {
        let j = parse_job(LIVE).expect("must parse the live shape");
        assert_eq!(j.wr_id, 6);
        assert_eq!(j.record_ms, 110449);
        assert_eq!(j.attempt, 1);
        assert_eq!(j.character_slug, "swoop");
        assert_eq!(j.costume_slug, None, "null costume = base costume, NOT an error");
        assert_eq!(j.kart_slug.as_deref(), Some("rob_hog"));
    }

    #[test]
    fn parses_a_job_that_has_a_costume() {
        let j = parse_job(&LIVE.replace(r#""costume_slug":null"#, r#""costume_slug":"explorer""#)).unwrap();
        assert_eq!(j.costume_slug.as_deref(), Some("explorer"));
    }

    #[test]
    fn rejects_a_job_with_no_character_slug() {
        // The Pi's claim filters these out, but never trust the wire: without a character
        // there is no set_selection to build, so the engine cannot seed the minimap.
        let bad = LIVE.replace(r#""character_slug":"swoop""#, r#""character_slug":null"#);
        assert!(parse_job(&bad).is_none());
    }

    #[test]
    fn slug_to_display_matches_the_engines_template_keys() {
        // Engine keys come from filenames via `_`->space + .title() (selection.py:69).
        assert_eq!(slug_to_display("baby_blooper"), "Baby Blooper");
        assert_eq!(slug_to_display("toadette"), "Toadette");
        assert_eq!(slug_to_display("mario_circuit"), "Mario Circuit");
        assert_eq!(slug_to_display("w_twin_chopper"), "W Twin Chopper");
        assert_eq!(slug_to_display(""), "");
    }

    #[test]
    fn fail_reasons_are_the_stable_strings_the_pi_stores() {
        assert_eq!(WrError::NoTrail.reason(), "no_trail");
        assert_eq!(WrError::No1080p60.reason(), "no_1080p60");
        assert_eq!(WrError::Timeout.reason(), "timeout");
        assert_eq!(WrError::VideoUnavailable.reason(), "video_unavailable");
        assert!(WrError::EngineFailed("boom".into()).reason().starts_with("engine_failed"));
    }

    #[test]
    fn a_time_mismatch_reason_carries_both_numbers_for_a_human() {
        // This is the reason Paul reads when mkwrs links the wrong video, so it has to
        // say what we saw AND what was expected -- "time_mismatch" alone is useless.
        let r = WrError::TimeMismatch { detected_ms: 62934, expected_ms: 62000 }.reason();
        assert!(r.contains("62934") && r.contains("62000"), "unhelpful reason: {r}");
    }
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd src-tauri && cargo test wr::job`
Expected: FAIL — `cannot find function parse_job in this scope`.

- [ ] **Step 3: Implement**

Add above the `#[cfg(test)]` block in `src-tauri/src/wr/job.rs`:

```rust
#[derive(Debug, Clone)]
pub struct WrJob {
    pub wr_id: i64,
    pub cc: i64,
    pub course_slug: String,
    pub course_name: String,
    pub video_url: String,
    pub record_ms: i64,
    pub character_slug: String,
    /// None = base costume. Legitimate and the common case (24 of 30 live WRs).
    pub costume_slug: Option<String>,
    pub kart_slug: Option<String>,
    /// 1-based, post-increment. Drives the retry tier (verify::tier_for).
    pub attempt: i64,
}

/// Parse a claim response. None = unusable (not merely absent).
pub fn parse_job(body: &str) -> Option<WrJob> {
    let v: serde_json::Value = serde_json::from_str(body).ok()?;
    let s = |k: &str| v.get(k).and_then(|x| x.as_str()).map(str::to_string);
    let i = |k: &str| v.get(k).and_then(|x| x.as_i64());
    Some(WrJob {
        wr_id: i("wr_id")?,
        cc: i("cc").unwrap_or(150),
        course_slug: s("course_slug")?,
        course_name: s("course_name")?,
        video_url: s("video_url")?,
        record_ms: i("record_ms")?,
        // Required: no character => no set_selection => the minimap cannot seed.
        character_slug: s("character_slug")?,
        costume_slug: s("costume_slug"),
        kart_slug: s("kart_slug"),
        attempt: i("attempt").unwrap_or(1),
    })
}

/// slug -> the engine's DISPLAY name. Mirrors how engine template keys are derived from
/// filenames: `_` -> space, then title-case each word (selection.py:69).
pub fn slug_to_display(slug: &str) -> String {
    slug.split('_')
        .filter(|w| !w.is_empty())
        .map(|w| {
            let mut c = w.chars();
            match c.next() {
                Some(f) => f.to_uppercase().collect::<String>() + c.as_str(),
                None => String::new(),
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
}

/// The Pi's WR job API.
pub struct Client {
    base: String,
    token: String,
    worker_id: String,
    http: reqwest::blocking::Client,
}

impl Client {
    pub fn new(server_url: &str, token: &str, worker_id: &str) -> Self {
        Self {
            base: server_url.trim_end_matches('/').to_string(),
            token: token.to_string(),
            worker_id: worker_id.to_string(),
            http: reqwest::blocking::Client::new(),
        }
    }

    fn post(&self, path: &str, body: Option<String>) -> Result<reqwest::blocking::Response, String> {
        let mut rq = self.http
            .post(format!("{}{}", self.base, path))
            .bearer_auth(&self.token)               // header ONLY — never ?token=
            .header("X-Worker-Id", &self.worker_id) // per-MACHINE lease identity
            .timeout(std::time::Duration::from_secs(30));
        if let Some(b) = body {
            rq = rq.header("content-type", "application/json").body(b);
        }
        rq.send().map_err(|e| e.to_string())
    }

    /// Ok(None) = 204, nothing claimable right now.
    pub fn claim(&self) -> Result<Option<WrJob>, String> {
        let res = self.post("/v1/wr-jobs/claim", None)?;
        if res.status().as_u16() == 204 { return Ok(None); }
        if !res.status().is_success() { return Err(format!("claim: HTTP {}", res.status())); }
        let body = res.text().map_err(|e| e.to_string())?;
        parse_job(&body).map(Some).ok_or_else(|| format!("claim: unusable job: {body}"))
    }

    /// false = we no longer hold the lease (409). Stop working; the job is someone else's.
    pub fn heartbeat(&self, wr_id: i64) -> Result<bool, String> {
        Ok(self.post(&format!("/v1/wr-jobs/{wr_id}/heartbeat"), None)?.status().is_success())
    }

    /// Hand the job back voluntarily. The Pi REFUNDS the attempt, so a pause never
    /// counts against the cap (unlike a crash, where the lease just lapses).
    pub fn release(&self, wr_id: i64) -> Result<bool, String> {
        Ok(self.post(&format!("/v1/wr-jobs/{wr_id}/release"), None)?.status().is_success())
    }

    pub fn complete(&self, wr_id: i64, points: &[[f64; 5]]) -> Result<(), String> {
        // Wire format is [t_ms, cx, cy, score, lap?]; -1.0 is our "engine omitted lap"
        // sentinel and must go back as null, which the Pi stores as the codec's LAP_NULL.
        let pts: Vec<serde_json::Value> = points.iter().map(|p| {
            if p[4] < 0.0 { serde_json::json!([p[0], p[1], p[2], p[3]]) }
            else { serde_json::json!([p[0], p[1], p[2], p[3], p[4]]) }
        }).collect();
        let body = serde_json::json!({ "ok": true, "points": pts }).to_string();
        let res = self.post(&format!("/v1/wr-jobs/{wr_id}/result"), Some(body))?;
        if res.status().is_success() { Ok(()) } else { Err(format!("result: HTTP {}", res.status())) }
    }

    pub fn fail(&self, wr_id: i64, err: &WrError) -> Result<(), String> {
        let body = serde_json::json!({ "ok": false, "error": err.reason() }).to_string();
        let res = self.post(&format!("/v1/wr-jobs/{wr_id}/result"), Some(body))?;
        if res.status().is_success() { Ok(()) } else { Err(format!("result: HTTP {}", res.status())) }
    }
}
```

In `src-tauri/src/wr/mod.rs`, add:

```rust
pub mod job;
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd src-tauri && cargo test wr::job`
Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add src-tauri/src/wr/job.rs src-tauri/src/wr/mod.rs
git commit -m "feat(wr): job payload + Pi HTTP client"
```

---

### Task 5: yt-dlp — self-updating binary and download

**Files:**
- Create: `src-tauri/src/wr/ytdlp.rs`
- Modify: `src-tauri/src/wr/mod.rs`

**Interfaces:**
- Consumes: `wr::WrError` (Task 1); `wr::verify::Tier` (Task 3).
- Produces: `wr::ytdlp::format_selector(Tier) -> &'static str`; `wr::ytdlp::ensure(dir: &Path) -> Result<PathBuf, String>`; `wr::ytdlp::download(exe: &Path, url: &str, tier: Tier, dest: &Path) -> Result<(), WrError>`; `wr::ytdlp::classify_failure(stderr: &str) -> WrError`.

**Why self-updating:** YouTube breaks yt-dlp regularly — the spike hit a 403 mid-transfer AND an n-challenge failure. pbenguin releases are manual and infrequent, so a bundled copy would rot between them and this feature would die quietly (spec §6.4).

- [ ] **Step 1: Write the failing test**

Create `src-tauri/src/wr/ytdlp.rs`:

```rust
//! Self-updating yt-dlp + the download step.

use super::verify::Tier;
use super::WrError;
use std::path::{Path, PathBuf};

/// Official standalone Windows build. Pinned to the yt-dlp org's own releases.
const YTDLP_URL: &str = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe";

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn native_selector_demands_1080p60_and_prefers_avc1() {
        let s = format_selector(Tier::Native1080p60);
        // NEVER a hardcoded format id: ids are per-video and 299 does not exist on every
        // upload. Must be a selector expression.
        assert!(!s.contains("299"), "hardcoded format id would fail unpredictably");
        assert!(s.contains("height=1080"));
        assert!(s.contains("fps=60"));
        assert!(s.contains("vcodec^=avc1"), "avc1 preferred (cheapest decode), not required");
        assert!(s.contains('/'), "must have fallbacks");
    }

    #[test]
    fn four_k_selector_asks_for_2160p60() {
        let s = format_selector(Tier::Downscaled4k);
        assert!(s.contains("height=2160"));
        assert!(s.contains("fps=60"));
    }

    #[test]
    fn classifies_a_missing_1080p60_stream() {
        assert_eq!(classify_failure("ERROR: Requested format is not available"), WrError::No1080p60);
    }

    #[test]
    fn classifies_an_unavailable_video() {
        assert_eq!(classify_failure("ERROR: Video unavailable"), WrError::VideoUnavailable);
        assert_eq!(classify_failure("ERROR: Private video. Sign in"), WrError::VideoUnavailable);
        assert_eq!(classify_failure("This video has been removed by the uploader"),
                   WrError::VideoUnavailable);
    }

    #[test]
    fn a_403_is_download_failed_not_unavailable_because_it_is_retryable() {
        // Observed live: the 197MB 4K pull 403'd mid-transfer and completed on retry.
        // Misclassifying it as unavailable would waste the job's remaining attempts.
        let e = classify_failure("ERROR: unable to download video data: HTTP Error 403: Forbidden");
        assert!(matches!(e, WrError::DownloadFailed(_)), "403 must stay retryable, got {e:?}");
    }

    #[test]
    fn an_unrecognised_error_is_download_failed_and_keeps_the_text() {
        match classify_failure("ERROR: something nobody predicted") {
            WrError::DownloadFailed(s) => assert!(s.contains("nobody predicted")),
            other => panic!("expected DownloadFailed, got {other:?}"),
        }
    }
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd src-tauri && cargo test wr::ytdlp`
Expected: FAIL — `cannot find function format_selector in this scope`.

- [ ] **Step 3: Implement**

Add above the `#[cfg(test)]` block in `src-tauri/src/wr/ytdlp.rs`:

```rust
/// The download format, per tier.
///
/// A SELECTOR, never a format id: ids are per-video (the spike's 1080p60 avc1 was `299`
/// on one video and would not exist on another). 1080p is non-negotiable — every engine
/// ROI is a 1080p pixel coord and `_norm()` would rescale anything else and blur the
/// templates. avc1 is merely preferred (cheapest decode); VP9 is fine on a PC.
pub fn format_selector(tier: Tier) -> &'static str {
    match tier {
        Tier::Native1080p60 =>
            "bestvideo[height=1080][fps=60][vcodec^=avc1]/bestvideo[height=1080][fps=60]/bestvideo[height=1080]",
        Tier::Downscaled4k =>
            "bestvideo[height=2160][fps=60]",
    }
}

/// Map yt-dlp's stderr to a terminal reason. A 403 stays RETRYABLE.
pub fn classify_failure(stderr: &str) -> WrError {
    let s = stderr.to_ascii_lowercase();
    if s.contains("requested format is not available") { return WrError::No1080p60; }
    if s.contains("video unavailable") || s.contains("private video")
        || s.contains("has been removed") || s.contains("removed by the uploader") {
        return WrError::VideoUnavailable;
    }
    WrError::DownloadFailed(stderr.trim().chars().take(300).collect())
}

/// Path to a usable yt-dlp.exe, fetching it if absent. Callers should also re-`fetch`
/// when downloads start failing — a stale yt-dlp is the likeliest way this feature dies.
pub fn ensure(dir: &Path) -> Result<PathBuf, String> {
    let exe = dir.join("yt-dlp.exe");
    if exe.is_file() { return Ok(exe); }
    fetch(dir)
}

/// (Re)download the official standalone yt-dlp.exe.
pub fn fetch(dir: &Path) -> Result<PathBuf, String> {
    std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    let exe = dir.join("yt-dlp.exe");
    let tmp = dir.join("yt-dlp.exe.part");
    let bytes = reqwest::blocking::Client::new()
        .get(YTDLP_URL)
        .timeout(std::time::Duration::from_secs(180))
        .send().map_err(|e| format!("fetch yt-dlp: {e}"))?
        .error_for_status().map_err(|e| format!("fetch yt-dlp: {e}"))?
        .bytes().map_err(|e| format!("fetch yt-dlp: {e}"))?;
    std::fs::write(&tmp, &bytes).map_err(|e| e.to_string())?;
    // Rename last: a half-written exe must never be mistaken for a usable one.
    std::fs::rename(&tmp, &exe).map_err(|e| e.to_string())?;
    log::info!("[wr] fetched yt-dlp ({} bytes)", bytes.len());
    Ok(exe)
}

/// Download `url` to `dest`. Video only — audio is never fetched.
pub fn download(exe: &Path, url: &str, tier: Tier, dest: &Path) -> Result<(), WrError> {
    let out = std::process::Command::new(exe)
        .args([
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
        ])
        .output()
        .map_err(|e| WrError::DownloadFailed(format!("spawn yt-dlp: {e}")))?;
    if out.status.success() && dest.is_file() { return Ok(()); }
    Err(classify_failure(&String::from_utf8_lossy(&out.stderr)))
}
```

In `src-tauri/src/wr/mod.rs`, add:

```rust
pub mod ytdlp;
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd src-tauri && cargo test wr::ytdlp`
Expected: PASS, 6 tests.

- [ ] **Step 5: Commit**

```bash
git add src-tauri/src/wr/ytdlp.rs src-tauri/src/wr/mod.rs
git commit -m "feat(wr): self-updating yt-dlp + download with failure classification"
```

---

### Task 6: The process shell — run a video through the engine

**Files:**
- Modify: `src-tauri/src/wr/engine.rs`

**Interfaces:**
- Consumes: `EngineDriver`, `Selections`, `Finalized` (Task 2).
- Produces: `wr::engine::run_video(engine: &EnginePath, video: &Path, sel: Selections, timeout: Duration, cancel: &dyn Fn() -> bool) -> Result<Finalized, WrError>`; `wr::engine::EnginePath` with `EnginePath::resolve() -> EnginePath`.

**Engine facts proven by the spike — respect all three:**
- Processing is **wall-clock bound** (~the video's own duration): the trackers are rate-limited on real time. You cannot speed this up.
- **`--video-once` stops playback but does NOT exit the process.** The shell MUST reap the engine itself once `run_finalized` arrives, plus a hard timeout.
- The engine prints non-JSON diagnostics to stdout; `EngineDriver::on_line` already ignores them, but surface `[MinimapTracker]` lines to the log — `Badge template locked from seed frame` and `calibrated: ...` are the canaries for a healthy run.

- [ ] **Step 1: Write the failing test**

Append to `src-tauri/src/wr/engine.rs`'s `mod tests`:

```rust
    #[test]
    fn engine_path_dev_uses_the_python_module() {
        let p = EnginePath::Dev;
        let (prog, args) = p.command_parts();
        assert_eq!(prog, "python");
        assert_eq!(&args[..2], &["-m", "mkw_tracker"]);
    }

    /// The real proof. Ignored by default: it is wall-clock bound (~100s) and needs the
    /// fixture + a working `python -m mkw_tracker`.
    ///
    ///   cd src-tauri && cargo test wr::engine::tests::fixture -- --ignored --nocapture
    ///
    /// Expected (from the 2026-07-15 spike, exact): total_time 1:02.934, ~1732 points.
    #[test]
    #[ignore]
    fn fixture_video_yields_the_exact_known_trail() {
        let repo = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
        let video = repo.join("temp/wr_mario_circuit.mp4");
        assert!(video.is_file(), "fixture missing: {video:?} — see the plan's fixture note");

        let f = run_video(
            &EnginePath::Dev,
            &video,
            Selections {
                course: "Mario Circuit".into(), character: "Toadette".into(),
                costume: Some("Explorer".into()), kart: Some("Baby Blooper".into()),
            },
            std::time::Duration::from_secs(300),
            &|| false,
        ).expect("the fixture must produce a trail");

        assert_eq!(f.total_time.as_deref(), Some("1:02.934"), "must read the WR time exactly");
        assert!(f.points.len() > 1500, "expected ~1732 points, got {}", f.points.len());
        let ms = time_to_ms(f.total_time.as_deref().unwrap()).unwrap();
        assert_eq!(ms, 62934);
    }
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd src-tauri && cargo test wr::engine`
Expected: FAIL — `cannot find type EnginePath in this scope`. (The `#[ignore]`d test does not run.)

- [ ] **Step 3: Implement**

Add to `src-tauri/src/wr/engine.rs`, above the tests:

```rust
use super::WrError;
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

/// Where the engine lives. Mirrors lib.rs:48's debug/release split.
pub enum EnginePath {
    /// Debug: `python -m mkw_tracker` from the repo root.
    Dev,
    /// Release: the PyInstaller exe bundled beside us.
    Bundled(PathBuf),
}

impl EnginePath {
    pub fn resolve() -> EnginePath {
        #[cfg(debug_assertions)]
        { EnginePath::Dev }
        #[cfg(not(debug_assertions))]
        {
            let dir = std::env::current_exe().ok()
                .and_then(|p| p.parent().map(|d| d.to_path_buf()))
                .unwrap_or_default();
            EnginePath::Bundled(dir.join("bin/mkw-tracker-engine.exe"))
        }
    }

    pub fn command_parts(&self) -> (String, Vec<String>) {
        match self {
            EnginePath::Dev => ("python".into(), vec!["-m".into(), "mkw_tracker".into()]),
            EnginePath::Bundled(p) => (p.to_string_lossy().into_owned(), vec![]),
        }
    }
}

/// Replay `video` through a throwaway engine and return what it detected.
///
/// Uses std::process::Command, NOT tauri-plugin-shell: the shell plugin needs an
/// AppHandle and yields an async stream, which would drag Tauri into this module and
/// make it untestable. lib.rs keeps using the plugin for the LIVE engine.
///
/// `cancel` is polled between lines; returning true aborts (a pause, or the idle gate
/// closing because a race started). Aborting discards — tracking must happen in one
/// unbroken pass.
pub fn run_video(
    engine: &EnginePath,
    video: &Path,
    sel: Selections,
    timeout: Duration,
    cancel: &dyn Fn() -> bool,
) -> Result<Finalized, WrError> {
    let (prog, base_args) = engine.command_parts();
    let mut cmd = std::process::Command::new(prog);
    cmd.args(base_args)
        .arg("--video").arg(video)
        .arg("--video-once")
        .arg("--no-display")
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null());
    #[cfg(debug_assertions)]
    cmd.current_dir(Path::new(env!("CARGO_MANIFEST_DIR")).parent().expect("repo root"));

    let mut child = cmd.spawn()
        .map_err(|e| WrError::EngineFailed(format!("spawn: {e}")))?;
    let mut stdin = child.stdin.take().expect("piped stdin");
    let stdout = child.stdout.take().expect("piped stdout");

    let mut driver = EngineDriver::new(sel);
    let started = Instant::now();
    let mut result: Option<Finalized> = None;

    for line in BufReader::new(stdout).lines() {
        // Distinct reasons: a deliberate stop must never be reported as a timeout, or the
        // Pi's last_error would blame the video for something we chose to do.
        if cancel() { let _ = child.kill(); let _ = child.wait(); return Err(WrError::Cancelled); }
        if started.elapsed() > timeout {
            let _ = child.kill(); let _ = child.wait(); return Err(WrError::Timeout);
        }
        let line = match line { Ok(l) => l, Err(_) => break };

        // The engine's own diagnostics: these are the canaries for a healthy run.
        if line.contains("[MinimapTracker]") || line.contains("[ThresholdStore]") {
            log::info!("[wr engine] {}", line.trim());
        }

        for cmd in driver.on_line(&line) {
            if writeln!(stdin, "{cmd}").is_err() { break; }
            let _ = stdin.flush();
        }

        if driver.finalized().is_some() {
            result = driver.finalized().cloned();
            break;   // --video-once stops PLAYBACK but never exits: we must reap it.
        }
    }

    let _ = child.kill();
    let _ = child.wait();
    result.ok_or(WrError::NoTrail)
}
```

- [ ] **Step 4: Run to verify the unit tests pass**

Run: `cd src-tauri && cargo test wr::engine`
Expected: PASS, 11 tests (the fixture test reports as ignored).

- [ ] **Step 5: Run the fixture test — the real proof**

Run: `cd src-tauri && cargo test wr::engine::tests::fixture -- --ignored --nocapture`
Expected: PASS in ~100 s, logging `[MinimapTracker] Badge template locked from seed frame` and `calibrated: ... thr=0.715`, asserting `total_time == "1:02.934"` and >1500 points.

**If it fails, do not "fix" the assertions** — they are measured facts from a real run. Report what you saw and STOP.

- [ ] **Step 6: Commit**

```bash
git add src-tauri/src/wr/engine.rs
git commit -m "feat(wr): engine process shell, proven against the fixture video"
```

---

### Task 7: The work loop and a dev command

**Files:**
- Create: `src-tauri/src/wr/service.rs`
- Modify: `src-tauri/src/wr/mod.rs`, `src-tauri/src/lib.rs`

**Interfaces:**
- Consumes: everything above.
- Produces: `wr::service::process_one(cfg: &ServiceCfg, cancel: &dyn Fn() -> bool) -> Outcome`; `wr::service::ServiceCfg { server_url, token, data_dir, engine }`; `wr::service::Outcome { Idle, Completed(i64), Failed(i64, WrError), Released(i64), Error(String) }`; the Tauri command `wr_process_one`.

**Scope:** exactly ONE job, then return. No polling loop, no gate, no tray — Plan 3 adds those. Keeping it to one job means the dev command is a clean end-to-end probe.

- [ ] **Step 1: Write the failing test**

Create `src-tauri/src/wr/service.rs`:

```rust
//! Compose one full job: claim -> download -> process -> verify -> upload -> cleanup.
//!
//! Deliberately ONE job per call. The polling loop and the idle gate (spec §6.2 — WR work
//! must never run while a race is being tracked) belong to Plan 3, which supplies
//! `cancel`.

use super::engine::{self, EnginePath, Selections};
use super::{job, state, verify, ytdlp, WrError};
use std::path::PathBuf;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn selections_map_slugs_to_engine_display_names() {
        let j = job::parse_job(r#"{"wr_id":1,"cc":150,"course_slug":"mario_circuit",
            "course_name":"Mario Circuit","video_url":"u","record_ms":62934,
            "character_slug":"toadette","costume_slug":"explorer","kart_slug":"baby_blooper",
            "attempt":1}"#).unwrap();
        let s = selections_for(&j);
        assert_eq!(s.course, "Mario Circuit");
        assert_eq!(s.character, "Toadette");
        assert_eq!(s.costume.as_deref(), Some("Explorer"));
        assert_eq!(s.kart.as_deref(), Some("Baby Blooper"));
    }

    #[test]
    fn a_base_costume_stays_none_so_set_selection_omits_it() {
        let j = job::parse_job(r#"{"wr_id":1,"cc":150,"course_slug":"choco_mountain",
            "course_name":"Choco Mountain","video_url":"u","record_ms":1,
            "character_slug":"bowser","costume_slug":null,"kart_slug":"reel_racer",
            "attempt":1}"#).unwrap();
        assert_eq!(selections_for(&j).costume, None);
    }

    #[test]
    fn course_name_is_used_verbatim_not_derived_from_the_slug() {
        // minimap_seeds keys on the EU display name with apostrophes stripped
        // ("Warios Galleon"), which slug_to_display would also produce -- but the Pi
        // already sends the exact display name, so use it and don't re-derive.
        let j = job::parse_job(r#"{"wr_id":1,"cc":150,"course_slug":"warios_galleon",
            "course_name":"Warios Galleon","video_url":"u","record_ms":1,
            "character_slug":"bowser","costume_slug":null,"kart_slug":null,"attempt":1}"#).unwrap();
        assert_eq!(selections_for(&j).course, "Warios Galleon");
    }

    #[test]
    fn video_path_is_per_job_so_two_jobs_cannot_collide() {
        let d = std::env::temp_dir();
        assert_ne!(video_path(&d, 6), video_path(&d, 7));
        assert!(video_path(&d, 6).to_string_lossy().contains("6"));
    }
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd src-tauri && cargo test wr::service`
Expected: FAIL — `cannot find function selections_for in this scope`.

- [ ] **Step 3: Implement**

Add above the `#[cfg(test)]` block in `src-tauri/src/wr/service.rs`:

```rust
pub struct ServiceCfg {
    pub server_url: String,
    pub token: String,
    pub data_dir: PathBuf,
    pub engine: EnginePath,
}

#[derive(Debug)]
pub enum Outcome {
    /// Nothing claimable (204).
    Idle,
    Completed(i64),
    Failed(i64, WrError),
    /// Cancelled mid-job: discarded and handed back. The Pi refunds the attempt.
    Released(i64),
    /// Something went wrong before/around the job itself. Not reported to the Pi.
    Error(String),
}

/// The Pi sends slugs; the engine wants DISPLAY names. `course_name` arrives already
/// display-shaped, so use it verbatim rather than re-deriving from the slug.
fn selections_for(j: &job::WrJob) -> Selections {
    Selections {
        course: j.course_name.clone(),
        character: job::slug_to_display(&j.character_slug),
        costume: j.costume_slug.as_deref().map(job::slug_to_display),
        kart: j.kart_slug.as_deref().map(job::slug_to_display),
    }
}

fn video_path(dir: &std::path::Path, wr_id: i64) -> PathBuf {
    dir.join(format!("wr-{wr_id}.mp4"))
}

/// Delete a job's video. Called on EVERY terminal outcome — a 98s video is ~55MB, so the
/// 30-course catch-up would leave ~1.6GB behind if this ever slipped.
fn cleanup(dir: &std::path::Path, wr_id: i64) {
    let _ = std::fs::remove_file(video_path(dir, wr_id));
}

/// Delete any video left behind by a crash. Cheap to re-download (~6s), so never resume.
pub fn sweep_orphans(dir: &std::path::Path) {
    let Ok(entries) = std::fs::read_dir(dir) else { return };
    for e in entries.flatten() {
        let name = e.file_name().to_string_lossy().to_string();
        if name.starts_with("wr-") && name.ends_with(".mp4") {
            log::info!("[wr] sweeping orphaned {name}");
            let _ = std::fs::remove_file(e.path());
        }
    }
}

/// Claim and fully process one job.
pub fn process_one(cfg: &ServiceCfg, cancel: &dyn Fn() -> bool) -> Outcome {
    let conn = match state::open(&cfg.data_dir) { Ok(c) => c, Err(e) => return Outcome::Error(e) };
    let worker = state::worker_id(&cfg.data_dir);
    sweep_orphans(&cfg.data_dir);

    let client = job::Client::new(&cfg.server_url, &cfg.token, &worker);
    let j = match client.claim() {
        Ok(Some(j)) => j,
        Ok(None) => return Outcome::Idle,
        Err(e) => return Outcome::Error(e),
    };
    state::set_inflight(&conn, Some(j.wr_id));
    log::info!("[wr] claimed wr_id={} {} attempt={}", j.wr_id, j.course_slug, j.attempt);

    let outcome = run_job(cfg, &client, &j, cancel);

    cleanup(&cfg.data_dir, j.wr_id);
    state::set_inflight(&conn, None);
    outcome
}

fn run_job(cfg: &ServiceCfg, client: &job::Client, j: &job::WrJob,
           cancel: &dyn Fn() -> bool) -> Outcome {
    let tier = verify::tier_for(j.attempt);
    let dest = video_path(&cfg.data_dir, j.wr_id);

    let exe = match ytdlp::ensure(&cfg.data_dir) { Ok(p) => p, Err(e) => return Outcome::Error(e) };
    if let Err(e) = ytdlp::download(&exe, &j.video_url, tier, &dest) {
        // A download failure is the classic symptom of a stale yt-dlp. Refresh once and
        // retry before burning the job's attempt on our own rot.
        log::warn!("[wr] download failed ({}), refreshing yt-dlp and retrying once", e.reason());
        let retry = ytdlp::fetch(&cfg.data_dir)
            .map_err(|fe| WrError::DownloadFailed(fe))
            .and_then(|exe2| ytdlp::download(&exe2, &j.video_url, tier, &dest));
        if let Err(e2) = retry {
            let _ = client.fail(j.wr_id, &e2);
            return Outcome::Failed(j.wr_id, e2);
        }
    }
    if cancel() { let _ = client.release(j.wr_id); return Outcome::Released(j.wr_id); }

    // Wall-clock bound (~the video's own length). 5 min covers every WR with room to spare.
    let finalized = match engine::run_video(
        &cfg.engine, &dest, selections_for(j), std::time::Duration::from_secs(300), cancel) {
        Ok(f) => f,
        // Match the variant, don't re-poll cancel(): a genuine timeout that happens to
        // land just as the user pauses must still be reported as a timeout.
        // release() refunds the attempt; fail() deliberately does not.
        Err(WrError::Cancelled) => { let _ = client.release(j.wr_id); return Outcome::Released(j.wr_id); }
        Err(e) => { let _ = client.fail(j.wr_id, &e); return Outcome::Failed(j.wr_id, e); }
    };

    match verify::verify(&finalized, j.record_ms) {
        Ok(points) => match client.complete(j.wr_id, &points) {
            Ok(()) => { log::info!("[wr] wr_id={} uploaded {} points", j.wr_id, points.len());
                        Outcome::Completed(j.wr_id) }
            Err(e) => Outcome::Error(e),
        },
        Err(e) => {
            log::warn!("[wr] wr_id={} rejected: {}", j.wr_id, e.reason());
            let _ = client.fail(j.wr_id, &e);
            Outcome::Failed(j.wr_id, e)
        }
    }
}
```

In `src-tauri/src/wr/mod.rs`, add:

```rust
pub mod service;
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd src-tauri && cargo test wr`
Expected: PASS — all wr tests (the fixture test ignored).

- [ ] **Step 5: Add the dev command**

Append to `src-tauri/src/wr/mod.rs`:

```rust
/// DEV ONLY: claim and process exactly one job, then report. Plan 3 replaces this with
/// the real loop behind the settings toggle + idle gate.
///
/// Takes `server_url`/`token` explicitly rather than reading sync.rs's CONFIG, so a probe
/// can never accidentally claim from the real Pi.
#[tauri::command]
pub async fn wr_process_one(app: tauri::AppHandle, server_url: String, token: String)
    -> Result<String, String> {
    use tauri::Manager;
    let dir = app.path().app_data_dir().map_err(|e| e.to_string())?.join("wr");
    tauri::async_runtime::spawn_blocking(move || {
        let cfg = service::ServiceCfg {
            server_url, token, data_dir: dir, engine: engine::EnginePath::resolve(),
        };
        format!("{:?}", service::process_one(&cfg, &|| false))
    }).await.map_err(|e| e.to_string())
}
```

In `src-tauri/src/lib.rs`, add `wr::wr_process_one` to the `invoke_handler` list beside `sync::sync_set_config`.

- [ ] **Step 6: Verify it builds and commit**

Run: `cd src-tauri && cargo build && cargo test wr`
Expected: builds clean; all wr unit tests pass.

```bash
git add src-tauri/src/wr/service.rs src-tauri/src/wr/mod.rs src-tauri/src/lib.rs
git commit -m "feat(wr): one-shot job loop + dev command"
```

---

## Manual verification

The fixture test (Task 6) proves the engine half. This proves the whole chain against a real Pi.

Use a **scratch Pi DB**, never `~/mkw-data/mkw.db`:

```bash
# terminal 1 — a throwaway Pi with real WR data
cd pi
export MKW_DB=/tmp/wrprobe.db && rm -f /tmp/wrprobe.db /tmp/wrprobe.db-wal /tmp/wrprobe.db-shm
npx tsx src/scripts/scrapeWr.ts          # NOTE: seed `courses` + an active `seasons` row
npx tsx src/scripts/mintToken.ts Paul    # copy the token
PORT=8799 MKWRS_MAX_INTERVAL_SEC=0 MKWRS_HISTORY_ENABLED=0 npm run dev
```

A bare DB has **no `courses` rows** (they come from `server/importer.py`) and **no active season**, so `scrapeWr` maps nothing and `server.ts` won't boot. Seed both first — see the Plan 1 ledger (`.superpowers/sdd/progress.md`) for the exact snippets.

Then from the pbenguin dev app's devtools console:

```js
await __TAURI__.core.invoke('wr_process_one', {
  serverUrl: 'http://127.0.0.1:8799', token: '<paste>'
})
```

Expect `Completed(<wr_id>)` after ~2-3 minutes (download ~10s, processing is wall-clock bound). Then confirm the trail is publicly readable:

```bash
curl -s "http://127.0.0.1:8799/v1/wr-trails?course=<the course slug>" | head -c 300
```

Expect a `points` array of 4-tuples. **A `Failed(_, TimeMismatch{..})` is a real result, not a bug** — it means the engine and mkwrs disagree about that video, which is exactly what the gate is for. Try another course.

## What this plan deliberately does not do

- **No tray, no settings, no autostart** — Plan 3.
- **No idle gate.** `process_one` takes a `cancel` closure and Plan 3 supplies one backed by the live tracker's screen state. This plan always passes `|| false`.
- **No polling loop.** One job per call.
- **No client display** — Plan 4.
- **No engine changes.** The spike proved none are needed.
