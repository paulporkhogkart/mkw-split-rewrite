"""TCP server (WSL2): receives newline-delimited JSON commands, drives the Switch.

Modelled on nxauto's agent.py but drives the controller via switch_bridge.sender_thread
so the RIGHT STICK can be held DOWN at 120 Hz (anti-spin) while the kart screen is active.

Usage (in WSL2, with sudo if nxbt needs root):
    sudo python3 controller_agent.py [--port 7878] [--adapter hci0] [--reconnect-addr AA:BB:CC:DD:EE:FF]

Module-level imports are limited to stdlib + switch_bridge + full_runner so that
    import controller_agent
works on Windows (where nxbt is not installed).  ALL nxbt/ProController imports
happen lazily inside _init_ctrl().
"""

import json
import sys
import socket
import threading
import time
from typing import Optional

from switch_bridge import ControllerState


# ── Anti-spin ControllerState subclass ───────────────────────────────────────

# Anti-spin R-stick wiggle: a perfectly still straight-DOWN hold let the spin resume, so
# while active we keep ry=DOWN but nudge rx a teeny bit LEFT/RIGHT each cycle — the flip
# registers as fresh stick input (the way a hand never holds perfectly still). Tune here.
_ANTISPIN_WIGGLE        = 22     # rx magnitude of the L/R nudge (out of 127); "teeny tiny"
_ANTISPIN_WIGGLE_PERIOD = 0.20   # seconds for one left+right wiggle cycle


def antispin_rx(t: float) -> int:
    """Tiny L/R wiggle of the R-stick around straight-down at time `t` (s): slightly LEFT
    for the first half of each period, slightly RIGHT for the second. Pure + testable."""
    phase = t % _ANTISPIN_WIGGLE_PERIOD
    return -_ANTISPIN_WIGGLE if phase < (_ANTISPIN_WIGGLE_PERIOD / 2) else _ANTISPIN_WIGGLE


class _AntiSpinState(ControllerState):
    """ControllerState that, while `antispin_active`, holds the R-stick DOWN (ry=-127) and
    nudges it a teeny bit LEFT/RIGHT each cycle (rx wiggle) — a perfectly still down-hold let
    the kart spin resume, so the wiggle keeps it reading as fresh input. The pilot toggles
    antispin_active via the 'antispin' command so this is on ONLY while the kart screen is
    active. Forcing the stick in snapshot() keeps it immune to button presses (which zero it)."""
    antispin_active = False     # set per-instance by the 'antispin' command

    def snapshot(self):
        snap = super().snapshot()
        if self.antispin_active:
            snap["ry"] = -127
            snap["rx"] = antispin_rx(time.monotonic())
        return snap


# ── ctrl_state_holder ────────────────────────────────────────────────────────
# A simple container holding (ctrl, state) once the controller is connected,
# or None until then.  Thread-safe via a single lock.

class _CtrlStateHolder:
    def __init__(self):
        self._lock  = threading.Lock()
        self._ctrl  = None   # ProController instance once connected
        self._state = None   # ControllerState instance once connected

    def set(self, ctrl, state):
        with self._lock:
            self._ctrl  = ctrl
            self._state = state

    def get(self):
        """Returns (ctrl, state) tuple, or (None, None) if not yet connected."""
        with self._lock:
            return self._ctrl, self._state

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._ctrl is not None


# ── Dispatch (factored out — unit-testable without a socket) ─────────────────

def dispatch(msg: dict, holder: _CtrlStateHolder) -> dict:
    """Handle one decoded command dict.  Returns a response dict (no socket I/O).

    This function is a plain callable so tests can call it directly without
    binding a real socket or starting the TCP server.
    """
    t = msg.get("type", "")

    # ── Ping ─────────────────────────────────────────────────────────────────
    if t == "ping":
        return {"ok": True, "error": ""}

    # ── Status queries ────────────────────────────────────────────────────────
    if t == "get_status":
        ctrl, _ = holder.get()
        mac = (ctrl.get_mac() or "") if ctrl is not None else ""
        return {"ok": True, "error": "", "connected": ctrl is not None, "mac": mac}

    if t == "get_mac":
        ctrl, _ = holder.get()
        mac = (ctrl.get_mac() or "") if ctrl is not None else ""
        return {"ok": True, "error": "", "mac": mac}

    # ── Wait (no controller needed) ───────────────────────────────────────────
    if t == "wait":
        time.sleep(float(msg.get("seconds", 0.1)))
        return {"ok": True, "error": ""}

    # ── Controller-required commands ──────────────────────────────────────────
    ctrl, state = holder.get()

    if t == "press":
        if state is None:
            return {"ok": False, "error": "controller not connected"}
        # Lazy import — full_runner is nxbt-free at module scope
        from full_runner import _press as _press_fn
        button   = str(msg.get("button", "A"))
        duration = float(msg.get("duration", 0.1))
        after    = float(msg.get("after",   0.05))
        _press_fn(state, button, duration=duration, after=after, dry_run=False)
        return {"ok": True, "error": ""}

    if t == "press_many":
        if state is None:
            return {"ok": False, "error": "controller not connected"}
        from full_runner import _press as _press_fn
        buttons  = list(msg.get("buttons", []))
        duration = float(msg.get("duration", 0.1))
        after    = float(msg.get("after",   0.05))
        for btn in buttons:
            _press_fn(state, str(btn), duration=duration, after=after, dry_run=False)
        return {"ok": True, "error": ""}

    if t == "hold":
        if state is None:
            return {"ok": False, "error": "controller not connected"}
        from full_runner import _hold as _hold_fn
        button   = str(msg.get("button", "A"))
        duration = float(msg.get("duration", 1.0))
        _hold_fn(state, button, duration, dry_run=False)
        return {"ok": True, "error": ""}

    if t == "rstick_down":
        # Re-assert R-stick DOWN — idempotent; safe to call repeatedly.
        if state is None:
            return {"ok": False, "error": "controller not connected"}
        state.replay_update(0, 0, 0, 0, -127)
        return {"ok": True, "error": ""}

    if t == "antispin":
        # Hold the R-stick DOWN only while `on` — the pilot turns this on for the kart
        # screen and off otherwise. The hold is immune to button presses (forced in
        # snapshot()), so navigation still works while it's active.
        if state is None:
            return {"ok": False, "error": "controller not connected"}
        state.antispin_active = bool(msg.get("on", False))
        return {"ok": True, "error": "", "antispin": state.antispin_active}

    if t == "macro":
        if ctrl is None:
            return {"ok": False, "error": "controller not connected"}
        ctrl.macro(str(msg.get("text", "")))
        return {"ok": True, "error": ""}

    return {"ok": False, "error": f"unknown command type: {t!r}"}


# ── Per-connection handler ────────────────────────────────────────────────────

def _handle_connection(conn: socket.socket, holder: _CtrlStateHolder):
    buf = b""
    conn.settimeout(None)
    try:
        while True:
            chunk = conn.recv(1024)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    msg  = json.loads(line)
                    resp = dispatch(msg, holder)
                except Exception as exc:
                    resp = {"ok": False, "error": str(exc)}
                conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))
    except OSError:
        pass
    finally:
        conn.close()


# ── Background connect thread ─────────────────────────────────────────────────

def _init_ctrl(holder: _CtrlStateHolder, adapter: str,
               reconnect_addr: Optional[str], stop: threading.Event):
    """Connect to the Switch, set up ControllerState + sender_thread (anti-spin)."""
    # Brief startup delay so bluetoothd/usbipd settle.
    time.sleep(5)

    for attempt in range(10):
        if stop.is_set():
            return
        try:
            # ALL nxbt/ProController imports are lazy — this is the only place.
            from controller import ProController
            from switch_bridge import sender_thread as _sender

            ctrl  = ProController(adapter=adapter)
            ctrl.connect(reconnect_addr=reconnect_addr)

            # _AntiSpinState forces ry=-127 on every snapshot() call,
            # so the sender always sends R-stick DOWN even during _press.
            state = _AntiSpinState()

            sender_stop = threading.Event()
            threading.Thread(
                target=_sender,
                args=(ctrl, state, sender_stop),
                daemon=True,
                name="ctrl-sender",
            ).start()

            holder.set(ctrl, state)
            print(f"[Agent] Switch connected. MAC={ctrl.get_mac() or 'unknown'}")
            return

        except Exception as exc:
            print(f"[Agent] Controller init error (attempt {attempt + 1}): {exc}",
                  file=sys.stderr)
            time.sleep(3)

    print("[Agent] Controller init failed after 10 attempts.", file=sys.stderr)


# ── Main server loop ──────────────────────────────────────────────────────────

def run_agent(port: int = 7878, adapter: str = "hci0",
              reconnect_addr: Optional[str] = None):
    """Bind TCP, spawn the background connect thread, accept clients forever."""
    print(f"[Agent] Listening on 127.0.0.1:{port} (adapter={adapter})")

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", port))
    server.listen(8)

    holder = _CtrlStateHolder()
    stop   = threading.Event()

    threading.Thread(
        target=_init_ctrl,
        args=(holder, adapter, reconnect_addr, stop),
        daemon=True,
        name="ctrl-init",
    ).start()

    try:
        while True:
            try:
                conn, addr = server.accept()
            except OSError:
                break
            print(f"[Agent] Client connected from {addr}")
            threading.Thread(
                target=_handle_connection,
                args=(conn, holder),
                daemon=True,
                name=f"agent-conn-{addr[1]}",
            ).start()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.close()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    import argparse
    p = argparse.ArgumentParser(
        description="WSL2 TCP controller agent — drives the Switch via nxbt + switch_bridge.")
    p.add_argument("--port",           type=int, default=7878,
                   help="TCP listen port (default: 7878)")
    p.add_argument("--adapter",        default="hci0",
                   help="Bluetooth adapter name (default: hci0)")
    p.add_argument("--reconnect-addr", default=None, metavar="MAC",
                   help="Switch Bluetooth MAC address for reconnect mode "
                        "(e.g. E0:EF:BF:03:74:19)")
    args = p.parse_args()
    run_agent(port=args.port, adapter=args.adapter, reconnect_addr=args.reconnect_addr)


if __name__ == "__main__":
    main()
