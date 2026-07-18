//! The WR trail-extraction service: claim a world record from the Pi, download its
//! YouTube video, replay it through the detection engine, verify, upload the trail.
//!
//! Lives INSIDE pbenguin rather than as a second binary (spec §6): the app already
//! ships, updates, bundles the engine exe and holds the player token.

pub mod engine;
pub mod gate;
pub mod job;
pub mod phase;
pub mod runner;
pub mod service;
pub mod state;
pub mod verify;
pub mod ytdlp;

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
    /// The engine (or yt-dlp) could not be spawned, or the engine produced stderr output
    /// and no trail — a crash, not a merely marginal video. OUR fault, not the video's.
    /// Distinct from `NoTrail` on purpose: it gives the Pi's `last_error` an honest signal
    /// (this is our bug, not a hard-to-track video) and keeps the crash visible in logs
    /// instead of it silently folding into the same bucket as a video whose minimap simply
    /// never locked. The job still stays claimable up to the attempts cap either way — the
    /// split is about what gets reported, not about escalating to a different tier.
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

/// PROBE, not gated to debug builds: claim and process exactly one job, then report.
/// Plan 3 replaces this with the real loop behind the settings toggle + idle gate.
///
/// Takes `server_url`/`token` explicitly rather than reading sync.rs's CONFIG, so a probe
/// can never accidentally claim from the real Pi.
///
/// HONEST GATING NOTE (this used to be commented "DEV ONLY", which overstated things): this
/// command is registered — and so webview-invokable — in every build, debug or release; it
/// is NOT behind `#[cfg(debug_assertions)]`. It was deliberately left that way rather than
/// runtime-gated: this module (`service`/`verify`/`ytdlp`/`job`) has no other caller yet
/// (Plan 3 supplies the real one), so cfg-gating this command's body to debug-only would
/// make the ENTIRE wr:: call graph unreachable dead code in a release build — trading one
/// low-risk warning for ~40 real ones. The actual risk of staying invokable is low: it
/// takes `server_url`/`token` as explicit caller-supplied args, so a webview call can never
/// reach the real Pi or the real player token on its own — reaching those requires already
/// knowing them.
#[tauri::command]
pub async fn wr_process_one(app: tauri::AppHandle, server_url: String, token: String)
    -> Result<String, String> {
    let dir = wr_data_dir(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        let cfg = service::ServiceCfg {
            server_url, token, data_dir: dir, engine: engine::EnginePath::resolve(),
        };
        format!("{:?}", service::process_one(&cfg, &|| false))
    }).await.map_err(|e| e.to_string())
}

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
    if key == state::SETTING_RUN_WR_SERVICE {
        use tauri::Manager;
        let rs = app.state::<crate::RunnerState>();
        // Take under the lock, stop OUTSIDE it: stop() joins the runner thread, whose
        // terminal refresh hook (Task 7's tray) locks RunnerState — joining under the
        // guard would self-deadlock the toggle-off path.
        let taken = {
            let mut guard = rs.0.lock().unwrap_or_else(|e| e.into_inner());
            match (value, guard.is_some()) {
                (true, false) => {
                    let r = runner::Runner::start(app.clone());
                    let h = app.clone();
                    r.set_refresh_hook(Box::new(move || crate::tray::refresh_tray_status(&h)));
                    *guard = Some(r);
                    None
                }
                (false, true) => guard.take(),
                _ => None,
            }
        };
        if let Some(r) = taken { r.stop(); }
    }
    if key == state::SETTING_START_AT_LOGIN {
        use tauri_plugin_autostart::ManagerExt;
        let mgr = app.autolaunch();
        let res = if value { mgr.enable() } else { mgr.disable() };
        if let Err(e) = res {
            return Err(format!("autostart registration failed: {e}"));
        }
    }
    crate::tray::sync_tray(&app);
    Ok(())
}
