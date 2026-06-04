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

/// True if the line is a run_finalized IPC event. Whitespace-agnostic: the engine
/// emits standard `json.dumps` output (a space after each colon), so parse and check
/// the `type` field rather than substring-match a specific formatting.
fn is_run_finalized(line: &str) -> bool {
    if !line.contains("run_finalized") {
        return false; // cheap reject for the overwhelming majority of lines
    }
    serde_json::from_str::<serde_json::Value>(line)
        .ok()
        .and_then(|v| v.get("type").and_then(|t| t.as_str()).map(|s| s == "run_finalized"))
        .unwrap_or(false)
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

use std::sync::Mutex;

#[derive(Default, Clone)]
struct Config { server_url: String, token: String, cc: i64 }

static CONFIG: Mutex<Config> = Mutex::new(Config { server_url: String::new(), token: String::new(), cc: 150 });
static OUTBOX: Mutex<Option<Connection>> = Mutex::new(None);

/// Called by lib.rs for every sidecar stdout line. Enqueues run_finalized events; ignores the rest.
pub fn on_line(line: &str) {
    if !is_run_finalized(line) {
        return;
    }
    let Some(id) = attempt_id_of(line) else { return };
    if let Ok(mut guard) = OUTBOX.lock() {
        if let Some(conn) = guard.as_ref() {
            outbox_insert(conn, &id, line);
        } else {
            log::warn!("[sync] outbox not initialised; dropping run_finalized");
        }
    }
}

#[tauri::command]
pub fn sync_set_config(server_url: String, token: String, cc: i64) {
    if let Ok(mut c) = CONFIG.lock() {
        *c = Config { server_url, token, cc };
    }
}

/// Open the outbox DB in the app data dir, then spawn the drain loop.
pub fn init(app: tauri::AppHandle) {
    use tauri::Manager;
    let dir = match app.path().app_data_dir() {
        Ok(d) => d,
        Err(e) => { log::error!("[sync] no app_data_dir: {e}"); return; }
    };
    let _ = std::fs::create_dir_all(&dir);
    let conn = match Connection::open(dir.join("sync_outbox.db")) {
        Ok(c) => c,
        Err(e) => { log::error!("[sync] open outbox failed: {e}"); return; }
    };
    ensure_outbox(&conn);
    *OUTBOX.lock().unwrap() = Some(conn);

    // A dedicated OS thread (not the tokio runtime) running a blocking loop:
    // rusqlite is sync and reqwest::blocking manages its own runtime, so this
    // avoids any dependency on Tauri's async runtime having a time driver.
    std::thread::spawn(move || {
        let client = reqwest::blocking::Client::new();
        loop {
            std::thread::sleep(std::time::Duration::from_secs(3));
            let cfg = CONFIG.lock().unwrap().clone();
            if cfg.server_url.is_empty() || cfg.token.is_empty() {
                continue;
            }
            // Snapshot pending rows WITHOUT holding the lock during the blocking POSTs.
            let pending: Vec<(String, String)> = {
                let guard = OUTBOX.lock().unwrap();
                match guard.as_ref() { Some(c) => outbox_pending(c), None => Vec::new() }
            };
            for (id, line) in pending {
                let Some(body) = build_upload_body(&line, cfg.cc) else {
                    // Unparseable row: drop it so it doesn't wedge the queue.
                    if let Some(c) = OUTBOX.lock().unwrap().as_ref() { outbox_delete(c, &id); }
                    continue;
                };
                let url = format!("{}/v1/runs", cfg.server_url.trim_end_matches('/'));
                match client.post(&url)
                    .bearer_auth(&cfg.token)
                    .header("content-type", "application/json")
                    .body(body)
                    .send()
                {
                    Ok(resp) if resp.status().is_success() => {
                        if let Some(c) = OUTBOX.lock().unwrap().as_ref() { outbox_delete(c, &id); }
                    }
                    Ok(resp) => log::debug!("[sync] {id}: server {}", resp.status()),
                    Err(e) => log::debug!("[sync] {id}: {e}"),
                }
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    // Realistic engine output: standard json.dumps spacing (a space after each colon).
    const LINE: &str = r#"{"type": "run_finalized", "attempt_id": "a1", "course": "Rainbow Road", "status": "finished", "total_time": "1:50.000"}"#;

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

    #[test]
    fn detects_run_finalized_regardless_of_spacing() {
        // Regression: the engine emits json.dumps output WITH a space after each colon.
        assert!(is_run_finalized(r#"{"type": "run_finalized", "attempt_id": "x"}"#));
        assert!(is_run_finalized(r#"{"type":"run_finalized","attempt_id":"x"}"#));
        assert!(!is_run_finalized(r#"{"type": "screen_change", "from": "RACING", "to": "RESET"}"#));
        assert!(!is_run_finalized("not json"));
    }
}
