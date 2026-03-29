"""Minimap overlay: crosshair and ROI boundary."""
import cv2
import numpy as np
from typing import Optional

from ..detection.screen import Screen
from ..minimap.tracker import MinimapState, MINIMAP_ROI
from ..utils.image import DISPLAY_SCALE, draw_roi_box, draw_roi_label

# Crosshair colours keyed by track_state
_COLOUR_TRACKING  = (0, 255, 255)   # cyan   — full template + Hough
_COLOUR_RING_ONLY = (0, 140, 255)   # orange — ring only (hazard / icon-swap)
_COLOUR_REACQUIRE = (0, 220, 255)   # yellow — building re-acquire streak
_COLOUR_MINIMAP   = (0, 255, 255)   # cyan   — ROI box default


def draw_minimap_crosshair(
    frame:   Optional[np.ndarray],
    display: Optional[np.ndarray],
    screen:  Screen,
    mm:      MinimapState,
    tracker=None,
):
    """
    Draw the minimap ROI boundary and crosshair at the smoothed player position.
    *tracker* is optional — if supplied, the active (possibly custom) ROI is read from it.

    Crosshair colour indicates tracking confidence:
      cyan   → full tracking (template + Hough)
      orange → ring-only (hazard / icon-swap: ring found, face not matched)
      yellow → re-acquiring after large jump or respawn
    """
    if screen != Screen.RACING:
        return

    if tracker is not None:
        rx, ry = tracker._roi_x, tracker._roi_y
        rw, rh = tracker._roi_w, tracker._roi_h
        is_custom = (rx != MINIMAP_ROI[0] or ry != MINIMAP_ROI[1] or
                     rw != MINIMAP_ROI[2] or rh != MINIMAP_ROI[3])
    else:
        rx, ry = MINIMAP_ROI[0], MINIMAP_ROI[1]
        rw, rh = MINIMAP_ROI[2], MINIMAP_ROI[3]
        is_custom = False

    roi_rect   = (rx, ry, rx + rw, ry + rh)
    roi_colour = (255, 120, 0) if is_custom else _COLOUR_MINIMAP

    ts = mm.track_state  # "idle" | "tracking" | "ring_only" | "reacquire" | "lost"
    state_suffix = {
        "tracking":  " [ring + face]",
        "ring_only": " [ring]",
        "reacquire": " [reacquiring]",
        "lost":      " [lost]",
        "idle":      " [idle]",
    }.get(ts, "")
    tracking_label = "minimap" + (" [custom ROI]" if is_custom else "") + state_suffix

    if frame is not None:
        draw_roi_box(frame, roi_rect, roi_colour, thickness=2 if is_custom else 1)
    if display is not None:
        draw_roi_label(display, roi_rect, roi_colour, tracking_label)

    # No position to draw if not tracking
    if not mm.tracking:
        return

    colour = {
        "ring_only": _COLOUR_RING_ONLY,
        "reacquire": _COLOUR_REACQUIRE,
    }.get(ts, _COLOUR_TRACKING)

    cx_s = int(round(mm.cx_smooth))
    cy_s = int(round(mm.cy_smooth))
    arm  = 16
    gap  = 4
    s    = DISPLAY_SCALE

    if frame is not None:
        BLACK = (0, 0, 0)
        for pts in [((cx_s - arm, cy_s), (cx_s - gap, cy_s)),
                    ((cx_s + gap, cy_s), (cx_s + arm, cy_s)),
                    ((cx_s, cy_s - arm), (cx_s, cy_s - gap)),
                    ((cx_s, cy_s + gap), (cx_s, cy_s + arm))]:
            cv2.line(frame, pts[0], pts[1], BLACK,  4, cv2.LINE_AA)
            cv2.line(frame, pts[0], pts[1], colour, 2, cv2.LINE_AA)
        cv2.circle(frame, (cx_s, cy_s), 4, BLACK,  -1, cv2.LINE_AA)
        cv2.circle(frame, (cx_s, cy_s), 2, colour, -1, cv2.LINE_AA)

    if display is not None:
        cx_d  = int(round(cx_s * s))
        cy_d  = int(round(cy_s * s))
        arm_d = max(5, int(arm * s))
        gap_d = max(2, int(gap * s))
        BLACK = (0, 0, 0)

        for pts in [((cx_d - arm_d, cy_d), (cx_d - gap_d, cy_d)),
                    ((cx_d + gap_d, cy_d), (cx_d + arm_d, cy_d)),
                    ((cx_d, cy_d - arm_d), (cx_d, cy_d - gap_d)),
                    ((cx_d, cy_d + gap_d), (cx_d, cy_d + arm_d))]:
            cv2.line(display, pts[0], pts[1], BLACK,  3, cv2.LINE_AA)
            cv2.line(display, pts[0], pts[1], colour, 1, cv2.LINE_AA)
        cv2.circle(display, (cx_d, cy_d), 3, BLACK,  -1, cv2.LINE_AA)
        cv2.circle(display, (cx_d, cy_d), 2, colour, -1, cv2.LINE_AA)

        label = f"mm {state_suffix.strip('[] ')} ({cx_s},{cy_s}) s={mm.last_score:.2f}"
        cv2.putText(display, label, (cx_d + 6, cy_d - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(display, label, (cx_d + 6, cy_d - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, colour, 1, cv2.LINE_AA)
