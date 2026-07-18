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

/// The Pi sends slugs; the engine wants ITS OWN display names. For the course that means
/// the seed-key shape — job::course_display_for_engine, NOT the Pi's course_name (which
/// misses the seed table on 7 of 30 courses; see that function's doc). Characters, karts
/// and costumes are slug_to_display: consistent-by-construction with the engine's
/// filename-derived template keys.
fn selections_for(j: &job::WrJob) -> Selections {
    Selections {
        course: job::course_display_for_engine(&j.course_slug),
        character: job::slug_to_display(&j.character_slug),
        costume: j.costume_slug.as_deref().map(job::slug_to_display),
        kart: j.kart_slug.as_deref().map(job::slug_to_display),
    }
}

fn video_path(dir: &std::path::Path, wr_id: i64) -> PathBuf {
    dir.join(format!("wr-{wr_id}.mp4"))
}

/// Only DownloadFailed could plausibly be explained by a stale yt-dlp (a network hiccup, a
/// format-parsing regression against a specific yt-dlp release). No1080p60/VideoUnavailable/
/// EngineFailed are permanent for this video/URL — a refresh-and-retry cannot fix a missing
/// stream or a removed video, so retrying on those just burns ~180s (the fetch timeout)
/// plus a second doomed download. Pulled out as its own function so this predicate can be
/// proven directly, without needing a real yt-dlp binary or network access.
fn is_staleness_explicable(e: &WrError) -> bool {
    matches!(e, WrError::DownloadFailed(_))
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

    // Crash recovery: an inflight record left over means we died mid-job last time,
    // before reaching the set_inflight(None) at the bottom of this function. Clear the
    // LOCAL record only — deliberately do NOT release() the lease. release() refunds
    // the attempt (that is its job: a voluntary pause must not count against the cap),
    // but a crash is exactly what attempts-on-claim exists to count: "a worker that
    // dies without reporting still burns one and a poison job can't retry forever"
    // (spec §4). An app restart inside the ~600s lease window would make the release
    // succeed and refund — under Plan 3's autostart, a job whose video reliably
    // crashes the app would then claim -> crash -> refund -> claim forever. Letting
    // the lease lapse burns the attempt; the only cost is that one job waiting out the
    // rest of its lease (<= ~10 min) before it can be claimed again.
    if let Some(orphan_wr_id) = state::inflight(&conn) {
        log::warn!("[wr] crash-orphaned inflight wr_id={orphan_wr_id}: clearing the local \
                    record; its lease will lapse and the attempt stays burned (per spec)");
        state::set_inflight(&conn, None);
    }

    let j = match client.claim() {
        Ok(Some(j)) => j,
        Ok(None) => return Outcome::Idle,
        Err(e) => return Outcome::Error(e),
    };
    state::set_inflight(&conn, Some(j.wr_id));
    log::info!("[wr] claimed wr_id={} {} attempt={}", j.wr_id, j.course_slug, j.attempt);

    let outcome = run_job(cfg, &client, &j, cancel);
    super::phase::set(None);

    cleanup(&cfg.data_dir, j.wr_id);
    state::set_inflight(&conn, None);
    outcome
}

/// Engine budget for one video, from the record itself. The engine is wall-clock bound
/// (~the video's duration) and a WR upload is ~the race plus a short menu intro and the
/// finish-still hold, so record + 180s covers every observed upload shape with room for
/// engine startup. Floor 300s keeps short courses generous; cap 540s stays a full minute
/// under the Pi's 600s lease. That margin is ENGINE-only: the same never-heartbeated
/// lease also covers the download (unbounded today — bounding yt-dlp is deliberately
/// deferred) and the result upload, so a badly stalled download can still overrun it.
/// An overrun wastes work but cannot corrupt: complete() is ownership-checked
/// server-side and 409s once the lease moves. If records ever approach this cap, wire
/// job::Client::heartbeat into the engine step instead of raising it (Plan 3).
fn engine_timeout_for(record_ms: i64) -> std::time::Duration {
    std::time::Duration::from_secs(((record_ms / 1000) + 180).clamp(300, 540) as u64)
}

/// Only a CONFIRMED ownership loss (server said false) stops in-flight work; a network
/// error keeps going — the lease is probably still ours and the next beat re-checks.
fn should_stop_after_heartbeat(res: &Result<bool, String>) -> bool {
    matches!(res, Ok(false))
}

/// Beat every 120s: the lease is 600s, so even one lost beat leaves wide margin, and
/// the cadence is cheap enough to never matter.
const HEARTBEAT_EVERY: std::time::Duration = std::time::Duration::from_secs(120);

fn run_job(cfg: &ServiceCfg, client: &job::Client, j: &job::WrJob,
           cancel: &(dyn Fn() -> bool + Sync)) -> Outcome {
    let tier = verify::tier_for(j.attempt);
    let dest = video_path(&cfg.data_dir, j.wr_id);

    let exe = match ytdlp::ensure(&cfg.data_dir) { Ok(p) => p, Err(e) => return Outcome::Error(e) };
    super::phase::set(Some(super::phase::Phase {
        kind: super::phase::PhaseKind::Downloading, course_slug: j.course_slug.clone() }));
    if let Err(e) = ytdlp::download(&exe, &j.video_url, tier, &dest, cancel) {
        // A cancel mid-download is a deliberate stop: release (refund), never fail.
        if matches!(e, WrError::Cancelled) {
            let _ = client.release(j.wr_id);
            return Outcome::Released(j.wr_id);
        }
        if is_staleness_explicable(&e) {
            // See is_staleness_explicable's doc for why only THIS class of error retries.
            log::warn!("[wr] download failed ({}), refreshing yt-dlp and retrying once", e.reason());
            let retry = ytdlp::fetch(&cfg.data_dir)
                .map_err(WrError::DownloadFailed)
                .and_then(|exe2| ytdlp::download(&exe2, &j.video_url, tier, &dest, cancel));
            if let Err(e2) = retry {
                // Mirror the first leg: a cancel during the RETRY is the same deliberate
                // stop — release (refund), never fail (which would burn the attempt and
                // record a nonsense "cancelled" as last_error).
                if matches!(e2, WrError::Cancelled) {
                    let _ = client.release(j.wr_id);
                    return Outcome::Released(j.wr_id);
                }
                let _ = client.fail(j.wr_id, &e2);
                return Outcome::Failed(j.wr_id, e2);
            }
        } else {
            let _ = client.fail(j.wr_id, &e);
            return Outcome::Failed(j.wr_id, e);
        }
    }
    if cancel() { let _ = client.release(j.wr_id); return Outcome::Released(j.wr_id); }

    super::phase::set(Some(super::phase::Phase {
        kind: super::phase::PhaseKind::Processing, course_slug: j.course_slug.clone() }));
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
    // Declared here, NOT inside the scope closure below: a scoped thread is joined only
    // when std::thread::scope itself returns, which is after the closure body finishes —
    // so a flag the spawned thread borrows must outlive the closure, not just live inside
    // it (the same reason `lease_lost` above is already out here).
    let done = std::sync::atomic::AtomicBool::new(false);
    let run_result = std::thread::scope(|s| {
        let done_ref = &done;
        let lost_ref = &lease_lost;
        s.spawn(move || {
            let mut since_beat = std::time::Duration::ZERO;
            while !done_ref.load(std::sync::atomic::Ordering::Relaxed) {
                if cancel_or_lost() { return; }
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
        Ok(f) => f,
        // Match the variant, don't re-poll cancel(): a genuine timeout that happens to
        // land just as the user pauses must still be reported as a timeout.
        // release() refunds the attempt; fail() deliberately does not.
        // (A lease-lost cancel lands here too: release() then 409s harmlessly — fine.)
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
            // Include the engine's own run status: it's the difference between a bare,
            // unhelpful "no trail" and a log line that says WHY ("reset"/"dnf"/...).
            log::warn!("[wr] wr_id={} rejected: {} (engine status={})", j.wr_id, e.reason(),
                finalized.status.as_deref().unwrap_or("<none>"));
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

    /// Accepts and immediately drops every connection, counting them. Each reqwest call
    /// = exactly one connection (a fresh connection that dies before any response is
    /// not retried by reqwest/hyper — retries only apply to reused pool connections).
    /// This is the only observation point for WHICH calls process_one makes without a
    /// mock server: 1 connection = claim only; 2 = a release snuck back in before it.
    fn counting_listener() -> (String, Arc<AtomicUsize>) {
        let l = TcpListener::bind("127.0.0.1:0").expect("bind probe listener");
        let addr = l.local_addr().unwrap();
        let total = Arc::new(AtomicUsize::new(0));
        let t = total.clone();
        std::thread::spawn(move || {
            for stream in l.incoming() {
                let Ok(stream) = stream else { break };
                t.fetch_add(1, SeqCst);
                drop(stream);
            }
        });
        (format!("http://{addr}"), total)
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
    fn crash_orphan_is_cleared_locally_without_a_lease_release() {
        // A crash must BURN its attempt (spec §4: "a worker that dies without reporting
        // still burns one and a poison job can't retry forever"). release() REFUNDS the
        // attempt, and an app restart inside the ~600s lease window would make the
        // refund succeed — under autostart, a job whose video crashes the app would
        // claim -> crash -> refund -> claim forever. So crash recovery must clear the
        // LOCAL record only and let the lease lapse.
        let (url, total) = counting_listener();
        let dir = tmpdir("orphan_no_release");
        let conn = state::open(&dir).unwrap();
        state::set_inflight(&conn, Some(42));
        drop(conn);

        let cfg = ServiceCfg {
            server_url: url,
            token: "probe".into(),
            data_dir: dir.clone(),
            engine: EnginePath::Dev,
        };
        let _ = process_one(&cfg, &|| false);

        assert_eq!(total.load(SeqCst), 1,
            "expected exactly ONE request (the claim); a second one means the orphan \
             was release()d, which refunds the attempt a crash must burn");
        let conn2 = state::open(&dir).unwrap();
        assert_eq!(state::inflight(&conn2), None,
            "the stale local record must still be cleared, or every future call re-sees it");
    }

    #[test]
    fn process_one_with_no_orphan_leaves_inflight_untouched_before_claiming() {
        // Guards the other direction: process_one must not go around releasing/clearing
        // wr_ids that were never actually left inflight.
        let dir = tmpdir("no_orphan");
        let conn = state::open(&dir).unwrap();
        assert_eq!(state::inflight(&conn), None, "precondition: nothing inflight yet");
        drop(conn);

        let cfg = ServiceCfg {
            server_url: "http://127.0.0.1:1".into(),
            token: "probe".into(),
            data_dir: dir.clone(),
            engine: EnginePath::Dev,
        };
        let _ = process_one(&cfg, &|| false);

        let conn2 = state::open(&dir).unwrap();
        assert_eq!(state::inflight(&conn2), None);
    }

    #[test]
    fn only_download_failed_is_treated_as_explicable_by_a_stale_yt_dlp() {
        assert!(is_staleness_explicable(&WrError::DownloadFailed("x".into())));
        assert!(!is_staleness_explicable(&WrError::No1080p60),
            "a missing 1080p60 stream is permanent for this video — refreshing yt-dlp can't add a format");
        assert!(!is_staleness_explicable(&WrError::VideoUnavailable),
            "a removed/private video is permanent — retrying just burns the fetch timeout");
        assert!(!is_staleness_explicable(&WrError::EngineFailed("spawn: x".into())),
            "our own spawn failure isn't a yt-dlp staleness symptom");
        assert!(!is_staleness_explicable(&WrError::Timeout));
        assert!(!is_staleness_explicable(&WrError::Cancelled));
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
    fn course_sent_to_the_engine_is_seed_key_shaped_not_the_pi_display_name() {
        // The Pi's canonical name is "DK Spaceport" (server/courses.py) but the engine's
        // seed row is 'Dk Spaceport' (detection-derived). Sending the Pi name verbatim
        // finds no seed -> guaranteed no_trail — the pre-fix behaviour this test kills.
        let j = job::parse_job(r#"{"wr_id":1,"course_slug":"dk_spaceport",
            "course_name":"DK Spaceport","video_url":"u","record_ms":1,
            "character_slug":"bowser","costume_slug":null,"kart_slug":null,"attempt":1}"#).unwrap();
        assert_eq!(selections_for(&j).course, "Dk Spaceport");
    }

    #[test]
    fn course_mapping_handles_the_sky_high_sundae_exception() {
        let j = job::parse_job(r#"{"wr_id":1,"course_slug":"sky_high_sundae",
            "course_name":"Sky-High Sundae","video_url":"u","record_ms":1,
            "character_slug":"bowser","costume_slug":null,"kart_slug":null,"attempt":1}"#).unwrap();
        assert_eq!(selections_for(&j).course, "Sky-High Sundae");
    }

    #[test]
    fn video_path_is_per_job_so_two_jobs_cannot_collide() {
        let d = std::env::temp_dir();
        assert_ne!(video_path(&d, 6), video_path(&d, 7));
        assert!(video_path(&d, 6).to_string_lossy().contains("6"));
    }

    #[test]
    fn engine_timeout_scales_with_the_record_within_the_lease() {
        // Mario Circuit (1:02.934): the 300s floor applies.
        assert_eq!(engine_timeout_for(62_934), Duration::from_secs(300));
        // Rainbow Road, the slowest board record (3'53"260 = 233s, mkwrs 2026-07-17):
        // the old fixed 300s left ~50s for intro + finish-still + startup — one
        // long-intro upload away from burning all 5 attempts on the marquee track.
        assert_eq!(engine_timeout_for(233_260), Duration::from_secs(413));
        // The ENGINE budget stays 60s under the Pi's 600s lease (the whole job shares
        // that one lease — see engine_timeout_for's doc for the download/upload margins).
        assert_eq!(engine_timeout_for(900_000), Duration::from_secs(540));
    }

    #[test]
    fn heartbeat_verdicts_only_a_confirmed_loss_stops_work() {
        // Ok(false) = the server CONFIRMED we no longer own the lease: stop, the job is
        // someone else's now. An Err is a network blip — the lease is probably still
        // ours, and stopping on flaky wifi would abandon healthy jobs.
        assert!(should_stop_after_heartbeat(&Ok(false)));
        assert!(!should_stop_after_heartbeat(&Ok(true)));
        assert!(!should_stop_after_heartbeat(&Err("timeout".into())));
    }
}
