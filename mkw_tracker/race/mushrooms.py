"""MushroomTracker, MushroomState, load_mushroom_templates."""
import time
import cv2
import numpy as np
from dataclasses import dataclass
from typing import Dict, Optional

from ..detection.screen import Screen
from ..utils.paths import resource_path

MUSHROOM_ROI = (50, 134, 240, 226)
MUSHROOM_MATCH_THRESHOLD = 0.55

# Mushroom icons are matched with the same grayscale + slack approach as the
# screen tells (continuous-tone TM_CCOEFF_NORMED over a +/- search_pad window),
# which is robust to per-capture-card brightness/contrast differences that a
# fixed binary threshold collapses under.  The packaged templates in
# images/mushrooms/<lang>/ are grayscale crops (regenerated from old_assets/);
# a user recapture (saved grayscale to the data dir) takes precedence.
MUSHROOM_SEARCH_PAD = 6

MUSHROOM_TEMPLATES: Dict[int, np.ndarray] = {}


def load_mushroom_templates(switch2_language: str = None):
    """Load grayscale mushroom quantity templates (user override, then packaged)."""
    import os
    from ..utils.paths import data_dir
    lang = switch2_language or "en_uk"
    MUSHROOM_TEMPLATES.clear()

    def _load_mush(count: int, filename: str) -> bool:
        lang_rel = f"images/mushrooms/{lang}/{filename}"
        candidates = [
            str(data_dir() / lang_rel),   # user recapture (grayscale)
            resource_path(lang_rel),      # packaged grayscale crop
        ]
        for path in candidates:
            if os.path.exists(path):
                tmpl = cv2.imread(path, cv2.IMREAD_GRAYSCALE)   # continuous-tone, no threshold
                if tmpl is not None:
                    MUSHROOM_TEMPLATES[count] = tmpl
                    return True
        print(f"[WARN] MushroomTracker: could not load {lang_rel}")
        return False

    _load_mush(3, "3mush.png")
    _load_mush(2, "2mush.png")
    _load_mush(1, "1mush.png")
    print(f"[MushroomTracker] {len(MUSHROOM_TEMPLATES)} mushroom templates loaded (grayscale)")


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
        # ROI read from settings so HUD-editor edits take effect (after restart),
        # matching how SelectionTracker reads its ROIs.
        from ..config.settings import get_settings as _gs
        self._roi = tuple(_gs().get('mushroom_roi', list(MUSHROOM_ROI)))
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

        # Grayscale crop padded by search_pad so the template can slide +/- a few
        # px (absorbs small per-setup positional offset); no binarisation.
        x1, y1, x2, y2 = self._roi
        h, w = frame.shape[:2]
        pad = MUSHROOM_SEARCH_PAD
        crop = frame[max(0, y1 - pad):min(h, y2 + pad), max(0, x1 - pad):min(w, x2 + pad)]
        if crop.size == 0:
            return self.state
        processed = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop

        def _score(tmpl) -> float:
            if tmpl is None or tmpl.shape[0] > processed.shape[0] or tmpl.shape[1] > processed.shape[1]:
                return 0.0
            return float(cv2.minMaxLoc(cv2.matchTemplate(processed, tmpl, cv2.TM_CCOEFF_NORMED))[1])

        # Reconfirm fast-path
        if self.state.count > 0:
            score = _score(MUSHROOM_TEMPLATES.get(self.state.count))
            if score >= MUSHROOM_MATCH_THRESHOLD:
                self._loss_streak   = 0
                self._pending_count = self.state.count
                self.state.conf     = score
                return self.state

        # Full scan (high to low)
        best_count = 0
        best_score = 0.0
        for count in (3, 2, 1):
            score = _score(MUSHROOM_TEMPLATES.get(count))
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
