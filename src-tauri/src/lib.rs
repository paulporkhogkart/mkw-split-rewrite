mod bridge;
mod discord;
mod sync;
mod tray;
mod updater;
mod wr;

use std::sync::Mutex;
use tauri::{Emitter, Manager};
use tauri_plugin_shell::{process::CommandEvent, ShellExt};

/// Silently grant camera and microphone permissions to the webview so getUserMedia
/// works without native permission popups. Must be called after the window is
/// created (i.e. inside the setup closure).
#[cfg(target_os = "windows")]
fn grant_media_permissions(window: &tauri::WebviewWindow) {
    let _ = window.with_webview(|webview| {
        use webview2_com::{
            Microsoft::Web::WebView2::Win32::{
                COREWEBVIEW2_PERMISSION_KIND,
                COREWEBVIEW2_PERMISSION_KIND_CAMERA,
                COREWEBVIEW2_PERMISSION_KIND_MICROPHONE,
                COREWEBVIEW2_PERMISSION_STATE_ALLOW,
            },
            PermissionRequestedEventHandler,
        };

        unsafe {
            let wv = webview.controller().CoreWebView2().unwrap();
            let handler = PermissionRequestedEventHandler::create(Box::new(|_, args| {
                if let Some(args) = args {
                    let mut kind = COREWEBVIEW2_PERMISSION_KIND(0);
                    args.PermissionKind(&mut kind)?;
                    if kind == COREWEBVIEW2_PERMISSION_KIND_CAMERA
                        || kind == COREWEBVIEW2_PERMISSION_KIND_MICROPHONE
                    {
                        args.SetState(COREWEBVIEW2_PERMISSION_STATE_ALLOW)?;
                    }
                }
                Ok(())
            }));
            let mut token: i64 = 0;
            wv.add_PermissionRequested(&handler, &mut token).unwrap();
        }
    });
}

/// Holds the Python sidecar child process once started. None until start_tracker is called.
struct SidecarState(Mutex<Option<tauri_plugin_shell::process::CommandChild>>);

/// The WR service loop, when the run_wr_service setting is on. None otherwise.
pub struct RunnerState(pub Mutex<Option<wr::runner::Runner>>);

/// Spawn (or re-spawn) the tracker sidecar and wire up its stdout listener.
fn do_spawn_sidecar(app: tauri::AppHandle, state: &SidecarState) {
    let shell = app.shell();

    #[cfg(debug_assertions)]
    let spawn_result = {
        let project_root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .expect("project root");
        shell
            .command("python")
            .args(["-m", "mkw_tracker", "--no-display"])
            .current_dir(project_root)
            .spawn()
    };

    #[cfg(not(debug_assertions))]
    let spawn_result = {
        let exe_dir = std::env::current_exe()
            .expect("current_exe")
            .parent()
            .expect("exe parent dir")
            .to_path_buf();
        shell
            .command(exe_dir.join("bin/mkw-tracker-engine.exe").to_string_lossy().as_ref())
            .args(["--no-display"])
            .spawn()
    };

    match spawn_result {
        Ok((mut rx, child)) => {
            *state.0.lock().unwrap() = Some(child);
            wr::gate::ACTIVITY.set_tracking(true);
            // Notify the frontend immediately so it knows the process launched
            // and is just slow to produce output (e.g. Windows Defender scanning
            // _internal/ DLLs on first run).
            let _ = app.emit("tracker-event", "{\"type\":\"spawned\"}");

            let handle = app.clone();
            tauri::async_runtime::spawn(async move {
                while let Some(event) = rx.recv().await {
                    match event {
                        CommandEvent::Stdout(line) => {
                            let msg = String::from_utf8_lossy(&line);
                            // Idle-gate signal: only screen_change resets the WR idle clock
                            // (cheap substring check — the full JSON parse isn't needed here).
                            if msg.contains("\"type\":\"screen_change\"")
                                || msg.contains("\"type\": \"screen_change\"") {
                                wr::gate::ACTIVITY.note_screen_change();
                            }
                            let _ = handle.emit("tracker-event", msg.as_ref());
                            if let Some(ev) = sync::on_line(msg.as_ref()) {
                                let _ = handle.emit("tracker-event", &ev);
                            }
                        }
                        CommandEvent::Stderr(line) => {
                            let text = String::from_utf8_lossy(&line);
                            log::error!("[tracker stderr] {text}");
                            let msg = format!(
                                "{{\"type\":\"stderr\",\"line\":{}}}",
                                serde_json::to_string(text.as_ref()).unwrap_or_default()
                            );
                            let _ = handle.emit("tracker-event", &msg);
                        }
                        CommandEvent::Error(e) => {
                            log::error!("[tracker error] {e}");
                            let msg = format!(
                                "{{\"type\":\"stderr\",\"line\":{}}}",
                                serde_json::to_string(&format!("[spawn error] {e}")).unwrap_or_default()
                            );
                            let _ = handle.emit("tracker-event", &msg);
                        }
                        CommandEvent::Terminated(status) => {
                            wr::gate::ACTIVITY.set_tracking(false);
                            // A dead tracker (crash or kill) means no activity to report;
                            // without this a trayed app keeps the stale presence forever.
                            discord::discord_clear_presence();
                            log::error!("[tracker] exited with {status:?}");
                            let msg = format!(
                                "{{\"type\":\"stderr\",\"line\":{}}}",
                                serde_json::to_string(&format!("[tracker exited: {status:?}]")).unwrap_or_default()
                            );
                            let _ = handle.emit("tracker-event", &msg);
                        }
                        _ => {}
                    }
                }
            });
        }
        Err(e) => {
            log::error!("Failed to spawn tracker: {e}");
            let msg = format!(
                "{{\"type\":\"stderr\",\"line\":{}}}",
                serde_json::to_string(&format!("[failed to spawn tracker] {e}")).unwrap_or_default()
            );
            let _ = app.emit("tracker-event", &msg);
        }
    }
}

/// Spawns the Python tracker sidecar. Called from the frontend only after confirming
/// no update is pending, so the process is never started unnecessarily.
/// Idempotent: if the sidecar is already running (e.g. after an HMR reload) this is a no-op.
#[tauri::command]
fn start_tracker(app: tauri::AppHandle, state: tauri::State<SidecarState>) {
    if state.0.lock().unwrap().is_some() {
        return;
    }
    do_spawn_sidecar(app, &state);
}

/// Kill the running tracker without restarting it (e.g. before applying an update).
#[tauri::command]
fn stop_tracker(state: tauri::State<SidecarState>) {
    kill_sidecar(&state);
}

/// Delete BOTH app-owned data roots — the engine's %APPDATA%\mkw-tracker and the
/// Tauri identifier dir (wr_service.db background settings, sync outbox, WR video
/// cache) — then quit. The explicit in-app replacement for the old NSIS
/// "delete app data" checkbox (spec 2026-07-19 §5), which also covered both roots;
/// uninstall itself never deletes data. The frontend confirms before invoking.
/// On any failure: no exit — the error returns to the frontend, which shows it.
#[tauri::command]
fn delete_app_data(app: tauri::AppHandle, state: tauri::State<SidecarState>) -> Result<(), String> {
    // Stop everything that may hold files open under the data dirs. Runner stop
    // joins from the main thread — same pattern as wr_set_setting and RunEvent::Exit.
    if let Some(rs) = app.try_state::<RunnerState>() {
        let taken = rs.0.lock().unwrap_or_else(|e| e.into_inner()).take();
        if let Some(runner) = taken {
            runner.stop();
        }
    }
    kill_sidecar(&state);
    let mut targets: Vec<std::path::PathBuf> = Vec::new();
    if let Ok(d) = app.path().data_dir() {
        targets.push(d.join("mkw-tracker"));
    }
    if let Ok(d) = app.path().app_data_dir() {
        targets.push(d);
    }
    let mut failures: Vec<String> = Vec::new();
    for dir in targets {
        match std::fs::remove_dir_all(&dir) {
            Ok(()) => {}
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
            Err(e) => failures.push(format!("{}: {e}", dir.display())),
        }
    }
    if failures.is_empty() {
        app.exit(0);
        Ok(())
    } else {
        Err(failures.join("\n"))
    }
}

/// Kill the running tracker and immediately restart it (e.g. after a device change).
#[tauri::command]
fn restart_tracker(app: tauri::AppHandle, state: tauri::State<SidecarState>) {
    kill_sidecar(&state);
    do_spawn_sidecar(app, &state);
}

/// Open a URI (e.g. ms-settings:camera) via the system shell.
#[tauri::command]
fn open_url(url: String) -> Result<(), String> {
    let mut cmd = std::process::Command::new("cmd");
    cmd.args(["/c", "start", "", &url]);
    // Suppress the cmd console flash (GUI-subsystem parent spawning a console child).
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt as _;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    cmd.spawn().map(|_| ()).map_err(|e| e.to_string())
}

/// Write a newline-delimited JSON message to the tracker's stdin.
#[tauri::command]
fn send_to_tracker(state: tauri::State<SidecarState>, message: String) -> Result<(), String> {
    let mut guard = state.0.lock().map_err(|e| e.to_string())?;
    let child = guard.as_mut().ok_or("Tracker not running")?;
    let mut data = message.into_bytes();
    data.push(b'\n');
    child.write(&data).map_err(|e| e.to_string())
}

/// Resolve the screenshot folder: an explicit non-empty `dir`, else the user's
/// Pictures\pbenguin folder. Shared by save + open-in-explorer so both agree.
fn screenshot_dir(app: &tauri::AppHandle, dir: Option<String>) -> Result<std::path::PathBuf, String> {
    match dir {
        Some(d) if !d.trim().is_empty() => Ok(std::path::PathBuf::from(d)),
        _ => Ok(app
            .path()
            .picture_dir()
            .map_err(|e| e.to_string())?
            .join("pbenguin")),
    }
}

/// Save a PNG screenshot (raw bytes from the frontend canvas). Writes into `dir`
/// when given a non-empty path, else the user's Pictures\pbenguin folder. Returns
/// the full path written.
#[tauri::command]
fn save_screenshot(
    app: tauri::AppHandle,
    bytes: Vec<u8>,
    stamp: String,
    dir: Option<String>,
) -> Result<String, String> {
    let dir = screenshot_dir(&app, dir)?;
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let path = dir.join(format!("mkw-{stamp}.png"));
    std::fs::write(&path, &bytes).map_err(|e| e.to_string())?;
    Ok(path.to_string_lossy().into_owned())
}

/// Copy a PNG screenshot to the OS clipboard as an image. Decodes the (already
/// compressed) PNG to RGBA8 and hands it to the clipboard-manager plugin.
#[tauri::command]
fn copy_screenshot_to_clipboard(app: tauri::AppHandle, bytes: Vec<u8>) -> Result<(), String> {
    use tauri_plugin_clipboard_manager::ClipboardExt;
    let img = image::load_from_memory_with_format(&bytes, image::ImageFormat::Png)
        .map_err(|e| e.to_string())?
        .to_rgba8();
    let (w, h) = img.dimensions();
    let image = tauri::image::Image::new_owned(img.into_raw(), w, h);
    app.clipboard().write_image(&image).map_err(|e| e.to_string())
}

/// Open the screenshot folder in the system file explorer (for browsing, not a
/// picker). Creates it first so Explorer doesn't open on a missing folder.
#[tauri::command]
fn open_screenshot_dir(app: tauri::AppHandle, dir: Option<String>) -> Result<(), String> {
    let dir = screenshot_dir(&app, dir)?;
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    std::process::Command::new("explorer")
        .arg(&dir)
        .spawn()
        .map(|_| ())
        .map_err(|e| e.to_string())
}

/// True when this process was started by the login autostart entry. A --tray-start
/// launch creates NO window and NO webview (spec 2026-07-17 §1): the camera cannot be
/// touched by a hidden start because App.svelte's onMount->start_tracker only ever runs
/// inside a window the user asked for.
fn is_tray_start() -> bool {
    std::env::args().any(|a| a == "--tray-start")
}

/// Create the main window (the exact shape tauri.conf.json used to declare) or focus it
/// if it already exists. Every fresh creation re-runs the frontend's onMount, so live
/// tracking starts on restore just as it does on a normal launch.
pub fn show_main_window(app: &tauri::AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
        // Undo a hide-to-tray: the frontend reacquires the camera + frame poll. A no-op
        // listener-side unless a tray-hidden preceded it.
        let _ = w.emit("tray-shown", ());
        return;
    }
    match tauri::WebviewWindowBuilder::new(app, "main", tauri::WebviewUrl::default())
        .title("pbenguin")
        .inner_size(1200.0, 760.0)
        .min_inner_size(600.0, 440.0)
        .center()
        .resizable(true)
        .decorations(false)
        .build()
    {
        Ok(_w) => {
            #[cfg(target_os = "windows")]
            if let Some(w) = app.get_webview_window("main") {
                grant_media_permissions(&w);
            }
        }
        Err(e) => log::error!("create main window: {e}"),
    }
}

/// Kill the live engine sidecar if running. Shared by stop/restart commands, app exit,
/// and close-to-tray with keep_tracking_in_tray off (camera released).
fn kill_sidecar(state: &SidecarState) {
    if let Ok(mut guard) = state.0.lock() {
        if let Some(child) = guard.take() {
            let _ = child.kill();
            wr::gate::ACTIVITY.set_tracking(false);
        }
    }
    // Not tracking = no activity to report. The webview (the only presence driver) may
    // already be destroyed — e.g. close-to-tray — so the last payload would otherwise
    // stay live on Discord until full app exit.
    discord::discord_clear_presence();
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            // A second manual launch while trayed: surface the existing instance.
            // Load-bearing, not cosmetic — two processes would share one worker-id and
            // sweep_orphans would glob-delete each other's live download (spec §1).
            show_main_window(app);
        }))
        .plugin(
            tauri_plugin_log::Builder::new()
                .target(tauri_plugin_log::Target::new(
                    tauri_plugin_log::TargetKind::Webview,
                ))
                .build(),
        )
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_clipboard_manager::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            Some(vec!["--tray-start"]),
        ))
        .invoke_handler(tauri::generate_handler![start_tracker, stop_tracker, restart_tracker, delete_app_data, send_to_tracker, open_url, save_screenshot, copy_screenshot_to_clipboard, open_screenshot_dir, discord::discord_set_presence, discord::discord_clear_presence, sync::sync_set_config, sync::sync_test_connection, sync::sync_resolve_pending, sync::sync_discard_pending, sync::sync_list_pending, sync::sync_course_reads, sync::sync_roster, sync::sync_pb_best, wr::wr_process_one, wr::wr_get_settings, wr::wr_set_setting, updater::update_check, updater::update_download, updater::update_apply, bridge::bridge_check, bridge::bridge_migrate])
        .setup(|app| {
            app.manage(SidecarState(Mutex::new(None)));
            app.manage(RunnerState(Mutex::new(None)));
            app.manage(updater::PendingUpdate(Mutex::new(None)));
            if let Ok(c) = wr::settings_db(app.handle()) {
                if wr::state::get_flag(&c, wr::state::SETTING_RUN_WR_SERVICE) {
                    let runner = wr::runner::Runner::start(app.handle().clone());
                    *app.state::<RunnerState>().0.lock().unwrap() = Some(runner);
                }
                // Re-assert the login autostart entry every boot: the Run key stores an absolute
                // exe path, and the NSIS→Velopack migration (and any future install move) makes
                // the stored path stale. enable() is an idempotent registry write.
                if wr::state::get_flag(&c, wr::state::SETTING_START_AT_LOGIN) {
                    use tauri_plugin_autostart::ManagerExt;
                    if let Err(e) = app.autolaunch().enable() {
                        log::warn!("autostart re-assert failed: {e}");
                    }
                }
            }
            tray::sync_tray(app.handle());
            if let Some(rs) = app.try_state::<RunnerState>() {
                if let Ok(guard) = rs.0.lock() {
                    if let Some(r) = guard.as_ref() {
                        let h = app.handle().clone();
                        r.set_refresh_hook(Box::new(move || {
                            // Post-and-forget: tray setters BLOCK until the main thread
                            // services them (run_item_main_thread!), and Runner::stop()
                            // joins this thread FROM the main thread — a direct call here
                            // deadlocks every quit. A queued task at exit simply never
                            // runs, which is harmless.
                            let h2 = h.clone();
                            let _ = h.run_on_main_thread(move || crate::tray::refresh_tray_status(&h2));
                        }));
                    }
                }
            }
            {
                let h = app.handle().clone();
                wr::phase::set_notifier(Box::new(move || {
                    let h2 = h.clone();
                    let _ = h.run_on_main_thread(move || tray::refresh_tray_status(&h2));
                }));
            }
            if !is_tray_start() {
                show_main_window(app.handle());
            }
            sync::init(app.handle().clone());
            Ok(())
        })
        .on_window_event(|window, event| {
            // This logic is for the main window only — never a future secondary window.
            if window.label() != "main" { return; }
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                let app = window.app_handle();
                let Ok(c) = wr::settings_db(app) else { return };
                if !wr::state::get_flag(&c, wr::state::SETTING_CLOSE_TO_TRAY) {
                    return; // default: closing quits, exactly as today
                }
                api.prevent_close();
                if wr::state::get_flag(&c, wr::state::SETTING_KEEP_TRACKING_IN_TRAY) {
                    // Tracking continues in the tray: HIDE (don't destroy) so the webview
                    // keeps driving Discord presence from live tracker events (2026-07-19,
                    // amends spec §1's destroy decision). tray-hidden tells the frontend
                    // to release the browser camera + stop the frame poll, so the hidden
                    // window is near-idle; Chromium throttles all rendering at zero
                    // visibility. show_main_window's tray-shown undoes both.
                    let _ = window.emit("tray-hidden", ());
                    let _ = window.hide();
                } else {
                    // The camera light goes OFF on tray-enter unless the user opted to
                    // keep tracking (spec §3) — kill the engine and destroy the webview
                    // (a fresh onMount on restore is the same path as a normal launch;
                    // destroy() bypasses CloseRequested, so no loop).
                    if let Some(state) = app.try_state::<SidecarState>() {
                        kill_sidecar(&state);
                    }
                    let _ = window.destroy();
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            match event {
                // Last-window-destroyed arrives with code: None; a deliberate app.exit(n)
                // (tray Quit) carries Some(n). Stay resident only for the former, and only
                // when close-to-tray is on — i.e. exactly when our close handler just
                // destroyed the window expecting the process to live on in the tray.
                // Everything else (normal quit with the flag off, tray Quit, OS session
                // end) falls through to a real exit.
                tauri::RunEvent::ExitRequested { code, api, .. } => {
                    if code.is_none() {
                        if let Ok(c) = wr::settings_db(app_handle) {
                            if wr::state::get_flag(&c, wr::state::SETTING_CLOSE_TO_TRAY) {
                                api.prevent_exit();
                            }
                        }
                    }
                }
                tauri::RunEvent::Exit => {
                    if let Some(rs) = app_handle.try_state::<RunnerState>() {
                        // Guard dropped before stop(): see wr_set_setting's same pattern.
                        let taken = rs.0.lock().unwrap_or_else(|e| e.into_inner()).take();
                        if let Some(runner) = taken { runner.stop(); }
                    }
                    if let Some(state) = app_handle.try_state::<SidecarState>() {
                        if let Ok(mut guard) = state.0.lock() {
                            if let Some(child) = guard.take() {
                                let _ = child.kill();
                            }
                        }
                    }
                    discord::shutdown();
                }
                _ => {}
            }
        });
}
