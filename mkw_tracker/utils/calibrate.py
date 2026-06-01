"""Auto-calibration solver: fit gain/offset/gamma against reference frames.

Given the user's live captures of known static targets (Switch HDR test
patterns) and shipped reference frames of the same targets, sample patch means
from each pair, pool them, and solve for the per-channel (gain, offset) +
shared gamma that minimises the patch-wise RMSE.  Result is fed straight into
utils/normalize.Normalizer.

Two reference slots are supported (the Switch has two HDR test screens).
Pooling patches from both pairs doubles the conditioning, which materially
improves the gamma fit.  Either or both refs may be present; the solver works
with whichever slots have a matching live capture.

References are loaded from images/calibration/switch_hdr_test_{1,2}.png.
The dev script scripts/capture_calibration_ref.py captures these from the
developer's own setup.  When no refs are shipped, auto-calibrate falls back
to manual sliders in the wizard.
"""
import os
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from .paths import resource_path


# The Switch HDR test patterns occupy this sub-region of the 1920x1080 frame;
# everything outside is system UI chrome (titles, button hints, etc.) that
# differs between capture cards, locales, and HDR/SDR modes.  Patches are
# sampled only INSIDE this ROI on both reference and live frames so the
# solver never fits against UI variability.
PATTERN_ROI: Tuple[int, int, int, int] = (482, 162, 482 + 956, 162 + 532)

# 24 sample patches (6x4 grid of 80x80) spread evenly inside PATTERN_ROI.
# Coordinates are in full 1920x1080 frame space, NOT relative to the ROI.
DEFAULT_PATCHES: List[Tuple[int, int, int, int]] = [
    (x, y, x + 80, y + 80)
    for y in (204, 326, 448, 570)
    for x in (550, 698, 846, 994, 1142, 1290)
]

# Slot-keyed reference frames.  Switch HDR calibration walks the user through
# 7 distinct test patterns; we exploit all of them.  Each slot is independent
# - calibration works with any non-empty subset of shipped+captured slots.
NUM_SLOTS: int = 7
REF_PATHS: Dict[int, str] = {
    slot: f"images/calibration/switch_hdr_test_{slot}.png"
    for slot in range(1, NUM_SLOTS + 1)
}

# Gamma values to grid-search.  0.50..2.00 in steps of 0.05.  Capture-card
# gamma drift is usually mild; fine spacing keeps the linear fit on top of
# it well-conditioned.
_GAMMA_CANDIDATES: List[float] = [round(0.50 + 0.05 * i, 2) for i in range(31)]

# Sanity bounds on the output transform - clamped here so wild fits from
# degenerate patches don't produce unusable LUTs downstream.
_GAIN_MIN,   _GAIN_MAX   = 0.30, 3.00
_OFFSET_MIN, _OFFSET_MAX = -100, 100
_GAMMA_MIN,  _GAMMA_MAX  = 0.50, 2.00


def _clip(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _patch_means(frame: np.ndarray, patches) -> np.ndarray:
    """Per-channel BGR mean per patch.  Shape: (N, 3) float64."""
    out = np.zeros((len(patches), 3), dtype=np.float64)
    for i, (x1, y1, x2, y2) in enumerate(patches):
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            continue
        out[i] = crop.reshape(-1, 3).mean(axis=0)
    return out


def _fit_gain_offset(live: np.ndarray, ref: np.ndarray) -> Tuple[float, float, float]:
    """Least-squares fit of `out = gain * live + offset` minimising ||out - ref||.

    Returns (gain, offset, rmse).  When `live` has too little variance to fit
    a slope reliably, falls back to (1, mean(ref) - mean(live), residual_rmse).
    """
    if live.size < 2 or float(np.std(live)) < 1.0:
        offset = float(np.mean(ref) - np.mean(live))
        pred   = live + offset
        rmse   = float(np.sqrt(np.mean((pred - ref) ** 2)))
        return 1.0, offset, rmse

    A = np.column_stack([live, np.ones_like(live)])
    sol, *_ = np.linalg.lstsq(A, ref, rcond=None)
    gain, offset = float(sol[0]), float(sol[1])
    pred = live * gain + offset
    rmse = float(np.sqrt(np.mean((pred - ref) ** 2)))
    return gain, offset, rmse


def solve_transform(
    pairs: List[Tuple[np.ndarray, np.ndarray]],
    patches = DEFAULT_PATCHES,
) -> dict:
    """Fit per-channel gain+offset and shared gamma against one or more
    (live_frame, ref_frame) pairs.

    Patches from every pair are pooled into a single least-squares fit, so two
    pairs double the sample count and improve gamma conditioning.

    Returns a dict shaped like the calib_* settings keys plus a fit_quality:
        {
          "gain_r": float, "gain_g": float, "gain_b": float,
          "offset_r": int, "offset_g": int, "offset_b": int,
          "gamma": float,
          "fit_quality": float    # RMSE across all channels, 0-255 scale.
                                  # <5 great, 5-15 ok, >20 poor.
        }
    """
    if not pairs:
        raise ValueError("solve_transform requires at least one (live, ref) pair")

    live_chunks = [_patch_means(live, patches) for live, _ in pairs]
    ref_chunks  = [_patch_means(ref,  patches) for _, ref  in pairs]
    live_means  = np.vstack(live_chunks)   # (N * pairs, 3) BGR
    ref_means   = np.vstack(ref_chunks)

    best: Optional[Tuple[float, list, float]] = None
    for gamma in _GAMMA_CANDIDATES:
        live_g = (live_means / 255.0) ** gamma * 255.0
        fits = [_fit_gain_offset(live_g[:, c], ref_means[:, c]) for c in range(3)]
        rmse_total = float(np.sqrt(np.mean([f[2] ** 2 for f in fits])))
        if best is None or rmse_total < best[2]:
            best = (gamma, fits, rmse_total)

    gamma, fits, rmse = best  # type: ignore[misc]
    # fits is ordered [B, G, R] to match OpenCV's BGR channel layout
    (gb, ob, _), (gg, og, _), (gr, or_, _) = fits

    return {
        "gain_r":   _clip(gr,  _GAIN_MIN,   _GAIN_MAX),
        "gain_g":   _clip(gg,  _GAIN_MIN,   _GAIN_MAX),
        "gain_b":   _clip(gb,  _GAIN_MIN,   _GAIN_MAX),
        "offset_r": int(round(_clip(or_, _OFFSET_MIN, _OFFSET_MAX))),
        "offset_g": int(round(_clip(og,  _OFFSET_MIN, _OFFSET_MAX))),
        "offset_b": int(round(_clip(ob,  _OFFSET_MIN, _OFFSET_MAX))),
        "gamma":        _clip(gamma, _GAMMA_MIN, _GAMMA_MAX),
        "fit_quality":  rmse,
    }


def load_reference_frames() -> Dict[int, np.ndarray]:
    """Load all shipped reference frames keyed by slot.  Missing slots omitted.

    Returned frames are normalised to 1920x1080 (the same space all detectors
    operate in), so the patch coordinates in DEFAULT_PATCHES are always valid.
    """
    out: Dict[int, np.ndarray] = {}
    for slot, rel in REF_PATHS.items():
        path = resource_path(rel)
        if not os.path.exists(path):
            continue
        img = cv2.imread(path, cv2.IMREAD_COLOR)
        if img is None:
            continue
        h, w = img.shape[:2]
        if (w, h) != (1920, 1080):
            img = cv2.resize(img, (1920, 1080), interpolation=cv2.INTER_LINEAR)
        out[slot] = img
    return out
