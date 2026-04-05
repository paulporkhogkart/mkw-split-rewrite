"""
Raw hold-duration recorder with simultaneous inputs and thumbstick support.

Hold keys → sent to Switch in real time. Multiple keys simultaneously works.
Release → duration logged. Output is plain text.

Usage:
    sudo ~/autotemplate-venv/bin/python3 record.py [--reconnect MAC] [--out out.txt]
    sudo ~/autotemplate-venv/bin/python3 record.py --dry-run

Button mapping:
    Arrow keys   DPAD          Z  A     X  B
    Q  L         W  R          E  ZL    R  ZR
    Enter  PLUS  Backspace  MINUS
    H  HOME      C  CAPTURE

Left stick (analog):
    I=up   K=down   J=left   L=right
    (hold combinations for diagonals, e.g. I+J = up-left)

Recorder controls:
    [   start a named segment
    ]   end segment
    ESC quit and save
"""
import argparse
import threading
import time

import blessed

SWITCH_MAC = "E0:EF:BF:03:74:19"

# Keyboard key → Switch button name
BUTTON_MAP = {
    "KEY_UP":        "DPAD_UP",
    "KEY_DOWN":      "DPAD_DOWN",
    "KEY_LEFT":      "DPAD_LEFT",
    "KEY_RIGHT":     "DPAD_RIGHT",
    "z":             "A",
    "x":             "B",
    "q":             "L",
    "w":             "R",
    "e":             "ZL",
    "r":             "ZR",
    "KEY_ENTER":     "PLUS",
    "KEY_BACKSPACE": "MINUS",
    "h":             "HOME",
    "c":             "CAPTURE",
}

# Keyboard key → (stick x, stick y, display name)  range -100..+100
STICK_MAP = {
    "i": ( 0,    100, "L_STICK_UP"),
    "k": ( 0,   -100, "L_STICK_DOWN"),
    "j": (-100,    0, "L_STICK_LEFT"),
    "l": ( 100,    0, "L_STICK_RIGHT"),
}

RELEASE_TIMEOUT = 0.18   # seconds without repeat → key released
SEND_INTERVAL   = 0.016  # ~60 Hz state push to Switch


def _parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--reconnect", default=SWITCH_MAC)
    p.add_argument("--out", default=f"recorded_{int(time.time())}.txt")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


# ── State ─────────────────────────────────────────────────────────────────────

class InputState:
    """Thread-safe map of currently held keys with timestamps."""

    def __init__(self):
        self._lock   = threading.Lock()
        self._held   = {}   # key → {"start": float, "last": float}

    def touch(self, key: str) -> bool:
        """Mark key as seen now. Returns True if this is a new press."""
        now = time.monotonic()
        with self._lock:
            if key in self._held:
                self._held[key]["last"] = now
                return False
            self._held[key] = {"start": now, "last": now}
            return True

    def sweep(self) -> list[tuple[str, float]]:
        """Return and remove keys whose last-seen is older than RELEASE_TIMEOUT."""
        now = time.monotonic()
        released = []
        with self._lock:
            for key, info in list(self._held.items()):
                if now - info["last"] > RELEASE_TIMEOUT:
                    # Estimate release happened at last_seen + half the timeout
                    release_est = info["last"] + RELEASE_TIMEOUT / 2
                    dur = max(release_est - info["start"], 0.05)
                    released.append((key, dur))
                    del self._held[key]
        return released

    def snapshot(self) -> list[str]:
        with self._lock:
            return list(self._held.keys())


# ── Switch sender ─────────────────────────────────────────────────────────────

def _compute_stick(held_keys: list[str]) -> tuple[int, int]:
    x, y = 0, 0
    for k in held_keys:
        if k in STICK_MAP:
            dx, dy, _ = STICK_MAP[k]
            x += dx
            y += dy
    return max(-100, min(100, x)), max(-100, min(100, y))


def _sender_thread(ctrl, state: InputState, alive: threading.Event):
    while not alive.is_set():
        keys = state.snapshot()
        parts = [BUTTON_MAP[k] for k in keys if k in BUTTON_MAP]

        sx, sy = _compute_stick(keys)
        if sx != 0 or sy != 0:
            parts.append(f"L_STICK@{sx:+04d}{sy:+04d}")

        if parts:
            # Single macro line = all inputs sent simultaneously
            macro = " ".join(parts) + f" {SEND_INTERVAL:.3f}s"
            try:
                ctrl._nx.macro(ctrl._idx, macro, block=True)
            except Exception:
                pass
        else:
            time.sleep(SEND_INTERVAL)


# ── Logger ────────────────────────────────────────────────────────────────────

class Logger:
    def __init__(self, path: str):
        self._f       = open(path, "w", buffering=1)
        self._seg     = 0
        self._active  = False

    def start_segment(self, label: str):
        self._seg += 1
        self._active = True
        self._write(f"\n--- [{self._seg}] {label} ---")

    def end_segment(self):
        if self._active:
            self._write("--- end ---")
            self._active = False

    def log_hold(self, key: str, duration: float):
        name = BUTTON_MAP.get(key) or (STICK_MAP[key][2] if key in STICK_MAP else key)
        line = f"hold {name} {duration:.3f}s"
        print(f"    {line}")
        if self._active:
            self._write(line)

    def _write(self, line: str):
        print(f"  {line.strip()}")
        self._f.write(line + "\n")

    def close(self):
        self._f.close()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    args  = _parse_args()
    term  = blessed.Terminal()
    state = InputState()
    log   = Logger(args.out)

    ctrl = None
    stop_sender = threading.Event()

    if not args.dry_run:
        from controller import ProController
        ctrl = ProController()
        print(f"Reconnecting to {args.reconnect}…")
        ctrl.connect(reconnect_addr=args.reconnect)
        print("Connected!\n")
        threading.Thread(target=_sender_thread,
                         args=(ctrl, state, stop_sender),
                         daemon=True).start()
    else:
        print("DRY RUN\n")

    print("Hold keys → held on Switch + recorded. Simultaneous keys work.")
    print("IJKL = left stick.  [ = start segment  ] = end  ESC = quit\n")
    print(f"Output → {args.out}\n")

    # Release watcher
    def _watcher():
        while not stop_sender.is_set():
            for key, dur in state.sweep():
                log.log_hold(key, dur)
            time.sleep(0.02)

    threading.Thread(target=_watcher, daemon=True).start()

    # Input loop — breaks out to prompt for segment label, then re-enters
    need_label = False
    running    = True

    while running:
        if need_label:
            label = input("  Segment label: ").strip() or f"segment_{log._seg + 1}"
            log.start_segment(label)
            print("  Recording… ] to end, [ for next segment, ESC to quit.\n")
            need_label = False

        with term.cbreak(), term.hidden_cursor():
            while True:
                val = term.inkey(timeout=0.005)
                if not val:
                    continue

                raw = str(val)

                if val.name == "KEY_ESCAPE" or raw == "\x1b":
                    running = False
                    break

                if raw == "[":
                    need_label = True
                    break

                if raw == "]":
                    log.end_segment()
                    continue

                lookup = val.name if val.is_sequence else raw.lower()
                if lookup in BUTTON_MAP or lookup in STICK_MAP:
                    is_new = state.touch(lookup)
                    if is_new:
                        name = BUTTON_MAP.get(lookup) or STICK_MAP[lookup][2]
                        status = "●" if log._active else " "
                        print(f"  {status} {name}")

    stop_sender.set()
    log.end_segment()
    log.close()
    if ctrl:
        ctrl.disconnect()
    print(f"\nSaved → {args.out}")


if __name__ == "__main__":
    main()
