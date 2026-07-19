//! Chip sprite-sheet cache + full-pack download (spec 2026-07-19).
//! store = disk layout/lock/tags (pure). net = site fetch + manifest rewrite.
//! pack = full-pack downloader. commands = tauri commands + protocol glue.

pub mod net;
pub mod pack;
pub mod protocol;
pub mod store;

#[cfg(test)]
pub mod testutil;

/// `<app-data>/chips`. Panics never: app_data_dir is infallible post-setup on Windows.
pub fn chips_root<R: tauri::Runtime>(app: &tauri::AppHandle<R>) -> std::path::PathBuf {
    use tauri::Manager;
    app.path().app_data_dir().expect("app_data_dir").join("chips")
}

/// Tag an in-flight full-pack download is filling (eviction must spare it). Task 6 wires it.
pub fn active_pack_tag() -> Option<String> {
    ACTIVE_PACK_TAG.lock().unwrap_or_else(|e| e.into_inner()).clone()
}
pub static ACTIVE_PACK_TAG: std::sync::Mutex<Option<String>> = std::sync::Mutex::new(None);
