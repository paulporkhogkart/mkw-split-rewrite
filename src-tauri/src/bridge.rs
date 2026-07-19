//! One-time NSIS → Velopack handover (spec 2026-07-19 §3). The v3.0.0 bridge
//! release reaches existing installs through the OLD updater as a normal NSIS
//! update; this module then moves the install to Velopack.
//!
//! NSIS (Tauri's default, per-user) and Velopack both install to
//! `%LocalAppData%\pbenguin` — the SAME directory — so the old install must be
//! **fully gone** before Setup runs there, or the two collide (Setup writing
//! into an occupied tree, then an uninstall that can rip out the just-installed
//! files — failure modes up to "no app installed"). The detached helper this
//! module spawns therefore runs, in order:
//!   1. wait for this process to exit (our own exe is a live lock the
//!      uninstaller would otherwise have to fight; by construction it's
//!      already gone by the time step 2 starts),
//!   2. run the old install's `uninstall.exe /S`,
//!   3. poll until the install dir actually vanishes — a silent NSIS
//!      uninstaller re-spawns itself from a temp copy and returns control to
//!      `-Wait` before that copy has finished deleting the tree, so a fixed
//!      "run it and move on" would race Setup against the tail of the delete,
//!   4. run `Setup.exe --silent`, which now lands on empty ground.
//! If the dir somehow lingers past the poll timeout we proceed anyway — Setup
//! is the only remaining path forward at that point.
//! A silent NSIS uninstall never shows the delete-app-data checkbox, so both
//! %APPDATA% data roots (mkw-tracker and the Tauri identifier dir) survive.
//! Self-gating: on a Velopack install bridge_check is false forever.
use std::path::{Path, PathBuf};
use tauri::Emitter;

const REPO_URL: &str = "https://github.com/paulporkhogkart/mkw-split-rewrite";
const SETUP_ASSET: &str = "pbenguin-win-Setup.exe";

/// NSIS marker: Tauri's NSIS installer writes uninstall.exe beside the app exe.
/// Velopack marker: Update.exe one level above the app dir — and it wins, so a
/// Velopack install can never be mistaken for NSIS.
fn is_nsis_layout(exe_dir: &Path) -> bool {
    let velopack = exe_dir
        .parent()
        .map(|p| p.join("Update.exe").exists())
        .unwrap_or(false);
    !velopack && exe_dir.join("uninstall.exe").exists()
}

fn nsis_install_dir() -> Option<PathBuf> {
    if cfg!(debug_assertions) {
        return None; // dev runs are never an install of any kind
    }
    let dir = std::env::current_exe().ok()?.parent()?.to_path_buf();
    is_nsis_layout(&dir).then_some(dir)
}

/// The PowerShell the detached helper runs. Order is load-bearing (see module
/// doc comment): wait for our exit → uninstall the old NSIS tree → poll for the
/// install dir to actually vanish → run Setup into the now-empty dir.
fn helper_command(pid: u32, setup: &Path, uninstaller: &Path, install_dir: &Path) -> String {
    // Escape single quotes in paths for PowerShell single-quoted strings by doubling them.
    let setup_escaped = setup.display().to_string().replace('\'', "''");
    let uninst_escaped = uninstaller.display().to_string().replace('\'', "''");
    let installdir_escaped = install_dir.display().to_string().replace('\'', "''");
    format!(
        "Wait-Process -Id {pid} -ErrorAction SilentlyContinue; \
         Start-Process -Wait -FilePath '{uninst}' -ArgumentList '/S'; \
         for ($i=0; $i -lt 60 -and (Test-Path -LiteralPath '{installdir}'); $i++) {{ Start-Sleep -Milliseconds 500 }}; \
         Start-Process -Wait -FilePath '{setup}' -ArgumentList '--silent'",
        uninst = uninst_escaped,
        installdir = installdir_escaped,
        setup = setup_escaped,
    )
}

#[tauri::command]
pub fn bridge_check() -> Result<bool, String> {
    Ok(nsis_install_dir().is_some())
}

#[tauri::command]
pub async fn bridge_migrate(app: tauri::AppHandle) -> Result<(), String> {
    let install_dir = nsis_install_dir().ok_or("not an NSIS install")?;
    let url = format!(
        "{REPO_URL}/releases/download/v{}/{SETUP_ASSET}",
        env!("CARGO_PKG_VERSION")
    );
    let setup_path = std::env::temp_dir().join(SETUP_ASSET);

    // Blocking streamed download with progress on the same channel the normal
    // updater uses, so the title-bar strip needs no second wiring.
    let emitter = app.clone();
    let dest = setup_path.clone();
    tauri::async_runtime::spawn_blocking(move || -> Result<(), String> {
        let client = reqwest::blocking::Client::builder()
            .connect_timeout(std::time::Duration::from_secs(30))
            // Whole-request bound: the Setup exe is ~120 MB; 15 min covers a slow
            // connection while still guaranteeing a stalled transfer eventually errors
            // instead of wedging the migration forever.
            .timeout(std::time::Duration::from_secs(900))
            .build()
            .map_err(|e| e.to_string())?;
        let mut resp = client.get(&url).send().map_err(|e| e.to_string())?;
        if !resp.status().is_success() {
            return Err(format!("setup download failed: HTTP {}", resp.status()));
        }
        let total = resp.content_length().unwrap_or(0);
        let mut out = std::fs::File::create(&dest).map_err(|e| e.to_string())?;
        let mut done: u64 = 0;
        let mut buf = [0u8; 64 * 1024];
        loop {
            use std::io::{Read, Write};
            let n = resp.read(&mut buf).map_err(|e| e.to_string())?;
            if n == 0 {
                break;
            }
            out.write_all(&buf[..n]).map_err(|e| e.to_string())?;
            done += n as u64;
            if total > 0 {
                let _ = emitter.emit("update-progress", (done * 100 / total) as i16);
            }
        }
        Ok(())
    })
    .await
    .map_err(|e| e.to_string())??;

    let cmd = helper_command(
        std::process::id(),
        &setup_path,
        &install_dir.join("uninstall.exe"),
        &install_dir,
    );
    let mut helper = std::process::Command::new("powershell");
    helper.args(["-NoProfile", "-WindowStyle", "Hidden", "-Command", &cmd]);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt as _;
        helper.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    helper.spawn().map_err(|e| e.to_string())?;

    // Graceful exit (code Some(0) bypasses the close-to-tray prevent_exit guard,
    // and RunEvent::Exit kills the sidecar + WR runner). The helper takes over.
    app.exit(0);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    // Std-only temp dirs, same pattern as wr/state.rs (no dev-dependencies rule).
    fn tmpdir(tag: &str) -> std::path::PathBuf {
        let d = std::env::temp_dir().join(format!("bridge_test_{tag}"));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    #[test]
    fn nsis_layout_detected_by_uninstaller() {
        let d = tmpdir("nsis");
        assert!(!is_nsis_layout(&d), "empty dir is not an NSIS install");
        std::fs::write(d.join("uninstall.exe"), b"x").unwrap();
        assert!(is_nsis_layout(&d));
    }

    #[test]
    fn velopack_layout_wins_over_stray_uninstaller() {
        // Velopack layout: <root>/Update.exe + <root>/current/<exe>. Even if an
        // uninstall.exe somehow sits in current/, Update.exe above means "not NSIS".
        let root = tmpdir("velopack");
        let cur = root.join("current");
        std::fs::create_dir(&cur).unwrap();
        std::fs::write(root.join("Update.exe"), b"x").unwrap();
        std::fs::write(cur.join("uninstall.exe"), b"x").unwrap();
        assert!(!is_nsis_layout(&cur));
    }

    #[test]
    fn helper_command_escapes_single_quotes_in_paths() {
        let cmd = helper_command(
            7,
            std::path::Path::new("C:\\Users\\o'brien\\AppData\\Local\\Temp\\pbenguin-win-Setup.exe"),
            std::path::Path::new("C:\\Users\\o'brien\\AppData\\Local\\pbenguin\\uninstall.exe"),
            std::path::Path::new("C:\\Users\\o'brien\\AppData\\Local\\pbenguin"),
        );
        assert!(cmd.contains("o''brien"), "embedded ' must be doubled for PowerShell");
        // Every occurrence of the name must be the doubled form — no raw un-escaped
        // apostrophe may survive in any of the three interpolated paths.
        assert_eq!(cmd.matches("o'brien").count(), 0);
        assert!(cmd.matches("o''brien").count() >= 3, "setup, uninstaller, AND installdir must all be escaped");
    }

    #[test]
    fn helper_command_shape_uninstalls_before_reinstalling() {
        // Reordered per spec: NSIS (per-user default) and Velopack both install to
        // %LocalAppData%\pbenguin, so the old tree must be fully gone (uninstall +
        // poll for the dir to vanish) before Setup runs — never the old collision
        // order (Setup into the occupied dir, then uninstall).
        let cmd = helper_command(
            1234,
            std::path::Path::new("C:\\tmp\\pbenguin-win-Setup.exe"),
            std::path::Path::new("C:\\Users\\paul\\AppData\\Local\\pbenguin\\uninstall.exe"),
            std::path::Path::new("C:\\Users\\paul\\AppData\\Local\\pbenguin"),
        );
        assert!(cmd.contains("Wait-Process -Id 1234"));
        assert!(cmd.contains("uninstall.exe' -ArgumentList '/S'"));
        assert!(cmd.contains("pbenguin-win-Setup.exe' -ArgumentList '--silent'"));
        assert!(cmd.contains("Test-Path"), "must poll for the install dir to actually vanish");

        let wait_pos = cmd.find("Wait-Process").unwrap();
        let uninst_pos = cmd.find("uninstall.exe").unwrap();
        let poll_pos = cmd.find("Test-Path").unwrap();
        let setup_pos = cmd.find("Setup.exe").unwrap();

        assert!(wait_pos < uninst_pos, "helper must wait for our exit before uninstalling");
        assert!(uninst_pos < poll_pos, "uninstall must run before the poll loop");
        assert!(poll_pos < setup_pos, "poll loop must sit between uninstall and setup");
    }
}
