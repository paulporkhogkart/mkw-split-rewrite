"""
Gamepad record + replay for Switch automation.

Reads a USB gamepad via evdev, forwards inputs to the Switch via nxbt in
real time, and saves a recording. Replay drives the Switch from a saved file
with no gamepad needed.

Setup — forward your USB gamepad to WSL2 first (PowerShell, Admin):
    usbipd list
    usbipd bind   --busid X-Y
    usbipd attach --wsl --busid X-Y

Usage:
    # List detected gamepads
    sudo ~/autotemplate-venv/bin/python3 record_gamepad.py --list

    # Record (connects to Switch, records to file)
    sudo ~/autotemplate-venv/bin/python3 record_gamepad.py --out session.json

    # Replay a recording to Switch
    sudo ~/autotemplate-venv/bin/python3 record_gamepad.py --replay session.json

    # Record without forwarding to Switch (test gamepad only)
    sudo ~/autotemplate-venv/bin/python3 record_gamepad.py --out session.json --no-switch

Button layout assumes Xbox-style gamepad (most common Linux HID mapping):
    A=A  B=B  X=Y  Y=X  LB=L  RB=R  LT=ZL  RT=ZR
    Back=MINUS  Start=PLUS  Guide=HOME  LS=L_STICK  RS=R_STICK
    Left stick → L_STICK   Right stick → R_STICK   D-pad → DPAD_*

Edit BUTTON_MAP / AXIS_MAP below if your gamepad uses different codes.
Run with --list to see raw event codes from your device.
"""

import argparse
import json
import os
import select
import sys
import threading
import time

import evdev
from evdev import ecodes

SWITCH_MAC   = "E0:EF:BF:03:74:19"
SEND_HZ      = 60
SEND_INTERVAL = 1.0 / SEND_HZ
AXIS_DEADZONE = 0.08   # fraction of max range


# ── Mapping ───────────────────────────────────────────────────────────────────
#
# evdev key code → Switch button name

BUTTON_MAP = {
    ecodes.BTN_SOUTH:  "A",
    ecodes.BTN_EAST:   "B",
    ecodes.BTN_NORTH:  "X",
    ecodes.BTN_WEST:   "Y",
    ecodes.BTN_TL:     "L",
    ecodes.BTN_TR:     "R",
    ecodes.BTN_SELECT: "MINUS",
    ecodes.BTN_START:  "PLUS",
    ecodes.BTN_MODE:   "HOME",
    ecodes.BTN_THUMBL: "L_STICK_PRESS",
    ecodes.BTN_THUMBR: "R_STICK_PRESS",
}

# Trigger threshold — above this fraction of max range → button pressed
TRIGGER_THRESHOLD = 0.5


# ── Gamepad discovery ─────────────────────────────────────────────────────────

def list_gamepads() -> list[evdev.InputDevice]:
    pads = []
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
            caps = dev.capabilities()
            if ecodes.EV_KEY in caps and ecodes.EV_ABS in caps:
                pads.append(dev)
        except Exception:
            pass
    return pads


def pick_gamepad(gamepads: list[evdev.InputDevice]) -> evdev.InputDevice:
    if not gamepads:
        sys.exit("No gamepads found. Check 'usbipd attach --wsl' and try again.")
    if len(gamepads) == 1:
        print(f"Using gamepad: {gamepads[0].name} ({gamepads[0].path})")
        return gamepads[0]
    print("Multiple gamepads found:")
    for i, g in enumerate(gamepads):
        print(f"  [{i}] {g.name} ({g.path})")
    idx = int(input("Select [0]: ").strip() or "0")
    return gamepads[idx]


# ── State ─────────────────────────────────────────────────────────────────────

class SwitchState:
    """Current desired Switch controller state derived from gamepad input."""

    def __init__(self, device: evdev.InputDevice):
        self._lock    = threading.Lock()
        self._buttons: set[str] = set()
        self._lx = self._ly = self._rx = self._ry = 0  # -100..+100

        # Learn axis ranges from device absinfo
        abs_caps = device.capabilities().get(ecodes.EV_ABS, [])
        self._abs_info = {code: info for code, info in abs_caps}

    # ── reads (called from evdev thread) ─────────────────────────────────────

    def apply_event(self, event: evdev.InputEvent) -> bool:
        """Update state from one evdev event. Returns True if state changed."""
        if event.type == ecodes.EV_KEY:
            return self._apply_key(event.code, event.value)
        if event.type == ecodes.EV_ABS:
            return self._apply_abs(event.code, event.value)
        return False

    def _apply_key(self, code: int, value: int) -> bool:
        btn = BUTTON_MAP.get(code)
        if btn is None:
            return False
        with self._lock:
            before = frozenset(self._buttons)
            if value:
                self._buttons.add(btn)
            else:
                self._buttons.discard(btn)
            return self._buttons != before

    def _apply_abs(self, code: int, value: int) -> bool:
        info = self._abs_info.get(code)

        # D-pad via HAT axes
        if code == ecodes.ABS_HAT0X:
            with self._lock:
                self._buttons.discard("DPAD_LEFT")
                self._buttons.discard("DPAD_RIGHT")
                if value < 0:
                    self._buttons.add("DPAD_LEFT")
                elif value > 0:
                    self._buttons.add("DPAD_RIGHT")
            return True

        if code == ecodes.ABS_HAT0Y:
            with self._lock:
                self._buttons.discard("DPAD_UP")
                self._buttons.discard("DPAD_DOWN")
                if value < 0:
                    self._buttons.add("DPAD_UP")
                elif value > 0:
                    self._buttons.add("DPAD_DOWN")
            return True

        # Triggers → digital ZL / ZR
        if code in (ecodes.ABS_Z, ecodes.ABS_RZ) and info:
            span     = info.max - info.min
            norm     = (value - info.min) / span if span else 0
            pressed  = norm > TRIGGER_THRESHOLD
            btn      = "ZL" if code == ecodes.ABS_Z else "ZR"
            with self._lock:
                before = btn in self._buttons
                if pressed:
                    self._buttons.add(btn)
                else:
                    self._buttons.discard(btn)
                return pressed != before

        # Analog sticks
        axis_map = {
            ecodes.ABS_X:  "_lx",
            ecodes.ABS_Y:  "_ly",
            ecodes.ABS_RX: "_rx",
            ecodes.ABS_RY: "_ry",
        }
        attr = axis_map.get(code)
        if attr and info:
            span = info.max - info.min
            norm = ((value - info.min) / span * 2 - 1) if span else 0
            # Apply deadzone
            if abs(norm) < AXIS_DEADZONE:
                norm = 0.0
            scaled = int(round(norm * 100))
            with self._lock:
                if getattr(self, attr) == scaled:
                    return False
                setattr(self, attr, scaled)
            return True

        return False

    # ── snapshot (called from sender thread) ──────────────────────────────────

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "buttons": sorted(self._buttons),
                "lx": self._lx, "ly": self._ly,
                "rx": self._rx, "ry": self._ry,
            }


# ── nxbt sender ───────────────────────────────────────────────────────────────

def _build_macro(snap: dict) -> str | None:
    parts = list(snap["buttons"])
    lx, ly = snap["lx"], -snap["ly"]   # Y axis inverted for Switch
    rx, ry = snap["rx"], -snap["ry"]
    if lx != 0 or ly != 0:
        parts.append(f"L_STICK@{lx:+04d}{ly:+04d}")
    if rx != 0 or ry != 0:
        parts.append(f"R_STICK@{rx:+04d}{ry:+04d}")
    if not parts:
        return None
    return " ".join(parts) + f" {SEND_INTERVAL:.3f}s"


def sender_thread(ctrl, state: SwitchState, stop: threading.Event):
    while not stop.is_set():
        snap  = state.snapshot()
        macro = _build_macro(snap)
        if macro:
            try:
                ctrl._nx.macro(ctrl._idx, macro, block=True)
            except Exception:
                pass
        else:
            time.sleep(SEND_INTERVAL)


# ── Recording ─────────────────────────────────────────────────────────────────

def record(device: evdev.InputDevice, state: SwitchState,
           out_path: str, stop: threading.Event):
    """Write state-change events to out_path as newline-delimited JSON."""
    t0      = time.monotonic()
    records = []
    last    = None

    print(f"\nRecording → {out_path}")
    print("Press Ctrl+C to stop.\n")

    try:
        while not stop.is_set():
            r, _, _ = select.select([device.fd], [], [], 0.02)
            if not r:
                continue
            for event in device.read():
                if state.apply_event(event):
                    snap = state.snapshot()
                    if snap != last:
                        snap["t"] = round(time.monotonic() - t0, 4)
                        records.append(snap)
                        last = {k: v for k, v in snap.items() if k != "t"}
                        _print_snap(snap)
    except KeyboardInterrupt:
        pass

    with open(out_path, "w") as f:
        json.dump(records, f, indent=2)
    print(f"\nSaved {len(records)} events → {out_path}")


def _print_snap(snap: dict):
    btns = " ".join(snap["buttons"]) or "-"
    print(f"  t={snap['t']:.3f}  [{btns}]"
          f"  L({snap['lx']:+4d},{snap['ly']:+4d})"
          f"  R({snap['rx']:+4d},{snap['ry']:+4d})")


# ── Replay ────────────────────────────────────────────────────────────────────

def replay(ctrl, path: str):
    with open(path) as f:
        records = json.load(f)

    if not records:
        print("Empty recording.")
        return

    print(f"Replaying {len(records)} events from {path}\n")
    t0 = time.monotonic()

    for i, snap in enumerate(records):
        target_t = snap["t"]
        now_t    = time.monotonic() - t0
        wait     = target_t - now_t
        if wait > 0:
            time.sleep(wait)

        # Build and send macro for this state
        macro = _build_macro(snap)
        if macro:
            try:
                ctrl._nx.macro(ctrl._idx, macro, block=True)
            except Exception as e:
                print(f"  [WARN] macro failed: {e}")

        _print_snap(snap)

    print("\nReplay done.")


# ── List mode ─────────────────────────────────────────────────────────────────

def do_list():
    pads = list_gamepads()
    if not pads:
        print("No gamepads found.")
        return
    for pad in pads:
        print(f"\n{pad.name}")
        print(f"  path: {pad.path}")
        caps = pad.capabilities(verbose=True)
        keys = [name for (name, _) in caps.get(("EV_KEY", ecodes.EV_KEY), [])]
        axes = [name for (name, _) in caps.get(("EV_ABS", ecodes.EV_ABS), [])]
        print(f"  buttons: {', '.join(keys[:20])}")
        print(f"  axes:    {', '.join(axes)}")


# ── Main ──────────────────────────────────────────────────────────────────────

def _parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--list",      action="store_true", help="List detected gamepads and exit")
    p.add_argument("--out",       default=f"session_{int(time.time())}.json", help="Output file for recording")
    p.add_argument("--replay",    default=None,  help="Replay a recorded session to Switch")
    p.add_argument("--reconnect", default=SWITCH_MAC)
    p.add_argument("--no-switch", action="store_true", help="Don't connect to Switch (record only)")
    return p.parse_args()


def main():
    args = _parse_args()

    if args.list:
        do_list()
        return

    # Connect Switch (unless --no-switch or replaying with --no-switch)
    ctrl = None
    if not args.no_switch:
        from controller import ProController
        ctrl = ProController()
        print(f"Reconnecting to {args.reconnect}…")
        ctrl.connect(reconnect_addr=args.reconnect)
        print("Connected!\n")

    if args.replay:
        if ctrl is None:
            sys.exit("--replay requires a Switch connection. Remove --no-switch.")
        replay(ctrl, args.replay)
        ctrl.disconnect()
        return

    # Record mode
    pads = list_gamepads()
    device = pick_gamepad(pads)
    state  = SwitchState(device)

    stop = threading.Event()

    if ctrl:
        t = threading.Thread(target=sender_thread, args=(ctrl, state, stop), daemon=True)
        t.start()

    try:
        record(device, state, args.out, stop)
    finally:
        stop.set()
        if ctrl:
            ctrl.disconnect()


if __name__ == "__main__":
    main()
