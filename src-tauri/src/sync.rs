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
            status     TEXT NOT NULL DEFAULT 'ready',
            is_pb      INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        )",
        [],
    ).expect("create outbox");
    // Migrate pre-existing tables. Errors if the column already exists — ignored.
    conn.execute("ALTER TABLE outbox ADD COLUMN status TEXT NOT NULL DEFAULT 'ready'", []).ok();
    conn.execute("ALTER TABLE outbox ADD COLUMN is_pb INTEGER NOT NULL DEFAULT 0", []).ok();
}

fn outbox_insert(conn: &Connection, attempt_id: &str, body: &str, status: &str, is_pb: bool) {
    conn.execute(
        "INSERT OR REPLACE INTO outbox(attempt_id, body, status, is_pb) VALUES (?1, ?2, ?3, ?4)",
        rusqlite::params![attempt_id, body, status, is_pb as i64],
    ).ok();
}

fn outbox_pending(conn: &Connection) -> Vec<(String, String)> {
    let mut stmt = conn.prepare(
        "SELECT attempt_id, body FROM outbox WHERE status='ready' ORDER BY created_at"
    ).unwrap();
    let rows = stmt.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?))).unwrap();
    rows.filter_map(|r| r.ok()).collect()
}

fn outbox_list_pending(conn: &Connection) -> Vec<(String, String, bool)> {
    let mut stmt = conn.prepare(
        "SELECT attempt_id, body, is_pb FROM outbox WHERE status='pending_review' ORDER BY created_at"
    ).unwrap();
    let rows = stmt.query_map([], |r| Ok((
        r.get::<_, String>(0)?, r.get::<_, String>(1)?, r.get::<_, i64>(2)? != 0,
    ))).unwrap();
    rows.filter_map(|r| r.ok()).collect()
}

fn outbox_get_body(conn: &Connection, attempt_id: &str) -> Option<String> {
    conn.query_row(
        "SELECT body FROM outbox WHERE attempt_id=?1",
        rusqlite::params![attempt_id],
        |r| r.get::<_, String>(0),
    ).ok()
}

fn outbox_update_ready(conn: &Connection, attempt_id: &str, body: &str) {
    conn.execute(
        "UPDATE outbox SET body=?2, status='ready' WHERE attempt_id=?1",
        rusqlite::params![attempt_id, body],
    ).ok();
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

/// The gating matrix. Returns the ids of missing required fields; empty = complete
/// enough to upload. `0`/negative are valid; only null/empty counts as missing.
fn missing_fields(v: &serde_json::Value, is_pb: bool) -> Vec<String> {
    let mut missing = Vec::new();
    let str_present = |key: &str| -> bool {
        v.get(key).and_then(|x| x.as_str()).map(|s| !s.trim().is_empty()).unwrap_or(false)
    };
    for key in ["course", "character", "kart"] {
        if !str_present(key) { missing.push(key.to_string()); }
    }
    let status = v.get("status").and_then(|x| x.as_str()).unwrap_or("");
    if status == "finished" {
        let total_ok = v.get("total_time").and_then(|x| x.as_str())
            .and_then(parse_time_ms).is_some();
        if !total_ok { missing.push("total_time".to_string()); }
        if is_pb {
            if let Some(n) = v.get("total_laps").and_then(|x| x.as_i64()) {
                let laps = v.get("laps").and_then(|x| x.as_array());
                for lap_no in 1..=n {
                    let entry = laps.and_then(|arr| arr.iter().find(|e|
                        e.get("lap").and_then(|x| x.as_i64()) == Some(lap_no)));
                    let time_ok = entry.and_then(|e| e.get("time_str")).and_then(|x| x.as_str())
                        .map(|s| !s.is_empty()).unwrap_or(false);
                    let coins_ok = entry.and_then(|e| e.get("coins")).map(|x| !x.is_null()).unwrap_or(false);
                    let shrooms_ok = entry.and_then(|e| e.get("shrooms")).map(|x| !x.is_null()).unwrap_or(false);
                    if !time_ok { missing.push(format!("lap_{lap_no}_time")); }
                    if !coins_ok { missing.push(format!("lap_{lap_no}_coins")); }
                    if !shrooms_ok { missing.push(format!("lap_{lap_no}_shrooms")); }
                }
            }
        }
    }
    missing
}

/// True if this is a finished run whose time beats the cached best (lowers the cache).
fn is_finished_new_pb(conn: &Connection, v: &serde_json::Value) -> bool {
    if v.get("status").and_then(|x| x.as_str()) != Some("finished") { return false; }
    let Some(course) = v.get("course").and_then(|x| x.as_str()) else { return false; };
    let Some(ms) = v.get("total_time").and_then(|x| x.as_str()).and_then(parse_time_ms) else { return false; };
    is_new_pb(conn, &slugify(course), 150, ms)
}

/// Route one run_finalized line: store it with the right status and return the event to
/// emit. Complete → `ready` (+ `pb_achieved` if a PB); incomplete → `pending_review`
/// (+ `run_needs_review`).
fn route_line(conn: &Connection, line: &str) -> Option<String> {
    let id = attempt_id_of(line)?;
    let v: serde_json::Value = serde_json::from_str(line).ok()?;
    let is_pb = is_finished_new_pb(conn, &v);
    let missing = missing_fields(&v, is_pb);
    if missing.is_empty() {
        outbox_insert(conn, &id, line, "ready", is_pb);
        if is_pb {
            let course = v.get("course").and_then(|x| x.as_str()).unwrap_or("");
            let time = v.get("total_time").and_then(|x| x.as_str()).unwrap_or("");
            return Some(serde_json::json!({"type":"pb_achieved","course":course,"time":time}).to_string());
        }
        None
    } else {
        outbox_insert(conn, &id, line, "pending_review", is_pb);
        let mut run = v.clone();
        if let Some(o) = run.as_object_mut() { o.remove("type"); }
        Some(serde_json::json!({
            "type": "run_needs_review", "attempt_id": id, "is_pb": is_pb,
            "missing": missing, "run": run,
        }).to_string())
    }
}

// ── Per-course read cache (Phase 2b) ─────────────────────────────────────────
// Caches the combined {pb_splits, trails, friends_pbs} payload per course slug so
// the monitor's reads survive a flaky network (last-good served offline).
fn ensure_course_cache(conn: &Connection) {
    conn.execute(
        "CREATE TABLE IF NOT EXISTS course_cache (
            course_slug TEXT PRIMARY KEY,
            payload     TEXT NOT NULL,
            fetched_at  INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        )",
        [],
    ).expect("create course_cache");
}

fn course_cache_put(conn: &Connection, slug: &str, payload: &str) {
    conn.execute(
        "INSERT INTO course_cache(course_slug, payload) VALUES (?1, ?2)
         ON CONFLICT(course_slug) DO UPDATE SET payload=excluded.payload, fetched_at=strftime('%s','now')",
        rusqlite::params![slug, payload],
    ).ok();
}

fn course_cache_get(conn: &Connection, slug: &str) -> Option<String> {
    conn.query_row(
        "SELECT payload FROM course_cache WHERE course_slug=?1",
        rusqlite::params![slug],
        |r| r.get::<_, String>(0),
    ).ok()
}

fn course_cache_clear(conn: &Connection, slug: &str) {
    conn.execute("DELETE FROM course_cache WHERE course_slug=?1", rusqlite::params![slug]).ok();
}

/// Slugified `course` of a stored run body, for invalidating its cache after upload.
fn course_slug_of(line: &str) -> Option<String> {
    let v: serde_json::Value = serde_json::from_str(line).ok()?;
    let s = slugify(v.get("course")?.as_str()?);
    if s.is_empty() { None } else { Some(s) }
}

use std::sync::Mutex;

#[derive(Default, Clone)]
struct Config { server_url: String, token: String }

static CONFIG: Mutex<Config> = Mutex::new(Config { server_url: String::new(), token: String::new() });
static OUTBOX: Mutex<Option<Connection>> = Mutex::new(None);
/// Latest detected course (from selection_update), used as the course for the run_started ping.
static LAST_COURSE: Mutex<String> = Mutex::new(String::new());

/// Course display-name from a selection_update line, or None (absent/blank/other type).
fn course_from_selection(line: &str) -> Option<String> {
    if !line.contains("selection_update") { return None; }
    let v: serde_json::Value = serde_json::from_str(line).ok()?;
    if v.get("type")?.as_str()? != "selection_update" { return None; }
    let c = v.get("course")?.as_str()?;
    if c.is_empty() { None } else { Some(c.to_string()) }
}

/// True when a screen_change line marks a FRESH entry into RACING (not a pause-resume).
fn is_racing_entry(line: &str) -> bool {
    if !line.contains("screen_change") { return false; }
    let v: serde_json::Value = match serde_json::from_str(line) { Ok(v) => v, Err(_) => return false };
    if v.get("type").and_then(|t| t.as_str()) != Some("screen_change") { return false; }
    let to = v.get("to").and_then(|t| t.as_str()).unwrap_or("");
    let from = v.get("from").and_then(|t| t.as_str()).unwrap_or("");
    to == "RACING" && from != "RACING" && from != "RACE_MENU" && from != "HOME"
}

/// Fire-and-forget POST /v1/runs/start so the server emits a run_started event (ephemeral,
/// no DB write). Runs on its own thread (blocking reqwest) so it never blocks the stdout loop.
fn fire_run_started(course: String) {
    std::thread::spawn(move || {
        let cfg = CONFIG.lock().unwrap().clone();
        if cfg.server_url.trim().is_empty() || cfg.token.trim().is_empty() { return; }
        let url = format!("{}/v1/runs/start", cfg.server_url.trim_end_matches('/'));
        let body = serde_json::json!({ "course": course, "cc": 150 }).to_string();
        let _ = reqwest::blocking::Client::new()
            .post(&url).bearer_auth(&cfg.token)
            .header("content-type", "application/json").body(body)
            .timeout(std::time::Duration::from_secs(8)).send();
    });
}

/// Called by lib.rs for every sidecar stdout line. Tracks the latest course + pings run_started
/// on a fresh RACING entry, then gates run_finalized events (route_line), returning any event.
pub fn on_line(line: &str) -> Option<String> {
    if let Some(c) = course_from_selection(line) {
        if let Ok(mut lc) = LAST_COURSE.lock() { *lc = c; }
    } else if is_racing_entry(line) {
        let course = LAST_COURSE.lock().map(|c| c.clone()).unwrap_or_default();
        if !course.is_empty() { fire_run_started(course); }
    }
    if !is_run_finalized(line) { return None; }
    let guard = OUTBOX.lock().ok()?;
    let conn = guard.as_ref()?;
    route_line(conn, line)
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

/// Merge `filled` (a JSON object of corrected fields) into the stored body and flip the
/// row to `ready`. Pure (takes the conn) so it's unit-testable.
fn resolve_in_outbox(conn: &Connection, attempt_id: &str, filled: &serde_json::Value) {
    let Some(body) = outbox_get_body(conn, attempt_id) else { return };
    let Ok(mut v) = serde_json::from_str::<serde_json::Value>(&body) else { return };
    if let (Some(obj), Some(f)) = (v.as_object_mut(), filled.as_object()) {
        for (k, val) in f { obj.insert(k.clone(), val.clone()); }
    }
    if let Ok(merged) = serde_json::to_string(&v) {
        outbox_update_ready(conn, attempt_id, &merged);
    }
}

#[tauri::command]
pub fn sync_resolve_pending(attempt_id: String, filled: serde_json::Value) {
    if let Ok(guard) = OUTBOX.lock() {
        if let Some(conn) = guard.as_ref() { resolve_in_outbox(conn, &attempt_id, &filled); }
    }
}

#[tauri::command]
pub fn sync_discard_pending(attempt_id: String) {
    if let Ok(guard) = OUTBOX.lock() {
        if let Some(conn) = guard.as_ref() { outbox_delete(conn, &attempt_id); }
    }
}

/// JSON array of `{attempt_id, run, is_pb}` for every held (pending_review) run, for the UI to
/// resurface on launch. `type` is stripped from each run.
#[tauri::command]
pub fn sync_list_pending() -> String {
    if let Ok(guard) = OUTBOX.lock() {
        if let Some(conn) = guard.as_ref() {
            let arr: Vec<serde_json::Value> = outbox_list_pending(conn).iter().filter_map(|(id, body, is_pb)| {
                let mut v: serde_json::Value = serde_json::from_str(body).ok()?;
                if let Some(o) = v.as_object_mut() { o.remove("type"); }
                Some(serde_json::json!({ "attempt_id": id, "run": v, "is_pb": is_pb }))
            }).collect();
            return serde_json::to_string(&arr).unwrap_or_else(|_| "[]".into());
        }
    }
    "[]".into()
}

const EMPTY_COURSE_READS: &str = r#"{"pb_splits":{"total_ms":null,"splits":{}},"trails":[],"friends_pbs":[]}"#;

/// Per-player trail selection passed from the frontend's Trails settings.
#[derive(serde::Deserialize)]
pub struct PlayerTrailCfg { pub player_id: i64, pub mode: String, pub n: i64 }

/// Tag each run in a /v1/players/:id/trails response with its player_id for the combined payload.
fn tag_runs_with_player(player_id: i64, runs: &serde_json::Value) -> Vec<serde_json::Value> {
    runs.as_array().map(|arr| arr.iter().map(|run| {
        let mut r = run.clone();
        if let Some(o) = r.as_object_mut() { o.insert("player_id".into(), serde_json::json!(player_id)); }
        r
    }).collect()).unwrap_or_default()
}

/// Fetch the caller's PB splits + each configured player's trails + friends-PBs and combine
/// into one payload. Err on any non-2xx / network error. Parses via text()+serde_json so no
/// reqwest "json" feature is required.
async fn fetch_course_reads(cfg: &Config, course: &str, players: &[PlayerTrailCfg]) -> Result<String, String> {
    let base = cfg.server_url.trim_end_matches('/');
    let client = reqwest::Client::new();
    let q = [("course", course), ("cc", "150")];
    async fn get_json(rb: reqwest::RequestBuilder, what: &str) -> Result<serde_json::Value, String> {
        let resp = rb.timeout(std::time::Duration::from_secs(8)).send().await.map_err(|e| e.to_string())?;
        if !resp.status().is_success() { return Err(format!("{what} {}", resp.status())); }
        let txt = resp.text().await.map_err(|e| e.to_string())?;
        serde_json::from_str(&txt).map_err(|e| e.to_string())
    }
    let pb = get_json(client.get(format!("{base}/v1/me/pb-splits")).query(&q).bearer_auth(&cfg.token), "pb-splits").await?;
    let fp = get_json(client.get(format!("{base}/v1/friends-pbs")).query(&q), "friends-pbs").await?;
    let mut trails: Vec<serde_json::Value> = Vec::new();
    for p in players {
        if p.mode == "none" { continue; }
        let n = p.n.to_string();
        let runs = get_json(
            client.get(format!("{base}/v1/players/{}/trails", p.player_id))
                .query(&[("course", course), ("cc", "150"), ("mode", p.mode.as_str()), ("n", n.as_str())]),
            "player-trails",
        ).await?;
        trails.extend(tag_runs_with_player(p.player_id, &runs));
    }
    Ok(serde_json::json!({ "pb_splits": pb, "trails": trails, "friends_pbs": fp }).to_string())
}

/// Frontend reads for a course (PB splits + per-player trails + friends PBs), via the server
/// with the token, cached per course. `config` is the per-player trail selection (players with
/// mode != none). Returns the last cache when offline / unconfigured. JSON string.
#[tauri::command]
pub async fn sync_course_reads(course: String, config: Option<Vec<PlayerTrailCfg>>) -> String {
    let slug = slugify(&course);
    let players = config.unwrap_or_default();
    let cfg = { CONFIG.lock().unwrap().clone() };
    let cached = || -> String {
        OUTBOX.lock().ok()
            .and_then(|g| g.as_ref().and_then(|c| course_cache_get(c, &slug)))
            .unwrap_or_else(|| EMPTY_COURSE_READS.to_string())
    };
    if cfg.server_url.trim().is_empty() || cfg.token.trim().is_empty() {
        return cached();
    }
    match fetch_course_reads(&cfg, &course, &players).await {
        Ok(payload) => {
            if let Ok(g) = OUTBOX.lock() {
                if let Some(c) = g.as_ref() { course_cache_put(c, &slug, &payload); }
            }
            payload
        }
        Err(e) => { log::debug!("[sync] course_reads {slug}: {e}"); cached() }
    }
}

/// Fetch the season roster for the Trails settings list. Returns the server JSON array
/// (each `{player_id, display_name, is_me}`; is_me set when a token is configured), or
/// `"[]"` when unconfigured / unreachable (the frontend caches the last good roster).
#[tauri::command]
pub async fn sync_roster() -> String {
    let cfg = { CONFIG.lock().unwrap().clone() };
    if cfg.server_url.trim().is_empty() { return "[]".into(); }
    let base = cfg.server_url.trim_end_matches('/');
    let mut rb = reqwest::Client::new()
        .get(format!("{base}/v1/roster"))
        .timeout(std::time::Duration::from_secs(8));
    if !cfg.token.trim().is_empty() { rb = rb.bearer_auth(&cfg.token); }
    match rb.send().await {
        Ok(r) if r.status().is_success() => r.text().await.unwrap_or_else(|_| "[]".into()),
        _ => "[]".into(),
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
    ensure_course_cache(&conn);
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
                        if let Some(c) = OUTBOX.lock().unwrap().as_ref() {
                            outbox_delete(c, &id);
                            // Invalidate the course's read cache so the new PB/trail is re-fetched.
                            if let Some(slug) = course_slug_of(&line) { course_cache_clear(c, &slug); }
                        }
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
        outbox_insert(&conn, "a1", LINE, "ready", false);
        outbox_insert(&conn, "a1", LINE, "ready", false);  // same id → replace, not duplicate
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

    #[test]
    fn route_line_complete_pb_goes_ready_with_pb_achieved() {
        let conn = Connection::open_in_memory().unwrap();
        ensure_outbox(&conn); ensure_pb_cache(&conn);
        let line = r#"{"type":"run_finalized","attempt_id":"a1","course":"Rainbow Road","character":"Mario","kart":"K","status":"finished","total_time":"1:50.000","total_laps":1,"laps":[{"lap":1,"time_str":"1:50.000","coins":3,"shrooms":1}]}"#;
        let ev: serde_json::Value = serde_json::from_str(&route_line(&conn, line).unwrap()).unwrap();
        assert_eq!(ev["type"], "pb_achieved");
        assert_eq!(outbox_pending(&conn).len(), 1);        // ready → will drain
        assert_eq!(outbox_list_pending(&conn).len(), 0);
    }

    #[test]
    fn route_line_incomplete_goes_pending_review() {
        let conn = Connection::open_in_memory().unwrap();
        ensure_outbox(&conn); ensure_pb_cache(&conn);
        // missing kart → held
        let line = r#"{"type":"run_finalized","attempt_id":"b1","course":"Rainbow Road","character":"Mario","kart":"","status":"finished","total_time":"1:50.000"}"#;
        let ev: serde_json::Value = serde_json::from_str(&route_line(&conn, line).unwrap()).unwrap();
        assert_eq!(ev["type"], "run_needs_review");
        assert_eq!(ev["attempt_id"], "b1");
        assert!(ev["missing"].as_array().unwrap().iter().any(|m| m == "kart"));
        assert!(ev["run"].get("type").is_none());          // type stripped from the embedded run
        assert_eq!(outbox_pending(&conn).len(), 0);        // NOT uploaded
        assert_eq!(outbox_list_pending(&conn).len(), 1);   // held for review
    }

    #[test]
    fn outbox_status_routes_ready_vs_pending() {
        let conn = Connection::open_in_memory().unwrap();
        ensure_outbox(&conn);
        outbox_insert(&conn, "a1", LINE, "ready", false);
        outbox_insert(&conn, "a2", LINE, "pending_review", false);
        let pend: Vec<_> = outbox_pending(&conn).into_iter().map(|(id, _)| id).collect();
        assert_eq!(pend, vec!["a1"]);
        let review: Vec<_> = outbox_list_pending(&conn).into_iter().map(|(id, _, _)| id).collect();
        assert_eq!(review, vec!["a2"]);
    }

    #[test]
    fn outbox_update_ready_merges_body_and_flips_status() {
        let conn = Connection::open_in_memory().unwrap();
        ensure_outbox(&conn);
        outbox_insert(&conn, "a2", LINE, "pending_review", false);
        outbox_update_ready(&conn, "a2", r#"{"type":"run_finalized","attempt_id":"a2","course":"X"}"#);
        assert_eq!(outbox_pending(&conn).len(), 1);
        assert_eq!(outbox_list_pending(&conn).len(), 0);
        assert_eq!(outbox_get_body(&conn, "a2").unwrap(), r#"{"type":"run_finalized","attempt_id":"a2","course":"X"}"#);
    }

    fn _v(s: &str) -> serde_json::Value { serde_json::from_str(s).unwrap() }

    #[test]
    fn missing_fields_matrix() {
        let complete = _v(r#"{"course":"X","character":"Mario","kart":"K","status":"finished","total_time":"1:00.000"}"#);
        assert!(missing_fields(&complete, false).is_empty());

        let reset = _v(r#"{"course":"X","character":"","kart":"K","status":"reset"}"#);
        assert_eq!(missing_fields(&reset, false), vec!["character"]);

        let no_total = _v(r#"{"course":"X","character":"M","kart":"K","status":"finished"}"#);
        assert_eq!(missing_fields(&no_total, false), vec!["total_time"]);

        let pb_ok = _v(r#"{"course":"X","character":"M","kart":"K","status":"finished","total_time":"1:00.000","total_laps":2,
            "laps":[{"lap":1,"time_str":"0:30.000","coins":5,"shrooms":2},{"lap":2,"time_str":"0:30.000","coins":-1,"shrooms":0}]}"#);
        assert!(missing_fields(&pb_ok, true).is_empty());

        let pb_gap = _v(r#"{"course":"X","character":"M","kart":"K","status":"finished","total_time":"1:00.000","total_laps":2,
            "laps":[{"lap":1,"time_str":"0:30.000","coins":5,"shrooms":2},{"lap":2,"time_str":"0:30.000","coins":null,"shrooms":0}]}"#);
        assert_eq!(missing_fields(&pb_gap, true), vec!["lap_2_coins"]);

        let pb_no_n = _v(r#"{"course":"X","character":"M","kart":"K","status":"finished","total_time":"1:00.000"}"#);
        assert!(missing_fields(&pb_no_n, true).is_empty());
    }

    #[test]
    fn resolve_merges_filled_fields_and_releases() {
        let conn = Connection::open_in_memory().unwrap();
        ensure_outbox(&conn);
        outbox_insert(&conn, "b1",
            r#"{"type":"run_finalized","attempt_id":"b1","course":"Rainbow Road","character":"Mario","kart":null,"status":"finished"}"#,
            "pending_review", false);
        let filled: serde_json::Value = serde_json::from_str(r#"{"kart":"Standard Kart"}"#).unwrap();
        resolve_in_outbox(&conn, "b1", &filled);
        let body = outbox_get_body(&conn, "b1").unwrap();
        let v: serde_json::Value = serde_json::from_str(&body).unwrap();
        assert_eq!(v["kart"], "Standard Kart");
        assert_eq!(outbox_pending(&conn).len(), 1);            // now ready
        assert_eq!(outbox_list_pending(&conn).len(), 0);
    }

    #[test]
    fn list_pending_includes_persisted_is_pb() {
        let conn = Connection::open_in_memory().unwrap();
        ensure_outbox(&conn);
        // A held PB and a held non-PB.
        outbox_insert(&conn, "pb1",
            r#"{"type":"run_finalized","attempt_id":"pb1","course":"Rainbow Road","status":"finished"}"#,
            "pending_review", true);
        outbox_insert(&conn, "np1",
            r#"{"type":"run_finalized","attempt_id":"np1","course":"Mario Circuit","status":"reset"}"#,
            "pending_review", false);
        let rows = outbox_list_pending(&conn);
        let pb = rows.iter().find(|(id, _, _)| id == "pb1").unwrap();
        let np = rows.iter().find(|(id, _, _)| id == "np1").unwrap();
        assert_eq!(pb.2, true);
        assert_eq!(np.2, false);
    }

    #[test]
    fn course_cache_roundtrip_and_clear() {
        let conn = Connection::open_in_memory().unwrap();
        ensure_course_cache(&conn);
        assert_eq!(course_cache_get(&conn, "rainbow_road"), None);
        course_cache_put(&conn, "rainbow_road", r#"{"pb_splits":{}}"#);
        assert_eq!(course_cache_get(&conn, "rainbow_road").as_deref(), Some(r#"{"pb_splits":{}}"#));
        course_cache_put(&conn, "rainbow_road", r#"{"x":1}"#);   // upsert overwrites
        assert_eq!(course_cache_get(&conn, "rainbow_road").as_deref(), Some(r#"{"x":1}"#));
        course_cache_clear(&conn, "rainbow_road");
        assert_eq!(course_cache_get(&conn, "rainbow_road"), None);
    }

    #[test]
    fn course_slug_of_extracts_and_slugifies() {
        assert_eq!(course_slug_of(r#"{"course":"Rainbow Road"}"#).as_deref(), Some("rainbow_road"));
        assert_eq!(course_slug_of(r#"{"course":"Bowser's Castle"}"#).as_deref(), Some("bowsers_castle"));
        assert_eq!(course_slug_of(r#"{"status":"reset"}"#), None);   // no course field
        assert_eq!(course_slug_of("not json"), None);
    }

    #[test]
    fn tag_runs_with_player_adds_id() {
        let runs = serde_json::json!([{"run_id":10,"points":[]},{"run_id":20,"points":[]}]);
        let out = tag_runs_with_player(7, &runs);
        assert_eq!(out.len(), 2);
        assert_eq!(out[0]["player_id"], 7);
        assert_eq!(out[0]["run_id"], 10);
        assert_eq!(out[1]["player_id"], 7);
        assert!(tag_runs_with_player(7, &serde_json::json!("not an array")).is_empty());
    }

    #[test]
    fn course_from_selection_extracts_only_nonblank_selection_courses() {
        assert_eq!(
            course_from_selection(r#"{"type": "selection_update", "course": "Rainbow Road"}"#).as_deref(),
            Some("Rainbow Road"),
        );
        // Blank course → None (selection in progress, course not yet known).
        assert_eq!(course_from_selection(r#"{"type": "selection_update", "course": ""}"#), None);
        // No course key → None.
        assert_eq!(course_from_selection(r#"{"type": "selection_update", "character": "Mario"}"#), None);
        // Other event types / non-JSON → None.
        assert_eq!(course_from_selection(r#"{"type": "screen_change", "to": "RACING"}"#), None);
        assert_eq!(course_from_selection("heartbeat"), None);
    }

    #[test]
    fn is_racing_entry_only_fires_on_fresh_entry() {
        // Fresh entry from the pre-race flow → true.
        assert!(is_racing_entry(r#"{"type": "screen_change", "from": "RACE_INTRO", "to": "RACING"}"#));
        // Pause/resume re-entries → false (not a new run).
        assert!(!is_racing_entry(r#"{"type": "screen_change", "from": "RACE_MENU", "to": "RACING"}"#));
        assert!(!is_racing_entry(r#"{"type": "screen_change", "from": "HOME", "to": "RACING"}"#));
        // Already racing (self-transition) → false.
        assert!(!is_racing_entry(r#"{"type": "screen_change", "from": "RACING", "to": "RACING"}"#));
        // Leaving RACING / other transitions → false.
        assert!(!is_racing_entry(r#"{"type": "screen_change", "from": "RACING", "to": "POST_TIME_TRIAL"}"#));
        // Wrong event type / non-JSON → false.
        assert!(!is_racing_entry(r#"{"type": "selection_update", "course": "Rainbow Road"}"#));
        assert!(!is_racing_entry("not json"));
    }
}
