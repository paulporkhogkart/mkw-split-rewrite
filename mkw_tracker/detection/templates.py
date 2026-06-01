"""Template loading and matching helpers shared across detection subsystems."""
import os
import cv2
import numpy as np
from typing import Dict, Optional

from ..utils.paths import resource_path


def prepare_text_edges(
    bgr: np.ndarray,
    blur_ksize: int = 3,
    canny_low: int = 30,
    canny_high: int = 100,
) -> np.ndarray:
    """
    Extract Canny edges from a BGR image - background-agnostic text detection.
    Templates and live ROI crops must both use this function for matching to work.
    """
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (blur_ksize, blur_ksize), 0)
    return cv2.Canny(blurred, canny_low, canny_high)


def purge_tight_pngs(directory: str):
    """Delete all _tight.png cache files in *directory*."""
    directory = resource_path(directory)
    if not os.path.exists(directory):
        return
    count = 0
    for filename in os.listdir(directory):
        if filename.lower().endswith('_tight.png'):
            os.remove(os.path.join(directory, filename))
            count += 1
    if count:
        print(f"[templates] Purged {count} _tight.png file(s) from {directory}")


def load_template_dir(
    directory: str,
    tight: bool = False,
    binary_thresh: int = 170,
    thresh_type: int = cv2.THRESH_BINARY,
    white_text: bool = False,
) -> Dict[str, np.ndarray]:
    """
    Load all PNGs from *directory* as grayscale templates keyed by display name.

    tight=True  - tight-crop each template to non-zero bounding box (cached as
                  <name>_tight.png).  Not applied when white_text=True.
    white_text  - process with Canny edge detection instead of binary threshold.
                  Full-width templates preserved to keep relative letter spacing.
    """
    templates: Dict[str, np.ndarray] = {}
    directory = resource_path(directory)
    if not os.path.exists(directory):
        return templates

    for filename in os.listdir(directory):
        if not filename.lower().endswith('.png') or filename.lower().endswith('_tight.png'):
            continue
        base = filename[:-4]
        name = base.replace('_', ' ').title()
        path = os.path.join(directory, filename)
        tight_path = os.path.join(directory, f"{base}_tight.png")

        if tight and not white_text:
            if os.path.exists(tight_path):
                img = cv2.imread(tight_path, cv2.IMREAD_GRAYSCALE)
                if img is not None:
                    templates[name] = img
                    continue
            src = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
            if src is None:
                print(f"[WARN] Could not load template: {path}")
                continue
            _, mask = cv2.threshold(src, binary_thresh, 255, thresh_type)
            coords = cv2.findNonZero(mask)
            if coords is None:
                print(f"[WARN] Blank template after threshold, skipping: {path}")
                continue
            x, y, w, h = cv2.boundingRect(coords)
            cropped = mask[y:y + h, x:x + w]
            cv2.imwrite(tight_path, cropped)
            templates[name] = cropped
        elif white_text:
            src = cv2.imread(path, cv2.IMREAD_COLOR)
            if src is None:
                print(f"[WARN] Could not load template: {path}")
                continue
            templates[name] = prepare_text_edges(src)
        else:
            img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
            if img is not None:
                templates[name] = img

    return templates


def prepare_roi(
    roi_bgr: np.ndarray,
    binary_thresh: int = 170,
    thresh_type: int = cv2.THRESH_BINARY,
) -> Optional[np.ndarray]:
    """Convert a BGR ROI crop to thresholded grayscale ready for matchTemplate."""
    if roi_bgr is None:
        return None
    gray = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, binary_thresh, 255, thresh_type)
    return thresh


def match_top_n(
    roi_bgr: np.ndarray,
    templates: Dict[str, np.ndarray],
    n: int = 5,
    binary_thresh: int = 170,
    thresh_type: int = cv2.THRESH_BINARY,
    _prepared: Optional[np.ndarray] = None,
) -> list:
    """Return top-N [(name, score)] sorted by score descending (no threshold filter)."""
    if _prepared is not None:
        processed = _prepared
    else:
        processed = prepare_roi(roi_bgr, binary_thresh, thresh_type)
        if processed is None:
            return []

    def _score(tmpl: np.ndarray) -> float:
        if tmpl.shape[0] > processed.shape[0] or tmpl.shape[1] > processed.shape[1]:
            return 0.0
        result = cv2.matchTemplate(processed, tmpl, cv2.TM_CCOEFF_NORMED)
        return float(cv2.minMaxLoc(result)[1])

    scores = [(name, _score(tmpl)) for name, tmpl in templates.items()]
    scores.sort(key=lambda x: x[1], reverse=True)
    return scores[:n]


def match_best(
    roi_bgr: np.ndarray,
    templates: Dict[str, np.ndarray],
    binary_thresh: int = 170,
    threshold: float = 0.7,
    thresh_type: int = cv2.THRESH_BINARY,
    reconfirm_name: Optional[str] = None,
    reconfirm_threshold: Optional[float] = None,
    _prepared: Optional[np.ndarray] = None,
) -> tuple:
    """
    Return (best_name, score) from template dict, or (None, score) if below threshold.

    If reconfirm_name is given, that template is checked first.  Only runs the
    full scan when the reconfirm check fails - the common case (nothing changed)
    costs exactly one template match.

    Pass _prepared to supply an already-processed ROI and skip BGR→gray→threshold.
    """
    if _prepared is not None:
        processed = _prepared
    else:
        processed = prepare_roi(roi_bgr, binary_thresh, thresh_type)
        if processed is None:
            return None, 0.0

    def _score(tmpl: np.ndarray) -> float:
        if tmpl.shape[0] > processed.shape[0] or tmpl.shape[1] > processed.shape[1]:
            return 0.0
        result = cv2.matchTemplate(processed, tmpl, cv2.TM_CCOEFF_NORMED)
        return float(cv2.minMaxLoc(result)[1])

    _reconfirm_threshold = reconfirm_threshold if reconfirm_threshold is not None else threshold
    reconfirm_score: float = 0.0
    if reconfirm_name and reconfirm_name in templates:
        reconfirm_score = _score(templates[reconfirm_name])
        if reconfirm_score >= _reconfirm_threshold:
            return reconfirm_name, reconfirm_score

    best_name: Optional[str] = reconfirm_name if reconfirm_score >= threshold else None
    best_score: float = reconfirm_score if reconfirm_score >= threshold else 0.0
    for name, tmpl in templates.items():
        if name == reconfirm_name:
            continue
        s = _score(tmpl)
        if s > best_score:
            best_score = s
            best_name = name

    if best_score < threshold:
        return None, best_score
    return best_name, best_score
