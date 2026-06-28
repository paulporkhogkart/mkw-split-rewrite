import pytest
from manual import ManualController, to_button


class FakeBridge:
    def __init__(self): self.pressed = []; self.closed = False
    def press(self, button, **kw): self.pressed.append(button); return True
    def get_status(self): return {"connected": True, "mac": "AA:BB"}
    def close(self): self.closed = True


@pytest.mark.parametrize("label,btn", [
    ("up", "DPAD_UP"), ("down", "DPAD_DOWN"), ("left", "DPAD_LEFT"),
    ("right", "DPAD_RIGHT"), ("a", "A"), ("b", "B"), ("plus", "PLUS"), ("home", "HOME"),
])
def test_to_button(label, btn):
    assert to_button(label) == btn


def test_press_maps_and_forwards():
    b = FakeBridge(); m = ManualController(b)
    assert m.press("up") is True
    assert b.pressed == ["DPAD_UP"]


def test_status_and_close():
    b = FakeBridge(); m = ManualController(b)
    assert m.status()["connected"] is True
    m.close(); assert b.closed is True
