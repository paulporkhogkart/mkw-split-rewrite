//! Compose one full job: claim -> download -> process -> verify -> upload -> cleanup.
//!
//! Deliberately ONE job per call. The polling loop and the idle gate (spec §6.2 — WR work
//! must never run while a race is being tracked) belong to Plan 3, which supplies
//! `cancel`.

use super::engine::{self, EnginePath, Selections};
use super::{job, state, verify, ytdlp, WrError};
use std::path::PathBuf;

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

/// Serializes `process_one` against itself. `sweep_orphans` deletes by glob, so two
/// overlapping calls would have one delete the other's live video mid-job — and the engine
/// is wall-clock bound (~100s per video), so an overlap is a long window, not a
/// hypothetical. One machine does one job at a time by design (there is nothing to gain
/// from concurrency when the work is real-time bound), so this states the existing
/// contract rather than adding a new constraint.
///
/// Plan 3's polling loop inherits this: the guard is what lets it call `process_one`
/// without re-deriving the argument that a glob delete is safe.
static PROCESS_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

/// Claim and fully process one job.
pub fn process_one(cfg: &ServiceCfg, cancel: &(dyn Fn() -> bool + Sync)) -> Outcome {
    // Held for the WHOLE job (`_guard`, not `_`: the latter drops immediately and would
    // serialize nothing). Recover from a poisoned lock rather than propagating the panic —
    // a previous job panicking must not permanently disable the service. Same recovery
    // pattern engine.rs uses for its watchdog child mutex.
    let _guard = PROCESS_LOCK.lock().unwrap_or_else(|e| e.into_inner());

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
           cancel: &(dyn Fn() -> bool + Sync)) -> Outcome {
    let tier = verify::tier_for(j.attempt);
    let dest = video_path(&cfg.data_dir, j.wr_id);

    let exe = match ytdlp::ensure(&cfg.data_dir) { Ok(p) => p, Err(e) => return Outcome::Error(e) };
    if let Err(e) = ytdlp::download(&exe, &j.video_url, tier, &dest) {
        // A download failure is the classic symptom of a stale yt-dlp. Refresh once and
        // retry before burning the job's attempt on our own rot.
        log::warn!("[wr] download failed ({}), refreshing yt-dlp and retrying once", e.reason());
        let retry = ytdlp::fetch(&cfg.data_dir)
            .map_err(WrError::DownloadFailed)
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;
    use std::sync::atomic::{AtomicUsize, Ordering::SeqCst};
    use std::sync::Arc;
    use std::time::{Duration, Instant};

    fn tmpdir(tag: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("wr_service_test_{tag}"));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    /// A bare TCP listener that accepts, holds each connection for `hold`, then drops it
    /// without replying.
    ///
    /// This is NOT a mock server: it never speaks HTTP, models no endpoint and asserts
    /// nothing about the wire (claim() just errors, which is the point). It is an
    /// OBSERVATION POINT, and the only one available — the lock lives INSIDE process_one,
    /// so a counter wrapped around the CALL cannot distinguish "working" from "broken":
    /// the blocked thread has already incremented it while waiting on the mutex. claim()'s
    /// connection is made from inside the critical section, so concurrent connections here
    /// mean concurrent critical sections, and nothing else does.
    ///
    /// `hold` also makes the window deterministic: 300ms dwarfs thread-spawn jitter, so an
    /// unlocked overlap is a certainty rather than a race the test might lose.
    fn stalling_listener(hold: Duration) -> (String, Arc<AtomicUsize>) {
        let l = TcpListener::bind("127.0.0.1:0").expect("bind probe listener");
        let addr = l.local_addr().unwrap();
        let live = Arc::new(AtomicUsize::new(0));
        let max = Arc::new(AtomicUsize::new(0));
        let max_out = max.clone();
        std::thread::spawn(move || {
            for stream in l.incoming() {
                let Ok(stream) = stream else { break };
                let (live, max) = (live.clone(), max.clone());
                // A thread PER connection. A sequential accept loop would leave the second
                // connection sitting in the OS backlog and report a concurrency of 1 even
                // with NO lock at all — i.e. it would pass either way and prove nothing.
                std::thread::spawn(move || {
                    let n = live.fetch_add(1, SeqCst) + 1;
                    max.fetch_max(n, SeqCst);
                    std::thread::sleep(hold);
                    live.fetch_sub(1, SeqCst);
                    drop(stream);
                });
            }
        });
        (format!("http://{addr}"), max_out)
    }

    #[test]
    fn process_one_never_overlaps_itself_because_sweep_orphans_deletes_by_glob() {
        let hold = Duration::from_millis(300);
        let (url, max_concurrent) = stalling_listener(hold);
        let dir = tmpdir("serialize");
        // Pre-create the scratch DB so neither thread's state::open() can be what
        // serializes them (or what fails under contention). The LOCK must be the only
        // thing keeping them apart, or this test would credit the lock for SQLite's work.
        drop(state::open(&dir).unwrap());

        let started = Instant::now();
        std::thread::scope(|s| {
            for _ in 0..2 {
                s.spawn(|| {
                    let cfg = ServiceCfg {
                        server_url: url.clone(),
                        token: "probe".into(),
                        data_dir: dir.clone(),
                        engine: EnginePath::Dev,
                    };
                    // Both calls end in Outcome::Error (claim never gets a reply). The
                    // RESULT is not under test — the overlap is.
                    let _ = process_one(&cfg, &|| false);
                });
            }
        });

        // 0 would mean neither call ever reached claim() and the test proved nothing;
        // 2 means both were inside the critical section at once.
        assert_eq!(max_concurrent.load(SeqCst), 1,
            "two process_one calls held the critical section at once: sweep_orphans deletes \
             wr-*.mp4 by glob, so one would delete the other's actively-downloading video");
        // Corroborates in the time domain: serialized, two 300ms stalls cannot fit into
        // less than 600ms. Guards against a future refactor that keeps concurrency at 1 by
        // never connecting rather than by locking.
        assert!(started.elapsed() >= hold * 2,
            "expected two serialized {hold:?} stalls, took only {:?}", started.elapsed());
    }

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
        // The canonical display name for "dk_spaceport" is "DK Spaceport"
        // (server/courses.py CANONICAL_COURSES). Title-casing the slug word-by-word
        // (job::slug_to_display) would instead produce "Dk Spaceport" -- a genuinely
        // different string -- so this input actually discriminates "use course_name
        // verbatim" from "re-derive from the slug", unlike a course whose name happens
        // to title-case identically either way.
        let j = job::parse_job(r#"{"wr_id":1,"cc":150,"course_slug":"dk_spaceport",
            "course_name":"DK Spaceport","video_url":"u","record_ms":1,
            "character_slug":"bowser","costume_slug":null,"kart_slug":null,"attempt":1}"#).unwrap();
        assert_eq!(selections_for(&j).course, "DK Spaceport");
    }

    #[test]
    fn video_path_is_per_job_so_two_jobs_cannot_collide() {
        let d = std::env::temp_dir();
        assert_ne!(video_path(&d, 6), video_path(&d, 7));
        assert!(video_path(&d, 6).to_string_lossy().contains("6"));
    }
}
