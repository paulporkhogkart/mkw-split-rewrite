# Asset Sweep Control Shell — Design (SP1)

**Date:** 2026-06-28
**Status:** Approved design, pre-plan
**Branch context:** `asset-clip-sweep`

## Context

The asset extraction effort has two sequential phases that never run at the same time
(they share the GPU):

1. **Record** — a ~40 h automated 4K60 sweep that captures one clip per
   character×costume (×kart) combo. Built and bring-up-complete; launched today by
   `run_sweep.bat`, which opens **three separate console windows**.
2. **Matte/extract** — a batch GPU job that turns each clip into background-free,
   un-darkened cutout frames (`segment → loopframes → matte → undark`). The *flow* is
   finalised and validated on 12 prototype vehicles; the code is not yet productionised.

Running phase 1 today is "fragmented and janky": three console windows, a separate
OpenCV preview window, a blocking `msvcrt` keyboard pilot, and no graceful
stop/resume. This spec covers a **single-window control shell for phase 1 (the
recording sweep)** — referred to as **SP1**.

The matte/extract runner is **SP2** — a *second mode in the same window*, designed
later once that pipeline is productionised. SP1 is built so SP2 drops into the same
supervisor + log-pane + control plumbing. SP2 is explicitly out of scope here.

### Why a supervisor and not "one app"

The three processes cannot be merged: nxbt requires Linux Bluetooth (WSL2), while
ffmpeg + detection require the Windows capture card. They communicate over TCP
(controller agent, `:7878`) and WebSocket (tracker broadcaster, `:8766`). The shell
is therefore a **supervisor** that spawns and monitors the three as child processes —
it does not absorb them.

## Goals

- One window. No extra console or OpenCV windows spawned.
- The three child stdouts visible as **three split log panes** (all visible at once).
- A **live thumbnail** of the capture feed (no engine overlay) — enough to see what
  character we're on and whether the automation has wandered onto an unexpected screen.
- A **health strip** that surfaces trouble at a glance.
- **Two-step start**, **Pause/Resume**, **Stop**:
  - Pause stops the sweep at the nearest non-destructive point and keeps the
    agent + tracker warm; Resume continues where we left off.
  - Stop tears everything down cleanly; it also runs on window close.
- A **manual controller cluster** (on-screen buttons) for the initial drive to
  character-select and for nudging a wedged run.
- **Minimal resource cost** — this is the hard constraint. The shell must not cause
  dropped 4K60 frames. No second consumer of the capture card; reuse the
  already-decoded frame.

## Non-goals (YAGNI)

- No SP2 matte/extract functionality (separate spec).
- No keyboard bindings for the manual cluster — click only (user's call).
- No remote/web access — a native on-rig window (user doesn't need remote).
- No editing of grid/yaml/config from the shell.
- No persistence of layout/window state beyond what's trivial.

## Architecture

```
                       ┌─────────────────────────────────────────────┐
                       │  Sweep Console (Tkinter, ONE window)         │
                       │                                             │
   spawns + pipes ─────┼─► Agent  (start_agent.py)        stdout ──┐ │
   stdout (no console) ┼─► Tracker(mkw_tracker --clip-     stdout ─┼─┼─► 3 split log panes
                       ┼─►        capture --ws-port 8766           │ │
                       ┼─►        --no-window)                     │ │
                       ┼─► Sweep  (sweep_runner.py)       stdout ──┘ │
                       │                                             │
   read-only WS  ──────┼──ws://127.0.0.1:8766─► thumbnail + state ──►│ thumbnail + health strip
                       │                                             │
   manual presses ─────┼──TCP 127.0.0.1:7878──► controller agent     │ manual cluster
                       └─────────────────────────────────────────────┘
```

- The supervisor is the parent of all three children; each is launched with piped
  stdout/stderr and `CREATE_NO_WINDOW` so no console window appears.
- The supervisor is a **read-only** WS subscriber to the tracker broadcaster
  (`:8766`) for live state and the thumbnail. It never opens the capture device.
- The manual cluster sends presses to the controller agent (`:7878`) via the existing
  `controller_bridge.ControllerBridge`, only while the sweep is not running.

### Integration facts (verified against current code)

- Tracker: `--clip-capture` **requires** `--ws-port`; `--no-window` suppresses the
  `cv2.imshow("MKW Tracker", …)` overlay (built for headless Tauri use). `[clip]` /
  `[stall]` watchdog diagnostics still print to stdout even though `--clip-capture`
  silences the per-frame IPC stdout. So piping the tracker stdout yields the useful
  diagnostics without the JSON flood.
- Broadcaster (`mkw_tracker/ipc/broadcaster.py`): binds `0.0.0.0` (clients must use
  `127.0.0.1`, **not** `localhost` — IPv6 `::1` is refused). Fans every emitted line
  to subscribers; has a thread-safe `broadcast(line)`. Already emits unsolicited
  `selection_update` / `screen_change` / `clip_done` / heartbeat broadcasts and
  answers `at_*` commands. We **add** a `preview` event (below).
- Sweep (`tools/autotemplate/sweep_runner.py`): main loop is
  `for slug, presses in g.sweep_steps("characters"): verify_on; capture_char;
  sweep_karts`. `--start-from <char>` skips earlier characters; per-clip `_exists`
  skips already-recorded clips; `_park_on_kart` is closed-loop (navigates from any
  cursor position). `--pilot` uses blocking `msvcrt` (to be retired).
- Controller bridge: `ControllerBridge(host, port=7878)` exposes
  `press/hold/rstick_down/antispin/get_status/close`. The WSL agent holds R-stick
  down (anti-spin) continuously.
- Clips land in `captures_sdr/en_uk/clips/<item>.mkv`. Grid total = **6,273**
  (153 char×costume combos × (1 char clip + 40 kart clips)).

## Components

Pure-logic units are separated from I/O so they can be unit-tested with fakes
(mirrors the existing `SweepRunner` vs `BridgeController`/`WsClient` split).

| Unit | Responsibility | Interface (used by) | Depends on |
|---|---|---|---|
| `ControlState` | The lifecycle state machine. Pure. | `on_event(evt) -> actions`; `state` | nothing |
| `ProgressModel` | Clip count → done/total/%/rate/ETA. Pure. | `update(done, now) -> dict` | nothing |
| `HealthModel` | Latest WS messages + local probes → health fields. Mostly pure. | `apply(msg)`, `snapshot()` | nothing |
| `ProcessSupervisor` | Spawn/teardown the 3 children; pump each stdout to a sink. | `start_agent/tracker/sweep`, `stop_sweep`, `stop_all`, `on_line(child, cb)` | `subprocess`, threads |
| `WsConsumer` | Read-only `:8766` subscriber; route `preview`→thumbnail, others→`HealthModel`. | `start()`, `on_state(cb)`, `on_preview(cb)` | `websockets`, asyncio thread |
| `ManualController` | Thin wrapper round `ControllerBridge`; connect only when sweep idle. | `press(btn)`, `connect/close`, `available` | `controller_bridge` |
| `ConsoleApp` (Tk) | Widgets + wiring. No business logic. | — | `tkinter`, all above |

Proposed location (script-style, matching the `tools/autotemplate` convention of
`sys.path`-based imports rather than a package):

```
tools/sweep_console/
├── app.py             # entry: build Tk app, wire supervisor + ws + manual + models
├── controlstate.py    # ControlState (pure)
├── progress.py        # ProgressModel (pure)
├── health.py          # HealthModel (pure)
├── supervisor.py      # ProcessSupervisor (I/O)
├── wsconsumer.py      # WsConsumer (I/O)
├── manual.py          # ManualController (I/O)
└── ui.py              # ConsoleApp Tk widgets
run_console.bat        # `python tools\sweep_console\app.py`  (replaces run_sweep.bat)
```

## Lifecycle / control state machine

States: `IDLE → RIG_STARTING → RIG_WARM → SWEEPING ⇄ PAUSED`, plus a transient
`STOPPING → IDLE`.

| Event (button) | From | Action | To |
|---|---|---|---|
| **Start Rig** | IDLE | spawn Agent, await ready; spawn Tracker, await broadcaster; enable manual cluster | RIG_WARM |
| **Begin Sweep** | RIG_WARM | close manual bridge; spawn Sweep `--start-from <resume marker>` if present (else from the start); disable manual | SWEEPING |
| **Pause** | SWEEPING | request graceful stop (below); Sweep exits; agent+tracker stay; enable manual | PAUSED |
| **Resume** | PAUSED | close manual bridge; spawn Sweep `--start-from <current char>`; disable manual | SWEEPING |
| **Stop** | any | request graceful stop of Sweep if running; then kill Tracker, then Agent (incl. WSL) | IDLE |
| window close | any | same as Stop, then exit | — |
| child exited unexpectedly | SWEEPING/WARM | mark unhealthy in the strip; surface in that pane; leave others running | (unchanged) |

Notes:
- **Start Rig** is separate from **Begin Sweep** because you must manually drive to
  character-select (with the cluster) between them. This is inherent, not incidental.
- The manual cluster is enabled **only** in RIG_WARM and PAUSED (sweep not running),
  so it never contends with the sweep's own connection to the agent. The supervisor
  closes its manual `ControllerBridge` before spawning the sweep and reopens it after
  the sweep exits.

## Pause / Resume / Stop semantics

The sweep is given a **`--stop-file <path>`** (a path chosen by the supervisor). It
checks `os.path.exists(stop_file)` at each **inter-clip boundary** — the top of the
character loop *and* the top of each kart iteration inside `sweep_karts`, i.e. only
between clips, never mid-recording. When the flag is present it:

1. lets the in-flight clip finish (we only check between clips, so this is automatic —
   no recording is ever interrupted → non-destructive),
2. returns to a known anchor via the existing `_return_to("CHARACTER_SELECT", …)`
   (presses B until the detector reports CHARACTER_SELECT),
3. exits cleanly (`ctrl.stop(); client.close()`).

The sweep only needs this one "stop gracefully" signal. **Pause vs Stop is the
supervisor's distinction**, decided on its side after the sweep exits:

- **Pause**: keep Agent + Tracker warm; remove the stop-file; re-enable manual. On
  **Resume**, spawn the sweep with `--start-from <current char>`. Because the sweep
  left the Switch on CHARACTER_SELECT, the character loop's nav presses start from the
  right anchor; `_exists` skips the already-recorded character clip and every recorded
  kart in that row (closed-loop `_park_on_kart` re-navigates to the first missing
  kart). Net: resume continues exactly where it left off, at clip granularity.
- **Stop**: additionally terminate Tracker then Agent (and the underlying WSL agent
  process — see teardown), returning to IDLE.

**Current character** for `--start-from` is tracked by the supervisor from the sweep's
stdout (`-- char: <slug> --` lines) and mirrored to a small **resume marker** file
(e.g. `captures_sdr/en_uk/clips/.resume_char`). This is what makes *Start continues
where we left off* true even across a full **Stop + app close**: the next **Begin
Sweep** passes `--start-from <marker>`. Within a warm **Pause → Resume** the in-memory
current char is used (identical effect). In all cases `_exists` does the fine-grained
per-clip skipping, so resume continues at clip granularity and never re-records. The
marker is cleared when the sweep reports full completion.

Pause latency = up to one clip (~15–20 s). Acceptable, and far better than the
character-boundary alternative (~10–13 min per kart row).

## Monitoring layer

### Thumbnail (new `preview` broadcast)

In `main.py`'s `--clip-capture` loop, after `clip_mgr.pump()`, on a **throttle of
~1–2 Hz**: take the current frame, resize to ~320×180, `cv2.imencode('.png')`,
base64, and `broadcaster.broadcast(json({"type":"preview","w":…,"h":…,"data":…}))`.

- Cost: 1–2 small PNG encodes per second — negligible next to 4K60 NVENC. Gated so it
  never runs faster than the throttle and is skipped entirely when there are no WS
  subscribers (the broadcaster already early-returns when `not self._clients`).
- The UI decodes via stdlib `tkinter.PhotoImage(data=<base64 png>)` (Tk 8.6, shipped
  with CPython) — **no Pillow dependency**. (PPM is the fallback if any PNG quirk
  arises: `cv2` → PPM bytes → `PhotoImage(data=…)`.)
- This **replaces** the old `cv2.imshow` overlay window, which we drop simply by
  launching the tracker with the existing `--no-window` flag. Net rendering cost on
  the rig goes *down*.

### Health strip

Fields, refreshed ~1 Hz from WS state + cheap local probes:

| Field | Source |
|---|---|
| controller ✓/✗ + MAC | `ManualController.get_status()` / agent heartbeat, or last `clip_done` liveness |
| current screen | `screen_change` broadcast (e.g. KART_SELECT) — the "unexpected screen" tripwire |
| current char / costume / kart | `selection_update` broadcast |
| clips done / total + % | count `*.mkv` in `captures_sdr/en_uk/clips/` vs 6,273 |
| ETA + rate | `ProgressModel` from clip-count deltas over time |
| last-clip age | seconds since last `clip_done` — the stall tripwire |
| fps + dropped | tracker stall-watchdog stdout if exposed, else last-frame age (best-effort) |
| free disk | `shutil.disk_usage` on the captures drive |

Counting `.mkv` files (rather than parsing logs) makes progress robust and independent
of stdout format.

## Layout (function-over-form)

Single window; **three split log panes** stacked on the right, all visible.

```
┌─ MKW Asset Sweep ───────────────────────────────────────────────┐
│ [Start Rig] [Begin Sweep] [Pause] [Stop]            ● rig warm   │
├──────────────────────────────┬──────────────────────────────────┤
│      live thumbnail          │ controller ✓     screen KART_SEL  │
│        (~320×180)            │ Mario / Touring / Pipe Frame      │
│                              │ clips 3,847/6,273  61%  ETA 14h22 │
│  ── Manual control ──        │ last clip 7s   fps 60/0   412 GB  │
│        ┌───┐                 ├──────────────────────────────────┤
│        │ ▲ │   [A] [B]       │ Agent   ──────────────────────── │
│    ┌───┼───┼───┐ [+] [HOME]  │  …                                │
│    │ ◀ │   │ ▶ │             │ Tracker ──────────────────────── │
│    └───┼───┼───┘             │  …                                │
│        │ ▼ │                 │ Sweep   ──────────────────────── │
│        └───┘                 │  …                                │
└──────────────────────────────┴──────────────────────────────────┘
```

Manual cluster buttons: D-pad (▲▼◀▶), A, B, +, HOME. Each click →
`ManualController.press(<BTN>)`. Greyed out while SWEEPING.

## Code changes to existing files

1. **`mkw_tracker/main.py`** — add the throttled `preview` thumbnail broadcast inside
   the `--clip-capture` loop. (Launching with `--no-window` is a spawn argument, not a
   code change.)
2. **`tools/autotemplate/sweep_runner.py`** —
   - add `--stop-file <path>`; check it at the top of the character loop and at the top
     of each kart iteration in `sweep_karts`; on hit, `_return_to("CHARACTER_SELECT")`
     and exit cleanly;
   - retire/bypass the `--pilot` `msvcrt` path (nav is now UI→agent). Keep `main()`
     able to start directly into the sweep loop given the rig is already positioned.
3. **`tools/autotemplate/start_agent.py`** — ensure clean teardown when the supervisor
   terminates it (propagate termination to the WSL `controller_agent`, e.g. handle
   SIGTERM / close cleanly), so Stop doesn't leave a dangling agent holding Bluetooth.
4. **New** `tools/sweep_console/` package + `run_console.bat` (the bulk of the work).

`run_sweep.bat` is kept during bring-up as a fallback, then removed once the console
is trusted.

## Resource posture (the hard constraint)

- Supervisor: Tkinter idle + ~1–2 Hz updates → negligible CPU, no GPU, ~10–15 MB.
- Removing the `cv2.imshow` overlay window *frees* the rendering it did each frame.
- Thumbnail: 1–2 small PNG encodes/s on the tracker, skipped when no subscriber.
- **No second consumer of the capture card** — the one thing proven to break ffmpeg.
  The UI reads only the already-decoded frame, over WS.
- Net expected effect: a wash-to-slightly-better than today.

## Error handling / edge cases

- **Agent password**: `start_agent.py` reads the WSL sudo password from the keyring
  (configured on this box); if the keyring read fails it falls back to `getpass`, which
  as a piped child with no console would hang. Mitigation: the supervisor watches for
  the agent failing to reach "ready" within a timeout and surfaces a clear message in
  the Agent pane + health strip ("agent didn't come up — check keyring"). We rely on
  the documented working keyring; we do not build interactive password entry in SP1.
- **Bluetooth drop over 40 h**: the agent reconnects (existing behaviour). The health
  strip's controller field flips to ✗ and recovers; last-clip age catches a true stall.
- **ffmpeg stall / dropped frames**: the tracker's existing loop-stall + feed-stall
  watchdogs print to stdout (Tracker pane); last-clip age + fps fields flag it.
- **Agent single-client assumption**: the manual `ControllerBridge` is opened only
  when the sweep isn't running and closed before the sweep spawns, so at most one
  client talks to the agent at a time. (Implementation will read `controller_agent.py`
  to confirm its client model; if multi-client, the handshake still holds.)
- **Child crashes**: the supervisor marks that child unhealthy and surfaces it, but
  does not auto-restart in SP1 (a crashed tracker mid-sweep needs human attention —
  auto-restart could silently corrupt the run). Manual Stop → Start Rig recovers.
- **Stop while mid-clip**: never interrupts a recording (flag checked only between
  clips); the in-flight clip finishes first.

## Testing strategy

- **Pure units** (`ControlState`, `ProgressModel`, `HealthModel`) — unit tests:
  state transitions for every button from every state; ETA/rate math; health field
  derivation from sampled WS messages and edge inputs (no clips yet, stalled, done).
- **`sweep_runner` stop-file** — extend the existing dry-run tests: with a stop-file
  present, the loop returns to CHARACTER_SELECT and exits at the next inter-clip
  boundary; without it, the full traversal is unchanged. Resume path: `--start-from`
  + `_exists` skips recorded clips (already covered) continues correctly.
- **`ProcessSupervisor` / `WsConsumer`** — tested with fake processes/sockets
  (line pumping, teardown ordering, message routing). No real children in unit tests.
- **Manual end-to-end** on the rig during bring-up (the parts that need real hardware:
  thumbnail latency, manual cluster, pause/resume timing, clean teardown of the WSL
  agent). This mirrors how the sweep itself was brought up.

## SP2 seam (not built here)

The supervisor, log panes, control bar, and health strip are phase-agnostic. SP2 adds
a second mode that, instead of Agent+Tracker+Sweep, runs the matte/extract steps
(`segment → loopframes → matte (GPU venv) → undark`) over the recorded clips with its
own progress/GPU view. Known SP2 wiring (banked, not actioned): use the **recurrence**
segmenter from `temp/asset_eyetest/detection_scripts/` (not the stale timestamp-based
`tools/asset_matte/clip_segment.py`); generalise the hardcoded 12-vehicle prototype
lists to the full sweep output; shell out to the GPU venv for the matte step; and
confirm which script produced the validated `matte_all/loopframes/` (a prior-session
artifact — producer not currently in the tree).
