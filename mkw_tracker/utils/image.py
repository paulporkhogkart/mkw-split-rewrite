"""Common image-drawing helpers shared across overlay modules."""
import cv2
import numpy as np

DISPLAY_SCALE = 720 / 1080   # 1080p -> 720p


def draw_roi_box(
    frame: np.ndarray,
    roi: tuple,
    color: tuple,
    thickness: int = 2,
):
    """Draw a bounding box on *frame* (full-res). No label."""
    x1, y1, x2, y2 = roi
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness)


def draw_roi_label(
    display: np.ndarray,
    roi: tuple,
    color: tuple,
    label: str,
    font_scale: float = 0.45,
):
    """Draw the label for an ROI box on the already-resized display frame."""
    x1, y1, x2, y2 = [int(v * DISPLAY_SCALE) for v in roi]
    (tw, th), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, 1)
    lx = x1
    ly = y1 - 4
    if ly - th - baseline < 0:
        ly = y2 + th + 4
    cv2.rectangle(display, (lx, ly - th - baseline), (lx + tw + 4, ly + baseline),
                  color, -1)
    cv2.putText(display, label, (lx + 2, ly),
                cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 0, 0), 1, cv2.LINE_AA)
