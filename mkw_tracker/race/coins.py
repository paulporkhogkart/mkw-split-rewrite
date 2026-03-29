"""CoinTracker, CoinState."""
import os
import time
import cv2
import numpy as np
from dataclasses import dataclass
from typing import Optional

from ..detection.screen import Screen
from .laps import load_digit_templates, read_digit_roi

COIN_LEFT_ROI  = (118, 984, 118 + 36, 984 + 44)
COIN_RIGHT_ROI = (154, 984, 154 + 36, 984 + 44)
COIN_DIGIT_THRESHOLD = 0.60


@dataclass
class CoinState:
    coins:      Optional[int] = None
    left_conf:  float         = 0.0
    right_conf: float         = 0.0


class CoinTracker:
    """Detects coin count (0-99) from two digit ROIs."""

    def __init__(
        self,
        digit_dir: str = 'images/timestamps/cropped',
        scan_interval: float = 0.1,
        digit_h: int = 35,
    ):
        self.scan_interval = scan_interval
        self._templates    = load_digit_templates(digit_dir, digit_h)
        self.state:      CoinState = CoinState()
        self._last_scan: float     = 0.0

    def reset(self):
        self.state = CoinState()

    def debug_frame(self, frame: np.ndarray, out_dir: str = "debug_laps"):
        os.makedirs(out_dir, exist_ok=True)
        configs = [("coin_left", COIN_LEFT_ROI), ("coin_right", COIN_RIGHT_ROI)]
        for tag, roi in configs:
            x1, y1, x2, y2 = roi
            crop = frame[y1:y2, x1:x2]
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop
            _, processed = cv2.threshold(gray, 170, 255, cv2.THRESH_BINARY)
            crop_path = os.path.join(out_dir, f"{tag}_roi_crop.png")
            cv2.imwrite(crop_path, processed)

    def update(self, frame: np.ndarray, screen: Screen) -> CoinState:
        if screen != Screen.RACING:
            return self.state
        now = time.perf_counter()
        if (now - self._last_scan) < self.scan_interval:
            return self.state
        self._last_scan = now

        cached_left  = self.state.coins // 10 if self.state.coins is not None else None
        cached_right = self.state.coins %  10 if self.state.coins is not None else None

        left,  lconf = read_digit_roi(
            frame, COIN_LEFT_ROI, self._templates,
            threshold=COIN_DIGIT_THRESHOLD, reconfirm_digit=cached_left,
        )
        right, rconf = read_digit_roi(
            frame, COIN_RIGHT_ROI, self._templates,
            threshold=COIN_DIGIT_THRESHOLD, reconfirm_digit=cached_right,
        )

        if left is not None and right is not None:
            total = left * 10 + right
            if total != self.state.coins:
                print(f"  Coins: {total} ({lconf:.3f}, {rconf:.3f})")
            self.state.coins      = total
            self.state.left_conf  = lconf
            self.state.right_conf = rconf

        return self.state
