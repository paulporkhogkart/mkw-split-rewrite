"""Thin wrapper over controller_bridge.ControllerBridge for the on-screen cluster."""

_BUTTONS = {
    "up": "DPAD_UP", "down": "DPAD_DOWN", "left": "DPAD_LEFT", "right": "DPAD_RIGHT",
    "a": "A", "b": "B", "plus": "PLUS", "home": "HOME",
}


def to_button(label):
    return _BUTTONS[label]


class ManualController:
    def __init__(self, bridge):
        self._bridge = bridge

    def press(self, label):
        return self._bridge.press(to_button(label))

    def status(self):
        return self._bridge.get_status()

    def close(self):
        self._bridge.close()
