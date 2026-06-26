"""Real socket round-trip tests for ControllerBridge.

Spins up a tiny in-process TCP echo server that returns canned JSON responses,
then drives ControllerBridge against it.  No nxbt, no WSL2 required.
"""
import json
import socket
import sys
import os
import threading
import pytest

# Make controller_bridge importable from the test suite (lives under tools/autotemplate/).
_AT = os.path.join(os.path.dirname(__file__), "..", "tools", "autotemplate")
if _AT not in sys.path:
    sys.path.insert(0, _AT)

from controller_bridge import ControllerBridge


# ── Tiny in-process echo server ───────────────────────────────────────────────

class _EchoServer:
    """Accepts one connection, reads newline-delimited JSON commands, dispatches
    to a canned response table, sends the response back, then closes."""

    def __init__(self, responses: dict):
        """responses maps command type -> response dict to send back."""
        self._responses = responses
        self._received: list = []   # list of decoded command dicts in order
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(1)
        self.port = self._srv.getsockname()[1]
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            conn, _ = self._srv.accept()
            buf = b""
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
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    self._received.append(msg)
                    t = msg.get("type", "")
                    resp = self._responses.get(t, {"ok": True, "error": ""})
                    conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))
            conn.close()
        except OSError:
            pass
        finally:
            self._srv.close()

    @property
    def received(self):
        return list(self._received)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_press_sends_correct_json_and_parses_ok():
    """press("A", duration=0.5) must send exact JSON and parse {"ok": true}."""
    srv = _EchoServer({"press": {"ok": True, "error": ""}})
    bridge = ControllerBridge(host="127.0.0.1", port=srv.port)
    bridge.connect()

    result = bridge.press("A", duration=0.5, after=0.0)

    assert result is True
    assert len(srv.received) == 1
    cmd = srv.received[0]
    assert cmd["type"]     == "press"
    assert cmd["button"]   == "A"
    assert cmd["duration"] == 0.5
    bridge.close()


def test_get_status_parses_connected_and_mac():
    """get_status() must return {"connected": True, "mac": "AA:BB:CC:DD:EE:FF"}."""
    srv = _EchoServer({
        "get_status": {
            "ok": True, "error": "",
            "connected": True, "mac": "AA:BB:CC:DD:EE:FF",
        }
    })
    bridge = ControllerBridge(host="127.0.0.1", port=srv.port)
    bridge.connect()

    status = bridge.get_status()

    assert status["connected"] is True
    assert status["mac"] == "AA:BB:CC:DD:EE:FF"
    bridge.close()


def test_ping_returns_true():
    srv = _EchoServer({"ping": {"ok": True, "error": ""}})
    bridge = ControllerBridge(host="127.0.0.1", port=srv.port)
    bridge.connect()
    assert bridge.ping() is True
    bridge.close()


def test_rstick_down_sends_correct_type():
    srv = _EchoServer({"rstick_down": {"ok": True, "error": ""}})
    bridge = ControllerBridge(host="127.0.0.1", port=srv.port)
    bridge.connect()
    result = bridge.rstick_down()
    assert result is True
    assert srv.received[0]["type"] == "rstick_down"
    bridge.close()


def test_hold_sends_correct_fields():
    srv = _EchoServer({"hold": {"ok": True, "error": ""}})
    bridge = ControllerBridge(host="127.0.0.1", port=srv.port)
    bridge.connect()
    result = bridge.hold("B", duration=2.0)
    assert result is True
    cmd = srv.received[0]
    assert cmd["type"]     == "hold"
    assert cmd["button"]   == "B"
    assert cmd["duration"] == 2.0
    bridge.close()


def test_press_many_sends_buttons_list():
    srv = _EchoServer({"press_many": {"ok": True, "error": ""}})
    bridge = ControllerBridge(host="127.0.0.1", port=srv.port)
    bridge.connect()
    result = bridge.press_many(["A", "B"], duration=0.1)
    assert result is True
    cmd = srv.received[0]
    assert cmd["type"]    == "press_many"
    assert cmd["buttons"] == ["A", "B"]
    bridge.close()


def test_failed_response_returns_false():
    """If the agent returns ok=false, _send_command should return False."""
    srv = _EchoServer({"press": {"ok": False, "error": "controller not connected"}})
    bridge = ControllerBridge(host="127.0.0.1", port=srv.port)
    bridge.connect()
    result = bridge.press("A")
    assert result is False
    bridge.close()


def test_get_mac_returns_mac_string():
    srv = _EchoServer({"get_mac": {"ok": True, "error": "", "mac": "E0:EF:BF:03:74:19"}})
    bridge = ControllerBridge(host="127.0.0.1", port=srv.port)
    bridge.connect()
    mac = bridge.get_mac()
    assert mac == "E0:EF:BF:03:74:19"
    bridge.close()
