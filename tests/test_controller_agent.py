"""Unit tests for controller_agent.dispatch().

Tests the dispatch function directly (no socket, no nxbt, no WSL2).
Uses fakes/patches for full_runner._press and full_runner._hold to avoid
real hardware calls or sleeps.
"""
import sys
import os
import threading
import pytest

# Make controller_agent importable on Windows (no nxbt at module scope).
_AT = os.path.join(os.path.dirname(__file__), "..", "tools", "autotemplate")
if _AT not in sys.path:
    sys.path.insert(0, _AT)

from controller_agent import _CtrlStateHolder, dispatch, _AntiSpinState


# ── Fakes ─────────────────────────────────────────────────────────────────────

class _FakeCtrl:
    """Fake ProController — records macro calls."""
    def __init__(self, mac="AA:BB:CC:DD:EE:FF"):
        self._mac = mac
        self.macro_calls: list = []

    def get_mac(self):
        return self._mac

    def macro(self, text: str):
        self.macro_calls.append(text)


class _FakeState:
    """Fake ControllerState — records replay_update calls."""
    def __init__(self):
        self.frames_sent   = 1000   # non-zero so _press frame math works
        self.replay_calls: list = []

    def replay_update(self, buttons, lx, ly, rx, ry):
        self.replay_calls.append((buttons, lx, ly, rx, ry))

    def snapshot(self):
        return {"buttons": 0, "lx": 0, "ly": 0, "rx": 0, "ry": 0}


def _make_holder(ctrl=None, state=None):
    h = _CtrlStateHolder()
    if ctrl is not None or state is not None:
        h.set(ctrl or _FakeCtrl(), state or _FakeState())
    return h


# ── ping ──────────────────────────────────────────────────────────────────────

def test_ping_returns_ok():
    h = _make_holder()
    resp = dispatch({"type": "ping"}, h)
    assert resp["ok"] is True


# ── get_status ────────────────────────────────────────────────────────────────

def test_get_status_not_connected():
    h = _make_holder()   # no ctrl/state set
    resp = dispatch({"type": "get_status"}, h)
    assert resp["ok"] is True
    assert resp["connected"] is False
    assert resp["mac"] == ""


def test_get_status_connected():
    ctrl  = _FakeCtrl(mac="AA:BB:CC:DD:EE:FF")
    state = _FakeState()
    h = _make_holder(ctrl=ctrl, state=state)
    resp = dispatch({"type": "get_status"}, h)
    assert resp["ok"] is True
    assert resp["connected"] is True
    assert resp["mac"] == "AA:BB:CC:DD:EE:FF"


# ── get_mac ───────────────────────────────────────────────────────────────────

def test_get_mac_not_connected():
    h = _make_holder()
    resp = dispatch({"type": "get_mac"}, h)
    assert resp["ok"] is True
    assert resp["mac"] == ""


def test_get_mac_connected():
    ctrl = _FakeCtrl(mac="E0:EF:BF:03:74:19")
    h = _make_holder(ctrl=ctrl)
    resp = dispatch({"type": "get_mac"}, h)
    assert resp["ok"] is True
    assert resp["mac"] == "E0:EF:BF:03:74:19"


# ── wait ──────────────────────────────────────────────────────────────────────

def test_wait_returns_ok(monkeypatch):
    import time
    slept = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
    h = _make_holder()
    resp = dispatch({"type": "wait", "seconds": 0.0}, h)
    assert resp["ok"] is True


# ── press (controller required) ───────────────────────────────────────────────

def test_press_not_connected_returns_error():
    h = _make_holder()   # no controller
    resp = dispatch({"type": "press", "button": "A"}, h)
    assert resp["ok"] is False
    assert "not connected" in resp["error"]


def test_press_calls_full_runner_press(monkeypatch):
    """dispatch press must call full_runner._press with correct args."""
    calls = []

    # Patch _press inside the full_runner module referenced lazily by dispatch.
    import importlib, full_runner as _fr
    monkeypatch.setattr(_fr, "_press", lambda state, btn, duration, after, dry_run: calls.append(
        (btn, duration, after, dry_run)
    ))

    state = _FakeState()
    h = _make_holder(state=state)
    resp = dispatch({"type": "press", "button": "A", "duration": 0.5, "after": 0.1}, h)
    assert resp["ok"] is True
    assert len(calls) == 1
    btn, dur, after, dry = calls[0]
    assert btn   == "A"
    assert dur   == 0.5
    assert after == 0.1
    assert dry   is False


def test_press_many_not_connected():
    h = _make_holder()
    resp = dispatch({"type": "press_many", "buttons": ["A", "B"]}, h)
    assert resp["ok"] is False


def test_press_many_calls_press_for_each(monkeypatch):
    calls = []
    import full_runner as _fr
    monkeypatch.setattr(_fr, "_press", lambda state, btn, duration, after, dry_run: calls.append(btn))

    state = _FakeState()
    h = _make_holder(state=state)
    resp = dispatch({"type": "press_many", "buttons": ["X", "Y", "B"]}, h)
    assert resp["ok"] is True
    assert calls == ["X", "Y", "B"]


# ── hold ──────────────────────────────────────────────────────────────────────

def test_hold_not_connected():
    h = _make_holder()
    resp = dispatch({"type": "hold", "button": "B", "duration": 1.0}, h)
    assert resp["ok"] is False


def test_hold_calls_full_runner_hold(monkeypatch):
    calls = []
    import full_runner as _fr
    monkeypatch.setattr(_fr, "_hold", lambda state, btn, dur, dry_run: calls.append((btn, dur, dry_run)))

    state = _FakeState()
    h = _make_holder(state=state)
    resp = dispatch({"type": "hold", "button": "ZL", "duration": 2.0}, h)
    assert resp["ok"] is True
    assert calls == [("ZL", 2.0, False)]


# ── rstick_down ───────────────────────────────────────────────────────────────

def test_rstick_down_not_connected():
    h = _make_holder()
    resp = dispatch({"type": "rstick_down"}, h)
    assert resp["ok"] is False


def test_rstick_down_calls_replay_update():
    state = _FakeState()
    h = _make_holder(state=state)
    resp = dispatch({"type": "rstick_down"}, h)
    assert resp["ok"] is True
    # Must have called replay_update with R-stick ry=-127
    assert any(call[4] == -127 for call in state.replay_calls), \
        f"Expected replay_update with ry=-127, got: {state.replay_calls}"


# ── macro ─────────────────────────────────────────────────────────────────────

def test_macro_not_connected():
    h = _make_holder()
    resp = dispatch({"type": "macro", "text": "A 0.1s"}, h)
    assert resp["ok"] is False


def test_macro_calls_ctrl_macro():
    ctrl  = _FakeCtrl()
    state = _FakeState()
    h = _make_holder(ctrl=ctrl, state=state)
    resp = dispatch({"type": "macro", "text": "A 0.1s\n0.5s"}, h)
    assert resp["ok"] is True
    assert ctrl.macro_calls == ["A 0.1s\n0.5s"]


# ── unknown type ──────────────────────────────────────────────────────────────

def test_unknown_type_returns_error():
    h = _make_holder()
    resp = dispatch({"type": "frobnicate"}, h)
    assert resp["ok"] is False
    assert "unknown" in resp["error"].lower()


def test_missing_type_returns_error():
    h = _make_holder()
    resp = dispatch({}, h)
    assert resp["ok"] is False


# ── _AntiSpinState ────────────────────────────────────────────────────────────

def test_antispin_ry_pulses_down_then_neutral():
    """antispin_ry pulses: full DOWN for the first DUTY of each PERIOD, neutral after."""
    from controller_agent import antispin_ry, _ANTISPIN_PERIOD, _ANTISPIN_DUTY
    down_end = _ANTISPIN_PERIOD * _ANTISPIN_DUTY
    assert antispin_ry(0.0) == -127                       # start of cycle: DOWN
    assert antispin_ry(down_end - 1e-6) == -127           # just before the switch: DOWN
    assert antispin_ry(down_end + 1e-6) == 0              # after the switch: neutral
    assert antispin_ry(_ANTISPIN_PERIOD) == -127          # next cycle: DOWN again


def test_antispin_state_forces_pulse_over_replay_and_passes_buttons():
    """_AntiSpinState.snapshot() forces the anti-spin pulse value regardless of what
    replay_update writes (so _press zeroing the sticks never reaches the wire), while
    buttons still go through."""
    from switch_bridge import BIT_A
    s = _AntiSpinState()
    s.replay_update(0, 0, 0, 0, 99)              # replay writes a sentinel ry the pulse never uses
    assert s.snapshot()["ry"] in (-127, 0)        # snapshot overrides it with the pulse value
    s.replay_update(BIT_A, 0, 0, 0, 99)          # a press zeros the sticks in state
    assert s.snapshot()["ry"] in (-127, 0)        # wire still carries the anti-spin pulse, not 99
    assert s.snapshot()["buttons"] == BIT_A        # button still goes through


# ── Import clean on Windows (no nxbt) ────────────────────────────────────────

def test_controller_agent_imports_without_nxbt():
    """Verify that importing controller_agent raises no ImportError on Windows.
    Since we already imported it at the top of this file without error, this
    is satisfied by the test module loading successfully.  Explicitly assert
    the dispatch symbol is callable as a sanity check.
    """
    assert callable(dispatch)
