"""LapTracker, LapState, digit template loading."""
import os
import time
import cv2
import numpy as np
from dataclasses import dataclass
from typing import Dict, Optional

from ..detection.screen import Screen
from ..utils.paths import resource_path

LAP_CURRENT_ROI = (282, 979, 282 + 38, 979 + 49)
LAP_TOTAL_ROI   = (341, 990, 341 + 27, 990 + 38)
LAP_DIGIT_THRESHOLD = 0.70


def load_digit_templates(
    directory: str,
    target_height: int,
    blur_ksize: int = 3,
) -> Dict[str, np.ndarray]:
    """Load grayscale common-canvas digit templates, scaled so the glyph is
    ~target_height tall (canvas = glyph + 2*2px pad, built by
    scripts/harvest_digit_templates.py from real capture). All ten templates
    share one shape - matching compares every candidate over the same
    support, which is what stops a narrow '1' from winning inside a damaged
    '8'."""
    templates: Dict[str, np.ndarray] = {}
    directory = resource_path(directory)
    if not os.path.exists(directory):
        print(f"[LapTracker] Template directory not found: {directory}")
        return templates

    out_h = out_w = 0
    for filename in sorted(os.listdir(directory)):
        stem = filename[:-4]
        if not filename.lower().endswith('.png') or not (len(stem) == 1 and stem.isdigit()):
            continue
        src = cv2.imread(os.path.join(directory, filename), cv2.IMREAD_GRAYSCALE)
        if src is None:
            print(f"[WARN] Could not load {filename}")
            continue
        scale  = target_height / (src.shape[0] - 4)      # canvas pad = 2 each side
        out_w  = max(1, int(round(src.shape[1] * scale)))
        out_h  = max(1, int(round(src.shape[0] * scale)))
        interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
        scaled = cv2.resize(src, (out_w, out_h), interpolation=interp)
        templates[stem] = cv2.GaussianBlur(scaled, (blur_ksize, blur_ksize), 0)

    print(f"[LapTracker] Loaded {len(templates)} digit templates "
          f"(glyph h~{target_height}px, canvas {out_h}x{out_w}) from '{directory}'")
    return templates


def read_digit_roi(
    frame: np.ndarray,
    roi: tuple,
    templates: Dict[str, np.ndarray],
    threshold: float = LAP_DIGIT_THRESHOLD,
    binary_thresh: int = 170,            # kept for call-site compatibility; unused
    reconfirm_digit: Optional[int] = None,
    reconfirm_threshold: float = 0.85,
    margin: float = 0.05,
) -> tuple:
    """Match the slot against all digit templates over a common support.

    Grayscale NCC (TM_CCOEFF_NORMED) - gain/offset invariant, survives washed
    capture. Returns (digit, score); (None, best_score) when below threshold
    OR when the winner fails the best-vs-second margin (an ambiguous slot is
    safer unread than guessed: every consumer re-reads)."""
    x1, y1, x2, y2 = roi
    crop = frame[y1:y2, x1:x2]
    if crop.size == 0 or not templates:
        return None, 0.0
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop
    gray = cv2.GaussianBlur(gray, (3, 3), 0)

    def _score(tmpl: np.ndarray) -> float:
        if tmpl.shape[0] > gray.shape[0] or tmpl.shape[1] > gray.shape[1]:
            return 0.0
        result = cv2.matchTemplate(gray, tmpl, cv2.TM_CCOEFF_NORMED)
        return float(cv2.minMaxLoc(result)[1])

    reconfirm_key = str(reconfirm_digit) if reconfirm_digit is not None else None
    if reconfirm_key and reconfirm_key in templates:
        s = _score(templates[reconfirm_key])
        if s >= reconfirm_threshold:
            return reconfirm_digit, s

    scores = sorted(((name, _score(t)) for name, t in templates.items()),
                    key=lambda kv: kv[1], reverse=True)
    best_name, best = scores[0]
    second = scores[1][1] if len(scores) > 1 else 0.0
    if best < threshold or (best - second) < margin:
        return None, best
    return int(best_name), best


@dataclass
class LapState:
    current_lap: Optional[int] = None
    current_lap_conf: float    = 0.0
    total_laps:  Optional[int] = None
    total_laps_conf: float     = 0.0


class LapTracker:
    """Detects current/total lap counts during RACING from two digit ROIs."""

    def __init__(
        self,
        digit_dir: str = 'images/digits',
        scan_interval: float = 0.1,
        current_lap_digit_h: int = 40,
        total_laps_digit_h:  int = 28,
    ):
        self.scan_interval = scan_interval
        self._current_templates = load_digit_templates(digit_dir, current_lap_digit_h)
        self._total_templates   = load_digit_templates(digit_dir, total_laps_digit_h)
        self.state:      LapState = LapState()
        self._last_scan: float    = 0.0

    def reset(self):
        self.state = LapState()

    def debug_frame(self, frame: np.ndarray, out_dir: str = "debug_laps"):
        os.makedirs(out_dir, exist_ok=True)
        configs = [
            ("current", LAP_CURRENT_ROI, self._current_templates),
            ("total",   LAP_TOTAL_ROI,   self._total_templates),
        ]
        for tag, roi, templates in configs:
            x1, y1, x2, y2 = roi
            crop = frame[y1:y2, x1:x2]
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop
            _, processed = cv2.threshold(gray, 170, 255, cv2.THRESH_BINARY)
            crop_path = os.path.join(out_dir, f"{tag}_roi_crop.png")
            cv2.imwrite(crop_path, processed)
            print(f"[LapTracker.debug] {tag} ROI crop: {processed.shape} -> {crop_path}")
            best_digit, best_score, best_tmpl = None, 0.0, None
            for digit, tmpl in sorted(templates.items()):
                if tmpl.shape[0] > processed.shape[0] or tmpl.shape[1] > processed.shape[1]:
                    continue
                result = cv2.matchTemplate(processed, tmpl, cv2.TM_CCOEFF_NORMED)
                score  = float(cv2.minMaxLoc(result)[1])
                if score > best_score:
                    best_score, best_digit, best_tmpl = score, digit, tmpl
            if best_tmpl is not None:
                tmpl_path = os.path.join(out_dir, f"{tag}_best_{best_digit}_{best_score:.2f}.png")
                cv2.imwrite(tmpl_path, best_tmpl)
                print(f"  {tag} winner: digit={best_digit} score={best_score:.3f}")

    def update(self, frame: np.ndarray, screen: Screen) -> tuple:
        """Returns (LapState, lap_incremented: bool)."""
        if screen != Screen.RACING:
            return self.state, False

        now = time.perf_counter()
        if (now - self._last_scan) < self.scan_interval:
            return self.state, False
        self._last_scan = now

        lap_incremented = False
        digit, conf = read_digit_roi(
            frame, LAP_CURRENT_ROI, self._current_templates,
            reconfirm_digit=self.state.current_lap,
        )
        if digit is not None:
            if digit != self.state.current_lap and self.state.current_lap is not None:
                print(f"  Lap: {digit} ({conf:.3f})")
                lap_incremented = True
            self.state.current_lap      = digit
            self.state.current_lap_conf = conf

        if self.state.total_laps is None:
            digit, conf = read_digit_roi(frame, LAP_TOTAL_ROI, self._total_templates)
            if digit is not None:
                print(f"  Total laps: {digit} ({conf:.3f})")
                self.state.total_laps      = digit
                self.state.total_laps_conf = conf

        return self.state, lap_incremented
