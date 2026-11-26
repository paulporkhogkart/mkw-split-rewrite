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
/// EVERY `Native1080p60` branch must keep `[fps=60]`. Never add a `bestvideo[height=1080]`
/// branch to it: that MATCHES 1080p30 while the 1080p60 is still processing, so the job
/// would silently take the worse stream of a video whose good one is an hour away.
/// Matching nothing is the INTENDED outcome: yt-dlp reports "Requested format is not
/// available" (`WrError::No1080p60`), and `download_with_fallback` then PROBES the video to
/// decide, from evidence, whether 1080p60 is coming (wait) or never will (`Fallback1080p30`).
/// The lower-fps tier lives behind that probe, never inside this selector.
pub fn format_selector(tier: Tier) -> &'static str {
    match tier {
        Tier::Native1080p60 =>
            "bestvideo[height=1080][fps=60][vcodec^=avc1]/bestvideo[height=1080][fps=60]",
        Tier::Fallback1080p30 =>
            "bestvideo[height=1080][fps<=30][vcodec^=avc1]/bestvideo[height=1080][fps<=30]",
    }
}

/// One video-bearing format from `yt-dlp -J` (audio-only and storyboards dropped).
#[derive(Debug, Clone, PartialEq)]
pub struct VideoFormat { pub height: i64, pub fps: f64 }

/// What the processing-state decision needs from `yt-dlp -J`.
#[derive(Debug, Clone, PartialEq)]
pub struct Probe {
    /// Unix seconds. `timestamp` when yt-dlp has it (YouTube always has, measured), else
    /// midnight UTC of `upload_date`.
    pub uploaded_at: Option<i64>,
    pub formats: Vec<VideoFormat>,
}

/// How old a 30fps-only upload must be before we believe no 60fps version is coming.
/// The format list alone already separates "60fps source mid-processing" (720p60 exists)
/// from "30fps source" — this is the belt-and-braces for whatever the list cannot show.
pub const FALLBACK_MIN_AGE_SECS: i64 = 24 * 3600;

/// Above this a format is "high fps". YouTube reports integer rates (24/25/30/48/50/60);
/// the slack covers a 30.0x oddity without letting 48+ through.
const HIGH_FPS: f64 = 31.0;

pub fn parse_probe(json: &str) -> Result<Probe, String> {
    let v: serde_json::Value = serde_json::from_str(json).map_err(|e| format!("probe json: {e}"))?;
    let formats = v.get("formats").and_then(|f| f.as_array())
        .ok_or_else(|| "probe json: no formats list".to_string())?;
    let formats = formats.iter().filter_map(|f| {
        let vcodec = f.get("vcodec").and_then(|c| c.as_str()).unwrap_or("none");
        if vcodec == "none" { return None; }
        Some(VideoFormat {
            height: f.get("height").and_then(|h| h.as_i64())?,
            fps: f.get("fps").and_then(|r| r.as_f64()).unwrap_or(0.0),
        })
    }).collect();
    let uploaded_at = v.get("timestamp").and_then(|t| t.as_i64())
        .or_else(|| v.get("upload_date").and_then(|d| d.as_str()).and_then(upload_date_to_unix));
    Ok(Probe { uploaded_at, formats })
}

/// `YYYYMMDD` -> unix seconds at 00:00 UTC (days-from-civil, proleptic Gregorian).
fn upload_date_to_unix(d: &str) -> Option<i64> {
    if d.len() != 8 || !d.bytes().all(|b| b.is_ascii_digit()) { return None; }
    let (y, m, day): (i64, i64, i64) = (d[..4].parse().ok()?, d[4..6].parse().ok()?, d[6..].parse().ok()?);
    if !(1..=12).contains(&m) || !(1..=31).contains(&day) { return None; }
    let y = if m <= 2 { y - 1 } else { y };
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let doy = (153 * (if m > 2 { m - 3 } else { m + 9 }) + 2) / 5 + day - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    Some((era * 146_097 + doe - 719_468) * 86_400)
}

#[derive(Debug, Clone, PartialEq)]
pub enum HdVerdict {
    /// The source is 30fps and old enough: 1080p30 is the best this video will ever have.
    Fallback1080p30,
    /// 1080p60 may still come (or nothing usable exists yet). The detail is for humans.
    Wait(String),
}

/// Decide, from the format list, whether a missing 1080p60 is "still processing" or
/// "never coming". Errs toward waiting: a wrong wait costs a retry on the cooldown, a
/// wrong fallback costs a 30fps trail stored forever when a 60fps one was an hour away.
///
/// YouTube only serves 60fps from 720p up, and processes low resolutions first. So
///  * any format above 30fps => a 60fps source => its 1080p60 is coming: WAIT, however old;
///  * no 1080p at all        => nothing to fall back to yet: WAIT;
///  * 1080p, but nothing above 30fps anywhere => a 30fps source — FALL BACK once the upload
///    is past FALLBACK_MIN_AGE_SECS (unknown age never falls back).
pub fn hd_verdict(p: &Probe, now_unix: i64) -> HdVerdict {
    if p.formats.iter().any(|f| f.height == 1080 && f.fps > HIGH_FPS) {
        return HdVerdict::Wait("1080p60 is listed but the download missed it; retrying".into());
    }
    if let Some(best) = p.formats.iter().filter(|f| f.fps > HIGH_FPS).max_by_key(|f| f.height) {
        return HdVerdict::Wait(format!("{}p{} is out, 1080p60 still processing", best.height, best.fps.round()));
    }
    if !p.formats.iter().any(|f| f.height == 1080) {
        let best = p.formats.iter().max_by_key(|f| f.height)
            .map(|f| format!("{}p{}", f.height, f.fps.round())).unwrap_or_else(|| "nothing".into());
        return HdVerdict::Wait(format!("no 1080p yet (best {best})"));
    }
    match p.uploaded_at {
        None => HdVerdict::Wait("30fps upload of unknown age; not falling back blind".into()),
        Some(t) if now_unix - t < FALLBACK_MIN_AGE_SECS => HdVerdict::Wait(format!(
            "30fps upload, {}h old; 1080p30 fallback at {}h",
            (now_unix - t).max(0) / 3600, FALLBACK_MIN_AGE_SECS / 3600)),
        Some(_) => HdVerdict::Fallback1080p30,
    }
}

/// The download policy, with the two yt-dlp calls injected (tests script them):
/// 1080p60 first; only a MISSING-FORMAT failure triggers a probe, and only a
/// `Fallback1080p30` verdict triggers a second download. Returns the tier actually used.
pub(crate) fn fallback_flow(
    mut download_tier: impl FnMut(Tier) -> Result<(), WrError>,
    probe: impl FnOnce() -> Result<Probe, WrError>,
    now_unix: i64,
) -> Result<Tier, WrError> {
    match download_tier(Tier::Native1080p60) {
        Ok(()) => return Ok(Tier::Native1080p60),
        Err(WrError::No1080p60) => {}
        Err(e) => return Err(e),
    }
    let p = match probe() {
        Ok(p) => p,
        Err(WrError::Cancelled) => return Err(WrError::Cancelled),
        // No evidence, no fallback: report the plain truth.
        Err(e) => { log::warn!("[wr] format probe failed: {}", e.reason()); return Err(WrError::No1080p60); }
    };
    match hd_verdict(&p, now_unix) {
        HdVerdict::Wait(d) => Err(WrError::AwaitingHd(d)),
        HdVerdict::Fallback1080p30 => {
            log::info!("[wr] no 1080p60 will come (30fps source); falling back to 1080p30");
            download_tier(Tier::Fallback1080p30).map(|()| Tier::Fallback1080p30)
        }
    }
}

/// Bound on the metadata-only probe (one page fetch; seconds in practice).
const PROBE_CAP: std::time::Duration = std::time::Duration::from_secs(90);

/// `yt-dlp -J`: the video's metadata incl. every format, no download.
pub fn probe(exe: &Path, url: &str, cancel: &(dyn Fn() -> bool + Sync)) -> Result<Probe, WrError> {
    let mut cmd = std::process::Command::new(exe);
    cmd.args(["-J", "--no-playlist", "--no-warnings", "--", url]);
    let r = run_tool(cmd, PROBE_CAP, cancel, true)?;
    if !r.success { return Err(classify_failure(&r.stderr)); }
    parse_probe(&r.stdout).map_err(WrError::DownloadFailed)
}

/// `download` with the evidence-gated 30fps fallback (see `fallback_flow`).
pub fn download_with_fallback(
    exe: &Path, url: &str, dest: &Path, cancel: &(dyn Fn() -> bool + Sync),
) -> Result<Tier, WrError> {
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64).unwrap_or(0);
    fallback_flow(|t| download(exe, url, t, dest, cancel), || probe(exe, url, cancel), now)
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
    cmd: std::process::Command,
    cap: std::time::Duration,
    cancel: &(dyn Fn() -> bool + Sync),
) -> Result<(bool, String), WrError> {
    run_tool(cmd, cap, cancel, false).map(|r| (r.success, r.stderr))
}

pub(crate) struct ToolRun { pub success: bool, pub stdout: String, pub stderr: String }

/// The shared yt-dlp process shell (see `run_download`'s doc). With `capture_stdout` the
/// child's stdout is collected too (the `-J` probe's JSON); both pipes then drain on their
/// own threads, because a child blocked writing a full stdout pipe never closes stderr.
pub(crate) fn run_tool(
    mut cmd: std::process::Command,
    cap: std::time::Duration,
    cancel: &(dyn Fn() -> bool + Sync),
    capture_stdout: bool,
) -> Result<ToolRun, WrError> {
    use std::io::Read;
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::Mutex;

    cmd.stdin(std::process::Stdio::null())
        .stdout(if capture_stdout { std::process::Stdio::piped() } else { std::process::Stdio::null() })
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
    let stdout = child.stdout.take();

    let started = std::time::Instant::now();
    let child = Mutex::new(child);
    let done = AtomicBool::new(false);
    let cancelled = AtomicBool::new(false);
    let timed_out = AtomicBool::new(false);

    let (stdout_text, stderr_text) = std::thread::scope(|s| {
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
        let out = stdout.map(|mut o| s.spawn(move || {
            let mut buf = String::new();
            let _ = o.read_to_string(&mut buf);
            buf
        }));
        // Drain stderr to EOF (yt-dlp's stderr is small; no ring needed). Blocks until
        // the child exits or is killed — either closes the pipe.
        let mut buf = String::new();
        let mut rdr = stderr;
        let _ = rdr.read_to_string(&mut buf);
        let out = out.map(|h| h.join().unwrap_or_default()).unwrap_or_default();
        done.store(true, Ordering::Relaxed);
        (out, buf)
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
    Ok(ToolRun { success: status.success(), stdout: stdout_text, stderr: stderr_text })
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

    // ---- 1080p60-missing fallback (2026-11-27) -------------------------------------------

    const DAY: i64 = 24 * 3600;
    const NOW: i64 = 1_795_700_000;

    /// A `yt-dlp -J` document in the real shape (trimmed): video formats carry height/fps,
    /// audio-only and storyboard formats carry vcodec "none" and must be ignored.
    fn probe_json(ts: Option<i64>, video: &[(i64, f64)]) -> String {
        let mut fs: Vec<String> = video.iter()
            .map(|(h, f)| format!(r#"{{"format_id":"x","vcodec":"avc1.640028","height":{h},"fps":{f}}}"#))
            .collect();
        fs.push(r#"{"format_id":"140","vcodec":"none","acodec":"mp4a.40.2","height":null,"fps":null}"#.into());
        fs.push(r#"{"format_id":"sb0","vcodec":"none","ext":"mhtml","height":90,"fps":0.5}"#.into());
        let ts = ts.map(|t| t.to_string()).unwrap_or_else(|| "null".into());
        format!(r#"{{"id":"v","timestamp":{ts},"upload_date":"20261126","formats":[{}]}}"#, fs.join(","))
    }
    /// What a 60fps upload looks like (measured on real WR videos 2026-11-27): <=480p is 30fps,
    /// 60fps exists only from 720p up.
    const FULL_60: &[(i64, f64)] = &[(144, 30.0), (360, 30.0), (480, 30.0), (720, 60.0), (1080, 60.0)];
    const MID_PROCESSING_60: &[(i64, f64)] = &[(144, 30.0), (360, 30.0), (480, 30.0), (720, 60.0)];
    const EARLY_PROCESSING: &[(i64, f64)] = &[(144, 30.0), (360, 30.0)];
    const UPLOAD_30: &[(i64, f64)] = &[(144, 30.0), (360, 30.0), (720, 30.0), (1080, 30.0)];

    fn probe_of(ts: Option<i64>, video: &[(i64, f64)]) -> Probe {
        parse_probe(&probe_json(ts, video)).expect("fixture parses")
    }

    #[test]
    fn parse_probe_keeps_video_formats_and_the_upload_time_only() {
        let p = probe_of(Some(NOW), UPLOAD_30);
        assert_eq!(p.uploaded_at, Some(NOW));
        assert_eq!(p.formats.len(), UPLOAD_30.len(), "audio + storyboard (vcodec none) dropped: {p:?}");
        assert!(p.formats.contains(&VideoFormat { height: 1080, fps: 30.0 }));
    }

    #[test]
    fn parse_probe_falls_back_to_upload_date_when_there_is_no_timestamp() {
        let p = parse_probe(r#"{"upload_date":"20261126","formats":[]}"#).unwrap();
        assert_eq!(p.uploaded_at, Some(1_795_651_200), "2026-11-26T00:00:00Z");
        let p = parse_probe(r#"{"formats":[]}"#).unwrap();
        assert_eq!(p.uploaded_at, None);
    }

    #[test]
    fn parse_probe_rejects_a_document_without_formats() {
        assert!(parse_probe("not json").is_err());
        assert!(parse_probe(r#"{"id":"v"}"#).is_err(), "no formats list = not a usable probe");
    }

    #[test]
    fn an_old_30fps_upload_falls_back_to_1080p30() {
        assert_eq!(hd_verdict(&probe_of(Some(NOW - 3 * DAY), UPLOAD_30), NOW), HdVerdict::Fallback1080p30);
    }

    #[test]
    fn a_young_30fps_upload_waits_out_the_age_gate() {
        // Exactly the worry: everything LOOKS final, but the upload is fresh.
        let v = hd_verdict(&probe_of(Some(NOW - 2 * 3600), UPLOAD_30), NOW);
        assert!(matches!(v, HdVerdict::Wait(_)), "{v:?}");
        let v = hd_verdict(&probe_of(Some(NOW - FALLBACK_MIN_AGE_SECS + 60), UPLOAD_30), NOW);
        assert!(matches!(v, HdVerdict::Wait(_)), "a minute short of the gate is still a wait: {v:?}");
        assert_eq!(hd_verdict(&probe_of(Some(NOW - FALLBACK_MIN_AGE_SECS), UPLOAD_30), NOW),
                   HdVerdict::Fallback1080p30, "at the gate exactly: fall back");
    }

    #[test]
    fn a_60fps_upload_mid_processing_never_falls_back_however_old() {
        // 720p60 is out, 1080p60 is not yet: the 60fps source is proven, so 1080p60 IS
        // coming. Age must not override that - not even a month.
        for age in [3600, 3 * DAY, 30 * DAY] {
            match hd_verdict(&probe_of(Some(NOW - age), MID_PROCESSING_60), NOW) {
                HdVerdict::Wait(d) => assert!(d.contains("720p60") && d.contains("still processing"),
                                              "detail names what IS out: {d}"),
                other => panic!("age {age}: a 60fps source must wait for 1080p60, got {other:?}"),
            }
        }
    }

    #[test]
    fn sixty_fps_evidence_beats_a_listed_1080p30_however_old() {
        // The case the fps guard exists for: a 1080p30 rendition IS listed (so the plain
        // "no 1080p" check would not stop it), but a 60fps format proves the source is
        // 60fps — taking the 30fps stream would store a worse trail forever.
        let mixed: &[(i64, f64)] = &[(360, 30.0), (720, 60.0), (1080, 30.0)];
        match hd_verdict(&probe_of(Some(NOW - 30 * DAY), mixed), NOW) {
            HdVerdict::Wait(d) => assert!(d.contains("still processing"), "{d}"),
            other => panic!("a proven 60fps source must never fall back to 30fps, got {other:?}"),
        }
    }

    #[test]
    fn early_processing_with_no_1080p_at_all_waits() {
        match hd_verdict(&probe_of(Some(NOW - 30 * DAY), EARLY_PROCESSING), NOW) {
            HdVerdict::Wait(d) => assert!(d.contains("no 1080p"), "{d}"),
            other => panic!("nothing at 1080p means nothing to fall back to, got {other:?}"),
        }
    }

    #[test]
    fn an_unknown_upload_time_never_falls_back() {
        let v = hd_verdict(&probe_of(None, UPLOAD_30), NOW);
        assert!(matches!(v, HdVerdict::Wait(_)), "no age = cannot prove it is finished: {v:?}");
    }

    #[test]
    fn a_listed_1080p60_is_not_a_reason_to_fall_back() {
        // The selector missed but the probe lists 1080p60 (processing finished in between,
        // or a codec quirk): the right move is the next attempt at 1080p60, not 30fps.
        let v = hd_verdict(&probe_of(Some(NOW - 30 * DAY), FULL_60), NOW);
        assert!(matches!(v, HdVerdict::Wait(_)), "{v:?}");
    }

    #[test]
    fn the_fallback_selector_is_1080p_and_never_60fps_or_lower_res() {
        let s = format_selector(Tier::Fallback1080p30);
        for branch in s.split('/') {
            assert!(branch.contains("height=1080"), "branch without 1080p: {branch}");
            assert!(branch.contains("fps<=30"), "branch must stay the 30fps tier: {branch}");
        }
        assert!(s.split('/').next().unwrap().contains("vcodec^=avc1"), "avc1 preferred first");
    }

    /// Drive `fallback_flow` with scripted download/probe results; records which tiers it
    /// asked for and whether it probed.
    fn flow(first: Result<(), WrError>, second: Result<(), WrError>, probe: Result<Probe, WrError>)
        -> (Result<Tier, WrError>, Vec<Tier>, bool) {
        let mut asked = Vec::new();
        let mut results = vec![second, first];
        let mut probed = false;
        let out = fallback_flow(
            |t| { asked.push(t); results.pop().expect("at most two downloads") },
            || { probed = true; probe },
            NOW);
        (out, asked, probed)
    }

    #[test]
    fn a_normal_1080p60_download_never_probes() {
        let (out, asked, probed) = flow(Ok(()), Ok(()), Err(WrError::Timeout));
        assert_eq!(out, Ok(Tier::Native1080p60));
        assert_eq!(asked, vec![Tier::Native1080p60]);
        assert!(!probed, "the common path must stay one yt-dlp call");
    }

    #[test]
    fn missing_1080p60_on_an_old_30fps_upload_downloads_the_fallback() {
        let (out, asked, probed) = flow(Err(WrError::No1080p60), Ok(()),
                                        Ok(probe_of(Some(NOW - 3 * DAY), UPLOAD_30)));
        assert!(probed);
        assert_eq!(asked, vec![Tier::Native1080p60, Tier::Fallback1080p30]);
        assert_eq!(out, Ok(Tier::Fallback1080p30));
    }

    #[test]
    fn missing_1080p60_mid_processing_reports_awaiting_hd_without_a_second_download() {
        let (out, asked, _) = flow(Err(WrError::No1080p60), Ok(()),
                                   Ok(probe_of(Some(NOW - 3600), MID_PROCESSING_60)));
        assert_eq!(asked, vec![Tier::Native1080p60]);
        match out {
            Err(e @ WrError::AwaitingHd(_)) => assert!(e.reason().starts_with("awaiting_hd: "), "{}", e.reason()),
            other => panic!("expected AwaitingHd, got {other:?}"),
        }
    }

    #[test]
    fn other_download_failures_pass_through_unprobed() {
        let (out, _, probed) = flow(Err(WrError::DownloadFailed("HTTP Error 403".into())), Ok(()),
                                    Ok(probe_of(Some(NOW - 3 * DAY), UPLOAD_30)));
        assert!(matches!(out, Err(WrError::DownloadFailed(_))), "{out:?}");
        assert!(!probed, "only a missing-format failure is a quality question");
    }

    #[test]
    fn a_cancel_during_the_probe_stays_a_cancel() {
        let (out, _, _) = flow(Err(WrError::No1080p60), Ok(()), Err(WrError::Cancelled));
        assert_eq!(out, Err(WrError::Cancelled), "cancel must stay distinct (release, not fail)");
    }

    #[test]
    fn a_failed_probe_keeps_the_honest_no_1080p60() {
        let (out, asked, _) = flow(Err(WrError::No1080p60), Ok(()),
                                   Err(WrError::DownloadFailed("probe broke".into())));
        assert_eq!(out, Err(WrError::No1080p60));
        assert_eq!(asked.len(), 1, "no evidence = no fallback download");
    }

    /// LIVE (network): the real `probe()` against a real yt-dlp and the Mario Circuit WR
    /// fixture video (a 60fps upload). `WR_YTDLP=<path to yt-dlp.exe> cargo test --lib
    /// live_probe -- --ignored`.
    #[test]
    #[ignore]
    fn live_probe_reads_a_real_format_list() {
        let exe = std::env::var("WR_YTDLP").expect("set WR_YTDLP to a yt-dlp.exe");
        let p = probe(Path::new(&exe), "https://www.youtube.com/watch?v=blldIdJg7zo", &|| false)
            .expect("probe");
        assert!(p.uploaded_at.is_some(), "{p:?}");
        assert!(p.formats.iter().any(|f| f.height == 1080 && f.fps > HIGH_FPS), "{:?}", p.formats);
        assert!(p.formats.iter().all(|f| f.height > 0));
    }

    #[test]
    fn run_tool_captures_stdout_alongside_stderr() {
        // The probe needs stdout (the JSON) - run_download nulls it. Both pipes must be
        // drained concurrently or a big -J document deadlocks the child.
        let mut c = Command::new("python");
        c.args(["-c", "import sys; sys.stdout.write('x'*200000); sys.stderr.write('warn')"]);
        let r = run_tool(c, Duration::from_secs(30), &|| false, true).expect("runs");
        assert!(r.success);
        assert_eq!(r.stdout.len(), 200_000);
        assert_eq!(r.stderr, "warn");
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
