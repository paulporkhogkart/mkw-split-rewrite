"""MushroomTracker, MushroomState, load_mushroom_templates."""
import time
import cv2
import numpy as np
from dataclasses import dataclass
from typing import Dict, Optional

from ..detection.screen import Screen
from ..utils.paths import resource_path

MUSHROOM_ROI = (50, 50, 50 + 190, 50 + 190)
MUSHROOM_MATCH_THRESHOLD = 0.55

MUSHROOM_TEMPLATES: Dict[int, np.ndarray] = {}


def load_mushroom_templates():
    """Load mushroom quantity templates. Call once at startup."""
    specs = [
        (3, "images/mushrooms/3mush.png"),
        (2, "images/mushrooms/2mush.png"),
        (1, "images/mushrooms/1mush.png"),
    ]
    for count, path_str in specs:
        tmpl = cv2.imread(resource_path(path_str), cv2.IMREAD_GRAYSCALE)
        if tmpl is None:
            print(f"[WARN] MushroomTracker: could not load {path_str}")
            continue
        _, binary = cv2.threshold(tmpl, 170, 255, cv2.THRESH_BINARY)
        MUSHROOM_TEMPLATES[count] = binary
    print(f"[MushroomTracker] {len(MUSHROOM_TEMPLATES)} mushroom templates loaded")


@dataclass
class MushroomState:
    count: int   = 0
    conf:  float = 0.0


class MushroomTracker:
    """Detects remaining mushrooms (0-3) during RACING."""

    LOSS_FRAMES: int = 2
    GAIN_FRAMES: int = 2

    def __init__(self, scan_interval: float = 0.1):
        self.scan_interval   = scan_interval
        self.state:          MushroomState = MushroomState()
        self._last_scan:     float         = 0.0
        self._loss_streak:   int           = 0
        self._pending_count: int           = 0
        self._pending_conf:  float         = 0.0
        self._gain_streak:   int           = 0
        self._gain_count:    int           = 0
        self._gain_conf:     float         = 0.0

    def reset(self):
        self.state         = MushroomState()
        self._loss_streak  = 0
        self._pending_count = 0
        self._pending_conf  = 0.0
        self._gain_streak  = 0
        self._gain_count   = 0
        self._gain_conf    = 0.0

    def update(self, frame: np.ndarray, screen: Screen) -> MushroomState:
        if screen != Screen.RACING:
            return self.state
        now = time.perf_counter()
        if (now - self._last_scan) < self.scan_interval:
            return self.state
        self._last_scan = now

        x1, y1, x2, y2 = MUSHROOM_ROI
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return self.state
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop
        _, processed = cv2.threshold(gray, 170, 255, cv2.THRESH_BINARY)

        # Reconfirm fast-path
        if self.state.count > 0:
            tmpl = MUSHROOM_TEMPLATES.get(self.state.count)
            if tmpl is not None and (
                tmpl.shape[0] <= processed.shape[0] and
                tmpl.shape[1] <= processed.shape[1]
            ):
                result = cv2.matchTemplate(processed, tmpl, cv2.TM_CCOEFF_NORMED)
                score  = float(cv2.minMaxLoc(result)[1])
                if score >= MUSHROOM_MATCH_THRESHOLD:
                    self._loss_streak   = 0
                    self._pending_count = self.state.count
                    self.state.conf     = score
                    return self.state

        # Full scan (high to low)
        best_count = 0
        best_score = 0.0
        for count in (3, 2, 1):
            tmpl = MUSHROOM_TEMPLATES.get(count)
            if tmpl is None:
                continue
            if tmpl.shape[0] > processed.shape[0] or tmpl.shape[1] > processed.shape[1]:
                continue
            result = cv2.matchTemplate(processed, tmpl, cv2.TM_CCOEFF_NORMED)
            score  = float(cv2.minMaxLoc(result)[1])
            if score >= MUSHROOM_MATCH_THRESHOLD and score > best_score:
                best_score = score
                best_count = count

        if best_count < self.state.count:
            self._gain_streak = 0
            if best_count == self._pending_count:
                self._loss_streak += 1
            else:
                self._pending_count = best_count
                self._pending_conf  = best_score
                self._loss_streak   = 1
            if self._loss_streak >= self.LOSS_FRAMES:
                print(f"  Mushrooms: {self._pending_count} ({self._pending_conf:.3f})")
                self.state.count    = self._pending_count
                self.state.conf     = self._pending_conf
                self._loss_streak   = 0
        elif best_count > self.state.count:
            self._loss_streak = 0
            if best_count == self._gain_count:
                self._gain_streak += 1
                self._gain_conf    = best_score
            else:
                self._gain_count  = best_count
                self._gain_conf   = best_score
                self._gain_streak = 1
            if self._gain_streak >= self.GAIN_FRAMES:
                print(f"  Mushrooms: {self._gain_count} ({self._gain_conf:.3f})")
                self.state.count  = self._gain_count
                self.state.conf   = self._gain_conf
                self._gain_streak = 0
        else:
            self.state.conf     = best_score
            self._loss_streak   = 0
            self._gain_streak   = 0
            self._pending_count = best_count
            self._gain_count    = best_count

        return self.state
