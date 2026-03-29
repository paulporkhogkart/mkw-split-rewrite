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
    binary_thresh: int = 127,
) -> Dict[str, np.ndarray]:
    """Load digit templates from *directory*, scaled to *target_height*."""
    templates: Dict[str, np.ndarray] = {}
    directory = resource_path(directory)
    if not os.path.exists(directory):
        print(f"[LapTracker] Template directory not found: {directory}")
        return templates

    for filename in sorted(os.listdir(directory)):
        if not filename.lower().endswith('.png'):
            continue
        stem = filename[:-4]
        if not (len(stem) == 1 and stem.isdigit()):
            continue
        path = os.path.join(directory, filename)
        src  = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if src is None:
            print(f"[WARN] Could not load {path}")
            continue

        _, binary = cv2.threshold(src, binary_thresh, 255, cv2.THRESH_BINARY)
        coords = cv2.findNonZero(binary)
        if coords is None:
            continue
        x, y, w, h = cv2.boundingRect(coords)
        cropped = binary[y:y + h, x:x + w]

        scale  = target_height / h
        new_w  = max(1, int(w * scale))
        interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
        scaled = cv2.resize(cropped, (new_w, target_height), interpolation=interp)
        _, scaled = cv2.threshold(scaled, 127, 255, cv2.THRESH_BINARY)
        templates[stem] = scaled

    print(f"[LapTracker] Loaded {len(templates)} digit templates "
          f"(target h={target_height}px) from '{directory}'")
    return templates


def read_digit_roi(
    frame: np.ndarray,
    roi: tuple,
    templates: Dict[str, np.ndarray],
    threshold: float = LAP_DIGIT_THRESHOLD,
    binary_thresh: int = 170,
    reconfirm_digit: Optional[int] = None,
    reconfirm_threshold: float = 0.85,
) -> tuple:
    """Crop *roi* from *frame* and match against *templates*. Returns (digit, score)."""
    x1, y1, x2, y2 = roi
    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        return None, 0.0

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop
    _, processed = cv2.threshold(gray, binary_thresh, 255, cv2.THRESH_BINARY)

    def _score(tmpl: np.ndarray) -> float:
        if tmpl.shape[0] > processed.shape[0] or tmpl.shape[1] > processed.shape[1]:
            return 0.0
        result = cv2.matchTemplate(processed, tmpl, cv2.TM_CCOEFF_NORMED)
        return float(cv2.minMaxLoc(result)[1])

    reconfirm_score: float = 0.0
    reconfirm_key = str(reconfirm_digit) if reconfirm_digit is not None else None
    if reconfirm_key and reconfirm_key in templates:
        reconfirm_score = _score(templates[reconfirm_key])
        if reconfirm_score >= reconfirm_threshold:
            return reconfirm_digit, reconfirm_score

    best_name: Optional[str] = reconfirm_key if reconfirm_score >= threshold else None
    best_score: float = reconfirm_score if reconfirm_score >= threshold else 0.0
    for name, tmpl in templates.items():
        if name == reconfirm_key:
            continue
        s = _score(tmpl)
        if s > best_score:
            best_score = s
            best_name  = name

    if best_score < threshold or best_name is None:
        return None, best_score
    return int(best_name), best_score


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
        digit_dir: str = 'images/timestamps/cropped',
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
