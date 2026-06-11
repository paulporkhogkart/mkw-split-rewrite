"""Whole-marker "badge" template for the minimap player marker.

The badge is the character face plus the white ring the game draws around the
local player. One masked, zero-mean NCC template over Lab pixels: HDR-flattened
capture shifts gain/offset per window, which TM_CCOEFF_NORMED cancels, and the
ring contributes washout-resistant structure that map terrain cannot imitate.
Measured margins and the design rationale live in
docs/superpowers/specs/2026-06-11-minimap-badge-tracking-design.md.
"""
import cv2
import numpy as np
from typing import Optional, Tuple

BADGE_HALF   = 22   # template half-side -> 44x44 crop
BADGE_PAD    = 8    # slide reach around the search centre (covers Hough error;
                    # the ring's thin annulus makes the peak ~4px sharp)
BADGE_MASK_R = 21   # circular mask: face + ring, halo/corner terrain excluded

_ANNULUS_RADII = (19, 21, 23)
_ANNULUS_THICK = 3


def _lab(bgr: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32) / 255.0


def _crop_padded(roi: np.ndarray, x1: int, y1: int, x2: int, y2: int) -> np.ndarray:
    """Crop roi[y1:y2, x1:x2], replicate-padding any part outside the image."""
    hh, ww = roi.shape[:2]
    lpad = max(0, -x1); tpad = max(0, -y1)
    rpad = max(0, x2 - ww); bpad = max(0, y2 - hh)
    crop = roi[max(0, y1):min(hh, y2), max(0, x1):min(ww, x2)]
    if lpad or tpad or rpad or bpad:
        crop = cv2.copyMakeBorder(crop, tpad, bpad, lpad, rpad,
                                  cv2.BORDER_REPLICATE)
    return crop


def _make_annulus(r: int, pad: int = 4) -> np.ndarray:
    size = 2 * (r + pad) + 1
    img = np.zeros((size, size), dtype=np.uint8)
    cv2.circle(img, (size // 2, size // 2), r, 255, _ANNULUS_THICK, cv2.LINE_AA)
    return img


def refine_seed_centre(roi: np.ndarray, cx: int, cy: int,
                       window: int = 16) -> Tuple[int, int]:
    """Snap a stored seed point onto the actual ring centre.

    Stored seeds are hand-captured once and sit a few px off the live badge
    (start position varies per setup); an off-centre seed bakes that offset
    into the template for the whole race. One annulus-NCC pass over a
    +/-window box fixes it. Returns the input unchanged when nothing
    ring-like is found.
    """
    reach = window + max(_ANNULUS_RADII) + 4
    x1, y1 = cx - reach, cy - reach
    crop = _crop_padded(roi, x1, y1, cx + reach + 1, cy + reach + 1)
    gray = cv2.GaussianBlur(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    best, bx, by = -1.0, cx, cy
    for r in _ANNULUS_RADII:
        tpl = _make_annulus(r)
        if gray.shape[0] < tpl.shape[0] or gray.shape[1] < tpl.shape[1]:
            continue
        res = cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED)
        _, mx, _, loc = cv2.minMaxLoc(res)
        if mx > best:
            half = tpl.shape[0] // 2
            best, bx, by = mx, x1 + loc[0] + half, y1 + loc[1] + half
    # Floor between terrain-noise peaks (<=0.31 measured) and seed-time ring
    # scores (>=0.49 across the bootest/koops/short clips). No ring -> no move.
    if best < 0.35:
        return cx, cy
    # clamp to the window: a stronger ring elsewhere must not steal the seed
    bx = max(cx - window, min(cx + window, bx))
    by = max(cy - window, min(cy + window, by))
    return bx, by


class BadgeTemplate:
    """Masked Lab template of the player badge, locked once per race at seed."""

    def __init__(self):
        self._tpl:  Optional[np.ndarray] = None   # (44, 44, 3) float32 Lab
        self._mask: Optional[np.ndarray] = None   # (44, 44, 3) float32 0/1
        self._bgr:  Optional[np.ndarray] = None   # (44, 44, 3) uint8, for display

    @property
    def ready(self) -> bool:
        return self._tpl is not None

    @property
    def bgr(self) -> Optional[np.ndarray]:
        """The raw BGR badge crop (for overlay / IPC sample display)."""
        return self._bgr

    def clear(self):
        self._tpl = None
        self._mask = None
        self._bgr = None

    def build(self, roi: np.ndarray, cx: int, cy: int) -> bool:
        h = BADGE_HALF
        crop = _crop_padded(roi, cx - h, cy - h, cx + h, cy + h)
        if crop.shape[:2] != (2 * h, 2 * h):
            return False
        self._bgr = crop.copy()
        self._tpl = _lab(crop)
        m = np.zeros((2 * h, 2 * h), dtype=np.float32)
        cv2.circle(m, (h, h), BADGE_MASK_R, 1.0, -1)
        self._mask = np.repeat(m[:, :, None], 3, axis=2)
        return True

    def score(self, roi: np.ndarray, cx: int, cy: int):
        """Masked zero-mean NCC slid +/-BADGE_PAD around (cx, cy).

        Returns (best_score, (rx, ry)) where (rx, ry) is the correlation
        argmax - the position to publish. (0.0, None) when not ready or the
        centre is outside the ROI.
        """
        if not self.ready:
            return 0.0, None
        hh, ww = roi.shape[:2]
        if not (0 <= cx < ww and 0 <= cy < hh):
            return 0.0, None
        h, pad = BADGE_HALF, BADGE_PAD
        big = _crop_padded(roi, cx - h - pad, cy - h - pad,
                           cx + h + pad, cy + h + pad)
        res = cv2.matchTemplate(_lab(big), self._tpl, cv2.TM_CCOEFF_NORMED,
                                mask=self._mask)
        res = np.nan_to_num(res, nan=-1.0, posinf=-1.0, neginf=-1.0)
        _, mx, _, loc = cv2.minMaxLoc(res)
        return float(mx), (cx - pad + loc[0], cy - pad + loc[1])
