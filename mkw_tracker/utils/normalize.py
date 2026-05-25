"""Per-channel gain+offset+gamma capture normalization.

Applied to every captured frame before any detector sees it, so users with
different capture cards / HDR setups don't have to retune the ~50 per-tell
binary thresholds.  Hot-reloadable from Settings; rebuilds the LUT lazily on
a dirty flag set by mark_dirty().  Identity transform when calib_enabled = 0.

Transform per channel c with input pixel x in [0, 255]:
    out_c = clip(round(((x/255)^gamma) * gain_c * 255 + offset_c), 0, 255)

Apply cost: ~0.5ms per 1920x1080 BGR frame via a single cv2.LUT call.
"""
import threading
from typing import Optional

import cv2
import numpy as np


def build_lut(
    gain_r: float, gain_g: float, gain_b: float,
    offset_r: int, offset_g: int, offset_b: int,
    gamma: float,
) -> np.ndarray:
    """Build a (256, 1, 3) uint8 BGR LUT for cv2.LUT."""
    x = np.arange(256, dtype=np.float64) / 255.0
    if gamma != 1.0:
        x = np.power(x, gamma)
    lut_b = np.clip(np.round(x * gain_b * 255.0 + offset_b), 0, 255).astype(np.uint8)
    lut_g = np.clip(np.round(x * gain_g * 255.0 + offset_g), 0, 255).astype(np.uint8)
    lut_r = np.clip(np.round(x * gain_r * 255.0 + offset_r), 0, 255).astype(np.uint8)
    # BGR channel order to match OpenCV
    return np.stack([lut_b, lut_g, lut_r], axis=-1).reshape(256, 1, 3)


class Normalizer:
    """Hot-reloadable capture-normalization LUT.

    apply(frame) is a single cv2.LUT call; pass-through when calib_enabled = 0.
    Call mark_dirty() after Settings change; the LUT rebuilds on the next apply().
    """

    def __init__(self, settings) -> None:
        self._settings = settings
        self._lock     = threading.Lock()
        self._lut:     Optional[np.ndarray] = None
        self._enabled: bool = True
        self._dirty:   bool = True

    def mark_dirty(self) -> None:
        with self._lock:
            self._dirty = True

    def _rebuild(self) -> None:
        s = self._settings
        self._enabled = bool(s.get("calib_enabled", 1))
        if self._enabled:
            self._lut = build_lut(
                float(s.get("calib_gain_r",   1.0)),
                float(s.get("calib_gain_g",   1.0)),
                float(s.get("calib_gain_b",   1.0)),
                int  (s.get("calib_offset_r", 0)),
                int  (s.get("calib_offset_g", 0)),
                int  (s.get("calib_offset_b", 0)),
                float(s.get("calib_gamma",    1.0)),
            )
        else:
            self._lut = None
        self._dirty = False

    def apply(self, frame: Optional[np.ndarray]) -> Optional[np.ndarray]:
        if frame is None:
            return frame
        with self._lock:
            if self._dirty:
                self._rebuild()
            if not self._enabled or self._lut is None:
                return frame
            return cv2.LUT(frame, self._lut)

    def current(self) -> dict:
        """Return the active transform values (for IPC/wizard echo)."""
        s = self._settings
        return {
            "enabled":  int(s.get("calib_enabled", 1)),
            "gain_r":   float(s.get("calib_gain_r",  1.0)),
            "gain_g":   float(s.get("calib_gain_g",  1.0)),
            "gain_b":   float(s.get("calib_gain_b",  1.0)),
            "offset_r": int  (s.get("calib_offset_r", 0)),
            "offset_g": int  (s.get("calib_offset_g", 0)),
            "offset_b": int  (s.get("calib_offset_b", 0)),
            "gamma":    float(s.get("calib_gamma",    1.0)),
        }
