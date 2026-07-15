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

/// Stable per-install lease identity. Generated once into `worker-id` beside the DB and
/// reused forever; a plain file (not the DB) so a scratch-DB reset can't silently change
/// our identity and orphan a live lease.
pub fn worker_id(dir: &Path) -> String {
    let _ = std::fs::create_dir_all(dir);
    let path = dir.join("worker-id");
    if let Ok(s) = std::fs::read_to_string(&path) {
        let s = s.trim().to_string();
        if !s.is_empty() { return s; }
    }
    let id = generate_worker_id();
    let _ = std::fs::write(&path, &id);
    id
}

/// 32 hex chars of OS entropy, with no new dependency.
///
/// `RandomState` is std's HashMap hasher seeder, and std seeds it from the OS RNG
/// (that is the whole point — it exists to make HashDoS attacks infeasible). Two
/// independently-constructed hashers therefore give 128 bits of OS-derived entropy.
/// This is a real entropy source, not a clock-derived PRNG.
///
/// Not crypto: nothing here is a secret. A collision would only mean two machines
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
}
