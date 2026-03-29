use std::sync::Mutex;
use tauri::{Emitter, Manager};
use tauri_plugin_shell::{process::CommandEvent, ShellExt};

/// Keeps the sidecar process alive for the lifetime of the app.
#[allow(dead_code)]
struct SidecarState(Mutex<tauri_plugin_shell::process::CommandChild>);

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .setup(|app| {
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
                // The Python sidecar is installed alongside the Tauri exe as
                // mkw-tracker-x86_64-pc-windows-msvc.exe (via bundle.resources).
                // We cannot use shell.sidecar() because the binary is not
                // registered under externalBin — that caused NSIS to install it
                // as mkw-tracker.exe, overwriting the Tauri binary.
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
                    app.manage(SidecarState(Mutex::new(child)));

                    let handle = app.handle().clone();
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
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
