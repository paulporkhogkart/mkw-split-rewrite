# WR Service Plan 3 — Tray + Background Modes: design

**Date:** 2026-07-17
**Status:** approved (design); implementation plan pending
**Parent:** `2026-07-15-pbenguin-wr-service-design.md` §6.1–§6.3 (settings model, idle gate, tray
UX). This addendum resolves that spec's four open decision points (decided by Paul 2026-07-17)
and absorbs the three hardening items named by the fix wave
(`docs/superpowers/plans/2026-07-17-wr-fix-wave.md` final review).

Decisions locked:

| Decision | Choice |
|---|---|
| Autostart architecture | **Tray-only at login** — no window, no webview until asked |
| Idle-gate activity signal | **Any `screen_change` resets the 10-min clock** |
| Close-to-tray + Keep-live-tracking off | Engine stopped on tray-enter (camera released); restore restarts it |
| Tray icons | **Hand-made 16×16 grey + red PNGs**, produced offline (hi-res → downscale), committed for Paul's eyeball |

---

## 1. Process model — the window is on demand, the engine is Rust-owned

The main window leaves `tauri.conf.json`'s static `app.windows` and is created
programmatically:

- **Normal launch** (double-click): `setup()` creates the window immediately — byte-for-byte
  today's UX.
- **Autostart launch**: `tauri-plugin-autostart` registers the Run entry with a `--tray-start`
  argument. `setup()` sees the flag and creates **no window and no webview** — only the tray
  and (if enabled) the WR loop. **The camera cannot be touched by a hidden start by
  construction**: `App.svelte`'s unconditional `onMount → start_tracker` (the trap recorded in
  parent spec §6.3) only ever runs inside a window the user asked for.
- **Open/restore** (tray left-click or menu): create the window if absent, focus it if
  present. Every creation re-runs `onMount`, so live tracking starts on restore exactly as it
  does on launch today (`start_tracker` is already idempotent).
- **Close-to-tray** (checkbox on): intercept `WindowEvent::CloseRequested`, `prevent_close()`,
  then **destroy** the window — the webview is a pure viewer (run uploads flow through
  `sync::on_line` in Rust; the engine's events simply have no listener while trayed), so
  nothing is lost but the on-screen log history, which restarts empty on restore. With the
  checkbox off, close quits, as today.
- **Staying resident (found in review 2026-07-18):** destroying the last window fires
  `RunEvent::ExitRequested`, which unprevented exits the app — so close-to-tray requires a
  guarded `api.prevent_exit()`. The guard distinguishes cases with no extra state:
  last-window-destroy carries `code: None`, a deliberate `app.exit(n)` carries `Some(n)`.
  Prevent only on `None` AND close-to-tray enabled.
- **Quit** (tray menu, or close with the checkbox off): the existing `RunEvent::Exit` sidecar
  teardown runs unchanged (tray Quit = `app.exit(0)` → `Some(0)` → never prevented). If a WR job is in flight: set the loop's cancel flag, join the loop
  thread briefly (≤2 s) so it can `release()` the lease; past that, exit anyway — the lease
  lapse covers an unreleased job (attempt burned, per crash semantics).

**Single instance is now load-bearing, not cosmetic.** With autostart on, a manual launch
would start a SECOND process: two live engines contending for the camera, and two WR workers
sharing one `worker-id` — where process B's `sweep_orphans` glob-delete would remove process
A's actively-downloading `wr-*.mp4` (the exact overlap `PROCESS_LOCK` prevents, which is
per-process only). Add `tauri-plugin-single-instance`: a second launch forwards to the first,
which creates/focuses the window.

## 2. The tray — exists only when something needs it

- **Existence rule:** the tray icon is present iff any of the three global checkboxes
  (Close-to-tray / Start-at-login / Run-WR-service) is on; created/torn down live on settings
  change. All-off (the default) = no tray, closing quits — a user who ignores the feature
  notices no change (parent §6.1's rule).
- **Icons:** two committed assets, `icons/tray-16-idle.png` (grey-shaded pbenguin mark) and
  `icons/tray-16-active.png` (red-shaded), hand-made offline from the 128×128 source
  (hi-res → filtered downscale, then re-tint), for Paul to eyeball at review. Red while a job
  is downloading/processing; grey otherwise (idle, waiting, paused).
- **Tooltip:** `Downloading <course>…` / `Processing <course>…` / `Waiting — tracking active` /
  `Paused` / `Idle`. The parent spec's `Idle — 3 queued` needs a queue-count endpoint the Pi
  doesn't have; **dropped for v1** (future nicety: `GET /v1/wr-jobs/stats`).
- **Menu:** `Pause` / `Resume` (context-sensitive; pausing mid-job = parent §6.5 semantics —
  cancel → release → attempt refunded) · `Open pbenguin` · `Quit`. Left-click = open/focus.

## 3. Settings — four keys, one existing store

- Persisted in the WR service's existing `wr_service.db` KV (`wr/state.rs` `wr_local`),
  read Rust-side at startup before any webview exists: `close_to_tray`, `start_at_login`,
  `run_wr_service`, `keep_tracking_in_tray` (all `"0"`/`"1"`, default `"0"`).
- Two Tauri commands: `wr_get_settings() -> { close_to_tray: bool, start_at_login: bool,
  run_wr_service: bool, keep_tracking_in_tray: bool }` and
  `wr_set_setting(key: String, value: bool)`. Setting `start_at_login` enables/disables the
  autostart plugin (with `--tray-start`); setting `run_wr_service` starts/stops the loop
  thread; any change re-evaluates tray existence.
- **UI:** a "Background" section in `SettingsModal.svelte` with the parent §6.1 checkbox copy
  verbatim (three global + the indented "When in tray: Keep live tracking running").
- **Close-to-tray with Keep-live-tracking off:** tray-enter calls the `stop_tracker` path
  (sidecar killed, camera light off). With it on, the engine keeps running windowless.

## 4. The gate and the loop

- **Signals (Rust, no engine changes):** the engine-stdout forwarder in `lib.rs` (which
  already sees every event line) maintains two shared atomics: `tracking_running` (set on
  sidecar spawn success, cleared on stop/restart kill and on `CommandEvent::Terminated`) and
  `last_screen_change_ts` (updated on any `screen_change` line — cheap substring check before
  parsing; heartbeats and other chatter do NOT reset it, per the decision).
- **Gate predicate (pure, unit-tested):**
  `open = !tracking_running || (now - last_screen_change_ts) ≥ WR_IDLE_MINUTES (10)`.
  The SAME predicate negated is the `cancel` closure handed to `process_one` — one consistent
  rule: any screen change while a job runs closes the gate, cancels the job, releases the
  lease (refund). Starting the live tracker resets `last_screen_change_ts`, so the gate shuts
  the moment tracking starts.
- **Loop:** one dedicated OS thread (house pattern — blocking reqwest; no tokio), started when
  `run_wr_service` is on AND the sync CONFIG has a server URL + token (the loop reads the
  ordinary player token from `sync.rs`'s CONFIG — no second credential store, per Plan 2's
  constraint). Sequence per iteration: gate check → `process_one(cfg, &cancel)` → backoff by
  outcome: `Completed` → next immediately; `Idle` (204) → 60 s doubling to a 5 min ceiling;
  `Error` → 2 min; `Failed` → 30 s; gate closed → re-check every 30 s. Pause (tray) holds the
  loop; Resume releases it. Toggling `run_wr_service` off mid-job = pause semantics
  (cancel → release), then the thread parks.
- `wr_process_one` (the dev probe) stays, unchanged — documented, harmless, useful.

## 5. Hardening (the fix wave's three named items ship here)

1. **Heartbeat, finally wired:** while a job is in flight, a scoped thread calls
   `job::Client::heartbeat(wr_id)` every 120 s (lease 600 s). Kills the tight-margin coupling
   between `engine_timeout_for`'s 540 s cap and the lease for good; a `false` heartbeat result
   (lease lost) sets the job's cancel flag — stop working for a job someone else now owns.
2. **yt-dlp bounded:** `ytdlp::download` moves from `.output()` to spawn + the same
   watchdog-kill pattern `run_video` uses (cap ~240 s; cancel-aware, so a pause no longer
   waits out a full download). Proven by a wedged-child test (a fake exe that sleeps),
   mirroring the engine watchdog's test style.
3. **Pi sweep alert for silent-crash deaths:** additive `wr_jobs.alerted_at TEXT` column
   (connect.ts ALTER list). Both publish sites set it: the `/result` route (existing path) and
   a new sweep on the scraper tick that publishes `wr_job_dead` for `deadJobs()` rows with
   `alerted_at IS NULL`. Closes the final-review's deferred Important: a job whose attempts
   burn via crash + lease lapse now alerts within one scrape interval. Once-only semantics
   tested; a revived-then-re-dead job re-alerts (revival clears `alerted_at` alongside
   `last_error`).

## 6. Failure modes and edges

- **Two instances:** solved structurally (single-instance plugin, §1).
- **Settings flips mid-job:** WR-service off / Pause / quit → cancel + release (refund);
  close-to-tray with Keep-live-tracking off stops only the LIVE engine (the WR job's own
  engine is a separate child and continues; the gate actually opens wider once tracking
  stops).
- **Loop crash containment:** the loop thread wraps `process_one` so a panic in one job logs,
  backs off 5 min, and continues (the existing `PROCESS_LOCK` poison-recovery already covers
  the lock).
- **Trayed with no token configured:** loop stays parked; tooltip `Idle`; no claims attempted.

## 7. Testing

- **Pure/unit (Rust):** gate predicate truth table (running/stopped × fresh/stale activity);
  backoff schedule per outcome; settings round-trip through `wr_local`; tooltip/menu state
  mapping; heartbeat cadence decision function.
- **Process (Rust):** wedged yt-dlp watchdog test (bounded, cancel-aware).
- **Pi (vitest):** sweep alerts once per death (`alerted_at`), route+sweep never double-alert,
  revival re-arms alerting.
- **Manual smoke checklist (Paul's eyeball — window lifecycle is not honestly
  unit-testable):** autostart → tray only, camera light NEVER on; open from tray → window +
  tracking start; close-to-tray with Keep-live-tracking off → camera light OFF; …with it on →
  tracking continues, run upload still works trayed; Pause/Resume mid-job; Quit mid-job →
  clean exit, lease releases or lapses; second manual launch while trayed → focuses, no second
  process; all-checkboxes-off → app behaves exactly as today, no tray.

## 8. Out of scope

- Plan 4 (client WR-dot display) — unchanged, next.
- Queue-count in the tooltip (needs a Pi stats endpoint).
- WS `wr_update` wake nudge (polling with backoff is enough at this cadence).
- The Sky-High Sundae engine seed-row migration (separate decision; would allow deleting
  `course_display_for_engine`'s exception — see that function's doc).
