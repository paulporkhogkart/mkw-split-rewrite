use std::sync::Mutex;
use tauri::{Emitter, Manager};

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
struct SidecarState(Mutex<Option<std::process::Child>>);

/// Spawn (or re-spawn) the tracker sidecar and wire up its stdout listener.
fn do_spawn_sidecar(app: tauri::AppHandle, state: &SidecarState) {
    #[cfg(debug_assertions)]
    let spawn_result = {
        let project_root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .expect("project root");
        std::process::Command::new("python")
            .args(["-m", "mkw_tracker", "--ws-port", "8765", "--no-display"])
            .current_dir(project_root)
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .spawn()
    };

    #[cfg(not(debug_assertions))]
    let spawn_result = {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        let exe_dir = std::env::current_exe()
            .expect("current_exe")
            .parent()
            .expect("exe parent dir")
            .to_path_buf();
        std::process::Command::new(exe_dir.join("bin/mkw-tracker-engine.exe"))
            .args(["--ws-port", "8765", "--no-display"])
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .creation_flags(CREATE_NO_WINDOW)
            .spawn()
    };

    match spawn_result {
        Ok(mut child) => {
            // Drain stdout in a background thread, emitting each line as a Tauri event.
            if let Some(stdout) = child.stdout.take() {
                let handle = app.clone();
                std::thread::spawn(move || {
                    use std::io::BufRead;
                    for line in std::io::BufReader::new(stdout).lines() {
                        match line {
                            Ok(msg) if !msg.is_empty() => {
                                let _ = handle.emit("tracker-event", &msg);
                            }
                            _ => {}
                        }
                    }
                });
            }
            // Forward stderr to the frontend event log so crashes are visible.
            if let Some(stderr) = child.stderr.take() {
                let handle = app.clone();
                std::thread::spawn(move || {
                    use std::io::BufRead;
                    for line in std::io::BufReader::new(stderr).lines().flatten() {
                        eprintln!("[tracker stderr] {line}");
                        let msg = format!("{{\"type\":\"stderr\",\"line\":{}}}", serde_json::to_string(&line).unwrap_or_default());
                        let _ = handle.emit("tracker-event", &msg);
                    }
                });
            }
            *state.0.lock().unwrap() = Some(child);
        }
        Err(e) => eprintln!("Failed to spawn tracker: {e}"),
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
    if let Ok(mut guard) = state.0.lock() {
        if let Some(mut child) = guard.take() {
            let _ = child.kill();
        }
    }
}

/// Kill the running tracker and immediately restart it (e.g. after a device change).
#[tauri::command]
fn restart_tracker(app: tauri::AppHandle, state: tauri::State<SidecarState>) {
    if let Ok(mut guard) = state.0.lock() {
        if let Some(mut child) = guard.take() {
            let _ = child.kill();
        }
    }
    do_spawn_sidecar(app, &state);
}

/// Open a URI (e.g. ms-settings:camera) via the system shell.
#[tauri::command]
fn open_url(url: String) -> Result<(), String> {
    std::process::Command::new("cmd")
        .args(["/c", "start", "", &url])
        .spawn()
        .map(|_| ())
        .map_err(|e| e.to_string())
}

/// Write a newline-delimited JSON message to the tracker's stdin.
#[tauri::command]
fn send_to_tracker(state: tauri::State<SidecarState>, message: String) -> Result<(), String> {
    use std::io::Write;
    let mut guard = state.0.lock().map_err(|e| e.to_string())?;
    let child = guard.as_mut().ok_or("Tracker not running")?;
    let stdin = child.stdin.as_mut().ok_or("Tracker stdin not available")?;
    let mut data = message.into_bytes();
    data.push(b'\n');
    stdin.write_all(&data).map_err(|e| e.to_string())
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .invoke_handler(tauri::generate_handler![start_tracker, stop_tracker, restart_tracker, send_to_tracker, open_url])
        .setup(|app| {
            app.manage(SidecarState(Mutex::new(None)));
            #[cfg(target_os = "windows")]
            grant_media_permissions(&app.get_webview_window("main").expect("main window"));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            if let tauri::RunEvent::Exit = event {
                if let Some(state) = app_handle.try_state::<SidecarState>() {
                    if let Ok(mut guard) = state.0.lock() {
                        if let Some(mut child) = guard.take() {
                            let _ = child.kill();
                        }
                    }
                }
            }
        });
}
