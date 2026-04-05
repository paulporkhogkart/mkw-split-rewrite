"""
Tests reconnecting to an already-paired Switch without pairing mode.

Usage:
    sudo ~/autotemplate-venv/bin/python3 test_reconnect.py
"""
from controller import ProController
import time

SWITCH_MAC = "E0:EF:BF:03:74:19"

ctrl = ProController()

print(f"Reconnecting to {SWITCH_MAC}…")
ctrl.connect(reconnect_addr=SWITCH_MAC)
print("Connected!")

time.sleep(1)

for _ in range(3):
    print("DPAD_LEFT")
    ctrl.press("DPAD_LEFT", duration=0.05)
    time.sleep(0.3)
    print("DPAD_RIGHT")
    ctrl.press("DPAD_RIGHT", duration=0.05)
    time.sleep(0.3)

ctrl.disconnect()
print("Done.")
