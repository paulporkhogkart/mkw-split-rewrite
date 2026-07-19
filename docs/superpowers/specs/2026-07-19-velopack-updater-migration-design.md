# pbenguin auto-updater migration to Velopack — design

**Date:** 2026-07-19
**Status:** Approved (design review with Paul, 2026-07-19)

## Problem

The current updater is the stock Tauri v2 plugin (`@tauri-apps/plugin-updater`) wired
minimally in `src/App.svelte` (`checkForUpdate` / `applyUpdate`):

- Every update downloads the **full ~120 MB NSIS installer**, even when the release
  changed a few hundred KB of code. ~105 MB of that is the PyInstaller sidecar
  (`_internal/` — OpenCV, numpy, ffmpeg) which rarely changes between releases.
- The downloaded bytes live only in memory in the `pendingUpdate` object. Closing the
  app, a dropped connection, or restarting manually instead of pressing **Restart to
  apply** discards the whole download. There is no resume, no on-disk cache, and no
  apply-on-next-boot. The plugin simply has none of these features (no delta support
  either — confirmed against upstream docs/issues, July 2026).

## Goals

1. **Delta updates** — a code-only release downloads a few MB, not 120.
2. **Persistent, interruption-safe downloads** — an interrupted download resumes /
   is not thrown away.
3. **Apply on any restart** — a downloaded-but-unapplied update installs on the next
   boot, whether or not the user pressed the button.
4. Keep GitHub Releases as the only distribution channel; keep the existing title-bar
   update-strip UX; keep the shared-tag Pi deploy flow and the pbenguin
   change-detection gate in CI untouched.

## Non-goals

- No change to what a "release" is (still a version tag driving both pbenguin CI and
  Pi deploy).
- No Authenticode code signing (wasn't signed before; unchanged posture).
- No install-wizard UX (accepted: Velopack installs silently).

## Chosen approach

Migrate packaging + updating to **Velopack** (velopack.io): NSIS bundling is replaced
by `vpk pack`; `tauri-plugin-updater` is replaced by the `velopack` Rust crate.
Velopack natively provides delta packages, a persistent local packages directory,
apply-on-restart semantics, and a `GithubSource` in the Rust crate
(`velopack::sources`) so GitHub Releases remains the feed.

Alternatives considered and rejected:
- **Keep Tauri updater, add disk cache + Range-resume ourselves** — fixes
  interruption pain but every update stays ~120 MB.
- **Hand-rolled component split** (shell installer + `chips.lock`-style per-file
  engine manifest) — biggest bandwidth win but the most bespoke machinery to own;
  moves the engine out of the install dir and touches sidecar spawning.

## Design

### 1. Client update stack (Rust + Svelte)

- `VelopackApp::build().run()` becomes the **first statement of `main()`**
  (src-tauri/src/main.rs), before Tauri init — it handles the
  `--veloapp-install/-updated/-obsolete/-uninstall` hook invocations and fast-exits.
- New `src-tauri/src/updater.rs`: an `UpdateManager` over
  `sources::GithubSource` pointed at `paulporkhogkart/mkw-split-rewrite`, exposed as
  three Tauri commands replacing the JS plugin calls:
  - `update_check` → `Option<{ version }>`
  - `update_download` → streams progress events (`update-progress`: percent) to the
    webview; Velopack downloads **deltas when available and falls back to the full
    package automatically**; packages persist in Velopack's local packages dir.
  - `update_apply` → `stop_tracker` semantics preserved (engine killed first, as
    today), then `apply_updates_and_restart`.
- **Apply-on-boot:** at startup, before the tracker spawns (including `--tray-start`
  boots), check `update_pending_restart()`. If a downloaded update is pending, apply
  and relaunch immediately. This makes the button a convenience, not the only path.
- `src/App.svelte`: `checkForUpdate`/`applyUpdate` switch from
  `@tauri-apps/plugin-updater` to `invoke()` + an `update-progress` listener. The
  title-bar update strip (label, %, "Restart to apply") is unchanged.
- Removals: `tauri-plugin-updater` from `Cargo.toml`; `plugins.updater` config +
  `createUpdaterArtifacts` from `tauri.conf.json`; updater permissions from
  `capabilities/default.json`; `@tauri-apps/plugin-updater` from `package.json`.
  The minisign signing key (GitHub secrets `TAURI_SIGNING_PRIVATE_KEY[_PASSWORD]`)
  is retired after the bridge release ships — Velopack hash-verifies packages
  against the feed over HTTPS.

### 2. Packaging & CI (`.github/workflows/release.yml`)

`build-tauri` job changes:

1. `tauri build --no-bundle` (compile exe + frontend only).
2. Assemble the app directory by hand, preserving today's resource layout relative
   to the exe: `pbenguin.exe`, `sidecar/mkw-tracker-engine.exe`,
   `sidecar/_internal/`, plus whatever `--no-bundle` leaves as required runtime
   files (verified during implementation against the current resource map).
3. `vpk download github` — fetches the previous release as the delta baseline.
4. `vpk pack` — emits `Setup.exe` (renamed `pbenguin-<ver>-setup.exe`), the full
   `.nupkg`, the **delta `.nupkg`**, and `releases.win.json`.
5. Upload all of it as release assets (same `softprops/action-gh-release` publish
   job).

Unchanged: the `changes` gate (server/site-only tags still skip pbenguin), the
python sidecar build job, the portable-zip artifact, tag-driven Pi deploy.

### 3. One-time bridge migration (v3.0.0)

Existing installs are NSIS, installed per-user to `%LocalAppData%\pbenguin` (Tauri's
NSIS default) — the SAME directory Velopack targets, not Program Files; they migrate
**automatically**:

- The bridge release publishes **both artifact sets**: the old-style NSIS installer
  + `latest.json` (so every existing v2.x install auto-updates into the bridge via
  the old updater) *and* the full Velopack set.
- On startup the app detects its install mode via the Velopack locator (a Velopack
  install has `Update.exe` above the app dir; locator fails on an NSIS install).
  When NSIS-installed, a migration module: downloads the same-version Velopack
  `Setup.exe` from the release → spawns a detached helper that waits for this process
  to exit, then runs the NSIS uninstaller silently (`uninstall.exe /S` — silent mode
  never deletes app data; the delete-app-data hook requires the interactive checkbox),
  then polls until the (shared) install directory is actually gone, then runs
  `Setup.exe --silent` (installs per-user to `%LocalAppData%`, launches the new copy)
  → exits. Uninstall runs **before** Setup because both land in the same directory —
  Setup must never write into a tree the old install still occupies.
- **Straggler safety net:** every future release re-uploads a pinned static
  `latest.json` (committed at `.github/bridge-latest.json`, pointing at the bridge's
  NSIS installer asset in the v3.0.0 release). Old clients resolve
  `releases/latest/download/latest.json`, so an install that misses the bridge
  window still funnels through it later.
- `installer-hooks.nsh` and the NSIS config in `tauri.conf.json` are kept **only**
  for building the bridge's NSIS artifact, then removed in the following release.

### 4. Behavior changes (accepted in review)

- Install stays per-user at `%LocalAppData%\pbenguin` (no UAC) — both NSIS (Tauri's
  default) and Velopack use this same directory; there is no Program Files involved
  and nothing "moves". Velopack recreates Start Menu/desktop shortcuts. Tray autostart survives: the
  Run key is (re)registered from `current_exe()` via `tauri-plugin-autostart`, and
  Velopack's `current/` exe path is stable across updates.
- Install/uninstall run silently — the NSIS wizard art and prompts go away.
- WebView2 bootstrapper download dropped (inbox on Win10/11; covers the user base).

### 5. App-data / uninstall policy (decided 2026-07-19)

- **Updates never touch `%APPDATA%\mkw-tracker`** — structurally guaranteed:
  a Velopack update swaps the app folder only; no uninstall pass runs.
- **Uninstall keeps app data.** Velopack uninstall is silent (no checkbox UI).
  Velopack's uninstall callback *could* delete app data but only unconditionally —
  rejected as unsafe.
- **Replacement for the old checkbox:** a "Delete all app data…" button (with
  confirm dialog) in the Settings modal, which wipes `%APPDATA%\mkw-tracker` and
  quits. Functionally the old opt-in, relocated somewhere that can ask first.

### 6. Testing

- **Local delta loop:** `vpk pack` two consecutive local versions into a directory;
  install A; point the app at a `FileSource`; verify: delta download → apply →
  relaunch; and the kill-app-mid-flow path (pending package applies on next boot).
- **Bridge rehearsal:** install the real v2.9.0 NSIS build; point it at a draft
  GitHub release carrying the bridge; watch the full NSIS → Velopack handover,
  including silent NSIS uninstall and app-data survival.
- **CI dry-run** on a prerelease tag before tagging v3.0.0.

## Risks / verification items for the plan

- **Delta efficiency over the PyInstaller `_internal` tree** — expected small for
  code-only releases (unchanged DLLs diff to ~nothing), but the first release pair
  confirms. Worst case Velopack falls back to a full download — today's size with
  persistence, still a net win.
- **Exact `--no-bundle` output layout** vs the resource map (`sidecar\...` keys) —
  verify the assembled folder runs before packing.
- **NSIS uninstaller invocation from the bridge** (exact uninstaller filename/flags,
  and ordering so the uninstaller doesn't kill the new Velopack copy) — verify in a
  VM rehearsal.
- **GithubSource multi-version-behind behavior** — clients several versions behind
  may lack an unbroken delta chain; Velopack's full-package fallback covers it.
- **Version scheme:** bridge is **v3.0.0**; Velopack requires semver (satisfied).
