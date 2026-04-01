use std::sync::Mutex;
use tauri::{Emitter, Manager};
use tauri_plugin_shell::{process::CommandEvent, ShellExt};

/// Silently grant camera permission to the webview so getUserMedia works without
/// the native "localhost wants to use your camera" popup. Must be called after
/// the window is created (i.e. inside the setup closure).
#[cfg(target_os = "windows")]
fn grant_camera_permission(window: &tauri::WebviewWindow) {
    let _ = window.with_webview(|webview| {
        use webview2_com::{
            Microsoft::Web::WebView2::Win32::{
                COREWEBVIEW2_PERMISSION_KIND,
                COREWEBVIEW2_PERMISSION_KIND_CAMERA,
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
                    if kind == COREWEBVIEW2_PERMISSION_KIND_CAMERA {
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
            .args(["-m", "mkw_tracker", "--ws-port", "8765", "--no-display"])
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
            .command(exe_dir.join("bin/mkw-tracker-x86_64-pc-windows-msvc.exe").to_string_lossy().as_ref())
            .args(["--ws-port", "8765", "--no-display"])
            .spawn()
    };

    match spawn_result {
        Ok((mut rx, child)) => {
            *state.0.lock().unwrap() = Some(child);

            let handle = app.clone();
            tauri::async_runtime::spawn(async move {
                while let Some(event) = rx.recv().await {
                    match event {
                        CommandEvent::Stdout(line) => {
                            let msg = String::from_utf8_lossy(&line);
                            let _ = handle.emit("tracker-event", msg.as_ref());
                        }
                        CommandEvent::Stderr(line) => {
                            eprintln!("[tracker stderr] {}", String::from_utf8_lossy(&line));
                        }
                        CommandEvent::Error(e) => {
                            eprintln!("[tracker error] {e}");
                        }
                        CommandEvent::Terminated(status) => {
                            eprintln!("[tracker] exited with {status:?}");
                        }
                        _ => {}
                    }
                }
            });
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

/// Kill the running tracker and immediately restart it (e.g. after a device change).
#[tauri::command]
fn restart_tracker(app: tauri::AppHandle, state: tauri::State<SidecarState>) {
    if let Ok(mut guard) = state.0.lock() {
        if let Some(child) = guard.take() {
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
    let mut guard = state.0.lock().map_err(|e| e.to_string())?;
    let child = guard.as_mut().ok_or("Tracker not running")?;
    let mut data = message.into_bytes();
    data.push(b'\n');
    child.write(&data).map_err(|e| e.to_string())
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .invoke_handler(tauri::generate_handler![start_tracker, restart_tracker, send_to_tracker, open_url])
        .setup(|app| {
            app.manage(SidecarState(Mutex::new(None)));
            #[cfg(target_os = "windows")]
            grant_camera_permission(&app.get_webview_window("main").expect("main window"));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            if let tauri::RunEvent::Exit = event {
                if let Some(state) = app_handle.try_state::<SidecarState>() {
                    if let Ok(mut guard) = state.0.lock() {
                        if let Some(child) = guard.take() {
                            let _ = child.kill();
                        }
                    }
                }
            }
        });
}
