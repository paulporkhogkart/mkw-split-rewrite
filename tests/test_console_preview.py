import base64
import numpy as np
from mkw_tracker.tools.preview import encode_preview_b64, maybe_preview


def _frame():
    return np.zeros((1080, 1920, 3), dtype=np.uint8)


def test_encode_downscales_to_width():
    import cv2
    b64 = encode_preview_b64(_frame(), width=320)
    img = cv2.imdecode(np.frombuffer(base64.b64decode(b64), np.uint8), cv2.IMREAD_COLOR)
    assert img.shape[1] == 320 and img.shape[0] == 180   # 16:9 preserved


def test_maybe_preview_throttles():
    msg, last = maybe_preview(_frame(), now=0.0, last_emit=0.0, interval=0.5)
    assert msg is None and last == 0.0                   # too soon (0 since last)


def test_maybe_preview_emits_after_interval():
    msg, last = maybe_preview(_frame(), now=1.0, last_emit=0.0, interval=0.5)
    assert msg["type"] == "preview" and msg["w"] == 320 and msg["h"] == 180
    assert isinstance(msg["data"], str) and last == 1.0


def test_maybe_preview_none_frame():
    assert maybe_preview(None, now=5.0, last_emit=0.0)[0] is None
