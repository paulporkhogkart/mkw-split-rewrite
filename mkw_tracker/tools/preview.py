"""Low-cost preview-thumbnail encoder for the clip-capture WS broadcast.

Pure helpers (cv2 + base64) so the throttle/encoding logic is unit-tested away
from the main loop. No ffmpeg, no device access.
"""
import base64

import cv2


def _dims(frame, width):
    h, w = frame.shape[:2]
    nw = int(width)
    nh = max(1, int(round(h * (width / w))))
    return nw, nh


def encode_preview_b64(frame, width=320):
    nw, nh = _dims(frame, width)
    small = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".png", small)
    if not ok:
        return ""
    return base64.b64encode(buf.tobytes()).decode("ascii")


def maybe_preview(frame, now, last_emit, interval=0.5, width=320):
    """Throttle gate. Returns (message|None, new_last_emit)."""
    if frame is None or (now - last_emit) < interval:
        return None, last_emit
    b64 = encode_preview_b64(frame, width)
    if not b64:
        return None, last_emit
    nw, nh = _dims(frame, width)
    return {"type": "preview", "w": nw, "h": nh, "data": b64}, now
