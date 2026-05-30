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

# Bounding box of the lap/total timer digits (union of timestamp.TIMESTAMP_ROIS).
FINISH_TIMER_BBOX = (1556, 54, 1860, 106)


class FinishStillDetector:
    """Detect the final-lap finish without the position overlay.

    On the final lap the race timer freezes on the total time with NO colour flash,
    so the timer DIGITS stay static for several seconds.  Intermediate lap pauses
    flash gold<->white (~10 golds / 6s) and during racing the digits advance, so
    neither stays still that long.

    The frame diff is restricted to the bright DIGIT pixels (grayscale above
    BRIGHT_THRESHOLD in either frame), NOT the whole ROI — so a moving track behind
    the semi-transparent HUD is ignored.  A white<->gold flash still changes the
    digit luma, so flashing (intermediate) pauses register as "changed".  This is a
    cheap masked absdiff over a small ROI — lighter than template matching.

    Tunables (validate against recorded footage):
      STILL_SECONDS    — must exceed the gold flash period (~0.6s) but stay under the
                         ~8s final pause; 2.5s is safe.
      DIFF_THRESHOLD   — mean abs diff over the digit pixels below which they count as
                         still; raise it if capture noise / leftover background motion
                         false-resets stillness.
      BRIGHT_THRESHOLD — grayscale cutoff isolating the (near-white / gold) digits from
                         the background; raise it if bright moving background leaks in.
    """
    STILL_SECONDS    = 2.5
    DIFF_THRESHOLD   = 8.0
    BRIGHT_THRESHOLD = 175
    MIN_BRIGHT_PX    = 40
    SCAN_INTERVAL    = 0.05

    def __init__(self):
        self.detected = False
        self._prev = None
        self._still_since = None
        self._last_scan = 0.0

    def reset(self):
        self.detected = False
        self._prev = None
        self._still_since = None
        self._last_scan = 0.0

    def update(self, frame: np.ndarray, screen: Screen, on_final_lap: bool) -> bool:
        if self.detected:
            return True
        if screen != Screen.RACING or not on_final_lap:
            self._prev = None
            self._still_since = None
            return False
        now = time.perf_counter()
        if (now - self._last_scan) < self.SCAN_INTERVAL:
            return False
        self._last_scan = now
        x1, y1, x2, y2 = FINISH_TIMER_BBOX
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return False
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop
        prev = self._prev
        self._prev = gray
        if prev is None or prev.shape != gray.shape:
            self._still_since = None
            return False
        # Compare only the bright digit pixels (bright in either frame) so a moving
        # background is ignored; the white<->gold flash still flips digit luma.
        mask = (gray > self.BRIGHT_THRESHOLD) | (prev > self.BRIGHT_THRESHOLD)
        if int(mask.sum()) < self.MIN_BRIGHT_PX:
            self._still_since = None          # no digits visible
            return False
        diff = float(cv2.absdiff(gray, prev)[mask].mean())
        if diff > self.DIFF_THRESHOLD:
            self._still_since = None          # digits advancing or flashing
        elif self._still_since is None:
            self._still_since = now
        elif (now - self._still_since) >= self.STILL_SECONDS:
            self.detected = True
            print(f"  [finish] digits frozen >= {self.STILL_SECONDS}s on final lap "
                  f"-> capturing final time")
        return self.detected
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
