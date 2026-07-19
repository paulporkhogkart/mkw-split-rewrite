//! Velopack-based auto-updater (spec 2026-07-19). Replaces tauri-plugin-updater:
//! delta downloads, on-disk package persistence, auto-apply of pending updates
//! on next boot (VelopackApp default). In dev / portable runs UpdateManager::new
//! fails with NotInstalled and every command degrades to "no update".
use std::sync::Mutex;
use tauri::Emitter;
use velopack::{sources, UpdateCheck, UpdateInfo, UpdateManager};

const REPO_URL: &str = "https://github.com/paulporkhogkart/mkw-split-rewrite";

/// The update found by update_check, held until update_apply.
pub struct PendingUpdate(pub Mutex<Option<UpdateInfo>>);

/// PBENGUIN_UPDATE_PATH (a local `vpk pack` output dir) overrides GitHub —
/// only the local rehearsal sets it.
enum Feed {
    Dir(String),
    Github,
}

fn resolve_feed(env_val: Option<String>) -> Feed {
    match env_val {
        Some(p) if !p.trim().is_empty() => Feed::Dir(p),
        _ => Feed::Github,
    }
}

fn manager() -> Result<UpdateManager, velopack::Error> {
    match resolve_feed(std::env::var("PBENGUIN_UPDATE_PATH").ok()) {
        Feed::Dir(p) => UpdateManager::new(sources::FileSource::new(&p), None, None),
        Feed::Github => {
            UpdateManager::new(sources::GithubSource::new(REPO_URL, None, false), None, None)
        }
    }
}

#[tauri::command]
pub async fn update_check(
    pending: tauri::State<'_, PendingUpdate>,
) -> Result<Option<String>, String> {
    // NotInstalled (dev build, portable copy) is a normal state, not an error.
    let um = match manager() {
        Ok(m) => m,
        Err(_) => return Ok(None),
    };
    match um.check_for_updates() {
        Ok(UpdateCheck::UpdateAvailable(info)) => {
            // check_for_updates returns Box<UpdateInfo>; UpdateInfo is Clone so unbox by value.
            let info = *info;
            let v = info.TargetFullRelease.Version.clone();
            *pending.0.lock().unwrap() = Some(info);
            Ok(Some(v))
        }
        Ok(_) => Ok(None),
        Err(e) => Err(e.to_string()),
    }
}

#[tauri::command]
pub async fn update_download(
    app: tauri::AppHandle,
    pending: tauri::State<'_, PendingUpdate>,
) -> Result<(), String> {
    let info = pending
        .0
        .lock()
        .unwrap()
        .clone()
        .ok_or("no pending update")?;
    let um = manager().map_err(|e| e.to_string())?;
    let (tx, rx) = std::sync::mpsc::channel::<i16>();
    let emitter = app.clone();
    std::thread::spawn(move || {
        for pct in rx {
            let _ = emitter.emit("update-progress", pct);
        }
    });
    tauri::async_runtime::spawn_blocking(move || um.download_updates(&info, Some(tx)))
        .await
        .map_err(|e| e.to_string())?
        .map_err(|e| e.to_string())?;
    let _ = app.emit("update-progress", 100i16);
    Ok(())
}

#[tauri::command]
pub async fn update_apply(pending: tauri::State<'_, PendingUpdate>) -> Result<(), String> {
    let info = pending
        .0
        .lock()
        .unwrap()
        .clone()
        .ok_or("no pending update")?;
    let um = manager().map_err(|e| e.to_string())?;
    // Exits this process on success (Update.exe swaps the app dir and relaunches).
    // The frontend has already invoked stop_tracker, so the engine is down.
    um.apply_updates_and_restart(&info.TargetFullRelease)
        .map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn env_path_overrides_github() {
        assert!(matches!(resolve_feed(Some("C:\\vpk\\out".into())), Feed::Dir(p) if p == "C:\\vpk\\out"));
    }

    #[test]
    fn blank_or_missing_env_means_github() {
        assert!(matches!(resolve_feed(None), Feed::Github));
        assert!(matches!(resolve_feed(Some("  ".into())), Feed::Github));
    }
}
