"""
Quick test — pairs with Switch 2 as a Pro Controller and sends a few buttons.

Usage:
    python3 test_controller.py

Before running:
  - On Switch 2: HOME → Controllers → Change Grip/Order  (puts it in pairing mode)
  - In WSL2: sudo systemctl start bluetooth
  - Bluetooth adapter must be forwarded via usbipd-win (hciconfig should show hci0)
"""
from controller import ProController
import time

ctrl = ProController()

print("Connecting to Switch 2 (make sure it is in pairing mode)…")
ctrl.connect()
print("Connected!")

time.sleep(2)

print("Pressing A…")
ctrl.press("A")
time.sleep(0.5)

print("Pressing B…")
ctrl.press("B")
time.sleep(0.5)

print("Pressing DPAD_RIGHT…")
ctrl.press("DPAD_RIGHT", duration=0.05)
time.sleep(0.5)

print("\nRun in another terminal:  bluetoothctl devices")
input("Press Enter to disconnect…")

ctrl.disconnect()
print("Done — if the Switch registered those presses, everything is working.")
