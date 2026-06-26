"""
Thin wrapper around nxbt for Pro Controller emulation.

nxbt docs: https://github.com/Brikwerk/nxbt
"""
import time
import nxbt

# Button name → nxbt.Buttons constant
BUTTONS = {
    "A": nxbt.Buttons.A, "B": nxbt.Buttons.B,
    "X": nxbt.Buttons.X, "Y": nxbt.Buttons.Y,
    "L":  nxbt.Buttons.L,  "R":  nxbt.Buttons.R,
    "ZL": nxbt.Buttons.ZL, "ZR": nxbt.Buttons.ZR,
    "PLUS":  nxbt.Buttons.PLUS,  "MINUS": nxbt.Buttons.MINUS,
    "HOME":  nxbt.Buttons.HOME,  "CAPTURE": nxbt.Buttons.CAPTURE,
    "DPAD_UP":    nxbt.Buttons.DPAD_UP,
    "DPAD_DOWN":  nxbt.Buttons.DPAD_DOWN,
    "DPAD_LEFT":  nxbt.Buttons.DPAD_LEFT,
    "DPAD_RIGHT": nxbt.Buttons.DPAD_RIGHT,
    "L_STICK": nxbt.Buttons.L_STICK_PRESS,
    "R_STICK": nxbt.Buttons.R_STICK_PRESS,
}


class ProController:
    """
    Pairs as a Nintendo Switch Pro Controller via Bluetooth (BlueZ / nxbt).

    Usage:
        ctrl = ProController()
        ctrl.connect()          # blocks until Switch pairs
        ctrl.press("A")
        ctrl.press("DPAD_RIGHT", duration=0.05)
        ctrl.wait(1.0)
        ctrl.disconnect()
    """

    def __init__(self, adapter: str = "hci0"):
        self._adapter_path = f"/org/bluez/{adapter}"
        self._nx = nxbt.Nxbt()
        self._idx = None
        self.switch_mac = None   # set on connect; returned by get_mac()

    # ── Connection ──────────────────────────────────────────────────────────

    def connect(self, reconnect_addr: str | None = None) -> None:
        """
        Create the virtual controller and wait for the Switch to pair.

        reconnect_addr: Bluetooth MAC of the Switch to reconnect to a previously
                        paired device instead of entering pairing mode again.
        """
        kwargs = dict(
            controller_type=nxbt.PRO_CONTROLLER,
            adapter_path=self._adapter_path,
            colour_body=(74, 74, 74),
            colour_buttons=(28, 28, 28),
        )
        if reconnect_addr:
            kwargs["reconnect_address"] = reconnect_addr

        self._idx = self._nx.create_controller(**kwargs)
        print("Waiting for Switch to connect… (open Change Grip/Order on Switch)")
        self._nx.wait_for_connection(self._idx)
        self.switch_mac = reconnect_addr
        print(f"Switch connected. MAC={self.switch_mac or 'unknown'}")
        # Short settle time so the Switch registers the controller fully
        time.sleep(1.0)

    def disconnect(self) -> None:
        if self._idx is not None:
            self._nx.remove_controller(self._idx)
            self._idx = None

    def get_mac(self):
        """The Switch MAC we connected to (the reconnect address), or None."""
        return self.switch_mac

    # ── Inputs ─────────────────────────────────────────────────────────────

    def press(self, button: str, duration: float = 0.1, after: float = 0.05) -> None:
        """Press and release a single button."""
        if self._idx is None:
            raise RuntimeError("Controller not connected")
        btn = BUTTONS.get(button.upper())
        if btn is None:
            raise ValueError(f"Unknown button: {button!r}. Valid: {list(BUTTONS)}")
        self._nx.press_buttons(self._idx, [btn], down=duration, up=after, block=True)

    def press_many(self, *buttons: str, duration: float = 0.1, after: float = 0.05) -> None:
        """Press multiple buttons simultaneously."""
        if self._idx is None:
            raise RuntimeError("Controller not connected")
        btns = [BUTTONS[b.upper()] for b in buttons]
        self._nx.press_buttons(self._idx, btns, down=duration, up=after, block=True)

    def macro(self, text: str) -> None:
        """
        Run a raw nxbt macro string, e.g.:
            A 0.1s
            0.2s
            DPAD_RIGHT 0.05s
        """
        if self._idx is None:
            raise RuntimeError("Controller not connected")
        self._nx.macro(self._idx, text, block=True)

    def wait(self, seconds: float) -> None:
        time.sleep(seconds)

    # ── Context manager ─────────────────────────────────────────────────────

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.disconnect()
