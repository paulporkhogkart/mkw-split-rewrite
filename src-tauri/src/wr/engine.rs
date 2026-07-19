//! Drives the Python engine over stdio to extract a trail from a WR video.
//!
//! `EngineDriver` is PURE: feed it stdout lines, it returns stdin commands. All the
//! sequencing knowledge proven by the 2026-07-15 spike lives here and is unit-tested;
//! `run_video` (Task 3) is the thin process shell around it.

use super::WrError;
use serde_json::json;
use std::collections::VecDeque;
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant};

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
    /// The engine's own run status (e.g. "finished", "reset", "dnf"). Surfaced in the
    /// rejection log line so a run with no usable trail says WHY ("reset"/"dnf") instead
    /// of a bare, unhelpful "no trail".
    pub status: Option<String>,
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
                    status: v.get("status").and_then(|s| s.as_str()).map(str::to_string),
                    points,
                });
                vec![]
            }
            _ => vec![],
        }
    }
}

/// Parse the engine's `M:SS.mmm` into milliseconds. Strict on purpose: this is a
/// machine-generated format, so anything that is not exactly that shape is a signal
/// something changed, not something to guess at. A plausible-but-wrong number here
/// would silently fail verification against a GOOD video.
pub fn time_to_ms(s: &str) -> Option<i64> {
    let (m, rest) = s.trim().split_once(':')?;
    let (sec, ms) = rest.split_once('.')?;
    // Exact field widths: "1:02.934". A 1- or 2-digit fraction is NOT milliseconds, and
    // parsing it as though it were reads "9" as 9ms rather than 900ms.
    if sec.len() != 2 || ms.len() != 3 { return None; }
    // All-ASCII-digit checks subsume the sign checks: '-' and '+' are not digits, so
    // negatives cannot read as plausible times. They also reject unicode digits and
    // whitespace inside a field, which `parse()` would otherwise accept or mangle.
    if m.is_empty() || !m.bytes().all(|b| b.is_ascii_digit()) { return None; }
    if !sec.bytes().all(|b| b.is_ascii_digit()) { return None; }
    if !ms.bytes().all(|b| b.is_ascii_digit()) { return None; }
    let m: i64 = m.parse().ok()?;
    let sec: i64 = sec.parse().ok()?;
    let ms: i64 = ms.parse().ok()?;
    if sec >= 60 { return None; }
    Some(m * 60_000 + sec * 1_000 + ms)
}

/// Where the engine lives. Mirrors lib.rs:48's debug/release split.
pub enum EnginePath {
    /// Debug: `python -m mkw_tracker` from the repo root. Only ever constructed when
    /// `debug_assertions` is ON (see `resolve` below) — so a `cargo build --release`
    /// never constructs it and dead_code would otherwise flag it there; pre-existing,
    /// symmetric with `Bundled` below.
    #[allow(dead_code)]
    Dev,
    /// Release: the PyInstaller exe bundled beside us. Only ever constructed when
    /// `debug_assertions` is off (see `resolve` below), so a plain debug `cargo build`/
    /// `cargo test` never constructs it and dead_code would otherwise flag it — it IS
    /// used, just not in the profile that flags it.
    #[allow(dead_code)]
    Bundled(PathBuf),
    /// Test-only: an arbitrary program + args. `Bundled` takes no args, so it cannot
    /// express a deliberately-wedged child (`python -c "time.sleep(60)"`) — and a wedged
    /// child is the ONLY way to test the watchdog, since the fixture keeps talking.
    #[cfg(test)]
    Custom(String, Vec<String>),
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
            #[cfg(test)]
            EnginePath::Custom(prog, args) => (prog.clone(), args.clone()),
        }
    }
}

/// Replay `video` through a throwaway engine and return what it detected.
///
/// Uses std::process::Command, NOT tauri-plugin-shell: the shell plugin needs an
/// AppHandle and yields an async stream, which would drag Tauri into this module and
/// make it untestable. lib.rs keeps using the plugin for the LIVE engine.
///
/// `cancel` is polled by the watchdog every 250ms; returning true aborts (a pause, or the
/// idle gate closing because a race started). Aborting discards — tracking must happen in
/// one unbroken pass.
///
/// `cancel` is `+ Sync` because the watchdog thread polls it: it CANNOT be polled from the
/// read loop, which is exactly where it would never run. See the watchdog comment below.
pub fn run_video(
    engine: &EnginePath,
    video: &Path,
    sel: Selections,
    timeout: Duration,
    cancel: &(dyn Fn() -> bool + Sync),
) -> Result<Finalized, WrError> {
    let (prog, base_args) = engine.command_parts();
    let mut cmd = std::process::Command::new(prog);
    cmd.args(base_args)
        .arg("--video").arg(video)
        .arg("--video-once")
        .arg("--no-display")
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        // Piped, NOT null: a Python crash's stderr is the only diagnostic we get (there is
        // no console to inherit into — this is a GUI app). Piped-without-draining would
        // DEADLOCK the moment the engine fills the pipe buffer (~4-8KB) — that is exactly
        // why `null` was the safe choice before — so a dedicated drain thread below empties
        // it continuously into a bounded ring buffer.
        .stderr(std::process::Stdio::piped());
    // GUI-subsystem parent + console-subsystem child = a visible console window per
    // spawn unless suppressed (plugin-shell does the same for the live engine).
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt as _;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    #[cfg(debug_assertions)]
    cmd.current_dir(Path::new(env!("CARGO_MANIFEST_DIR")).parent().expect("repo root"));

    let mut child = cmd.spawn()
        .map_err(|e| WrError::EngineFailed(format!("spawn: {e}")))?;
    let mut stdin = child.stdin.take().expect("piped stdin");
    let stdout = child.stdout.take().expect("piped stdout");
    let stderr = child.stderr.take().expect("piped stderr");

    let started = Instant::now();
    let child = Mutex::new(child);
    let done = AtomicBool::new(false);
    let cancelled = AtomicBool::new(false);
    let timed_out = AtomicBool::new(false);
    // Bounded ring of the engine's last STDERR_TAIL_LINES stderr lines: the drain thread
    // pops the oldest line whenever it would exceed the cap. This bounds LINE COUNT, not
    // bytes — `BufReader::lines()` accumulates one newline-free line unboundedly, so a
    // single giant line with no `\n` would still grow memory (and the log file below)
    // without limit. Safe here because the only producer is the Python engine, which never
    // emits stderr without newlines (print()/logging always terminate lines, and Python
    // tracebacks are always multi-line) — but that is an assumption about the producer, not
    // a bound the ring enforces on its own.
    const STDERR_TAIL_LINES: usize = 50;
    let stderr_tail: Mutex<VecDeque<String>> = Mutex::new(VecDeque::new());

    let result = std::thread::scope(|s| {
        // WATCHDOG. The read loop below blocks inside read_line, which returns only on a
        // line or on EOF. Checking the timeout there is useless: a wedged engine sends
        // neither. Its heartbeat is emitted from inside its own synchronous frame loop
        // (main.py:1416 — sidecar.py's writer thread merely drains a queue that loop
        // fills), so the very failure this timeout exists to catch — a stuck cv2.read() —
        // silences the heartbeat too, and read_line would block forever.
        //
        // Only an independent thread can enforce it. Killing the child closes its stdout,
        // which unblocks the reader with EOF and lets the loop unwind normally.
        s.spawn(|| {
            while !done.load(Ordering::Relaxed) {
                if cancel() {
                    cancelled.store(true, Ordering::Relaxed);
                    let _ = child.lock().unwrap().kill();
                    return;
                }
                if started.elapsed() > timeout {
                    timed_out.store(true, Ordering::Relaxed);
                    let _ = child.lock().unwrap().kill();
                    return;
                }
                std::thread::sleep(Duration::from_millis(250));
            }
        });

        // STDERR DRAIN. Runs for as long as the child's stderr stays open. Without this
        // thread, `Stdio::piped()` above would deadlock rather than help the moment the
        // engine writes enough to fill the OS pipe buffer.
        s.spawn(|| {
            for line in BufReader::new(stderr).lines() {
                let Ok(line) = line else { break };
                let mut tail = stderr_tail.lock().unwrap();
                if tail.len() >= STDERR_TAIL_LINES { tail.pop_front(); }
                tail.push_back(line);
            }
        });

        // The read loop never locks `child`, so it cannot contend with the watchdog.
        let mut driver = EngineDriver::new(sel);
        let mut result: Option<Finalized> = None;
        for line in BufReader::new(stdout).lines() {
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
        // Retire the watchdog, then kill/reap the child NOW — still inside the scope.
        // --video-once stops playback but never exits, and the stderr drain thread above
        // stays blocked reading its pipe until that pipe closes. Deferring the kill to
        // after this closure (as before stderr existed) would deadlock `thread::scope`'s
        // implicit join on that thread at the end of this block.
        done.store(true, Ordering::Relaxed);
        let _ = child.lock().unwrap().kill();
        result
    });

    let mut child = child.into_inner().unwrap_or_else(|e| e.into_inner());
    let _ = child.kill();
    let _ = child.wait();

    let stderr_tail: Vec<String> =
        stderr_tail.into_inner().unwrap_or_else(|e| e.into_inner()).into_iter().collect();
    let label = video.file_name().and_then(|n| n.to_str()).unwrap_or("video").to_string();

    // Distinguish cancel/timeout/no-trail: a cancel must NEVER be reported to the server as
    // a failure (the caller release()s, refunding the attempt); a timeout must. Both are
    // read from flags the watchdog actually set, not re-derived from elapsed() — a late but
    // legitimate EOF (an engine crash at 299.9s of a 300s budget) is NoTrail-shaped, not a
    // timeout we imposed.
    let outcome = match result {
        Some(f) => Ok(f),
        None if cancelled.load(Ordering::Relaxed) => Err(WrError::Cancelled),
        None if timed_out.load(Ordering::Relaxed) => Err(WrError::Timeout),
        None => {
            // A genuine "no trail" (minimap never locked) is a SILENT clean exit — nothing
            // on stderr. If the engine wrote to stderr AND produced no trail, that is an
            // engine failure (a crash): OUR fault, not the video's. Keeping it a distinct
            // variant from NoTrail gives the Pi's `last_error` an honest signal (a crash we
            // should go fix vs a video that's genuinely hard to track) and makes the crash
            // visible in logs instead of it quietly reading as just another untrackable
            // video.
            let joined = stderr_tail.join("\n");
            log::error!("[wr engine] {label}: produced no trail; captured stderr tail:\n{}",
                if joined.is_empty() { "<empty>" } else { &joined });
            if stderr_tail.is_empty() {
                Err(WrError::NoTrail)
            } else if joined.contains("unrecognized arguments") {
                // argparse's exact phrase for flags it doesn't know: the bundled engine
                // predates --video/--video-once. A worker problem, not a video problem —
                // the caller refunds the attempt and parks instead of burning the queue.
                Err(WrError::EngineIncompatible(joined))
            } else {
                Err(WrError::EngineFailed(joined))
            }
        }
    };

    // The bounded log file: ONE file, always fully overwritten (never appended), so it can
    // never accumulate across runs — it always reflects only the most recent run. Skipped
    // when `video` has no real parent directory (e.g. a bare relative filename in a test),
    // so tests never scatter a stray log file into the working directory.
    if let Some(dir) = video.parent().filter(|p| !p.as_os_str().is_empty()) {
        let status = match &outcome { Ok(f) => f.status.as_deref(), Err(_) => None };
        write_stderr_log(dir, &label, status, &stderr_tail);
    }

    outcome
}

/// Overwrite (never append to) `<dir>/engine-stderr.log` with this run's captured stderr
/// tail. A single file, replaced whole every call, capped at whatever `tail` already was
/// bounded to by the ring buffer above — so it can roll over run after run without ever
/// growing, per-job files, or any log-rotation machinery.
fn write_stderr_log(dir: &Path, label: &str, status: Option<&str>, tail: &[String]) {
    let unix_ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let mut out = format!("=== {label} status={} unix_ts={unix_ts} ===\n",
                           status.unwrap_or("<none>"));
    if tail.is_empty() {
        out.push_str("<no stderr output>\n");
    } else {
        for line in tail { out.push_str(line); out.push('\n'); }
    }
    let path = dir.join("engine-stderr.log");
    if let Err(e) = std::fs::write(&path, out) {
        log::warn!("[wr] could not write {path:?}: {e}");
    }
}

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
        assert_eq!(f.status.as_deref(), Some("finished"),
            "status must be captured, not dropped — it's what lets a rejection log say \
             'reset'/'dnf' instead of a bare, unhelpful 'no trail'");
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

    #[test]
    fn engine_path_dev_uses_the_python_module() {
        let p = EnginePath::Dev;
        let (prog, args) = p.command_parts();
        assert_eq!(prog, "python");
        assert_eq!(&args[..2], &["-m", "mkw_tracker"]);
    }

    /// A child that produces NO stdout and never exits — the shape of a wedged engine.
    /// The engine's heartbeat is emitted from inside its synchronous frame loop
    /// (main.py:1416), so the very failure a hard timeout exists to catch — a stuck
    /// cv2.read() — silences the heartbeat too. The fixture CANNOT test this: it talks
    /// every 0.2s, so it exercises the same blocked read and looks like proof.
    fn wedged_child() -> EnginePath {
        EnginePath::Custom(
            "python".into(),
            vec!["-c".into(), "import time; time.sleep(60)".into()],
        )
    }

    #[test]
    fn timeout_fires_against_a_silent_child_that_never_exits() {
        let started = std::time::Instant::now();
        let err = run_video(
            &wedged_child(),
            std::path::Path::new("unused.mp4"),
            sel(),
            std::time::Duration::from_secs(2),
            &|| false,
        )
        .expect_err("a wedged engine must time out, not hang forever");
        // Without a watchdog, read_line blocks forever and we never get here at all.
        assert!(matches!(err, WrError::Timeout), "expected Timeout, got {err:?}");
        assert!(
            started.elapsed() < std::time::Duration::from_secs(15),
            "the timeout must actually fire; took {:?}",
            started.elapsed()
        );
    }

    #[test]
    fn cancel_fires_against_a_silent_child_and_is_not_reported_as_a_timeout() {
        let started = std::time::Instant::now();
        let err = run_video(
            &wedged_child(),
            std::path::Path::new("unused.mp4"),
            sel(),
            // Long timeout: only a working cancel can end this run, so a Timeout result
            // here would mean cancel never fired.
            std::time::Duration::from_secs(600),
            &|| true,
        )
        .expect_err("a cancelled run must abort");
        // Cancelled vs Timeout must stay distinct: the caller release()s on a cancel
        // (refunding the attempt) and only fail()s on a timeout.
        assert!(matches!(err, WrError::Cancelled), "expected Cancelled, got {err:?}");
        assert!(
            started.elapsed() < std::time::Duration::from_secs(15),
            "cancel must abort promptly; took {:?}",
            started.elapsed()
        );
    }

    /// A child that writes to stderr and exits immediately with NO stdout at all — the
    /// shape of a genuine Python crash (import error, unhandled exception before the
    /// engine ever emits its first JSON line).
    fn crashing_child(stderr_script: &str) -> EnginePath {
        EnginePath::Custom("python".into(), vec!["-c".into(), stderr_script.into()])
    }

    #[test]
    fn stderr_output_with_no_trail_is_engine_failed_not_no_trail() {
        let engine = crashing_child("import sys; sys.stderr.write('boom crash\\n'); sys.exit(1)");
        let err = run_video(
            &engine, Path::new("unused.mp4"), sel(), Duration::from_secs(10), &|| false,
        ).expect_err("a crash with no trail must be reported, not silently swallowed");
        match err {
            WrError::EngineFailed(msg) => assert!(msg.contains("boom crash"),
                "the captured stderr tail must be in the error: {msg}"),
            other => panic!("expected EngineFailed (stderr present, no trail), got {other:?}"),
        }
    }

    #[test]
    fn an_argparse_rejection_is_engine_incompatible_not_engine_failed() {
        // The exact failure shape of the 2026-07-19 stale-engine build: argparse prints
        // usage + "error: unrecognized arguments: --video ..." to stderr and exits 2.
        // This must be distinguishable from a video-caused crash — the caller refunds
        // and parks on it rather than burning the job's (and then the queue's) attempts.
        let engine = crashing_child(
            "import sys; sys.stderr.write('usage: mkw-tracker-engine.exe [-h] [--purge-tight]\\n\
             mkw-tracker-engine.exe: error: unrecognized arguments: --video wr-1.mp4\\n'); sys.exit(2)");
        let err = run_video(
            &engine, Path::new("unused.mp4"), sel(), Duration::from_secs(10), &|| false,
        ).expect_err("an arg rejection produces no trail");
        match err {
            WrError::EngineIncompatible(msg) => assert!(msg.contains("unrecognized arguments")),
            other => panic!("expected EngineIncompatible, got {other:?}"),
        }
    }

    #[test]
    fn no_trail_with_no_stderr_output_stays_no_trail() {
        // Exits cleanly (no crash signal, no stderr) but never emits run_finalized —
        // the genuine "minimap never locked" shape, which must stay retryable as NoTrail
        // rather than being reclassified as an engine failure.
        let engine = crashing_child("pass");
        let err = run_video(
            &engine, Path::new("unused.mp4"), sel(), Duration::from_secs(10), &|| false,
        ).expect_err("no run_finalized was ever emitted");
        assert!(matches!(err, WrError::NoTrail),
            "a silent exit with no stderr must stay NoTrail, got {err:?}");
    }

    #[test]
    fn stderr_ring_buffer_is_bounded_and_keeps_the_tail_not_the_head() {
        let engine = crashing_child(
            "import sys\nfor i in range(200):\n    sys.stderr.write(f'line{i}\\n')\nsys.exit(1)"
        );
        let err = run_video(
            &engine, Path::new("unused.mp4"), sel(), Duration::from_secs(15), &|| false,
        ).expect_err("no trail expected");
        match err {
            WrError::EngineFailed(msg) => {
                let n = msg.lines().count();
                assert!(n <= 50, "ring buffer must cap at ~50 lines so a spewing engine \
                                   cannot grow memory; got {n} lines");
                assert!(msg.lines().any(|l| l == "line199"),
                    "must keep the TAIL of the output: {msg}");
                assert!(!msg.lines().any(|l| l == "line0"),
                    "the earliest lines must have been dropped, not the ring's whole point: {msg}");
            }
            other => panic!("expected EngineFailed, got {other:?}"),
        }
    }

    /// A child that emits `run_finalized` and then LINGERS instead of exiting — exactly
    /// what `--video-once` actually does (it stops playback but never exits, and it never
    /// writes to stderr again either, so the drain thread's `BufReader::lines()` sits
    /// blocked in `read()` waiting for the pipe to close).
    ///
    /// This is the regression guard for moving the child `kill()` INSIDE `thread::scope`
    /// (see the "Retire the watchdog" comment above): killing the child is what closes its
    /// stderr pipe and unblocks the drain thread so the scope's implicit join can return. If
    /// that kill ever moves back to after the scope closure — as it was before this was
    /// discovered, and as a future refactor could plausibly reintroduce — the drain thread
    /// stays blocked forever (the child is still alive) and `run_video` hangs on every
    /// successful run, even though every *other* test here still passes: they all use
    /// children that exit on their own, so stderr closes regardless of kill ordering. This
    /// is the only test whose child stays alive past `run_finalized`.
    fn lingering_child_after_finalized() -> EnginePath {
        EnginePath::Custom(
            "python".into(),
            vec![
                "-c".into(),
                "import json, sys, time\n\
                 print(json.dumps({'type': 'run_finalized', 'status': 'finished', \
                 'total_time': '1:02.934', 'points': [[14, 1635, 875, 0.79, 1]]}))\n\
                 sys.stdout.flush()\n\
                 time.sleep(60)\n"
                    .into(),
            ],
        )
    }

    #[test]
    fn run_video_returns_promptly_when_the_child_lingers_after_run_finalized() {
        let started = std::time::Instant::now();
        let f = run_video(
            &lingering_child_after_finalized(),
            Path::new("unused.mp4"),
            sel(),
            Duration::from_secs(120),
            &|| false,
        ).expect("a lingering child that already emitted run_finalized must still yield the trail");
        assert_eq!(f.total_time.as_deref(), Some("1:02.934"));
        // The proof: without the kill-inside-scope fix, this blocks on the drain thread's
        // join until the 120s timeout (or the test harness) kills it. With the fix, the
        // child is killed the moment run_finalized is seen, so this returns in well under
        // 10s. The reviewer measured 60.02s (kill moved outside scope) vs 0.26s (fixed).
        assert!(
            started.elapsed() < Duration::from_secs(10),
            "must return promptly once run_finalized is seen rather than waiting for the \
             child to exit (or the timeout) on its own; took {:?}",
            started.elapsed()
        );
    }

    #[test]
    fn stderr_log_file_is_overwritten_each_run_not_appended_or_accumulated() {
        let dir = std::env::temp_dir().join("wr_engine_test_stderr_logfile");
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        // A real path *inside* a real directory — unlike the bare "unused.mp4" the other
        // tests use — so run_video's `video.parent()` guard actually lets it write.
        let video = dir.join("wr-999.mp4");
        let log_path = dir.join("engine-stderr.log");

        let run1 = crashing_child("import sys; sys.stderr.write('first run boom\\n')");
        let _ = run_video(&run1, &video, sel(), Duration::from_secs(10), &|| false);
        let contents1 = std::fs::read_to_string(&log_path).expect("log file must be written");
        assert!(contents1.contains("first run boom"));

        let run2 = crashing_child("import sys; sys.stderr.write('second run only\\n')");
        let _ = run_video(&run2, &video, sel(), Duration::from_secs(10), &|| false);
        let contents2 = std::fs::read_to_string(&log_path).unwrap();
        assert!(contents2.contains("second run only"));
        assert!(!contents2.contains("first run boom"),
            "must be OVERWRITTEN, not appended — an appending log grows forever, which is \
             exactly what the user asked NOT to happen");

        let _ = std::fs::remove_dir_all(&dir);
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

    #[test]
    fn time_to_ms_rejects_anything_that_is_not_exactly_m_ss_mmm() {
        // A short fraction is NOT milliseconds: "9" would read as 9ms, not 900ms.
        assert_eq!(time_to_ms("1:02.9"), None);
        assert_eq!(time_to_ms("1:02.93"), None);
        assert_eq!(time_to_ms("1:2.934"), None, "seconds must be 2 digits");
        assert_eq!(time_to_ms("1:60.000"), None, "60 seconds is not a valid clock reading");
        assert_eq!(time_to_ms("1:-5.100"), None, "negatives must not read as plausible times");
        assert_eq!(time_to_ms("-1:02.934"), None);
        assert_eq!(time_to_ms("102.934"), None, "no minute separator");
        assert_eq!(time_to_ms("1:02"), None, "no fraction separator");
        assert_eq!(time_to_ms(""), None);
        assert_eq!(time_to_ms("nonsense"), None);
        // Still parses the real thing (the spike's measured WR time).
        assert_eq!(time_to_ms("1:02.934"), Some(62934));
    }
}
