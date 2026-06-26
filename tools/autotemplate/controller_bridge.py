"""TCP client (Windows): sends JSON commands to the WSL2 controller_agent.

Modelled on nxauto's ControllerBridge (autoflow_engine/actions/bridge.py).
Auto-reconnects in a background thread when the connection drops.

Module-level imports are stdlib only — safe to import on Windows without nxbt.
"""

import json
import socket
import sys
import threading
import time
from typing import Optional


class ControllerBridge:
    """Non-blocking TCP client to the WSL2 controller_agent.  Auto-reconnects on disconnect."""

    def __init__(self, host: str = "127.0.0.1", port: int = 7878,
                 reconnect_interval: float = 2.0):
        self._host               = host
        self._port               = port
        self._reconnect_interval = reconnect_interval
        self._sock: Optional[socket.socket] = None
        self._lock      = threading.Lock()
        self._connected = False

    # ── Connection lifecycle ──────────────────────────────────────────────────

    def connect(self) -> bool:
        """Try once to connect.  Returns True on success."""
        with self._lock:
            if self._connected:
                return True
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(3.0)
                s.connect((self._host, self._port))
                s.settimeout(10.0)
                self._sock      = s
                self._connected = True
                print(f"[Bridge] Connected to controller agent at "
                      f"{self._host}:{self._port}", file=sys.stderr)
                return True
            except OSError as exc:
                print(f"[Bridge] Connection failed: {exc}", file=sys.stderr)
                self._sock      = None
                self._connected = False
                return False

    def close(self) -> None:
        with self._lock:
            self._connected = False
            if self._sock:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None

    def is_connected(self) -> bool:
        return self._connected

    def start_reconnect_loop(self) -> None:
        """Spawn a daemon thread that keeps trying to reconnect when disconnected."""
        def _loop():
            while True:
                if not self._connected:
                    self.connect()
                time.sleep(self._reconnect_interval)
        threading.Thread(target=_loop, daemon=True, name="bridge-reconnect").start()

    # ── Raw command send ──────────────────────────────────────────────────────

    def _send_command_raw(self, cmd: dict) -> Optional[dict]:
        """Send a command dict and return the full response dict. Thread-safe."""
        with self._lock:
            if not self._connected or self._sock is None:
                return None
            try:
                line = json.dumps(cmd) + "\n"
                self._sock.sendall(line.encode("utf-8"))
                buf = b""
                while b"\n" not in buf:
                    chunk = self._sock.recv(256)
                    if not chunk:
                        raise OSError("Connection closed by agent")
                    buf += chunk
                return json.loads(buf.split(b"\n")[0])
            except (OSError, json.JSONDecodeError) as exc:
                print(f"[Bridge] Command failed: {exc}", file=sys.stderr)
                self._connected = False
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None
                return None

    def _send_command(self, cmd: dict) -> bool:
        """Send a command dict and return True if response has ok=true."""
        resp = self._send_command_raw(cmd)
        return bool(resp and resp.get("ok", False))

    # ── High-level methods ────────────────────────────────────────────────────

    def press(self, button: str, duration: float = 0.1, after: float = 0.05) -> bool:
        return self._send_command({
            "type": "press", "button": button,
            "duration": duration, "after": after,
        })

    def press_many(self, buttons: list, duration: float = 0.1, after: float = 0.05) -> bool:
        return self._send_command({
            "type": "press_many", "buttons": buttons,
            "duration": duration, "after": after,
        })

    def hold(self, button: str, duration: float = 1.0) -> bool:
        return self._send_command({
            "type": "hold", "button": button, "duration": duration,
        })

    def rstick_down(self) -> bool:
        """Re-assert R-stick DOWN on the agent (idempotent anti-spin reset)."""
        return self._send_command({"type": "rstick_down"})

    def antispin(self, on: bool) -> bool:
        """Hold the R-stick DOWN on the agent while `on` (kart-select anti-spin); release otherwise."""
        return self._send_command({"type": "antispin", "on": bool(on)})

    def wait(self, seconds: float) -> bool:
        return self._send_command({"type": "wait", "seconds": seconds})

    def macro(self, text: str) -> bool:
        return self._send_command({"type": "macro", "text": text})

    def ping(self) -> bool:
        return self._send_command({"type": "ping"})

    def get_status(self) -> dict:
        """Returns {"connected": bool, "mac": str}.  Safe to call before nxbt is ready."""
        resp = self._send_command_raw({"type": "get_status"})
        if resp and resp.get("ok"):
            return {
                "connected": bool(resp.get("connected")),
                "mac":       resp.get("mac") or "",
            }
        return {"connected": False, "mac": ""}

    def get_mac(self) -> str:
        resp = self._send_command_raw({"type": "get_mac"})
        return (resp.get("mac") or "") if resp and resp.get("ok") else ""
