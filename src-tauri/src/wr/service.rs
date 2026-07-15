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

/// Claim and fully process one job.
pub fn process_one(cfg: &ServiceCfg, cancel: &(dyn Fn() -> bool + Sync)) -> Outcome {
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
