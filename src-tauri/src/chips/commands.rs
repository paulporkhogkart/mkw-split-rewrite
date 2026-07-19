//! Tauri commands + runner thread management. Logic lives in store/net/pack (tested);
//! this file is glue: settings flags, thread spawn, event emit.

use super::{chips_root, pack, store};
use crate::wr;
use std::path::Path;
use std::sync::atomic::{AtomicU8, Ordering};
use std::sync::{Arc, Mutex};

pub struct ChipsJob { pub ctl: Arc<AtomicU8> }
pub struct ChipsState(pub Mutex<Option<ChipsJob>>);

/// (file count, byte total) under every tag dir. Walks — called on settings open only.
pub fn cached_stats(dir: &Path) -> (u64, u64) {
    fn walk(p: &Path, acc: &mut (u64, u64)) {
        let Ok(rd) = std::fs::read_dir(p) else { return };
        for e in rd.flatten() {
            let Ok(md) = e.metadata() else { continue };
            if md.is_dir() { walk(&e.path(), acc); }
            else { acc.0 += 1; acc.1 += md.len(); }
        }
    }
    let mut acc = (0, 0);
    walk(dir, &mut acc);
    acc
}

fn complete_pack_tag(dir: &Path) -> Option<String> {
    let rd = std::fs::read_dir(dir).ok()?;
    rd.flatten().find_map(|e| {
        let n = e.file_name().to_str()?.to_string();
        (store::valid_tag(&n) && e.path().join(".complete").exists()).then_some(n)
    })
}

#[tauri::command]
pub fn chips_get_status(app: tauri::AppHandle) -> serde_json::Value {
    let dir = chips_root(&app);
    let (files, bytes) = cached_stats(&dir);
    let current = store::current_tag(&dir);
    let pack_tag = complete_pack_tag(&dir);
    let (wanted, paused) = wr::settings_db(&app).map(|c| (
        wr::state::get_flag(&c, wr::state::SETTING_CHIPS_PACK_WANTED),
        wr::state::get_flag(&c, wr::state::SETTING_CHIPS_PACK_PAUSED),
    )).unwrap_or((false, false));
    serde_json::json!({
        "currentTag": current, "cachedFiles": files, "cachedBytes": bytes,
        "packComplete": pack_tag.is_some(), "packTag": pack_tag,
        "packWanted": wanted, "packPaused": paused,
        "updateAvailable": matches!((&pack_tag, &current), (Some(p), Some(c)) if p != c),
    })
}

fn spawn_runner(app: tauri::AppHandle, state: &ChipsState) {
    let mut guard = state.0.lock().unwrap_or_else(|e| e.into_inner());
    if let Some(j) = guard.as_ref() {
        if j.ctl.load(Ordering::SeqCst) == pack::CTL_RUN { return; } // already running
    }
    let ctl = Arc::new(AtomicU8::new(pack::CTL_RUN));
    *guard = Some(ChipsJob { ctl: ctl.clone() });
    let dir = chips_root(&app);
    std::thread::spawn(move || {
        use tauri::Emitter;
        let emit_app = app.clone();
        let emit = move |p: &pack::Progress| { let _ = emit_app.emit("chips-progress", p); };
        match pack::run_pack(&dir, &ctl, &emit) {
            Ok(pack::Outcome::Complete) => log::info!("[chips] pack complete"),
            Ok(_) => log::info!("[chips] pack interrupted (pause/cancel)"),
            Err(e) => {
                log::error!("[chips] pack failed: {e}");
                let _ = app.emit("chips-progress", serde_json::json!({
                    "tag": "", "done": 0, "total": 0, "shard": "", "shard_bytes": 0,
                    "state": "error", "error": e,
                }));
            }
        }
    });
}

#[tauri::command]
pub fn chips_start_pack(app: tauri::AppHandle, state: tauri::State<ChipsState>) {
    if let Ok(c) = wr::settings_db(&app) {
        wr::state::set_flag(&c, wr::state::SETTING_CHIPS_PACK_WANTED, true);
        wr::state::set_flag(&c, wr::state::SETTING_CHIPS_PACK_PAUSED, false);
    }
    spawn_runner(app, &state);
}

#[tauri::command]
pub fn chips_pause_pack(app: tauri::AppHandle, state: tauri::State<ChipsState>) {
    if let Some(j) = state.0.lock().unwrap_or_else(|e| e.into_inner()).as_ref() {
        j.ctl.store(pack::CTL_PAUSE, Ordering::SeqCst);
    }
    if let Ok(c) = wr::settings_db(&app) {
        wr::state::set_flag(&c, wr::state::SETTING_CHIPS_PACK_PAUSED, true);
    }
}

#[tauri::command]
pub fn chips_cancel_pack(app: tauri::AppHandle, state: tauri::State<ChipsState>) {
    if let Some(j) = state.0.lock().unwrap_or_else(|e| e.into_inner()).as_ref() {
        j.ctl.store(pack::CTL_CANCEL, Ordering::SeqCst);
    }
    if let Ok(c) = wr::settings_db(&app) {
        wr::state::set_flag(&c, wr::state::SETTING_CHIPS_PACK_WANTED, false);
        wr::state::set_flag(&c, wr::state::SETTING_CHIPS_PACK_PAUSED, false);
    }
    let dir = chips_root(&app);
    // staging of whichever tag the runner was on; sweep every tag's leftovers
    if let Ok(rd) = std::fs::read_dir(&dir) {
        for e in rd.flatten() {
            if e.file_name().to_str().map(store::valid_tag).unwrap_or(false) {
                let _ = std::fs::remove_dir_all(e.path().join(".stage"));
                let _ = std::fs::remove_file(e.path().join(".pack-state.json"));
            }
        }
    }
}

#[tauri::command]
pub fn chips_delete_cache(app: tauri::AppHandle, state: tauri::State<ChipsState>) {
    chips_cancel_pack(app.clone(), state);
    let _ = std::fs::remove_dir_all(chips_root(&app));
}

/// setup() hook: resume a wanted, unpaused, incomplete pack quietly. A complete pack on
/// the CURRENT tag means there is nothing to do; a complete pack on a stale tag does NOT
/// auto-redownload (spec: update is an explicit button) — so bail whenever any complete
/// pack exists.
pub fn boot_resume(app: &tauri::AppHandle) {
    use tauri::Manager;
    let Ok(c) = wr::settings_db(app) else { return };
    if !wr::state::get_flag(&c, wr::state::SETTING_CHIPS_PACK_WANTED) { return; }
    if wr::state::get_flag(&c, wr::state::SETTING_CHIPS_PACK_PAUSED) { return; }
    if complete_pack_tag(&chips_root(app)).is_some() { return; }
    let state = app.state::<ChipsState>();
    spawn_runner(app.clone(), &state);
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::chips::testutil::TmpDir;

    #[test]
    fn cached_stats_counts_files_and_bytes_recursively() {
        let t = TmpDir::new();
        std::fs::write(t.path().join("a.tar"), b"hello").unwrap();
        let sub = t.path().join("chips-v1").join("chips");
        std::fs::create_dir_all(&sub).unwrap();
        std::fs::write(sub.join("b__idle.webp"), b"world!").unwrap();
        let (files, bytes) = cached_stats(t.path());
        assert_eq!(files, 2);
        assert_eq!(bytes, 5 + 6);
    }
}
