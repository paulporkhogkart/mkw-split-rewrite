"""
Windows-side gamepad reader. Uses XInput (built into Windows, no deps)
to read the 8BitDo Pro 2 in Xbox mode and streams state to the WSL2 bridge.

No pip installs required — ctypes is stdlib.

Usage:
    windows-venv\Scripts\python.exe input_server.py
    windows-venv\\Scripts\\python.exe input_server.py --host 172.x.x.x  # explicit WSL2 IP
    windows-venv\Scripts\python.exe input_server.py --debug            # print raw values

Find your WSL2 IP if localhost doesn't work:
    In WSL2: ip addr show eth0 | grep "inet "
"""
import argparse
import ctypes
import socket
import struct
import time
import sys

# ── XInput ────────────────────────────────────────────────────────────────────

class _GAMEPAD(ctypes.Structure):
    _fields_ = [
        ("wButtons",      ctypes.c_ushort),
        ("bLeftTrigger",  ctypes.c_ubyte),
        ("bRightTrigger", ctypes.c_ubyte),
        ("sThumbLX",      ctypes.c_short),
        ("sThumbLY",      ctypes.c_short),
        ("sThumbRX",      ctypes.c_short),
        ("sThumbRY",      ctypes.c_short),
    ]

class _STATE(ctypes.Structure):
    _fields_ = [
        ("dwPacketNumber", ctypes.c_ulong),
        ("Gamepad",        _GAMEPAD),
    ]

try:
    _xi = ctypes.windll.xinput1_4
except AttributeError:
    _xi = ctypes.windll.xinput9_1_0

# XInput button bitmasks
XI_DPAD_UP    = 0x0001
XI_DPAD_DOWN  = 0x0002
XI_DPAD_LEFT  = 0x0004
XI_DPAD_RIGHT = 0x0008
XI_START      = 0x0010
XI_BACK       = 0x0020
XI_LB         = 0x0100
XI_RB         = 0x0200
XI_A          = 0x1000
XI_B          = 0x2000
XI_X          = 0x4000
XI_Y          = 0x8000
XI_HOME       = 0x0400   # guide button (may not work on all drivers)

TRIGGER_THRESHOLD = 30   # 0–255

# ── Protocol (must match switch_bridge.py) ────────────────────────────────────
# 7 bytes: uint16 buttons, int8 lx, ly, rx, ry, lt, rt
PACKET_FMT  = "!Hbbbbbb"
PACKET_SIZE = struct.calcsize(PACKET_FMT)

BIT_A       = 1 << 0
BIT_B       = 1 << 1
BIT_X       = 1 << 2
BIT_Y       = 1 << 3
BIT_L       = 1 << 4
BIT_R       = 1 << 5
BIT_ZL      = 1 << 6
BIT_ZR      = 1 << 7
BIT_PLUS    = 1 << 8
BIT_MINUS   = 1 << 9
BIT_HOME    = 1 << 10
BIT_CAPTURE = 1 << 11
BIT_DPAD_U  = 1 << 12
BIT_DPAD_D  = 1 << 13
BIT_DPAD_L  = 1 << 14
BIT_DPAD_R  = 1 << 15


def _clamp127(v: int, maxval: int = 32767) -> int:
    return max(-127, min(127, int(v * 127 / maxval)))


def read_state(controller_index: int = 0) -> tuple | None:
    state = _STATE()
    ret   = _xi.XInputGetState(controller_index, ctypes.byref(state))
    if ret != 0:   # ERROR_SUCCESS = 0; anything else = not connected
        return None

    gp = state.Gamepad
    xi = gp.wButtons

    buttons = 0
    if xi & XI_A:         buttons |= BIT_A
    if xi & XI_B:         buttons |= BIT_B
    if xi & XI_X:         buttons |= BIT_X
    if xi & XI_Y:         buttons |= BIT_Y
    if xi & XI_LB:        buttons |= BIT_L
    if xi & XI_RB:        buttons |= BIT_R
    if xi & XI_START:     buttons |= BIT_PLUS
    if xi & XI_BACK:      buttons |= BIT_MINUS
    if xi & XI_HOME:      buttons |= BIT_HOME
    if xi & XI_DPAD_UP:   buttons |= BIT_DPAD_U
    if xi & XI_DPAD_DOWN: buttons |= BIT_DPAD_D
    if xi & XI_DPAD_LEFT: buttons |= BIT_DPAD_L
    if xi & XI_DPAD_RIGHT:buttons |= BIT_DPAD_R

    if gp.bLeftTrigger  > TRIGGER_THRESHOLD: buttons |= BIT_ZL
    if gp.bRightTrigger > TRIGGER_THRESHOLD: buttons |= BIT_ZR

    lx = _clamp127(gp.sThumbLX)
    ly = _clamp127(gp.sThumbLY)
    rx = _clamp127(gp.sThumbRX)
    ry = _clamp127(gp.sThumbRY)
    lt = gp.bLeftTrigger  >> 1   # 0–255 → 0–127
    rt = gp.bRightTrigger >> 1

    return buttons, lx, ly, rx, ry, lt, rt


def debug_loop():
    print("Debug mode — press Ctrl+C to quit.\n")
    last = None
    while True:
        state = read_state()
        if state is None:
            print("Controller not detected (XInput index 0)")
            time.sleep(1)
            continue
        if state != last:
            buttons, lx, ly, rx, ry, lt, rt = state
            btn_names = []
            for mask, name in [
                (BIT_A,"A"),(BIT_B,"B"),(BIT_X,"X"),(BIT_Y,"Y"),
                (BIT_L,"L"),(BIT_R,"R"),(BIT_ZL,"ZL"),(BIT_ZR,"ZR"),
                (BIT_PLUS,"PLUS"),(BIT_MINUS,"MINUS"),(BIT_HOME,"HOME"),
                (BIT_DPAD_U,"↑"),(BIT_DPAD_D,"↓"),(BIT_DPAD_L,"←"),(BIT_DPAD_R,"→"),
            ]:
                if buttons & mask:
                    btn_names.append(name)
            print(f"  [{' '.join(btn_names) or '-'}]"
                  f"  L({lx:+4d},{ly:+4d})  R({rx:+4d},{ry:+4d})"
                  f"  LT={lt}  RT={rt}")
            last = state
        time.sleep(0.008)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host",  default="localhost")
    p.add_argument("--port",  default=7777, type=int)
    p.add_argument("--index", default=0, type=int, help="XInput controller index (0–3)")
    p.add_argument("--debug", action="store_true")
    args = p.parse_args()

    if args.debug:
        debug_loop()
        return

    # Verify controller is connected before bothering with TCP
    if read_state(args.index) is None:
        sys.exit(f"Controller not found at XInput index {args.index}. "
                 f"Is the 8BitDo on and in Xbox mode?")

    print(f"Controller detected at index {args.index}.")
    print(f"Connecting to {args.host}:{args.port}…")
    sock = socket.create_connection((args.host, args.port))
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    print("Connected. Streaming — Ctrl+C to stop.\n")

    last_state = None
    try:
        while True:
            state = read_state(args.index)
            if state is None:
                print("Controller disconnected.")
                break
            if state != last_state:
                sock.sendall(struct.pack(PACKET_FMT, *state))
                last_state = state
            time.sleep(0.001)   # 1 ms = 1000 Hz poll
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
        print("\nDone.")


if __name__ == "__main__":
    main()
