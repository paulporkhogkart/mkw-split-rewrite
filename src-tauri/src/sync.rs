//! Decoupled client→server upload module (mirrors discord.rs).
//! Consumes engine `run_finalized` events, persists them in a rusqlite outbox,
//! and a background task uploads them to the server. The engine never sees the network.
use rusqlite::Connection;

/// Extract the `attempt_id` from a run_finalized JSON line.
fn attempt_id_of(line: &str) -> Option<String> {
    let v: serde_json::Value = serde_json::from_str(line).ok()?;
    v.get("attempt_id")?.as_str().map(|s| s.to_string())
}

/// Turn a stored run_finalized line into the POST body: drop `type`, set `cc`.
fn build_upload_body(line: &str, cc: i64) -> Option<String> {
    let mut v: serde_json::Value = serde_json::from_str(line).ok()?;
    let obj = v.as_object_mut()?;
    obj.remove("type");
    obj.insert("cc".into(), serde_json::json!(cc));
    serde_json::to_string(&v).ok()
}

fn ensure_outbox(conn: &Connection) {
    conn.execute(
        "CREATE TABLE IF NOT EXISTS outbox (
            attempt_id TEXT PRIMARY KEY,
            body       TEXT NOT NULL,
            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        )",
        [],
    ).expect("create outbox");
}

fn outbox_insert(conn: &Connection, attempt_id: &str, body: &str) {
    conn.execute(
        "INSERT OR REPLACE INTO outbox(attempt_id, body) VALUES (?1, ?2)",
        rusqlite::params![attempt_id, body],
    ).ok();
}

fn outbox_pending(conn: &Connection) -> Vec<(String, String)> {
    let mut stmt = conn.prepare("SELECT attempt_id, body FROM outbox ORDER BY created_at").unwrap();
    let rows = stmt.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?))).unwrap();
    rows.filter_map(|r| r.ok()).collect()
}

fn outbox_delete(conn: &Connection, attempt_id: &str) {
    conn.execute("DELETE FROM outbox WHERE attempt_id = ?1", rusqlite::params![attempt_id]).ok();
}

#[cfg(test)]
mod tests {
    use super::*;

    const LINE: &str = r#"{"type":"run_finalized","attempt_id":"a1","course":"Rainbow Road","status":"finished","total_time":"1:50.000"}"#;

    #[test]
    fn attempt_id_is_extracted() {
        assert_eq!(attempt_id_of(LINE).as_deref(), Some("a1"));
        assert_eq!(attempt_id_of("not json"), None);
    }

    #[test]
    fn upload_body_drops_type_and_sets_cc() {
        let body = build_upload_body(LINE, 150).unwrap();
        let v: serde_json::Value = serde_json::from_str(&body).unwrap();
        assert!(v.get("type").is_none());
        assert_eq!(v["cc"], 150);
        assert_eq!(v["attempt_id"], "a1");
        assert_eq!(v["course"], "Rainbow Road");
    }

    #[test]
    fn outbox_is_idempotent_by_attempt_id() {
        let conn = Connection::open_in_memory().unwrap();
        ensure_outbox(&conn);
        outbox_insert(&conn, "a1", LINE);
        outbox_insert(&conn, "a1", LINE);          // same id → replace, not duplicate
        assert_eq!(outbox_pending(&conn).len(), 1);
        outbox_delete(&conn, "a1");
        assert_eq!(outbox_pending(&conn).len(), 0);
    }
}
