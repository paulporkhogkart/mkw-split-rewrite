# Distribution & Auto-Update Architecture

## Overview

The distribution model treats **everything as one release artifact**: the Tauri UI wraps the
Python sidecar into a single Velopack package. Velopack manages install, per-user updates
(delta-first), and uninstall; there is no NSIS installer and no `tauri-plugin-updater` in the
normal (post-bridge) flow.

```
GitHub Release (v3.1.0)
├── mkw-tracker-v3.1.0-windows-x64.zip   ← portable engine bundle (internal name; testing)
├── pbenguin-win-Setup.exe               ← Velopack bootstrapper (first install)
├── pbenguin-3.1.0-full.nupkg            ← Velopack full package
├── pbenguin-3.1.0-delta.nupkg           ← Velopack delta package (vs previous release)
├── releases.win.json                    ← Velopack release feed index
└── assets.win.json                      ← Velopack vpk build artifact (may accompany release; not consumed by updater)
```

The one-time **v3.0.0 bridge** release additionally ships the legacy NSIS installer + `latest.json`
so pre-Velopack installs can migrate — see [Bridge](#bridge-v300-one-time-nsis--velopack-handover)
below. Every later tag ships only the Velopack asset set above.

> **Naming split (intentional).** The user-facing app is **pbenguin** (`productName`):
> the installer, Start Menu entry, Add/Remove Programs, and `pbenguin.exe` all show it.
> The Tauri `identifier` (`com.paulporkhogkart.mkw-tracker`), the bundled Python engine
> (`mkw-tracker-engine.exe`), and the data folder (`%APPDATA%/mkw-tracker/`) keep the
> original internal name **on purpose** — that preserves the updater's app identity (so
> existing installs upgrade in place, not side-by-side) and the user's settings/replays
> across the rename. Only `productName` changed. Velopack's package id (`-u pbenguin`)
> is also `pbenguin`, matching `productName`, not the internal identifier.

---

## Components

### Python sidecar (`mkw_tracker.spec`)

PyInstaller `--onedir` compiles the tracker into a standalone folder:

```
dist/mkw-tracker/
├── mkw-tracker-engine.exe   ← entry point
└── _internal/
    ├── images/              ← all template PNGs (bundled via spec datas=[])
    ├── cv2/
    ├── numpy/
    └── ...DLLs...
```

`resource_path()` in `mkw_tracker/utils/paths.py` resolves `images/…` paths from
`sys._MEIPASS` when frozen, and from the repo root in development — no code changes
needed anywhere else when switching between modes.

`data_dir()` points `mkw_tracker.db` to `%APPDATA%/mkw-tracker/` when frozen so
the database persists across updates. In development it stays at the repo root.

### Tauri sidecar integration

The engine is **not** launched through Tauri's shell-plugin `Command::sidecar()` /
`externalBin` mechanism. Instead, `tauri.conf.json`'s `bundle.resources` copies the built
sidecar straight into the install tree, relative to the app exe:

```json
{
  "bundle": {
    "resources": {
      "sidecar/mkw-tracker-engine.exe": "bin/mkw-tracker-engine.exe",
      "sidecar/_internal": "bin/_internal"
    }
  }
}
```

`src-tauri/src/lib.rs`'s `do_spawn_sidecar` handles two modes:

- **Debug builds** (`cargo tauri dev`): spawns `python -m mkw_tracker` directly from the
  repository root, skipping the bundled engine entirely.
- **Release builds**: resolves the current exe's directory (`std::env::current_exe()`) and
  spawns `bin/mkw-tracker-engine.exe` directly as a plain child process relative to wherever
  `pbenguin.exe` is running from (e.g. `…\current\bin\mkw-tracker-engine.exe` under Velopack).

Both modes pipe stdio for the existing IPC protocol and have no dependency on Tauri's sidecar
path resolution.

---

## Install layout (Velopack)

Per-user, no UAC prompt:

```
%LocalAppData%\pbenguin\
├── Update.exe              ← Velopack's updater/bootstrapper, lives ABOVE current\
└── current\
    ├── pbenguin.exe
    └── bin\
        ├── mkw-tracker-engine.exe
        └── _internal\...
```

Each applied update swaps the contents of `current\` (via `Update.exe`); the running app
exits, control passes to `Update.exe`, and the new version relaunches. There is no
separate "installer" step for updates — only for the very first install (`Setup.exe`).

---

## Auto-Update Flow

### `src-tauri/src/updater.rs`

Replaces `tauri-plugin-updater` entirely (no longer a dependency; the plugin registration,
`plugins.updater` config block, and `updater:default` capability are all gone). Built on the
`velopack` crate:

- `update_check()` — constructs a `velopack::UpdateManager` against a `Feed` and calls
  `check_for_updates()`. `Feed` resolution (`resolve_feed`):
  - `PBENGUIN_UPDATE_PATH` env var set to a non-blank path → `Feed::Dir` → a
    `sources::FileSource` pointed at a local `vpk pack` output directory. **This is only
    ever set by the local rehearsal loop** (see below) — normal installs never set it.
  - Otherwise → `Feed::Github` → `sources::GithubSource` against
    `https://github.com/paulporkhogkart/mkw-split-rewrite`.
  - In dev/portable runs, `UpdateManager::new` itself fails (`NotInstalled`, no Velopack
    install marker present) and every command degrades to "no update" rather than erroring.
  - On finding an update, stashes the `UpdateInfo` in `PendingUpdate` (Tauri-managed state)
    and returns the version string to the frontend.
- `update_download()` — downloads via `UpdateManager::download_updates`, **delta-first**:
  Velopack tries the delta `.nupkg` against the installed version first and only falls back
  to the full package if no usable delta exists (e.g. first-ever update, or too many versions
  behind). Downloads land in Velopack's own **persistent packages directory** — not a temp
  file — so an interrupted download resumes/completes without re-fetching finished packages
  on the next check. Progress streams to the frontend as `update-progress` events (`i16`
  percent, 0–100).
- `update_apply()` — calls `apply_updates_and_restart`; does not return on success (the
  process exits, `Update.exe` swaps `current\`, the new version relaunches). The frontend
  calls `stop_tracker` first so the engine sidecar is already down.

### Apply: button or automatic on next boot

`src-tauri/src/main.rs` runs `velopack::VelopackApp::build().run()` as the very first line of
`main()`, before `mkw_tracker_lib::run()`. This is what makes apply-on-next-boot work even if
the user never clicks anything: if a downloaded update is sitting in the packages directory
unapplied, `VelopackApp`'s boot hook applies it right there, before the rest of the app even
starts. This is the **headline behavior change** from the old NSIS/Tauri-updater flow (which
only updated when the user acted on the prompt) — quitting the app is now itself enough to
get onto the new version by the next launch, on top of the in-app **Restart to apply** button
(`src/App.svelte`) that applies immediately without waiting for the next boot.

The title-bar update strip (`src/App.svelte`) shows, in order of state:
`v{version} …%` (downloading) → `v{version} ready` (downloaded, "Restart to apply" button
shown) → nothing (no update, or applied). A separate `migrating` state ("installer upgrade
N%") is reserved for the one-time bridge handover, below.

---

## App-data policy (spec 2026-07-19 §5)

**Updates and uninstalls never touch app data.** Velopack applying an update only replaces
`current\`; it has no concept of `%APPDATA%` and never deletes anything there. Uninstalling
(from Add/Remove Programs, or the bridge's silent `uninstall.exe /S`) removes the install
directory only.

The **only** delete path is the explicit Settings → **Delete all app data…** button
(`delete_app_data` in `src-tauri/src/lib.rs`), which is the in-app replacement for the old
NSIS uninstaller's "delete app data" checkbox. It deletes **both** app-owned data roots:

1. `%APPDATA%\mkw-tracker` — the engine's data dir (`mkw_tracker.db`: config, replays,
   minimap seeds/ROIs/thresholds).
2. `%APPDATA%\com.paulporkhogkart.mkw-tracker` — the Tauri identifier dir (`wr_service.db`
   background-service settings, the sync outbox DB, the WR video cache).

Sequence: stop the WR runner → kill the engine sidecar → `sync::shutdown()` (closes the sync
outbox's sqlite connection so its file handle releases before deletion) → delete both roots,
each retried up to 5× at 300ms intervals (`remove_dir_all_retrying`) to ride out transient
Windows share-locks from a just-killed process/just-closed handle → on full success, exit the
app; on any failure, return the error to the frontend, which shows a native error dialog (no
exit, so the user isn't left in a half-torn-down state without knowing why). The Tauri
webview's own profile directory is **not** deleted — it's locked open by the running process
itself and isn't part of either data root anyway.

---

## Bridge (v3.0.0: one-time NSIS → Velopack handover)

`src-tauri/src/bridge.rs` exists only to move existing NSIS installs onto Velopack, exactly
once, on the v3.0.0 release. It self-gates: `bridge_check()` returns `true` only when the
running exe sits in an NSIS install layout (an `uninstall.exe` beside the app exe, and **no**
`Update.exe` one directory up — a Velopack marker always wins, so a Velopack install can
never be mis-detected as NSIS). Every install after the bridge, this is permanently `false`.

Sequence when `bridge_migrate()` runs:

1. Download `pbenguin-win-Setup.exe` (same version) from the GitHub release, streaming
   progress over the same `update-progress` event the normal updater uses.
2. Spawn a detached, hidden PowerShell helper that: waits for this process to exit, then
   runs `Setup.exe --silent` (installs to `%LocalAppData%` and launches the new copy), then
   runs the old install's `uninstall.exe /S`. Order is load-bearing — our process must be
   gone before Setup launches the new app (single-instance plugin would otherwise refuse a
   second launch), and the uninstall runs last so it's never fighting a live exe for file
   locks.
3. This process exits with code `0` (bypasses the close-to-tray `prevent_exit` guard;
   `RunEvent::Exit` teardown kills the sidecar + WR runner normally).

A silent NSIS uninstall never shows the delete-app-data checkbox, so `%APPDATA%\mkw-tracker`
survives untouched — the new Velopack copy picks up the same settings/replays.

---

## Releasing a New Version

### 1. Bump the version

```bash
python scripts/set_version.py patch   # or minor / major / an explicit x.y.z
```

Rewrites `package.json`, `pyproject.toml`, `src-tauri/Cargo.toml`, and
`src-tauri/tauri.conf.json` together, then syncs `Cargo.lock` and `package-lock.json`. These
six files are recognized by `.github/workflows/release.yml`'s change-detection job as
version-bump-only — a tag that touches nothing else skips the pbenguin build entirely (it
still ships the Pi/site/bot changes a shared version tag may also carry).

### 2. Commit, tag, and push

```bash
git add package.json package-lock.json pyproject.toml src-tauri/Cargo.toml src-tauri/Cargo.lock src-tauri/tauri.conf.json
git commit -m "bump to v0.2.0"
git tag v0.2.0
git push origin main --tags
```

### 3. CI takes over (`.github/workflows/release.yml`)

- `changes` — skips the desktop build/release entirely if the tag's diff is only
  Pi/web/server/deploy files, `*.md` docs, or the six version-bump files above.
- `build-python` — PyInstaller portable bundle (unchanged from pre-Tauri).
- `build-tauri` — `npm run tauri build -- --no-bundle`, assembles a Velopack `pack/`
  directory (`pbenguin.exe` + `bin/` sidecar), installs `vpk`, verifies it (`vpk --version`
  fail-fast right after install), downloads the previous release as a delta baseline
  (`vpk download github --token`, `continue-on-error: true` — the first-ever Velopack
  release has nothing to diff against), then `vpk pack -u pbenguin -v <version> …`. A
  **"Normalize Setup asset name"** step follows immediately: `vpk pack`'s own default Setup
  filename is `{packId}-Setup.exe` (i.e. `pbenguin-Setup.exe`, no `-win-` infix) — this step
  renames it to the canonical `pbenguin-win-Setup.exe` that `bridge.rs`'s `SETUP_ASSET`
  constant and the frontend both expect, hard-failing if the glob doesn't find exactly one
  Setup exe (so an unexpected `vpk` output shape breaks the build loudly rather than
  silently shipping the wrong asset name). Only on the `v3.0.0` tag
  (`startsWith(github.ref_name, 'v3.0.0')`): also builds the legacy NSIS installer, signs it,
  and generates the old-style `latest.json`.
- `publish` — creates the GitHub Release with the Velopack asset set (+ the NSIS installer
  and `latest.json` only on `v3.0.0`), plus a "Pin bridge latest.json for stragglers" step
  that re-publishes the committed `.github/bridge-latest.json` on every *non*-bridge release
  so `releases/latest/download/latest.json` keeps resolving for any NSIS install that hasn't
  migrated yet. A final guard step fails the job if the Velopack assets
  (`*.nupkg`, `releases.win.json`, `pbenguin-win-Setup.exe`) are missing from the release.

**Known triage item:** `vpk` is installed via `dotnet tool install -g vpk` with no version
pin — this is a deliberate loose end, not a design choice to hold up as safe. A future `vpk`
release that changes CLI flags or output naming would surface as a CI failure (the
`--version` check, or the Setup-name normalize step's own assertion), not silently, but it
is still worth pinning at some point.

### 4. Users receive the update

The app checks GitHub on its own schedule/trigger, downloads the delta package in the
background, and — per the flow above — applies it either when the user clicks **Restart to
apply** or automatically the next time the app starts, whichever comes first.

---

## Release asset table

| Asset | Produced by | Ships on |
|---|---|---|
| `mkw-tracker-v<ver>-windows-x64.zip` | `build-python` (portable engine bundle; internal name, for standalone testing) | every release |
| `pbenguin-win-Setup.exe` | `vpk pack`, renamed by the "Normalize Setup asset name" CI step (raw `vpk` default is `pbenguin-Setup.exe`) | every release |
| `pbenguin-<ver>-full.nupkg` | `vpk pack` | every release |
| `pbenguin-<ver>-delta.nupkg` | `vpk pack` (present once a previous release exists as a delta baseline) | every release after the first |
| `releases.win.json` | `vpk pack` | every release |
| `assets.win.json` | `vpk pack` (build artifact; not consumed by updater or bridge) | may accompany release |
| `*x64-setup.exe` + `.sig` (legacy Tauri NSIS installer) | bridge-only `npm run tauri build -- --bundles nsis`, signed with `TAURI_SIGNING_PRIVATE_KEY[_PASSWORD]` | **`v3.0.0` only** |
| `latest.json` (legacy Tauri-updater manifest) | bridge-only Python step | **`v3.0.0` only**, then re-published verbatim (as `.github/bridge-latest.json`) on every later release until the [post-bridge cleanup](#post-bridge-cleanup-checklist) removes that step |

---

## Local rehearsal loop

> **STATUS: NOT YET RUN.** Everything in this section is the manual QA checklist to execute
> before tagging `v3.0.0` (reproduced here from the implementation plan's Task 7) — it is a
> plan of commands to run on a real Windows session, not a report of results observed. A
> previous status report mis-cited this section as already-executed results; it was not, and
> nothing below should be read as evidence the flow works until someone actually runs it and
> the results are appended (see Step 6).

### Step 1: Build a local sidecar + app pack

```powershell
pip install pyinstaller; pyinstaller mkw_tracker.spec --noconfirm
npm run tauri build -- --no-bundle
mkdir temp\vpk\pack\bin
copy src-tauri\target\release\pbenguin.exe temp\vpk\pack\
copy dist\mkw-tracker\mkw-tracker-engine.exe temp\vpk\pack\bin\
xcopy /e /i dist\mkw-tracker\_internal temp\vpk\pack\bin\_internal
```

(If the built exe is `mkw-tracker.exe`, copy it as `pbenguin.exe` — same fallback CI uses.)

Note: a **local** `vpk pack` run here (unlike CI) is not followed by a rename step, so it
produces the un-normalized `pbenguin-Setup.exe`, not `pbenguin-win-Setup.exe`. That's harmless
for this rehearsal loop specifically: `temp\vpk\releases\pbenguin-Setup.exe` is run directly
by filename below (Step 2), and the update-check path uses `PBENGUIN_UPDATE_PATH` to point at
the whole output directory via `FileSource` (Step 3) — neither depends on the exact Setup
filename. Only the CI/GitHub path (`bridge.rs`'s hardcoded `SETUP_ASSET` download, and the
real updater's `GithubSource`) needs the exact `pbenguin-win-Setup.exe` name, and that's what
CI's normalize step guarantees.

### Step 2: Pack version A and install it

```powershell
dotnet tool install -g vpk
vpk pack -u pbenguin -v 2.99.0 -p temp\vpk\pack -e pbenguin.exe -i src-tauri\icons\icon.ico -o temp\vpk\releases
temp\vpk\releases\pbenguin-Setup.exe
```

Expected: silent install into `%LocalAppData%\pbenguin`, app launches, tracker runs. No update
strip (no newer version in the feed).

### Step 3: Pack version B and verify the delta update path

Rebuild after any trivial code change, re-assemble `temp\vpk\pack`, then:

```powershell
vpk pack -u pbenguin -v 2.99.1 -p temp\vpk\pack -e pbenguin.exe -i src-tauri\icons\icon.ico -o temp\vpk\releases
setx PBENGUIN_UPDATE_PATH "%CD%\temp\vpk\releases"
```

Relaunch the installed app. Expected: strip shows `v2.99.1 …%` → `v2.99.1 ready`; note the
delta `.nupkg` size in `temp\vpk\releases` (should be a small fraction of the full). Click
**Restart to apply** → app relaunches as 2.99.1.

### Step 4: Verify the interruption + apply-on-boot paths

- Re-pack a `2.99.2`, relaunch, and **quit the app mid-download** (or drop the network).
  Relaunch: download restarts/completes without redownloading finished packages, strip
  reaches "ready".
- With the update "ready", **quit without pressing the button**. Relaunch. Expected:
  `VelopackApp` applies the pending update during boot — app comes up as 2.99.2. This is the
  headline bug fix; if it doesn't happen, stop and investigate before anything ships.
- `reg query HKCU\Software\Microsoft\Windows\CurrentVersion\Run` (with Start-at-login enabled
  in Settings) → the entry points at `%LocalAppData%\pbenguin\current\pbenguin.exe`.

Cleanup: `reg delete` nothing — instead toggle Start-at-login off in Settings; `setx
PBENGUIN_UPDATE_PATH ""`, uninstall pbenguin from Add/Remove Programs, confirm
`%APPDATA%\mkw-tracker` **still exists** afterwards.

### Step 5: Bridge rehearsal (against a draft release)

1. Install the real v2.9.0 NSIS build (from the v2.9.0 GitHub release).
2. Tag `v3.0.0-rc.1` on a branch (or use `workflow_dispatch`) so CI produces a **draft**
   release with both artifact sets; or hand-upload a locally built set to a draft release.
3. Point the old install at it: the old updater reads `releases/latest/download/latest.json`,
   which only resolves for published releases — so for the rehearsal, publish the RC as a
   **pre-release marked latest** temporarily, or edit a copy of v2.9.0's config… simplest
   honest path: publish the RC fully on a **fork** and install a v2.9.0 build whose endpoint
   points at the fork. Whichever route: what must be observed is —
   - old app auto-updates to the bridge build and relaunches,
   - strip shows `installer upgrade N%`, app exits,
   - Velopack copy launches by itself within ~30s,
   - `C:\Program Files\pbenguin` is gone (silent NSIS uninstall ran),
   - `%APPDATA%\mkw-tracker` intact, settings/replays present in the new copy,
   - Add/Remove Programs shows exactly one pbenguin entry (the Velopack one).
4. Delete the rehearsal release/tag afterwards.

### Step 6: Record results

Append a dated "rehearsal results" note (delta size observed, any deviations) below this
section, and commit:

```bash
git add docs/distribution.md
git commit -m "docs(distribution): velopack rehearsal results"
```

---

## Post-bridge cleanup checklist

Execute manually, once, after `v3.0.0` is confirmed to have migrated the active install base
(no meaningful trickle of old-updater check-ins left):

1. Download `latest.json` from the `v3.0.0` release and commit it as
   `.github/bridge-latest.json` (if not already committed from CI's own copy).
2. Remove `bundle.windows.nsis` (the `"nsis"` bundle target, `windows.nsis` config block) and
   `createUpdaterArtifacts` from `src-tauri/tauri.conf.json`; delete
   `src-tauri/installer-hooks.nsh`.
3. Delete the bridge-gated steps from `.github/workflows/release.yml` (every step under
   `if: startsWith(github.ref_name, 'v3.0.0')` in the `build-tauri` job, plus the
   `tauri-installer`/`update-manifest` downloads and the "Pin bridge latest.json for
   stragglers" step in `publish`) once no NSIS stragglers remain.
4. Delete the `TAURI_SIGNING_PRIVATE_KEY` / `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` GitHub
   secrets.
5. (Optional) remove `src-tauri/src/bridge.rs` and its two commands
   (`bridge_check`, `bridge_migrate`) — self-gating (always returns `false` post-bridge), but
   dead weight once no NSIS install can possibly exist anymore.

---

## Development Workflow (no changes needed)

```bash
python -m mkw_tracker            # still works — resource_path() falls back to repo root
```

`mkw_tracker.db` is still created at the repo root in dev mode.

---

## Local Build Test

```bash
pip install pyinstaller
pyinstaller mkw_tracker.spec --noconfirm
# Test the bundle:
dist/mkw-tracker/mkw-tracker-engine.exe --no-ipc
```
