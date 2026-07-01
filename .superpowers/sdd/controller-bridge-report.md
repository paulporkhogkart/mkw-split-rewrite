# Controller Bridge — Software Design Document

## Architecture Overview

The clip-sweep pipeline is now split across two processes:

```
Windows (sweep_runner.py)                WSL2 (controller_agent.py)
─────────────────────────────────────    ──────────────────────────────────────
BridgeController                         _CtrlStateHolder
  └─ ControllerBridge ──── TCP 7878 ───> dispatch()
       press / hold / rstick_down          └─ full_runner._press / _hold
       get_status / get_mac               ProController (nxbt)
                                          ControllerState + sender_thread
                                            R-stick held DOWN @ 120 Hz (anti-spin)
```

The Windows side runs the sweep logic entirely. The WSL2 agent owns Bluetooth,
nxbt, and the 120 Hz sender loop. Communication is newline-delimited JSON over
a TCP loopback socket.

---

## Files Built

### `tools/autotemplate/controller_agent.py` (WSL2)

TCP server listening on `127.0.0.1:7878`. Modelled on nxauto's `agent.py`.

Key design choices:
- `_CtrlStateHolder`: thread-safe `(ctrl, state)` container; starts empty and is
  populated by the background `_init_ctrl` thread once the Switch pairs.
- `dispatch(msg, holder)`: a **standalone function** (not a method) that handles
  one decoded command dict and returns a response dict.  No socket I/O — fully
  unit-testable by calling it directly in tests.
- `_init_ctrl`: background thread; 5 s startup settle, 10 attempts 3 s apart.
  ALL nxbt/ProController imports live **inside** this function — the module top
  never imports nxbt, so `import controller_agent` succeeds on Windows.
- Anti-spin: immediately after `ProController.connect()` succeeds, a
  `ControllerState` is created, `state.replay_update(0, 0, 0, 0, -127)` is
  called to set R-stick Y = -127 (full DOWN), then `sender_thread` is started.
  The sender sends the held state to the Switch at 120 Hz continuously —
  R-stick stays DOWN for the whole session unless a command explicitly changes it.

Supported commands:
| type | requires ctrl | description |
|---|---|---|
| `ping` | no | health-check |
| `get_status` | no | `{connected, mac}` |
| `get_mac` | no | `{mac}` |
| `wait` | no | `time.sleep(seconds)` |
| `press` | yes | calls `full_runner._press(state, button, duration, after, dry_run=False)` |
| `press_many` | yes | loops `_press` for each button |
| `hold` | yes | calls `full_runner._hold(state, button, duration, dry_run=False)` |
| `rstick_down` | yes | re-asserts `state.replay_update(0,0,0,0,-127)` (idempotent) |
| `macro` | yes | calls `ctrl.macro(text)` |

### `tools/autotemplate/controller_bridge.py` (Windows)

TCP client. Modelled on nxauto's `ControllerBridge` (`autoflow_engine/actions/bridge.py`).

- `connect()`: single-attempt connect with 3 s timeout.
- `_send_command_raw(cmd)`: thread-safe send + recv into newline-terminated buffer.
  On OSError or decode failure, marks disconnected and closes socket.
- `_send_command(cmd)`: wraps raw, returns `bool(resp["ok"])`.
- `start_reconnect_loop()`: daemon thread that calls `connect()` every
  `reconnect_interval` seconds while disconnected.
- Methods: `press`, `press_many`, `hold`, `rstick_down`, `wait`, `macro`,
  `ping`, `get_status`, `get_mac`, `close`.

### `tools/autotemplate/sweep_runner.py` (modified)

Changes from the previous version:
- `NxbtController` **removed** — it drove nxbt directly from WSL2, no longer needed.
- `BridgeController` **added**: imports `ControllerBridge` lazily in `__init__`,
  calls `connect()`, then delegates `press`/`hold`/`rstick_down`/`stop` through
  the bridge.
- `main()`: now builds `BridgeController(host, port)` in LIVE mode.
  Added `--agent-host` (default 127.0.0.1) and `--agent-port` (default 7878).
  Removed `--mac` (the agent owns the Switch MAC via `--reconnect-addr`).
- Module top still `import time` only — no nxbt, no switch_bridge, no full_runner
  at module scope.

### `tests/test_controller_bridge.py`

Real socket round-trip tests. Approach:
- `_EchoServer`: in-process TCP server on a random ephemeral port.  Accepts one
  connection, reads newline-delimited JSON commands, returns canned dicts from a
  response table, closes.
- `ControllerBridge` is pointed at the echo server's port.
- Assertions cover: exact JSON sent (type/button/duration/after fields),
  response parsing (`ok` bool, `connected`, `mac`), and error propagation
  (`ok=false` → `press()` returns False).

### `tests/test_controller_agent.py`

Unit tests for `dispatch()`. No socket, no nxbt, no WSL2.
- `_FakeCtrl`: records `get_mac()` return and `macro()` calls.
- `_FakeState`: records `replay_update()` calls; `frames_sent` = 1000 so
  `_press`'s frame-count math doesn't spin.
- `_make_holder()`: builds a `_CtrlStateHolder` optionally pre-loaded with fakes.
- `monkeypatch` patches `full_runner._press` and `full_runner._hold` to collect
  call args without real sleeps or hardware.
- Covers: ping, get_status/get_mac (connected + not-connected), wait, press,
  press_many, hold, rstick_down (asserts `ry=-127`), macro, unknown type, missing type.

---

## Anti-Spin Preservation via switch_bridge

`switch_bridge.ControllerState` + `sender_thread` is the same mechanism used by
`NxbtController` (the old class) and `full_runner.FullRunner._setup_hardware`.

The sender loop runs at 120 Hz (`SEND_HZ = 120`) and calls
`ctrl._nx.set_controller_input(ctrl._idx, packet)` every `1/120` s.  The packet
is derived from `ControllerState.snapshot()` which reads the current
`(buttons, lx, ly, rx, ry)` atomically.

By calling `state.replay_update(0, 0, 0, 0, -127)` **before** starting
`sender_thread`, the very first packet sent to the Switch has R-stick Y = -127.
This prevents the character from spinning on the selection screen from the moment
the controller connects.  `replay_update` bypasses the `_replaying` guard so the
value is always written regardless of other state.

The `rstick_down` command re-asserts `state.replay_update(0, 0, 0, 0, -127)`
idempotently — useful if a `press` command temporarily cleared the stick value
(which it does: `_press` sets `state.replay_update(bit, 0, 0, 0, 0)` while the
button is held, then `state.replay_update(0, 0, 0, 0, 0)` to release).

---

## Lazy-Import Strategy

| Location | What's lazy | Why |
|---|---|---|
| `controller_agent.py` module top | nxbt, ProController, ControllerState, sender_thread | nxbt only exists in WSL2/Linux; Windows runs `import controller_agent` for tests and bridge testing without nxbt |
| `controller_bridge.py` | nothing — pure stdlib | Already safe everywhere |
| `sweep_runner.py` module top | everything except `time` | `from sweep_runner import SweepRunner` must work on Windows for unit tests |
| `BridgeController.__init__` | `ControllerBridge` import | Keeps module top clean; consistent with existing lazy-import pattern |

---

## Test Suite

| Phase | Count |
|---|---|
| Before (asset-clip-sweep HEAD) | 353 |
| After task 3b9c1cc (bridge + agent added) | 380 |
| After 0a1dc68 (anti-spin + rstick_down tidy) | 381 |

### dry-run behaviour

`python tools/autotemplate/sweep_runner.py --dry-run` uses `_DryController` and
`_DryClient` — no TCP connection to the agent, no WebSocket to the broadcaster.
Every press/hold prints `[DRY] press ...` and returns immediately.  The sweep
walks the full grid and prints "Sweep complete." without any hardware.

---

## Concerns / Open Items

- `controller_agent.py` targets WSL2 Linux; `--reconnect-addr` is required in
  practice to avoid pairing-mode on every restart.  The 5 s startup delay in
  `_init_ctrl` matches nxauto's agent and should be sufficient for BlueZ to settle.
- `_press` in `full_runner.py` busy-waits on `state.frames_sent` (requires the
  sender thread to be running).  If `dispatch("press", ...)` is called before
  the sender thread starts it will hang.  Mitigation: the holder starts empty;
  `dispatch` returns `{"ok": false, "error": "controller not connected"}` until
  `_init_ctrl` succeeds.
- The echo server in `test_controller_bridge.py` accepts exactly one connection
  (single-accept design) — adequate for the sequential test cases.  If tests
  are ever parallelised with `pytest-xdist`, each test needs its own `_EchoServer`
  instance (which they already do, since each test function creates a new one).

---

## Post-Commit Fixes — `0a1dc68`

### FIX 1 — Anti-spin at snapshot level (`_AntiSpinState`)

**Root cause:** `full_runner._press` calls `state.replay_update(bit, 0,0,0,0)` (press)
then `replay_update(0,0,0,0,0)` (release) — both zero R-stick Y.  The 120 Hz sender
reads `state.snapshot()`, so during a press (~12 frames) the Switch sees R-stick
NEUTRAL → kart can spin mid-press.

**Fix:** Added `_AntiSpinState(ControllerState)` in `controller_agent.py` with a
`snapshot()` override that forces `snap["ry"] = -127` on every call, making anti-spin
immune to whatever `replay_update` writes.  Moved `from switch_bridge import
ControllerState` to module top (nxbt-free; Windows-safe).  `_init_ctrl` now
instantiates `_AntiSpinState()` instead of `ControllerState()` and drops the now-
redundant `state.replay_update(0, 0, 0, 0, -127)` pre-sender call.

**Test:** `test_antispin_state_forces_rstick_down_even_after_button_update` in
`tests/test_controller_agent.py` verifies ry=-127 after a neutral release and after a
button press that zeros the sticks, while confirming the button bit still propagates.

### FIX 2 — Drop dead `dur` param from `rstick_down`

`ControllerBridge.rstick_down(self, dur: float = 0)` accepted `dur` but never
sent it.  `BridgeController.rstick_down(self, dur: float = 0)` forwarded it
as `dur=dur` (also unused).  `_DryController.rstick_down(self, dur)` printed
`{dur}s` (misleading "timed hold").

Changed all three to `rstick_down(self)`.  The delegating call in
`BridgeController.rstick_down` is updated to `self._bridge.rstick_down()`.
No call sites in `main()` or `SweepRunner` passed `dur` (confirmed by grep).
