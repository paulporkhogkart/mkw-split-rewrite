"""
Full sequence test with reconnect after console restart.

Usage:
    sudo ~/autotemplate-venv/bin/python3 test_sequence.py
"""
from controller import ProController
import time

SWITCH_MAC = "E0:EF:BF:03:74:19"

ctrl = ProController()

print(f"Reconnecting to {SWITCH_MAC}…")
ctrl.connect(reconnect_addr=SWITCH_MAC)
print("Connected!")

time.sleep(0.5)

print("DPAD_DOWN")
ctrl.press("DPAD_DOWN", duration=0.05)
time.sleep(0.5)

print("DPAD_LEFT")
ctrl.press("DPAD_LEFT", duration=0.05)
time.sleep(0.5)

print("DPAD_LEFT")
ctrl.press("DPAD_LEFT", duration=0.05)
time.sleep(0.5)

print("A")
ctrl.press("A")
time.sleep(1.5)

for i in range(16):
    print(f"DPAD_DOWN ({i+1}/16)")
    ctrl.press("DPAD_DOWN", duration=0.05)
    time.sleep(0.5)

print("A")
ctrl.press("A")
time.sleep(1.5)

for i in range(5):
    print(f"DPAD_DOWN ({i+1}/5)")
    ctrl.press("DPAD_DOWN", duration=0.05)
    time.sleep(0.5)

print("A")
ctrl.press("A")
time.sleep(1.5)

print("DPAD_DOWN")
ctrl.press("DPAD_DOWN", duration=0.05)
time.sleep(0.5)

print("A")
ctrl.press("A")
time.sleep(1.5)

print("A")
ctrl.press("A")

print("Waiting 20s for console to restart…")
time.sleep(20)

print("Reconnecting after restart…")
ctrl.disconnect()
ctrl.connect(reconnect_addr=SWITCH_MAC)
print("Reconnected!")

time.sleep(0.5)

print("A")
ctrl.press("A")

ctrl.disconnect()
print("Done.")
