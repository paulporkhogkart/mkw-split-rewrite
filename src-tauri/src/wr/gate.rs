//! The WR idle gate (spec 2026-07-17 §4). WR work may only run while live tracking is
//! stopped, or has shown no screen_change for WR_IDLE_MS. The SAME predicate, negated,
//! is the in-flight job's cancel: any screen change closes the gate AND cancels.

// SeqCst throughout: the runner thread reads these; two Relaxed stores could formally let it see tracking=true with a stale activity timestamp. Frequency makes the cost irrelevant.
use std::sync::atomic::{AtomicBool, AtomicI64, Ordering};

/// 10 minutes (spec §6.2 WR_IDLE_MINUTES).
pub const WR_IDLE_MS: i64 = 10 * 60 * 1000;

/// Live-tracker activity signals, maintained by lib.rs's engine-stdout forwarder and the
/// sidecar spawn/kill paths. Plain atomics: written from the forwarder's async task and
/// read from the runner thread.
pub struct TrackerActivity {
    tracking: AtomicBool,
    last_change_ms: AtomicI64,
}

impl TrackerActivity {
    pub const fn new() -> Self {
        Self { tracking: AtomicBool::new(false), last_change_ms: AtomicI64::new(0) }
    }

    /// Any screen_change event — the ONLY thing that resets the idle clock (decided
    /// 2026-07-17: navigating menus counts as activity; the engine's 0.2s heartbeats
    /// and other chatter do not).
    pub fn note_screen_change(&self) {
        self.last_change_ms.store(now_epoch_ms(), Ordering::SeqCst);
    }

    /// Turning tracking ON also counts as activity, so the gate shuts the moment the
    /// live engine starts rather than 10 minutes later.
    pub fn set_tracking(&self, on: bool) {
        if on { self.note_screen_change(); }
        self.tracking.store(on, Ordering::SeqCst);
    }

    pub fn tracking_running(&self) -> bool { self.tracking.load(Ordering::SeqCst) }
    pub fn last_change_epoch_ms(&self) -> i64 { self.last_change_ms.load(Ordering::SeqCst) }
}

/// Process-wide instance. A static (not app-managed state) because the wr runner thread
/// and lib.rs's forwarder both need it without threading an AppHandle through pure code.
pub static ACTIVITY: TrackerActivity = TrackerActivity::new();

pub fn now_epoch_ms() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0)
}

/// The gate predicate (pure — the runner and the cancel closure share it).
/// Open = tracking stopped, or no screen change for WR_IDLE_MS.
pub fn gate_open(tracking_running: bool, last_change_epoch_ms: i64, now_epoch_ms: i64) -> bool {
    !tracking_running || (now_epoch_ms - last_change_epoch_ms) >= WR_IDLE_MS
}

#[cfg(test)]
mod tests {
    use super::*;

    const T0: i64 = 1_700_000_000_000;
    const MIN: i64 = 60_000;

    #[test]
    fn gate_truth_table() {
        // Tracking stopped: always open, staleness irrelevant.
        assert!(gate_open(false, T0, T0));
        assert!(gate_open(false, T0, T0 + 1));
        // Tracking running, fresh activity: closed.
        assert!(!gate_open(true, T0, T0));
        assert!(!gate_open(true, T0, T0 + 9 * MIN));
        // Running, exactly at the threshold: open (>=, not >).
        assert!(gate_open(true, T0, T0 + WR_IDLE_MS));
        assert!(gate_open(true, T0, T0 + WR_IDLE_MS + 1));
    }

    #[test]
    fn activity_updates_move_the_clock() {
        let a = TrackerActivity::new();
        assert!(!a.tracking_running());
        a.set_tracking(true);
        assert!(a.tracking_running());
        let before = a.last_change_epoch_ms();
        assert!(before > 0, "set_tracking(true) must count as activity, or the gate \
                             would open the instant tracking starts");
        a.note_screen_change();
        assert!(a.last_change_epoch_ms() >= before);
        a.set_tracking(false);
        assert!(!a.tracking_running());
    }

    #[test]
    fn fresh_activity_state_is_open_gate() {
        // Boot state: never tracked, never saw a screen — the gate must be open.
        let a = TrackerActivity::new();
        assert!(gate_open(a.tracking_running(), a.last_change_epoch_ms(), now_epoch_ms()));
    }
}
