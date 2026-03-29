use std::sync::Mutex;
use tauri::{Emitter, Manager};
use tauri_plugin_shell::{process::CommandEvent, ShellExt};

/// Holds the Python sidecar child process once started. None until start_tracker is called.
struct SidecarState(Mutex<Option<tauri_plugin_shell::process::CommandChild>>);

/// Spawns the Python tracker sidecar. Called from the frontend only after confirming
/// no update is pending, so the process is never started unnecessarily.
#[tauri::command]
fn start_tracker(app: tauri::AppHandle, state: tauri::State<SidecarState>) {
    let shell = app.shell();

    #[cfg(debug_assertions)]
    let spawn_result = {
        let project_root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .expect("project root");
        shell
            .command("python")
            .args(["-m", "mkw_tracker"])
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
            .command(exe_dir.join("mkw-tracker-x86_64-pc-windows-msvc.exe").to_string_lossy().as_ref())
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

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .invoke_handler(tauri::generate_handler![start_tracker])
        .setup(|app| {
            app.manage(SidecarState(Mutex::new(None)));
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
