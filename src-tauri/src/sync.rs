//! Decoupled client→server upload module (mirrors discord.rs).
//! Consumes engine `run_finalized` events, persists them in a rusqlite outbox,
//! and a background task uploads them to the server. The engine never sees the network.
use rusqlite::Connection;

/// Mirror of the server's `slugify` (pi/src/db/slug.ts): lowercase, drop apostrophes,
/// collapse any other non-alphanumeric run to a single underscore, trim underscores.
fn slugify(name: &str) -> String {
    let mut out = String::new();
    let mut prev_us = false;
    for ch in name.to_lowercase().chars() {
        if ch == '\'' || ch == '\u{2019}' { continue; }      // straight + curly apostrophe
        if ch.is_ascii_alphanumeric() {
            out.push(ch);
            prev_us = false;
        } else if !prev_us {
            out.push('_');
            prev_us = true;
        }
    }
    out.trim_matches('_').to_string()
}

/// Parse "M:SS.mmm" -> milliseconds. Returns None on any other shape.
fn parse_time_ms(t: &str) -> Option<i64> {
    let (m, rest) = t.trim().split_once(':')?;
    let (s, ms) = rest.split_once('.')?;
    if s.len() != 2 || ms.len() != 3 { return None; }
    let m: i64 = m.parse().ok()?;
    let s: i64 = s.parse().ok()?;
    let ms: i64 = ms.parse().ok()?;
    Some(m * 60_000 + s * 1_000 + ms)
}

/// Extract the `attempt_id` from a run_finalized JSON line.
fn attempt_id_of(line: &str) -> Option<String> {
    let v: serde_json::Value = serde_json::from_str(line).ok()?;
    v.get("attempt_id")?.as_str().map(|s| s.to_string())
}

/// Turn a stored run_finalized line into the POST body: drop the IPC `type` tag and
/// forward everything else as-is. `cc` is NOT injected — it isn't a client setting;
/// the engine will include it per run if MKW ever ships more than 150cc, and the
/// server defaults to 150 when it's absent.
fn build_upload_body(line: &str) -> Option<String> {
    let mut v: serde_json::Value = serde_json::from_str(line).ok()?;
    let obj = v.as_object_mut()?;
    obj.remove("type");
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

fn ensure_pb_cache(conn: &Connection) {
    conn.execute(
        "CREATE TABLE IF NOT EXISTS pb_cache (
            course_slug TEXT NOT NULL,
            cc          INTEGER NOT NULL,
            best_ms     INTEGER NOT NULL,
            PRIMARY KEY (course_slug, cc)
        )",
        [],
    ).expect("create pb_cache");
}

fn pb_cache_best(conn: &Connection, slug: &str, cc: i64) -> Option<i64> {
    conn.query_row(
        "SELECT best_ms FROM pb_cache WHERE course_slug=?1 AND cc=?2",
        rusqlite::params![slug, cc],
        |r| r.get::<_, i64>(0),
    ).ok()
}

fn pb_cache_put(conn: &Connection, slug: &str, cc: i64, ms: i64) {
    conn.execute(
        "INSERT INTO pb_cache(course_slug, cc, best_ms) VALUES (?1,?2,?3)
         ON CONFLICT(course_slug, cc) DO UPDATE SET best_ms=excluded.best_ms",
        rusqlite::params![slug, cc, ms],
    ).ok();
}

/// Parse a `/v1/me/pbs` JSON body into (course_slug, cc, best_ms) rows. Tolerant: skips
/// malformed entries.
fn parse_me_pbs(body: &str) -> Vec<(String, i64, i64)> {
    let v: serde_json::Value = match serde_json::from_str(body) { Ok(v) => v, Err(_) => return Vec::new() };
    let arr = match v.as_array() { Some(a) => a, None => return Vec::new() };
    arr.iter().filter_map(|e| {
        Some((
            e.get("course_slug")?.as_str()?.to_string(),
            e.get("cc")?.as_i64()?,
            e.get("total_time_ms")?.as_i64()?,
        ))
    }).collect()
}

/// Replace the cache contents with the server's authoritative bests.
fn seed_pb_cache(conn: &Connection, rows: &[(String, i64, i64)]) {
    conn.execute("DELETE FROM pb_cache", []).ok();
    for (slug, cc, ms) in rows { pb_cache_put(conn, slug, *cc, *ms); }
}

/// True if `ms` beats the cached best (or there is none). On true, lowers the cache
/// immediately so consecutive offline PBs are each detected.
fn is_new_pb(conn: &Connection, slug: &str, cc: i64, ms: i64) -> bool {
    match pb_cache_best(conn, slug, cc) {
        Some(best) if ms >= best => false,
        _ => { pb_cache_put(conn, slug, cc, ms); true }
    }
}

use std::sync::Mutex;

#[derive(Default, Clone)]
struct Config { server_url: String, token: String }

static CONFIG: Mutex<Config> = Mutex::new(Config { server_url: String::new(), token: String::new() });
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
pub fn sync_set_config(server_url: String, token: String) {
    if let Ok(mut c) = CONFIG.lock() {
        *c = Config { server_url, token };
    }
}

/// Probe the configured server and report a human-readable result.
///
/// Reads the SAME `CONFIG` the uploader's drain loop uses, so it also reveals
/// whether the Sync settings actually reached the uploader (boundary check):
/// an empty URL/token here means the frontend → Rust config push isn't landing.
///
/// Network checks are non-destructive:
///   1. `GET /health`  — reachability + correct base URL (no auth).
///   2. `POST /v1/runs` with an empty `{}` body — `requireToken` runs first, so an
///      invalid token yields 401; a valid token falls through to the payload check
///      and yields 400 ("bad payload") without ever writing a run.
#[tauri::command]
pub async fn sync_test_connection() -> Result<String, String> {
    let cfg = { CONFIG.lock().unwrap().clone() };
    if cfg.server_url.trim().is_empty() {
        return Err("The uploader has no server URL. If you've entered one above, the \
                    settings aren't reaching the uploader — close and reopen Settings, \
                    then try again.".into());
    }
    if cfg.token.trim().is_empty() {
        return Err("The uploader has no token. Paste your token above, then try again.".into());
    }
    let base = cfg.server_url.trim_end_matches('/').to_string();
    let client = reqwest::Client::new();

    // 1. Reachability (no auth).
    match client.get(format!("{base}/health"))
        .timeout(std::time::Duration::from_secs(8)).send().await
    {
        Err(e) => return Err(format!("Couldn't reach {base} — {e}")),
        Ok(r) if !r.status().is_success() =>
            return Err(format!("{base}/health returned HTTP {}.", r.status().as_u16())),
        Ok(_) => {}
    }

    // 2. Auth (non-destructive: empty body never writes a run).
    match client.post(format!("{base}/v1/runs"))
        .bearer_auth(&cfg.token)
        .header("content-type", "application/json")
        .body("{}")
        .timeout(std::time::Duration::from_secs(8))
        .send().await
    {
        Err(e) => Err(format!("Server reachable, but the runs endpoint errored — {e}")),
        Ok(r) => match r.status().as_u16() {
            400 => Ok(format!("Connected to {base}. Server is up and your token is valid.")),
            401 => Err("Reached the server, but it rejected your token (401). Check the token.".into()),
            other => Err(format!("Reached the server, but /v1/runs replied HTTP {other} (expected 400/401).")),
        },
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
    ensure_pb_cache(&conn);
    *OUTBOX.lock().unwrap() = Some(conn);

    // A dedicated OS thread (not the tokio runtime) running a blocking loop:
    // rusqlite is sync and reqwest::blocking manages its own runtime, so this
    // avoids any dependency on Tauri's async runtime having a time driver.
    std::thread::spawn(move || {
        let client = reqwest::blocking::Client::new();
        let mut seeded = false;
        loop {
            std::thread::sleep(std::time::Duration::from_secs(3));
            let cfg = CONFIG.lock().unwrap().clone();
            if cfg.server_url.is_empty() || cfg.token.is_empty() {
                continue;
            }
            if !seeded {
                let url = format!("{}/v1/me/pbs", cfg.server_url.trim_end_matches('/'));
                if let Ok(resp) = client.get(&url).bearer_auth(&cfg.token).send() {
                    if resp.status().is_success() {
                        if let Ok(body) = resp.text() {
                            let rows = parse_me_pbs(&body);
                            if let Some(c) = OUTBOX.lock().unwrap().as_ref() { seed_pb_cache(c, &rows); }
                            seeded = true;
                        }
                    }
                }
            }
            // Snapshot pending rows WITHOUT holding the lock during the blocking POSTs.
            let pending: Vec<(String, String)> = {
                let guard = OUTBOX.lock().unwrap();
                match guard.as_ref() { Some(c) => outbox_pending(c), None => Vec::new() }
            };
            for (id, line) in pending {
                let Some(body) = build_upload_body(&line) else {
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
                        seeded = false;
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
    fn upload_body_drops_type_and_forwards_fields() {
        let body = build_upload_body(LINE).unwrap();
        let v: serde_json::Value = serde_json::from_str(&body).unwrap();
        assert!(v.get("type").is_none());
        assert_eq!(v["attempt_id"], "a1");
        assert_eq!(v["course"], "Rainbow Road");
        assert_eq!(v["status"], "finished");
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

    #[test]
    fn slugify_matches_server_rule() {
        assert_eq!(slugify("Rainbow Road"), "rainbow_road");
        assert_eq!(slugify("Bowser's Castle"), "bowsers_castle");   // apostrophe dropped
        assert_eq!(slugify("  Mario  Circuit "), "mario_circuit");  // collapse + trim
    }

    #[test]
    fn parse_time_ms_handles_mmss() {
        assert_eq!(parse_time_ms("1:50.123"), Some(110123));
        assert_eq!(parse_time_ms("0:36.400"), Some(36400));
        assert_eq!(parse_time_ms("nope"), None);
    }

    #[test]
    fn parse_me_pbs_reads_rows() {
        let body = r#"[{"course_slug":"rainbow_road","cc":150,"total_time_ms":110000},
                       {"course_slug":"mario_circuit","cc":150,"total_time_ms":95000}]"#;
        let rows = parse_me_pbs(body);
        assert_eq!(rows.len(), 2);
        assert_eq!(rows[0], ("rainbow_road".to_string(), 150, 110000));
    }

    #[test]
    fn is_new_pb_optimistically_lowers_the_cache() {
        let conn = Connection::open_in_memory().unwrap();
        ensure_pb_cache(&conn);
        // No entry → it's a PB, and the cache now holds it.
        assert!(is_new_pb(&conn, "rainbow_road", 150, 110000));
        assert_eq!(pb_cache_best(&conn, "rainbow_road", 150), Some(110000));
        // Slower → not a PB, cache unchanged.
        assert!(!is_new_pb(&conn, "rainbow_road", 150, 111000));
        assert_eq!(pb_cache_best(&conn, "rainbow_road", 150), Some(110000));
        // Faster → PB, cache lowers (handles back-to-back offline PBs).
        assert!(is_new_pb(&conn, "rainbow_road", 150, 108500));
        assert_eq!(pb_cache_best(&conn, "rainbow_road", 150), Some(108500));
    }
}
