"""
WSL2-side bridge. Receives gamepad state from input_server.py (Windows),
forwards to Switch via nxbt, and records/replays sessions.

Usage:
    # Record a session (input_server.py must be running on Windows)
    sudo ~/autotemplate-venv/bin/python3 switch_bridge.py --out session.json

    # Replay a session to Switch
    sudo ~/autotemplate-venv/bin/python3 switch_bridge.py --replay session.json

    # Bridge only, no recording
    sudo ~/autotemplate-venv/bin/python3 switch_bridge.py
"""
import argparse
import asyncio
import json
import socket
import struct
import threading
import time

SWITCH_MAC  = "E0:EF:BF:03:74:19"
LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 7777
SEND_HZ     = 120   # Pro Controller reports at 120Hz

# ── Protocol (must match input_server.py) ─────────────────────────────────────
PACKET_FMT  = "!Hbbbbbb"
PACKET_SIZE = struct.calcsize(PACKET_FMT)

BIT_A       = 1 << 0
BIT_B       = 1 << 1
BIT_X       = 1 << 2
BIT_Y       = 1 << 3
BIT_L       = 1 << 4
BIT_R       = 1 << 5
BIT_ZL      = 1 << 6
BIT_ZR      = 1 << 7
BIT_PLUS    = 1 << 8
BIT_MINUS   = 1 << 9
BIT_HOME    = 1 << 10
BIT_CAPTURE = 1 << 11
BIT_DPAD_U  = 1 << 12
BIT_DPAD_D  = 1 << 13
BIT_DPAD_L  = 1 << 14
BIT_DPAD_R  = 1 << 15

BIT_TO_SWITCH = {
    BIT_A:       "A",
    BIT_B:       "B",
    BIT_X:       "X",
    BIT_Y:       "Y",
    BIT_L:       "L",
    BIT_R:       "R",
    BIT_ZL:      "ZL",
    BIT_ZR:      "ZR",
    BIT_PLUS:    "PLUS",
    BIT_MINUS:   "MINUS",
    BIT_HOME:    "HOME",
    BIT_CAPTURE: "CAPTURE",
    BIT_DPAD_U:  "DPAD_UP",
    BIT_DPAD_D:  "DPAD_DOWN",
    BIT_DPAD_L:  "DPAD_LEFT",
    BIT_DPAD_R:  "DPAD_RIGHT",
}


# ── State ─────────────────────────────────────────────────────────────────────

class ControllerState:
    def __init__(self):
        self._lock     = threading.Lock()
        self.buttons   = 0
        self.lx = self.ly = self.rx = self.ry = 0
        self._replaying = False
        self.frames_sent: int = 0   # incremented by sender_thread each cycle; never wraps in practice

    def update(self, buttons, lx, ly, rx, ry, lt, rt):
        """Live gamepad update — ignored while a replay is active."""
        with self._lock:
            if self._replaying:
                return
            self.buttons = buttons
            self.lx, self.ly = lx, ly
            self.rx, self.ry = rx, ry

    def replay_update(self, buttons, lx, ly, rx, ry):
        """Replay write — always goes through regardless of lock."""
        with self._lock:
            self.buttons = buttons
            self.lx, self.ly = lx, ly
            self.rx, self.ry = rx, ry

    def set_replaying(self, active: bool):
        with self._lock:
            self._replaying = active

    def snapshot(self) -> dict:
        with self._lock:
            return dict(buttons=self.buttons,
                        lx=self.lx, ly=self.ly,
                        rx=self.rx, ry=self.ry)


# ── nxbt direct-input sender ──────────────────────────────────────────────────
# Uses set_controller_input() instead of macros — state is held continuously
# with zero gaps between cycles, so buttons stay held as long as the gamepad
# holds them.

def _snap_to_packet(nx, snap: dict) -> dict:
    packet = nx.create_input_packet()

    for bit, name in BIT_TO_SWITCH.items():
        if snap["buttons"] & bit:
            packet[name] = True

    # Scale -127..127 → -100..100
    def s(v): return max(-100, min(100, int(v * 100 / 127)))

    packet["L_STICK"]["X_VALUE"] = s(snap["lx"])
    packet["L_STICK"]["Y_VALUE"] = s(snap["ly"])
    packet["R_STICK"]["X_VALUE"] = s(snap["rx"])
    packet["R_STICK"]["Y_VALUE"] = s(snap["ry"])

    return packet


def sender_thread(ctrl, state: ControllerState, stop: threading.Event):
    interval = 1.0 / SEND_HZ
    while not stop.is_set():
        snap   = state.snapshot()
        packet = _snap_to_packet(ctrl._nx, snap)
        try:
            ctrl._nx.set_controller_input(ctrl._idx, packet)
        except Exception:
            pass
        state.frames_sent += 1   # CPython int write is GIL-safe; no lock needed
        time.sleep(interval)


# ── TCP receiver ──────────────────────────────────────────────────────────────

def recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionResetError("input_server disconnected")
        buf += chunk
    return buf


def receive_loop(conn: socket.socket, state: ControllerState,
                 recorder, stop: threading.Event):
    try:
        while not stop.is_set():
            raw = recv_exact(conn, PACKET_SIZE)
            buttons, lx, ly, rx, ry, lt, rt = struct.unpack(PACKET_FMT, raw)
            state.update(buttons, lx, ly, rx, ry, lt, rt)
            if recorder:
                recorder.record(state.snapshot())
    except (ConnectionResetError, OSError):
        print("\ninput_server disconnected.")
        stop.set()


# ── Recording ─────────────────────────────────────────────────────────────────

class Recorder:
    def __init__(self, path: str):
        self._path = path
        self._t0   = time.monotonic()
        self._last = None
        self._log  = []

    def record(self, snap: dict):
        if snap == self._last:
            return
        entry = dict(snap)
        entry["t"] = round(time.monotonic() - self._t0, 4)
        self._log.append(entry)
        self._last = snap
        _print_snap(entry)

    def save(self):
        with open(self._path, "w") as f:
            json.dump(self._log, f, indent=2)
        print(f"\nSaved {len(self._log)} events → {self._path}")


def _print_snap(snap: dict):
    btns = [name for bit, name in BIT_TO_SWITCH.items() if snap["buttons"] & bit]
    lx, ly = snap.get("lx", 0), snap.get("ly", 0)
    rx, ry = snap.get("rx", 0), snap.get("ry", 0)
    t = snap.get("t", 0)
    print(f"  t={t:.3f}  [{' '.join(btns) or '-'}]"
          f"  L({lx:+4d},{ly:+4d})  R({rx:+4d},{ry:+4d})")


# ── Replay ────────────────────────────────────────────────────────────────────
# Updates shared ControllerState at each event's precise recorded timestamp.
# sender_thread keeps running at 120Hz throughout — no pausing needed.

def replay(state: ControllerState, path: str):
    with open(path) as f:
        events = json.load(f)

    if not events:
        print("Empty recording.")
        return

    print(f"Replaying {len(events)} events from {path}")
    print(f"  Duration: {events[-1]['t']:.2f}s  Events: {len(events)}\n")

    state.set_replaying(True)
    try:
        state.replay_update(0, 0, 0, 0, 0)
        t0 = time.monotonic()

        for ev in events:
            remaining = (t0 + ev["t"]) - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)
            state.replay_update(ev["buttons"], ev["lx"], ev["ly"], ev["rx"], ev["ry"])
            _print_snap(ev)

        time.sleep(0.1)
        state.replay_update(0, 0, 0, 0, 0)
        print("\nReplay done.")
    finally:
        state.set_replaying(False)


# ── Recording control ─────────────────────────────────────────────────────────

class RecordingControl:
    """Shared recording state, toggled from the control thread."""
    def __init__(self):
        self._lock     = threading.Lock()
        self._recorder = None

    def start(self, tmp_path: str):
        with self._lock:
            self._recorder = Recorder(tmp_path)
        print(f"\n  ● REC started  (Enter to stop)\n")

    def stop(self) -> "Recorder | None":
        with self._lock:
            rec = self._recorder
            self._recorder = None
        return rec

    def record(self, snap: dict):
        with self._lock:
            if self._recorder:
                self._recorder.record(snap)

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._recorder is not None



# ── Tracker WebSocket listener ────────────────────────────────────────────────

def _windows_host_ip() -> str:
    """Find the Windows host IP reachable from WSL2."""
    import subprocess
    # Prefer the default route gateway — works in both NAT and mirrored mode
    try:
        out = subprocess.check_output(
            ["ip", "route", "show", "default"], text=True)
        for part in out.split():
            if part.count(".") == 3 and part != "0.0.0.0":
                return part
    except Exception:
        pass
    # Fallback: nameserver from resolv.conf
    try:
        with open("/etc/resolv.conf") as f:
            for line in f:
                if line.startswith("nameserver"):
                    ip = line.split()[1].strip()
                    if not ip.startswith("10.255"):   # skip mirror-mode stub
                        return ip
    except OSError:
        pass
    return "localhost"


_NEUTRAL_SNAP = {"buttons": 0, "lx": 0, "ly": 0, "rx": 0, "ry": 0}


class AutoRecordGate:
    """Flag that must be enabled before RACING auto-record activates."""
    def __init__(self):
        self._lock    = threading.Lock()
        self._enabled = False

    def toggle(self):
        with self._lock:
            self._enabled = not self._enabled
            return self._enabled

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled


class PendingReplay:
    """Path of a recording queued to play on next RACING state, or None."""
    def __init__(self):
        self._lock = threading.Lock()
        self._path = None

    def set(self, path: str):
        with self._lock:
            self._path = path

    def take(self) -> "str | None":
        with self._lock:
            p, self._path = self._path, None
            return p

    def cancel(self):
        with self._lock:
            self._path = None

    @property
    def is_set(self) -> bool:
        with self._lock:
            return self._path is not None


def tracker_listener_thread(ws_url: str, rec_ctrl: RecordingControl,
                             pending_replay: PendingReplay,
                             auto_rec: AutoRecordGate,
                             state: ControllerState,
                             stop: threading.Event):
    asyncio.run(_tracker_async(ws_url, rec_ctrl, pending_replay,
                               auto_rec, state, stop))


async def _tracker_async(ws_url: str, rec_ctrl: RecordingControl,
                         pending_replay: PendingReplay,
                         auto_rec: AutoRecordGate,
                         state: ControllerState,
                         stop: threading.Event):
    try:
        import websockets
    except ImportError:
        print("[tracker] websockets not installed — run: pip install websockets")
        return

    current_course = None
    session        = [0]   # mutable counter for auto-named files

    print(f"[tracker] Connecting to {ws_url} …")

    while not stop.is_set():
        try:
            async with websockets.connect(ws_url) as ws:
                print("[tracker] Connected — will auto-record on RACING state.")
                async for raw in ws:
                    if stop.is_set():
                        break
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    if msg.get("type") == "selection_update":
                        if msg.get("course"):
                            current_course = msg["course"]

                    elif msg.get("type") == "screen_change":
                        to_s   = msg.get("to",   "")
                        from_s = msg.get("from", "")

                        if to_s == "RACING":
                            # Fire any pending replay
                            path = pending_replay.take()
                            if path:
                                print(f"[tracker] RACING — starting replay: {path}")
                                def _do_replay(p=path):
                                    replay(state, p)
                                    print("[tracker] Replay finished.\n")
                                threading.Thread(target=_do_replay, daemon=True).start()

                            # Auto-record (only when gate is enabled)
                            elif auto_rec.enabled and not rec_ctrl.is_recording:
                                import os
                                os.makedirs("recordings", exist_ok=True)
                                tmp = f"recordings/.tmp_{int(time.monotonic()*1000)}.json"
                                rec_ctrl.start(tmp)
                                print("[tracker] RACING detected — recording started.")

                        elif from_s == "RACING" and rec_ctrl.is_recording:
                            rec = rec_ctrl.stop()
                            if rec and rec._log:
                                import os
                                session[0] += 1
                                slug  = (current_course or "race").lower().replace(" ", "_")
                                name  = f"{slug}_{session[0]}"
                                path  = f"recordings/{name}.json"
                                rec._path = path
                                rec.save()
                                print(f"[tracker] Race ended — saved as {path}")

        except Exception as exc:
            if not stop.is_set():
                print(f"[tracker] Disconnected ({exc}), retrying in 3s…")
                await asyncio.sleep(3)


def _list_recordings() -> list[str]:
    import os
    if not os.path.isdir("recordings"):
        return []
    return sorted(f for f in os.listdir("recordings")
                  if f.endswith(".json") and not f.startswith("."))


def _resolve_recording(name: str) -> "str | None":
    import os
    candidates = [
        name,
        f"recordings/{name}",
        f"recordings/{name}.json",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def control_thread(rec_ctrl: RecordingControl,
                   state: ControllerState,
                   pending_replay: PendingReplay,
                   auto_rec: AutoRecordGate,
                   stop: threading.Event):
    """
    Reads commands from the WSL2 terminal.

    Commands:
        Enter (empty)       toggle recording on / off
        list                list saved recordings
        play <name>         replay a recording now (gamepad paused during replay)
        play-race <name>    queue a replay to start when RACING state is detected
        auto-rec            toggle auto-record on RACING detection on / off
        <name>              shorthand for play <name>
    """
    import os
    print("  Enter              → start / stop recording")
    print("  list               → list recordings")
    print("  play <name>        → replay a recording now")
    print("  play-race <name>   → replay when RACING state is next detected")
    print("  auto-rec           → toggle auto-record on RACING (currently OFF)")
    print("  Ctrl+C             → quit\n")
    session = 0
    while not stop.is_set():
        try:
            cmd = input().strip()
        except EOFError:
            break

        # ── list ──────────────────────────────────────────────────────────────
        if cmd == "list":
            files = _list_recordings()
            if files:
                for f in files:
                    print(f"    {f}")
            else:
                print("  No recordings found in recordings/")
            continue

        # ── play-race ─────────────────────────────────────────────────────────
        if cmd.startswith("play-race "):
            name = cmd.removeprefix("play-race ").strip()
            path = _resolve_recording(name)
            if path is None:
                print(f"  File not found: {name!r}  (use 'list' to see available)")
                continue
            pending_replay.set(path)
            print(f"  Queued for next RACING state: {path}")
            print("  (type 'play-race cancel' to cancel)\n")
            continue

        if cmd == "play-race cancel":
            pending_replay.cancel()
            print("  Pending replay cancelled.")
            continue

        # ── auto-rec toggle ───────────────────────────────────────────────────
        if cmd == "auto-rec":
            enabled = auto_rec.toggle()
            print(f"  Auto-record on RACING: {'ON' if enabled else 'OFF'}")
            continue

        # ── play / replay ─────────────────────────────────────────────────────
        if cmd.startswith("play ") or (cmd and cmd != ""):
            name = cmd.removeprefix("play ").strip() if cmd.startswith("play ") else cmd
            path = _resolve_recording(name)
            if path is None:
                # Could be a toggle-record intent with accidental input — treat
                # unrecognised non-empty input as a file-not-found warning only
                # if it doesn't look like a bare Enter.
                print(f"  File not found: {name!r}  (use 'list' to see available)")
                continue
            if rec_ctrl.is_recording:
                print("  Stop recording first before replaying.")
                continue
            print(f"\n  ▶ Replaying {path} …")
            replay(state, path)
            print("  Replay finished.\n")
            continue

        # ── toggle recording (empty Enter) ────────────────────────────────────
        if rec_ctrl.is_recording:
            rec = rec_ctrl.stop()
            if rec:
                session += 1
                name = input(f"  Save as (no .json) [session_{session}]: ").strip()
                if not name:
                    name = f"session_{session}"
                path = f"recordings/{name}.json"
                os.makedirs("recordings", exist_ok=True)
                rec._path = path
                rec.save()
                print(f"\n  Enter to START recording  |  play <name> to replay\n")
        else:
            tmp = f"recordings/.tmp_{int(time.monotonic()*1000)}.json"
            os.makedirs("recordings", exist_ok=True)
            rec_ctrl.start(tmp)


# ── Main ──────────────────────────────────────────────────────────────────────

def _parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--reconnect",   default=SWITCH_MAC)
    p.add_argument("--replay",      default=None,  help="Replay a recorded session")
    p.add_argument("--port",        default=LISTEN_PORT, type=int)
    p.add_argument("--tracker-ws",  default=None,  metavar="URL",
                   help="mkw_tracker WebSocket URL for auto-record on RACING "
                        "(e.g. ws://172.24.240.1:8765). "
                        "Use 'auto' to detect Windows host IP automatically.")
    return p.parse_args()


def main():
    args = _parse_args()

    from controller import ProController
    ctrl = ProController()
    print(f"Reconnecting to {args.reconnect}…")
    ctrl.connect(reconnect_addr=args.reconnect)
    print("Connected!\n")

    state          = ControllerState()
    rec_ctrl       = RecordingControl()
    pending_replay = PendingReplay()
    auto_rec       = AutoRecordGate()
    stop           = threading.Event()

    if args.replay:
        # Connect sender so the Switch stays paired, then replay
        threading.Thread(target=sender_thread, args=(ctrl, state, stop),
                         daemon=True).start()
        replay(state, args.replay)
        stop.set()
        ctrl.disconnect()
        return

    # nxbt sender — runs for the whole session
    threading.Thread(target=sender_thread, args=(ctrl, state, stop),
                     daemon=True).start()

    # Recording control — reads commands from this terminal
    threading.Thread(target=control_thread,
                     args=(rec_ctrl, state, pending_replay, auto_rec, stop),
                     daemon=True).start()

    # Optional tracker WebSocket listener — auto-record on RACING
    if args.tracker_ws:
        ws_url = args.tracker_ws
        if ws_url == "auto":
            ws_url = f"ws://{_windows_host_ip()}:8765"
        threading.Thread(target=tracker_listener_thread,
                         args=(ws_url, rec_ctrl, pending_replay, auto_rec,
                               state, stop),
                         daemon=True).start()

    # TCP server — keeps re-accepting if input_server reconnects
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((LISTEN_HOST, args.port))
    server.listen(1)
    server.settimeout(1.0)
    print(f"Listening on port {args.port} — start input_server.py on Windows.\n")

    try:
        while not stop.is_set():
            try:
                conn, addr = server.accept()
            except socket.timeout:
                continue
            print(f"input_server connected from {addr}\n")
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            disc_stop = threading.Event()

            def _recv(c=conn, s=state, r=rec_ctrl, ds=disc_stop):
                try:
                    while not ds.is_set():
                        raw = recv_exact(c, PACKET_SIZE)
                        buttons, lx, ly, rx, ry, lt, rt = struct.unpack(PACKET_FMT, raw)
                        s.update(buttons, lx, ly, rx, ry, lt, rt)
                        r.record(s.snapshot())
                except (ConnectionResetError, OSError):
                    pass
                finally:
                    ds.set()
                    print("input_server disconnected — waiting for reconnect…\n")

            t = threading.Thread(target=_recv, daemon=True)
            t.start()
            disc_stop.wait()   # block until this connection drops
            conn.close()

    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.close()
        # Save any in-progress recording
        rec = rec_ctrl.stop()
        if rec and rec._log:
            rec.save()
        ctrl.disconnect()


if __name__ == "__main__":
    main()
