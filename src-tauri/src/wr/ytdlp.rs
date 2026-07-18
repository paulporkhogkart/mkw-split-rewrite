//! Self-updating yt-dlp + the download step.

use super::verify::Tier;
use super::WrError;
use std::path::{Path, PathBuf};

/// Official standalone Windows build. Pinned to the yt-dlp org's own releases.
const YTDLP_URL: &str = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe";

/// The download format, per tier.
///
/// A SELECTOR, never a format id: ids are per-video (the spike's 1080p60 avc1 was `299`
/// on one video and would not exist on another). 1080p is non-negotiable — every engine
/// ROI is a 1080p pixel coord and `_norm()` would rescale anything else and blur the
/// templates. avc1 is merely preferred (cheapest decode); VP9 is fine on a PC.
///
/// EVERY branch must keep `[fps=60]`. There is deliberately NO non-60fps fallback: a
/// `bestvideo[height=1080]` branch looks like a harmless safety net but is the opposite —
/// it MATCHES a 1080p30 upload, so the job would silently process a 30fps video instead of
/// failing. Matching nothing is the INTENDED outcome: yt-dlp then reports "Requested
/// format is not available", which `classify_failure` turns into `WrError::No1080p60` — a
/// clean, correct failure. Do not "helpfully" re-add a lower-fps fallback.
pub fn format_selector(tier: Tier) -> &'static str {
    match tier {
        Tier::Native1080p60 =>
            "bestvideo[height=1080][fps=60][vcodec^=avc1]/bestvideo[height=1080][fps=60]",
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::process::Command;
    use std::time::Duration;

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
    fn every_native_fallback_branch_still_demands_1080p60() {
        let s = format_selector(Tier::Native1080p60);
        // Whole-string `contains` is not enough: a branch that drops [fps=60] would still
        // pass that, and would silently download a 1080p30 stream instead of failing.
        for branch in s.split('/') {
            assert!(branch.contains("height=1080"), "branch without 1080p: {branch}");
            assert!(branch.contains("fps=60"), "branch without 60fps: {branch}");
        }
        assert!(!s.contains("299"), "hardcoded format ids are per-video and must never appear");
        assert!(s.split('/').next().unwrap().contains("vcodec^=avc1"), "avc1 preferred first");
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

    #[test]
    fn a_spawn_failure_is_engine_failed_not_download_failed() {
        // A real spawn failure, not a simulated one: point at an exe that cannot exist so
        // Command::output() itself errors, exercising the actual code path rather than
        // asserting on classify_failure() (which spawn failures never reach).
        let missing = Path::new("this-path-definitely-does-not-exist-wr-test.exe");
        let dest = std::env::temp_dir().join("wr_ytdlp_spawn_test_unused.mp4");
        let err = download(missing, "https://example.invalid/video", Tier::Native1080p60, &dest,
                           &|| false)
            .expect_err("a nonexistent exe must fail to spawn");
        assert!(matches!(err, WrError::EngineFailed(_)),
            "a spawn failure is OUR fault, not the video's — expected EngineFailed, got {err:?}");
    }

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
}
