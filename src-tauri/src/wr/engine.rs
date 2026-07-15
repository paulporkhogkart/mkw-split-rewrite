//! Drives the Python engine over stdio to extract a trail from a WR video.
//!
//! `EngineDriver` is PURE: feed it stdout lines, it returns stdin commands. All the
//! sequencing knowledge proven by the 2026-07-15 spike lives here and is unit-tested;
//! `run_video` (Task 3) is the thin process shell around it.

use super::WrError;
use serde_json::json;
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
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
            let _ = child.kill(); let _ = child.wait();
            return Err(WrError::Timeout);
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
