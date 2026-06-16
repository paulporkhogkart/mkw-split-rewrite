import json
from mkw_tracker.ipc.protocol import emit_nosignal_mode


def test_emit_nosignal_mode_auto_with_brand():
    assert json.loads(emit_nosignal_mode(True, "ugreen")) == {
        "type": "nosignal_mode", "auto": True, "brand": "ugreen"}


def test_emit_nosignal_mode_manual():
    assert json.loads(emit_nosignal_mode(False)) == {
        "type": "nosignal_mode", "auto": False, "brand": None}
