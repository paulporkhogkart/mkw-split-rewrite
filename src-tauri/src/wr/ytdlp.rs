//! Self-updating yt-dlp + the download step.

use super::verify::Tier;
use super::WrError;
use std::path::{Path, PathBuf};

/// Official standalone Windows build from the yt-dlp org's own NIGHTLY channel (built
/// daily from master). Not stable: YouTube breaks yt-dlp every few weeks and stable
/// releases lag that by weeks — 2026-08-17 YouTube killed the android_vr client, stable
/// 2026.07.04 failed every download for days while the fix (PR #17461) sat in nightly,
/// and the maintainers' standing answer is `--update-to nightly`. `ensure` keeps us on the
/// latest nightly with a cheap HEAD per job, so a broken build is replaced the day the
/// fix lands instead of the month the next stable ships.
pub const YTDLP_URL: &str =
    "https://github.com/yt-dlp/yt-dlp-nightly-builds/releases/latest/download/yt-dlp.exe";

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
    WrError::DownloadFailed(failure_headline(stderr))
}

/// The one line of yt-dlp stderr worth keeping: its LAST `ERROR:` line (the terminal
/// verdict), else the last non-empty line. yt-dlp front-loads WARNINGs — the 2026-08-19
/// "No supported JavaScript runtime" one alone is ~330 chars — so a head-truncation of the
/// whole stream kept the noise and dropped the cause. Bounded to 300 chars.
fn failure_headline(stderr: &str) -> String {
    let lines: Vec<&str> = stderr.lines().map(str::trim).filter(|l| !l.is_empty()).collect();
    let line = lines.iter().rev().find(|l| l.starts_with("ERROR:"))
        .or_else(|| lines.last())
        .copied()
        .unwrap_or("");
    line.strip_prefix("ERROR:").map(str::trim).unwrap_or(line).chars().take(300).collect()
}

/// The release tag named by GitHub's `.../releases/latest/download/<asset>` redirect
/// (`.../releases/download/<tag>/<asset>`). None for any other shape — "unknown" must never
/// become a fabricated tag, or a changed endpoint would silently freeze refreshes.
fn tag_from_location(location: &str) -> Option<String> {
    location.split("/releases/download/").nth(1)?
        .split('/').next()
        .filter(|t| !t.is_empty())
        .map(str::to_string)
}

/// Which build the local exe is, as recorded at fetch time (the release tag). Absent =
/// unknown (a hand-placed exe, or a blind refresh) — which reads as "not the latest".
const VERSION_MARKER: &str = "yt-dlp.version";

fn local_tag(dir: &Path) -> Option<String> {
    std::fs::read_to_string(dir.join(VERSION_MARKER)).ok()
        .map(|s| s.trim().to_string()).filter(|s| !s.is_empty())
}

fn record_tag(dir: &Path, tag: Option<&str>) {
    let p = dir.join(VERSION_MARKER);
    let _ = match tag { Some(t) => std::fs::write(&p, t), None => std::fs::remove_file(&p).or(Ok(())) };
}

/// Ask GitHub which build "latest" currently is — one HEAD, no transfer. GitHub answers
/// the `.../releases/latest/download/<asset>` URL with a 302 whose Location names the
/// release; the redirect is deliberately NOT followed.
fn latest_tag(url: &str) -> Result<String, String> {
    let client = reqwest::blocking::Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(std::time::Duration::from_secs(15))
        .build().map_err(|e| e.to_string())?;
    let resp = client.head(url).send().map_err(|e| format!("probe yt-dlp release: {e}"))?;
    let loc = resp.headers().get(reqwest::header::LOCATION)
        .and_then(|v| v.to_str().ok())
        .ok_or_else(|| format!("probe yt-dlp release: HTTP {} without a Location", resp.status()))?;
    tag_from_location(loc).ok_or_else(|| format!("probe yt-dlp release: unrecognised redirect {loc}"))
}

/// Path to a usable, current yt-dlp.exe. Called at the start of EVERY job: fetches when
/// absent, otherwise probes GitHub for the latest build (cheap) and refreshes only when
/// the local one is not it. There is deliberately no refresh-on-download-failure any more:
/// the 2026-08-19 incident (yt-dlp stable broken by YouTube for two days) had that path
/// re-download the SAME broken build on every attempt — 15 × 18MB for nothing — while
/// burning the job's attempts. Freshness is decided here, by tag, once per job.
///
/// `url` is the release asset to track (`YTDLP_URL` in production; ServiceCfg carries it so
/// tests run the real code against a local stand-in for GitHub).
pub fn ensure(url: &str, dir: &Path, cancel: &(dyn Fn() -> bool + Sync)) -> Result<PathBuf, String> {
    let exe = dir.join("yt-dlp.exe");
    let latest = latest_tag(url);
    if !exe.is_file() {
        // Nothing usable: the fetch must succeed (its error is the job's error).
        let p = fetch_from(url, dir, cancel)?;
        record_tag(dir, latest.as_deref().ok());
        return Ok(p);
    }
    match latest {
        Ok(tag) if local_tag(dir).as_deref() == Some(tag.as_str()) => Ok(exe),
        Ok(tag) => {
            log::info!("[wr] yt-dlp {tag} is out (have {}); refreshing",
                       local_tag(dir).as_deref().unwrap_or("an unknown build"));
            match fetch_from(url, dir, cancel) {
                Ok(p) => { record_tag(dir, Some(&tag)); Ok(p) }
                // The build we have was working yesterday; a flaky CDN must not become
                // a failed job. (fetch_from renames last, so the exe is intact.)
                Err(e) => { log::warn!("[wr] yt-dlp refresh failed ({e}); keeping the current build"); Ok(exe) }
            }
        }
        // Can't tell (offline, or GitHub changed the redirect shape): keep what we have —
        // but never forever. Past STALE_AFTER, refresh blind; the marker is dropped rather
        // than guessed, so the next successful probe re-syncs it with one transfer.
        Err(e) => {
            if exe_age(&exe) <= STALE_AFTER { return Ok(exe); }
            log::warn!("[wr] cannot tell which yt-dlp is latest ({e}); exe is >{}h old, refreshing blind",
                       STALE_AFTER.as_secs() / 3600);
            match fetch_from(url, dir, cancel) {
                Ok(p) => { record_tag(dir, None); Ok(p) }
                Err(e) => { log::warn!("[wr] blind yt-dlp refresh failed ({e}); keeping the current build"); Ok(exe) }
            }
        }
    }
}

/// The blind-refresh backstop threshold (see `ensure_from`). Nightlies are daily; a day
/// is the natural unit, and bounds a misbehaving endpoint to one transfer per day.
const STALE_AFTER: std::time::Duration = std::time::Duration::from_secs(24 * 3600);

fn exe_age(exe: &Path) -> std::time::Duration {
    std::fs::metadata(exe).and_then(|m| m.modified()).ok()
        .and_then(|t| t.elapsed().ok())
        .unwrap_or(std::time::Duration::ZERO)
}

/// (Re)download the standalone yt-dlp.exe from `url`. Cancel-aware: Runner::stop() joins
/// the thread this runs on, so a quit/pause/toggle-off mid-refresh must interrupt the
/// transfer rather than sit out up to the full 180s window (review 2026-07-18 — the
/// download step was made cancel-aware in Task 5, but this fetch was missed). URL-injected
/// so tests can observe cancel behaviour against a local dripping server.
pub fn fetch_from(url: &str, dir: &Path, cancel: &(dyn Fn() -> bool + Sync)) -> Result<PathBuf, String> {
    use std::io::Read;
    std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    let exe = dir.join("yt-dlp.exe");
    let tmp = dir.join("yt-dlp.exe.part");
    let mut resp = reqwest::blocking::Client::new()
        .get(url)
        .timeout(std::time::Duration::from_secs(180))
        .send().map_err(|e| format!("fetch yt-dlp: {e}"))?
        .error_for_status().map_err(|e| format!("fetch yt-dlp: {e}"))?;
    // Chunked read with a cancel check per chunk. The 180s request timeout stays the
    // outer bound for a stalled socket; cancel latency on a live transfer is one chunk.
    let mut bytes: Vec<u8> = Vec::new();
    let mut buf = [0u8; 64 * 1024];
    loop {
        if cancel() { return Err("yt-dlp fetch cancelled".into()); }
        match resp.read(&mut buf) {
            Ok(0) => break,
            Ok(n) => bytes.extend_from_slice(&buf[..n]),
            Err(e) => return Err(format!("fetch yt-dlp: {e}")),
        }
    }
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

    cmd.stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::piped());
    // GUI-subsystem parent + console-subsystem child = a visible console window per
    // spawn unless suppressed (plugin-shell does the same for the live engine).
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt as _;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    let mut child = cmd
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

/// Shared with service.rs's tests (the real download step against a local stand-in
/// for GitHub's release endpoint).
#[cfg(test)]
pub(crate) mod test_support {
    /// A stand-in for GitHub's release endpoint. HEAD on the "latest" asset URL answers
    /// with a 302 to `head_location` (or a bare 200 when None — the "endpoint changed
    /// shape" case); GET serves `body`. GETs are counted: an 18MB transfer is the one
    /// thing the freshness policy must spend sparingly.
    pub(crate) fn fake_release_server(head_location: Option<String>, body: &'static [u8])
        -> (String, std::sync::Arc<std::sync::atomic::AtomicUsize>) {
        use std::io::{Read as _, Write as _};
        use std::sync::atomic::{AtomicUsize, Ordering::SeqCst};
        let l = std::net::TcpListener::bind("127.0.0.1:0").expect("bind release server");
        let addr = l.local_addr().unwrap();
        let gets = std::sync::Arc::new(AtomicUsize::new(0));
        let g = gets.clone();
        std::thread::spawn(move || {
            for stream in l.incoming() {
                let Ok(mut s) = stream else { break };
                let mut req = [0u8; 4096];
                let n = s.read(&mut req).unwrap_or(0);
                let req = String::from_utf8_lossy(&req[..n]);
                if req.starts_with("HEAD ") {
                    let _ = match &head_location {
                        Some(loc) => write!(s, "HTTP/1.1 302 Found\r\nLocation: {loc}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"),
                        None => write!(s, "HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"),
                    };
                } else {
                    g.fetch_add(1, SeqCst);
                    let _ = write!(s, "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", body.len());
                    let _ = s.write_all(body);
                }
                let _ = s.flush();
            }
        });
        (format!("http://{addr}/yt-dlp.exe"), gets)
    }

    pub(crate) const LATEST: &str = "2026.08.18.122307";
    pub(crate) fn latest_location() -> Option<String> {
        Some(format!("https://github.com/yt-dlp/yt-dlp-nightly-builds/releases/download/{LATEST}/yt-dlp.exe"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use super::test_support::*;
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
    fn the_terminal_error_line_survives_a_long_preceding_warning() {
        // The 2026-08-19 incident verbatim: yt-dlp printed a ~330-char "No supported
        // JavaScript runtime" WARNING and THEN the real ERROR. A head-truncation kept only
        // the warning, so pbenguin.log and the Pi both hid the actual cause (HTTP 403) for
        // 15 attempts. The detail must be the terminal error — bounded, but never blind.
        let warning = format!("WARNING: [youtube] No supported JavaScript runtime could be found. {}",
                              "See the wiki for details on installing one. ".repeat(8));
        assert!(warning.len() > 300, "precondition: the warning alone must overflow the cap");
        let stderr = format!("{warning}\nERROR: unable to download video data: HTTP Error 403: Forbidden\n");
        match classify_failure(&stderr) {
            WrError::DownloadFailed(d) => {
                assert!(d.contains("HTTP Error 403"), "must keep the terminal error, got: {d}");
                assert!(!d.starts_with("WARNING"), "a warning is not the failure, got: {d}");
                assert!(!d.starts_with("ERROR:"), "the reason already says download_failed; drop the redundant tag, got: {d}");
                assert!(d.chars().count() <= 300, "detail must stay bounded, got {} chars", d.chars().count());
            }
            other => panic!("expected DownloadFailed, got {other:?}"),
        }
    }

    #[test]
    fn a_spawn_failure_is_engine_failed_not_download_failed() {
        // A real spawn failure, not a simulated one: point at an exe that cannot exist so
        // Command::spawn() inside run_download itself errors, exercising the actual code path rather than
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

    /// A local HTTP server that drips `chunks` KB-sized body chunks, one per `chunk_ms`.
    /// The shape of a slow yt-dlp.exe transfer from GitHub — the only way to observe
    /// whether fetch honours cancel mid-body without touching the network.
    fn dripping_http_server(chunks: usize, chunk_ms: u64) -> String {
        use std::io::{Read as _, Write as _};
        let l = std::net::TcpListener::bind("127.0.0.1:0").expect("bind drip server");
        let addr = l.local_addr().unwrap();
        std::thread::spawn(move || {
            if let Ok((mut s, _)) = l.accept() {
                let mut req = [0u8; 2048];
                let _ = s.read(&mut req);
                let body_len = chunks * 1024;
                let _ = write!(
                    s, "HTTP/1.1 200 OK\r\nContent-Length: {body_len}\r\nConnection: close\r\n\r\n");
                for _ in 0..chunks {
                    if s.write_all(&[0u8; 1024]).is_err() { return; }
                    let _ = s.flush();
                    std::thread::sleep(Duration::from_millis(chunk_ms));
                }
            }
        });
        format!("http://{addr}/yt-dlp.exe")
    }

    #[test]
    fn a_cancelled_fetch_aborts_promptly_instead_of_sitting_out_the_transfer() {
        // Runner::stop() joins the runner thread; a fetch that ignores cancel makes a
        // quit or toggle-off sit out up to the full 180s transfer (found in the
        // 2026-07-18 review: the spec's "~30s worst case" was 6x understated whenever
        // a yt-dlp refresh was in flight).
        let url = dripping_http_server(40, 250); // ~10s if allowed to finish
        let dir = tmpdir("fetch_cancel");
        let started = std::time::Instant::now();
        let err = fetch_from(&url, &dir, &|| true).expect_err("a cancelled fetch must abort");
        assert!(err.contains("cancel"), "the error must say it was a cancel, got: {err}");
        assert!(started.elapsed() < Duration::from_secs(3),
            "cancel must interrupt the body read, not wait out the drip; took {:?}",
            started.elapsed());
        assert!(!dir.join("yt-dlp.exe").exists(),
            "a cancelled fetch must not leave a usable-looking exe behind");
    }

    #[test]
    fn an_uncancelled_fetch_completes_and_installs_the_exe_atomically() {
        // The happy path through the same chunked reader: bytes land, .part is renamed,
        // and the finished exe is exactly the served body.
        let url = dripping_http_server(3, 1);
        let dir = tmpdir("fetch_ok");
        let exe = fetch_from(&url, &dir, &|| false).expect("fetch must succeed");
        assert_eq!(exe, dir.join("yt-dlp.exe"));
        let got = std::fs::read(&exe).unwrap();
        assert_eq!(got.len(), 3 * 1024, "must have read the whole Content-Length body");
        assert!(!dir.join("yt-dlp.exe.part").exists(), "the temp file must be renamed away");
    }

    #[test]
    fn ensure_skips_the_download_when_the_local_build_is_already_the_latest() {
        // The common case on every job start: a cheap HEAD, no 18MB transfer, and the exe
        // we already have is the one returned.
        let (url, gets) = fake_release_server(latest_location(), b"new-bytes");
        let dir = tmpdir("ensure_current");
        std::fs::write(dir.join("yt-dlp.exe"), b"old-bytes").unwrap();
        std::fs::write(dir.join("yt-dlp.version"), LATEST).unwrap();

        let exe = ensure(&url, &dir, &|| false).expect("a current exe is usable");

        assert_eq!(exe, dir.join("yt-dlp.exe"));
        assert_eq!(gets.load(std::sync::atomic::Ordering::SeqCst), 0,
            "the build is already the latest tag; re-downloading it is the 2026-08-19 waste");
        assert_eq!(std::fs::read(&exe).unwrap(), b"old-bytes", "must not have been replaced");
    }

    #[test]
    fn ensure_refreshes_once_when_a_newer_build_is_published() {
        // The whole point of the nightly channel: when YouTube breaks yt-dlp and the fix
        // lands upstream, the next job picks it up — one transfer, exe swapped, tag recorded
        // so the job after that is back to a free HEAD.
        let (url, gets) = fake_release_server(latest_location(), b"new-bytes");
        let dir = tmpdir("ensure_newer");
        std::fs::write(dir.join("yt-dlp.exe"), b"old-bytes").unwrap();
        std::fs::write(dir.join("yt-dlp.version"), "2026.08.17.000000").unwrap();

        let exe = ensure(&url, &dir, &|| false).expect("refresh must succeed");

        assert_eq!(std::fs::read(&exe).unwrap(), b"new-bytes", "the newer build must be installed");
        assert_eq!(gets.load(std::sync::atomic::Ordering::SeqCst), 1, "exactly one transfer");
        assert_eq!(std::fs::read_to_string(dir.join("yt-dlp.version")).unwrap().trim(), LATEST,
            "the marker must now name the installed build, or every job would re-download it");
    }

    /// A release server whose HEAD advertises `LATEST` but whose GET fails outright —
    /// the shape of GitHub's asset CDN having a bad minute.
    fn broken_transfer_server() -> String {
        use std::io::{Read as _, Write as _};
        let l = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = l.local_addr().unwrap();
        std::thread::spawn(move || {
            for stream in l.incoming() {
                let Ok(mut s) = stream else { break };
                let mut req = [0u8; 4096];
                let n = s.read(&mut req).unwrap_or(0);
                let _ = if String::from_utf8_lossy(&req[..n]).starts_with("HEAD ") {
                    write!(s, "HTTP/1.1 302 Found\r\nLocation: {}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
                           latest_location().unwrap())
                } else {
                    write!(s, "HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                };
                let _ = s.flush();
            }
        });
        format!("http://{addr}/yt-dlp.exe")
    }

    #[test]
    fn ensure_keeps_the_existing_exe_when_the_refresh_transfer_fails() {
        // A newer build exists but can't be fetched right now. The build we have was
        // working yesterday; a flaky CDN must not turn into a failed job.
        let url = broken_transfer_server();
        let dir = tmpdir("ensure_xfer_fail");
        std::fs::write(dir.join("yt-dlp.exe"), b"old-bytes").unwrap();
        std::fs::write(dir.join("yt-dlp.version"), "2026.08.17.000000").unwrap();

        let exe = ensure(&url, &dir, &|| false)
            .expect("a failed refresh must degrade to the existing exe, not fail the job");

        assert_eq!(std::fs::read(&exe).unwrap(), b"old-bytes");
        assert_eq!(std::fs::read_to_string(dir.join("yt-dlp.version")).unwrap().trim(), "2026.08.17.000000",
            "the marker must still describe the exe actually on disk");
    }

    fn age_file(path: &Path, by: Duration) {
        let f = std::fs::File::options().write(true).open(path).unwrap();
        f.set_modified(std::time::SystemTime::now() - by).unwrap();
    }

    #[test]
    fn ensure_blind_refreshes_a_stale_exe_when_the_latest_tag_is_unknowable() {
        // If GitHub ever stops answering HEAD with a tagged redirect, the tag compare can
        // never say "newer" — and a policy that only refreshes on "newer" would freeze
        // yt-dlp forever, which is exactly the quiet death the self-update exists to
        // prevent. Backstop: an exe older than a day gets refreshed blind, and since we
        // don't know what we installed, the marker is dropped rather than guessed.
        let (url, gets) = fake_release_server(None, b"new-bytes");
        let dir = tmpdir("ensure_blind_stale");
        let exe = dir.join("yt-dlp.exe");
        std::fs::write(&exe, b"old-bytes").unwrap();
        std::fs::write(dir.join("yt-dlp.version"), "2026.08.17.000000").unwrap();
        age_file(&exe, Duration::from_secs(48 * 3600));

        let got = ensure(&url, &dir, &|| false).expect("blind refresh must yield an exe");

        assert_eq!(gets.load(std::sync::atomic::Ordering::SeqCst), 1, "a day-old exe gets one blind transfer");
        assert_eq!(std::fs::read(&got).unwrap(), b"new-bytes");
        assert!(!dir.join("yt-dlp.version").exists(),
            "we don't know which build a blind refresh installed; a stale marker would be a lie");
    }

    #[test]
    fn ensure_leaves_a_fresh_exe_alone_when_the_latest_tag_is_unknowable() {
        // The other half of the backstop: "unknown" is not "stale". A build fetched hours
        // ago is kept, or a misbehaving endpoint would cost 18MB per job.
        let (url, gets) = fake_release_server(None, b"new-bytes");
        let dir = tmpdir("ensure_blind_fresh");
        std::fs::write(dir.join("yt-dlp.exe"), b"old-bytes").unwrap();

        let got = ensure(&url, &dir, &|| false).unwrap();

        assert_eq!(gets.load(std::sync::atomic::Ordering::SeqCst), 0);
        assert_eq!(std::fs::read(&got).unwrap(), b"old-bytes");
    }

    #[test]
    fn ensure_fetches_and_records_the_tag_when_no_exe_exists() {
        // First run on a fresh install: nothing usable, so the transfer is mandatory —
        // and the tag is recorded so the very next job is a free HEAD, not a second 18MB.
        let (url, gets) = fake_release_server(latest_location(), b"new-bytes");
        let dir = tmpdir("ensure_first_run");

        let exe = ensure(&url, &dir, &|| false).expect("first fetch must succeed");

        assert_eq!(std::fs::read(&exe).unwrap(), b"new-bytes");
        assert_eq!(gets.load(std::sync::atomic::Ordering::SeqCst), 1);
        assert_eq!(std::fs::read_to_string(dir.join("yt-dlp.version")).unwrap().trim(), LATEST);
    }

    #[test]
    fn ensure_keeps_the_existing_exe_when_github_cannot_be_reached() {
        // Offline (or GitHub down) with a build on disk: the job proceeds with it. The
        // probe is a nicety, never a dependency.
        let dir = tmpdir("ensure_offline");
        std::fs::write(dir.join("yt-dlp.exe"), b"old-bytes").unwrap();

        let exe = ensure("http://127.0.0.1:1/yt-dlp.exe", &dir, &|| false)
            .expect("an unreachable release server must not fail a job that has an exe");

        assert_eq!(std::fs::read(&exe).unwrap(), b"old-bytes");
    }

    fn tmpdir(tag: &str) -> std::path::PathBuf {
        let d = std::env::temp_dir().join(format!("wr_ytdlp_test_{tag}"));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    #[test]
    fn yt_dlp_comes_from_the_nightly_channel_not_stable() {
        // 2026-08-17: YouTube killed the android_vr client; stable 2026.07.04 (the latest
        // stable for 6+ weeks) kept failing every download while the fix sat in nightly
        // (PR #17461, merged 2026-08-18). yt-dlp's own answer to "YouTube broke again" is
        // `--update-to nightly`. Stable lags YouTube by weeks; nightly is built daily from
        // master and is the channel the maintainers point users at. Stay on it.
        assert!(YTDLP_URL.starts_with("https://github.com/yt-dlp/yt-dlp-nightly-builds/releases/latest/download/"),
            "must be the nightly channel's latest asset, got {YTDLP_URL}");
        assert!(YTDLP_URL.ends_with("/yt-dlp.exe"), "the official standalone Windows build");
    }

    #[test]
    fn the_build_tag_parses_from_githubs_release_asset_redirect() {
        // GitHub answers HEAD on `.../releases/latest/download/<asset>` with a 302 whose
        // Location names the concrete release — the cheapest possible "is there a newer
        // build?" probe (no 18MB transfer). Observed live 2026-08-20.
        let loc = "https://github.com/yt-dlp/yt-dlp-nightly-builds/releases/download/2026.08.18.122307/yt-dlp.exe";
        assert_eq!(tag_from_location(loc).as_deref(), Some("2026.08.18.122307"));
        // Anything else (a direct 200, a moved endpoint, a bare host) is "unknown", never a
        // made-up tag that would freeze refreshes forever.
        assert_eq!(tag_from_location("https://github.com/yt-dlp/yt-dlp-nightly-builds/releases/latest"), None);
        assert_eq!(tag_from_location("https://objects.githubusercontent.com/releases/download/"), None);
        assert_eq!(tag_from_location(""), None);
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
