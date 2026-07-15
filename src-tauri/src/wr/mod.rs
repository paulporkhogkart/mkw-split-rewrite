//! The WR trail-extraction service: claim a world record from the Pi, download its
//! YouTube video, replay it through the detection engine, verify, upload the trail.
//!
//! Lives INSIDE pbenguin rather than as a second binary (spec §6): the app already
//! ships, updates, bundles the engine exe and holds the player token.

pub mod engine;
pub mod job;
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
