//! The WR service loop (spec 2026-07-17 §4): one OS thread — gate check ->
//! process_one -> outcome-driven backoff. Blocking reqwest on a plain thread is the
//! house pattern (sync.rs does the same); no tokio.

use super::service::{self, Outcome, ServiceCfg};
use super::gate;
// `WrError` is unused outside `#[cfg(test)]` — imported solely so the test module's
// `use super::*` can name it (its Outcome::Failed case).
#[cfg(test)]
use super::WrError;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

/// Next sleep before another claim attempt, from what just happened. `prev_idle` is the
/// previous Idle backoff (doubling), ignored for the other arms. Named (not `_`)
/// bindings below are deliberate: a bare `_` does not count as reading the matched
/// field under rustc's dead_code analysis, and Outcome's fields have no other reader
/// yet — see service.rs's Outcome for the same fields, constructed but not consumed
/// beyond logging.
pub fn next_backoff(outcome: &Outcome, prev_idle: Duration) -> Duration {
    match outcome {
        Outcome::Completed(_id) => Duration::ZERO,
        Outcome::Idle => {
            let next = if prev_idle.is_zero() { 60 } else { prev_idle.as_secs().saturating_mul(2) };
            Duration::from_secs(next.clamp(60, 300))
        }
        Outcome::Error(_msg) => Duration::from_secs(120),
        Outcome::Failed(_id, _err) => Duration::from_secs(30),
        Outcome::Released(_id) => Duration::from_secs(30),
    }
}

/// Handle to the loop thread. Dropping without stop() leaves the thread running until
/// app exit; stop() is the orderly path (quit, or the run_wr_service toggle going off).
pub struct Runner {
    shutdown: Arc<AtomicBool>,
    paused: Arc<AtomicBool>,
    handle: Option<std::thread::JoinHandle<()>>,
    refresh: Arc<Mutex<Option<Box<dyn Fn() + Send>>>>,
}

impl Runner {
    /// Spawn the loop. It claims nothing until the gate is open AND sync's CONFIG has a
    /// server + token (reads the ordinary player token — no second credential store).
    pub fn start(app: tauri::AppHandle) -> Runner {
        let shutdown = Arc::new(AtomicBool::new(false));
        let paused = Arc::new(AtomicBool::new(false));
        let refresh: Arc<Mutex<Option<Box<dyn Fn() + Send>>>> = Arc::new(Mutex::new(None));
        let (sd, pd, rf) = (shutdown.clone(), paused.clone(), refresh.clone());

        let handle = std::thread::Builder::new()
            .name("wr-runner".into())
            .spawn(move || run_loop(app, sd, pd, rf))
            .expect("spawn wr-runner thread");

        Runner { shutdown, paused, handle: Some(handle), refresh }
    }

    /// Task 7 installs the tray-refresh hook here (runner -> tray, decoupled).
    pub fn set_refresh_hook(&self, hook: Box<dyn Fn() + Send>) {
        *self.refresh.lock().unwrap_or_else(|e| e.into_inner()) = Some(hook);
    }

    pub fn set_paused(&self, paused: bool) {
        self.paused.store(paused, Ordering::Relaxed);
    }
    pub fn is_paused(&self) -> bool { self.paused.load(Ordering::Relaxed) }

    /// Orderly shutdown: flags the loop (whose cancel closure aborts any in-flight job,
    /// releasing the lease) and joins. The engine watchdog polls at 250ms and the
    /// download watchdog likewise, so the join resolves within a few seconds worst-case.
    pub fn stop(mut self) {
        self.shutdown.store(true, Ordering::Relaxed);
        if let Some(h) = self.handle.take() { let _ = h.join(); }
    }
}

fn ping_refresh(refresh: &Arc<Mutex<Option<Box<dyn Fn() + Send>>>>) {
    if let Some(f) = refresh.lock().unwrap_or_else(|e| e.into_inner()).as_ref() { f(); }
}

/// Sleep `d` in 1s slices so shutdown/pause stay responsive.
fn interruptible_sleep(d: Duration, shutdown: &AtomicBool) {
    let mut left = d;
    while !left.is_zero() && !shutdown.load(Ordering::Relaxed) {
        let step = left.min(Duration::from_secs(1));
        std::thread::sleep(step);
        left = left.saturating_sub(step);
    }
}

fn run_loop(
    app: tauri::AppHandle,
    shutdown: Arc<AtomicBool>,
    paused: Arc<AtomicBool>,
    refresh: Arc<Mutex<Option<Box<dyn Fn() + Send>>>>,
) {
    let mut idle_backoff = Duration::ZERO;
    while !shutdown.load(Ordering::Relaxed) {
        let gate_now = gate::gate_open(
            gate::ACTIVITY.tracking_running(),
            gate::ACTIVITY.last_change_epoch_ms(),
            gate::now_epoch_ms(),
        );
        if paused.load(Ordering::Relaxed) || !gate_now {
            ping_refresh(&refresh);
            interruptible_sleep(Duration::from_secs(30), &shutdown);
            continue;
        }
        let (server_url, token) = crate::sync::config_snapshot();
        if server_url.trim().is_empty() || token.trim().is_empty() {
            ping_refresh(&refresh);
            interruptible_sleep(Duration::from_secs(60), &shutdown);
            continue;
        }
        let Ok(data_dir) = super::wr_data_dir(&app) else {
            interruptible_sleep(Duration::from_secs(120), &shutdown);
            continue;
        };
        let cfg = ServiceCfg {
            server_url, token, data_dir,
            engine: super::engine::EnginePath::resolve(),
        };
        // The cancel closure IS the negated gate (spec: one consistent rule), plus
        // pause and shutdown. Any screen change mid-job aborts -> release -> refund.
        let sd2 = shutdown.clone();
        let pd2 = paused.clone();
        let cancel = move || {
            sd2.load(Ordering::Relaxed)
                || pd2.load(Ordering::Relaxed)
                || !gate::gate_open(
                    gate::ACTIVITY.tracking_running(),
                    gate::ACTIVITY.last_change_epoch_ms(),
                    gate::now_epoch_ms(),
                )
        };
        ping_refresh(&refresh);
        // Contain a panicking job: log, long backoff, keep serving (the PROCESS_LOCK
        // already recovers from poisoning).
        let outcome = match std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            service::process_one(&cfg, &cancel)
        })) {
            Ok(o) => o,
            Err(_) => {
                super::phase::set(None); // a panicked job must not leave a stale tooltip phase
                log::error!("[wr] process_one panicked; backing off 5 min");
                ping_refresh(&refresh);
                interruptible_sleep(Duration::from_secs(300), &shutdown);
                continue;
            }
        };
        log::info!("[wr] runner outcome: {outcome:?}");
        idle_backoff = next_backoff(&outcome, idle_backoff);
        if !matches!(outcome, Outcome::Idle) && !matches!(outcome, Outcome::Completed(_)) {
            // Non-idle outcomes use their fixed backoff; reset the idle ladder.
            let pause = idle_backoff;
            idle_backoff = Duration::ZERO;
            ping_refresh(&refresh);
            interruptible_sleep(pause, &shutdown);
            continue;
        }
        ping_refresh(&refresh);
        interruptible_sleep(idle_backoff, &shutdown);
        if matches!(outcome, Outcome::Completed(_)) { idle_backoff = Duration::ZERO; }
    }
    ping_refresh(&refresh);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn backoff_schedule_per_outcome() {
        let base = Duration::from_secs(60);
        // Completed: straight back to work — there may be a queue to drain.
        assert_eq!(next_backoff(&Outcome::Completed(1), base), Duration::ZERO);
        // Idle 204: doubling from 60s, capped at 300s.
        assert_eq!(next_backoff(&Outcome::Idle, Duration::ZERO), Duration::from_secs(60));
        assert_eq!(next_backoff(&Outcome::Idle, base), Duration::from_secs(120));
        assert_eq!(next_backoff(&Outcome::Idle, Duration::from_secs(240)), Duration::from_secs(300));
        assert_eq!(next_backoff(&Outcome::Idle, Duration::from_secs(300)), Duration::from_secs(300));
        // Errors (our side / network): 2 min.
        assert_eq!(next_backoff(&Outcome::Error("x".into()), base), Duration::from_secs(120));
        // A reported failure burned an attempt server-side; modest pause.
        assert_eq!(next_backoff(&Outcome::Failed(1, WrError::NoTrail), base), Duration::from_secs(30));
        // Released (cancel/pause/gate): quick re-check — the gate logic decides the rest.
        assert_eq!(next_backoff(&Outcome::Released(1), base), Duration::from_secs(30));
    }
}
