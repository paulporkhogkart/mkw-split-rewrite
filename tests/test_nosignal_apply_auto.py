"""_apply_nosignal_auto: in auto mode (no tell_tree_NO_SIGNAL override) swap the
region from the device name; in manual mode leave it and report manual."""
import json
from mkw_tracker.database.config_repo import set_config
from mkw_tracker.detection.screen import ScreenDetector, Screen, NO_SIGNAL_PRESETS
from mkw_tracker.main import _apply_nosignal_auto


class _Settings:
    def __init__(self, device): self._device = device
    def get(self, key, default=None):
        return self._device if key == "camera_device" else default


class _Ipc:
    def __init__(self): self.events = []
    def emit(self, e): self.events.append(e)


def _modes(ipc):
    return [json.loads(e) for e in ipc.events if json.loads(e).get("type") == "nosignal_mode"]


def test_apply_auto_picks_preset_from_device(memdb):
    d = ScreenDetector(); ipc = _Ipc()
    _apply_nosignal_auto(_Settings("UGREEN 25773"), d, ipc)
    region = d._tells_by_screen[Screen.NO_SIGNAL].groups[0][0]
    assert region.roi == NO_SIGNAL_PRESETS["ugreen"]["roi"]
    assert _modes(ipc)[-1] == {"type": "nosignal_mode", "auto": True, "brand": "ugreen"}


def test_apply_auto_unknown_device_keeps_elgato_default(memdb):
    d = ScreenDetector(); ipc = _Ipc()
    _apply_nosignal_auto(_Settings("Random USB Cam"), d, ipc)
    region = d._tells_by_screen[Screen.NO_SIGNAL].groups[0][0]
    assert region.roi == NO_SIGNAL_PRESETS["elgato"]["roi"]
    assert _modes(ipc)[-1] == {"type": "nosignal_mode", "auto": True, "brand": None}


def test_apply_auto_manual_when_override_present(memdb):
    set_config("tell_tree_NO_SIGNAL", "{}")            # simulate a hand edit
    d = ScreenDetector(); ipc = _Ipc()
    before = d._tells_by_screen[Screen.NO_SIGNAL].groups[0][0].roi
    _apply_nosignal_auto(_Settings("UGREEN 25773"), d, ipc)
    after = d._tells_by_screen[Screen.NO_SIGNAL].groups[0][0].roi
    assert after == before                              # manual: not swapped
    assert _modes(ipc)[-1] == {"type": "nosignal_mode", "auto": False, "brand": None}
