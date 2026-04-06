"""FinishDetector, FinishState, load_finish_templates."""
import os
import time
import cv2
import numpy as np
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional

from ..detection.screen import Screen
from ..utils.paths import resource_path, data_dir

FINISH_ROI = (1290, 410, 1290 + 90, 410 + 90)
FINISH_MATCH_THRESHOLD = 0.60
FINISH_CONFIRM_FRAMES  = 3

_FINISH_TEMPLATE_SPECS: list = [
    ("images/screens/finish.png",  170),
    ("images/screens/youwin.png",  170),
    ("images/screens/youlose.png", 100),
]

FINISH_TEMPLATES: Dict[str, tuple] = {}


def load_finish_templates(switch2_language: str = "en_uk"):
    """Load finish overlay templates for the given language."""
    FINISH_TEMPLATES.clear()
    lang = switch2_language or "en_uk"
    for base_path, thresh in _FINISH_TEMPLATE_SPECS:
        # Always resolve through the language directory.
        prefix = "images/screens/"
        rest = base_path[len(prefix):]
        lang_path = f"{prefix}{lang}/{rest}"
        name = os.path.splitext(os.path.basename(base_path))[0]
        # Check user data dir first, then resource path.
        tmpl = None
        user_path = str(data_dir() / lang_path)
        if os.path.exists(user_path):
            tmpl = cv2.imread(user_path, cv2.IMREAD_GRAYSCALE)
        if tmpl is None:
            tmpl = cv2.imread(resource_path(lang_path), cv2.IMREAD_GRAYSCALE)
        if tmpl is None:
            print(f"[WARN] FinishDetector: could not load {lang_path}")
            continue
        _, binary = cv2.threshold(tmpl, thresh, 255, cv2.THRESH_BINARY)
        FINISH_TEMPLATES[name] = (binary, thresh)
    print(f"[FinishDetector] {len(FINISH_TEMPLATES)} finish templates loaded (lang={lang!r})")


@dataclass
class FinishState:
    detected:       bool          = False
    result:         Optional[str] = None
    conf:           float         = 0.0
    total_time:     Optional[str] = None
    split_times:    list = field(default_factory=list)
    final_lap_time: Optional[str] = None


class FinishDetector:
    """Checks FINISH/YOU WIN/YOU LOSE overlays during RACING."""

    def __init__(
        self,
        on_finish: Optional[Callable[[FinishState], None]] = None,
        scan_interval: float = 0.1,
    ):
        self.on_finish     = on_finish
        self.scan_interval = scan_interval
        self.state         = FinishState()
        self._last_scan    = 0.0
        self._confirm_streak = 0
        self._confirm_name   = None

    def reset(self):
        self.state = FinishState()
        self._confirm_streak = 0
        self._confirm_name   = None

    def update(self, frame: np.ndarray, screen: Screen) -> FinishState:
        if screen != Screen.RACING or self.state.detected:
            return self.state

        now = time.perf_counter()
        if (now - self._last_scan) < self.scan_interval:
            return self.state
        self._last_scan = now

        x1, y1, x2, y2 = FINISH_ROI
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return self.state
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop

        best_name:  Optional[str] = None
        best_score: float         = 0.0

        for name, (tmpl, thresh) in FINISH_TEMPLATES.items():
            _, processed = cv2.threshold(gray, thresh, 255, cv2.THRESH_BINARY)
            if tmpl.shape[0] > processed.shape[0] or tmpl.shape[1] > processed.shape[1]:
                continue
            result = cv2.matchTemplate(processed, tmpl, cv2.TM_CCOEFF_NORMED)
            score  = float(cv2.minMaxLoc(result)[1])
            if score > best_score:
                best_score = score
                best_name  = name

        if best_score >= FINISH_MATCH_THRESHOLD and best_name is not None:
            if best_name == self._confirm_name:
                self._confirm_streak += 1
            else:
                self._confirm_name   = best_name
                self._confirm_streak = 1

            if self._confirm_streak >= FINISH_CONFIRM_FRAMES:
                self.state.detected = True
                self.state.result   = best_name
                self.state.conf     = best_score
                print(f"  Finish detected: {best_name} ({best_score:.3f})")
                if self.on_finish:
                    self.on_finish(self.state)
        else:
            self._confirm_streak = 0
            self._confirm_name   = None

        return self.state

    def record_times(self, total_time: Optional[str], split_times: list):
        """Store timing data after finish is detected."""
        self.state.total_time  = total_time
        self.state.split_times = split_times

        def _to_ms(ts: str) -> Optional[int]:
            try:
                mins, rest = ts.split(":")
                secs, millis = rest.split(".")
                return int(mins) * 60_000 + int(secs) * 1000 + int(millis)
            except Exception:
                return None

        if total_time is None:
            return
        total_ms = _to_ms(total_time)
        if total_ms is None:
            return

        split_ms_list = [_to_ms(s) for s in split_times if s is not None]
        final_ms = total_ms - sum(split_ms_list)
        if final_ms < 0:
            print("  [WARN] Final lap time negative")
            return
        m  =  final_ms // 60_000
        s  = (final_ms %  60_000) // 1000
        ms =  final_ms %  1000
        self.state.final_lap_time = f"{m}:{s:02d}.{ms:03d}"
        print(f"  Final lap time: {self.state.final_lap_time}")
