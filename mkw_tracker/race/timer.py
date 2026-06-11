"""RaceTimer - live in-race elapsed estimate for the friend-card timer.

Produces the engine's best estimate of the *real* in-game elapsed ms during
RACING, cheaply: a wall-clock counter anchored on the existing digit timer read
(reused from the timestamp tracker) and corrected by sparse re-reads. The card
applies any display delay; this value is un-lagged ground truth.

Anchor rule (cumulative race time only ever rises during a continuous run):
  * start            - first clean read with ms > 0 sets the anchor
  * backward read    - ignored (the ~7s lap-split flash + misreads); the local
                       counter carries the true cumulative time through it
  * within tolerance - re-anchor (drift correction)
  * forward read     - ignored unless `forward_confirm` consecutive reads agree
                       (process-stall recovery only)
Frozen on non-RACING; re-baselined on resume so a paused gap is not counted.
"""
import time
from typing import Optional

import numpy as np

from ..detection.screen import Screen
from .laps import load_digit_templates, read_digit_roi
from .timestamp import TIMESTAMP_ROIS


def read_timer_ms(frame, templates, threshold: float) -> Optional[int]:
    """Read the six-digit A:BC.DEF timer -> total ms, or None if any digit is unread."""
    vals = []
    for slot in ('A', 'B', 'C', 'D', 'E', 'F'):
        digit, _ = read_digit_roi(frame, TIMESTAMP_ROIS[slot], templates,
                                  threshold=threshold, reconfirm_digit=None)
        if digit is None:
            return None
        vals.append(digit)
    a, b, c, d, e, f = vals
    return a * 60_000 + (b * 10 + c) * 1000 + (d * 100 + e * 10 + f)


class RaceTimer:
    def __init__(self, digit_dir: str = 'images/digits', digit_h: int = 42,
                 digit_threshold: float = 0.50, resync_interval: float = 0.5,
                 tolerance_ms: int = 300, forward_confirm: int = 3, templates=None):
        self._templates = templates if templates is not None else load_digit_templates(digit_dir, digit_h)
        self.digit_threshold = digit_threshold
        self.resync_interval = resync_interval
        self.tolerance_ms = tolerance_ms
        self.forward_confirm = forward_confirm
        self.reset()

    def reset(self):
        self.running = False
        self.anchor_ms = 0
        self.anchor_perf = 0.0
        self._paused = False
        self._fwd = 0
        self._last_read = 0.0

    def _estimate(self, now: float) -> int:
        return int(round(self.anchor_ms + (now - self.anchor_perf) * 1000.0))

    def step(self, read_ms: Optional[int], now: float, racing: bool) -> Optional[int]:
        if not racing:
            if self.running and not self._paused:        # freeze at current
                self.anchor_ms = self._estimate(now)
                self.anchor_perf = now
            self._paused = True
            return self.anchor_ms if self.running else None

        if self._paused:                                 # resumed
            self.anchor_perf = now                        # drop the paused gap
            self._paused = False
            self._fwd = 0

        if read_ms is not None:
            if not self.running:
                if read_ms > 0:                          # start on first clean read > 0
                    self.running = True
                    self.anchor_ms = read_ms
                    self.anchor_perf = now
                    self._fwd = 0
            else:
                diff = read_ms - self._estimate(now)
                if abs(diff) <= self.tolerance_ms:        # drift correction
                    self.anchor_ms = read_ms
                    self.anchor_perf = now
                    self._fwd = 0
                elif diff > self.tolerance_ms:            # forward: confirm before snap
                    self._fwd += 1
                    if self._fwd >= self.forward_confirm:
                        self.anchor_ms = read_ms
                        self.anchor_perf = now
                        self._fwd = 0
                else:                                     # backward: ignore (lap flash)
                    self._fwd = 0

        return self._estimate(now) if self.running else None

    def update(self, frame: np.ndarray, screen: Screen, now: Optional[float] = None) -> Optional[int]:
        if now is None:
            now = time.perf_counter()
        racing = screen == Screen.RACING
        read_ms = None
        if racing and (now - self._last_read) >= self.resync_interval:
            self._last_read = now
            read_ms = read_timer_ms(frame, self._templates, self.digit_threshold)
        return self.step(read_ms, now, racing)
