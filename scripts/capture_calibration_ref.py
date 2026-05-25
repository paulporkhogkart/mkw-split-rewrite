"""Dev tool: capture calibration reference frames from the configured camera.

Switch HDR calibration walks the user through 7 distinct test patterns and we
exploit all of them.  Run this script with one or more slot numbers and it
captures each in turn, leaving the preview open between captures so you can
navigate the Switch to the next pattern without restarting.

Usage:
    python scripts/capture_calibration_ref.py 1                # one slot
    python scripts/capture_calibration_ref.py 1 2 3 4 5 6 7    # all seven, sequential
    python scripts/capture_calibration_ref.py 3 5              # arbitrary subset

A live preview window opens.  For each slot in turn the title bar prompts
which slot you're capturing.  Keys (case-insensitive):
    c   Capture current frame for the active slot → save as
        images/calibration/switch_hdr_test_<slot>.png, advance to next slot
    n   Skip this slot without saving, advance to next
    q   Quit immediately (any remaining slots are skipped)

The same camera-source logic as the main app is used (HDR ffmpeg pipe or SDR
cv2.VideoCapture), so what you capture here is byte-identical to what the
running app will see.
"""
import argparse
import sys
from pathlib import Path

import cv2

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from mkw_tracker.utils.camera import build_camera_source
from mkw_tracker.database.connection import close_connection
from mkw_tracker.database.migrations import apply_migrations
from mkw_tracker.config.settings import get_settings


_REF_W, _REF_H = 1920, 1080
_SLOT_CHOICES = list(range(1, 8))   # mirrors mkw_tracker.utils.calibrate.NUM_SLOTS

# The HDR test patterns live inside this region of the captured frame.
# Drawing it on the preview lets the operator confirm the pattern is fully
# inside the area the solver will sample from.
_ROI_X, _ROI_Y, _ROI_W, _ROI_H = 482, 162, 956, 532


def _norm(frame):
    """Match main.py's normalization: resize to 1920x1080 if needed."""
    if frame is None:
        return None
    h, w = frame.shape[:2]
    if w == _REF_W and h == _REF_H:
        return frame
    return cv2.resize(frame, (_REF_W, _REF_H), interpolation=cv2.INTER_LINEAR)


def _draw_overlay(disp, slot: int, remaining: list, scale: float):
    """Render slot prompt + ROI guide on the (already-resized) preview frame."""
    # ROI rectangle (scaled to preview size)
    x1 = int(_ROI_X * scale); y1 = int(_ROI_Y * scale)
    x2 = int((_ROI_X + _ROI_W) * scale); y2 = int((_ROI_Y + _ROI_H) * scale)
    cv2.rectangle(disp, (x1, y1), (x2, y2), (80, 255, 80), 2)
    cv2.putText(disp, "sampled region",
                (x1 + 6, y1 + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (80, 255, 80), 1)

    # Slot header
    cv2.putText(disp, f"Capturing slot {slot}",
                (20, 36), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 80), 2)
    if remaining:
        nxt = ", ".join(str(s) for s in remaining)
        cv2.putText(disp, f"Up next: {nxt}",
                    (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 200, 220), 1)

    # Key hints (bottom-left)
    cv2.putText(disp, "c = capture & advance   n = skip   q = quit",
                (20, disp.shape[0] - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                (200, 220, 255), 1)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("slots", type=int, nargs="+", choices=_SLOT_CHOICES, metavar="SLOT",
                   help="One or more slot numbers (1..7).  Captured sequentially.")
    p.add_argument("--device", default=None,
                   help="Camera device name (defaults to camera_device from settings).")
    args = p.parse_args()

    apply_migrations()
    settings = get_settings()
    device   = args.device or settings.get("camera_device") or None

    out_dir = _REPO_ROOT / "images" / "calibration"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[ref-capture] device={device!r} slots={args.slots}")
    print(f"[ref-capture] output dir: {out_dir}")
    cam = build_camera_source(device_name=device)

    # Preview is drawn at 1280x720; the ROI guide must scale accordingly.
    _disp_w, _disp_h = 1280, 720
    _scale = _disp_w / _REF_W

    saved_count = 0
    pending = list(args.slots)
    aborted = False
    try:
        while pending:
            slot = pending[0]
            upcoming = pending[1:]
            win = f"Calibration capture - slot {slot}  (c=save  n=skip  q=quit)"
            while True:
                ok, frame = cam.read()
                if not ok or frame is None:
                    continue
                frame = _norm(frame)

                disp = cv2.resize(frame, (_disp_w, _disp_h), interpolation=cv2.INTER_LINEAR)
                _draw_overlay(disp, slot, upcoming, _scale)
                cv2.imshow(win, disp)

                key = (cam.waitKey(1) & 0xFF)
                if key in (ord("c"), ord("C")):
                    out_path = out_dir / f"switch_hdr_test_{slot}.png"
                    cv2.imwrite(str(out_path), frame)
                    print(f"[ref-capture] slot {slot}: saved {out_path}")
                    saved_count += 1
                    cv2.destroyWindow(win)
                    pending.pop(0)
                    break
                if key in (ord("n"), ord("N")):
                    print(f"[ref-capture] slot {slot}: skipped")
                    cv2.destroyWindow(win)
                    pending.pop(0)
                    break
                if key in (ord("q"), ord("Q")):
                    print(f"[ref-capture] quit early (skipped slots: {pending})")
                    aborted = True
                    break
            if aborted:
                break
    finally:
        cam.release()
        cv2.destroyAllWindows()
        close_connection()

    print(f"[ref-capture] done. Saved {saved_count} of {len(args.slots)} requested.")
    return 0 if saved_count == len(args.slots) else 1


if __name__ == "__main__":
    sys.exit(main())
