//! Per-install identity + local scratch state for the WR service.
//!
//! The worker id is the LEASE identity on the Pi. The player token authenticates the
//! person; this identifies the machine, so two of Paul's PCs on one token can hold
//! separate leases (spec §6, X-Worker-Id).

use rusqlite::Connection;
use std::path::Path;

/// Open (creating if needed) the WR service's local scratch DB.
/// Separate file from sync_outbox.db: different lifecycle, and a corrupt WR scratch
/// file must never take the run uploader down with it.
pub fn open(dir: &Path) -> Result<Connection, String> {
    std::fs::create_dir_all(dir).map_err(|e| format!("create {dir:?}: {e}"))?;
    let conn = Connection::open(dir.join("wr_service.db")).map_err(|e| e.to_string())?;
    conn.execute(
        "CREATE TABLE IF NOT EXISTS wr_local (key TEXT PRIMARY KEY, value TEXT)",
        [],
    ).map_err(|e| e.to_string())?;
    Ok(conn)
}

fn get(conn: &Connection, key: &str) -> Option<String> {
    conn.query_row("SELECT value FROM wr_local WHERE key=?1", [key], |r| r.get(0)).ok()
}

fn put(conn: &Connection, key: &str, value: Option<&str>) {
    match value {
        Some(v) => { let _ = conn.execute(
            "INSERT INTO wr_local(key,value) VALUES(?1,?2)
             ON CONFLICT(key) DO UPDATE SET value=excluded.value", [key, v]); }
        None => { let _ = conn.execute("DELETE FROM wr_local WHERE key=?1", [key]); }
    }
}

/// The wr_id currently being worked, if any. Present after a crash => its video file
/// is an orphan to sweep.
pub fn inflight(conn: &Connection) -> Option<i64> {
    get(conn, "inflight_wr_id").and_then(|s| s.parse().ok())
}

pub fn set_inflight(conn: &Connection, wr_id: Option<i64>) {
    put(conn, "inflight_wr_id", wr_id.map(|v| v.to_string()).as_deref());
}

/// Background-mode settings (spec 2026-07-17 §3). Exact key strings — the frontend sends
/// these verbatim through wr_set_setting.
pub const SETTING_CLOSE_TO_TRAY: &str = "close_to_tray";
pub const SETTING_START_AT_LOGIN: &str = "start_at_login";
pub const SETTING_RUN_WR_SERVICE: &str = "run_wr_service";
pub const SETTING_KEEP_TRACKING_IN_TRAY: &str = "keep_tracking_in_tray";

/// Read a boolean setting; unset = false (every setting defaults to today's behaviour).
pub fn get_flag(conn: &Connection, key: &str) -> bool {
    get(conn, key).as_deref() == Some("1")
}

pub fn set_flag(conn: &Connection, key: &str, value: bool) {
    put(conn, key, Some(if value { "1" } else { "0" }));
}

/// Sync credentials, persisted so a --tray-start boot (no webview, which is what feeds
/// sync's in-RAM CONFIG) can still run the WR service. Same exposure as the webview's
/// localStorage copy: plaintext in the same user profile.
pub const SETTING_SYNC_SERVER_URL: &str = "sync_server_url";
pub const SETTING_SYNC_TOKEN: &str = "sync_token";

pub fn get_str(conn: &Connection, key: &str) -> Option<String> {
    get(conn, key)
}

pub fn set_str(conn: &Connection, key: &str, value: &str) {
    put(conn, key, Some(value));
}

/// Stable per-install lease identity. Generated once into `worker-id` beside the DB and
/// reused forever; a plain file (not the DB) so a scratch-DB reset can't silently change
/// our identity and orphan a live lease.
pub fn worker_id(dir: &Path) -> String {
    if let Err(e) = std::fs::create_dir_all(dir) {
        log::error!("[wr] cannot create {dir:?}: {e} — worker id will not persist, \
                     so each restart will orphan its previous lease until it expires");
    }
    let path = dir.join("worker-id");
    if let Ok(s) = std::fs::read_to_string(&path) {
        let s = s.trim().to_string();
        if is_valid_worker_id(&s) {
            return s;
        }
        if !s.is_empty() {
            // Not cosmetic: a corrupt byte here makes every request's X-Worker-Id an
            // INVALID HTTP header value, so every request fails forever with no self-heal.
            // Regenerate rather than trust a stored value that fails our own rules.
            log::warn!("[wr] stored worker id at {path:?} failed validation ({s:?}); \
                        regenerating — any live lease under the old id simply expires \
                        server-side rather than being actively orphaned");
        }
    }
    let id = generate_worker_id();
    // Atomic write: temp file + rename (mirrors ytdlp::fetch's pattern below in this
    // module's sibling file). This id IS our lease identity, so a crash mid-write must
    // never leave a torn value on disk to be misread as valid-but-wrong on the next boot —
    // the temp file simply never gets renamed over the real one in that case.
    let tmp = dir.join("worker-id.tmp");
    match std::fs::write(&tmp, &id).and_then(|_| std::fs::rename(&tmp, &path)) {
        Ok(()) => {}
        Err(e) => log::error!("[wr] cannot persist worker id to {path:?}: {e} — each restart \
                              will orphan its previous lease until it expires"),
    }
    id
}

/// Legal HTTP header value AND the server's own limit: ≤64 chars, alphanumeric/dash only.
fn is_valid_worker_id(s: &str) -> bool {
    !s.is_empty() && s.len() <= 64 && s.chars().all(|c| c.is_ascii_alphanumeric() || c == '-')
}

/// 32 hex chars seeded from OS entropy, with no new dependency.
///
/// `RandomState` is std's HashMap hasher seeder, and std seeds it from the OS RNG (that
/// is the whole point — it exists to make HashDoS attacks infeasible). Note this is ONE
/// OS-seeded draw per thread, not two: std caches the seed thread-locally and merely
/// increments it per `new()` call, so the second hasher is derived from the same seed
/// rather than independently sourced. The output is 128 bits wide, backed by that single
/// OS draw — it is a real entropy source, not a clock-derived PRNG, and it differs
/// reliably across installs and processes.
///
/// That is adequate here because this is an install id, not a secret: it is never used
/// to authenticate (the player token does that). A collision would only mean two machines
/// contend for one lease, which is recoverable, not data loss.
fn generate_worker_id() -> String {
    use std::collections::hash_map::RandomState;
    use std::hash::{BuildHasher, Hasher};
    let a = RandomState::new().build_hasher().finish();
    let b = RandomState::new().build_hasher().finish();
    format!("{a:016x}{b:016x}")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmpdir(tag: &str) -> std::path::PathBuf {
        let d = std::env::temp_dir().join(format!("wr_state_test_{tag}"));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    #[test]
    fn worker_id_is_stable_across_calls() {
        let d = tmpdir("stable");
        let a = worker_id(&d);
        let b = worker_id(&d);
        assert_eq!(a, b, "worker id must persist — it is the lease identity");
        assert!(!a.is_empty());
    }

    #[test]
    fn worker_id_differs_per_install() {
        let a = worker_id(&tmpdir("inst_a"));
        let b = worker_id(&tmpdir("inst_b"));
        assert_ne!(a, b, "two installs must not share a lease identity");
    }

    #[test]
    fn worker_id_is_url_and_header_safe() {
        let id = worker_id(&tmpdir("safe"));
        assert!(id.len() <= 64, "server rejects >64 chars");
        assert!(id.chars().all(|c| c.is_ascii_alphanumeric() || c == '-'),
                "must be a legal HTTP header value: {id}");
    }

    #[test]
    fn worker_id_survives_an_unwritable_dir() {
        // A dir path *under an existing file* cannot be created, on Windows or unix.
        let f = std::env::temp_dir().join("wr_state_test_unwritable_file");
        let _ = std::fs::remove_dir_all(&f);
        std::fs::write(&f, b"not a directory").unwrap();
        let bad = f.join("sub");

        // Precondition: if this ever starts succeeding, the test below would silently
        // stop exercising the write-failure path and quietly pass forever.
        assert!(std::fs::create_dir_all(&bad).is_err(),
                "precondition: {bad:?} must be uncreatable for this test to mean anything");

        // Must degrade, not panic: a non-persistable id still lets the service run, and
        // the orphaned lease expires server-side.
        let id = worker_id(&bad);
        assert!(!id.is_empty(), "must still hand back a usable id");
        assert!(id.len() <= 64 && id.chars().all(|c| c.is_ascii_alphanumeric() || c == '-'),
                "degraded id must still be well-formed: {id}");

        let _ = std::fs::remove_file(&f);
    }

    #[test]
    fn worker_id_regenerates_a_corrupt_stored_value() {
        let d = tmpdir("corrupt");
        // A byte that would make X-Worker-Id an invalid HTTP header value if trusted
        // as-is — the exact failure mode a read-back-only-checks-non-empty bug misses.
        std::fs::write(d.join("worker-id"), "bad\nvalue\0here").unwrap();
        let id = worker_id(&d);
        assert!(is_valid_worker_id(&id), "must regenerate a well-formed id, got {id:?}");
        assert_ne!(id, "bad\nvalue\0here");
        // Persisted: a second call must return the SAME regenerated id, not regenerate
        // again every boot.
        assert_eq!(worker_id(&d), id, "the regenerated id must itself persist");
    }

    #[test]
    fn worker_id_regenerates_an_oversized_stored_value() {
        let d = tmpdir("oversized");
        let too_long = "a".repeat(100);
        std::fs::write(d.join("worker-id"), &too_long).unwrap();
        let id = worker_id(&d);
        assert!(id.len() <= 64, "server rejects >64 chars, got len {}", id.len());
        assert_ne!(id, too_long);
    }

    #[test]
    fn worker_id_accepts_a_previously_persisted_valid_value_unchanged() {
        // Guards against over-eager validation: a genuinely well-formed stored id must be
        // returned VERBATIM, not silently replaced.
        let d = tmpdir("valid_roundtrip");
        let first = worker_id(&d);
        assert!(is_valid_worker_id(&first));
        assert_eq!(worker_id(&d), first);
    }

    #[test]
    fn inflight_roundtrips_and_clears() {
        let d = tmpdir("inflight");
        let c = open(&d).unwrap();
        assert_eq!(inflight(&c), None);
        set_inflight(&c, Some(42));
        assert_eq!(inflight(&c), Some(42));
        set_inflight(&c, None);
        assert_eq!(inflight(&c), None, "a finished job must leave no in-flight record");
    }

    #[test]
    fn inflight_survives_reopen() {
        let d = tmpdir("reopen");
        { let c = open(&d).unwrap(); set_inflight(&c, Some(7)); }
        let c2 = open(&d).unwrap();
        assert_eq!(inflight(&c2), Some(7), "a crash must leave the orphan discoverable");
    }

    #[test]
    fn a_read_waits_out_a_concurrent_writers_lock_instead_of_degrading_to_false() {
        // wr_service.db is hit from three threads (runner: inflight writes; main: tray
        // refresh + CloseRequested/ExitRequested flag reads; commands: setting writes).
        // If a read landing on another connection's commit window got SQLITE_BUSY,
        // get_flag would swallow it to FALSE and close-to-tray would silently quit
        // instead of traying. What prevents that is rusqlite's DEFAULT 5s busy
        // handler (rusqlite 0.32.1 inner_connection.rs:119 — sqlite3_busy_timeout
        // 5000 on every open), which open() relies on WITHOUT setting anything.
        // This test pins that reliance: if a rusqlite upgrade ever drops or shrinks
        // the default, the lifecycle paths above start misreading under contention
        // and this is the only thing that will say so (review 2026-07-18).
        let d = tmpdir("busy_wait");
        let c1 = open(&d).unwrap();
        set_flag(&c1, SETTING_CLOSE_TO_TRAY, true);
        let c2 = open(&d).unwrap();
        c1.execute_batch("BEGIN EXCLUSIVE").unwrap();
        let writer = std::thread::spawn(move || {
            std::thread::sleep(std::time::Duration::from_millis(300));
            c1.execute_batch("COMMIT").unwrap();
        });
        let started = std::time::Instant::now();
        assert!(get_flag(&c2, SETTING_CLOSE_TO_TRAY),
            "a stored '1' must never read as false just because a writer was mid-commit");
        assert!(started.elapsed() >= std::time::Duration::from_millis(150),
            "the truthful read implies the reader actually waited for the writer, \
             not that the lock was already free (took {:?})", started.elapsed());
        writer.join().unwrap();
    }

    #[test]
    fn flags_default_false_and_roundtrip() {
        let d = tmpdir("flags");
        let c = open(&d).unwrap();
        assert!(!get_flag(&c, SETTING_CLOSE_TO_TRAY), "unset flag must read false");
        set_flag(&c, SETTING_CLOSE_TO_TRAY, true);
        assert!(get_flag(&c, SETTING_CLOSE_TO_TRAY));
        set_flag(&c, SETTING_CLOSE_TO_TRAY, false);
        assert!(!get_flag(&c, SETTING_CLOSE_TO_TRAY));
    }

    #[test]
    fn flags_are_independent_keys() {
        let d = tmpdir("flags_indep");
        let c = open(&d).unwrap();
        set_flag(&c, SETTING_RUN_WR_SERVICE, true);
        assert!(get_flag(&c, SETTING_RUN_WR_SERVICE));
        assert!(!get_flag(&c, SETTING_KEEP_TRACKING_IN_TRAY),
            "setting one flag must not bleed into another");
    }

    #[test]
    fn flags_survive_reopen() {
        let d = tmpdir("flags_reopen");
        { let c = open(&d).unwrap(); set_flag(&c, SETTING_START_AT_LOGIN, true); }
        let c2 = open(&d).unwrap();
        assert!(get_flag(&c2, SETTING_START_AT_LOGIN), "settings must persist across restarts");
    }

    #[test]
    fn str_settings_roundtrip_and_survive_reopen() {
        let d = tmpdir("str_settings");
        { let c = open(&d).unwrap();
          set_str(&c, SETTING_SYNC_SERVER_URL, "https://pi.example");
          set_str(&c, SETTING_SYNC_TOKEN, "tok123"); }
        let c2 = open(&d).unwrap();
        assert_eq!(get_str(&c2, SETTING_SYNC_SERVER_URL).as_deref(), Some("https://pi.example"));
        assert_eq!(get_str(&c2, SETTING_SYNC_TOKEN).as_deref(), Some("tok123"));
        assert_eq!(get_str(&c2, "never_set"), None);
    }
}
