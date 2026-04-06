"""TimestampTracker, TimestampState."""
import time
import cv2
import numpy as np
from dataclasses import dataclass
from typing import Dict, Optional

from ..detection.screen import Screen
from .laps import load_digit_templates, read_digit_roi

TIMESTAMP_ROIS = {
    'A': (1556, 54, 1556 + 42, 54 + 52),
    'B': (1628, 54, 1628 + 42, 54 + 52),
    'C': (1670, 54, 1670 + 42, 54 + 52),
    'D': (1738, 54, 1738 + 42, 54 + 52),
    'E': (1778, 54, 1778 + 42, 54 + 52),
    'F': (1818, 54, 1818 + 42, 54 + 52),
}
TIMESTAMP_DIGIT_THRESHOLD = 0.50


@dataclass
class TimestampState:
    A: Optional[int] = None
    B: Optional[int] = None
    C: Optional[int] = None
    D: Optional[int] = None
    E: Optional[int] = None
    F: Optional[int] = None
    A_conf: float = 0.0
    B_conf: float = 0.0
    C_conf: float = 0.0
    D_conf: float = 0.0
    E_conf: float = 0.0
    F_conf: float = 0.0

    def formatted(self) -> Optional[str]:
        if any(getattr(self, d) is None for d in ('A', 'B', 'C', 'D', 'E', 'F')):
            return None
        return f"{self.A}:{self.B}{self.C}.{self.D}{self.E}{self.F}"

    def min_conf(self) -> float:
        return min(self.A_conf, self.B_conf, self.C_conf,
                   self.D_conf, self.E_conf, self.F_conf)


class TimestampTracker:
    """Reads the six-digit A:BC.DEF lap timer during RACING."""

    _SLOTS = ('A', 'B', 'C', 'D', 'E', 'F')

    def __init__(
        self,
        digit_dir: str = 'images/timestamps/cropped',
        scan_interval: float = 0.1,
        digit_h: int = 42,
    ):
        self.scan_interval = scan_interval
        self._templates    = load_digit_templates(digit_dir, digit_h)
        self.state:          TimestampState  = TimestampState()
        self._last_scan:     float           = 0.0
        self.splits:         Dict[int, str]  = {}
        self.total_time:     Optional[str]   = None
        self.final_lap_time: Optional[str]   = None
        self._burst_lap:       Optional[int] = None
        self._burst_is_finish: bool          = False
        self._burst_reads:     int           = 0
        self._burst_candidate: Optional[str] = None
        self._BURST_CONFIRM:   int           = 3

    def reset(self):
        self.state             = TimestampState()
        self.splits            = {}
        self.total_time        = None
        self.final_lap_time    = None
        self._burst_lap        = None
        self._burst_is_finish  = False
        self._burst_reads      = 0
        self._burst_candidate  = None

    @staticmethod
    def _to_ms(ts: str) -> Optional[int]:
        try:
            mins, rest = ts.split(":")
            secs, millis = rest.split(".")
            return int(mins) * 60_000 + int(secs) * 1000 + int(millis)
        except Exception:
            return None

    @staticmethod
    def _from_ms(ms: int) -> str:
        m   =  ms // 60_000
        s   = (ms %  60_000) // 1000
        mil =  ms %  1000
        return f"{m}:{s:02d}.{mil:03d}"

    def record_split(self, lap: int):
        ts = self.state.formatted()
        if ts is not None:
            self.splits[lap] = ts
            print(f"  Split lap {lap}: {ts}")

    def record_finish(self, lap: int):
        ts = self.state.formatted()
        if ts is None:
            return
        self.total_time = ts
        print(f"  Total time: {ts}")
        total_ms = self._to_ms(ts)
        if total_ms is None:
            return
        prior_ms = [self._to_ms(s) for s in self.splits.values()]
        if any(v is None for v in prior_ms):
            print("  [WARN] Some splits unparseable - final lap time unreliable")
            return
        final_ms = total_ms - sum(prior_ms)
        if final_ms < 0:
            print("  [WARN] Final lap time negative - split data unreliable")
            return
        self.final_lap_time = self._from_ms(final_ms)
        self.splits[lap]    = self.final_lap_time
        print(f"  Final lap time: {self.final_lap_time}")

    def debug_frame(self, frame: np.ndarray, out_dir: str = "debug_laps"):
        import os
        os.makedirs(out_dir, exist_ok=True)
        for slot, roi in TIMESTAMP_ROIS.items():
            x1, y1, x2, y2 = roi
            crop = frame[y1:y2, x1:x2]
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop
            _, processed = cv2.threshold(gray, 170, 255, cv2.THRESH_BINARY)
            crop_path = os.path.join(out_dir, f"ts_{slot}_roi_crop.png")
            cv2.imwrite(crop_path, processed)

    def update(
        self,
        frame: np.ndarray,
        screen: Screen,
        capture_now: bool = False,
        lap_number: Optional[int] = None,
        is_finish: bool = False,
    ) -> TimestampState:
        if screen != Screen.RACING:
            return self.state

        if capture_now and self._burst_lap is None and lap_number is not None:
            self._burst_lap       = lap_number
            self._burst_is_finish = is_finish
            self._burst_reads     = 0
            self._burst_candidate = None
            self.state = TimestampState()

        if self._burst_lap is None:
            return self.state

        now = time.perf_counter()
        if (now - self._last_scan) < self.scan_interval:
            return self.state
        self._last_scan = now

        scores: Dict[str, str] = {}
        for slot, roi in TIMESTAMP_ROIS.items():
            digit, conf = read_digit_roi(
                frame, roi, self._templates,
                threshold=TIMESTAMP_DIGIT_THRESHOLD,
                reconfirm_digit=None,
            )
            if digit is not None:
                setattr(self.state, slot,           digit)
                setattr(self.state, f"{slot}_conf", conf)
                scores[slot] = f"{digit}({conf:.2f})"
            else:
                scores[slot] = f"?({conf:.2f})"

        self._burst_reads += 1
        current_read = self.state.formatted()
        print(f"  [ts burst #{self._burst_reads}] "
              + " ".join(f"{s}={v}" for s, v in scores.items())
              + f"  -> {current_read or '?'}")

        if current_read is None:
            return self.state

        if current_read == self._burst_candidate:
            if self._burst_is_finish:
                self.record_finish(self._burst_lap)
            else:
                self.record_split(self._burst_lap)
            self._burst_lap = None
        else:
            self._burst_candidate = current_read
            if self._burst_reads >= self._BURST_CONFIRM * 2:
                print(f"  [WARN] Timestamp burst did not stabilise after "
                      f"{self._burst_reads} reads — committing best guess")
                if self._burst_is_finish:
                    self.record_finish(self._burst_lap)
                else:
                    self.record_split(self._burst_lap)
                self._burst_lap = None

        return self.state
