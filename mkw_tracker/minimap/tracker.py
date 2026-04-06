"""MinimapTracker – Hough-first tracking.

Each frame during RACING:

  1. Find the player ring with HoughCircles in the search window.
  2. Score the character template at the ring centre (single-point correlation).
  3. Classify by score:
       ≥ confident_score  →  TRACKING   (full confidence, calibrate)
       ≥ accept_score     →  RING_ONLY  (ring found, face swapped: hazard/ghost)
       <  accept_score    →  reject     (probably another player's ring)
  4. No ring found        →  miss

This inverts the old template-first design.  Ghosts that appear without a ring
are automatically rejected at step 1.  The character template is only used for
identity confirmation at a known ring position — so it never needs to slide
across the search window.
"""
import math
import cv2
import numpy as np
from enum import Enum
from dataclasses import dataclass
from typing import Optional

from ..detection.screen import Screen
from ..database.replay_repo import (
    get_minimap_threshold, set_minimap_threshold,
    get_minimap_roi, set_minimap_roi,
)

# ── Minimap ROI (full-frame pixels, 1080p) ────────────────────────────────────
MINIMAP_ROI = (1442, 251, 466, 796)   # x, y, w, h

# ── Tuning constants ──────────────────────────────────────────────────────────
_MM_RADIUS_MIN        = 12
_MM_RADIUS_MAX        = 42
_MM_CHAR_W_F          = 0.30   # interior crop width  = radius × this
_MM_CHAR_H_F          = 0.45   # interior crop height = radius × this
_MM_CHAR_W_PX         = 24     # template canonical width
_MM_CHAR_H_PX         = 36     # template canonical height
_MM_EMA_ALPHA         = 0.25
_MM_ACCEPT_SCORE      = 0.18   # minimum template score to accept a ring hit
_MM_CONFIDENT_SCORE   = 0.90   # default confidence threshold (auto-calibrated)
_MM_MAX_JUMP_PX       = 40     # position jump that triggers re-acquire
_MM_REACQUIRE_FRAMES  = 4      # consecutive confirmed hits to commit after jump
_MM_LOST_FRAMES       = 36     # consecutive misses before entering LOST
_MM_MISS_EXPAND       = 4      # misses before switching to loose search window
_MM_SEARCH_TIGHT      = 30     # search half-window: normal (px)
_MM_SEARCH_LOOSE      = 80     # search half-window: after _MM_MISS_EXPAND misses
_MM_HOUGH_R_MIN       = 17
_MM_HOUGH_R_MAX       = 25
_MM_HOUGH_PARAM1      = 50
_MM_HOUGH_PARAM2      = 18
# Radius of the face-mask applied before Hough when we have a reliable
# reference position.  Blanks out the character interior so large sprites
# (King Boo etc.) don't break the ring's circular edge.
# Should be just inside the ring's inner edge: _MM_HOUGH_R_MIN - ~4px.
_MM_HOUGH_FACE_MASK_R = 13
_MM_CALIB_MARGIN_BASE = 0.50
_MM_CALIB_MIN         = 0.75
_MM_CALIB_MAX         = 0.98


# ── Internal state enum ───────────────────────────────────────────────────────

class _TrackState(Enum):
    IDLE      = "idle"
    TRACKING  = "tracking"
    RING_ONLY = "ring_only"
    REACQUIRE = "reacquire"
    LOST      = "lost"


# ── Public state dataclass ────────────────────────────────────────────────────

@dataclass
class MinimapState:
    cx:          Optional[int] = None
    cy:          Optional[int] = None
    cx_smooth:   float         = 0.0
    cy_smooth:   float         = 0.0
    radius:      int           = 0
    tracking:    bool          = False   # True whenever a position is being published
    last_score:  float         = 0.0
    track_state: str           = "idle"  # _TrackState.value for overlay display


# ── Threshold persistence ─────────────────────────────────────────────────────

class ThresholdStore:
    """Persists per-(course, character, costume) confidence thresholds in the DB."""

    def get(self, course: str, character: str,
            costume: Optional[str]) -> Optional[float]:
        return get_minimap_threshold(course, character, costume)

    def set(self, course: str, character: str,
            costume: Optional[str], threshold: float):
        old = self.get(course, character, costume)
        set_minimap_threshold(course, character, costume, threshold)
        print(f"  [ThresholdStore] Saved thr={threshold:.3f} for "
              f"'{course}' / '{character}' / '{costume or 'Base'}'"
              + (f" (was {old:.3f})" if old is not None else " (new)"))


# ── Tracker ───────────────────────────────────────────────────────────────────

class MinimapTracker:
    """
    Tracks the player-character ring on the minimap during RACING.

    Algorithm (each frame):
      1. HoughCircles in the search window  →  ring position + radius
      2. Template correlation at ring centre  →  identity score
      3. score ≥ confident  →  TRACKING
         score ≥ accept     →  RING_ONLY  (hazard / ghost face)
         score <  accept    →  reject (another player's ring)
         no ring            →  miss → LOST after _MM_LOST_FRAMES
    """

    def __init__(self):
        self._roi_x, self._roi_y = MINIMAP_ROI[0], MINIMAP_ROI[1]
        self._roi_w, self._roi_h = MINIMAP_ROI[2], MINIMAP_ROI[3]
        self._char_template: Optional[np.ndarray] = None
        self._char_mask:     Optional[np.ndarray] = None
        self._clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
        self._ts    = _TrackState.IDLE
        self.state  = MinimapState()
        self._miss_streak:      int   = 0
        self._reacquire_streak: int   = 0
        self._confident_score:  float = _MM_CONFIDENT_SCORE
        self._calib_scores:     list  = []
        self._calibrated:       bool  = False
        self.debug_log:         bool  = False

    # ── Public API ────────────────────────────────────────────────────────────

    def reset(self):
        self._ts               = _TrackState.IDLE
        self.state             = MinimapState()
        self._miss_streak      = 0
        self._reacquire_streak = 0
        self._char_template    = None
        self._char_mask        = None
        self._confident_score  = _MM_CONFIDENT_SCORE
        self._calib_scores     = []
        self._calibrated       = False

    def set_roi(self, x: int, y: int, w: int, h: int):
        self._roi_x, self._roi_y = x, y
        self._roi_w, self._roi_h = w, h

    def seed(self, cx_full: int, cy_full: int, radius: int = 0,
             frame: Optional[np.ndarray] = None,
             confident_score: Optional[float] = None):
        if radius == 0:
            radius = (_MM_RADIUS_MIN + _MM_RADIUS_MAX) // 2
        self._confident_score   = confident_score if confident_score is not None \
                                  else _MM_CONFIDENT_SCORE
        self.state.cx           = cx_full
        self.state.cy           = cy_full
        self.state.cx_smooth    = float(cx_full)
        self.state.cy_smooth    = float(cy_full)
        self.state.radius       = radius
        self.state.tracking     = True
        self._miss_streak       = 0
        self._reacquire_streak  = 0
        self._ts                = _TrackState.TRACKING
        self._sync_state_field()
        print(f"  [MinimapTracker] Seeded ({cx_full},{cy_full}) r={radius} "
              f"conf_thr={self._confident_score:.2f}")

        if frame is not None:
            roi = frame[self._roi_y:self._roi_y + self._roi_h,
                        self._roi_x:self._roi_x + self._roi_w]
            interior = self._crop_interior(
                roi,
                float(cx_full - self._roi_x),
                float(cy_full - self._roi_y),
                float(radius),
            )
            if interior is not None:
                self._char_template = self._make_template(interior)
                self._char_mask     = self._make_circle_mask(
                    _MM_CHAR_H_PX, _MM_CHAR_W_PX)
                print("  [MinimapTracker] Character template locked from seed frame")
            else:
                print("  [MinimapTracker] WARNING: could not crop template at seed point")

    def calibrate_from_race(self) -> float:
        """
        Auto-calibrate the confidence threshold from scores accumulated this race.
        Trims the bottom 15 %, takes the median, applies a downward margin,
        and clamps to [_MM_CALIB_MIN, _MM_CALIB_MAX].
        """
        if self._calibrated:
            return self._confident_score
        n = len(self._calib_scores)
        if n < 30:
            print(f"  [MinimapTracker] calibration skipped - only {n} scores")
            self._calibrated = True
            return self._confident_score
        trimmed = sorted(self._calib_scores)[max(1, int(n * 0.15)):]
        median  = trimmed[len(trimmed) // 2]
        margin  = _MM_CALIB_MARGIN_BASE * (1.0 - median)
        thr     = max(_MM_CALIB_MIN, min(_MM_CALIB_MAX, median - margin))
        old     = self._confident_score
        self._confident_score = thr
        self._calibrated      = True
        self._calib_scores    = []
        print(f"  [MinimapTracker] calibrated: n={n} median={median:.3f} "
              f"margin={margin:.3f} thr={thr:.3f} (was {old:.3f})")
        return thr

    # Alias kept for call-sites that use the old name
    calibrate_from_lap1 = calibrate_from_race

    # ── Main update ───────────────────────────────────────────────────────────

    def update(self, frame: np.ndarray, screen: Screen) -> MinimapState:
        if screen != Screen.RACING:
            return self.state
        if self._char_template is None:
            return self.state

        roi = frame[self._roi_y:self._roi_y + self._roi_h,
                    self._roi_x:self._roi_x + self._roi_w]
        if roi.size == 0:
            return self.state

        ref_x = int(round(self.state.cx_smooth)) - self._roi_x
        ref_y = int(round(self.state.cy_smooth)) - self._roi_y

        window = (None               if self._ts == _TrackState.LOST
                  else _MM_SEARCH_LOOSE if self._miss_streak >= _MM_MISS_EXPAND
                  else _MM_SEARCH_TIGHT)

        # ── Step 1: find the ring ─────────────────────────────────────────────
        # Apply face mask when we have a reliable reference (avoids masking
        # the wrong area during loose / full-ROI searches).
        mask_face = self._ts in (_TrackState.TRACKING, _TrackState.RING_ONLY)
        ring_found, hx, hy, hr = self._find_ring(
            roi, ref_x, ref_y, window, mask_face=mask_face)

        if not ring_found:
            self._reacquire_streak = 0
            self._on_miss()
            self._sync_state_field()
            return self.state

        # ── Step 2: score character template at ring centre ───────────────────
        r = hr if hr > 0 else (self.state.radius
                                if self.state.radius > 0
                                else (_MM_RADIUS_MIN + _MM_RADIUS_MAX) // 2)
        score = self._score_at(roi, hx, hy, r)
        self.state.last_score = score

        if score < _MM_ACCEPT_SCORE:
            # Ring found but wrong player — ignore it
            self._reacquire_streak = 0
            self._on_miss()
            self._sync_state_field()
            return self.state

        # ── Step 3: drive state machine ───────────────────────────────────────
        self._on_confirmed_hit(hx, hy, r, score)
        self._sync_state_field()
        return self.state

    # ── Confirmed-hit state machine ───────────────────────────────────────────

    def _on_confirmed_hit(self, cx_r: int, cy_r: int, radius: int, score: float):
        dist      = self._dist_from_smooth(cx_r, cy_r)
        confident = score >= self._confident_score

        if self._ts in (_TrackState.TRACKING, _TrackState.RING_ONLY):
            if dist > _MM_MAX_JUMP_PX:
                # Sudden large jump — require confirmation before committing
                self._ts               = _TrackState.REACQUIRE
                self._reacquire_streak = 1
                self._on_miss()
            else:
                self._ts = _TrackState.TRACKING if confident else _TrackState.RING_ONLY
                self._publish(cx_r, cy_r, radius)
                if confident and not self._calibrated:
                    self._calib_scores.append(score)

        elif self._ts == _TrackState.REACQUIRE:
            self._reacquire_streak += 1
            if self._reacquire_streak >= _MM_REACQUIRE_FRAMES:
                self._ts               = _TrackState.TRACKING if confident else _TrackState.RING_ONLY
                self._reacquire_streak = 0
                self._miss_streak      = 0
                self._publish(cx_r, cy_r, radius)
                if confident and not self._calibrated:
                    self._calib_scores.append(score)
            # else: still building streak — neither publish nor count as miss

        elif self._ts == _TrackState.LOST:
            # First ring+identity hit after going LOST — start fresh re-acquire
            self._ts               = _TrackState.REACQUIRE
            self._reacquire_streak = 1

    # ── Miss helper ───────────────────────────────────────────────────────────

    def _on_miss(self):
        self._miss_streak += 1
        if self._miss_streak >= _MM_LOST_FRAMES:
            self._ts            = _TrackState.LOST
            self.state.tracking = False

    def _sync_state_field(self):
        self.state.track_state = self._ts.value

    # ── Position helpers ──────────────────────────────────────────────────────

    def _dist_from_smooth(self, cx_r: int, cy_r: int) -> float:
        cx_f = cx_r + self._roi_x
        cy_f = cy_r + self._roi_y
        return math.sqrt((cx_f - self.state.cx_smooth) ** 2
                         + (cy_f - self.state.cy_smooth) ** 2)

    def _publish(self, cx_roi: int, cy_roi: int, radius: int = 0):
        cx_full = cx_roi + self._roi_x
        cy_full = cy_roi + self._roi_y
        self.state.cx = cx_full
        self.state.cy = cy_full
        if not self.state.tracking:
            self.state.cx_smooth = float(cx_full)
            self.state.cy_smooth = float(cy_full)
        else:
            a = _MM_EMA_ALPHA
            self.state.cx_smooth += a * (cx_full - self.state.cx_smooth)
            self.state.cy_smooth += a * (cy_full - self.state.cy_smooth)
        if radius > 0:
            self.state.radius = radius
        self.state.tracking = True
        self._miss_streak   = 0

    # ── Ring detection (Hough) ────────────────────────────────────────────────

    def _find_ring(self, roi: np.ndarray, ref_x: int, ref_y: int,
                   window: Optional[int], mask_face: bool = False) -> tuple:
        """
        Run HoughCircles in the search window around (ref_x, ref_y).
        window=None searches the full ROI.
        Returns (found, cx_roi, cy_roi, radius).
        Picks the circle whose centre is closest to (ref_x, ref_y).

        mask_face=True blanks out the character interior before running Hough,
        preventing large character sprites (King Boo etc.) from breaking the
        ring's circular edge and causing jitter.
        """
        h, w = roi.shape[:2]
        if window is not None:
            x1 = max(0, ref_x - window);  y1 = max(0, ref_y - window)
            x2 = min(w, ref_x + window);  y2 = min(h, ref_y + window)
        else:
            x1, y1, x2, y2 = 0, 0, w, h

        crop = roi[y1:y2, x1:x2]
        if crop.size == 0:
            return False, ref_x, ref_y, 0

        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)

        if mask_face:
            # Erase the expected character interior so the ring edge is the
            # dominant circular gradient.  Centre = expected ring position in
            # crop coordinates.
            face_mask = np.ones_like(gray)
            cv2.circle(face_mask,
                       (ref_x - x1, ref_y - y1),
                       _MM_HOUGH_FACE_MASK_R, 0, -1)
            gray = cv2.multiply(gray, face_mask)

        circles = cv2.HoughCircles(
            gray, cv2.HOUGH_GRADIENT, dp=1, minDist=10,
            param1=_MM_HOUGH_PARAM1, param2=_MM_HOUGH_PARAM2,
            minRadius=_MM_HOUGH_R_MIN, maxRadius=_MM_HOUGH_R_MAX,
        )
        if circles is None:
            return False, ref_x, ref_y, 0

        local_rx = ref_x - x1
        local_ry = ref_y - y1
        best = min(
            np.round(circles[0]).astype(int),
            key=lambda c: math.sqrt((c[0] - local_rx) ** 2 + (c[1] - local_ry) ** 2),
        )
        return True, int(best[0]) + x1, int(best[1]) + y1, int(best[2])

    # ── Template scoring ──────────────────────────────────────────────────────

    def _score_at(self, roi: np.ndarray, cx_r: int, cy_r: int,
                  radius: int) -> float:
        """
        Single-point template correlation at the ring centre.
        Crops the character interior, normalises to the template canonical size,
        and returns TM_CCORR_NORMED score (1×1 result — no sliding window).
        """
        interior = self._crop_interior(roi, float(cx_r), float(cy_r),
                                        float(radius))
        if interior is None:
            return 0.0
        patch = self._make_template(interior)
        # patch and self._char_template are identical shape → result is (1, 1)
        result = cv2.matchTemplate(
            patch, self._char_template, cv2.TM_CCORR_NORMED,
            mask=self._char_mask,
        )
        return float(result[0, 0])

    # ── Template / mask construction ──────────────────────────────────────────

    def _make_template(self, bgr: np.ndarray) -> np.ndarray:
        """Resize to canonical size and convert to normalised HSV-CLAHE float."""
        patch = cv2.resize(bgr, (_MM_CHAR_W_PX, _MM_CHAR_H_PX),
                           interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
        h   = hsv[:, :, 0].astype(np.float32) / 179.0
        s   = hsv[:, :, 1].astype(np.float32) / 255.0
        v   = self._clahe.apply(hsv[:, :, 2]).astype(np.float32) / 255.0
        return np.stack([h, s, v], axis=2)

    @staticmethod
    def _make_circle_mask(h_px: int, w_px: int) -> np.ndarray:
        mask = np.zeros((h_px, w_px), dtype=np.float32)
        cv2.circle(mask, (w_px // 2, h_px // 2),
                   int(round(min(h_px, w_px) * 0.45)), 255.0, -1)
        return mask

    @staticmethod
    def _crop_interior(bgr: np.ndarray, cx: float, cy: float,
                       r: float) -> Optional[np.ndarray]:
        """Crop the character face interior from inside the ring."""
        half_w = max(4, int(round(r * _MM_CHAR_W_F)))
        half_h = max(4, int(round(r * _MM_CHAR_H_F)))
        x1, y1 = int(round(cx)) - half_w, int(round(cy)) - half_h
        x2, y2 = int(round(cx)) + half_w, int(round(cy)) + half_h
        hh, ww = bgr.shape[:2]
        if x1 < 0 or y1 < 0 or x2 > ww or y2 > hh:
            return None
        patch = bgr[y1:y2, x1:x2]
        return patch if patch.size > 0 else None
