# Velopack Auto-Updater Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Tauri NSIS updater with Velopack so pbenguin gets delta updates, persistent/resumable downloads, and apply-on-any-restart, with a one-time automatic NSIS→Velopack bridge at v3.0.0.

**Architecture:** The `velopack` Rust crate replaces `tauri-plugin-updater` (a new `updater.rs` exposes `update_check`/`update_download`/`update_apply` commands; `VelopackApp::build().run()` boots first in `main()` and auto-applies pending updates). A `bridge.rs` module detects an NSIS install at runtime and hands it over to a silently-installed Velopack copy. CI packs with `vpk` instead of NSIS; the bridge release (v3.0.0) ships both artifact sets.

**Tech Stack:** Rust (Tauri v2), `velopack` crate, Svelte 4 frontend, `vpk` CLI (dotnet tool) in GitHub Actions, GitHub Releases as the update feed.

**Spec:** `docs/superpowers/specs/2026-07-19-velopack-updater-migration-design.md` — read it before starting.

## Global Constraints

- **Updates and uninstalls must NEVER delete `%APPDATA%\mkw-tracker`.** The only data-delete path is the explicit in-app Settings button (Task 4).
- The **first pbenguin release after this plan merges MUST be tagged `v3.0.0`** — CI gates the bridge NSIS artifacts on `startsWith(github.ref_name, 'v3.0.0')`. No other pbenguin tag may ship in between (old clients would see no `latest.json`).
- Keep the NSIS bundle config (`tauri.conf.json` → `bundle.windows.nsis`, `createUpdaterArtifacts`, `installer-hooks.nsh`) and the minisign GitHub secrets **until after v3.0.0 ships**. Only `plugins.updater` is removed now.
- `VelopackApp::build().run()` must be the **first statement of `main()`**, before any Tauri code.
- The title-bar update-strip UX in `App.svelte` keeps its current look (label + progress track + "Restart to apply" button).
- Repo URL constant everywhere: `https://github.com/paulporkhogkart/mkw-split-rewrite`. Velopack `packId`: `pbenguin`. Setup asset name: `pbenguin-win-Setup.exe`.
- Third-party signatures shown here match docs.rs/velopack at plan time; if `cargo check` disagrees, adapt to the crate's actual signature — do not restructure the design.
- Rust tests live in-module under `#[cfg(test)]` following the existing pattern in `src-tauri/src/wr/state.rs`. Run them with `cargo test` from `src-tauri/`.

---

### Task 1: Rust updater module (`updater.rs`), Velopack bootstrap, plugin removal

**Files:**
- Create: `src-tauri/src/updater.rs`
- Modify: `src-tauri/Cargo.toml` (deps)
- Modify: `src-tauri/src/main.rs` (Velopack bootstrap)
- Modify: `src-tauri/src/lib.rs:1-7` (mod decl), `:327` (remove plugin), `:335` (invoke_handler), `:337` (manage state)
- Modify: `src-tauri/tauri.conf.json:37-44` (remove `plugins.updater`)
- Modify: `src-tauri/capabilities/default.json:12` (remove `updater:default`)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces (used by Tasks 3 & 7):
  - `update_check() -> Result<Option<String>, String>` — `Some(version_string)` when an update exists (also stashes it in `PendingUpdate` state); `None` in dev/portable mode or when current.
  - `update_download() -> Result<(), String>` — downloads (delta-first), emitting `update-progress` events with an integer percent payload (`i16`, 0–100).
  - `update_apply() -> Result<(), String>` — applies the stashed update and restarts; does not return on success.
  - `pub struct PendingUpdate(pub Mutex<Option<UpdateInfo>>)` managed in Tauri state.
  - Env override `PBENGUIN_UPDATE_PATH` (a local `vpk pack` output dir) switches the feed from GitHub to a local dir — the hook Task 7's rehearsal uses.

- [ ] **Step 1: Write the failing unit test for feed resolution**

At the bottom of a new `src-tauri/src/updater.rs`, start with only the test module and the enum it exercises (the enum not yet written, so the test fails to compile — that's the failing state):

```rust
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
```

Add `mod updater;` after `mod discord;` in `src-tauri/src/lib.rs:1`.

- [ ] **Step 2: Run the test to verify it fails**

Run (from `src-tauri/`): `cargo test updater`
Expected: COMPILE ERROR — `resolve_feed`/`Feed` not found.

- [ ] **Step 3: Add the velopack dependency and remove the old plugin**

Run (from `src-tauri/`): `cargo add velopack` (picks the latest release).
In `src-tauri/Cargo.toml` delete the line `tauri-plugin-updater = "2"`.

- [ ] **Step 4: Write the updater module**

`src-tauri/src/updater.rs` above the test module:

```rust
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
            let v = info.target_full_release.version.to_string();
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
    um.apply_updates_and_restart(&info.target_full_release)
        .map_err(|e| e.to_string())
}
```

If `UpdateInfo` turns out not to implement `Clone`, hold `Option<UpdateInfo>` behind the mutex and `.take()` it in `update_download`/`update_apply` instead of cloning — do not wrap in `Arc`.

- [ ] **Step 5: Bootstrap Velopack in `main.rs`**

Replace the body of `src-tauri/src/main.rs:4-6`:

```rust
fn main() {
    // Must run before anything else: handles Velopack install/update/uninstall hook
    // invocations and auto-applies a downloaded-but-unapplied update on boot (that is
    // the "quit instead of pressing the button" path — spec §1).
    velopack::VelopackApp::build().run();
    mkw_tracker_lib::run()
}
```

Because `velopack` is referenced from `main.rs` (the binary target) it is already a normal dependency; no extra wiring needed.

- [ ] **Step 6: Deregister the old plugin, register the new commands and state**

In `src-tauri/src/lib.rs`:
- Delete line 327: `.plugin(tauri_plugin_updater::Builder::new().build())`
- In the `invoke_handler` list (line 335) append: `updater::update_check, updater::update_download, updater::update_apply`
- In `setup` (after `app.manage(RunnerState(...))`, line 338) add:
  `app.manage(updater::PendingUpdate(Mutex::new(None)));`

In `src-tauri/tauri.conf.json` delete the whole `"updater": { ... }` block from `plugins` (leave `"plugins": {}`).
In `src-tauri/capabilities/default.json` delete the `"updater:default",` line.

- [ ] **Step 7: Run tests and compile check**

Run (from `src-tauri/`): `cargo test updater`
Expected: 2 passed.
Run: `cargo check`
Expected: clean (fix any velopack signature drift per Global Constraints).

- [ ] **Step 8: Commit**

```bash
git add src-tauri/
git commit -m "feat(updater): replace tauri-plugin-updater with velopack (delta + persistent downloads, auto-apply on boot)"
```

---

### Task 2: Bridge module — one-time NSIS→Velopack handover + autostart re-assert

**Files:**
- Create: `src-tauri/src/bridge.rs`
- Modify: `src-tauri/src/lib.rs` (mod decl, invoke_handler, setup autostart re-assert)

(No `[dev-dependencies]` in `src-tauri/Cargo.toml` — standing project rule. Tests use the repo's std-only `tmpdir(tag)` helper pattern, as in `src-tauri/src/wr/state.rs:143`.)

**Interfaces:**
- Consumes: `update-progress` event channel convention from Task 1 (same event name, integer percent payload).
- Produces (used by Task 3):
  - `bridge_check() -> Result<bool, String>` — `true` only when running from an NSIS install in release mode.
  - `bridge_migrate(app) -> Result<(), String>` — downloads `pbenguin-win-Setup.exe` for `CARGO_PKG_VERSION` (progress on `update-progress`), spawns a detached PowerShell helper (wait for our exit → run Setup silently → run NSIS uninstaller silently), then `app.exit(0)`.

**Ordering notes baked into the helper (spec §3 risks):** the old process fully exits before Setup runs, so the `single_instance` plugin never sees two live instances; the NSIS uninstaller runs last, after the old exe is dead, so no file locks; a silent (`/S`) NSIS uninstall never shows the delete-app-data checkbox, so `%APPDATA%\mkw-tracker` survives.

- [ ] **Step 1: Write the failing unit tests**

Create `src-tauri/src/bridge.rs` containing only:

```rust
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
    fn helper_command_shape() {
        let cmd = helper_command(
            1234,
            std::path::Path::new("C:\\tmp\\pbenguin-win-Setup.exe"),
            std::path::Path::new("C:\\Program Files\\pbenguin\\uninstall.exe"),
        );
        assert!(cmd.contains("Wait-Process -Id 1234"));
        assert!(cmd.contains("pbenguin-win-Setup.exe' -ArgumentList '--silent'"));
        assert!(cmd.contains("uninstall.exe' -ArgumentList '/S'"));
        let setup_pos = cmd.find("Setup.exe").unwrap();
        let uninst_pos = cmd.find("uninstall.exe").unwrap();
        assert!(setup_pos < uninst_pos, "setup must run before uninstall");
    }
}
```

Add `mod bridge;` after `mod updater;`… i.e. the top of `src-tauri/src/lib.rs` reads:

```rust
mod bridge;
mod discord;
mod sync;
mod tray;
mod updater;
mod wr;
```

- [ ] **Step 2: Run to verify failure**

Run (from `src-tauri/`): `cargo test bridge`
Expected: COMPILE ERROR — `is_nsis_layout` / `helper_command` not found.

- [ ] **Step 3: Implement the bridge module**

Fill `src-tauri/src/bridge.rs` above the tests:

```rust
//! One-time NSIS → Velopack handover (spec 2026-07-19 §3). The v3.0.0 bridge
//! release reaches existing installs through the OLD updater as a normal NSIS
//! update; this module then moves the install to Velopack:
//!   download pbenguin-win-Setup.exe (same version) → detached helper waits for
//!   this process to exit → Setup --silent installs to %LocalAppData% and
//!   launches the new copy → NSIS uninstall.exe /S removes the old install.
//! A silent NSIS uninstall never shows the delete-app-data checkbox, so
//! %APPDATA%\mkw-tracker is untouched. Self-gating: on a Velopack install
//! bridge_check is false forever.
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

/// The PowerShell the detached helper runs. Order is load-bearing: our process
/// must be gone before Setup launches the new app (single-instance plugin), and
/// the uninstaller runs last so it never fights a live exe for file locks.
fn helper_command(pid: u32, setup: &Path, uninstaller: &Path) -> String {
    format!(
        "Wait-Process -Id {pid} -ErrorAction SilentlyContinue; \
         Start-Process -Wait -FilePath '{setup}' -ArgumentList '--silent'; \
         Start-Process -FilePath '{uninst}' -ArgumentList '/S'",
        setup = setup.display(),
        uninst = uninstaller.display(),
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
        let mut resp = reqwest::blocking::get(&url).map_err(|e| e.to_string())?;
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
```

- [ ] **Step 4: Register commands and re-assert autostart on boot**

In `src-tauri/src/lib.rs`:
- Append to the `invoke_handler` list: `bridge::bridge_check, bridge::bridge_migrate`
- In `setup`, inside the existing `if let Ok(c) = wr::settings_db(app.handle())` block (line 339), after the WR-runner start, add:

```rust
// Re-assert the login autostart entry every boot: the Run key stores an absolute
// exe path, and the NSIS→Velopack migration (and any future install move) makes
// the stored path stale. enable() is an idempotent registry write.
if wr::state::get_flag(&c, wr::state::SETTING_START_AT_LOGIN) {
    use tauri_plugin_autostart::ManagerExt;
    if let Err(e) = app.autolaunch().enable() {
        log::warn!("autostart re-assert failed: {e}");
    }
}
```

- [ ] **Step 5: Run tests**

Run (from `src-tauri/`): `cargo test bridge`
Expected: 3 passed.
Run: `cargo check`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src-tauri/
git commit -m "feat(bridge): one-time NSIS->Velopack handover + autostart re-assert on boot"
```

---

### Task 3: Frontend swap — `App.svelte` + drop the JS plugin

**Files:**
- Modify: `src/App.svelte:3` (import), `:112-123` (state), `:1420-1433` (functions), `~:1441` onMount (listener), `:1626-1636` (strip markup)
- Modify: `package.json` (remove `@tauri-apps/plugin-updater`)

**Interfaces:**
- Consumes: `update_check`/`update_download`/`update_apply` (Task 1), `bridge_check`/`bridge_migrate` (Task 2), `update-progress` event (integer percent payload).
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Remove the plugin import and dependency**

Delete `src/App.svelte:3`: `import { check } from "@tauri-apps/plugin-updater";`
Run: `npm uninstall @tauri-apps/plugin-updater`

- [ ] **Step 2: Replace the updater state block**

Replace `src/App.svelte:118-123` (`pendingUpdate` … reactive `downloadPercent`) with:

```js
  let updateVersion = "";
  let downloadPercent = null;   // set by update-progress events (0-100)
  let updateReady = false;
  let migrating = false;        // one-time NSIS→Velopack handover in flight
```

- [ ] **Step 3: Replace the updater functions**

Replace `applyUpdate`/`checkForUpdate` (`src/App.svelte:1420-1433`) with:

```js
  async function applyUpdate() {
    await invoke("stop_tracker");           // engine down before the app dir swaps
    try { await invoke("update_apply"); }   // does not return on success
    catch (e) { pushLog(`[update] apply failed: ${e}`); }
  }
  async function checkForUpdate() {
    try {
      if (await invoke("bridge_check")) {
        // One-time NSIS→Velopack handover: reuse the strip in "migrating" state.
        migrating = true; updateVersion = version;
        await invoke("bridge_migrate");     // downloads setup, then exits the app
        return;
      }
      const v = await invoke("update_check");
      if (!v) return;
      updateVersion = v;
      await invoke("update_download");      // progress arrives via update-progress
      updateReady = true;
    } catch (e) { pushLog(`[update] ${e}`); }
  }
```

(`pushLog` replaces the old fully-silent catch so a failed update is at least visible in the event log — the strip itself still never shows an error state.)

- [ ] **Step 4: Listen for progress events**

In `onMount` (`src/App.svelte:1441`), next to the existing `listen("tracker-event", …)` call, add:

```js
    _unlistenTray.push(await listen("update-progress", ev => { downloadPercent = ev.payload; }));
```

(`_unlistenTray` is the existing unlisten array — reusing it keeps teardown in one place.)

- [ ] **Step 5: Extend the strip label for the migrating state**

Replace the label line `src/App.svelte:1629`:

```svelte
          <span class="upd-label">{migrating ? `installer upgrade ${downloadPercent ?? 0}%` : updateReady ? `v${updateVersion} ready` : `v${updateVersion} ${downloadPercent !== null ? `${downloadPercent}%` : "…"}`}</span>
```

The `{#if !updateReady}` progress-track / button branch below is correct as-is for migration (never "ready", shows the track).

- [ ] **Step 6: Verify build and dev behavior**

Run: `npm run build` — expected: clean Svelte build.
Run: `npm run tauri dev` — expected: app boots; event log shows no `[update]` line (dev build: `bridge_check` false, `update_check` returns null via NotInstalled); update strip absent. Quit.

- [ ] **Step 7: Commit**

```bash
git add src/App.svelte package.json package-lock.json
git commit -m "feat(ui): drive the update strip from velopack commands (incl. bridge migrating state)"
```

---

### Task 4: Settings "Delete all app data…" button

**Files:**
- Modify: `src-tauri/src/lib.rs` (new `delete_app_data` command + handler registration)
- Modify: `src-tauri/capabilities/default.json` (add `dialog:allow-ask`)
- Modify: `src/components/SettingsModal.svelte:140-165` region (new Data section) + styles

**Interfaces:**
- Consumes: existing `kill_sidecar` helper (`src-tauri/src/lib.rs:298`).
- Produces: `delete_app_data() -> Result<(), String>` — kills the engine, deletes `%APPDATA%\mkw-tracker`, exits the app. This is the **only** data-delete path in the product (replaces the old NSIS checkbox, spec §5).

- [ ] **Step 1: Add the command**

In `src-tauri/src/lib.rs`, after `stop_tracker` (line 167), add:

```rust
/// Delete %APPDATA%\mkw-tracker (settings, replays, minimap tuning) and quit.
/// The explicit in-app replacement for the old NSIS "delete app data" checkbox
/// (spec 2026-07-19 §5) — uninstall itself never deletes data. The frontend
/// shows a native confirm dialog before invoking this.
#[tauri::command]
fn delete_app_data(app: tauri::AppHandle, state: tauri::State<SidecarState>) -> Result<(), String> {
    kill_sidecar(&state);
    let dir = app
        .path()
        .data_dir()
        .map_err(|e| e.to_string())?
        .join("mkw-tracker");
    match std::fs::remove_dir_all(&dir) {
        Ok(()) => {}
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
        Err(e) => return Err(e.to_string()),
    }
    app.exit(0);
    Ok(())
}
```

Append `delete_app_data` to the `invoke_handler` list.
In `src-tauri/capabilities/default.json`, after `"dialog:allow-open"` add `"dialog:allow-ask"`.

- [ ] **Step 2: Compile check**

Run (from `src-tauri/`): `cargo check`
Expected: clean.

- [ ] **Step 3: Add the Settings UI**

In `src/components/SettingsModal.svelte`:

Script (next to `chooseScreenshotDir`, line 28):

```js
  import { ask } from "@tauri-apps/plugin-dialog";
  async function deleteAppData() {
    const yes = await ask(
      "Delete ALL pbenguin data — settings, replays, minimap tuning — and quit?\nThis cannot be undone.",
      { title: "Delete app data", kind: "warning", okLabel: "Delete and quit", cancelLabel: "Cancel" },
    );
    if (yes) invoke("delete_app_data").catch((e) => console.error("delete_app_data failed", e));
  }
```

Markup — insert directly after the Background `</div>` (line 165), before the Continue button:

```svelte
            <div class="bg-section">
              <h3>Data</h3>
              <button class="btn-sm btn-danger" on:click={deleteAppData}>Delete all app data…</button>
              <div class="bg-hint">Removes settings, replays and tuning, then quits. Uninstalling pbenguin keeps this data.</div>
            </div>
```

Styles — next to the existing `.btn-sm` rules (line 468):

```css
  .btn-danger { color: var(--err, #c05a5a); border-color: rgba(192,90,90,.35); }
  .btn-danger:hover { background: rgba(192,90,90,.12); color: var(--err, #c05a5a); }
```

(Check `src/lib/palette.js` for the repo's error token name; use it if `--err` isn't defined.)

- [ ] **Step 4: Verify in dev**

Run: `npm run tauri dev` → gear icon → Settings → language tab shows the Data section. Click the button → native warning dialog appears → **Cancel** (don't nuke your own dev data — dev DB is at repo root anyway, and `%APPDATA%\mkw-tracker` may hold real tuning). Confirm the dialog opens and cancel leaves the app running.

- [ ] **Step 5: Commit**

```bash
git add src-tauri/ src/components/SettingsModal.svelte
git commit -m "feat(settings): explicit delete-all-app-data button (replaces NSIS uninstall checkbox)"
```

---

### Task 5: CI — Velopack packaging, bridge-gated NSIS, publish

**Files:**
- Modify: `.github/workflows/release.yml` (build-tauri + publish jobs)

**Interfaces:**
- Consumes: app-dir layout expectations from Task 1/2 (`pbenguin.exe` at root, engine at `bin/`), asset name `pbenguin-win-Setup.exe` (Task 2's download URL), packId `pbenguin`.
- Produces: GitHub Release assets per release: `pbenguin-win-Setup.exe`, full+delta `.nupkg`, `releases.win.json`, portable zip; **plus, for `v3.0.0` only**: the NSIS `*-setup.exe`, `.sig`, and `latest.json`.

- [ ] **Step 1: Switch the Tauri build step to no-bundle and assemble the pack dir**

In `.github/workflows/release.yml` `build-tauri` job, replace the "Build Tauri NSIS installer" step (line 244-248) with:

```yaml
      - name: Build Tauri app (no bundle)
        run: npm run tauri build -- --no-bundle

      - name: Assemble Velopack pack directory
        shell: bash
        run: |
          set -euo pipefail
          ls -lh src-tauri/target/release/*.exe
          mkdir -p pack/bin
          # tauri CLI names the binary after productName; fall back to the cargo name.
          cp src-tauri/target/release/pbenguin.exe pack/pbenguin.exe 2>/dev/null \
            || cp src-tauri/target/release/mkw-tracker.exe pack/pbenguin.exe
          cp src-tauri/sidecar/mkw-tracker-engine.exe pack/bin/mkw-tracker-engine.exe
          cp -r src-tauri/sidecar/_internal pack/bin/_internal
```

(The `bin/` layout mirrors today's installed tree — `lib.rs` spawns `exe_dir/bin/mkw-tracker-engine.exe`.)

- [ ] **Step 2: Add the vpk pack steps**

Directly after the assemble step:

```yaml
      - name: Install vpk CLI
        run: dotnet tool install -g vpk

      - name: Download previous release (delta baseline)
        # First Velopack release has no baseline — that's fine, vpk just skips deltas.
        continue-on-error: true
        run: vpk download github --repoUrl https://github.com/${{ github.repository }} -o velopack_releases

      - name: Velopack pack
        run: >-
          vpk pack -u pbenguin -v ${{ needs.build-python.outputs.version }}
          -p pack -e pbenguin.exe
          -i src-tauri/icons/icon.ico --packTitle pbenguin
          -o velopack_releases

      - name: List Velopack output (debug)
        shell: bash
        run: ls -lh velopack_releases/
```

- [ ] **Step 3: Gate the NSIS + latest.json steps to the bridge tag**

Re-add the old NSIS build as a **separate, bridge-only step** after the vpk steps (the old "Generate update manifest" python step body from line 254-298 is reused verbatim inside it):

```yaml
      # ── Bridge-only (v3.0.0): last NSIS artifacts so old clients update into the bridge ──
      - name: Build NSIS installer (bridge release only)
        if: startsWith(github.ref_name, 'v3.0.0')
        run: npm run tauri build -- --bundles nsis
        env:
          TAURI_SIGNING_PRIVATE_KEY: ${{ secrets.TAURI_SIGNING_PRIVATE_KEY }}
          TAURI_SIGNING_PRIVATE_KEY_PASSWORD: ${{ secrets.TAURI_SIGNING_PRIVATE_KEY_PASSWORD }}

      - name: Generate update manifest (bridge release only)
        if: startsWith(github.ref_name, 'v3.0.0')
        shell: bash
        run: |
          <verbatim body of the current "Generate update manifest (latest.json)" step>
        env:
          VERSION: ${{ needs.build-python.outputs.version }}
          REPO: ${{ github.repository }}
          TAG: ${{ github.ref_name }}
```

Update the two artifact-upload steps at the end of the job: the `tauri-installer` and `update-manifest` uploads get the same `if: startsWith(github.ref_name, 'v3.0.0')`, and add a new always-on upload:

```yaml
      - name: Upload Velopack artifacts
        uses: actions/upload-artifact@v7
        with:
          name: velopack-release
          path: velopack_releases/
          retention-days: 1
```

- [ ] **Step 4: Rework the publish job**

Replace the `publish` job's steps with (note `if: always()`-style download tolerance for the bridge-only artifacts, and switch the runner's asset list):

```yaml
    steps:
      - uses: actions/download-artifact@v8
        with:
          name: python-bundle

      - uses: actions/download-artifact@v8
        with:
          name: velopack-release
          path: velopack_releases

      - uses: actions/download-artifact@v8
        if: startsWith(github.ref_name, 'v3.0.0')
        with:
          name: tauri-installer

      - uses: actions/download-artifact@v8
        if: startsWith(github.ref_name, 'v3.0.0')
        with:
          name: update-manifest

      # After v3.0.0 ships, its latest.json gets committed as .github/bridge-latest.json
      # (post-bridge checklist). Re-publishing it on every later release keeps
      # releases/latest/download/latest.json resolving for straggler NSIS installs,
      # which then funnel through the bridge.
      - uses: actions/checkout@v6
        with:
          path: repo
      - name: Pin bridge latest.json for stragglers
        if: ${{ !startsWith(github.ref_name, 'v3.0.0') }}
        shell: bash
        run: |
          if [ -f repo/.github/bridge-latest.json ]; then
            cp repo/.github/bridge-latest.json latest.json
          fi

      - name: Create GitHub Release
        uses: softprops/action-gh-release@v2
        with:
          tag_name: ${{ github.ref_name }}
          name: ${{ github.ref_name }}
          generate_release_notes: true
          files: |
            *.zip
            *.exe
            *.exe.sig
            latest.json
            velopack_releases/*

      - name: Fail if release is missing Velopack assets
        shell: bash
        run: |
          ls velopack_releases/*.nupkg velopack_releases/releases.win.json
          ls velopack_releases/*Setup.exe
```

`softprops` skips missing patterns, so non-bridge releases simply have no `*-setup.exe.sig`/`latest.json` (until the pinned copy exists). The final `ls` step is the guard that the Velopack set (which `GithubSource` reads: the `.nupkg`s + `releases.win.json` + Setup) actually made it into the upload set.

- [ ] **Step 5: Validate workflow syntax**

Run: `npx --yes yaml-lint .github/workflows/release.yml` (or `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/release.yml'))"`)
Expected: parses clean.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci(release): pack + publish with vpk; NSIS + latest.json gated to the v3.0.0 bridge"
```

---

### Task 6: Docs — rewrite distribution.md, post-bridge checklist

**Files:**
- Modify: `docs/distribution.md` (full rewrite of the update-flow half)
- Modify: `CLAUDE.md` (one-line update in the Desktop app surface row, replacing any NSIS-updater description if present — currently none, so verify only)

**Interfaces:** none — documentation of Tasks 1–5.

- [ ] **Step 1: Rewrite `docs/distribution.md`**

Keep the "Python sidecar" and naming-split sections; replace the NSIS/auto-update/signing sections with the Velopack model. The rewrite must cover, concretely:

- Release asset table: `pbenguin-win-Setup.exe`, `pbenguin-<ver>-full.nupkg`, `pbenguin-<ver>-delta.nupkg`, `releases.win.json`, portable zip; bridge-only extras for v3.0.0.
- Update flow: `GithubSource` check → delta-first download into Velopack's packages dir (persistent, interruption-safe) → apply via button **or automatically on next boot** (`VelopackApp` default).
- Install layout: `%LocalAppData%\pbenguin\current\pbenguin.exe` + `bin\` engine; `Update.exe` above `current\`; per-user, no UAC.
- App-data policy (spec §5): updates/uninstall never touch `%APPDATA%\mkw-tracker`; Settings → "Delete all app data…" is the only delete path.
- Local rehearsal loop (copy Task 7's commands in).
- **Post-bridge cleanup checklist** (a literal checklist section — executed manually after v3.0.0 is confirmed migrated):
  1. Download `latest.json` from the v3.0.0 release and commit it as `.github/bridge-latest.json`.
  2. Remove `bundle.windows.nsis` + `createUpdaterArtifacts` from `tauri.conf.json`; delete `src-tauri/installer-hooks.nsh`.
  3. Delete the bridge-gated steps from `release.yml` (`startsWith(github.ref_name, 'v3.0.0')` blocks) once no NSIS stragglers remain.
  4. Delete the `TAURI_SIGNING_PRIVATE_KEY[_PASSWORD]` GitHub secrets.
  5. (Optional) remove `src-tauri/src/bridge.rs` + its commands — self-gating, but dead weight once no NSIS install exists.

- [ ] **Step 2: Verify CLAUDE.md**

Check the Repo Surfaces table row for the desktop app mentions nothing NSIS/updater-specific that is now wrong. (At plan time it doesn't — verify and move on.)

- [ ] **Step 3: Commit**

```bash
git add docs/distribution.md
git commit -m "docs(distribution): velopack update model + post-bridge cleanup checklist"
```

---

### Task 7: Local rehearsals (manual QA gate — run before tagging v3.0.0)

**Files:** none created (throwaway dirs under `temp/`). This task is exercised by Paul or the implementer on a real Windows session, not in CI.

**Interfaces:** Consumes everything. Produces the go/no-go for tagging v3.0.0.

- [ ] **Step 1: Build a local sidecar + app pack**

```powershell
pip install pyinstaller; pyinstaller mkw_tracker.spec --noconfirm
npm run tauri build -- --no-bundle
mkdir temp\vpk\pack\bin
copy src-tauri\target\release\pbenguin.exe temp\vpk\pack\
copy dist\mkw-tracker\mkw-tracker-engine.exe temp\vpk\pack\bin\
xcopy /e /i dist\mkw-tracker\_internal temp\vpk\pack\bin\_internal
```

(If the built exe is `mkw-tracker.exe`, copy it as `pbenguin.exe` — same fallback CI uses.)

- [ ] **Step 2: Pack version A and install it**

```powershell
dotnet tool install -g vpk
vpk pack -u pbenguin -v 2.99.0 -p temp\vpk\pack -e pbenguin.exe -i src-tauri\icons\icon.ico -o temp\vpk\releases
temp\vpk\releases\pbenguin-win-Setup.exe
```

Expected: silent install into `%LocalAppData%\pbenguin`, app launches, tracker runs. No update strip (no newer version in the feed).

- [ ] **Step 3: Pack version B and verify the delta update path**

Rebuild after any trivial code change, re-assemble `temp\vpk\pack`, then:

```powershell
vpk pack -u pbenguin -v 2.99.1 -p temp\vpk\pack -e pbenguin.exe -i src-tauri\icons\icon.ico -o temp\vpk\releases
setx PBENGUIN_UPDATE_PATH "%CD%\temp\vpk\releases"
```

Relaunch the installed app. Expected: strip shows `v2.99.1 …%` → `v2.99.1 ready`; note the delta `.nupkg` size in `temp\vpk\releases` (should be a small fraction of the full). Click **Restart to apply** → app relaunches as 2.99.1.

- [ ] **Step 4: Verify the interruption + apply-on-boot paths**

- Re-pack a `2.99.2`, relaunch, and **quit the app mid-download** (or drop the network). Relaunch: download restarts/completes without redownloading finished packages, strip reaches "ready".
- With the update "ready", **quit without pressing the button**. Relaunch. Expected: `VelopackApp` applies the pending update during boot — app comes up as 2.99.2. This is the headline bug fix; if it doesn't happen, stop and investigate before anything ships.
- `reg query HKCU\Software\Microsoft\Windows\CurrentVersion\Run` (with Start-at-login enabled in Settings) → the entry points at `%LocalAppData%\pbenguin\current\pbenguin.exe`.

Cleanup: `reg delete` nothing — instead toggle Start-at-login off in Settings; `setx PBENGUIN_UPDATE_PATH ""`, uninstall pbenguin from Add/Remove Programs, confirm `%APPDATA%\mkw-tracker` **still exists** afterwards.

- [ ] **Step 5: Bridge rehearsal (against a draft release)**

1. Install the real v2.9.0 NSIS build (from the v2.9.0 GitHub release).
2. Tag `v3.0.0-rc.1` on a branch (or use `workflow_dispatch`) so CI produces a **draft** release with both artifact sets; or hand-upload a locally built set to a draft release.
3. Point the old install at it: the old updater reads `releases/latest/download/latest.json`, which only resolves for published releases — so for the rehearsal, publish the RC as a **pre-release marked latest** temporarily, or edit a copy of v2.9.0's config… simplest honest path: publish the RC fully on a **fork** and install a v2.9.0 build whose endpoint points at the fork. Whichever route: what must be observed is —
   - old app auto-updates to the bridge build and relaunches,
   - strip shows `installer upgrade N%`, app exits,
   - Velopack copy launches by itself within ~30s,
   - `%LocalAppData%\pbenguin` now has the Velopack layout (`Update.exe` +
     `current\pbenguin.exe`) and no `uninstall.exe` — NSIS and Velopack share this same
     per-user directory, so the bridge uninstalls the old tree first rather than
     "moving" anything from Program Files,
   - `%APPDATA%\mkw-tracker` intact, settings/replays present in the new copy,
   - Add/Remove Programs shows exactly one pbenguin entry (the Velopack one).
4. Delete the rehearsal release/tag afterwards.

- [ ] **Step 6: Record results**

Append a dated "rehearsal results" note (delta size observed, any deviations) to `docs/distribution.md`, commit:

```bash
git add docs/distribution.md
git commit -m "docs(distribution): velopack rehearsal results"
```

---

## Self-Review (completed at plan time)

- **Spec coverage:** §1 client stack → Task 1+3; §2 CI → Task 5; §3 bridge (incl. straggler latest.json) → Tasks 2+5; §4 behavior changes (autostart re-assert) → Task 2 Step 4; §5 app-data policy → Task 4 (+ Global Constraints); §6 testing → Task 7. Spec risk items: `--no-bundle` layout → Task 5 Step 1 `ls` guard + Task 7 Step 1; uninstall ordering → Task 2 helper + Task 7 Step 5; delta efficiency → Task 7 Step 3 observation; GithubSource fallback → noted in Task 1 (Velopack falls back to full automatically).
- **Placeholder scan:** one intentional verbatim-reuse marker (Task 5 Step 3 reuses the existing latest.json python step body, which is quoted in full in the current workflow file at lines 254-298) — the source is exact and in-repo, not invented later.
- **Type consistency:** `PendingUpdate` name/state usage consistent across Tasks 1/3; `update-progress` payload is `i16` percent in both `updater.rs` and `bridge.rs`; asset name `pbenguin-win-Setup.exe` identical in Task 2 (`SETUP_ASSET`), Task 5 (vpk output), Task 7.
